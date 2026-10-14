"""Overstats HTTP 服务数据源。

Overstats（https://github.com/AddOneSecondL/Overstats）是一套自托管的本地服务，
它把网易大神上游封装成统一的 HTTP API，token 配置在服务端。

对本插件的意义：**插件端零 token**。只要把 base_url 指向一个可用的 Overstats 服务
（自己部署的、或者别人提供的），就不用在本插件里存放任何大神凭据。

本数据源只依赖 Overstats 的对外契约（见其 OVERSTATS_API.md），并按上游实测行为工作：

- `/dashen-profile` 一次就把 `profile_card` + `sport` + `leisure` 三块都带回来，
  且不区分 mode —— 所以按 tag 缓存原始响应，竞技/快速查询共用，不重复回源；
- `/dashen-match` **忽略 mode**，返回竞技 + 快速（含角斗，若 include_fight 为真）
  按开始时间混排的列表 —— 所以插件本地按 instanceType 分流、本地切片翻页；
- `/dashen-match/detail` 传 `index` 时同样按混排列表定位（会串场），
  只有 `match_id + customer_token` 才是精确定位；
- `/dashen-match/detail/image` 只认 `index`，所以明细图改用 `/dashen-match/detail/replies`：
  它支持 `match_id` 精确定位并直接返回 base64 图片。
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import time
from typing import Any

import httpx

from .. import parsers, ranks
from ..cache import TTLCache
from ..errors import (
    ConfigError,
    OwError,
    PlayerNotFound,
    RateLimited,
    SourceUnavailable,
    UpstreamError,
)
from ..models import MatchDetail, MatchItem, PlayerProfile
from ..net import RETRY_BACKOFF_SECONDS, RETRYABLE_STATUS, build_client
from .base import OwSource

DEFAULT_PROFILE_PATH = "/api/v2/dashen-profile"
DEFAULT_MATCH_PATH = "/api/v2/dashen-match"
DEFAULT_PROFILE_IMAGE_PATH = "/api/v2/dashen-profile/image"
DEFAULT_MATCH_IMAGE_PATH = "/api/v2/dashen-match/image"
DEFAULT_MATCH_DETAIL_PATH = "/api/v2/dashen-match/detail"
DEFAULT_MATCH_DETAIL_REPLIES_PATH = "/api/v2/dashen-match/detail/replies"

# 一次性多要一些：上游一次回「竞技 24 条 + 快速 24 条」，
# 这样 /ow 对局 翻页与 /ow 明细 的序号都能在本地切片完成，不必二次回源。
POOL_LIMIT = 48
# 明细定位与列表必须用同一套筛选参数，否则序号会错位。
POOL_OPTIONS: dict[str, Any] = {"include_fight": False, "include_previous_season": True}

_IMAGE_KINDS = {"profile", "detail"}

# Overstats 错误码 → 提示
_ERROR_HINTS = {
    "bnet_not_found": "确认 BattleTag 大小写与 # 后的数字；该玩家也可能没有公开战绩。",
    "customer_token_missing": "服务端没有配置可用的大神 token。",
    "invalid_json": "请求体格式错误，通常是插件与服务端版本不匹配。",
}


def _first_dict(*candidates: Any) -> dict[str, Any]:
    for item in candidates:
        if isinstance(item, dict):
            return item
    return {}


def _queue_of(raw: Any) -> str:
    """把一条上游对局归到竞技 / 快速 / 角斗。

    实测（Overstats）：竞技是 `IT_RANKED`+`SportPreset`，
    快速是 `IT_UNRANKED`+`LeisurePreset`，角斗的 gameMode 里带 `Fight`。
    注意 `IT_UNRANKED` 里也含 `ranked` 子串，必须先判断 unranked。
    """
    if not isinstance(raw, dict):
        return ""
    instance = str(raw.get("instanceType") or "").strip().lower()
    game_mode = str(raw.get("gameMode") or "").strip().lower()
    if "fight" in game_mode or "fight" in instance:
        return "fight"
    if "unranked" in instance or "leisure" in game_mode:
        return "quick"
    if "ranked" in instance or "sport" in game_mode:
        return "competitive"
    return "quick"


class OverstatsSource(OwSource):
    name = "overstats"

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str = "",
        profile_path: str = DEFAULT_PROFILE_PATH,
        match_path: str = DEFAULT_MATCH_PATH,
        profile_image_path: str = DEFAULT_PROFILE_IMAGE_PATH,
        match_image_path: str = DEFAULT_MATCH_IMAGE_PATH,
        match_detail_path: str = DEFAULT_MATCH_DETAIL_PATH,
        match_detail_replies_path: str = DEFAULT_MATCH_DETAIL_REPLIES_PATH,
        timeout: float = 20.0,
        image_timeout: float = 60.0,
        payload_ttl: float = 120.0,
        max_retry: int = 0,
        debug: bool = False,
        logger: Any = None,
    ) -> None:
        super().__init__()
        self.logger = logger
        self._debug = bool(debug)
        self._max_retry = max(0, int(max_retry or 0))
        base = str(base_url or "").strip().rstrip("/")
        if not base.startswith(("http://", "https://")):
            raise ConfigError(
                "Overstats 服务地址无效",
                hint="请在插件配置的「Overstats HTTP 服务」里填写 base_url，"
                "例如 http://127.0.0.1:18080。",
            )
        self._base_url = base
        self._api_key = str(api_key or "").strip()
        self._profile_path = str(profile_path or DEFAULT_PROFILE_PATH)
        self._match_path = str(match_path or DEFAULT_MATCH_PATH)
        self._profile_image_path = str(profile_image_path or DEFAULT_PROFILE_IMAGE_PATH)
        self._match_image_path = str(match_image_path or DEFAULT_MATCH_IMAGE_PATH)
        self._match_detail_path = str(match_detail_path or DEFAULT_MATCH_DETAIL_PATH)
        self._match_detail_replies_path = str(
            match_detail_replies_path or DEFAULT_MATCH_DETAIL_REPLIES_PATH,
        )
        self._timeout = float(timeout or 20.0)
        # 服务端渲染图明显比 JSON 慢（实测 10~40 秒），单独给一个更宽松的超时。
        self._image_timeout = max(self._timeout, float(image_timeout or 60.0))
        headers = {"Accept": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        self._client = build_client(self._base_url, timeout=self._timeout, headers=headers)
        # 原始响应缓存：profile 与对局池都是「一个 tag 一份」，被多条指令复用。
        self._payloads = TTLCache(float(payload_ttl or 0))

    # ---------- 底层请求 ----------

    def _log(self, message: str) -> None:
        if self.logger:
            self.logger.warning(message)

    def _debug_log(self, message: str) -> None:
        if self._debug and self.logger:
            self.logger.info(f"[ow2cn/overstats] {message}")

    def _raise_for_error(self, payload: dict[str, Any], path: str) -> None:
        """把服务端业务错误体翻成插件自己的错误类型。"""
        code = str(payload.get("error") or payload.get("code") or "")
        message = str(payload.get("message") or payload.get("msg") or code)
        # 优先用插件自己的中文提示；服务端 hint 只在没有对应文案时兜底显示。
        hint = _ERROR_HINTS.get(code) or str(payload.get("hint") or "")
        if code == "bnet_not_found":
            raise PlayerNotFound("查不到这个 BattleTag 的战绩", hint=hint)
        if code == "not_found":
            raise SourceUnavailable(
                "Overstats 服务没有这个接口",
                hint=f"检查插件配置里的接口路径是否正确（当前请求 {path}）。",
            )
        if code in {"customer_token_missing", "token_invalid", "unauthorized"}:
            raise UpstreamError("Overstats 服务端没有可用的大神 token", hint=hint)
        if code == "rate_limited":
            raise RateLimited("Overstats 服务被限流了", hint=hint or "稍后再查。")
        raise UpstreamError(f"Overstats 服务返回错误：{message or '未知错误'}", hint=hint)

    def _status_error(self, status: int) -> OwError:
        if status == 429:
            return RateLimited(
                "Overstats 服务被限流了",
                hint="稍后再查；请降低查询频率。",
            )
        if status in (401, 403):
            return UpstreamError(
                "Overstats 服务鉴权失败",
                hint="检查插件配置里的 api_key（插件会以 Authorization: Bearer <值> 发送）。",
            )
        if status == 404:
            return SourceUnavailable(
                f"Overstats 服务返回 HTTP {status}",
                hint=f"检查 base_url 与接口路径是否正确（{self._base_url}）。",
            )
        return SourceUnavailable(
            f"Overstats 服务返回 HTTP {status}",
            hint="稍后再试；如果持续报错，请查看服务端日志。",
        )

    @staticmethod
    def _json_body(response: httpx.Response) -> Any:
        try:
            return response.json()
        except ValueError:
            return None

    async def _send(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        timeout: float | None = None,
        retries: int | None = None,
    ) -> httpx.Response:
        """发一次 POST；连接失败与 502/503/504 按 max_retry 重试（图片接口传 retries=0）。"""
        url = f"{self._base_url}{path}"
        attempts = (self._max_retry if retries is None else max(0, retries)) + 1
        for attempt in range(attempts):
            started = time.monotonic()
            last_try = attempt + 1 >= attempts
            try:
                response = await self._client.post(
                    url,
                    json=payload,
                    timeout=timeout or self._timeout,
                )
            except httpx.HTTPError as exc:
                self._debug_log(f"POST {path} 失败：{exc.__class__.__name__}")
                if not last_try:
                    await asyncio.sleep(RETRY_BACKOFF_SECONDS * (attempt + 1))
                    continue
                raise SourceUnavailable(
                    "无法连接 Overstats 服务",
                    hint=f"请确认 {self._base_url} 已启动且网络可达（{exc.__class__.__name__}）。",
                ) from exc
            # 请求体里有 customer_token，只记路径与状态码
            self._debug_log(
                f"POST {path} → HTTP {response.status_code}"
                f"（{(time.monotonic() - started) * 1000:.0f} ms）",
            )
            if response.status_code in RETRYABLE_STATUS and not last_try:
                await asyncio.sleep(RETRY_BACKOFF_SECONDS * (attempt + 1))
                continue
            return response
        raise AssertionError("unreachable")  # pragma: no cover - 循环必定 return 或 raise

    async def _post_json(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        timeout: float | None = None,
        retries: int | None = None,
    ) -> dict[str, Any]:
        response = await self._send(path, payload, timeout=timeout, retries=retries)
        body = self._json_body(response)
        if response.status_code in (401, 403):
            # 401/403 说明插件这边的 api_key 不对（插件只发这一个凭据），
            # 不能因为响应体里的 error 码就报成"服务端缺大神 token"。
            raise self._status_error(response.status_code)
        if response.status_code >= 400:
            # 服务端把业务错误也写在 JSON 体里（例如查无此人是 404 + bnet_not_found），
            # 只看 HTTP 状态会给出"检查 base_url"这种误导提示。
            if isinstance(body, dict) and (body.get("error") or body.get("code")):
                self._raise_for_error(body, path)
            raise self._status_error(response.status_code)
        if not isinstance(body, dict):
            raise SourceUnavailable("Overstats 服务返回了非 JSON 内容")
        if body.get("ok") is False or body.get("success") is False:
            self._raise_for_error(body, path)
        return body

    async def _post_bytes(self, path: str, payload: dict[str, Any]) -> bytes | None:
        # 渲染一张图要 10~40 秒，失败了再重来只会让群友干等，直接交给上层回落
        response = await self._send(path, payload, timeout=self._image_timeout, retries=0)
        if response.status_code >= 400:
            self._log(f"Overstats 图片接口返回 HTTP {response.status_code}（{path}）")
            return None
        content_type = response.headers.get("content-type", "")
        if "image" not in content_type:
            self._log(f"Overstats 图片接口返回了非图片内容（{path}：{content_type}）")
            return None
        return response.content or None

    # ---------- 原始数据（按 tag 缓存） ----------

    async def _profile_payload(self, tag: str) -> dict[str, Any]:
        """一次 /dashen-profile 同时带回 profile_card + sport + leisure，按 tag 缓存。"""

        async def factory() -> dict[str, Any]:
            try:
                return await self._post_json(self._profile_path, {"bnet_id": tag})
            except PlayerNotFound as exc:
                raise PlayerNotFound(f"查不到玩家「{tag}」", hint=exc.hint) from exc

        return await self._payloads.get_or_create(f"profile::{tag.lower()}", factory)

    async def _match_pool(self, tag: str) -> dict[str, Any]:
        """混排对局池（竞技 + 快速），附带 customer_token。

        列表、明细、明细图都从这里取，保证序号一致、且只回源一次。
        """

        async def factory() -> dict[str, Any]:
            payload = await self._post_json(
                self._match_path,
                {"bnet_id": tag, "limit": POOL_LIMIT, **POOL_OPTIONS},
            )
            raw = payload.get("matches")
            if not isinstance(raw, list):
                raw = payload.get("matchList")
            if not isinstance(raw, list) and isinstance(payload.get("data"), list):
                raw = payload["data"]
            entries = [item for item in (raw or []) if isinstance(item, dict)]
            if not entries:
                raise UpstreamError(
                    "服务端没有返回对局列表",
                    hint="稍后再试；如果一直为空，可能是该玩家这赛季还没打。",
                )
            return {"entries": entries, "token": str(payload.get("customer_token") or "")}

        return await self._payloads.get_or_create(f"pool::{tag.lower()}", factory)

    async def _mode_entries(self, tag: str, mode: str) -> list[dict[str, Any]]:
        """混排池 → 当前模式的对局（保持时间倒序，角斗对局不参与）。"""
        pool = await self._match_pool(tag)
        wanted = ranks.normalize_mode(mode)
        return [raw for raw in pool["entries"] if _queue_of(raw) == wanted]

    # ---------- 结果解析 ----------

    @staticmethod
    def _mode_block(payload: dict[str, Any], mode: str) -> dict[str, Any]:
        """Overstats 会把不同队列的数据放在 sport / leisure 等键下。"""
        normalized = ranks.normalize_mode(mode)
        keys = ("sport", "competitive") if normalized == "competitive" else ("leisure", "quick")
        for key in keys:
            block = payload.get(key)
            if isinstance(block, dict):
                inner = block.get("data")
                return inner if isinstance(inner, dict) else block
        return {}

    async def profile(
        self,
        tag: str,
        *,
        mode: str = "competitive",
        match_limit: int = 5,
    ) -> PlayerProfile:
        tag = str(tag or "").strip()
        payload = await self._profile_payload(tag)
        card_source = _first_dict(
            payload.get("profile_card"),
            payload.get("card"),
            payload.get("resolved"),
        )
        card_data = _first_dict(card_source.get("data"), card_source)
        card = parsers.parse_card(card_data, fallback_name=tag)
        if not card.name:
            card.name = tag

        block = self._mode_block(payload, mode)
        matches = await self._recent_matches(tag, mode=mode, block=block, limit=match_limit)
        # recentMatchCount 里的其余字段都是在「profile 数据块的对局列表」上算出来的
        # （实测 kill/伤害/治疗与它逐项吻合），所以阵亡也从同一份列表补，
        # 而不是用被 match_limit 截断后的展示列表。
        recent_matches = parsers.parse_matches(block.get("matchList")) or matches

        return PlayerProfile(
            query=card.name,
            mode=ranks.normalize_mode(mode),
            source=self.name,
            card=card,
            roles=parsers.parse_roles(block.get("guideCountData")),
            recent=parsers.parse_recent(
                block.get("recentMatchCount"),
                block.get("presetsSummaryData"),
                matches=recent_matches,
            ),
            season=parsers.parse_season(block.get("presetsSummaryData")),
            behavior=parsers.parse_behavior(block.get("gameAction")),
            matches=matches,
            heroes=parsers.parse_heroes(
                block.get("presetsHeroUseSummaryList"),
                block.get("openHeroUseSummaryList"),
                block.get("v6HeroUseSummaryList"),
            ),
        )

    async def _recent_matches(
        self,
        tag: str,
        *,
        mode: str,
        block: dict[str, Any],
        limit: int,
    ) -> list[MatchItem]:
        """对局列表优先取混排池（条数更多、与明细序号一致），取不到才用 profile 数据块。"""
        try:
            entries = await self._mode_entries(tag, mode)
        except OwError as exc:
            self._log(f"取 Overstats 对局列表失败，改用 profile 数据块里的对局：{exc}")
            entries = []
        if entries:
            return parsers.parse_matches(entries, limit=limit)
        return parsers.parse_matches(block.get("matchList"), limit=limit)

    async def matches(
        self,
        tag: str,
        *,
        mode: str = "competitive",
        page: int = 1,
        limit: int = 5,
    ) -> list[MatchItem]:
        """本地分页：混排池按模式过滤后切片，不额外回源。"""
        tag = str(tag or "").strip()
        page = max(1, int(page or 1))
        limit = max(1, int(limit or 1))
        entries = await self._mode_entries(tag, mode)
        offset = (page - 1) * limit
        return parsers.parse_matches(entries[offset : offset + limit])

    async def match_detail(
        self,
        tag: str,
        *,
        index: int = 0,
        mode: str = "competitive",
    ) -> MatchDetail:
        """单局明细。

        先在与 `/ow 对局` 完全同序的列表里用序号定位 matchId，再用
        `match_id + customer_token` 精确取明细——服务端的 index 定位是按
        竞技/快速混排列表算的，直接用会串场。
        """
        tag = str(tag or "").strip()
        order = max(0, int(index or 0))
        normalized = ranks.normalize_mode(mode)

        entries = await self._mode_entries(tag, normalized)
        if order >= len(entries):
            raise UpstreamError(
                f"没有找到第 {order + 1} 局记录",
                hint="先用 /ow 对局 看一下该模式下的序号，再查明细。",
            )
        match_id = str(entries[order].get("matchId") or "")
        if not match_id:
            raise UpstreamError("这条对局记录缺少 matchId，无法查询明细")

        pool = await self._match_pool(tag)
        token = str(pool.get("token") or "")
        if not token:
            raise UpstreamError(
                "服务端没有返回 customer_token，无法精确查询这一局",
                hint="检查服务端版本是否支持按 match_id 查明细。",
            )
        payload = await self._post_json(
            self._match_detail_path,
            {"bnet_id": tag, "match_id": match_id, "customer_token": token},
        )
        detail = parsers.parse_match_detail(payload, viewer=tag, mode_hint=normalized)
        if not detail.match_id:
            detail.match_id = match_id
        return detail

    async def render_image(
        self,
        tag: str,
        *,
        kind: str = "profile",
        mode: str = "competitive",
        index: int = 0,
    ) -> bytes | None:
        """拿服务端渲染好的图（插件端可以直接发到群里）。"""
        tag = str(tag or "").strip()
        if kind == "detail":
            return await self._detail_image(tag, mode=mode, index=index)
        path = self._profile_image_path if kind == "profile" else self._match_image_path
        return await self._post_bytes(path, {"bnet_id": tag, "mode": ranks.normalize_mode(mode)})

    async def _detail_image(self, tag: str, *, mode: str, index: int) -> bytes | None:
        """明细图：用 match_id 精确取，避免按 index 定位时跨模式串场。"""
        order = max(0, int(index or 0))
        try:
            entries = await self._mode_entries(tag, mode)
        except OwError as exc:
            self._log(f"取明细图失败（对局列表不可用）：{exc}")
            return None
        if order >= len(entries):
            return None
        match_id = str(entries[order].get("matchId") or "")
        pool = await self._match_pool(tag)
        token = str(pool.get("token") or "")
        if not match_id or not token:
            return None
        payload = await self._post_json(
            self._match_detail_replies_path,
            {"bnet_id": tag, "match_id": match_id, "customer_token": token},
            timeout=self._image_timeout,
            retries=0,
        )
        for reply in payload.get("replies") or []:
            if not isinstance(reply, dict) or reply.get("type") != "image":
                continue
            encoded = str(reply.get("base64") or "")
            if not encoded:
                continue
            try:
                return base64.b64decode(encoded)
            except (binascii.Error, ValueError):
                self._log("Overstats 明细图返回了无法解码的 base64 数据")
                return None
        return None

    async def describe(self) -> str:
        return (
            f"数据源：Overstats 服务（{self._base_url}，"
            f"鉴权={'已配置' if self._api_key else '无'}）"
        )

    async def clear_cache(self) -> None:
        await self._payloads.clear()

    async def close(self) -> None:
        await self._client.aclose()


def build(base_url: str, **kwargs: Any) -> OverstatsSource:
    return OverstatsSource(base_url=base_url, **kwargs)


__all__ = ["OverstatsSource", "build"]
