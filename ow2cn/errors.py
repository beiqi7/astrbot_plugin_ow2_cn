"""插件内部统一异常。

约定：`OwError.message` 是可以直接展示给群友的中文提示，不需要再加工。
其他未捕获异常由 main.py 兜底，统一提示"查询失败"。
"""

from __future__ import annotations


class OwError(Exception):
    """所有可预期错误的基类。"""

    code = "ow_error"

    def __init__(self, message: str, *, hint: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint

    def user_text(self) -> str:
        """给用户看的文案。"""
        if self.hint:
            return f"{self.message}\n提示：{self.hint}"
        return self.message


class ConfigError(OwError):
    """配置缺失或非法。"""

    code = "config_error"


class PlayerNotFound(OwError):
    """BattleTag 查不到对应账号。"""

    code = "player_not_found"


class AmbiguousTag(OwError):
    """省略了 #数字 且无法唯一命中。"""

    code = "ambiguous_tag"


class AuthError(OwError):
    """上游鉴权失败（token 失效 / 未配置）。"""

    code = "auth_error"


class UpstreamError(OwError):
    """上游返回了业务错误。"""

    code = "upstream_error"


class SourceUnavailable(OwError):
    """数据源不可用（网络错误、服务未启动等）。"""

    code = "source_unavailable"


class RateLimited(OwError):
    """触发上游限流。"""

    code = "rate_limited"
