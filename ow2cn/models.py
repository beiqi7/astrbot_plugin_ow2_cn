"""统一数据模型。

两种数据源（直连网易大神 / Overstats HTTP 服务）最终都会归一化成这里的结构，
渲染层只认识这些模型，不关心数据来自哪里。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PlayerCard:
    """基础资料卡。"""

    name: str = ""
    bnet_id: str = ""
    icon: str = ""
    title: str = ""
    level: int | None = None
    game_time_hours: float | None = None
    total_match: int | None = None
    customer_token: str = ""
    suspended_until_ms: int | None = None

    @property
    def suspended(self) -> bool:
        return bool(self.suspended_until_ms)


@dataclass
class RoleStat:
    """单一职责的竞技统计。"""

    role: str = ""
    last_rank: str = "未定级"
    max_rank: str = "未定级"
    match_sum: int | None = None
    win_rate: float | None = None
    kda: float | None = None
    max_win_streak: int | None = None


@dataclass
class RecentSummary:
    """近期表现汇总。"""

    role: str = ""
    match_sum: int | None = None
    win_rate: float | None = None
    ave_kill: float | None = None
    ave_assist: float | None = None
    ave_death: float | None = None
    ave_damage: float | None = None
    ave_cure: float | None = None
    ave_resist: float | None = None

    @property
    def kda(self) -> float | None:
        if self.ave_death is None:
            return None
        if self.ave_death == 0:
            return None
        kills = self.ave_kill or 0
        assists = self.ave_assist or 0
        return round((kills + assists) / self.ave_death, 2)


@dataclass
class HeroStat:
    """英雄使用统计。"""

    hero: str = ""
    role: str = ""
    match_sum: int | None = None
    win_sum: int | None = None
    win_rate: float | None = None
    game_time_seconds: float | None = None
    rank: str = ""
    icon: str = ""
    level: int | None = None
    # 每 10 分钟数据（statPerTenMinCount）：key 为 damage/healing/kills/deaths/assists。
    # 用每 10 分钟而不是场均，是因为同一局里常常换英雄，场均会被上场时长拉低，没法横向比。
    per10: dict[str, float] = field(default_factory=dict)

    @property
    def game_time_text(self) -> str:
        """上游 gameTime 的单位是秒（与 Overstats 的 _readable_playtime 口径一致）。"""
        seconds = self.game_time_seconds
        if not seconds:
            return "-"
        minutes = seconds / 60.0
        if minutes < 60:
            return f"{minutes:.0f} 分钟"
        return f"{minutes / 60.0:.1f} 小时"


@dataclass
class MatchItem:
    """一场对局。"""

    viewer: str = ""
    match_id: str = ""
    result: str = "未知"
    map_name: str = "未知地图"
    mode: str = ""
    hero: str = "未知英雄"
    hero_icon: str = ""
    role: str = ""
    kills: int | None = None
    assists: int | None = None
    deaths: int | None = None
    hero_damage: int | None = None
    cure: int | None = None
    resist_damage: int | None = None
    team_score: int | None = None
    opponent_score: int | None = None
    rank: str = ""
    begin_ts_ms: int | None = None

    @property
    def score_text(self) -> str:
        if self.team_score is None and self.opponent_score is None:
            return "-"
        return f"{self.team_score if self.team_score is not None else '-'}:{self.opponent_score if self.opponent_score is not None else '-'}"

    @property
    def kda_text(self) -> str:
        parts = [self.kills, self.assists, self.deaths]
        if all(p is None for p in parts):
            return "-"
        return "/".join("-" if p is None else str(p) for p in parts)


@dataclass
class MatchDetailPlayer:
    """单局明细里的一名玩家。"""

    name: str = ""
    hero: str = ""
    hero_icon: str = ""
    role: str = ""
    kills: int | None = None
    assists: int | None = None
    deaths: int | None = None
    hero_damage: int | None = None
    cure: int | None = None
    resist_damage: int | None = None
    damage_taken: int | None = None
    rank: str = ""
    highlights: list[str] = field(default_factory=list)
    is_self: bool = False

    @property
    def kda_text(self) -> str:
        parts = [self.kills, self.assists, self.deaths]
        if all(part is None for part in parts):
            return "-"
        return "/".join("-" if part is None else str(part) for part in parts)


@dataclass
class MatchDetail:
    """一场比赛的完整明细。"""

    viewer: str = ""
    match_id: str = ""
    result: str = "未知"
    map_name: str = "未知地图"
    mode: str = ""
    team_score: int | None = None
    opponent_score: int | None = None
    duration_sec: int | None = None
    start_ts: int | None = None
    bans_team: list[str] = field(default_factory=list)
    bans_enemy: list[str] = field(default_factory=list)
    teammates: list[MatchDetailPlayer] = field(default_factory=list)
    enemies: list[MatchDetailPlayer] = field(default_factory=list)
    self_player: MatchDetailPlayer | None = None

    @property
    def score_text(self) -> str:
        if self.team_score is None and self.opponent_score is None:
            return "-"
        team = "-" if self.team_score is None else self.team_score
        enemy = "-" if self.opponent_score is None else self.opponent_score
        return f"{team}:{enemy}"

    @property
    def duration_text(self) -> str:
        if not self.duration_sec:
            return "-"
        minutes, seconds = divmod(int(self.duration_sec), 60)
        return f"{minutes} 分 {seconds:02d} 秒"


@dataclass
class PlayerBehavior:
    """大神的「本周表现」（gameAction）：行为评价、点赞与被举报次数。"""

    comment: str = ""
    honors_received: int | None = None
    honors_sent: int | None = None
    reported: int | None = None


@dataclass
class PlayerProfile:
    """一次查询的完整结果。"""

    query: str = ""
    mode: str = "competitive"
    source: str = ""
    card: PlayerCard = field(default_factory=PlayerCard)
    roles: list[RoleStat] = field(default_factory=list)
    recent: RecentSummary | None = None
    # 赛季场均（presetsSummaryData），图片卡片里用来和近期表现对比
    season: RecentSummary | None = None
    behavior: PlayerBehavior | None = None
    matches: list[MatchItem] = field(default_factory=list)
    heroes: list[HeroStat] = field(default_factory=list)
