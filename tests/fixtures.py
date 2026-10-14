"""测试夹具：按网易大神真实返回结构构造的假数据。

结构与 `Overstats/src/client/api_structure/*.md` 里记录的抓包样例保持一致，
英雄/地图 guid 直接从插件自带的映射表里取，顺带验证映射表本身是对的。
"""

from __future__ import annotations

import base64
from typing import Any

from ow2cn import resources

TEST_TAG = "光头大猩猩#5104"
TEST_TOKEN = "ct_demo_9f3a1c"

# 查无此人时服务端返回的 404 体（实测文案）
NOT_FOUND_BODY = {
    "ok": False,
    "error": "bnet_not_found",
    "message": "Could not resolve customerToken from bnet_id",
    "hint": "Check exact letter case and the number after '#'.",
}

# 最小的 PNG 头，够插件判断"这是一张图"
PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
    b"\x1f\x15\xc4\x89"
)


# 找 guid：找不到就退回一个固定值，保证测试数据永远可用
def _hero(name: str, fallback: str) -> str:
    return resources.find_hero_guid(name) or fallback


def _map(name: str, fallback: str) -> str:
    for guid, info in resources.maps().items():
        if info.get("name") == name:
            return guid
    return fallback


LUCIO = _hero("卢西奥", "207165582859042877")
KIRIKO = _hero("雾子", "207165582859042891")
ANA = _hero("安娜", "207165582859042818")
REAPER = _hero("死神", "207165582859042818")
DVA = _hero("D.Va", "207165582859042819")

ILI0S = _map("伊利奥斯", "576460752303424194")
KINGS_ROW = _map("国王大道", "576460752303424195")
NEPAL = _map("尼泊尔", "576460752303424196")
BUSAN = _map("釜山", "576460752303424197")
EICHENWALDE = _map("艾兴瓦尔德", "576460752303424198")
ROUTE66 = _map("66号公路", "576460752303424199")
NUMBANI = _map("努巴尼", "576460752303424200")


def search_payload(tag: str = TEST_TAG, token: str = TEST_TOKEN) -> dict[str, Any]:
    """POST /searchBnetAccount 的 data 段。"""
    return {
        "bnetId": 663886420,
        "name": tag,
        "icon": "https://ld5picproxy.ds.163.com/overwatch/avatar_demo.png",
        "title": "纸糊的支援",
        "titleIcon": "https://ld5picproxy.ds.163.com/overwatch/title_demo.png",
        "level": 412,
        "gameTime": "1063.29",
        "totalMatchNum": 1234,
        "customerToken": token,
    }


def card_payload(token: str = TEST_TOKEN) -> dict[str, Any]:
    """GET /customer/queryCard 的 data 段（正常账号没有 riskStatus 字段）。"""
    return search_payload(token=token)


def _match(
    match_id: str,
    *,
    ret: int,
    map_guid: str,
    hero_guid: str,
    role: str,
    kills: int,
    assists: int,
    deaths: int,
    damage: int,
    cure: int,
    team: int,
    opponent: int,
    begin_ts: int,
    rank: dict[str, Any] | None = None,
    instance: str = "IT_RANKED",
    game_mode: str = "SportPreset",
) -> dict[str, Any]:
    return {
        "mapGuid": map_guid,
        "matchId": match_id,
        "matchRet": ret,
        "instanceType": instance,
        "heroGuid": hero_guid,
        "roleType": role,
        "heroIcon": "https://d15f34w2p8l1cc.cloudfront.net/overwatch/demo.png",
        "teamScore": team,
        "opponentScore": opponent,
        "kill": kills,
        "assist": assists,
        "death": deaths,
        "heroDamage": damage,
        "cure": cure,
        "resistDamage": 0,
        "rankInfo": rank
        or {
            "rank_name": "Platinum",
            "rankName": "Platinum",
            "rank_sub_tier": 5,
            "rankSubTier": 5,
            "rankScore": 395,
        },
        "beginTs": begin_ts,
        "gameMode": game_mode,
    }


def match_list_payload() -> list[dict[str, Any]]:
    """queryCountInfo.matchList / queryMatchList 的 data 段。"""
    base = 1777212060658
    return [
        _match(
            "m-0001",
            ret=1,
            map_guid=ILI0S,
            hero_guid=LUCIO,
            role="healer",
            kills=15,
            assists=16,
            deaths=4,
            damage=4631,
            cure=6801,
            team=3,
            opponent=1,
            begin_ts=base,
        ),
        _match(
            "m-0002",
            ret=-1,
            map_guid=KINGS_ROW,
            hero_guid=KIRIKO,
            role="healer",
            kills=4,
            assists=9,
            deaths=7,
            damage=2210,
            cure=9210,
            team=1,
            opponent=2,
            begin_ts=base - 1_800_000,
        ),
        _match(
            "m-0003",
            ret=1,
            map_guid=NEPAL,
            hero_guid=DVA,
            role="tank",
            kills=24,
            assists=3,
            deaths=6,
            damage=11230,
            cure=0,
            team=2,
            opponent=0,
            begin_ts=base - 3_600_000,
        ),
        _match(
            "m-0004",
            ret=1,
            map_guid=BUSAN,
            hero_guid=ANA,
            role="healer",
            kills=6,
            assists=14,
            deaths=3,
            damage=3100,
            cure=11800,
            team=3,
            opponent=2,
            begin_ts=base - 5_400_000,
        ),
        _match(
            "m-0005",
            ret=-1,
            map_guid=ILI0S,
            hero_guid=REAPER,
            role="dps",
            kills=18,
            assists=2,
            deaths=9,
            damage=14500,
            cure=0,
            team=2,
            opponent=3,
            begin_ts=base - 7_200_000,
        ),
        _match(
            "m-0006",
            ret=1,
            map_guid=EICHENWALDE,
            hero_guid=KIRIKO,
            role="healer",
            kills=9,
            assists=18,
            deaths=5,
            damage=5200,
            cure=10100,
            team=3,
            opponent=2,
            begin_ts=base - 9_000_000,
        ),
        _match(
            "m-0007",
            ret=-1,
            map_guid=ROUTE66,
            hero_guid=DVA,
            role="tank",
            kills=27,
            assists=5,
            deaths=8,
            damage=13200,
            cure=0,
            team=2,
            opponent=3,
            begin_ts=base - 10_800_000,
        ),
        _match(
            "m-0008",
            ret=1,
            map_guid=NUMBANI,
            hero_guid=ANA,
            role="healer",
            kills=5,
            assists=12,
            deaths=4,
            damage=2900,
            cure=12400,
            team=3,
            opponent=1,
            begin_ts=base - 12_600_000,
        ),
    ]


def pool_payload() -> list[dict[str, Any]]:
    """服务端 `/dashen-match` 的实测行为：竞技 + 快速按开始时间混排，且忽略 mode。

    真实响应里两类对局是交错的（实测 24 条竞技 + 24 条快速），
    插件必须自己按 instanceType 分流，否则 `/ow 对局` 的序号会和 `/ow 明细` 对不上。
    """
    base = 1777212060658
    entries: list[dict[str, Any]] = []
    for offset, item in zip((0, 2, 4, 6, 8, 10, 12, 14), match_list_payload(), strict=False):
        entries.append({**item, "beginTs": base - offset * 600_000})
    for offset, item in zip((1, 3, 5), quick_match_list_payload(), strict=False):
        entries.append({**item, "beginTs": base - offset * 600_000})
    entries.sort(key=lambda match: match["beginTs"], reverse=True)
    return entries


def count_info_payload() -> dict[str, Any]:
    """GET /customer/queryCountInfo 的 data 段。"""
    return {
        "bnetId": 663886420,
        "guideCountData": [
            {
                "roleType": "tank",
                "lastRankInfo": {
                    "rank_name": "Platinum",
                    "rankName": "Platinum",
                    "rank_sub_tier": 5,
                    "rankSubTier": 5,
                    "rankScore": 2760,
                },
                "maxRankInfo": {
                    "rank_name": "Diamond",
                    "rankName": "Diamond",
                    "rank_sub_tier": 3,
                    "rankSubTier": 3,
                    "rankScore": 3120,
                },
                "matchSum": 128,
                "winRate": "55.47",
                "kda": "4.12",
                "coreCount": 10176,
                "maxWinStreak": 7,
            },
            {
                "roleType": "dps",
                "lastRankInfo": {
                    "rank_name": "Gold",
                    "rankName": "Gold",
                    "rank_sub_tier": 2,
                    "rankSubTier": 2,
                    "rankScore": 1620,
                },
                "maxRankInfo": {
                    "rank_name": "Platinum",
                    "rankName": "Platinum",
                    "rank_sub_tier": 4,
                    "rankSubTier": 4,
                    "rankScore": 2140,
                },
                "matchSum": 67,
                "winRate": "64.18",
                "kda": "5.80",
                "maxWinStreak": 9,
            },
            {
                "roleType": "healer",
                "lastRankInfo": {
                    "rank_name": "Diamond",
                    "rankName": "Diamond",
                    "rank_sub_tier": 4,
                    "rankSubTier": 4,
                    "rankScore": 3380,
                },
                "maxRankInfo": {
                    "rank_name": "Master",
                    "rankName": "Master",
                    "rank_sub_tier": 5,
                    "rankSubTier": 5,
                    "rankScore": 3610,
                },
                "matchSum": 254,
                "winRate": "58.27",
                "kda": "6.31",
                "maxWinStreak": 12,
            },
        ],
        "presetsSummaryData": {
            "matchSum": 449,
            "winRate": "57.91",
            "aveHeroDamage": 7023,
            "aveCure": 6965,
            "aveResistDamage": 4590,
            "aveKill": 14,
            "aveAssist": 12,
            "aveDeath": 5,
            "matchWinSum": 260,
            "matchLossSum": 189,
        },
        "recentMatchCount": {
            "aveDamage": 3634,
            "aveResistDamage": 0,
            "aveCure": 9329,
            "aveAssist": 14,
            "aveKill": 8,
            "aveDeath": 0,
            "winRate": "66.67",
            "roleType": "healer",
        },
        "matchList": match_list_payload(),
        "frequentHeroIds": [LUCIO, KIRIKO, ANA],
        "presetsHeroUseSummaryList": [
            {
                "heroGuid": LUCIO,
                "heroLevel": 21,
                "matchSum": 132,
                "winSum": 84,
                "winRate": "63.64",
                "gameTime": 1104.5,
                "heroRankInfo": {"rank_name": "Diamond", "rank_sub_tier": 4, "rankScore": 3380},
                # 真实结构：guid → 每 10 分钟数值（伤害/治疗/消灭/阵亡/助攻）
                "statPerTenMinCount": {
                    "603482350067647671": 4210.5,
                    "603482350067646913": 11820.25,
                    "603482350067646495": 9.4,
                    "603482350067646506": 5.1,
                    "603482350067648392": 16.2,
                },
            },
            {
                "heroGuid": KIRIKO,
                "heroLevel": 15,
                "matchSum": 88,
                "winSum": 49,
                "winRate": "55.68",
                "gameTime": 742.0,
                "heroRankInfo": {"rank_name": "Platinum", "rank_sub_tier": 5, "rankScore": 2880},
                "statPerTenMinCount": {
                    "603482350067647671": 1327.43,
                    "603482350067646913": 9235.91,
                    "603482350067646495": 4.21,
                    "603482350067646506": 7.87,
                },
            },
            {
                "heroGuid": ANA,
                "heroLevel": 9,
                "matchSum": 34,
                "winSum": 17,
                "winRate": "50.00",
                "gameTime": 289.25,
                "heroRankInfo": {"rank_name": "Platinum", "rank_sub_tier": 3, "rankScore": 2640},
            },
            {
                "heroGuid": REAPER,
                "heroLevel": 12,
                "matchSum": 28,
                "winSum": 11,
                "winRate": "39.29",
                "gameTime": 260.0,
                "heroRankInfo": {"rank_name": "Gold", "rank_sub_tier": 2, "rankScore": 1620},
            },
            {
                "heroGuid": DVA,
                "heroLevel": 8,
                "matchSum": 24,
                "winSum": 14,
                "winRate": "58.33",
                "gameTime": 205.5,
                "heroRankInfo": {"rank_name": "Platinum", "rank_sub_tier": 1, "rankScore": 2100},
            },
            {
                "heroGuid": _hero("士兵：76", "207165582859042000"),
                "heroLevel": 6,
                "matchSum": 19,
                "winSum": 9,
                "winRate": "47.37",
                "gameTime": 150.0,
            },
            {
                "heroGuid": _hero("莫伊拉", "207165582859042001"),
                "heroLevel": 5,
                "matchSum": 16,
                "winSum": 5,
                "winRate": "31.25",
                "gameTime": 120.0,
            },
            {
                "heroGuid": _hero("奥丽莎", "207165582859042002"),
                "heroLevel": 4,
                "matchSum": 11,
                "winSum": 6,
                "winRate": "54.55",
                "gameTime": 96.0,
            },
        ],
        "openHeroUseSummaryList": [
            {
                "heroGuid": DVA,
                "matchSum": 41,
                "winSum": 24,
                "winRate": "58.54",
                "gameTime": 330.0,
                "heroRankInfo": {"rank_name": "Platinum", "rank_sub_tier": 5},
            },
        ],
        "userProvinceRankList": [],
        "gameAction": {
            "getHonorsCnt": 23,
            "sendHonorsCnt": 17,
            "reportedCnt": 0,
            "comment": "良好",
            "dt": "20260927",
        },
    }


def _detail_player(
    name: str,
    hero: str,
    *,
    kills: int,
    assists: int,
    deaths: int,
    damage: int = 0,
    cure: int = 0,
    resist: int = 0,
    rank: str = "Platinum",
    highlights: dict[str, bool] | None = None,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "name": name,
        "bnetId": abs(hash(name)) % 10_000_000,
        "heroGuid": hero,
        "kill": kills,
        "assist": assists,
        "death": deaths,
        "heroDamage": damage,
        "cure": cure,
        "resistDamage": resist,
        "damageTaken": damage,
        "rankInfo": {"rank_name": rank, "rank_sub_tier": 4, "rankScore": 2100},
    }
    item.update(highlights or {})
    return item


def match_info_payload(match_id: str = "m-0001") -> dict[str, Any]:
    """GET /customer/queryMatchInfo 的 data 段（单局明细）。"""
    return {
        "matchId": match_id,
        "mapGuid": ILI0S,
        "instanceType": "IT_RANKED",
        "matchRet": 1,
        "teamScore": 3,
        "opponentScore": 1,
        "gameTimeSec": 979,
        "startTime": 1777212060,
        "teamBanHeroGuids": [KIRIKO],
        "enemyBanHeroGuids": [ANA],
        "teammateList": [
            _detail_player(
                TEST_TAG,
                LUCIO,
                kills=15,
                assists=16,
                deaths=4,
                damage=4631,
                cure=6801,
                highlights={"cureMax": True},
            ),
            _detail_player("队友甲#1111", KIRIKO, kills=9, assists=12, deaths=6, damage=5200),
        ],
        "enemyList": [
            _detail_player(
                "对手甲#2222",
                ANA,
                kills=11,
                assists=8,
                deaths=7,
                damage=8100,
                cure=12600,
                highlights={"killMax": True},
            ),
        ],
    }


def detail_payload(match_id: str = "m-0001") -> dict[str, Any]:
    """Overstats `/api/v2/dashen-match/detail` 的返回（包装了 source_match + detail）。

    服务端按 `match_id` 精确定位时，`source_match` 就是列表里的那一条，
    这里照样从混排池里取，保证明细与列表行对得上。
    """
    source = next(
        (item for item in pool_payload() if item["matchId"] == match_id),
        pool_payload()[0],
    )
    detail = match_info_payload(match_id)
    detail["mapGuid"] = source["mapGuid"]
    detail["instanceType"] = source["instanceType"]
    detail["matchRet"] = source["matchRet"]
    detail["teamScore"] = source["teamScore"]
    detail["opponentScore"] = source["opponentScore"]
    return {
        "ok": True,
        "customer_token": TEST_TOKEN,
        "resolved": {"query": TEST_TAG, "bnet_id": "663886420"},
        "match_id": match_id,
        "match_kind": "normal",
        "source_match": source,
        "detail": detail,
    }


def detail_image_replies(match_id: str = "m-0001") -> dict[str, Any]:
    """Overstats `/api/v2/dashen-match/detail/replies` 的返回（含 base64 图片）。"""
    return {
        "ok": True,
        "customer_token": TEST_TOKEN,
        "resolved": {"query": TEST_TAG, "bnet_id": "663886420"},
        "match_id": match_id,
        "match_kind": "normal",
        "replies": [
            {
                "type": "meta",
                "meta_type": "ds_match_detail_players",
                "data": {"player_ids": [TEST_TAG], "competitive": match_id.startswith("m-")},
            },
            {
                "type": "image",
                "media_type": "image/png",
                "base64": base64.b64encode(PNG_BYTES).decode("ascii"),
            },
        ],
    }


# ---------- 快速（生涯/非竞技）数据集 ----------
# 与竞技数据刻意区分：matchId 前缀 q-、地图/英雄/职责都不同，
# 用来验证"生涯查询不会拿到竞技数据"。


def quick_match_list_payload() -> list[dict[str, Any]]:
    """快速（非竞技）对局：instanceType/gameMode 与竞技区分开。"""
    base = 1777212060658
    rank = {
        "rank_name": "None",
        "rankName": "None",
        "rank_sub_tier": 0,
        "rankSubTier": 0,
        "rankScore": 0,
    }
    return [
        _match(
            "q-0001",
            ret=-1,
            map_guid=KINGS_ROW,
            hero_guid=KIRIKO,
            role="healer",
            kills=6,
            assists=14,
            deaths=8,
            damage=3200,
            cure=9700,
            team=1,
            opponent=2,
            begin_ts=base,
            rank=rank,
            instance="IT_UNRANKED",
            game_mode="LeisurePreset",
        ),
        _match(
            "q-0002",
            ret=1,
            map_guid=NEPAL,
            hero_guid=DVA,
            role="tank",
            kills=21,
            assists=4,
            deaths=5,
            damage=10800,
            cure=0,
            team=3,
            opponent=0,
            begin_ts=base - 1_800_000,
            rank=rank,
            instance="IT_UNRANKED",
            game_mode="LeisurePreset",
        ),
        _match(
            "q-0003",
            ret=1,
            map_guid=BUSAN,
            hero_guid=ANA,
            role="healer",
            kills=3,
            assists=11,
            deaths=4,
            damage=2100,
            cure=8400,
            team=2,
            opponent=1,
            begin_ts=base - 3_600_000,
            rank=rank,
            instance="IT_UNRANKED",
            game_mode="LeisurePreset",
        ),
    ]


def quick_count_info_payload() -> dict[str, Any]:
    """快速（非竞技）块：没有段位字段，matchList 用 q- 前缀。"""
    return {
        "bnetId": 663886420,
        "gameMode": "leisure",
        "guideCountData": [
            {
                "roleType": "healer",
                "matchSum": 97,
                "winRate": "54.64",
                "kda": "3.86",
                "maxWinStreak": 7,
            },
            {
                "roleType": "dps",
                "matchSum": 73,
                "winRate": "54.79",
                "kda": "2.28",
                "maxWinStreak": 5,
            },
            {
                "roleType": "open",
                "matchSum": 18,
                "winRate": "55.56",
                "kda": "1.86",
                "maxWinStreak": 4,
            },
        ],
        "presetsSummaryData": {
            "matchSum": 188,
            "winRate": "54.79",
            "aveKill": 11,
            "aveAssist": 14,
            "aveDeath": 7,
            "aveCure": 6027,
        },
        "recentMatchCount": {
            "aveKill": 11,
            "aveAssist": 14,
            "aveDeath": 0,
            "aveDamage": 3438,
            "aveCure": 6027,
            "winRate": "45.45",
            "roleType": "healer",
        },
        "matchList": quick_match_list_payload(),
        "presetsHeroUseSummaryList": [
            {
                "heroGuid": KIRIKO,
                "matchSum": 40,
                "winSum": 22,
                "winRate": "55.00",
                "gameTime": 12000,
            },
            {"heroGuid": LUCIO, "matchSum": 30, "winSum": 15, "winRate": "50.00", "gameTime": 9000},
        ],
    }


def quick_detail_payload() -> dict[str, Any]:
    return detail_payload("q-0001")
