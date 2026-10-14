"""段位 / 职责 / 对局结果的展示映射。

段位名称沿用上游（网易大神）的英文枚举，这里只做展示层映射。
"""

from __future__ import annotations

from typing import Any

# 与上游 rank_id / rank_name 对应
RANK_LABELS_CN = {
    "Bronze": "青铜",
    "Silver": "白银",
    "Gold": "黄金",
    "Platinum": "白金",
    "Emerald": "翡翠",
    "Diamond": "钻石",
    "Master": "大师",
    "Grandmaster": "宗师",
    "Champion": "英杰",
    "Unranked": "未定级",
    "None": "未定级",
    "": "未定级",
}

# 上游偶尔返回 rank_level 数字，这里做兜底映射
RANK_LEVEL_LABELS_CN = {
    0: "青铜",
    1: "白银",
    2: "黄金",
    3: "白金",
    4: "钻石",
    5: "大师",
    6: "宗师",
    7: "英杰",
    8: "翡翠",
}

ROLE_LABELS_CN = {
    "tank": "坦克",
    "dps": "输出",
    "damage": "输出",
    "healer": "辅助",
    "support": "辅助",
    "open": "开放",
    "all": "全能",
}

# 队列模式：上游 gameMode 参数
MODE_TO_GAME_MODE = {
    "competitive": "sport",
    "quick": "leisure",
}

MODE_LABELS_CN = {
    "competitive": "竞技",
    "quick": "快速（非竞技）",
}

# 对局类型
INSTANCE_TYPE_LABELS_CN = {
    "IT_RANKED": "竞技",
    "IT_QUICK": "快速",
    "IT_UNRANKED": "快速",
    "IT_PRACTICE": "训练",
    "IT_ARCADE": "街机",
    "IT_FIGHT": "角斗",
    # 上游 matchList / 明细里用的是这类 Preset 值
    "SPORTPRESET": "竞技",
    "LEISUREPRESET": "快速",
    "ARCADEPRESET": "街机",
    "OPENPRESET": "开放队列",
    "PRACTICEPRESET": "训练",
    "FIGHTPRESET": "角斗",
    "STADIUMPRESET": "角斗",
}


# 用户可以直接写在指令里的模式说法。国服数据只有两个数据集：竞技（sport）与快速（leisure），
# 非竞技的那些说法——生涯、休闲、快速、开放——都落到 quick。
_COMPETITIVE_WORDS = frozenset(
    {"competitive", "rank", "ranked", "竞技", "排位", "天梯", "竞技比赛"},
)
_QUICK_WORDS = frozenset(
    {
        "quick",
        "casual",
        "unranked",
        "qp",
        "快速",
        "快速比赛",
        "生涯",
        "休闲",
        "休闲模式",
        "非竞技",
        "开放",
        "开放队列",
    },
)


def parse_mode_keyword(text: Any) -> str | None:
    """指令参数是模式关键词时返回归一化后的模式，否则返回 None（当作 BattleTag 等其它参数）。"""
    word = str(text or "").strip().lower()
    if word in _COMPETITIVE_WORDS:
        return "competitive"
    if word in _QUICK_WORDS:
        return "quick"
    return None


def normalize_mode(mode: str) -> str:
    """把用户/配置里的模式串归一化，认不出来的一律按竞技处理。"""
    return parse_mode_keyword(mode) or "competitive"


def game_mode_of(mode: str) -> str:
    """模式 → 上游 gameMode 参数。"""
    return MODE_TO_GAME_MODE.get(normalize_mode(mode), "sport")


def mode_label(mode: str) -> str:
    return MODE_LABELS_CN.get(normalize_mode(mode), "竞技")


def role_label(role: Any) -> str:
    text = str(role or "").strip().lower()
    return ROLE_LABELS_CN.get(text, text or "未知")


def _as_int(value: Any) -> int | None:
    try:
        if value in (None, ""):
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


def rank_label(rank_info: Any, *, fallback_level: Any = None) -> str:
    """把上游的 rankInfo 结构转成 "白金 3" 这样的展示文本。

    上游不同队列返回的字段并不一致：竞技会给完整 rankInfo，快速有时只给一个
    rankLevel 数字，甚至整块 lastRankInfo 都不返回，所以这里要兜底。
    """
    # 段位 0（青铜）是有效值，不能用 `or -1` 抹掉；None 才当缺失。
    fb_level = _as_int(fallback_level)
    level_label = RANK_LEVEL_LABELS_CN.get(fb_level, "") if fb_level is not None else ""

    if not isinstance(rank_info, dict):
        raw_name = str(rank_info or "").strip()
        if raw_name and raw_name.lower() not in {"none", "null", "unranked"}:
            return RANK_LABELS_CN.get(raw_name, raw_name)
        return level_label or "未定级"

    raw_name = (
        rank_info.get("rankName")
        or rank_info.get("rank_name")
        or rank_info.get("rankLevelName")
        or ""
    )
    raw_name = str(raw_name).strip()
    label = ""
    if raw_name and raw_name.lower() not in {"none", "null", "unranked"}:
        label = RANK_LABELS_CN.get(raw_name, raw_name)
    if not label:
        level = _as_int(rank_info.get("rankLevel", rank_info.get("rank_level")))
        label = RANK_LEVEL_LABELS_CN.get(level, "") if level is not None else ""
    if not label:
        label = level_label or "未定级"

    sub_tier = _as_int(rank_info.get("rankSubTier", rank_info.get("rank_sub_tier")))
    # 注意：上游的 rankScore 不是分数，而是「段位 id × 100 + (100 - 小段)」的紧凑编码
    # （黄金 5 = 295、白金 5 = 395），Overstats 也是按这个规则反解段位的。
    # 直接当"分"展示会误导，这里只展示段位与小段。
    if sub_tier and sub_tier > 0 and label not in {"未定级", "英杰"}:
        return f"{label} {sub_tier}"
    return label


def result_label(match_ret: Any) -> str:
    """1 胜 / -1 负 / 0 平（上游约定）。"""
    value = _as_int(match_ret)
    if value == 1:
        return "胜"
    if value == -1:
        return "负"
    if value == 0:
        return "平"
    return "未知"


def instance_label(instance_type: Any) -> str:
    key = str(instance_type or "").strip().upper()
    return INSTANCE_TYPE_LABELS_CN.get(key, key or "对局")


def format_percent(value: Any) -> str:
    """胜率美化：'64.18' → '64.2%'。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "-"
    return f"{number:.1f}%"


def format_number(value: Any) -> str:
    """大数字加千位分隔，非数字原样返回。"""
    number = _as_int(value)
    if number is None:
        return str(value) if value not in (None, "") else "-"
    return f"{number:,}"
