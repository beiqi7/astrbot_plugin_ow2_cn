"""端到端测试2：插件 → 服务层 → Overstats HTTP 数据源（插件端零 token）。

覆盖点：
- 通过 base_url 查询，插件配置里没有任何大神 token
- 服务端 bnet_not_found → 中文提示
- 可选鉴权头（Authorization: Bearer）
- /ow图 直接使用服务端渲染好的图片
- overstats 未填地址时回退到 dashen 直连
"""

from __future__ import annotations

import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import _bootstrap
import fixtures
from _bootstrap import base_config, load_plugin, run_command, texts
from mock_dashen import MockDashenServer
from mock_overstats import MockOverstatsServer


class OverstatsEndToEndTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = MockOverstatsServer().__enter__()
        cls.loop = _bootstrap.asyncio.new_event_loop()
        cls.tmp = tempfile.TemporaryDirectory()
        cls.plugin = load_plugin(
            base_config(data_source="overstats", overstats_base_url=cls.server.base_url),
            pathlib.Path(cls.tmp.name),
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.loop.run_until_complete(cls.plugin.terminate())
        cls.loop.close()
        cls.server.__exit__()
        cls.tmp.cleanup()

    def setUp(self) -> None:
        self.server.state.requests.clear()
        self.server.state.require_api_key = False
        self.loop.run_until_complete(self.plugin.service.clear_cache())
        self.plugin.store.clear()

    def call(self, command: str, args: str = "", **kwargs):
        return self.loop.run_until_complete(run_command(self.plugin, command, args, **kwargs))

    def test_overview_without_any_token(self) -> None:
        # 插件配置里的 dashen token 是假的，说明数据完全来自 Overstats 服务
        text = texts(self.call("ow 战绩", fixtures.TEST_TAG))
        self.assertIn(fixtures.TEST_TAG, text)
        self.assertIn("钻石 4", text)
        self.assertIn("伊利奥斯", text)
        self.assertIn("卢西奥", text)

        profile_calls = [
            item for item in self.server.state.requests if item["path"].endswith("/dashen-profile")
        ]
        self.assertTrue(profile_calls)
        self.assertEqual(profile_calls[-1]["body"]["bnet_id"], fixtures.TEST_TAG)

    def test_one_profile_request_serves_both_modes(self) -> None:
        # /dashen-profile 一次就带回 sport + leisure，竞技与生涯不该各打一次上游
        self.call("ow 战绩", fixtures.TEST_TAG)
        self.call("ow 生涯", fixtures.TEST_TAG)
        self.assertEqual(self.server.state.count_path("/dashen-profile"), 1)

    def test_player_not_found_passthrough(self) -> None:
        text = texts(self.call("ow 战绩", "查无此人#0000"))
        self.assertIn("查不到玩家", text)

    def test_api_key_header(self) -> None:
        self.plugin.config["overstats"]["api_key"] = "wrong-key"
        self.server.state.require_api_key = True
        self.server.state.api_key = "right-key"
        broken = load_plugin(
            base_config(
                data_source="overstats",
                overstats_base_url=self.server.base_url,
                overstats_api_key="wrong-key",
            ),
            pathlib.Path(self.tmp.name) / "bad_key",
        )
        text = texts(
            self.loop.run_until_complete(run_command(broken, "ow 战绩", fixtures.TEST_TAG)),
        )
        self.assertIn("鉴权失败", text)
        self.loop.run_until_complete(broken.terminate())

        # 换回正确的 key 就能查
        good = load_plugin(
            base_config(
                data_source="overstats",
                overstats_base_url=self.server.base_url,
                overstats_api_key="right-key",
            ),
            pathlib.Path(self.tmp.name) / "good_key",
        )
        ok_text = texts(
            self.loop.run_until_complete(run_command(good, "ow 战绩", fixtures.TEST_TAG)),
        )
        self.assertIn(fixtures.TEST_TAG, ok_text)
        self.loop.run_until_complete(good.terminate())
        self.plugin.config["overstats"]["api_key"] = ""

    def test_image_command_uses_server_render(self) -> None:
        results = self.call("ow 图", fixtures.TEST_TAG)
        self.assertTrue(results)
        images = results[-1].images
        self.assertTrue(images, f"期望返回图片，实际：{results[-1]}")
        self.assertIn("base64:", images[0].file)
        image_calls = [
            item for item in self.server.state.requests if item["path"].endswith("-profile/image")
        ]
        self.assertTrue(image_calls)

    def test_image_render_cached_within_ttl(self) -> None:
        # 服务端渲染一张图要 10~40 秒，连着查两次不该渲染两次
        self.call("ow 图", fixtures.TEST_TAG)
        self.call("ow 图", fixtures.TEST_TAG)
        self.assertEqual(self.server.state.count_path("/dashen-profile/image"), 1)

    def test_career_over_overstats(self) -> None:
        text = texts(self.call("ow 生涯", fixtures.TEST_TAG))
        self.assertIn("快速（非竞技）", text)
        self.assertIn(fixtures.TEST_TAG, text)

    def test_match_detail_over_overstats(self) -> None:
        text = texts(self.call("ow 明细", fixtures.TEST_TAG))
        self.assertIn("单局明细", text)
        self.assertIn("伊利奥斯", text)
        self.assertIn("我方", text)
        calls = [
            item
            for item in self.server.state.requests
            if item["path"].endswith("/dashen-match/detail")
        ]
        self.assertTrue(calls)
        # 用「与 /ow 对局 同序的 matchId + customer_token」精确定位
        self.assertEqual(calls[-1]["body"]["match_id"], "m-0001")
        self.assertEqual(calls[-1]["body"]["customer_token"], fixtures.TEST_TOKEN)

    def test_match_detail_career_mode(self) -> None:
        self.call("ow 明细", f"生涯 {fixtures.TEST_TAG}")
        calls = [
            item
            for item in self.server.state.requests
            if item["path"].endswith("/dashen-match/detail")
        ]
        # 生涯明细必须落到快速列表里的对局（q- 前缀），不能串成竞技
        self.assertTrue(str(calls[-1]["body"]["match_id"]).startswith("q-"))

    def test_match_detail_index_two(self) -> None:
        self.call("ow 明细", f"2 {fixtures.TEST_TAG}")
        calls = [
            item
            for item in self.server.state.requests
            if item["path"].endswith("/dashen-match/detail")
        ]
        self.assertEqual(calls[-1]["body"]["match_id"], "m-0002")

    def test_match_detail_image(self) -> None:
        self.plugin.config["render"]["use_image_card"] = True
        try:
            results = self.call("ow 明细", fixtures.TEST_TAG)
            images = results[-1].images
            self.assertTrue(images, f"期望明细图，实际：{results[-1]}")
            image_calls = [
                item
                for item in self.server.state.requests
                if item["path"].endswith("/dashen-match/detail/replies")
            ]
            self.assertTrue(image_calls)
            # 明细图必须用 match_id 精确定位，否则会按混排列表串到别的模式
            self.assertEqual(image_calls[-1]["body"]["match_id"], "m-0001")
            self.assertEqual(image_calls[-1]["body"]["customer_token"], fixtures.TEST_TOKEN)
        finally:
            self.plugin.config["render"]["use_image_card"] = False

    def test_status_shows_overstats_source(self) -> None:
        status = texts(self.call("ow 状态"))
        self.assertIn("Overstats 服务", status)
        self.assertIn(self.server.base_url, status)

    def test_fallback_to_dashen_when_base_url_missing(self) -> None:
        with MockDashenServer() as dashen:
            fallback = load_plugin(
                base_config(
                    data_source="overstats",
                    api_root=dashen.api_root,
                    overstats_base_url="",
                ),
                pathlib.Path(self.tmp.name) / "fallback",
            )
            status = texts(self.loop.run_until_complete(run_command(fallback, "ow 状态")))
            self.assertIn("网易大神直连", status)
            self.loop.run_until_complete(fallback.terminate())

    def test_config_error_when_nothing_configured(self) -> None:
        empty = load_plugin(
            base_config(
                data_source="overstats",
                token="",
                role_id="",
                overstats_base_url="",
            ),
            pathlib.Path(self.tmp.name) / "empty",
        )
        text = texts(
            self.loop.run_until_complete(run_command(empty, "ow 战绩", fixtures.TEST_TAG)),
        )
        self.assertIn("数据源配置不完整", text)
        self.loop.run_until_complete(empty.terminate())


class CardImageTest(unittest.TestCase):
    """图片卡片：默认用插件自带模板渲染，失败才退服务端图/文本。"""

    @classmethod
    def setUpClass(cls) -> None:
        from astrbot.api.star import Star

        cls.star = Star
        cls.server = MockOverstatsServer().__enter__()
        cls.loop = _bootstrap.asyncio.new_event_loop()
        cls.tmp = tempfile.TemporaryDirectory()
        cls.plugin = load_plugin(
            base_config(data_source="overstats", overstats_base_url=cls.server.base_url),
            pathlib.Path(cls.tmp.name),
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.star.render_available = False
        cls.loop.run_until_complete(cls.plugin.terminate())
        cls.loop.close()
        cls.server.__exit__()
        cls.tmp.cleanup()

    def setUp(self) -> None:
        self.star.render_available = False
        self.star.render_calls.clear()
        self.server.state.requests.clear()
        self.plugin.config["render"]["use_image_card"] = True
        self.plugin.config["render"]["image_source"] = "plugin"
        self.loop.run_until_complete(self.plugin.service.clear_cache())
        self.plugin.store.clear()

    def tearDown(self) -> None:
        self.plugin.config["render"]["use_image_card"] = False
        self.star.render_available = False

    def call(self, command: str, args: str = "", **kwargs):
        return self.loop.run_until_complete(run_command(self.plugin, command, args, **kwargs))

    def test_default_uses_plugin_template(self) -> None:
        self.star.render_available = True
        results = self.call("ow 战绩", fixtures.TEST_TAG)
        images = results[-1].images
        self.assertTrue(images, f"期望插件模板出图，实际：{results[-1]}")

        call = self.star.render_calls[-1]
        # 用的是插件自带的卡片模板（新版式里才有「表现对比」和「本周表现」）
        self.assertIn("表现对比", call["tmpl"])
        self.assertIn("本周表现", call["tmpl"])
        self.assertEqual(call["options"]["type"], "png")
        self.assertEqual(call["options"]["device_scale_factor_level"], "high")
        self.assertEqual(call["options"]["scale"], "device")
        # 模板数据里带上了对比条与本周表现
        self.assertTrue(call["data"]["stat_bars"])
        self.assertIn("comment", call["data"]["behavior"])
        # 英雄卡片带每 10 分钟的伤害/治疗（数据源 → 解析 → 模板上下文 全链路）
        lucio = next(h for h in call["data"]["heroes"] if h["hero"] == "卢西奥")
        cells = {cell["key"]: cell["value"] for cell in lucio["per10"]}
        self.assertEqual(cells["damage"], "4,210")
        self.assertEqual(cells["healing"], "11,820")
        # 走插件模板时不该再去要服务端渲染图
        self.assertEqual(self.server.state.count_path("/dashen-profile/image"), 0)

    def test_card_shows_more_matches_than_text_card(self) -> None:
        self.star.render_available = True
        self.call("ow 图", fixtures.TEST_TAG)
        self.assertGreaterEqual(len(self.star.render_calls[-1]["data"]["matches"]), 6)
        self.assertEqual(len(self.star.render_calls[-1]["data"]["heroes"]), 6)

    def test_falls_back_to_server_image_when_render_fails(self) -> None:
        self.star.render_available = False
        results = self.call("ow 图", fixtures.TEST_TAG)
        images = results[-1].images
        self.assertTrue(images, f"期望回落到服务端图，实际：{results[-1]}")
        self.assertTrue(self.server.state.count_path("/dashen-profile/image"))

    def test_server_source_skips_local_render(self) -> None:
        self.plugin.config["render"]["image_source"] = "server"
        results = self.call("ow 图", fixtures.TEST_TAG)
        self.assertTrue(results[-1].images)
        self.assertTrue(self.server.state.count_path("/dashen-profile/image"))
        self.assertEqual(self.star.render_calls, [])

    def test_text_fallback_truncates_extra_matches(self) -> None:
        # 图片取 12 场对局；渲染不出来又退到文本时，不该把 12 行都刷出来
        self.star.render_available = False
        original = self.plugin.service.rendered_image

        async def no_image(*_args, **_kwargs):
            return None

        self.plugin.service.rendered_image = no_image
        try:
            text = texts(self.call("ow 图", fixtures.TEST_TAG))
        finally:
            self.plugin.service.rendered_image = original
        self.assertIn("【最近对局】", text)
        self.assertIn("  5. ", text)
        self.assertNotIn("  6. ", text)

    def test_image_scale_config_respected(self) -> None:
        self.star.render_available = True
        self.plugin.config["render"]["image_scale"] = "ultra"
        self.call("ow 图", fixtures.TEST_TAG)
        self.assertEqual(
            self.star.render_calls[-1]["options"]["device_scale_factor_level"],
            "ultra",
        )


if __name__ == "__main__":
    unittest.main()
