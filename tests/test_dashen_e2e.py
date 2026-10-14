"""端到端测试：插件 → 服务层 → 网易大神直连数据源 → 假上游 HTTP 服务。

覆盖点：
- /ow 绑定 + /ow 战绩 的完整链路与渲染内容
- @群友 取个人绑定
- 请求头（GL-*）与上游参数是否符合抓包约定
- 翻页、缓存、错误提示（token 失效 / 查无此人）
- 群绑定与权限
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
from mock_dashen import EXPECTED_ROLE_ID, EXPECTED_TOKEN, MockDashenServer

OTHER_QQ = "10002"


class DashenEndToEndTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = MockDashenServer().__enter__()
        cls.loop = _bootstrap.asyncio.new_event_loop()
        cls.tmp = tempfile.TemporaryDirectory()
        cls.plugin = load_plugin(
            base_config(api_root=cls.server.api_root),
            pathlib.Path(cls.tmp.name),
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.loop.run_until_complete(cls.plugin.terminate())
        cls.loop.close()
        cls.server.__exit__()
        cls.tmp.cleanup()

    def setUp(self) -> None:
        self.server.state.clear()
        self.loop.run_until_complete(self.plugin.service.clear_cache())
        self.plugin.store.clear()

    # --- 工具 ---

    def call(self, command: str, args: str = "", **kwargs):
        return self.loop.run_until_complete(run_command(self.plugin, command, args, **kwargs))

    # --- 用例 ---

    def test_bind_then_overview(self) -> None:
        bound = self.call("ow 绑定", fixtures.TEST_TAG)
        self.assertIn("绑定成功", texts(bound))
        self.assertIn(fixtures.TEST_TAG, texts(bound))

        text = texts(self.call("ow 战绩"))
        # 资料卡
        self.assertIn(fixtures.TEST_TAG, text)
        self.assertIn("称号等级 412", text)
        self.assertIn("总时长 1,063.3 小时", text)
        # 段位（三段职责都在）
        self.assertIn("钻石 4", text)
        self.assertIn("白金 5", text)
        self.assertIn("黄金 2", text)
        self.assertIn("胜率 58.3%", text)
        # 近期表现
        self.assertIn("66.7%", text)
        # 对局：地图/英雄名称由 guid 映射表解析
        self.assertIn("伊利奥斯", text)
        self.assertIn("卢西奥", text)
        self.assertIn("辅助/卢西奥", text)
        # 常用英雄
        self.assertIn("常用英雄", text)

    def test_upstream_request_shape(self) -> None:
        self.call("ow 绑定", fixtures.TEST_TAG)
        self.call("ow 战绩")

        path = "/v1/a19ld5tool/searchBnetAccount"
        self.assertIn(path, self.server.state.paths())
        search = next(
            item
            for item in self.server.state.requests
            if item["path"].endswith("/searchBnetAccount")
        )
        headers = {k.lower(): v for k, v in search["headers"].items()}
        self.assertEqual(headers["gl-bigdata-auth-token"], EXPECTED_TOKEN)
        self.assertEqual(headers["gl-bigdata-role-id"], EXPECTED_ROLE_ID)
        self.assertEqual(headers["gl-bigdata-dts"], "2026")
        self.assertEqual(headers["gl-bigdata-server"], "1")
        self.assertEqual(headers["gl-clienttype"], "60")
        self.assertIn("app/df_client", headers["user-agent"])
        self.assertEqual(search["body"]["name"], fixtures.TEST_TAG)
        self.assertEqual(search["body"]["roleId"], EXPECTED_ROLE_ID)

        count = next(
            item for item in self.server.state.requests if item["path"].endswith("/queryCountInfo")
        )
        self.assertEqual(count["query"]["gameMode"], "sport")
        self.assertEqual(count["query"]["token"], fixtures.TEST_TOKEN)

    def test_matches_pagination(self) -> None:
        self.call("ow 绑定", fixtures.TEST_TAG)
        text = texts(self.call("ow 对局", "2"))
        self.assertIn("第 2 页", text)
        list_calls = [
            item for item in self.server.state.requests if item["path"].endswith("/queryMatchList")
        ]
        # 上游每页 3 条、插件每页 5 条：第 2 页要 6~10 条，得往后翻到上游第 3 页（短页即末页）
        self.assertEqual([item["query"]["page"] for item in list_calls], ["1", "2", "3"])

    def test_page_two_numbering_matches_global_order(self) -> None:
        """插件第 2 页 ≠ 上游第 2 页：编号 6 必须真的是第 6 场（m-0006，艾兴瓦尔德）。

        旧实现把上游第 2 页的前几条（m-0004 釜山…）标成 6、7、8，和 /ow 明细 6 对不上。
        """
        self.call("ow 绑定", fixtures.TEST_TAG)
        lines = texts(self.call("ow 对局", "2")).splitlines()
        row6 = next(line for line in lines if line.startswith("6. "))
        self.assertIn("艾兴瓦尔德", row6)
        self.assertFalse(any(line.startswith("1. ") for line in lines))
        self.assertFalse(any("釜山" in line for line in lines))

    def test_detail_index_beyond_first_upstream_page(self) -> None:
        self.call("ow 绑定", fixtures.TEST_TAG)
        self.call("ow 明细", "7")
        detail_call = [
            item for item in self.server.state.requests if item["path"].endswith("/queryMatchInfo")
        ][-1]
        self.assertEqual(detail_call["query"]["matchId"], "m-0007")

    def test_list_pages_and_detail_share_upstream_pages(self) -> None:
        self.call("ow 绑定", fixtures.TEST_TAG)
        self.call("ow 对局")
        self.call("ow 对局", "2")
        self.call("ow 明细", "8")
        pages = [
            item["query"]["page"]
            for item in self.server.state.requests
            if item["path"].endswith("/queryMatchList")
        ]
        # 每个上游页只拉一次，翻页和明细都从同一份累积窗口里切
        self.assertEqual(sorted(pages), ["1", "2", "3"])

    def test_upstream_ignoring_page_param_stops_paging(self) -> None:
        self.server.state.ignore_page = True
        self.call("ow 绑定", fixtures.TEST_TAG)
        text = texts(self.call("ow 对局", "2"))
        # 第 2 页整页重复 → 视为到底，不会一直往后翻，也不会把重复数据当成新对局
        self.assertEqual(self.server.state.count_path("/queryMatchList"), 2)
        self.assertIn("6. ", text)
        self.assertEqual(text.count("伊利奥斯"), 0)

    def test_mode_keyword_on_heroes_command(self) -> None:
        """/ow 英雄 生涯：「生涯」是模式，不能被当成 BattleTag 去查人。"""
        self.call("ow 绑定", fixtures.TEST_TAG)
        text = texts(self.call("ow 英雄", "生涯"))
        self.assertIn("快速（非竞技）", text)
        count = next(
            item for item in self.server.state.requests if item["path"].endswith("/queryCountInfo")
        )
        self.assertEqual(count["query"]["gameMode"], "leisure")
        names = [
            item["body"].get("name")
            for item in self.server.state.requests
            if item["path"].endswith("/searchBnetAccount")
        ]
        self.assertNotIn("生涯", names)

    def test_mode_keyword_after_tag_on_profile(self) -> None:
        text = texts(self.call("ow 战绩", f"{fixtures.TEST_TAG} 生涯"))
        self.assertIn("快速（非竞技）", text)
        self.assertIn("称号等级 412", text)

    def test_profile_commands_share_one_count_request(self) -> None:
        self.call("ow 绑定", fixtures.TEST_TAG)
        self.call("ow 资料")
        self.call("ow 战绩")
        self.call("ow 英雄")
        # 三条指令要的对局条数不同（1 / 5 / 5），但只该回源一次 queryCountInfo
        self.assertEqual(self.server.state.count_path("/queryCountInfo"), 1)

    def test_heroes_command(self) -> None:
        self.call("ow 绑定", fixtures.TEST_TAG)
        text = texts(self.call("ow 英雄"))
        self.assertIn("英雄数据", text)
        self.assertIn("卢西奥", text)
        self.assertIn("132 场", text)

    def test_match_detail_default_first(self) -> None:
        """不带序号 = 最近一场（列表第一行）。"""
        self.call("ow 绑定", fixtures.TEST_TAG)
        listing = texts(self.call("ow 对局"))
        text = texts(self.call("ow 明细"))
        self.assertIn("单局明细", text)
        self.assertIn("第 1 场（最近一场）", text)
        # 明细里的地图应与列表第一行一致
        first_row = listing.splitlines()[2]
        self.assertIn("伊利奥斯", first_row)
        self.assertIn("伊利奥斯", text)
        self.assertIn("伊利奥斯", text)
        self.assertIn("我方", text)
        self.assertIn("禁用", text)
        self.assertIn(fixtures.TEST_TAG, text)
        detail_call = next(
            item for item in self.server.state.requests if item["path"].endswith("/queryMatchInfo")
        )
        self.assertEqual(detail_call["query"]["matchId"], "m-0001")

    def test_match_detail_by_index(self) -> None:
        self.call("ow 绑定", fixtures.TEST_TAG)
        text = texts(self.call("ow 明细", "2"))
        self.assertIn("单局明细", text)
        detail_call = [
            item for item in self.server.state.requests if item["path"].endswith("/queryMatchInfo")
        ][-1]
        # 序号 2 → 列表里的第 2 条（0 起始索引 1）
        self.assertEqual(detail_call["query"]["matchId"], "m-0002")

    def test_match_detail_explicit_tag(self) -> None:
        text = texts(self.call("ow 明细", f"1 {fixtures.TEST_TAG}"))
        self.assertIn("单局明细", text)
        self.assertIn("伊利奥斯", text)

    def test_split_index_and_tag(self) -> None:
        split = self.plugin._split_index_and_tag
        # 不带参数：序号默认为 1（最近一场），模式交由配置默认值
        self.assertEqual(split("", ""), (1, "", None))
        self.assertEqual(split("3", ""), (3, "", None))
        self.assertEqual(split(fixtures.TEST_TAG, ""), (1, fixtures.TEST_TAG, None))
        self.assertEqual(split("2", fixtures.TEST_TAG), (2, fixtures.TEST_TAG, None))
        # 模式关键词可以出现在任意位置
        self.assertEqual(split("快速", ""), (1, "", "quick"))
        self.assertEqual(split("生涯", fixtures.TEST_TAG), (1, fixtures.TEST_TAG, "quick"))
        self.assertEqual(split("2", "竞技"), (2, "", "competitive"))
        self.assertEqual(split(fixtures.TEST_TAG, "生涯"), (1, fixtures.TEST_TAG, "quick"))
        # 三个参数位：序号 + 模式 + BattleTag
        self.assertEqual(split("2", "生涯", fixtures.TEST_TAG), (2, fixtures.TEST_TAG, "quick"))

    def test_match_detail_mode_keyword(self) -> None:
        self.call("ow 绑定", fixtures.TEST_TAG)
        self.assertIn("单局明细", texts(self.call("ow 明细", "生涯")))
        list_calls = [
            item for item in self.server.state.requests if item["path"].endswith("/queryMatchList")
        ]
        self.assertTrue(list_calls)
        self.assertEqual(list_calls[-1]["query"]["gameMode"], "leisure")

    def test_card_command_without_matches(self) -> None:
        self.call("ow 绑定", fixtures.TEST_TAG)
        text = texts(self.call("ow 资料"))
        self.assertIn("称号等级 412", text)
        self.assertNotIn("最近对局", text)

    def test_mention_uses_target_binding(self) -> None:
        # 10001 绑定自己的号，10002 通过 @ 查询
        self.call("ow 绑定", fixtures.TEST_TAG, sender_id="10001")
        text = texts(self.call("ow 战绩", at_qq="10001", sender_id=OTHER_QQ))
        self.assertIn(fixtures.TEST_TAG, text)
        self.assertIn("称号等级 412", text)

    def test_mention_unbound_user_hint(self) -> None:
        text = texts(self.call("ow 战绩", at_qq="999999", sender_id=OTHER_QQ))
        self.assertIn("还没有绑定", text)
        self.assertIn("/ow 绑定", text)

    def test_unbound_query_hint(self) -> None:
        text = texts(self.call("ow 战绩", sender_id="777777"))
        self.assertIn("还没有绑定 BattleTag", text)

    def test_explicit_tag_query(self) -> None:
        text = texts(self.call("ow 战绩", fixtures.TEST_TAG))
        self.assertIn("称号等级 412", text)

    def test_explicit_tag_not_found(self) -> None:
        text = texts(self.call("ow 战绩", "不存在的玩家#9999"))
        self.assertIn("查不到玩家", text)
        self.assertIn("#数字", text)

    def test_mode_query_uses_leisure(self) -> None:
        self.call("ow 绑定", fixtures.TEST_TAG)
        self.call("ow 快速")
        count = next(
            item for item in self.server.state.requests if item["path"].endswith("/queryCountInfo")
        )
        self.assertEqual(count["query"]["gameMode"], "leisure")

    def test_career_command_uses_leisure(self) -> None:
        self.call("ow 绑定", fixtures.TEST_TAG)
        text = texts(self.call("ow 生涯"))
        self.assertIn("快速（非竞技）", text)
        count = next(
            item for item in self.server.state.requests if item["path"].endswith("/queryCountInfo")
        )
        self.assertEqual(count["query"]["gameMode"], "leisure")

    def test_career_alias(self) -> None:
        self.call("ow 绑定", fixtures.TEST_TAG)
        self.assertIn("快速（非竞技）", texts(self.call("ow 休闲")))

    def test_emoji_pool_from_config(self) -> None:
        self.plugin.config["render"]["emojis"] = ["🎯"]
        try:
            self.call("ow 绑定", fixtures.TEST_TAG)
            text = texts(self.call("ow 战绩"))
            self.assertTrue(text.startswith("🎯 "), text[:20])
            heroes = texts(self.call("ow 英雄"))
            self.assertTrue(heroes.startswith("🎯 "), heroes[:20])
        finally:
            self.plugin.config["render"]["emojis"] = []

    def test_cache_avoids_repeat_requests(self) -> None:
        self.call("ow 绑定", fixtures.TEST_TAG)
        self.call("ow 战绩")
        first = self.server.state.count_path("/queryCountInfo")
        self.call("ow 战绩")
        second = self.server.state.count_path("/queryCountInfo")
        # 第二次应命中插件缓存，不再打上游
        self.assertEqual(first, second)
        # BattleTag → token 也有缓存，searchBnetAccount 只调一次
        self.assertEqual(self.server.state.count_path("/searchBnetAccount"), 1)

    def test_clear_cache_command(self) -> None:
        self.call("ow 绑定", fixtures.TEST_TAG)
        self.call("ow 战绩")
        self.assertIn("已清空", texts(self.call("ow 清除缓存", admin=True)))
        self.assertIn("只有管理员", texts(self.call("ow 清除缓存", admin=False)))

    def test_group_binding_needs_admin(self) -> None:
        denied = texts(self.call("ow 绑定群", fixtures.TEST_TAG, admin=False))
        self.assertIn("只有管理员", denied)
        granted = texts(self.call("ow 绑定群", fixtures.TEST_TAG, admin=True))
        self.assertIn("本群默认账号", granted)
        # 其他人（未绑定）现在能查到群默认账号
        text = texts(self.call("ow 战绩", sender_id=OTHER_QQ))
        self.assertIn(fixtures.TEST_TAG, text)

    def test_unbind(self) -> None:
        self.call("ow 绑定", fixtures.TEST_TAG)
        self.assertIn("已解除", texts(self.call("ow 解绑")))
        self.assertIn("没有绑定记录", texts(self.call("ow 解绑")))

    def test_status_and_help(self) -> None:
        status = texts(self.call("ow 状态"))
        self.assertIn("网易大神直连", status)
        self.assertIn("绑定数量", status)
        help_text = texts(self.call("ow 帮助"))
        self.assertIn("/ow 绑定", help_text)

    def test_invalid_token_message(self) -> None:
        broken = load_plugin(
            base_config(api_root=self.server.api_root, token="bad-token"),
            pathlib.Path(self.tmp.name) / "broken",
        )
        self.loop.run_until_complete(broken.service.clear_cache())
        text = texts(
            self.loop.run_until_complete(
                run_command(broken, "ow 战绩", fixtures.TEST_TAG),
            ),
        )
        self.assertIn("token", text.lower())
        self.assertIn("失效", text)
        self.loop.run_until_complete(broken.terminate())

    def test_missing_credentials_config_error(self) -> None:
        unconfigured = load_plugin(
            base_config(api_root=self.server.api_root, token="", role_id=""),
            pathlib.Path(self.tmp.name) / "unconfigured",
        )
        text = texts(
            self.loop.run_until_complete(
                run_command(unconfigured, "ow 战绩", fixtures.TEST_TAG),
            ),
        )
        self.assertIn("没有可用的网易大神账号凭据", text)
        # 默认数据源是 dashen，新用户第一次看到的提示里也要指出零 token 的 overstats 方案
        self.assertIn("overstats", text)
        self.loop.run_until_complete(unconfigured.terminate())

    def test_group_bind_requires_full_tag(self) -> None:
        strict = load_plugin(
            base_config(api_root=self.server.api_root, binding={"allow_relaxed_tag": False}),
            pathlib.Path(self.tmp.name) / "strict",
        )
        text = texts(
            self.loop.run_until_complete(
                run_command(strict, "ow 绑定群", "光头大猩猩", admin=True),
            ),
        )
        self.assertIn("#数字", text)
        self.assertEqual(strict.store.count()["group"], 0)
        self.loop.run_until_complete(strict.terminate())

    def test_group_bind_rejected_in_user_scope(self) -> None:
        user_only = load_plugin(
            base_config(api_root=self.server.api_root, binding={"scope": "user"}),
            pathlib.Path(self.tmp.name) / "user_only",
        )
        text = texts(
            self.loop.run_until_complete(
                run_command(user_only, "ow 绑定群", fixtures.TEST_TAG, admin=True),
            ),
        )
        self.assertIn("不会生效", text)
        self.assertEqual(user_only.store.count()["group"], 0)
        self.loop.run_until_complete(user_only.terminate())

    def test_debug_log_never_contains_tokens(self) -> None:
        import logging

        records: list[str] = []

        class Capture(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                records.append(record.getMessage())

        debug_plugin = load_plugin(
            base_config(api_root=self.server.api_root, basic={"debug": True}),
            pathlib.Path(self.tmp.name) / "debug",
        )
        handler = Capture()
        previous_level = debug_plugin.log.level
        debug_plugin.log.addHandler(handler)
        debug_plugin.log.setLevel(logging.INFO)
        try:
            self.loop.run_until_complete(run_command(debug_plugin, "ow 战绩", fixtures.TEST_TAG))
        finally:
            debug_plugin.log.removeHandler(handler)
            debug_plugin.log.setLevel(previous_level)
            self.loop.run_until_complete(debug_plugin.terminate())
        joined = "\n".join(records)
        self.assertIn("/queryCountInfo", joined)
        self.assertIn("HTTP 200", joined)
        self.assertNotIn(EXPECTED_TOKEN, joined)
        self.assertNotIn(fixtures.TEST_TOKEN, joined)


if __name__ == "__main__":
    unittest.main()
