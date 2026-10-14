"""`astrbot.api.star` 的桩实现。"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, ClassVar, Optional

logger = logging.getLogger("astrbot")


class Context:
    """占位上下文，插件只把它透传给 Star 基类。"""


class Star:
    #: 真实实现会调用 AstrBot 的 T2I 服务；测试里默认不可用（用来验证回落链路）。
    render_available: ClassVar[bool] = False
    #: 记录每次 html_render 的入参，断言"用了哪套模板、什么渲染选项"
    render_calls: ClassVar[list[dict[str, Any]]] = []

    def __init__(self, context: Context, config: dict | None = None) -> None:
        self.context = context
        self.logger = logging.getLogger("astrbot.plugin")

    async def html_render(
        self,
        tmpl: str,
        data: dict,
        return_url: bool = True,
        options: dict | None = None,
    ) -> str:
        type(self).render_calls.append({"tmpl": tmpl, "data": data, "options": options or {}})
        if not type(self).render_available:
            raise RuntimeError("html_render is not available in the test stub")
        return "http://t2i.test/rendered.png"

    async def text_to_image(self, text: str, return_url: bool = True) -> str:
        raise RuntimeError("text_to_image is not available in the test stub")


class StarTools:
    @classmethod
    def get_data_dir(cls, plugin_name: str | None = None) -> Path:
        root = os.environ.get("OW2CN_TEST_DATA_DIR") or str(Path.cwd() / ".ow2cn_test_data")
        path = Path(root) / "plugin_data" / str(plugin_name or "astrbot_plugin_ow2_cn")
        path.mkdir(parents=True, exist_ok=True)
        return path


def register(name: str, author: str = "", desc: str = "", version: str = "", repo: str = ""):
    def decorator(cls):
        cls.plugin_name = name
        cls.plugin_author = author
        cls.plugin_desc = desc
        cls.plugin_version = version
        cls.plugin_repo = repo
        return cls

    return decorator


__all__ = ["Context", "Star", "StarTools", "register"]
