"""图片卡片里的外链图片 → 内嵌 data URI。

为什么要内嵌：AstrBot 的文转图服务拿到 HTML 后自己去下载 <img> 里的外链再截图。
英雄图标放在海外 CDN 上、单张 110~155 KB（实际只显示 24~46 px），文转图服务那边
下载慢时，截图那一刻 PNG 只解码出上半截，卡片里就出现「图片显示不全」。

插件先把图片下载下来、缩到够用的尺寸，再以 data URI 塞进模板数据：
截图时没有任何网络请求要等，也不受文转图服务所在网络能否访问这些 CDN 的影响。
单张缩小后约 5 KB（WebP），整张卡片的图片合计几十 KB。
"""

from __future__ import annotations

import asyncio
import base64
import io
from collections.abc import Iterable
from typing import Any
from urllib.parse import urlsplit

import httpx

from .cache import TTLCache
from .net import LOOPBACK_HOSTS, build_client, is_local_host

# 缩放后的最大边长：头像显示 84 px、英雄图标 46 px，按 ultra（1.8 倍）清晰度留足余量
MAX_EDGE = 160
# 单张原图的下载上限，超过就不内嵌（防止上游给了个大图把内存撑爆）
MAX_SOURCE_BYTES = 2 * 1024 * 1024
# 单张内嵌后的上限：同一张图在卡片里会重复出现，太大会把发给文转图服务的数据撑到几 MB。
# 有 Pillow 时缩小后约 10 KB；没有 Pillow 的原图（110~155 KB）超过上限，保留原链接。
MAX_INLINE_BYTES = 64 * 1024
# 内嵌结果缓存：英雄图标基本不变，头像偶尔会换，一天足够
CACHE_TTL_SECONDS = 24 * 3600
# 下载失败的图片多久后再试
FAILURE_TTL_SECONDS = 300
# 同时下载的图片数
CONCURRENCY = 6

# 只认这几种图片，按文件头识别，不信任响应头里的 content-type
_SIGNATURES = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)


def _sniff_mime(data: bytes) -> str:
    for signature, mime in _SIGNATURES:
        if data.startswith(signature):
            return mime
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return ""


def _shrink(data: bytes) -> tuple[bytes, str] | None:
    """用 Pillow 缩小并重新编码（优先 WebP，保留透明）；没有 Pillow 时返回 None。"""
    try:
        from PIL import Image, features
    except ImportError:
        return None
    try:
        with Image.open(io.BytesIO(data)) as source:
            source.load()
            image = source.convert("RGBA") if source.mode not in ("RGB", "RGBA") else source.copy()
        lanczos = getattr(Image, "Resampling", Image).LANCZOS  # Pillow 9.1+ / 旧版
        image.thumbnail((MAX_EDGE, MAX_EDGE), lanczos)
        buffer = io.BytesIO()
        if features.check("webp"):
            image.save(buffer, "WEBP", quality=90, method=4)
            return buffer.getvalue(), "image/webp"
        image.save(buffer, "PNG", optimize=True)
        return buffer.getvalue(), "image/png"
    except Exception:  # 图片损坏、格式不支持等：交给调用方用原图
        return None


def to_data_uri(data: bytes) -> str | None:
    """图片字节 → data URI；不是认得的图片格式、或缩不下来太大时返回 None（保留原链接）。"""
    mime = _sniff_mime(data)
    if not mime:
        return None
    shrunk = _shrink(data)
    if shrunk is not None and len(shrunk[0]) < len(data):
        data, mime = shrunk
    if len(data) > MAX_INLINE_BYTES:
        return None
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def _fetchable(url: str, *, allow_private: bool = False) -> bool:
    """只下载公网 http(s) 图片：图片地址来自上游数据，不能被拿来探测内网。"""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return False
    return allow_private or not is_local_host(parts.hostname)


class ImageInliner:
    """下载并缓存卡片图片；任何一张失败都只是保留原 URL，不影响出图。"""

    def __init__(
        self,
        *,
        timeout: float = 8.0,
        logger: Any = None,
        allow_private_hosts: bool = False,
    ) -> None:
        self._timeout = float(timeout)
        self._logger = logger
        # 只给测试用：允许从本机假图片服务下载
        self._allow_private_hosts = bool(allow_private_hosts)
        self._client: httpx.AsyncClient | None = None
        self._cache = TTLCache(CACHE_TTL_SECONDS)
        self._semaphore = asyncio.Semaphore(CONCURRENCY)

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = build_client(
                "",
                timeout=self._timeout,
                direct_hosts=LOOPBACK_HOSTS,
                headers={
                    "Accept": "image/avif,image/webp,image/png,image/*;q=0.8,*/*;q=0.5",
                    "User-Agent": "Mozilla/5.0 (compatible; astrbot_plugin_ow2_cn)",
                },
            )
        return self._client

    async def _download(self, url: str) -> bytes | None:
        async with self._semaphore:
            try:
                async with self._http().stream("GET", url) as response:
                    if response.status_code != 200:
                        return None
                    chunks: list[bytes] = []
                    size = 0
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > MAX_SOURCE_BYTES:
                            return None
                        chunks.append(chunk)
                    return b"".join(chunks)
            except httpx.HTTPError:
                return None

    async def _inline_one(self, url: str) -> str:
        fetched = False

        async def factory() -> str:
            nonlocal fetched
            fetched = True
            data = await self._download(url)
            return (to_data_uri(data) if data else None) or ""

        result = await self._cache.get_or_create(url, factory)
        if fetched and not result:
            # 失败也短暂缓存成空串：同一张坏图不会每次出图都重新下载，几分钟后再给一次机会。
            # 只在刚下载失败时改 TTL；命中缓存时不续期，否则失败记录会一直续下去。
            await self._cache.set(url, "", FAILURE_TTL_SECONDS)
        return result

    async def inline(self, urls: Iterable[str], *, budget: float) -> dict[str, str]:
        """把一批 URL 换成 data URI，最多等 budget 秒；返回 {原 URL: data URI}（只含成功的）。"""
        wanted = sorted(
            {
                u
                for u in urls
                if u
                and not u.startswith("data:")
                and _fetchable(u, allow_private=self._allow_private_hosts)
            }
        )
        if not wanted or budget <= 0:
            return {}
        tasks = {url: asyncio.ensure_future(self._inline_one(url)) for url in wanted}
        done, pending = await asyncio.wait(tasks.values(), timeout=budget)
        for task in pending:
            task.cancel()
        if pending and self._logger:
            self._logger.warning(
                f"卡片图片下载超时 {len(pending)}/{len(wanted)} 张，这几张改用原链接"
            )
        result: dict[str, str] = {}
        for url, task in tasks.items():
            if task in done and not task.cancelled() and task.exception() is None and task.result():
                result[url] = task.result()
        return result

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        await self._cache.clear()


# 模板上下文里存放图片地址的字段（内置模板与旧版自定义模板都用这几个名字）
IMAGE_KEYS = frozenset({"icon", "hero_icon", "avatar"})


def collect_image_urls(value: Any) -> set[str]:
    """递归找出上下文里所有图片地址。"""
    found: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key in IMAGE_KEYS and isinstance(item, str) and item:
                found.add(item)
            else:
                found |= collect_image_urls(item)
    elif isinstance(value, list):
        for item in value:
            found |= collect_image_urls(item)
    return found


def replace_image_urls(value: Any, mapping: dict[str, str]) -> Any:
    """返回把图片地址换成 data URI 的新上下文（不修改原对象）。"""
    if isinstance(value, dict):
        return {
            key: (
                mapping.get(item, item)
                if key in IMAGE_KEYS and isinstance(item, str)
                else replace_image_urls(item, mapping)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [replace_image_urls(item, mapping) for item in value]
    return value


__all__ = [
    "IMAGE_KEYS",
    "ImageInliner",
    "collect_image_urls",
    "replace_image_urls",
    "to_data_uri",
]
