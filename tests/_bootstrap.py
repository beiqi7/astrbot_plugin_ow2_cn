"""测试引导：把插件根目录与 astrbot 桩目录挂到 sys.path，并提供调用指令的夹具。

`run_command()` 复刻 AstrBot 的调用约定：
- 唤醒前缀与指令组匹配由 AstrBot 完成，测试里直接按 "ow 战绩" 取 handler
- 指令参数由 AstrBot 按空格切分后按签名注入，这里做同样的事
"""

from __future__ import annotations

import asyncio
import inspect
import pathlib
import sys
from typing import Any

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
STUBS = HERE / "_stubs"

for _path in (str(STUBS), str(ROOT.parent), str(HERE), str(ROOT)):
    if _path not in sys.path:
        sys.path.append(_path)

from astrbot.api import AstrBotConfig
from astrbot.api.event import AstrMessageEvent, MessageEventResult
from astrbot.api.event.filter import HANDLERS
from mock_dashen import EXPECTED_ROLE_ID, EXPECTED_TOKEN

PLUGIN_MODULE = "astrbot_plugin_ow2_cn.main"


def base_config(
    *,
    data_source: str = "dashen",
    api_root: str = "",
    overstats_base_url: str = "",
    overstats_api_key: str = "",
    token: str = EXPECTED_TOKEN,
    role_id: str = EXPECTED_ROLE_ID,
    **overrides: Any,
) -> AstrBotConfig:
    """构造一份与 _conf_schema.json 对齐的插件配置。"""
    config = AstrBotConfig(
        {
            "data_source": data_source,
            "dashen": {
                "accounts": [{"name": "primary", "role_id": role_id, "token": token}],
                "dts": "2026",
                "server": "1",
                "client_type": "60",
                "api_root": api_root or "https://datamsapi.ds.163.com/v1/a19ld5tool",
                "timeout": 10,
            },
            "overstats": {
                "base_url": overstats_base_url,
                "api_key": overstats_api_key,
                "profile_path": "/api/v2/dashen-profile",
                "match_path": "/api/v2/dashen-match",
                "profile_image_path": "/api/v2/dashen-profile/image",
                "match_image_path": "/api/v2/dashen-match/image",
                "timeout": 10,
            },
            "query": {
                "default_mode": "competitive",
                "match_limit": 5,
                "match_limit_max": 15,
                "hero_limit": 3,
                "cache_ttl": 120,
                "token_cache_ttl": 21600,
                "allow_any_battle_tag": True,
            },
            "binding": {"scope": "both", "allow_relaxed_tag": True},
            "render": {
                "use_image_card": False,
                "card_template": "",
                "show_avatar": False,
                "image_source": "plugin",
                "image_scale": "high",
                "emojis": [],
                # 离线测试不去下载卡片里的外链图片（内嵌逻辑由 test_images 单独覆盖）
                "inline_images": False,
            },
            "basic": {"max_retry": 0, "debug": False},
        },
    )
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(config.get(key), dict):
            config[key].update(value)
        else:
            config[key] = value
    return config


def load_plugin(config: AstrBotConfig, data_dir: pathlib.Path):
    """按 AstrBot 的方式（包 + main.py）导入插件并实例化。

    通过 OW2CN_TEST_DATA_DIR 把持久化目录指到临时目录，
    这样绑定文件不会写进真实 data 目录。
    """
    import importlib
    import os

    from astrbot.api.star import Context

    data_dir.mkdir(parents=True, exist_ok=True)
    os.environ["OW2CN_TEST_DATA_DIR"] = str(data_dir)
    module = importlib.import_module(PLUGIN_MODULE)
    return module.Ow2CnPlugin(Context(), config)


def _parse_params(func, args: str) -> dict[str, Any]:
    """复刻 CommandFilter.validate_and_convert_params 的行为。"""
    signature = inspect.signature(func)
    names = [name for name in signature.parameters][2:]  # 跳过 self 与 event
    tokens = [token for token in str(args).split(" ") if token]
    params: dict[str, Any] = {}
    for index, name in enumerate(names):
        parameter = signature.parameters[name]
        if index < len(tokens):
            value = tokens[index]
            default = parameter.default
            if isinstance(default, bool):
                params[name] = value.lower() in {"true", "yes", "1"}
            elif isinstance(default, int):
                params[name] = int(value)
            elif isinstance(default, float):
                params[name] = float(value)
            else:
                params[name] = value
        else:
            params[name] = parameter.default
    return params


async def run_command(
    plugin,
    command: str,
    args: str = "",
    *,
    group_id: str = "9527",
    sender_id: str = "10001",
    sender_name: str = "测试用户",
    at_qq: str | None = None,
    admin: bool = False,
) -> list[MessageEventResult]:
    """执行一条指令并收集它 yield 出来的所有结果。"""
    spec = HANDLERS.get(command)
    if spec is None:
        raise AssertionError(f"没有注册指令 {command}，已注册：{sorted(HANDLERS)}")
    event = AstrMessageEvent(
        message_str=f"/{command} {args}".strip(),
        group_id=group_id,
        sender_id=sender_id,
        sender_name=sender_name,
        at_qq=at_qq,
        admin=admin,
    )
    params = _parse_params(spec.func, args)
    results: list[MessageEventResult] = []
    async for result in spec.func(plugin, event, **params):
        results.append(result)
    return results


def run(coro):
    """同步跑协程（unittest 里用）。"""
    return asyncio.get_event_loop().run_until_complete(coro)


def texts(results: list[MessageEventResult]) -> str:
    return "\n".join(result.text for result in results)
