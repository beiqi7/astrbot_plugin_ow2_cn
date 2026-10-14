"""输出渲染：纯文本卡片 + 图片卡片上下文。

文本卡片是主路径（QQ 群里最稳，任何 AstrBot 部署都能用）；
图片卡片走 AstrBot 的 html_render，需要 T2I 服务可用，失败会自动回落到文本。
"""

from __future__ import annotations

import random
import time
from collections.abc import Iterable
from typing import Any

from . import ranks, resources
from .models import MatchDetail, MatchDetailPlayer, MatchItem, PlayerProfile

LINE = "─" * 16

# 卡片标题用的随机 emoji 池（🦍 只是其中之一，权重不高）
DEFAULT_EMOJIS = (
    "🦍",
    "🎮",
    "🎯",
    "🏆",
    "⚡",
    "🔥",
    "🚀",
    "🍀",
    "🐉",
    "🦊",
    "🐧",
    "🐳",
    "🦉",
    "👾",
    "🧊",
    "🌊",
    "🧢",
    "🥷",
    "🛡️",
    "🎧",
    "🕹️",
    "🎲",
)


def pick_emoji(pool: Iterable[str] | None = None) -> str:
    """随机取一个 emoji；可以在配置里用 render.emojis 自定义池子。"""
    candidates = [str(item).strip() for item in (pool or ()) if str(item).strip()]
    return random.choice(candidates or list(DEFAULT_EMOJIS))


def _clock(ts: float) -> str:
    try:
        return time.strftime("%m-%d %H:%M", time.localtime(ts))
    except (OverflowError, OSError, ValueError):  # 上游给了离谱的时间戳
        return ""


def _relative_time(ts: int | None) -> str:
    """时间戳 → "3 小时前"。

    上游的对局列表用毫秒（beginTs），单局明细用秒（startTime），这里按量级自动识别，
    免得某个接口换了单位就把时间渲染到几万年后（Windows 上甚至会直接抛异常）。
    """
    if not ts:
        return ""
    try:
        value = float(ts)
    except (TypeError, ValueError):
        return ""
    if value > 1e11:  # 1e11 秒已经是公元 5000 年之后，只可能是毫秒
        value /= 1000.0
    delta = time.time() - value
    if delta < 0:
        return _clock(value)
    if delta < 3600:
        return f"{int(delta // 60)} 分钟前"
    if delta < 86400:
        return f"{int(delta // 3600)} 小时前"
    if delta < 86400 * 7:
        return f"{int(delta // 86400)} 天前"
    return _clock(value)


def _match_line(index: int, match: MatchItem) -> str:
    parts = [
        f"{index}. {match.result} {match.score_text}",
        match.map_name,
    ]
    hero = match.hero
    role = ranks.role_label(match.role)
    if hero:
        hero = f"{role}/{hero}" if role and role != "未知" else hero
    parts.append(hero)
    parts.append(f"KDA {match.kda_text}")
    if match.hero_damage:
        parts.append(f"伤害 {ranks.format_number(match.hero_damage)}")
    if match.cure:
        parts.append(f"治疗 {ranks.format_number(match.cure)}")
    when = _relative_time(match.begin_ts_ms)
    if when:
        parts.append(when)
    return "  ".join(part for part in parts if part)


def text_card(
    profile: PlayerProfile,
    *,
    hero_limit: int = 3,
    show_avatar: bool = False,
    extra_note: str = "",
    emoji: str = "",
) -> str:
    """把 PlayerProfile 渲染成适合 QQ 群里阅读的纯文本卡片。"""
    card = profile.card
    lines: list[str] = []

    header = card.name or profile.query or "未知玩家"
    lines.append(f"{emoji or pick_emoji()} {header} · {ranks.mode_label(profile.mode)}")
    lines.append(LINE)

    meta: list[str] = []
    if card.level is not None:
        meta.append(f"称号等级 {card.level}")
    if card.game_time_hours:
        meta.append(f"总时长 {card.game_time_hours:,.1f} 小时")
    if card.total_match:
        meta.append(f"总场次 {ranks.format_number(card.total_match)}")
    if card.title:
        meta.append(f"称号 {card.title}")
    if meta:
        lines.append(" | ".join(meta))
    if show_avatar and card.icon:
        lines.append(f"头像：{card.icon}")
    if card.suspended:
        until = _clock(card.suspended_until_ms / 1000)
        lines.append(
            f"⚠️ 该账号当前处于封禁/限制状态（至 {until}）"
            if until
            else "⚠️ 该账号当前处于封禁/限制状态"
        )

    if profile.roles:
        # 快速（非竞技）没有段位概念，上游也不返回 rankInfo，这时只展示职责数据
        has_rank = any(
            role.last_rank and role.last_rank not in {"未定级", "None"} for role in profile.roles
        )
        lines.append("")
        lines.append("【段位】" if has_rank else "【职责数据】")
        for role in profile.roles:
            detail: list[str] = []
            if role.match_sum:
                detail.append(f"{role.match_sum} 场")
            if role.win_rate is not None:
                detail.append(f"胜率 {ranks.format_percent(role.win_rate)}")
            if role.kda is not None:
                detail.append(f"KDA {role.kda:.2f}")
            if role.max_win_streak:
                detail.append(f"最高连胜 {role.max_win_streak}")
            role_name = ranks.role_label(role.role)
            if has_rank:
                line = f"  {role_name}：{role.last_rank}"
                if detail:
                    line += "  " + " · ".join(detail)
            elif detail:
                line = f"  {role_name}：" + " · ".join(detail)
            else:
                line = f"  {role_name}"
            lines.append(line)

    if profile.recent:
        recent = profile.recent
        detail = []
        if recent.win_rate is not None:
            detail.append(f"胜率 {ranks.format_percent(recent.win_rate)}")
        kda = recent.kda
        if kda is not None:
            detail.append(f"KDA {kda:.2f}")
        if recent.ave_kill is not None or recent.ave_death is not None:
            detail.append(
                "场均 "
                f"{recent.ave_kill or 0:.0f}/{recent.ave_assist or 0:.0f}/{recent.ave_death or 0:.0f}"
                "（消灭/助攻/阵亡）",
            )
        if recent.ave_damage:
            detail.append(f"伤害 {recent.ave_damage:.0f}")
        if recent.ave_cure:
            detail.append(f"治疗 {recent.ave_cure:.0f}")
        if detail:
            lines.append("")
            lines.append("【近期表现】")
            lines.append("  " + " · ".join(detail))

    if profile.matches:
        lines.append("")
        lines.append("【最近对局】")
        for index, match in enumerate(profile.matches, start=1):
            lines.append("  " + _match_line(index, match))

    if profile.heroes and hero_limit > 0:
        lines.append("")
        lines.append("【常用英雄】")
        for index, hero in enumerate(profile.heroes[:hero_limit], start=1):
            detail = []
            if hero.match_sum:
                detail.append(f"{hero.match_sum} 场")
            if hero.win_rate is not None:
                detail.append(f"胜率 {ranks.format_percent(hero.win_rate)}")
            detail.append(hero.game_time_text)
            if hero.rank and hero.rank != "未定级":
                detail.append(hero.rank)
            lines.append(
                f"  {index}. {hero.hero}（{ranks.role_label(hero.role)}）  " + " · ".join(detail),
            )

    if not profile.roles and not profile.matches and not profile.heroes:
        lines.append("")
        lines.append("没有取到战绩数据：该模式可能还没打过，或者玩家没有公开战绩。")

    if extra_note:
        lines.append("")
        lines.append(extra_note)
    return "\n".join(lines)


# 英雄「每 10 分钟」数据的展示顺序：(字段, 标签, 小数位)
_HERO_PER10_FIELDS = (
    ("damage", "伤害", 0),
    ("healing", "治疗", 0),
    ("kills", "消灭", 1),
    ("deaths", "阵亡", 1),
)


def _per10_cells(hero) -> list[dict[str, str]]:
    """英雄每 10 分钟数据 → [{key,label,value}]，缺的指标显示 "-"。"""
    cells = []
    for key, label, digits in _HERO_PER10_FIELDS:
        value = hero.per10.get(key)
        text = "-" if value is None else f"{value:,.{digits}f}"
        cells.append({"key": key, "label": label, "value": text})
    return cells


def _per10_line(hero) -> str:
    if not hero.per10:
        return ""
    return "每10分钟 " + " · ".join(f"{c['label']} {c['value']}" for c in _per10_cells(hero))


def heroes_card(profile: PlayerProfile, *, limit: int = 10, emoji: str = "") -> str:
    """英雄榜单页。"""
    card = profile.card
    head = f"{emoji or pick_emoji()} {card.name or profile.query}"
    lines = [
        f"{head} · 英雄数据（{ranks.mode_label(profile.mode)}）",
        LINE,
    ]
    if not profile.heroes:
        lines.append("暂无英雄统计数据。")
        return "\n".join(lines)
    for index, hero in enumerate(profile.heroes[:limit], start=1):
        detail = []
        if hero.match_sum:
            detail.append(f"{hero.match_sum} 场")
        if hero.win_sum:
            detail.append(f"胜 {hero.win_sum}")
        if hero.win_rate is not None:
            detail.append(f"胜率 {ranks.format_percent(hero.win_rate)}")
        detail.append(hero.game_time_text)
        if hero.rank and hero.rank != "未定级":
            detail.append(hero.rank)
        lines.append(
            f"{index}. {hero.hero}（{ranks.role_label(hero.role)}）  " + " · ".join(detail)
        )
        per10 = _per10_line(hero)
        if per10:
            lines.append(f"   {per10}")
    return "\n".join(lines)


def matches_card(
    profile: PlayerProfile,
    *,
    page: int = 1,
    start: int = 1,
    emoji: str = "",
) -> str:
    """对局列表单页。

    `start` 是这一页第一行的全局序号，这样 `/ow 对局 2` 里的编号可以直接
    拿去 `/ow 明细`（明细按同一份列表的全局序号定位）。
    """
    card = profile.card
    head = f"{emoji or pick_emoji()} {card.name or profile.query}"
    lines = [
        f"{head} · 最近对局（{ranks.mode_label(profile.mode)}，第 {page} 页）",
        LINE,
    ]
    if not profile.matches:
        lines.append("没有取到对局记录。")
        return "\n".join(lines)
    for offset, match in enumerate(profile.matches):
        lines.append(_match_line(start + offset, match))
    return "\n".join(lines)


def _detail_player_line(player: MatchDetailPlayer) -> str:
    who = f"👉 {player.name}" if player.is_self else f"   {player.name}"
    hero = player.hero
    role = ranks.role_label(player.role)
    if hero and role and role != "未知":
        hero = f"{role}/{hero}"
    parts = [who, hero, player.kda_text]
    if player.hero_damage is not None:
        parts.append(f"伤害 {ranks.format_number(player.hero_damage)}")
    if player.cure:
        parts.append(f"治疗 {ranks.format_number(player.cure)}")
    if player.resist_damage:
        parts.append(f"抗伤 {ranks.format_number(player.resist_damage)}")
    line = "  ".join(str(part) for part in parts if part)
    if player.highlights:
        line += "  [" + "/".join(player.highlights) + "]"
    return line


def match_detail_card(detail: MatchDetail, *, emoji: str = "", order: int = 1) -> str:
    """单局明细卡片。order 是用户看到的序号（1 = 最近一场）。"""
    title = f"{detail.viewer} · 单局明细" if detail.viewer else "单局明细"
    lines = [f"{emoji or pick_emoji()} {title}", LINE]

    head = [f"{detail.result} {detail.score_text}", detail.map_name]
    if detail.mode and detail.mode != "未知":
        head.append(detail.mode)
    head.append(detail.duration_text)
    lines.append(" · ".join(head))

    when = _relative_time(detail.start_ts)
    order_text = f"第 {order} 场" + ("（最近一场）" if order <= 1 else "")
    meta = [order_text] + ([f"开赛 {when}"] if when else [])
    lines.append(" · ".join(meta))
    if detail.bans_team or detail.bans_enemy:
        parts = []
        if detail.bans_team:
            parts.append("我方 " + "/".join(detail.bans_team))
        if detail.bans_enemy:
            parts.append("对方 " + "/".join(detail.bans_enemy))
        lines.append("禁用：" + " · ".join(parts))

    if detail.teammates:
        lines.append("")
        lines.append(f"【我方】{len(detail.teammates)} 人")
        for player in detail.teammates:
            lines.append(_detail_player_line(player))
    if detail.enemies:
        lines.append("")
        lines.append(f"【对方】{len(detail.enemies)} 人")
        for player in detail.enemies:
            lines.append(_detail_player_line(player))

    if not detail.teammates and not detail.enemies:
        lines.append("")
        lines.append("没有取到这一局的玩家数据。")
    return "\n".join(lines)


# 图片卡片的段位配色：key 是 ranks.rank_label 输出的中文段位名
_RANK_TIERS = {
    "青铜": "bronze",
    "白银": "silver",
    "黄金": "gold",
    "白金": "platinum",
    "翡翠": "emerald",
    "钻石": "diamond",
    "大师": "master",
    "宗师": "grandmaster",
    "英杰": "champion",
}

# 图片卡片里「近期 vs 赛季」对比的指标：(标签, 字段, 越低越好)
_STAT_ROWS = (
    ("消灭", "ave_kill", False),
    ("助攻", "ave_assist", False),
    ("阵亡", "ave_death", True),
    ("伤害", "ave_damage", False),
    ("治疗", "ave_cure", False),
    ("阻挡", "ave_resist", False),
)


def _rank_tier(label: str) -> str:
    """ "黄金 5" → "gold"，拿不到段位时返回 "unranked"（模板据此配色）。"""
    head = str(label or "").split(" ", 1)[0]
    return _RANK_TIERS.get(head, "unranked")


def _number(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:,.0f}"


def _stat_bars(recent, season) -> list[dict[str, Any]]:
    """近期与赛季场均并排对比，条宽按同一行里的较大值归一。"""
    rows: list[dict[str, Any]] = []
    for label, attr, lower_better in _STAT_ROWS:
        now = getattr(recent, attr, None) if recent else None
        base = getattr(season, attr, None) if season else None
        if now is None and base is None:
            continue
        peak = max(now or 0, base or 0) or 1
        rows.append(
            {
                "label": label,
                "recent": _number(now),
                "season": _number(base),
                "recent_pct": round((now or 0) * 100 / peak),
                "season_pct": round((base or 0) * 100 / peak),
                "lower_better": lower_better,
            },
        )
    return rows


def _split_tag(name: str) -> tuple[str, str]:
    head, sep, tail = str(name or "").partition("#")
    return head, (f"#{tail}" if sep else "")


def profile_context(profile: PlayerProfile, *, hero_limit: int = 3) -> dict[str, Any]:
    """给 HTML 模板用的上下文（字段全部是字符串/数字，模板里不用做二次处理）。

    旧字段（name/mode/icon/roles/recent/matches/heroes…）保持不变，
    自定义模板照常可用；新增字段给内置卡片的新版式用。
    """
    card = profile.card
    name = card.name or profile.query
    display_name, tag = _split_tag(name)
    recent = profile.recent
    behavior = profile.behavior
    has_rank = any(_rank_tier(role.last_rank) != "unranked" for role in profile.roles)
    return {
        "emoji": pick_emoji(),
        "name": name,
        "display_name": display_name,
        "tag": tag,
        "mode": ranks.mode_label(profile.mode),
        "icon": card.icon,
        "title": card.title,
        "level": card.level,
        "game_time": f"{card.game_time_hours:,.1f}" if card.game_time_hours else "-",
        "total_match": card.total_match,
        "suspended": card.suspended,
        "has_rank": has_rank,
        "roles": [
            {
                "role": ranks.role_label(role.role),
                "rank": role.last_rank,
                "max_rank": role.max_rank,
                "tier": _rank_tier(role.last_rank),
                "max_tier": _rank_tier(role.max_rank),
                "match_sum": role.match_sum,
                "win_rate": ranks.format_percent(role.win_rate),
                "kda": f"{role.kda:.2f}" if role.kda is not None else "-",
            }
            for role in profile.roles
        ],
        "recent": (
            {
                "win_rate": ranks.format_percent(recent.win_rate),
                "kda": f"{recent.kda:.2f}" if recent.kda is not None else "-",
                "ave_kill": recent.ave_kill,
                "ave_assist": recent.ave_assist,
                "ave_death": recent.ave_death,
                "ave_damage": recent.ave_damage,
                "ave_cure": recent.ave_cure,
            }
            if recent
            else None
        ),
        "season_win_rate": (
            ranks.format_percent(profile.season.win_rate) if profile.season else "-"
        ),
        "season_match_sum": profile.season.match_sum if profile.season else None,
        "stat_bars": _stat_bars(recent, profile.season),
        "behavior": (
            {
                "comment": behavior.comment or "-",
                "honors_received": _number(behavior.honors_received),
                "honors_sent": _number(behavior.honors_sent),
                "reported": _number(behavior.reported),
            }
            if behavior
            else None
        ),
        "matches": [
            {
                "result": match.result,
                "result_key": {"胜": "win", "负": "loss", "平": "draw"}.get(match.result, ""),
                "score": match.score_text,
                "map": match.map_name,
                "hero": match.hero,
                "hero_icon": match.hero_icon,
                "role": ranks.role_label(match.role),
                "kda": match.kda_text,
                "hero_damage": match.hero_damage,
                "cure": match.cure,
                "damage_text": ranks.format_number(match.hero_damage),
                "cure_text": ranks.format_number(match.cure),
                "when": _relative_time(match.begin_ts_ms),
            }
            for match in profile.matches
        ],
        "heroes": [
            {
                "hero": hero.hero,
                "role": ranks.role_label(hero.role),
                "level": hero.level,
                "match_sum": hero.match_sum,
                "win_sum": hero.win_sum,
                "loss_sum": (
                    hero.match_sum - hero.win_sum
                    if hero.match_sum is not None and hero.win_sum is not None
                    else None
                ),
                "win_rate": ranks.format_percent(hero.win_rate),
                "game_time": hero.game_time_text,
                "icon": hero.icon or resources.hero_icon(resources.find_hero_guid(hero.hero) or ""),
                "per10": _per10_cells(hero) if hero.per10 else [],
            }
            for hero in profile.heroes[:hero_limit]
        ],
    }
