"""astrbot.api 的最小桩实现（仅用于本地测试）。

字段与方法签名与 AstrBot 真实实现一致（见 AstrBotDevs/AstrBot 的
astrbot/api/*、astrbot/core/platform/astr_message_event.py），
这样测试就能在不启动 AstrBot 的情况下真正跑一遍插件逻辑。
"""

from __future__ import annotations

import logging

logger = logging.getLogger("astrbot")


class AstrBotConfig(dict):
    """AstrBot 的配置对象，本质是 dict。"""

    def save_config(self) -> None:  # pragma: no cover - 测试里不需要
        return None


__all__ = ["AstrBotConfig", "logger"]
