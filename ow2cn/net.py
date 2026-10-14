"""HTTP 客户端构造与重试约定（两个数据源共用）。

本机 / 内网地址（自建 Overstats、调试用 mock 上游）不能走系统代理：
Windows 上 Clash 之类的工具会写注册表代理，httpx 默认读取它，却拿不到
「本地地址不走代理」的例外，结果 http://127.0.0.1:18080 被转发给代理，返回 502。
"""

from __future__ import annotations

import ipaddress
from typing import Any
from urllib.parse import urlsplit

import httpx

# 网关类错误多半是瞬时的，按 basic.max_retry 重试；其余 4xx/5xx 重试也没用
RETRYABLE_STATUS = frozenset({502, 503, 504})
# 第 n 次重试前等待 n * RETRY_BACKOFF_SECONDS 秒
RETRY_BACKOFF_SECONDS = 0.6


def is_local_host(host: str) -> bool:
    """本机、内网 IP 与单段主机名（docker compose 服务名等）都只能直连。"""
    name = str(host or "").strip().strip("[]").rstrip(".").lower()
    if not name:
        return False
    if name.endswith(".localhost"):
        return True
    try:
        address = ipaddress.ip_address(name)
    except ValueError:
        return "." not in name
    return address.is_loopback or address.is_private or address.is_link_local


# 不针对某个服务地址的客户端（如下载卡片图片）也让这几个本机地址直连
LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")


def build_client(
    base_url: str,
    *,
    timeout: float,
    headers: dict[str, str] | None = None,
    direct_hosts: tuple[str, ...] = (),
) -> httpx.AsyncClient:
    """给某个服务地址建客户端：本地地址绕过环境代理，其余沿用环境设置（代理、证书等）。

    `direct_hosts` 额外指定一批总是直连的主机（例如 LOOPBACK_HOSTS）。
    """
    mounts: dict[str, Any] = {}
    host = urlsplit(str(base_url or "")).hostname or ""
    for name in (host, *direct_hosts) if is_local_host(host) else direct_hosts:
        pattern_host = f"[{name}]" if ":" in name else name
        # transport 为 None 表示这个 host 直连，不经过环境代理
        mounts[f"all://{pattern_host}"] = None
    return httpx.AsyncClient(
        timeout=httpx.Timeout(timeout, connect=min(5.0, timeout)),
        follow_redirects=True,
        headers=headers or {},
        mounts=mounts or None,
    )


__all__ = [
    "LOOPBACK_HOSTS",
    "RETRYABLE_STATUS",
    "RETRY_BACKOFF_SECONDS",
    "build_client",
    "is_local_host",
]
