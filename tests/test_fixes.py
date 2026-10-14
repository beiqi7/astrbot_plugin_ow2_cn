"""针对本轮修复的回归测试。

这些用例复刻真实适配器（aiocqhttp）的消息形态：
- @群友 会被转成 " @昵称(QQ) " 拼进 message_str，同时消息链里有对应的 At；
- 群聊里用「@机器人 触发」时，机器人自己的 At 也在消息链里。

旧实现里这两种形态会分别导致「拿 @文本 当 BattleTag 查询」和「把机器人自己
当成被查的人」，简化版事件桩发现不了，所以这里直接按真实形态构造事件。
"""

from __future__ import annotations

import pathlib
import sys
import tempfile
import unittest
from typing import ClassVar

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import _bootstrap
import fixtures
from _bootstrap import base_config, load_plugin, run_command, texts
from astrbot.api.event import AstrMessageEvent
from astrbot.api.message_components import At, Plain
from mock_dashen import MockDashenServer
from mock_overstats import MockOverstatsServer

from ow2cn import parsers, ranks, render
from ow2cn.cache import TTLCache
from ow2cn.sources import build_source

SELF_ID = "999"
SENDER = "10001"
TARGET_QQ = "12345"


class MentionResolutionTest(unittest.TestCase):
    """@ 解析：显式 tag 里的 @文本、@机器人 自身都要正确处理。"""

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

    def _event(self, *, message_str: str, chain, sender_id: str = SENDER) -> AstrMessageEvent:
        return AstrMessageEvent(
            message_str=message_str,
            group_id="9527",
            sender_id=sender_id,
            self_id=SELF_ID,
            chain=chain,
        )

    def test_at_group_member_resolves_binding_not_literal_text(self) -> None:
        """/ow 战绩 @张三：应查到张三的绑定，而不是把 "@张三(12345)" 当 BattleTag。"""
        self.plugin.store.set("user", TARGET_QQ, fixtures.TEST_TAG)
        # 适配器把 @群友 拼进文本，参数层拿到的 tag 就是这段 @文本
        event = self._event(
            message_str=f"ow 战绩  @张三({TARGET_QQ}) ",
            chain=[Plain(text="/ow 战绩 "), At(qq=TARGET_QQ, name="张三")],
            sender_id="20000",
        )
        target = self.plugin._resolve_target(event, f" @张三({TARGET_QQ}) ")
        self.assertEqual(target, fixtures.TEST_TAG)

    def test_at_self_wake_falls_back_to_sender_binding(self) -> None:
        """@机器人 /ow 战绩：机器人自己的 At 不能被当成被查的人。"""
        self.plugin.store.set("user", SENDER, fixtures.TEST_TAG)
        event = self._event(
            message_str="ow 战绩",
            chain=[At(qq=SELF_ID, name="机器人"), Plain(text=" /ow 战绩")],
        )
        target = self.plugin._resolve_target(event, "")
        self.assertEqual(target, fixtures.TEST_TAG)

    def test_at_self_plus_target_uses_target(self) -> None:
        """@机器人 @张三 /ow 战绩：跳过机器人，命中张三。"""
        self.plugin.store.set("user", TARGET_QQ, fixtures.TEST_TAG)
        event = self._event(
            message_str=f"ow 战绩  @张三({TARGET_QQ}) ",
            chain=[
                At(qq=SELF_ID, name="机器人"),
                At(qq=TARGET_QQ, name="张三"),
                Plain(text=" /ow 战绩 "),
            ],
            sender_id="20000",
        )
        target = self.plugin._resolve_target(event, f" @张三({TARGET_QQ}) ")
        self.assertEqual(target, fixtures.TEST_TAG)


class SplitIndexTagTest(unittest.TestCase):
    def setUp(self) -> None:
        from astrbot_plugin_ow2_cn.main import Ow2CnPlugin

        self.split = Ow2CnPlugin._split_index_and_tag

    def test_digit_after_tag_is_index_not_overwrite(self) -> None:
        # 旧实现：tag 先占位，后面的数字被丢进 tag，序号仍是 1 —— 查错人。
        self.assertEqual(self.split("光头大猩猩#5104", "2"), (2, "光头大猩猩#5104", None))

    def test_tag_then_mode_then_index(self) -> None:
        self.assertEqual(
            self.split("光头大猩猩#5104", "生涯", "3"),
            (3, "光头大猩猩#5104", "quick"),
        )

    def test_mention_with_spaced_nickname_is_dropped(self) -> None:
        # 昵称带空格时，适配器拼进来的 "@张 三(12345)" 会被切成两段参数
        self.assertEqual(self.split("@张", f"三({TARGET_QQ})", "2"), (2, "", None))

    def test_tag_with_hash_preferred_over_stray_word(self) -> None:
        self.assertEqual(
            self.split("光头大猩猩#5104", "哈哈"),
            (1, "光头大猩猩#5104", None),
        )

    def test_repeated_mode_word_not_taken_as_tag(self) -> None:
        self.assertEqual(self.split("生涯", "快速"), (1, "", "quick"))

    def test_mode_and_tag_for_commands_without_index(self) -> None:
        from astrbot_plugin_ow2_cn.main import Ow2CnPlugin

        split = Ow2CnPlugin._split_mode_and_tag
        self.assertEqual(split("生涯"), ("", "quick"))
        self.assertEqual(split("光头大猩猩#5104", "竞技"), ("光头大猩猩#5104", "competitive"))
        # 不需要序号的指令里，数字直接忽略，不会被当成 BattleTag 去查人
        self.assertEqual(split("5"), ("", None))


class ModeKeywordTest(unittest.TestCase):
    def test_keywords(self) -> None:
        self.assertEqual(ranks.parse_mode_keyword("生涯"), "quick")
        self.assertEqual(ranks.parse_mode_keyword(" QP "), "quick")
        self.assertEqual(ranks.parse_mode_keyword("排位"), "competitive")
        self.assertIsNone(ranks.parse_mode_keyword("光头大猩猩#5104"))
        # 认不出来的模式串仍按竞技处理（配置写错时的兜底）
        self.assertEqual(ranks.normalize_mode("whatever"), "competitive")


class RelativeTimeUnitTest(unittest.TestCase):
    """列表的 beginTs 是毫秒、明细的 startTime 是秒，两种都要渲染对。"""

    def test_seconds_and_millis_render_the_same(self) -> None:
        import time

        seconds = int(time.time()) - 3 * 3600
        self.assertEqual(render._relative_time(seconds), "3 小时前")
        self.assertEqual(render._relative_time(seconds * 1000), "3 小时前")

    def test_absurd_timestamp_does_not_raise(self) -> None:
        self.assertIsInstance(render._relative_time(10**18), str)

    def test_detail_card_start_time_in_seconds(self) -> None:
        import time

        from ow2cn.models import MatchDetail

        detail = MatchDetail(viewer="t#1", start_ts=int(time.time()) - 2 * 86400)
        self.assertIn("开赛 2 天前", render.match_detail_card(detail, emoji="🎮"))


class SourceFallbackTest(unittest.TestCase):
    def _close(self, source) -> None:
        _bootstrap.asyncio.new_event_loop().run_until_complete(source.close())

    def test_dashen_without_accounts_uses_filled_overstats(self) -> None:
        # 默认数据源是 dashen：只填了 Overstats 地址、忘了切数据源，也应该能用
        source = build_source(
            {
                "data_source": "dashen",
                "dashen": {"accounts": []},
                "overstats": {"base_url": "http://127.0.0.1:1"},
            },
        )
        try:
            self.assertEqual(source.name, "overstats")
        finally:
            self._close(source)

    def test_dashen_with_accounts_ignores_overstats(self) -> None:
        source = build_source(
            {
                "data_source": "dashen",
                "dashen": {"accounts": [{"role_id": "1", "token": "t"}]},
                "overstats": {"base_url": "http://127.0.0.1:1"},
            },
        )
        try:
            self.assertEqual(source.name, "dashen")
        finally:
            self._close(source)


class HeroMergeWinRateTest(unittest.TestCase):
    def test_win_rate_recomputed_after_merge(self) -> None:
        # presets: 21 场 11 胜 (52.38%)；open: 1 场 0 胜 → 合并 22 场 11 胜 = 50.0%
        presets = [
            {"heroGuid": "h1", "heroName": "堡垒", "matchSum": 21, "winSum": 11, "winRate": "52.38"}
        ]
        open_list = [
            {"heroGuid": "h1", "heroName": "堡垒", "matchSum": 1, "winSum": 0, "winRate": "0.00"}
        ]
        heroes = parsers.parse_heroes(presets, open_list)
        self.assertEqual(len(heroes), 1)
        self.assertEqual(heroes[0].match_sum, 22)
        self.assertEqual(heroes[0].win_sum, 11)
        self.assertEqual(heroes[0].win_rate, 50.0)


class HeroPerTenMinuteTest(unittest.TestCase):
    """英雄的伤害/治疗等数据用 statPerTenMinCount（每 10 分钟），跨队列按上场时长加权合并。"""

    DAMAGE = "603482350067647671"
    HEALING = "603482350067646913"
    KILLS = "603482350067646495"

    def test_per10_merged_by_play_time(self) -> None:
        # 预设队列打了 3 倍于开放队列的时长，合并值应偏向预设队列，而不是简单平均或取第一份。
        presets = [
            {
                "heroGuid": "h1",
                "heroName": "毛加",
                "matchSum": 17,
                "gameTime": 3000,
                "statPerTenMinCount": {self.DAMAGE: 10000, self.HEALING: 1200, self.KILLS: 18},
            }
        ]
        open_list = [
            {
                "heroGuid": "h1",
                "heroName": "毛加",
                "matchSum": 1,
                "gameTime": 1000,
                "statPerTenMinCount": {self.DAMAGE: 6000, self.HEALING: 800},
            }
        ]
        hero = parsers.parse_heroes(presets, open_list)[0]
        self.assertAlmostEqual(hero.per10["damage"], 9000.0)
        self.assertAlmostEqual(hero.per10["healing"], 1100.0)
        # 只有一个队列给了的指标，直接用那一份
        self.assertAlmostEqual(hero.per10["kills"], 18.0)

    def test_heroes_card_shows_per10_line(self) -> None:
        heroes = parsers.parse_heroes(
            [
                {
                    "heroGuid": "h1",
                    "heroName": "雾子",
                    "matchSum": 22,
                    "gameTime": 8695,
                    "statPerTenMinCount": {self.DAMAGE: 1327.43, self.HEALING: 9235.91},
                },
                {"heroGuid": "h2", "heroName": "堡垒", "matchSum": 5, "gameTime": 600},
            ],
        )
        from ow2cn.models import PlayerCard, PlayerProfile

        text = render.heroes_card(
            PlayerProfile(query="t#1", card=PlayerCard(name="t#1"), heroes=heroes),
            emoji="🎮",
        )
        self.assertIn("每10分钟 伤害 1,327 · 治疗 9,236 · 消灭 - · 阵亡 -", text)
        # 没有每 10 分钟数据的英雄不输出空行
        self.assertEqual(text.count("每10分钟"), 1)


class BanPlaceholderTest(unittest.TestCase):
    def test_zero_ban_guid_dropped(self) -> None:
        payload = {
            "detail": {
                "teamBanHeroGuids": ["h1", "0"],
                "enemyBanHeroGuids": ["0", ""],
            }
        }
        detail = parsers.parse_match_detail(payload)
        self.assertNotIn("0", detail.bans_team)
        self.assertEqual(len(detail.bans_team), 1)
        self.assertEqual(detail.bans_enemy, [])


class RankLevelZeroTest(unittest.TestCase):
    def test_level_zero_is_bronze_not_unranked(self) -> None:
        self.assertEqual(ranks.rank_label({"rankLevel": 0}), "青铜")

    def test_fallback_level_zero(self) -> None:
        self.assertEqual(ranks.rank_label(None, fallback_level=0), "青铜")


class OverstatsDetailPathTest(unittest.TestCase):
    def test_custom_detail_paths_forwarded(self) -> None:
        source = build_source(
            {
                "data_source": "overstats",
                "overstats": {
                    "base_url": "http://127.0.0.1:1",
                    "match_detail_path": "/custom/detail",
                    "match_detail_replies_path": "/custom/detail/replies",
                },
            }
        )
        try:
            self.assertEqual(source._match_detail_path, "/custom/detail")
            self.assertEqual(source._match_detail_replies_path, "/custom/detail/replies")
        finally:
            _bootstrap.asyncio.new_event_loop().run_until_complete(source.close())


class CacheSingleflightTest(unittest.TestCase):
    def test_concurrent_miss_calls_factory_once(self) -> None:
        cache = TTLCache(60)
        calls = {"n": 0}

        async def factory():
            calls["n"] += 1
            await _bootstrap.asyncio.sleep(0.02)
            return "v"

        async def run():
            results = await _bootstrap.asyncio.gather(
                cache.get_or_create("k", factory),
                cache.get_or_create("k", factory),
                cache.get_or_create("k", factory),
            )
            return results

        loop = _bootstrap.asyncio.new_event_loop()
        try:
            results = loop.run_until_complete(run())
        finally:
            loop.close()
        self.assertEqual(results, ["v", "v", "v"])
        self.assertEqual(calls["n"], 1)

    def test_ttl_zero_disables_store(self) -> None:
        cache = TTLCache(0)

        async def factory():
            return "v"

        loop = _bootstrap.asyncio.new_event_loop()
        try:
            loop.run_until_complete(cache.get_or_create("k", factory))
        finally:
            loop.close()
        self.assertEqual(len(cache), 0)


class MixedMatchPoolTest(unittest.TestCase):
    """服务端 `/dashen-match` 忽略 mode、返回竞技+快速混排列表带来的错位。

    实测（Overstats）：一次请求返回 24 条竞技 + 24 条快速，按时间交错；
    明细接口传 index 时也按同一份混排列表定位。插件必须自己按 instanceType
    分流，否则 `/ow 对局` 的竞技页里会混进快速对局，`/ow 明细 N` 也会查错模式。
    """

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
        self.loop.run_until_complete(self.plugin.service.clear_cache())
        self.plugin.store.clear()

    def call(self, command: str, args: str = "", **kwargs):
        return self.loop.run_until_complete(run_command(self.plugin, command, args, **kwargs))

    def _detail_request(self) -> dict:
        calls = [
            item
            for item in self.server.state.requests
            if item["path"].endswith("/dashen-match/detail")
        ]
        self.assertTrue(calls, "没有请求明细接口")
        return calls[-1]["body"]

    def test_competitive_list_excludes_quick_matches(self) -> None:
        # 第 1 页（5 条）本就全是竞技；第 2 页才是暴露问题的地方：
        # 旧实现直接切混排列表，会把夹在中间/后面的快速对局当成竞技第 6~8 场。
        text = texts(self.call("ow 对局", f"2 {fixtures.TEST_TAG}"))
        self.assertIn("艾兴瓦尔德", text)
        for quick_map in ("国王大道", "尼泊尔", "釜山"):
            self.assertNotIn(quick_map, text)

    def test_career_list_only_quick_matches(self) -> None:
        text = texts(self.call("ow 对局", f"生涯 {fixtures.TEST_TAG}"))
        self.assertIn("快速（非竞技）", text)
        self.assertIn("国王大道", text)
        self.assertIn("尼泊尔", text)
        self.assertNotIn("伊利奥斯", text)

    def test_detail_index_uses_mode_slice_not_pool_position(self) -> None:
        # 竞技第 3 场是 m-0003；混排池的第 3 条其实是 m-0002（旧实现会串到这里）
        self.call("ow 明细", f"3 {fixtures.TEST_TAG}")
        body = self._detail_request()
        self.assertEqual(body["match_id"], "m-0003")
        self.assertEqual(body["customer_token"], fixtures.TEST_TOKEN)

    def test_career_detail_index_uses_mode_slice(self) -> None:
        self.call("ow 明细", f"生涯 2 {fixtures.TEST_TAG}")
        body = self._detail_request()
        self.assertEqual(body["match_id"], "q-0002")

    def test_list_page_numbers_feed_detail_index(self) -> None:
        # 第 2 页的编号要接着第 1 页排（直接拿去 /ow 明细 才不会查错局）
        listing = texts(self.call("ow 对局", f"2 {fixtures.TEST_TAG}"))
        self.assertIn("6. ", listing)
        self.assertNotIn("1. ", listing)

    def test_list_and_detail_share_one_pool_request(self) -> None:
        self.call("ow 对局", fixtures.TEST_TAG)
        self.call("ow 对局", f"2 {fixtures.TEST_TAG}")
        self.call("ow 明细", f"2 {fixtures.TEST_TAG}")
        # 列表翻页与明细定位都从同一份池子里取，只有第一次回源
        self.assertEqual(self.server.state.count_path("/dashen-match"), 1)


class RecentDeathFallbackTest(unittest.TestCase):
    """上游 recentMatchCount 的 aveDeath 恒为 0（服务端补的默认值），要从对局现算。"""

    def test_death_derived_from_same_role_matches(self) -> None:
        recent = parsers.parse_recent(
            {"aveKill": 20, "aveAssist": 1, "aveDeath": 0, "winRate": "57.14", "roleType": "dps"},
            {},
            matches=[
                parsers.parse_match({"matchId": "a", "roleType": "dps", "death": 9}),
                parsers.parse_match({"matchId": "b", "roleType": "dps", "death": 6}),
                parsers.parse_match({"matchId": "c", "roleType": "healer", "death": 3}),
            ],
        )
        self.assertEqual(recent.ave_death, 7.5)
        self.assertEqual(recent.kda, 2.8)

    def test_no_matches_keeps_reported_value(self) -> None:
        recent = parsers.parse_recent({"aveKill": 8, "aveDeath": 0}, {})
        self.assertEqual(recent.ave_death, 0)


class RankScoreDisplayTest(unittest.TestCase):
    """上游 rankScore 是段位紧凑编码（黄金 5 = 295），不能当分数展示。"""

    def test_score_not_shown_as_points(self) -> None:
        text = ranks.rank_label(
            {"rank_name": "Gold", "rank_sub_tier": 5, "rankScore": 295},
        )
        self.assertEqual(text, "黄金 5")
        self.assertNotIn("分", text)

    def test_unranked_open_queue_has_no_score(self) -> None:
        text = ranks.rank_label(
            {"rank_name": "None", "rank_sub_tier": 0, "rankScore": 0},
        )
        self.assertEqual(text, "未定级")


class CardTemplateContractTest(unittest.TestCase):
    """内置卡片模板只允许引用 profile_context 真的会提供的字段。

    Jinja2 对未定义的变量默认渲染成空串，模板里写错字段名不会报错、只会默默少一块内容，
    所以这里把「模板变量 ⊆ 上下文键」做成硬约束。
    """

    KEYWORDS: ClassVar[frozenset[str]] = frozenset(
        {
            "true",
            "false",
            "none",
            "and",
            "or",
            "not",
            "is",
            "in",
            "if",
            "else",
            "elif",
            "endif",
            "for",
            "endfor",
            "set",
            "length",
            "default",
            "round",
            "int",
            "float",
            "string",
            "first",
            "last",
            "join",
            "trim",
            "abs",
            "min",
            "max",
            "loop",
        },
    )

    def _template(self) -> str:
        path = pathlib.Path(__file__).resolve().parent.parent / "templates" / "profile.html"
        return path.read_text(encoding="utf-8")

    def test_template_variables_exist_in_context(self) -> None:
        import re

        from ow2cn.models import PlayerCard, PlayerProfile

        template = self._template()
        context = render.profile_context(
            PlayerProfile(query="t#1", card=PlayerCard(name="t#1")),
            hero_limit=6,
        )
        loop_vars = set(re.findall(r"\{%-?\s*for\s+(\w+)\s+in\s+", template))
        chunk = re.compile(r"\{\{-?(.*?)-?\}\}|\{%-?\s*(.*?)\s*-?%\}", re.DOTALL)

        unknown: set[str] = set()
        for match in chunk.finditer(template):
            expression = match.group(1) or match.group(2) or ""
            expression = re.sub(r"'[^']*'|\"[^\"]*\"", " ", expression)  # 去掉字符串字面量
            expression = re.sub(r"\|\s*\w+", " ", expression)  # 去掉过滤器
            for token in re.finditer(r"(?<![\w.])([A-Za-z_]\w*)", expression):
                name = token.group(1)
                if name in self.KEYWORDS or name in loop_vars or name in context:
                    continue
                unknown.add(name)
        self.assertEqual(unknown, set(), f"模板引用了上下文里没有的字段：{sorted(unknown)}")

    def test_context_keeps_legacy_template_keys(self) -> None:
        """自定义模板用的是旧字段名，不能因为改版式把它们删掉。"""
        from ow2cn.models import PlayerCard, PlayerProfile

        context = render.profile_context(
            PlayerProfile(query="t#1", card=PlayerCard(name="t#1")),
            hero_limit=3,
        )
        for key in (
            "emoji",
            "name",
            "mode",
            "icon",
            "title",
            "level",
            "game_time",
            "total_match",
            "roles",
            "recent",
            "matches",
            "heroes",
        ):
            self.assertIn(key, context)


if __name__ == "__main__":
    unittest.main()
