"""网易大神（网易 DD）上游直连数据源。

这是国服唯一有真实数据的链路：BattleTag → customerToken → 战绩数据。
本节实现的请求形态与请求头，与社区项目 Overstats（https://github.com/AddOneSecondL/Overstats）
记录的抓包结果一致，向该项目致谢。

上游接口一览（api_root 默认为 https://datamsapi.ds.163.com/v1/a19ld5tool）：
- POST /searchBnetAccount        BattleTag → customerToken / bnetId
- GET  /customer/queryCard       基础资料卡
- GET  /customer/queryCountInfo  竞技/快速统计（段位、胜率、英雄使用）
- GET  /customer/queryMatchList  近期对局列表
- GET  /customer/queryMatchInfo  单场对局详情
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

from .. import parsers, ranks
from ..errors import (
    AuthError,
    ConfigError,
    PlayerNotFound,
    RateLimited,
    SourceUnavailable,
    UpstreamError,
)
from ..models import MatchDetail, MatchItem, PlayerProfile
from ..net import RETRY_BACKOFF_SECONDS, RETRYABLE_STATUS, build_client
from .base import OwSource

DEFAULT_API_ROOT = "https://datamsapi.ds.163.com/v1/a19ld5tool"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Version/4.0 Chrome/108.0.0.0 Mobile Safari/537.36 app/df_client"
)
DASHEN_ORIGIN = "https://act.ds.163.com"
DASHEN_REFERER = "https://act.ds.163.com/"

# 账号被判定失效后的冷却时间，避免每次查询都撞一个坏 token
CREDENTIAL_COOLDOWN_SECONDS = 300.0

# 对局窗口最多往后翻几页上游：翻页只是为了凑够本地切片，不必无限制地往后拉
MAX_MATCH_PAGES = 5


@dataclass
class Credential:
    """一个大神账号凭据。"""

    name: str
    role_id: str
    token: str
    cooldown_until: float = field(default=0.0)

    @property
    def available(self) -> bool:
        return self.cooldown_until <= time.monotonic()

    def cool_down(self, seconds: float = CREDENTIAL_COOLDOWN_SECONDS) -> None:
        self.cooldown_until = time.monotonic() + seconds


@dataclass
class _MatchWindow:
    """某个玩家某个模式下，跨上游分页累积起来的对局（保持上游的时间倒序）。"""

    expires_at: float = 0.0
    entries: list[dict[str, Any]] = field(default_factory=list)
    seen: set[str] = field(default_factory=set)
    next_page: int = 1
    page_size: int = 0
    exhausted: bool = False


def _entry_key(item: dict[str, Any]) -> str:
    """对局去重用的 key：优先 matchId，缺失时用开赛时间 + 地图 + 英雄凑一个。"""
    match_id = str(parsers.pick(item, "matchId", "match_id") or "")
    if match_id:
        return match_id
    return ":".join(str(item.get(key) or "") for key in ("beginTs", "mapGuid", "heroGuid"))


def _parse_accounts(raw: Any) -> list[Credential]:
    """把配置里的账号列表解析成凭据对象，非法项直接跳过。"""
    credentials: list[Credential] = []
    if isinstance(raw, dict):  # 允许写成 {"primary": {...}} 这种形式
        raw = list(raw.values())
    if not isinstance(raw, (list, tuple)):
        return credentials
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        token = str(parsers.pick(item, "token", "access_token") or "").strip()
        role_id = str(parsers.pick(item, "role_id", "roleId") or "").strip()
        if not token or not role_id:
            continue
        credentials.append(
            Credential(
                name=str(parsers.pick(item, "name") or f"account-{index + 1}"),
                role_id=role_id,
                token=token,
            ),
        )
    return credentials


def _list_of(payload: Any) -> list[Any]:
    """上游部分接口的 data 直接是数组，这里统一取出列表。"""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        raw = payload.get("_raw")
        if isinstance(raw, list):
            return raw
        for key in ("matchList", "list", "items", "records", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
    return []


class DashenSource(OwSource):
    name = "dashen"

    def __init__(
        self,
        accounts: Any,
        *,
        api_root: str = DEFAULT_API_ROOT,
        dts: str = "2026",
        server: str = "1",
        client_type: str = "60",
        user_agent: str = DEFAULT_USER_AGENT,
        timeout: float = 12.0,
        max_retry: int = 1,
        token_cache_ttl: float = 21600.0,
        payload_ttl: float = 120.0,
        debug: bool = False,
        logger: Any = None,
    ) -> None:
        super().__init__()
        self.logger = logger
        self._credentials = _parse_accounts(accounts)
        if not self._credentials:
            raise ConfigError(
                "没有可用的网易大神账号凭据",
                hint="二选一：① 在插件配置的「网易大神」里填写 accounts（至少包含 role_id 与 token，"
                "获取方式见 README）；② 把数据源改成 overstats 并填写服务地址，插件端就不需要 token。",
            )
        self._api_root = str(api_root or DEFAULT_API_ROOT).rstrip("/")
        self._dts = str(dts or "2026")
        self._server = str(server or "1")
        self._client_type = str(client_type or "60")
        self._user_agent = str(user_agent or DEFAULT_USER_AGENT)
        self._max_retry = max(0, int(max_retry))
        self._token_cache_ttl = float(token_cache_ttl or 0)
        self._payload_ttl = float(payload_ttl or 0)
        self._debug = bool(debug)
        # BattleTag → searchBnetAccount 结果缓存，避免每次查询都回源解析 token
        self._search_cache: dict[str, Any] = {}
        # (customerToken, gameMode) → 累积的对局窗口；列表翻页与明细定位共用
        self._windows: dict[str, _MatchWindow] = {}
        self._window_locks: dict[str, asyncio.Lock] = {}
        self._client = build_client(
            self._api_root,
            timeout=float(timeout or 12.0),
            headers={"Accept": "application/json, text/plain, */*"},
        )
        self._cursor = 0

    # ---------- 凭据与请求头 ----------

    def _pick_credential(self, exclude: set[int] | None = None) -> Credential:
        """轮转取一个可用账号；`exclude` 是本次请求已经试过的账号（按 id）。"""
        exclude = exclude or set()
        total = len(self._credentials)
        for offset in range(total):
            index = (self._cursor + offset) % total
            credential = self._credentials[index]
            if credential.available and id(credential) not in exclude:
                self._cursor = (index + 1) % total
                return credential
        # 全部在冷却中（或都试过了），挑最早恢复的那个
        credential = min(self._credentials, key=lambda c: c.cooldown_until)
        self._cursor = (self._credentials.index(credential) + 1) % total
        return credential

    def _headers(self, credential: Credential) -> dict[str, str]:
        return {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json;charset=UTF-8",
            "GL-Bigdata-Auth-Token": credential.token,
            "GL-Bigdata-Dts": self._dts,
            "GL-Bigdata-Role-Id": credential.role_id,
            "GL-Bigdata-Server": self._server,
            "GL-ClientType": self._client_type,
            "Origin": DASHEN_ORIGIN,
            "Referer": DASHEN_REFERER,
            "User-Agent": self._user_agent,
        }

    # ---------- 底层请求 ----------

    def _debug_log(self, message: str) -> None:
        if self._debug and self.logger:
            self.logger.info(f"[ow2cn/dashen] {message}")

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: Callable[[Credential], dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """带账号故障转移的请求：某个账号 token 失效时，当次请求立刻换下一个可用账号重发。

        `body` 是「凭据 → 请求体」的函数，因为 searchBnetAccount 的请求体里也带了 token。
        所有账号都失效时，抛出最后一个账号的 AuthError。
        """
        tried: set[int] = set()
        while True:
            credential = self._pick_credential(exclude=tried)
            tried.add(id(credential))
            try:
                return await self._request_once(
                    method,
                    path,
                    credential=credential,
                    params=params,
                    body=body(credential) if body else None,
                )
            except AuthError:
                if not self._has_untried(tried):
                    raise
                self._debug_log(f"账号 {credential.name} 被拒绝，改用下一个账号重试 {path}")

    def _has_untried(self, tried: set[int]) -> bool:
        return any(c.available and id(c) not in tried for c in self._credentials)

    async def _request_once(
        self,
        method: str,
        path: str,
        *,
        credential: Credential,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """用指定账号发请求；连接失败与 502/503/504 按 max_retry 重试。"""
        url = f"{self._api_root}{path}"
        last_error: Exception | None = None
        for attempt in range(self._max_retry + 1):
            started = time.monotonic()
            can_retry = attempt < self._max_retry
            try:
                response = await self._client.request(
                    method,
                    url,
                    params=params,
                    json=body,
                    headers=self._headers(credential),
                )
            except httpx.HTTPError as exc:
                last_error = exc
                self._debug_log(f"{method} {path} 失败：{exc.__class__.__name__}")
                if can_retry:
                    await asyncio.sleep(RETRY_BACKOFF_SECONDS * (attempt + 1))
                    continue
                raise SourceUnavailable(
                    "连接网易大神上游失败",
                    hint=f"请检查网络/代理设置（{exc.__class__.__name__}）。",
                ) from exc
            # 只记路径与状态码：query 里有 customerToken，请求头里有大神 token，都不能进日志
            self._debug_log(
                f"{method} {path} → HTTP {response.status_code}"
                f"（{(time.monotonic() - started) * 1000:.0f} ms，账号 {credential.name}）",
            )
            if response.status_code in RETRYABLE_STATUS and can_retry:
                await asyncio.sleep(RETRY_BACKOFF_SECONDS * (attempt + 1))
                continue

            # 401：token 失效（上游返回 {"code":401,"errMsg":"Token Invalid"}）
            if response.status_code in (401, 403):
                credential.cool_down()
                raise AuthError(
                    "网易大神 token 已失效或被拒绝",
                    hint="请重新获取 token 并更新插件配置（GL-Bigdata-Auth-Token 会过期）。",
                )
            if response.status_code == 429:
                raise RateLimited(
                    "网易大神上游限流",
                    hint="稍后再查；可多配几个账号轮转以降低单账号频率。",
                )
            if response.status_code >= 400:
                # 上游把错误说明放在响应体里（例如路径变更时的 {"code":404,"errMsg":"Not Found"}），
                # 带上它才好判断是接口变了还是临时故障。
                try:
                    error_body = response.json()
                except ValueError:
                    error_body = None
                message = ""
                if isinstance(error_body, dict):
                    message = str(
                        parsers.pick(error_body, "errMsg", "errmsg", "msg", "message") or "",
                    )
                raise UpstreamError(
                    f"上游返回 HTTP {response.status_code}" + (f"：{message}" if message else ""),
                    hint="稍后再试；如果持续报错，可能是上游接口变更。",
                )

            try:
                payload = response.json()
            except ValueError as exc:
                raise UpstreamError("上游返回了非 JSON 内容") from exc

            if not isinstance(payload, dict):
                raise UpstreamError("上游返回结构异常")

            code = payload.get("code")
            if code not in (None, 0, 200):
                message = str(
                    parsers.pick(payload, "errMsg", "errmsg", "msg", "message") or f"code={code}",
                )
                if code in (401, 403):
                    credential.cool_down()
                    raise AuthError(
                        f"网易大神 token 校验失败：{message}",
                        hint="请重新获取 token 并更新插件配置。",
                    )
                raise UpstreamError(f"上游报错：{message}")
            if payload.get("success") is False:
                raise UpstreamError(
                    f"上游报错：{payload.get('errMsg') or payload.get('msg') or 'success=false'}",
                )

            data = payload.get("data")
            return data if isinstance(data, dict) else {"_raw": data}
        raise SourceUnavailable("上游请求失败", hint=str(last_error or ""))

    # ---------- 上游接口 ----------

    async def search_bnet_account(self, tag: str) -> dict[str, Any]:
        """BattleTag → customerToken（带缓存）。"""
        cache_key = str(tag).strip().lower()
        now = time.time()
        cached = self._search_cache.get(cache_key)
        if cached and cached[0] > now:
            return cached[1]
        data = await self._request(
            "POST",
            "/searchBnetAccount",
            body=lambda credential: {
                "token": credential.token,
                "roleId": credential.role_id,
                "dts": self._dts,
                "server": self._server,
                "name": tag,
            },
        )
        if not parsers.pick(data, "customerToken", "customer_token"):
            raise PlayerNotFound(
                f"查不到玩家「{tag}」",
                hint="国服 BattleTag 需要带 #数字（例如 光头大猩猩#5104），"
                "并且该玩家的战绩在网易大神里是公开的。",
            )
        if self._token_cache_ttl > 0:
            # 顺手清掉过期项：机器人常驻几个月，查过的 tag 不能一直留在内存里
            for key in [k for k, (expire_at, _) in self._search_cache.items() if expire_at <= now]:
                self._search_cache.pop(key, None)
            self._search_cache[cache_key] = (now + self._token_cache_ttl, data)
        return data

    async def query_card(self, customer_token: str) -> dict[str, Any]:
        return await self._request(
            "GET",
            "/customer/queryCard",
            params={"season": "", "token": customer_token},
        )

    async def query_count_info(
        self,
        customer_token: str,
        game_mode: str,
        *,
        season: int | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"gameMode": game_mode, "token": customer_token}
        if season is not None:
            params["season"] = int(season)
        return await self._request("GET", "/customer/queryCountInfo", params=params)

    async def query_match_list(
        self,
        customer_token: str,
        game_mode: str,
        *,
        page: int = 1,
        season: int | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "gameMode": game_mode,
            "page": int(page),
            "token": customer_token,
        }
        if season is not None:
            params["season"] = int(season)
        return await self._request("GET", "/customer/queryMatchList", params=params)

    async def _match_window(
        self,
        customer_token: str,
        mode: str,
        needed: int,
    ) -> list[dict[str, Any]]:
        """把上游分页累积成一份本地列表，`/ow 对局` 翻页与 `/ow 明细` 序号都从这里切。

        上游每页多少条没有文档，所以不能把「插件第 N 页」直接映射成「上游第 N 页」
        （那样第 2 页会显示上游第 2 页的前几条，编号却从 6 开始，和明细对不上）。
        这里按需往后翻，直到凑够 needed 条、遇到空页/短页，或整页都是重复数据
        （上游忽略了 page 参数）为止。
        """
        game_mode = ranks.game_mode_of(mode)
        key = f"{customer_token}::{game_mode}"
        lock = self._window_locks.setdefault(key, asyncio.Lock())
        async with lock:
            now = time.monotonic()
            window = self._windows.get(key)
            if window is None or window.expires_at <= now:
                self._purge_windows(now, keep=key)
                window = _MatchWindow(expires_at=now + self._payload_ttl)
                if self._payload_ttl > 0:
                    self._windows[key] = window
            while (
                len(window.entries) < needed
                and not window.exhausted
                and window.next_page <= MAX_MATCH_PAGES
            ):
                page = window.next_page
                try:
                    data = await self.query_match_list(customer_token, game_mode, page=page)
                except (UpstreamError, SourceUnavailable, RateLimited):
                    if page == 1:
                        raise
                    break  # 后面的页拉不到就先用已有的，不让整条指令失败
                items = [item for item in _list_of(data) if isinstance(item, dict)]
                window.next_page += 1
                fresh = 0
                for item in items:
                    item_key = _entry_key(item)
                    if item_key in window.seen:
                        continue
                    window.seen.add(item_key)
                    window.entries.append(item)
                    fresh += 1
                if not fresh or (window.page_size and len(items) < window.page_size):
                    window.exhausted = True
                window.page_size = window.page_size or len(items)
            return list(window.entries)

    def _purge_windows(self, now: float, *, keep: str) -> None:
        """顺手清掉过期窗口（及空闲的锁），避免查过的玩家越积越多。

        cache_ttl=0 时窗口根本不入表，锁也要按「没有对应窗口」清掉，否则会一直累积。
        """
        for key in [k for k, w in self._windows.items() if w.expires_at <= now]:
            self._windows.pop(key, None)
        for key in [k for k in self._window_locks if k != keep and k not in self._windows]:
            if not self._window_locks[key].locked():
                self._window_locks.pop(key, None)

    async def query_match_info(self, customer_token: str, match_id: str) -> dict[str, Any]:
        return await self._request(
            "GET",
            "/customer/queryMatchInfo",
            params={"matchId": match_id, "token": customer_token},
        )

    # ---------- OwSource 接口 ----------

    async def normalize_tag(self, tag: str) -> str:
        """把 BattleTag 规整成上游接受的写法（保留中文，去掉多余空白）。"""
        return " ".join(str(tag or "").split())

    async def profile(
        self,
        tag: str,
        *,
        mode: str = "competitive",
        match_limit: int = 5,
    ) -> PlayerProfile:
        tag = await self.normalize_tag(tag)
        search_data = await self.search_bnet_account(tag)
        card = parsers.parse_card(search_data, fallback_name=tag)
        customer_token = card.customer_token

        count_data = await self.query_count_info(
            customer_token,
            ranks.game_mode_of(mode),
        )
        # queryCountInfo.matchList 就是 recentMatchCount 的统计窗口（实测两者逐项吻合），
        # 截断前先留住它，供近期数据的阵亡补算使用。
        window_matches = parsers.parse_matches(count_data.get("matchList"))
        matches = window_matches[:match_limit] if match_limit else window_matches
        if match_limit and len(matches) < match_limit:
            # queryCountInfo 里的对局数不固定，不够时用列表接口补齐
            try:
                extra = await self.matches(tag, mode=mode, page=1, limit=match_limit)
                seen = {m.match_id for m in matches}
                matches.extend([m for m in extra if m.match_id not in seen])
                matches = matches[:match_limit]
            except (UpstreamError, SourceUnavailable):
                pass

        return PlayerProfile(
            query=card.name or tag,
            mode=ranks.normalize_mode(mode),
            source=self.name,
            card=card,
            roles=parsers.parse_roles(count_data.get("guideCountData")),
            recent=parsers.parse_recent(
                count_data.get("recentMatchCount"),
                count_data.get("presetsSummaryData"),
                matches=window_matches,
            ),
            season=parsers.parse_season(count_data.get("presetsSummaryData")),
            behavior=parsers.parse_behavior(count_data.get("gameAction")),
            matches=matches,
            heroes=parsers.parse_heroes(
                count_data.get("presetsHeroUseSummaryList"),
                count_data.get("openHeroUseSummaryList"),
                count_data.get("v6HeroUseSummaryList"),
            ),
        )

    async def matches(
        self,
        tag: str,
        *,
        mode: str = "competitive",
        page: int = 1,
        limit: int = 5,
    ) -> list[MatchItem]:
        """本地分页：第 page 页就是累积窗口里的第 (page-1)*limit 条起，编号与明细一致。"""
        tag = await self.normalize_tag(tag)
        search_data = await self.search_bnet_account(tag)
        card = parsers.parse_card(search_data, fallback_name=tag)
        page = max(1, int(page or 1))
        limit = max(1, int(limit or 1))
        offset = (page - 1) * limit
        entries = await self._match_window(card.customer_token, mode, offset + limit)
        return parsers.parse_matches(entries[offset : offset + limit])

    async def match_detail(
        self,
        tag: str,
        *,
        index: int = 0,
        mode: str = "competitive",
    ) -> MatchDetail:
        """单局明细：在与 `/ow 对局` 同一份累积窗口里定位 matchId，再拉 queryMatchInfo。"""
        tag = await self.normalize_tag(tag)
        search_data = await self.search_bnet_account(tag)
        card = parsers.parse_card(search_data, fallback_name=tag)
        index = int(index)
        entries = await self._match_window(card.customer_token, mode, max(1, index + 1))
        if index < 0 or index >= len(entries):
            raise UpstreamError(
                f"没有找到第 {index + 1} 局记录",
                hint="先用 /ow 对局 看一下序号，再查明细。",
            )
        match_id = str(parsers.pick(entries[index], "matchId", "match_id") or "")
        if not match_id:
            raise UpstreamError("这条对局记录缺少 matchId，无法查询明细")
        data = await self.query_match_info(card.customer_token, match_id)
        if not isinstance(data, dict) or not data:
            raise UpstreamError("上游没有返回这一局的明细数据")
        detail = parsers.parse_match_detail(
            data,
            viewer=card.name or tag,
            mode_hint=ranks.normalize_mode(mode),
        )
        if not detail.match_id:
            detail.match_id = match_id
        return detail

    async def describe(self) -> str:
        return (
            f"数据源：网易大神直连（{self._api_root}，账号数 {len(self._credentials)}，"
            f"dts={self._dts}）"
        )

    async def clear_cache(self) -> None:
        """清空 BattleTag → customerToken 的缓存与对局窗口（正在用的锁保留）。"""
        self._search_cache.clear()
        self._windows.clear()
        for key in [k for k, lock in self._window_locks.items() if not lock.locked()]:
            self._window_locks.pop(key, None)

    async def close(self) -> None:
        await self._client.aclose()


def build(accounts: Any, **kwargs: Any) -> DashenSource:
    """给工厂用的构造入口。"""
    return DashenSource(accounts, **kwargs)


__all__ = ["DEFAULT_API_ROOT", "Credential", "DashenSource", "build"]
