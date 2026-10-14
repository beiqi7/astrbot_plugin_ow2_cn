"""插件配置读取工具。

WebUI 一般会按 _conf_schema.json 给出正确类型，但手改配置文件、旧版本迁移时
可能出现字符串数字、空串甚至非法值；这里统一容错，避免一个配置项写错就让插件加载失败。
"""

from __future__ import annotations

import math
from typing import Any

_TRUE_WORDS = frozenset({"1", "true", "yes", "y", "on", "是", "开", "开启"})
_FALSE_WORDS = frozenset({"0", "false", "no", "n", "off", "否", "关", "关闭", ""})


def section(config: Any, name: str) -> dict:
    """取一个配置分组（缺失或不是 dict 时返回空 dict）。"""
    getter = getattr(config, "get", None)
    value = getter(name) if callable(getter) else None
    return value if isinstance(value, dict) else {}


def as_float(value: Any, default: float, *, minimum: float | None = None) -> float:
    """转成浮点数；缺失、非法或低于 minimum 时返回 default。"""
    if isinstance(value, bool):  # bool 是 int 的子类，True 不该被当成 1
        return default
    try:
        number = float(value.strip()) if isinstance(value, str) else float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(number):
        return default
    if minimum is not None and number < minimum:
        return default
    return number


def as_int(value: Any, default: int, *, minimum: int | None = None) -> int:
    """转成整数（接受 "5"、5.0 这类写法）；缺失、非法或低于 minimum 时返回 default。"""
    number = as_float(value, float("nan"))
    if math.isnan(number):
        return default
    result = int(number)
    if minimum is not None and result < minimum:
        return default
    return result


def as_bool(value: Any, default: bool = False) -> bool:
    """转成布尔值；字符串 "false"/"0"/"关闭" 等按假处理，认不出来时返回 default。"""
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return value != 0
    text = str(value).strip().lower()
    if text in _TRUE_WORDS:
        return True
    if text in _FALSE_WORDS:
        return False
    return default


__all__ = ["as_bool", "as_float", "as_int", "section"]
