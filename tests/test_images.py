"""图片卡片的外链图片内嵌（v1.2.1：修复「部分内嵌图片显示不全」）。

文转图服务截图时，外链图片（海外 CDN、单张 110~155 KB）可能只下载了一半，
截出来的图标只有上半截。插件现在出图前先把图片下载、缩小并换成 data URI。
"""

from __future__ import annotations

import asyncio
import base64
import io
import pathlib
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, ClassVar

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import fixtures
from _bootstrap import base_config, load_plugin, run_command
from mock_overstats import MockOverstatsServer

from ow2cn import images

try:
    from PIL import Image

    HAS_PIL = True
except ImportError:  # pragma: no cover - 没装 Pillow 时跳过缩放相关断言
    HAS_PIL = False


def _png(size: int = 256) -> bytes:
    """生成一张带透明通道的测试 PNG（没有 Pillow 时用一张 1x1 的固定 PNG）。"""
    if not HAS_PIL:
        return base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
        )
    image = Image.new("RGBA", (size, size), (245, 166, 35, 255))
    for x in range(size):
        for y in range(size // 4):
            image.putpixel((x, y), (0, 0, 0, 0))  # 顶部透明，验证透明通道被保留
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


class _ImageHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        state = self.server.state  # type: ignore[attr-defined]
        state["hits"].append(self.path)
        if self.path.startswith("/missing"):
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.path.startswith("/slow"):
            time.sleep(state["slow_seconds"])
        body = b"<html>not an image</html>" if self.path.startswith("/html") else state["png"]
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def log_message(self, *_args: Any) -> None:
        return


class _ImageServer:
    def __enter__(self) -> _ImageServer:
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _ImageHandler)
        self.httpd.state = {"hits": [], "png": _png(), "slow_seconds": 3.0}  # type: ignore[attr-defined]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

    @property
    def state(self) -> dict[str, Any]:
        return self.httpd.state  # type: ignore[attr-defined]

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.httpd.server_address[1]}{path}"


class ContextWalkTest(unittest.TestCase):
    CONTEXT: ClassVar[dict[str, Any]] = {
        "icon": "https://a.example/avatar.png",
        "name": "x",
        "heroes": [{"hero": "卢西奥", "icon": "https://a.example/lucio.png"}],
        "matches": [{"hero_icon": "https://a.example/lucio.png"}, {"hero_icon": ""}],
        "behavior": None,
    }

    def test_collects_all_image_fields(self) -> None:
        self.assertEqual(
            images.collect_image_urls(self.CONTEXT),
            {"https://a.example/avatar.png", "https://a.example/lucio.png"},
        )

    def test_replace_returns_new_context(self) -> None:
        mapping = {"https://a.example/lucio.png": "data:image/png;base64,AAAA"}
        replaced = images.replace_image_urls(self.CONTEXT, mapping)
        self.assertEqual(replaced["heroes"][0]["icon"], "data:image/png;base64,AAAA")
        self.assertEqual(replaced["matches"][0]["hero_icon"], "data:image/png;base64,AAAA")
        # 没有替换结果的保持原链接，空值保持空
        self.assertEqual(replaced["icon"], "https://a.example/avatar.png")
        self.assertEqual(replaced["matches"][1]["hero_icon"], "")
        # 原上下文不被修改
        self.assertEqual(self.CONTEXT["heroes"][0]["icon"], "https://a.example/lucio.png")


class DataUriTest(unittest.TestCase):
    def test_rejects_non_images(self) -> None:
        self.assertIsNone(images.to_data_uri(b"<html></html>"))
        self.assertIsNone(images.to_data_uri(b""))

    def test_too_large_without_shrink_keeps_link(self) -> None:
        """没有 Pillow（缩不下来）时，大图不内嵌，免得把请求体撑到几 MB。"""
        big = b"\x89PNG\r\n\x1a\n" + b"\0" * (images.MAX_INLINE_BYTES + 1)
        original = images._shrink
        images._shrink = lambda _data: None
        try:
            self.assertIsNone(images.to_data_uri(big))
            self.assertTrue(images.to_data_uri(big[:1024]).startswith("data:image/png;base64,"))
        finally:
            images._shrink = original

    @unittest.skipUnless(HAS_PIL, "需要 Pillow")
    def test_shrinks_and_keeps_alpha(self) -> None:
        raw = _png(256)
        uri = images.to_data_uri(raw)
        self.assertIsNotNone(uri)
        header, encoded = uri.split(",", 1)
        self.assertIn(header, {"data:image/webp;base64", "data:image/png;base64"})
        data = base64.b64decode(encoded)
        self.assertLess(len(data), len(raw))
        with Image.open(io.BytesIO(data)) as decoded:
            self.assertLessEqual(max(decoded.size), images.MAX_EDGE)
            self.assertIn("A", decoded.getbands())


class InlinerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.server = _ImageServer().__enter__()

    def tearDown(self) -> None:
        self.server.__exit__()

    def _inline(self, urls, *, budget: float = 5.0, allow_private: bool = True, repeat: int = 1):
        async def main():
            inliner = images.ImageInliner(timeout=5, allow_private_hosts=allow_private)
            try:
                result = {}
                for _ in range(repeat):
                    result = await inliner.inline(urls, budget=budget)
                return result
            finally:
                await inliner.close()

        return asyncio.run(main())

    def test_inlines_and_caches(self) -> None:
        url = self.server.url("/hero.png")
        result = self._inline([url, url], repeat=3)
        self.assertTrue(result[url].startswith("data:image/"))
        # 同一张图三次出图只下载一次
        self.assertEqual(len(self.server.state["hits"]), 1)

    def test_failures_are_skipped(self) -> None:
        missing, html = self.server.url("/missing.png"), self.server.url("/html.png")
        self.assertEqual(self._inline([missing, html]), {})

    def test_failure_is_cached_briefly(self) -> None:
        missing = self.server.url("/missing.png")
        self._inline([missing], repeat=3)
        self.assertEqual(len(self.server.state["hits"]), 1)

    def test_budget_limits_waiting(self) -> None:
        fast, slow = self.server.url("/hero.png"), self.server.url("/slow.png")
        started = time.monotonic()
        result = self._inline([fast, slow], budget=1.0)
        self.assertLess(time.monotonic() - started, 2.5)
        self.assertIn(fast, result)
        self.assertNotIn(slow, result)

    def test_private_hosts_are_not_fetched_by_default(self) -> None:
        url = self.server.url("/hero.png")
        self.assertEqual(self._inline([url, "file:///etc/passwd"], allow_private=False), {})
        self.assertEqual(self.server.state["hits"], [])


class CardRenderInlineTest(unittest.TestCase):
    """端到端：/ow 图 交给 html_render 的数据里，图片已经是 data URI。"""

    @classmethod
    def setUpClass(cls) -> None:
        from astrbot.api.star import Star

        cls.star = Star
        cls.images = _ImageServer().__enter__()
        cls.server = MockOverstatsServer().__enter__()
        cls.loop = asyncio.new_event_loop()
        cls.tmp = tempfile.TemporaryDirectory()
        cls.plugin = load_plugin(
            base_config(
                data_source="overstats",
                overstats_base_url=cls.server.base_url,
                render={"use_image_card": True, "inline_images": True},
            ),
            pathlib.Path(cls.tmp.name),
        )
        # 测试图片服务在本机，打开测试开关允许下载
        cls.plugin.image_inliner = images.ImageInliner(timeout=5, allow_private_hosts=True)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.star.render_available = False
        cls.loop.run_until_complete(cls.plugin.terminate())
        cls.loop.close()
        cls.server.__exit__()
        cls.images.__exit__()
        cls.tmp.cleanup()

    def _render_data(self) -> dict[str, Any]:
        self.star.render_available = True
        self.star.render_calls.clear()
        self.loop.run_until_complete(run_command(self.plugin, "ow 图", fixtures.TEST_TAG))
        return self.star.render_calls[-1]["data"]

    def test_card_images_become_data_uris(self) -> None:
        original = self.plugin._inline_card_images

        async def with_local_urls(context, *, deadline):
            # 把卡片里的图片地址指向本机测试图片服务，其余流程保持真实
            local = {
                url: self.images.url(f"/img{i}.png")
                for i, url in enumerate(sorted(images.collect_image_urls(context)))
            }
            return await original(images.replace_image_urls(context, local), deadline=deadline)

        self.plugin._inline_card_images = with_local_urls
        try:
            data = self._render_data()
        finally:
            self.plugin._inline_card_images = original
        urls = images.collect_image_urls(data)
        self.assertTrue(urls, "卡片里应该有图片")
        self.assertTrue(all(url.startswith("data:image/") for url in urls), urls)

    def test_disabled_keeps_original_links(self) -> None:
        self.plugin.config["render"]["inline_images"] = False
        try:
            data = self._render_data()
        finally:
            self.plugin.config["render"]["inline_images"] = True
        urls = images.collect_image_urls(data)
        self.assertTrue(urls)
        self.assertFalse(any(url.startswith("data:") for url in urls))


if __name__ == "__main__":
    unittest.main()
