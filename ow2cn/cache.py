"""极简 TTL 缓存。

群聊里同一个 tag 经常被连着查询，缓存可以明显减少上游请求、降低被限流的概率。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any


class TTLCache:
    def __init__(self, default_ttl: float = 120.0) -> None:
        self._default_ttl = float(default_ttl)
        self._items: dict[str, tuple[float, Any]] = {}
        self._lock = asyncio.Lock()
        # 同一 key 正在回源时的 future，避免并发相同查询同时打上游（singleflight）。
        self._inflight: dict[str, asyncio.Future] = {}

    async def get(self, key: str) -> Any | None:
        async with self._lock:
            item = self._items.get(key)
            if not item:
                return None
            expire_at, value = item
            if expire_at < time.time():
                self._items.pop(key, None)
                return None
            return value

    async def set(self, key: str, value: Any, ttl: float | None = None) -> None:
        ttl = self._default_ttl if ttl is None else float(ttl)
        if ttl <= 0:
            return
        async with self._lock:
            self._items[key] = (time.time() + ttl, value)
            self._purge_locked()

    def _purge_locked(self) -> None:
        """顺手清掉已过期条目，避免只在命中时清理导致内存无限增长。"""
        now = time.time()
        expired = [k for k, (exp, _) in self._items.items() if exp < now]
        for k in expired:
            self._items.pop(k, None)

    async def get_or_create(self, key: str, factory, ttl: float | None = None) -> Any:
        """缓存未命中时调用 factory（协程函数）并写入缓存。

        同一 key 的并发未命中只会真正调用一次 factory，其余等待同一结果。
        """
        cached = await self.get(key)
        if cached is not None:
            return cached

        async with self._lock:
            inflight = self._inflight.get(key)
            if inflight is None:
                inflight = asyncio.get_running_loop().create_future()
                # 没有等待者时 owner 仍会 set_exception，加个回调消费掉，
                # 避免 asyncio "Future exception was never retrieved" 告警。
                inflight.add_done_callback(lambda f: f.cancelled() or f.exception())
                self._inflight[key] = inflight
                owner = True
            else:
                owner = False

        if not owner:
            # shield：某个等待者自己被取消（比如指令超时）时，asyncio 会顺带取消它
            # 正在 await 的 future；不挡一下，同一 key 的其他等待者会一起收到 CancelledError。
            try:
                return await asyncio.shield(inflight)
            except asyncio.CancelledError:
                if not inflight.cancelled():
                    raise  # 是这个等待者自己被取消了
                # 回源的那个任务被取消了（查询本身没出错）：由当前等待者重新回源
                return await self.get_or_create(key, factory, ttl)

        try:
            value = await factory()
        except asyncio.CancelledError:
            # owner 被取消不代表查询本身有错：标记为取消，等待者会各自重新回源。
            # 这里不 await 锁（任务已在取消中），直接同步摘掉 inflight。
            if self._inflight.get(key) is inflight:
                self._inflight.pop(key, None)
            inflight.cancel()
            raise
        except BaseException as exc:
            async with self._lock:
                self._inflight.pop(key, None)
            if not inflight.done():
                inflight.set_exception(exc)
            raise
        await self.set(key, value, ttl)
        async with self._lock:
            self._inflight.pop(key, None)
        if not inflight.done():
            inflight.set_result(value)
        return value

    async def clear(self) -> None:
        async with self._lock:
            self._items.clear()

    def __len__(self) -> int:
        """有效条目数（不含已过期但还没被清理的）。"""
        now = time.time()
        return sum(1 for expire_at, _ in self._items.values() if expire_at >= now)
