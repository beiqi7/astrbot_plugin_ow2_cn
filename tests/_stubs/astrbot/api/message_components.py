"""`astrbot.api.message_components` 的桩实现。"""

from __future__ import annotations

import base64
from pathlib import Path


class BaseMessageComponent:
    def __init__(self, **kwargs) -> None:
        for key, value in kwargs.items():
            setattr(self, key, value)


class Plain(BaseMessageComponent):
    def __init__(self, text: str = "", **kwargs) -> None:
        super().__init__(text=text, **kwargs)


class At(BaseMessageComponent):
    def __init__(self, qq=None, name: str | None = "", **kwargs) -> None:
        super().__init__(qq=qq, name=name, **kwargs)


class Image(BaseMessageComponent):
    def __init__(self, file: str | None = "", **kwargs) -> None:
        super().__init__(file=file, **kwargs)

    @staticmethod
    def fromURL(url: str, **kwargs):
        return Image(file=url, **kwargs)

    @staticmethod
    def fromFileSystem(path, **kwargs):
        return Image(file=str(Path(path).resolve()), **kwargs)

    @staticmethod
    def fromBase64(data: str, **kwargs):
        return Image(file=f"base64://{data}", **kwargs)

    @staticmethod
    def fromBytes(data: bytes):
        return Image.fromBase64(base64.b64encode(data).decode())


__all__ = ["At", "BaseMessageComponent", "Image", "Plain"]
