"""把上游原始 JSON 归一化成 ow2cn.models 中的数据模型。

两种数据源拿到的都是网易大神的原始结构（Overstats 也是原样透传），
所以解析逻辑共用一份，并且全部写成"字段缺失不报错"的容错风格：
上游字段命名在不同接口间存在 snake_case / camelCase 混用。
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from . import ranks, resources
from .models import (
    HeroStat,
    MatchDetail,
    MatchDetailPlayer,
    MatchItem,
    PlayerBehavior,
    PlayerCard,
    RecentSummary,
    RoleStat,
)


def safe_int(value: Any) -> int | None:
    try:
        if value in (None, ""):
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


def safe_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def pick(data: Any, *keys: str) -> Any:
    """按顺序取第一个「有值」字段。

    注意：数值 0 视为有效值（例如场均阵亡 0、消灭 0 都是真实数据），
    只有 None 与空串才当作缺失。
    """
    if not isinstance(data, dict):
        return None
    for key in keys:
        if key not in data:
            continue
        value = data[key]
        if value is None or value == "":
            continue
        return value
    return None


def parse_card(data: Any, *, fallback_name: str = "") -> PlayerCard:
    """searchBnetAccount / queryCard 的 data 段。"""
    if not isinstance(data, dict):
        data = {}
    risk = data.get("riskStatus") if isinstance(data.get("riskStatus"), dict) else {}
    card = PlayerCard(
        name=str(pick(data, "name", "battleTag", "bnetName") or fallback_name),
        bnet_id=str(pick(data, "bnetId", "bnet_id") or ""),
        icon=str(pick(data, "icon", "avatar") or ""),
        title=str(pick(data, "title") or ""),
        level=safe_int(pick(data, "level", "titleLevel")),
        game_time_hours=safe_float(pick(data, "gameTime", "game_time")),
        total_match=safe_int(pick(data, "totalMatchNum", "totalMatch")),
        customer_token=str(pick(data, "customerToken", "customer_token") or ""),
        suspended_until_ms=safe_int(pick(risk, "expireTime")),
    )
    return card


def parse_roles(guide_count_data: Any) -> list[RoleStat]:
    """queryCountInfo → guideCountData：各职责的竞技卡片。"""
    if not isinstance(guide_count_data, list):
        return []
    result: list[RoleStat] = []
    for item in guide_count_data:
        if not isinstance(item, dict):
            continue
        result.append(
            RoleStat(
                role=str(pick(item, "roleType", "role") or ""),
                last_rank=ranks.rank_label(item.get("lastRankInfo")),
                max_rank=ranks.rank_label(item.get("maxRankInfo")),
                match_sum=safe_int(pick(item, "matchSum", "matchCount")),
                win_rate=safe_float(pick(item, "winRate")),
                kda=safe_float(pick(item, "kda", "KDA")),
                max_win_streak=safe_int(pick(item, "maxWinStreak")),
            ),
        )
    return result


def _present(container: Any, *keys: str) -> Any:
    """按 key 存在性取值（0 也算有值），不存在返回 None。"""
    if not isinstance(container, dict):
        return None
    for key in keys:
        if key in container and container[key] not in (None, ""):
            return container[key]
    return None


def _recent_value(source: Any, summary: Any, *keys: str) -> Any:
    """近期数据以 recentMatchCount 为准，字段缺失时才回落到 summary（与 Overstats 口径一致）。"""
    value = _present(source, *keys)
    if value is not None:
        return value
    return _present(summary, *keys)


def _recent_death_average(matches: Any, role: str) -> float | None:
    """用同一批对局现算场均阵亡。

    实测上游（含 Overstats 服务端）的 `recentMatchCount` 里 `aveDeath` 恒为 0
    —— 那是服务端补的默认值，不是真实数据：同一份数据里的 matchList 明细
    平均阵亡 7 上下，`0` 会同时毁掉"场均阵亡"和 KDA 两项展示。
    其余字段（winRate/aveDamage/aveCure/aveKill）都与「按 roleType 过滤后
    的对局列表均值」吻合，所以阵亡也用同一批对局算出来才对得上。
    """
    if not isinstance(matches, list) or not matches:
        return None
    picked = [item for item in matches if role and item.role == role] or list(matches)
    deaths = [item.deaths for item in picked if item.deaths is not None]
    if not deaths:
        return None
    return round(sum(deaths) / len(deaths), 2)


def parse_recent(
    recent_match_count: Any,
    summary: Any = None,
    *,
    matches: Any = None,
) -> RecentSummary | None:
    """queryCountInfo → recentMatchCount（近期表现），字段缺失时用 presetsSummaryData 兜底。

    `matches` 传当前模式的对局列表时，会用它补掉上游恒为 0 的场均阵亡。
    """
    source = recent_match_count if isinstance(recent_match_count, dict) else {}
    summary = summary if isinstance(summary, dict) else {}
    if not source and not summary:
        return None
    recent = RecentSummary(
        role=str(_recent_value(source, summary, "roleType", "role") or ""),
        match_sum=safe_int(_recent_value(source, summary, "matchSum", "matchCount")),
        win_rate=safe_float(_recent_value(source, summary, "winRate")),
        ave_kill=safe_float(_recent_value(source, summary, "aveKill")),
        ave_assist=safe_float(_recent_value(source, summary, "aveAssist")),
        ave_death=safe_float(_recent_value(source, summary, "aveDeath")),
        ave_damage=safe_float(_recent_value(source, summary, "aveDamage", "aveHeroDamage")),
        ave_cure=safe_float(_recent_value(source, summary, "aveCure")),
        ave_resist=safe_float(_recent_value(source, summary, "aveResistDamage")),
    )
    if not recent.ave_death:
        # 上游的 aveDeath 常是服务端补的 0，用同一批对局现算才有意义；
        # 算不出来（没给对局）时保留原值，不要凭空抹成 None。
        derived = _recent_death_average(matches, recent.role)
        if derived is not None:
            recent.ave_death = derived
    return recent


# 英雄统计里的指标 guid → 插件内部字段名。
# 名称来自网易大神 query_tool.json 的 heroAttrList（valueType=通用数据），
# 例如 603482350067647671 = 对英雄伤害量、603482350067646913 = 治疗量。
HERO_STAT_GUIDS = {
    "603482350067647671": "damage",
    "603482350067646913": "healing",
    "603482350067646495": "kills",
    "603482350067646506": "deaths",
    "603482350067648392": "assists",
}


def _hero_per10(item: dict[str, Any]) -> dict[str, float]:
    raw = item.get("statPerTenMinCount")
    if not isinstance(raw, dict):
        return {}
    result: dict[str, float] = {}
    for guid, key in HERO_STAT_GUIDS.items():
        value = safe_float(raw.get(guid))
        if value is not None:
            result[key] = value
    return result


def _hero_games(hero_list: Any) -> list[HeroStat]:
    if not isinstance(hero_list, list):
        return []
    result: list[HeroStat] = []
    for item in hero_list:
        if not isinstance(item, dict):
            continue
        guid = pick(item, "heroGuid", "heroId")
        name = str(pick(item, "heroName", "name") or "") or resources.hero_name(guid)
        role = str(pick(item, "roleType", "role") or "") or resources.hero_role(guid)
        result.append(
            HeroStat(
                hero=name or "未知英雄",
                role=role,
                match_sum=safe_int(pick(item, "matchSum", "matchCount")),
                win_sum=safe_int(pick(item, "winSum", "winCount")),
                win_rate=safe_float(pick(item, "winRate")),
                game_time_seconds=safe_float(
                    pick(item, "gameTime", "playTime", "gameTimeSeconds"),
                ),
                rank=ranks.rank_label(item.get("heroRankInfo")),
                icon=str(pick(item, "heroIcon") or "") or resources.hero_icon(guid),
                level=safe_int(pick(item, "heroLevel")),
                per10=_hero_per10(item),
            ),
        )
    return result


def parse_season(summary: Any) -> RecentSummary | None:
    """presetsSummaryData → 赛季场均（与近期表现同结构，便于对比展示）。"""
    if not isinstance(summary, dict) or not summary:
        return None
    return RecentSummary(
        match_sum=safe_int(pick(summary, "matchSum", "matchCount")),
        win_rate=safe_float(pick(summary, "winRate")),
        ave_kill=safe_float(pick(summary, "aveKill")),
        ave_assist=safe_float(pick(summary, "aveAssist")),
        ave_death=safe_float(pick(summary, "aveDeath")),
        ave_damage=safe_float(pick(summary, "aveHeroDamage", "aveDamage")),
        ave_cure=safe_float(pick(summary, "aveCure")),
        ave_resist=safe_float(pick(summary, "aveResistDamage")),
    )


def parse_behavior(game_action: Any) -> PlayerBehavior | None:
    """queryCountInfo → gameAction（大神的「本周表现」）。"""
    if not isinstance(game_action, dict) or not game_action:
        return None
    return PlayerBehavior(
        comment=str(pick(game_action, "comment") or ""),
        honors_received=safe_int(pick(game_action, "getHonorsCnt")),
        honors_sent=safe_int(pick(game_action, "sendHonorsCnt")),
        reported=safe_int(pick(game_action, "reportedCnt")),
    )


def parse_heroes(*hero_lists: Any, limit: int | None = None) -> list[HeroStat]:
    """合并多个英雄统计列表（竞技/快速/全英雄），按场次排序去重。"""
    merged: dict[str, HeroStat] = {}
    # 每 10 分钟数据按各队列的上场时长加权合并：{英雄: {指标: (加权和, 权重和)}}
    per10_sums: dict[str, dict[str, tuple[float, float]]] = {}
    for hero_list in hero_lists:
        for stat in _hero_games(hero_list):
            weight = stat.game_time_seconds or 0.0
            sums = per10_sums.setdefault(stat.hero, {})
            for key, value in stat.per10.items():
                total, weights = sums.get(key, (0.0, 0.0))
                sums[key] = (total + value * weight, weights + weight)
            exist = merged.get(stat.hero)
            if exist is None:
                merged[stat.hero] = stat
            else:
                exist.match_sum = (exist.match_sum or 0) + (stat.match_sum or 0)
                exist.win_sum = (exist.win_sum or 0) + (stat.win_sum or 0)
                exist.game_time_seconds = (exist.game_time_seconds or 0) + (
                    stat.game_time_seconds or 0
                )
                for key, value in stat.per10.items():
                    exist.per10.setdefault(key, value)
    for name, sums in per10_sums.items():
        for key, (total, weights) in sums.items():
            if weights > 0:
                merged[name].per10[key] = total / weights
    stats = list(merged.values())
    for stat in stats:
        # 合并多个队列后，用合并后的 win_sum/match_sum 重算胜率，
        # 否则会沿用第一份列表里的旧胜率（如 21 场的 52.38 套在 22 场上）。
        if stat.match_sum and stat.win_sum is not None:
            stat.win_rate = round(stat.win_sum * 100.0 / stat.match_sum, 2)
        elif stat.win_rate is None and stat.match_sum:
            stat.win_rate = round((stat.win_sum or 0) * 100.0 / stat.match_sum, 2)
    stats.sort(key=lambda s: (s.match_sum or 0, s.game_time_seconds or 0), reverse=True)
    return stats[:limit] if limit else stats


def parse_match(item: Any) -> MatchItem | None:
    if not isinstance(item, dict):
        return None
    map_guid = pick(item, "mapGuid", "mapId", "map")
    hero_guid = pick(item, "heroGuid", "heroId")
    match = MatchItem(
        match_id=str(pick(item, "matchId", "match_id") or ""),
        result=ranks.result_label(pick(item, "matchRet", "matchResult", "result")),
        map_name=str(pick(item, "mapName", "map_name") or "") or resources.map_name(map_guid),
        mode=ranks.instance_label(pick(item, "instanceType", "gameMode", "mode")),
        hero=str(pick(item, "heroName", "hero") or "") or resources.hero_name(hero_guid),
        hero_icon=str(pick(item, "heroIcon", "hero_icon") or "") or resources.hero_icon(hero_guid),
        role=str(pick(item, "roleType", "role") or "") or resources.hero_role(hero_guid),
        kills=safe_int(pick(item, "kill", "kills")),
        assists=safe_int(pick(item, "assist", "assists")),
        deaths=safe_int(pick(item, "death", "deaths")),
        hero_damage=safe_int(pick(item, "heroDamage")),
        cure=safe_int(pick(item, "cure", "healing")),
        resist_damage=safe_int(pick(item, "resistDamage")),
        team_score=safe_int(pick(item, "teamScore", "score")),
        opponent_score=safe_int(pick(item, "opponentScore", "enemyScore")),
        rank=ranks.rank_label(item.get("rankInfo")),
        begin_ts_ms=safe_int(pick(item, "beginTs", "beginTime", "startTime")),
    )
    if not match.map_name:
        match.map_name = "未知地图"
    if not match.hero:
        match.hero = "未知英雄"
    return match


def parse_matches(match_list: Any, *, limit: int | None = None) -> list[MatchItem]:
    if not isinstance(match_list, Iterable) or isinstance(match_list, (str, bytes, dict)):
        return []
    items: list[MatchItem] = []
    for raw in match_list:
        parsed = parse_match(raw)
        if parsed is not None:
            items.append(parsed)
    return items[:limit] if limit else items


# ---------- 单局明细 ----------

# 上游用 *Max 布尔位标记"本场最佳"类数据
_HIGHLIGHT_FIELDS = (
    ("killMax", "击杀最多"),
    ("heroDamageMax", "伤害最高"),
    ("cureMax", "治疗最多"),
    ("resistDamageMax", "抗伤最多"),
)


def parse_detail_player(item: Any, *, viewer: str = "") -> MatchDetailPlayer | None:
    if not isinstance(item, dict):
        return None
    hero_guid = pick(item, "heroGuid", "heroId")
    name = str(pick(item, "name", "battleTag") or "")
    highlights = [label for field_name, label in _HIGHLIGHT_FIELDS if item.get(field_name) is True]
    return MatchDetailPlayer(
        name=name,
        hero=str(pick(item, "heroName") or "") or resources.hero_name(hero_guid) or "未知英雄",
        hero_icon=str(pick(item, "heroIcon") or "") or resources.hero_icon(hero_guid),
        role=str(pick(item, "roleType", "role") or "") or resources.hero_role(hero_guid),
        kills=safe_int(pick(item, "kill", "kills")),
        assists=safe_int(pick(item, "assist", "assists")),
        deaths=safe_int(pick(item, "death", "deaths")),
        hero_damage=safe_int(pick(item, "heroDamage")),
        cure=safe_int(pick(item, "cure", "healing")),
        resist_damage=safe_int(pick(item, "resistDamage")),
        damage_taken=safe_int(pick(item, "damageTaken")),
        rank=ranks.rank_label(item.get("rankInfo")),
        highlights=highlights,
        is_self=bool(viewer) and viewer.strip().lower() == name.strip().lower(),
    )


def _ban_names(guids: Any) -> list[str]:
    """禁用英雄 guid → 名称；上游用 "0"/空表示"没禁用"，要滤掉。"""
    names: list[str] = []
    for guid in guids or []:
        key = str(guid or "").strip()
        if not key or key == "0":
            continue
        names.append(resources.hero_name(key) or key)
    return names


def parse_match_detail(payload: Any, *, viewer: str = "", mode_hint: str = "") -> MatchDetail:
    """解析单局明细。

    兼容三种入参：
    - Overstats 的 detail 接口返回（含 detail / source_match / match_id）
    - 网易大神 queryMatchInfo 的 data 段
    - 大神原始响应（含 code / data / success）
    """
    wrapper = payload if isinstance(payload, dict) else {}
    source_match = (
        wrapper.get("source_match") if isinstance(wrapper.get("source_match"), dict) else {}
    )
    data = wrapper.get("detail")
    if not isinstance(data, dict):
        data = wrapper.get("data") if isinstance(wrapper.get("data"), dict) else wrapper

    map_guid = pick(data, "mapGuid", "mapId") or pick(source_match, "mapGuid")
    instance = pick(data, "instanceType", "gameMode") or pick(
        source_match, "instanceType", "gameMode"
    )
    mode_label = ranks.instance_label(instance)
    if mode_label in {"", "对局", "未知"} and mode_hint:
        # 明细里没带模式时，用请求时的模式兜底，别显示成"对局"
        mode_label = ranks.mode_label(mode_hint)
    detail = MatchDetail(
        viewer=viewer.strip(),
        match_id=str(pick(wrapper, "match_id", "matchId") or pick(data, "matchId") or ""),
        result=ranks.result_label(pick(data, "matchRet", "matchResult")),
        map_name=str(pick(data, "mapName") or "") or resources.map_name(map_guid) or "未知地图",
        mode=mode_label,
        team_score=safe_int(pick(data, "teamScore", "score")),
        opponent_score=safe_int(pick(data, "opponentScore", "enemyScore")),
        duration_sec=safe_int(pick(data, "gameTimeSec", "duration", "gameTime")),
        start_ts=safe_int(pick(data, "startTime", "beginTs")),
        bans_team=_ban_names(data.get("teamBanHeroGuids")),
        bans_enemy=_ban_names(data.get("enemyBanHeroGuids")),
    )

    viewers = {viewer.strip().lower()} if viewer else set()
    for item in data.get("teammateList") or []:
        player = parse_detail_player(item, viewer=viewer)
        if player is None:
            continue
        if player.name.strip().lower() in viewers:
            player.is_self = True
            detail.self_player = player
        detail.teammates.append(player)
    for item in data.get("enemyList") or []:
        player = parse_detail_player(item, viewer=viewer)
        if player is not None:
            detail.enemies.append(player)
    detail.teammates.sort(key=lambda p: (p.kills or 0) + (p.assists or 0), reverse=True)
    return detail
