"""英雄 / 地图 guid → 名称映射。

数据文件 `data/heroes.json`、`data/maps.json` 由网易大神 query_tool.json 整理而来，
只保留 guid、名称、职责/模式、图标这几列（见 README 的来源说明）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_DATA_DIR = Path(__file__).resolve().parent / "data"

_heroes: dict[str, dict[str, Any]] | None = None
_maps: dict[str, dict[str, Any]] | None = None


def _load(filename: str) -> dict[str, dict[str, Any]]:
    path = _DATA_DIR / filename
    try:
        with path.open("r", encoding="utf-8") as fp:
            data = json.load(fp)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): v for k, v in data.items() if isinstance(v, dict)}


def heroes() -> dict[str, dict[str, Any]]:
    global _heroes
    if _heroes is None:
        _heroes = _load("heroes.json")
    return _heroes


def maps() -> dict[str, dict[str, Any]]:
    global _maps
    if _maps is None:
        _maps = _load("maps.json")
    return _maps


def hero_name(guid: Any) -> str:
    info = heroes().get(str(guid or ""))
    if not info:
        return ""
    return str(info.get("name") or "")


def hero_role(guid: Any) -> str:
    info = heroes().get(str(guid or ""))
    if not info:
        return ""
    return str(info.get("role") or "")


def hero_icon(guid: Any) -> str:
    info = heroes().get(str(guid or ""))
    if not info:
        return ""
    return str(info.get("icon") or "")


def find_hero_guid(text: str) -> str | None:
    """按中文名/英文名/id 反查英雄 guid，用于 /ow 英雄 的模糊输入。"""
    keyword = str(text or "").strip().lower()
    if not keyword:
        return None
    for guid, info in heroes().items():
        if keyword in {
            str(info.get("name") or "").lower(),
            str(info.get("id") or "").lower(),
            guid.lower(),
        }:
            return guid
    for guid, info in heroes().items():
        if keyword and keyword in str(info.get("name") or "").lower():
            return guid
    return None


def map_name(guid: Any) -> str:
    info = maps().get(str(guid or ""))
    if not info:
        return ""
    return str(info.get("name") or "")


def map_mode(guid: Any) -> str:
    info = maps().get(str(guid or ""))
    if not info:
        return ""
    return str(info.get("mode") or "")
