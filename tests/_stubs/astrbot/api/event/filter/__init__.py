"""`astrbot.api.event.filter` 的桩实现。

除了提供与 AstrBot 同名的装饰器，还把注册的 handler 收集到 `HANDLERS` 里，
测试脚本可以直接按 `/ow 战绩` 这样的完整指令名取到处理函数并调用。
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from typing import Any, Optional


@dataclasses.dataclass
class HandlerSpec:
    full_name: str
    func: Callable[..., Any]
    aliases: set[str] = dataclasses.field(default_factory=set)


HANDLERS: dict[str, HandlerSpec] = {}


def _register(
    full_name: str, func: Callable[..., Any], alias: set[str] | None = None
) -> HandlerSpec:
    spec = HandlerSpec(full_name=full_name, func=func, aliases=set(alias or set()))
    HANDLERS[full_name] = spec
    return spec


class RegisteringCommandable:
    """对应 AstrBot 的指令组对象，支持级联注册子指令。"""

    def __init__(self, group_name: str) -> None:
        self.group_name = group_name

    def command(self, name: str, alias: set[str] | None = None, **_kwargs):
        def decorator(func: Callable[..., Any]):
            spec = _register(f"{self.group_name} {name}", func, alias)
            # 别名也注册一份，方便测试直接用别名触发
            for alias_name in spec.aliases:
                HANDLERS[f"{self.group_name} {alias_name}"] = spec
            return func

        return decorator


def command(name: str, alias: set[str] | None = None, **_kwargs):
    def decorator(func: Callable[..., Any]):
        spec = _register(name, func, alias)
        for alias_name in spec.aliases:
            HANDLERS[alias_name] = spec
        return func

    return decorator


def command_group(name: str, alias: set[str] | None = None, **_kwargs):
    def decorator(_func: Callable[..., Any]):
        return RegisteringCommandable(name)

    return decorator


def regex(*_args, **_kwargs):
    def decorator(func: Callable[..., Any]):
        return func

    return decorator


def permission_type(*_args, **_kwargs):
    def decorator(func: Callable[..., Any]):
        return func

    return decorator


def event_message_type(*_args, **_kwargs):
    def decorator(func: Callable[..., Any]):
        return func

    return decorator


class PermissionType:
    ADMIN = 1
    MEMBER = 2


class EventMessageType:
    ALL = "all"


__all__ = [
    "HANDLERS",
    "EventMessageType",
    "HandlerSpec",
    "PermissionType",
    "RegisteringCommandable",
    "command",
    "command_group",
    "event_message_type",
    "permission_type",
    "regex",
]
