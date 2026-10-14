"""数据源抽象。

插件对外只暴露 `OwSource` 这组接口：
- `profile()` 拿一份完整战绩（资料卡 + 段位 + 近期表现 + 对局 + 英雄）
- `matches()` 单独取近期对局列表

具体实现有：
- `dashen.DashenSource`：直连网易大神上游（需要大神 token）
- `overstats.OverstatsSource`：请求 Overstats HTTP 服务（插件端零 token）
"""

from __future__ import annotations

from ..models import MatchDetail, MatchItem, PlayerProfile


class OwSource:
    name = "base"

    def __init__(self) -> None:
        self.logger = None

    async def profile(
        self,
        tag: str,
        *,
        mode: str = "competitive",
        match_limit: int = 5,
    ) -> PlayerProfile:
        raise NotImplementedError

    async def matches(
        self,
        tag: str,
        *,
        mode: str = "competitive",
        page: int = 1,
        limit: int = 5,
    ) -> list[MatchItem]:
        """默认实现：复用 profile 里的对局列表。"""
        profile = await self.profile(tag, mode=mode, match_limit=limit)
        return list(profile.matches)

    async def match_detail(
        self,
        tag: str,
        *,
        index: int = 0,
        mode: str = "competitive",
    ) -> MatchDetail:
        """单局明细（index 从 0 开始，与对局列表顺序一致）。"""
        raise NotImplementedError

    async def describe(self) -> str:
        """给 /ow 状态 用的一行描述。"""
        return f"数据源：{self.name}"

    async def clear_cache(self) -> None:
        """清空数据源内部缓存（子类按需实现）。"""
        return

    async def close(self) -> None:
        """释放网络资源。"""
        return
