"""业务编排层：配置解析 / 数据源懒加载 / 结果缓存。

命令层（main.py）只调用这里的方法，不直接接触网络细节。
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any

from . import ranks
from .cache import TTLCache
from .config import as_bool, as_float, as_int, section
from .models import MatchDetail, MatchItem, PlayerProfile
from .sources import OwSource, build_source
from .store import SCOPE_GROUP, SCOPE_USER, BindingStore

# 单页对局 / 英雄条数的硬上限：再多群里就刷屏了，也避免配置写错把上游打穿
MAX_LIST_LIMIT = 50


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


class OwService:
    def __init__(self, config: Any, store: BindingStore, logger: Any = None) -> None:
        self.config = config
        self.store = store
        self.logger = logger

        query = section(config, "query")
        self.default_mode = ranks.normalize_mode(query.get("default_mode") or "competitive")
        self.match_limit = _clamp(as_int(query.get("match_limit"), 5, minimum=1), 1, MAX_LIST_LIMIT)
        self.match_limit_max = _clamp(
            as_int(query.get("match_limit_max"), 15, minimum=1),
            self.match_limit,
            MAX_LIST_LIMIT,
        )
        self.hero_limit = _clamp(as_int(query.get("hero_limit"), 5, minimum=1), 1, MAX_LIST_LIMIT)
        self.allow_any_battle_tag = as_bool(query.get("allow_any_battle_tag"), True)

        self._cache = TTLCache(as_float(query.get("cache_ttl"), 120.0, minimum=0.0))
        self._source: OwSource | None = None
        self._source_lock = asyncio.Lock()

    # ---------- 数据源 ----------

    async def source(self) -> OwSource:
        """懒加载数据源：配置有问题时把错误留给具体命令去提示，而不是让插件加载失败。"""
        if self._source is not None:
            return self._source
        async with self._source_lock:
            if self._source is None:
                self._source = build_source(self.config, self.logger)
        return self._source

    async def describe_source(self) -> str:
        try:
            source = await self.source()
        except Exception as exc:  # 配置错误也在这里被展示
            return f"数据源不可用：{exc}"
        return await source.describe()

    # ---------- 查询 ----------

    def normalize_tag(self, tag: str) -> str:
        """BattleTag 规整：去空白、去全角井号、限制长度。"""
        text = " ".join(str(tag or "").split())
        text = text.replace("＃", "#").replace("﹟", "#")
        return text[:64]

    async def profile(
        self,
        tag: str,
        *,
        mode: str | None = None,
        limit: int | None = None,
        use_cache: bool = True,
    ) -> PlayerProfile:
        mode = ranks.normalize_mode(mode or self.default_mode)
        limit = _clamp(int(limit or self.match_limit), 1, self.match_limit_max)
        tag = self.normalize_tag(tag)
        # 只分两档回源：常规条数（/ow 资料、战绩、英雄 共用）与上限条数（图片卡片），
        # 拿到后按各指令要的条数切片，不再每个 limit 各打一遍上游。
        fetch_limit = self.match_limit if limit <= self.match_limit else self.match_limit_max

        async def factory() -> PlayerProfile:
            source = await self.source()
            return await source.profile(tag, mode=mode, match_limit=fetch_limit)

        if not use_cache:
            return self._slice_matches(await factory(), limit)
        prefix = f"profile::{tag.lower()}::{mode}"
        # 上限那一档已经在缓存里时，它是常规档的超集，直接复用。
        full = await self._cache.get(f"{prefix}::{self.match_limit_max}")
        if full is None:
            full = await self._cache.get_or_create(f"{prefix}::{fetch_limit}", factory)
        return self._slice_matches(full, limit)

    @staticmethod
    def _slice_matches(profile: PlayerProfile, limit: int) -> PlayerProfile:
        if len(profile.matches) <= limit:
            return profile
        # 浅拷贝：缓存里的那份保持完整，供其他指令继续切片
        return replace(profile, matches=profile.matches[:limit])

    async def matches(
        self,
        tag: str,
        *,
        mode: str | None = None,
        page: int = 1,
        limit: int | None = None,
        use_cache: bool = True,
    ) -> list[MatchItem]:
        mode = ranks.normalize_mode(mode or self.default_mode)
        limit = _clamp(int(limit or self.match_limit), 1, self.match_limit_max)
        page = max(1, int(page or 1))
        tag = self.normalize_tag(tag)

        async def factory() -> list[MatchItem]:
            source = await self.source()
            return await source.matches(tag, mode=mode, page=page, limit=limit)

        if not use_cache:
            return await factory()
        key = f"matches::{tag.lower()}::{mode}::{page}::{limit}"
        return await self._cache.get_or_create(key, factory)

    async def match_detail(
        self,
        tag: str,
        *,
        index: int = 1,
        mode: str | None = None,
        use_cache: bool = True,
    ) -> MatchDetail:
        """单局明细。index 是用户看到的序号（从 1 开始）。"""
        mode = ranks.normalize_mode(mode or self.default_mode)
        order = max(1, int(index or 1))
        tag = self.normalize_tag(tag)

        async def factory() -> MatchDetail:
            source = await self.source()
            return await source.match_detail(tag, index=order - 1, mode=mode)

        if not use_cache:
            return await factory()
        key = f"detail::{tag.lower()}::{mode}::{order}"
        return await self._cache.get_or_create(key, factory)

    async def rendered_image(
        self,
        tag: str,
        *,
        kind: str = "profile",
        mode: str | None = None,
        index: int = 1,
    ) -> bytes | None:
        """只有 Overstats 数据源支持直接取服务端渲染好的图。

        服务端渲染一张图要 10~40 秒，所以按 TTL 缓存结果：群聊里连着发两次
        `/ow 图` 不会把上游打两遍。
        """
        source = await self.source()
        renderer = getattr(source, "render_image", None)
        if renderer is None:
            return None
        mode = ranks.normalize_mode(mode or self.default_mode)
        normalized = self.normalize_tag(tag)
        order = max(0, int(index or 1) - 1)

        async def factory() -> bytes | None:
            return await renderer(normalized, kind=kind, mode=mode, index=order)

        return await self._cache.get_or_create(
            f"image::{kind}::{normalized.lower()}::{mode}::{order}",
            factory,
        )

    async def status_text(self) -> str:
        counts = self.store.count()
        return (
            f"{await self.describe_source()}\n"
            f"缓存条目：{len(self._cache)}\n"
            f"绑定数量：群 {counts.get('group', 0)} 个 / 用户 {counts.get('user', 0)} 个"
        )

    async def clear_cache(self) -> None:
        """清空查询缓存与数据源内部缓存（管理员指令用）。"""
        await self._cache.clear()
        if self._source is not None:
            await self._source.clear_cache()

    async def close(self) -> None:
        if self._source is not None:
            await self._source.close()
            self._source = None
        await self._cache.clear()

    # ---------- 绑定关系 ----------

    @property
    def binding_scope(self) -> str:
        """绑定作用域（both / group / user），非法值按 both 处理。"""
        scope = str(section(self.config, "binding").get("scope") or "both").strip().lower()
        return scope if scope in {"both", "group", "user"} else "both"

    def resolve_binding(self, *, group_id: str = "", user_id: str = "") -> dict[str, Any] | None:
        """按作用域配置解析绑定：个人绑定优先，其次群绑定。"""
        scope = self.binding_scope
        if scope in {"both", "user"} and user_id:
            entry = self.store.get(SCOPE_USER, user_id)
            if entry:
                return entry
        if scope in {"both", "group"} and group_id:
            entry = self.store.get(SCOPE_GROUP, group_id)
            if entry:
                return entry
        return None

    def bind(
        self, scope: str, key: str, tag: str, *, operator_id: str = "", operator_name: str = ""
    ) -> dict[str, Any]:
        return self.store.set(
            scope,
            key,
            tag,
            operator_id=operator_id,
            operator_name=operator_name,
        )

    def unbind(self, scope: str, key: str) -> bool:
        return self.store.remove(scope, key)
