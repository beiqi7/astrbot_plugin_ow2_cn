"""数据源工厂。

配置项 `data_source` 决定走哪条链路：
- `dashen`    → 直连网易大神（需要 token）
- `overstats` → 请求 Overstats HTTP 服务（插件端零 token）

两条链路互为兜底：`overstats` 未填地址但配了大神账号时回退到 `dashen`；
`dashen` 没有可用账号但填了 Overstats 地址时改用 `overstats`。
两者都没有时抛 ConfigError，由命令层给出可操作的提示。
"""

from __future__ import annotations

from typing import Any

from ..config import as_bool, as_float, as_int, section
from ..errors import ConfigError
from .base import OwSource
from .dashen import (
    DEFAULT_API_ROOT,
    DEFAULT_USER_AGENT,
    DashenSource,
    _parse_accounts,
)
from .overstats import OverstatsSource


def _usable_accounts(accounts: Any) -> bool:
    """至少有一个账号同时具备 role_id 与 token 才认为直连可用。"""
    return len(_parse_accounts(accounts)) > 0


def _build_overstats(
    base_url: str,
    overstats_conf: dict,
    *,
    payload_ttl: float,
    max_retry: int,
    debug: bool,
    logger: Any,
) -> OverstatsSource:
    return OverstatsSource(
        base_url=base_url,
        api_key=str(overstats_conf.get("api_key") or ""),
        profile_path=str(overstats_conf.get("profile_path") or ""),
        match_path=str(overstats_conf.get("match_path") or ""),
        profile_image_path=str(overstats_conf.get("profile_image_path") or ""),
        match_image_path=str(overstats_conf.get("match_image_path") or ""),
        match_detail_path=str(overstats_conf.get("match_detail_path") or ""),
        match_detail_replies_path=str(
            overstats_conf.get("match_detail_replies_path") or "",
        ),
        timeout=as_float(overstats_conf.get("timeout"), 20.0, minimum=1.0),
        image_timeout=as_float(overstats_conf.get("image_timeout"), 60.0, minimum=1.0),
        payload_ttl=payload_ttl,
        max_retry=max_retry,
        debug=debug,
        logger=logger,
    )


def build_source(config: Any, logger: Any = None) -> OwSource:
    getter = getattr(config, "get", None)
    data_source = str((getter("data_source") if callable(getter) else "") or "dashen")
    data_source = data_source.strip().lower()
    dashen_conf = section(config, "dashen")
    overstats_conf = section(config, "overstats")
    query_conf = section(config, "query")
    basic_conf = section(config, "basic")

    base_url = str(overstats_conf.get("base_url") or "").strip()
    has_accounts = _usable_accounts(dashen_conf.get("accounts"))
    debug = as_bool(basic_conf.get("debug"))
    max_retry = as_int(basic_conf.get("max_retry"), 0, minimum=0)
    # 原始响应 / 对局窗口缓存跟着战绩缓存走（0 表示不缓存）。
    payload_ttl = as_float(query_conf.get("cache_ttl"), 0.0, minimum=0.0)
    overstats_options = {
        "payload_ttl": payload_ttl,
        "max_retry": max_retry,
        "debug": debug,
        "logger": logger,
    }

    if data_source == "overstats":
        if base_url:
            return _build_overstats(base_url, overstats_conf, **overstats_options)
        # 没填服务地址：如果配了可用的大神账号就回退，否则给出明确提示
        if not has_accounts:
            raise ConfigError(
                "数据源配置不完整",
                hint="请二选一：① 在「Overstats HTTP 服务」里填写 base_url（插件端零 token）；"
                "② 把数据源改成 dashen 并填写大神 accounts。",
            )
        if logger:
            logger.warning("数据源选择了 overstats 但没有填写 base_url，已回退到直连网易大神。")
    elif not has_accounts and base_url:
        # 数据源默认是 dashen：只填了 Overstats 地址、忘了切数据源时，直接用填好的服务。
        if logger:
            logger.warning("数据源选择了 dashen 但没有可用的大神账号，已改用 Overstats 服务。")
        return _build_overstats(base_url, overstats_conf, **overstats_options)

    return DashenSource(
        dashen_conf.get("accounts"),
        api_root=str(dashen_conf.get("api_root") or "").strip() or DEFAULT_API_ROOT,
        dts=str(dashen_conf.get("dts") or "2026"),
        server=str(dashen_conf.get("server") or "1"),
        client_type=str(dashen_conf.get("client_type") or "60"),
        user_agent=str(dashen_conf.get("user_agent") or "").strip() or DEFAULT_USER_AGENT,
        timeout=as_float(dashen_conf.get("timeout"), 12.0, minimum=1.0),
        max_retry=max_retry,
        token_cache_ttl=as_float(query_conf.get("token_cache_ttl"), 0.0, minimum=0.0),
        payload_ttl=payload_ttl,
        debug=debug,
        logger=logger,
    )


__all__ = ["DashenSource", "OverstatsSource", "OwSource", "build_source"]
