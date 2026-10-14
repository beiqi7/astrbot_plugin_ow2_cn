"""解析层回归测试：这些点都是从真实上游数据里踩出来的。

1. 数值 0 是有效数据（场均阵亡 0、消灭 0），不能当成"字段缺失"；
2. 近期数据以 recentMatchCount 为准，字段缺失才回落到 presetsSummaryData；
3. 英雄 gameTime 的单位是秒（对齐 Overstats 的 _readable_playtime）；
4. `level` 是称号等级，不是账号等级。
"""

from __future__ import annotations

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import _bootstrap

from ow2cn import parsers, ranks, render
from ow2cn.models import HeroStat, PlayerCard, PlayerProfile


class ParserTest(unittest.TestCase):
    def test_pick_keeps_zero(self) -> None:
        self.assertEqual(parsers.pick({"kill": 0}, "kill"), 0)
        self.assertIsNone(parsers.pick({"kill": None}, "kill"))
        self.assertIsNone(parsers.pick({"kill": ""}, "kill"))
        self.assertEqual(parsers.pick({"a": None, "b": 3}, "a", "b"), 3)

    def test_zero_kills_render_as_zero_not_dash(self) -> None:
        match = parsers.parse_match(
            {"matchId": "m", "kill": 0, "assist": 0, "death": 0, "mapGuid": "x"},
        )
        self.assertIsNotNone(match)
        self.assertEqual(match.kda_text, "0/0/0")

    def test_recent_prefers_recent_block_even_when_zero(self) -> None:
        recent = parsers.parse_recent(
            {"aveDeath": 0, "aveKill": 12, "aveAssist": 23, "winRate": "71.43"},
            {"aveDeath": 7, "aveKill": 14, "aveAssist": 12, "winRate": "57.91"},
        )
        self.assertIsNotNone(recent)
        self.assertEqual(recent.ave_death, 0)  # 不能被 summary 的 7 覆盖
        self.assertEqual(recent.ave_kill, 12)
        self.assertEqual(recent.win_rate, 71.43)
        # 场均阵亡 0 时 KDA 没有意义，返回 None，渲染成 "-"
        self.assertIsNone(recent.kda)

    def test_recent_falls_back_when_field_missing(self) -> None:
        recent = parsers.parse_recent({"aveKill": 8}, {"aveDeath": 5, "aveCure": 9000})
        self.assertIsNotNone(recent)
        self.assertEqual(recent.ave_death, 5)
        self.assertEqual(recent.ave_cure, 9000)

    def test_hero_playtime_is_seconds(self) -> None:
        short = HeroStat(hero="雾子", game_time_seconds=1800)  # 30 分钟
        self.assertEqual(short.game_time_text, "30 分钟")
        long_hero = HeroStat(hero="卢西奥", game_time_seconds=66270)  # 18.4 小时
        self.assertEqual(long_hero.game_time_text, "18.4 小时")
        # 真实数据里 5110 秒这类值应显示为小时而不是分钟
        self.assertEqual(HeroStat(hero="x", game_time_seconds=5110).game_time_text, "1.4 小时")
        self.assertEqual(HeroStat(hero="x").game_time_text, "-")

    def test_heroes_merge_sums_seconds(self) -> None:
        stats = parsers.parse_heroes(
            [{"heroName": "雾子", "matchSum": 2, "gameTime": 600}],
            [{"heroName": "雾子", "matchSum": 3, "gameTime": 900}],
        )
        self.assertEqual(len(stats), 1)
        self.assertEqual(stats[0].match_sum, 5)
        self.assertEqual(stats[0].game_time_seconds, 1500)

    def test_level_labelled_as_title_level(self) -> None:
        profile = PlayerProfile(
            query="光头大猩猩#5104",
            card=PlayerCard(name="光头大猩猩#5104", level=3, title="世界杯粉丝"),
            roles=[],
        )
        text = render.text_card(profile)
        self.assertIn("称号等级 3", text)
        self.assertNotIn("| 等级 3", text)

    def test_mode_aliases(self) -> None:
        # 生涯 / 休闲 / 开放 都落到非竞技（快速）数据集
        for alias in ("生涯", "休闲", "快速", "非竞技", "开放", "quick", "casual"):
            self.assertEqual(ranks.normalize_mode(alias), "quick", alias)
            self.assertEqual(ranks.game_mode_of(alias), "leisure", alias)
        for alias in ("竞技", "排位", "天梯", "competitive", "ranked"):
            self.assertEqual(ranks.normalize_mode(alias), "competitive", alias)
            self.assertEqual(ranks.game_mode_of(alias), "sport", alias)
        self.assertEqual(ranks.mode_label("quick"), "快速（非竞技）")

    def test_emoji_is_randomised_but_pool_respected(self) -> None:
        pool = ["🎮", "🍀"]
        for _ in range(20):
            self.assertIn(render.pick_emoji(pool), pool)
        # 空池回落到内置池，且默认池不是只有猩猩
        self.assertGreater(len(render.DEFAULT_EMOJIS), 5)
        self.assertIn("🦍", render.DEFAULT_EMOJIS)
        self.assertIn(render.pick_emoji(), render.DEFAULT_EMOJIS)
        # 传入 emoji 时按传入值渲染
        profile = PlayerProfile(query="t#1", card=PlayerCard(name="t#1"), roles=[])
        self.assertTrue(render.text_card(profile, emoji="🍀").startswith("🍀 t#1"))

    def test_career_view_hides_unranked_noise(self) -> None:
        from ow2cn.models import RoleStat

        career = PlayerProfile(
            query="t#1",
            mode="quick",
            card=PlayerCard(name="t#1"),
            roles=[
                RoleStat(role="healer", last_rank="未定级", match_sum=97, win_rate=54.64, kda=3.86),
            ],
        )
        text = render.text_card(career, emoji="🎮")
        self.assertIn("【职责数据】", text)
        self.assertIn("辅助：97 场", text)
        self.assertNotIn("未定级", text)
        self.assertIn("快速（非竞技）", text)

        ranked = PlayerProfile(
            query="t#1",
            mode="competitive",
            card=PlayerCard(name="t#1"),
            roles=[RoleStat(role="healer", last_rank="黄金 4", match_sum=26, win_rate=50.0)],
        )
        ranked_text = render.text_card(ranked, emoji="🎮")
        self.assertIn("【段位】", ranked_text)
        self.assertIn("辅助：黄金 4", ranked_text)

    def test_rank_label_variants(self) -> None:
        self.assertEqual(ranks.rank_label({"rank_name": "Gold", "rank_sub_tier": 4}), "黄金 4")
        self.assertEqual(
            ranks.rank_label({"rankName": "Grandmaster", "rank_sub_tier": 2}), "宗师 2"
        )
        self.assertEqual(ranks.rank_label({}), "未定级")
        self.assertEqual(ranks.rank_label(None, fallback_level=4), "钻石")


if __name__ == "__main__":
    unittest.main()
