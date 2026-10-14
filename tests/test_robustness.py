"""v1.2.0 这一轮的健壮性修复。

- 本机 / 内网服务地址不走系统代理（Windows 注册表代理会把 127.0.0.1 劫持成 502）
- 配置容错：字符串数字、非法值不再让插件加载失败
- 大神多账号：token 失效时当次请求自动换账号；502/503/504 按 max_retry 重试
- TTLCache singleflight：单个等待者被取消不连累其他等待者；回源任务被取消时等待者接手
- 绑定文件损坏时先备份，不被下一次写入覆盖
- /ow 解绑群、群共用模式下 @群友 的提示、BattleTag 格式校验
"""

from __future__ import annotations

import asyncio
import json
import os
import pathlib
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import _bootstrap
import fixtures
from _bootstrap import base_config, load_plugin, run_command, texts
from mock_dashen import EXPECTED_ROLE_ID, EXPECTED_TOKEN, MockDashenServer

from ow2cn import config as conf
from ow2cn import net
from ow2cn.cache import TTLCache
from ow2cn.errors import SourceUnavailable
from ow2cn.service import OwService
from ow2cn.sources.dashen import DashenSource
from ow2cn.sources.overstats import OverstatsSource
from ow2cn.store import SCOPE_GROUP, SCOPE_USER, BindingStore


class LocalHostDetectionTest(unittest.TestCase):
    def test_local_and_private_hosts(self) -> None:
        for host in (
            "127.0.0.1",
            "localhost",
            "api.localhost",
            "::1",
            "[::1]",
            "10.0.0.8",
            "192.168.1.20",
            "172.17.0.2",
            "169.254.1.1",
            "overstats",  # docker compose 服务名
        ):
            with self.subTest(host=host):
                self.assertTrue(net.is_local_host(host))

    def test_public_hosts(self) -> None:
        for host in ("example.com", "datamsapi.ds.163.com", "8.8.8.8", ""):
            with self.subTest(host=host):
                self.assertFalse(net.is_local_host(host))

    def test_local_client_bypasses_env_proxy(self) -> None:
        """环境里配了一个必然连不上的代理：本地地址照样能直连。"""

        class _Ok(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"ok")

            def log_message(self, *_args: Any) -> None:
                return

        httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Ok)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        url = f"http://127.0.0.1:{httpd.server_address[1]}"
        patch = {"HTTP_PROXY": "http://127.0.0.1:9", "HTTPS_PROXY": "http://127.0.0.1:9"}
        saved = {key: os.environ.get(key) for key in (*patch, "NO_PROXY", "no_proxy")}

        async def fetch() -> int:
            client = net.build_client(url, timeout=5)
            try:
                return (await client.get(f"{url}/")).status_code
            finally:
                await client.aclose()

        try:
            for key in ("NO_PROXY", "no_proxy"):
                os.environ.pop(key, None)
            os.environ.update(patch)
            self.assertEqual(asyncio.run(fetch()), 200)
        finally:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
            httpd.shutdown()
            httpd.server_close()


class ConfigHelpersTest(unittest.TestCase):
    def test_as_int(self) -> None:
        self.assertEqual(conf.as_int("7", 5), 7)
        self.assertEqual(conf.as_int(7.9, 5), 7)
        self.assertEqual(conf.as_int("abc", 5), 5)
        self.assertEqual(conf.as_int(None, 5), 5)
        self.assertEqual(conf.as_int(True, 5), 5)
        self.assertEqual(conf.as_int(0, 5, minimum=1), 5)

    def test_as_float(self) -> None:
        self.assertEqual(conf.as_float(" 2.5 ", 1.0), 2.5)
        self.assertEqual(conf.as_float("inf", 1.0), 1.0)
        self.assertEqual(conf.as_float(-1, 1.0, minimum=0.0), 1.0)

    def test_as_bool(self) -> None:
        self.assertTrue(conf.as_bool("true"))
        self.assertTrue(conf.as_bool("开启"))
        self.assertFalse(conf.as_bool("false", True))
        self.assertFalse(conf.as_bool("0", True))
        self.assertTrue(conf.as_bool("什么鬼", True))
        self.assertTrue(conf.as_bool(None, True))

    def test_bad_numbers_do_not_break_service(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = BindingStore(pathlib.Path(tmp) / "b.json")
            service = OwService(
                {
                    "query": {
                        "match_limit": "abc",
                        "match_limit_max": "3",
                        "hero_limit": -2,
                        "cache_ttl": "oops",
                        "allow_any_battle_tag": "false",
                    },
                },
                store,
            )
        self.assertEqual(service.match_limit, 5)
        # 上限不能低于常规条数
        self.assertEqual(service.match_limit_max, 5)
        self.assertEqual(service.hero_limit, 5)
        self.assertFalse(service.allow_any_battle_tag)


class _FlakyHandler(BaseHTTPRequestHandler):
    """前 N 次请求返回 503，之后返回固定 profile；图片接口永远 503。"""

    def do_POST(self) -> None:
        state = self.server.state  # type: ignore[attr-defined]
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        state["paths"].append(self.path)
        if self.path.endswith("/image") or state["fail"] > 0:
            state["fail"] = max(0, state["fail"] - 1)
            self._send(503, b'{"ok":false}', "application/json")
            return
        body = json.dumps(
            {
                "ok": True,
                "profile_card": {"data": fixtures.search_payload()},
                "sport": {"data": fixtures.count_info_payload()},
            },
        ).encode("utf-8")
        self._send(200, body, "application/json")

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: Any) -> None:
        return


class OverstatsRetryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _FlakyHandler)
        self.httpd.state = {"fail": 0, "paths": []}  # type: ignore[attr-defined]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def tearDown(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

    @property
    def state(self) -> dict[str, Any]:
        return self.httpd.state  # type: ignore[attr-defined]

    def _run(self, max_retry: int, coro_factory):
        async def main():
            source = OverstatsSource(base_url=self.base_url, max_retry=max_retry, payload_ttl=0)
            try:
                return await coro_factory(source)
            finally:
                await source.close()

        return asyncio.run(main())

    def test_gateway_error_is_retried(self) -> None:
        self.state["fail"] = 1
        payload = self._run(1, lambda s: s._post_json("/api/v2/dashen-profile", {"bnet_id": "x"}))
        self.assertTrue(payload["ok"])
        self.assertEqual(len(self.state["paths"]), 2)

    def test_no_retry_when_disabled(self) -> None:
        self.state["fail"] = 1
        with self.assertRaises(SourceUnavailable):
            self._run(0, lambda s: s._post_json("/api/v2/dashen-profile", {"bnet_id": "x"}))
        self.assertEqual(len(self.state["paths"]), 1)

    def test_image_requests_are_not_retried(self) -> None:
        image = self._run(
            3, lambda s: s.render_image(fixtures.TEST_TAG, kind="profile", mode="competitive")
        )
        self.assertIsNone(image)
        self.assertEqual(len(self.state["paths"]), 1)


class DashenFailoverTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = MockDashenServer().__enter__()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.__exit__()

    def setUp(self) -> None:
        self.server.state.clear()

    def _tokens_used(self) -> list[str]:
        result = []
        for item in self.server.state.requests:
            headers = {k.lower(): v for k, v in item["headers"].items()}
            result.append(headers.get("gl-bigdata-auth-token", ""))
        return result

    def test_expired_token_fails_over_within_same_request(self) -> None:
        accounts = [
            {"name": "expired", "role_id": EXPECTED_ROLE_ID, "token": "expired-token"},
            {"name": "good", "role_id": EXPECTED_ROLE_ID, "token": EXPECTED_TOKEN},
        ]

        async def main():
            source = DashenSource(accounts, api_root=self.server.api_root, max_retry=0)
            try:
                first = await source.profile(fixtures.TEST_TAG)
                await source.clear_cache()
                second = await source.profile(fixtures.TEST_TAG)
                return first, second
            finally:
                await source.close()

        first, second = asyncio.run(main())
        self.assertEqual(first.card.name, fixtures.TEST_TAG)
        self.assertEqual(second.card.name, fixtures.TEST_TAG)
        tokens = self._tokens_used()
        # 失效账号只撞一次就进冷却，之后的请求（包括第二次查询）都走好账号
        self.assertEqual(tokens.count("expired-token"), 1)
        self.assertEqual(tokens[0], "expired-token")
        self.assertTrue(all(token == EXPECTED_TOKEN for token in tokens[1:]))
        # searchBnetAccount 的请求体也要换成好账号的 token
        search_bodies = [
            item["body"]
            for item in self.server.state.requests
            if item["path"].endswith("/searchBnetAccount")
        ]
        self.assertEqual(search_bodies[-1]["token"], EXPECTED_TOKEN)

    def test_all_accounts_expired_reports_auth_error(self) -> None:
        accounts = [
            {"name": "a", "role_id": EXPECTED_ROLE_ID, "token": "bad-1"},
            {"name": "b", "role_id": EXPECTED_ROLE_ID, "token": "bad-2"},
        ]

        async def main():
            source = DashenSource(accounts, api_root=self.server.api_root, max_retry=0)
            try:
                await source.profile(fixtures.TEST_TAG)
            finally:
                await source.close()

        from ow2cn.errors import AuthError

        with self.assertRaises(AuthError):
            asyncio.run(main())
        self.assertEqual(sorted(self._tokens_used()), ["bad-1", "bad-2"])

    def test_window_locks_do_not_pile_up_without_cache(self) -> None:
        accounts = [{"name": "good", "role_id": EXPECTED_ROLE_ID, "token": EXPECTED_TOKEN}]

        async def main():
            source = DashenSource(
                accounts, api_root=self.server.api_root, payload_ttl=0, max_retry=0
            )
            try:
                for index in range(5):
                    await source._match_window(f"token-{index}", "competitive", 1)
                return len(source._window_locks)
            finally:
                await source.close()

        self.assertLessEqual(asyncio.run(main()), 1)


class CacheCancellationTest(unittest.TestCase):
    def test_cancelled_waiter_does_not_cancel_others(self) -> None:
        async def main():
            cache = TTLCache(60)
            release = asyncio.Event()
            calls = 0

            async def factory():
                nonlocal calls
                calls += 1
                await release.wait()
                return "value"

            owner = asyncio.create_task(cache.get_or_create("k", factory))
            await asyncio.sleep(0)
            waiter_a = asyncio.create_task(cache.get_or_create("k", factory))
            waiter_b = asyncio.create_task(cache.get_or_create("k", factory))
            await asyncio.sleep(0)
            waiter_a.cancel()
            await asyncio.sleep(0)
            release.set()
            results = await asyncio.gather(owner, waiter_b)
            self.assertTrue(waiter_a.cancelled())
            return results, calls

        results, calls = asyncio.run(main())
        self.assertEqual(results, ["value", "value"])
        self.assertEqual(calls, 1)

    def test_waiter_takes_over_when_owner_cancelled(self) -> None:
        async def main():
            cache = TTLCache(60)
            calls = 0

            async def factory():
                nonlocal calls
                calls += 1
                if calls == 1:
                    await asyncio.sleep(10)  # 第一次回源会被取消
                return "fresh"

            owner = asyncio.create_task(cache.get_or_create("k", factory))
            await asyncio.sleep(0)
            waiter = asyncio.create_task(cache.get_or_create("k", factory))
            await asyncio.sleep(0)
            owner.cancel()
            result = await asyncio.wait_for(waiter, timeout=5)
            self.assertTrue(owner.cancelled())
            return result, calls

        result, calls = asyncio.run(main())
        self.assertEqual(result, "fresh")
        self.assertEqual(calls, 2)


class BindingStoreRecoveryTest(unittest.TestCase):
    def test_corrupt_file_is_backed_up_not_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "bindings.json"
            path.write_text('{"user": {"1": {"tag": "A#1"}', encoding="utf-8")  # 截断的 JSON
            warnings: list[str] = []

            class _Log:
                def warning(self, message: str) -> None:
                    warnings.append(message)

            store = BindingStore(path, logger=_Log())
            self.assertEqual(store.count(), {SCOPE_GROUP: 0, SCOPE_USER: 0})
            backups = list(pathlib.Path(tmp).glob("bindings.json.corrupt-*"))
            self.assertEqual(len(backups), 1)
            self.assertIn('"A#1"', backups[0].read_text(encoding="utf-8"))
            self.assertTrue(warnings and "已备份" in warnings[0])

            store.set(SCOPE_USER, "2", "B#2")
            self.assertIn('"A#1"', backups[0].read_text(encoding="utf-8"))
            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8"))["user"]["2"]["tag"], "B#2"
            )

    def test_missing_file_is_silent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = BindingStore(pathlib.Path(tmp) / "none.json")
            self.assertEqual(store.count()[SCOPE_USER], 0)
            self.assertEqual(list(pathlib.Path(tmp).iterdir()), [])


class GroupBindingCommandsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = MockDashenServer().__enter__()
        cls.loop = asyncio.new_event_loop()
        cls.tmp = tempfile.TemporaryDirectory()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.loop.close()
        cls.server.__exit__()
        cls.tmp.cleanup()

    def _plugin(self, name: str, **overrides: Any):
        return load_plugin(
            base_config(api_root=self.server.api_root, **overrides),
            pathlib.Path(self.tmp.name) / name,
        )

    def _call(self, plugin, command: str, args: str = "", **kwargs: Any) -> str:
        return texts(self.loop.run_until_complete(run_command(plugin, command, args, **kwargs)))

    def _close(self, plugin) -> None:
        self.loop.run_until_complete(plugin.terminate())

    def test_unbind_group_requires_admin_and_removes_binding(self) -> None:
        plugin = self._plugin("unbind_group")
        try:
            self._call(plugin, "ow 绑定群", fixtures.TEST_TAG, admin=True)
            self.assertEqual(plugin.store.count()[SCOPE_GROUP], 1)
            self.assertIn("只有管理员", self._call(plugin, "ow 解绑群", admin=False))
            self.assertEqual(plugin.store.count()[SCOPE_GROUP], 1)
            self.assertIn("已解除本群默认账号", self._call(plugin, "ow 解绑群", admin=True))
            self.assertIn("没有设置默认账号", self._call(plugin, "ow 解绑群", admin=True))
            self.assertIn("只能在群里", self._call(plugin, "ow 解绑群", admin=True, group_id=""))
        finally:
            self._close(plugin)

    def test_mention_in_group_scope_explains_mode(self) -> None:
        plugin = self._plugin("group_scope", binding={"scope": "group"})
        self.server.state.clear()
        try:
            text = self._call(plugin, "ow 战绩", at_qq="12345")
            self.assertIn("不支持 @群友", text)
            self.assertEqual(self.server.state.count_path("/searchBnetAccount"), 0)
        finally:
            self._close(plugin)

    def test_bind_rejects_malformed_discriminator(self) -> None:
        plugin = self._plugin("strict_tag", binding={"allow_relaxed_tag": False})
        try:
            for bad in ("光头大猩猩#", "光头大猩猩#abc", "#5104"):
                with self.subTest(tag=bad):
                    self.assertIn("昵称#数字", self._call(plugin, "ow 绑定", bad))
            self.assertEqual(plugin.store.count()[SCOPE_USER], 0)
            self.assertIn("绑定成功", self._call(plugin, "ow 绑定", fixtures.TEST_TAG))
        finally:
            self._close(plugin)

    def test_status_shows_version(self) -> None:
        from ow2cn import __version__

        plugin = self._plugin("status")
        try:
            self.assertIn(f"v{__version__}", self._call(plugin, "ow 状态"))
        finally:
            self._close(plugin)


if __name__ == "__main__":
    unittest.main()
