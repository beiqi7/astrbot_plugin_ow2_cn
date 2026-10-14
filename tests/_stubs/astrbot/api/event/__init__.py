"""`astrbot.api.event` 的桩实现：AstrMessageEvent 与 filter。"""

from __future__ import annotations

from typing import Any, Optional

from astrbot.api.message_components import At, BaseMessageComponent, Image, Plain


class MessageEventResult:
    """与 AstrBot 的 MessageEventResult 行为一致的极简版本。"""

    def __init__(self, chain: list[BaseMessageComponent] | None = None) -> None:
        self.chain = chain or []

    @property
    def text(self) -> str:
        return "".join(c.text for c in self.chain if isinstance(c, Plain))

    @property
    def images(self) -> list[Image]:
        return [c for c in self.chain if isinstance(c, Image)]

    def __repr__(self) -> str:  # pragma: no cover
        return f"<MessageEventResult {self.chain!r}>"


class AstrMessageEvent:
    """测试用的消息事件：只实现插件真正用到的方法。"""

    def __init__(
        self,
        *,
        message_str: str = "",
        group_id: str = "",
        sender_id: str = "10000",
        sender_name: str = "测试用户",
        at_qq: str | None = None,
        admin: bool = False,
        self_id: str = "999",
        chain: list[BaseMessageComponent] | None = None,
    ) -> None:
        self.message_str = message_str
        self._group_id = group_id
        self._sender_id = sender_id
        self._sender_name = sender_name
        self._self_id = self_id
        self.is_at_or_wake_command = True
        self._admin = admin
        if chain is not None:
            # 调用方直接给出真实形态的消息链（含 @机器人 / @群友）。
            self._chain = chain
        else:
            self._chain = [Plain(text=message_str)]
            if at_qq:
                self._chain.append(At(qq=at_qq))

    # --- 与 AstrBot 同名的方法 ---

    def get_message_str(self) -> str:
        return self.message_str

    def get_group_id(self) -> str:
        return self._group_id

    def get_self_id(self) -> str:
        return self._self_id

    def get_sender_id(self) -> str:
        return self._sender_id

    def get_sender_name(self) -> str:
        return self._sender_name

    def get_messages(self) -> list[BaseMessageComponent]:
        return self._chain

    def is_admin(self) -> bool:
        return self._admin

    def plain_result(self, text: str) -> MessageEventResult:
        return MessageEventResult([Plain(text=text)])

    def image_result(self, url_or_path: str) -> MessageEventResult:
        return MessageEventResult(
            [
                Image.fromURL(url_or_path)
                if str(url_or_path).startswith("http")
                else Image.fromFileSystem(url_or_path)
            ]
        )

    def chain_result(self, chain: list[BaseMessageComponent]) -> MessageEventResult:
        return MessageEventResult(chain)

    def make_result(self) -> MessageEventResult:
        return MessageEventResult()


__all__ = ["AstrMessageEvent", "MessageEventResult"]
