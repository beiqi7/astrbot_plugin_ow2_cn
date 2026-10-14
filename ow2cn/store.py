"""BattleTag 绑定关系的持久化。

写在 AstrBot 的 data 目录（`data/plugin_data/<plugin_name>/bindings.json`），
而不是插件自身目录，避免插件更新/重装时被覆盖。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

SCOPE_GROUP = "group"
SCOPE_USER = "user"


class BindingStore:
    def __init__(self, path: Path, logger: Any = None) -> None:
        self._path = Path(path)
        self._logger = logger
        self._data: dict[str, dict[str, dict[str, Any]]] = {
            SCOPE_GROUP: {},
            SCOPE_USER: {},
        }
        self._load()

    # ---------- 读写文件 ----------

    def _load(self) -> None:
        try:
            raw = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return
        except (OSError, UnicodeDecodeError) as exc:
            self._quarantine(f"读取失败：{exc}")
            return
        try:
            data = json.loads(raw or "{}")
        except ValueError as exc:
            self._quarantine(f"不是合法 JSON：{exc}")
            return
        if not isinstance(data, dict):
            self._quarantine("顶层不是对象")
            return
        for scope in (SCOPE_GROUP, SCOPE_USER):
            entries = data.get(scope)
            if isinstance(entries, dict):
                self._data[scope] = {str(k): v for k, v in entries.items() if isinstance(v, dict)}

    def _quarantine(self, reason: str) -> None:
        """绑定文件读不出来时先改名备份，否则下一次绑定会用空数据把它整个覆盖掉。"""
        backup = self._path.with_name(f"{self._path.name}.corrupt-{int(time.time())}")
        try:
            os.replace(self._path, backup)
        except OSError as exc:
            backup_note = f"备份失败（{exc}），原文件保留不动"
        else:
            backup_note = f"已备份为 {backup.name}"
        if self._logger:
            self._logger.warning(f"绑定文件 {self._path} {reason}；{backup_note}，按空绑定启动。")

    def _flush(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_name(f"{self._path.name}.tmp")
        payload = json.dumps(self._data, ensure_ascii=False, indent=2)
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, self._path)  # 原子替换，避免写坏

    # ---------- 绑定操作 ----------

    def get(self, scope: str, key: str) -> dict[str, Any] | None:
        entry = self._data.get(scope, {}).get(str(key))
        return dict(entry) if entry else None

    def set(
        self,
        scope: str,
        key: str,
        tag: str,
        *,
        operator_id: str = "",
        operator_name: str = "",
    ) -> dict[str, Any]:
        entry = {
            "tag": str(tag),
            "operator_id": str(operator_id),
            "operator_name": str(operator_name),
            "bound_at": int(time.time()),
        }
        self._data.setdefault(scope, {})[str(key)] = entry
        self._flush()
        return entry

    def remove(self, scope: str, key: str) -> bool:
        if str(key) in self._data.get(scope, {}):
            self._data[scope].pop(str(key), None)
            self._flush()
            return True
        return False

    def count(self) -> dict[str, int]:
        return {scope: len(entries) for scope, entries in self._data.items()}

    def clear(self, scope: str = "") -> int:
        """清空绑定（scope 为空时清空全部），返回删除条数。"""
        removed = 0
        scopes = [scope] if scope in {SCOPE_GROUP, SCOPE_USER} else [SCOPE_GROUP, SCOPE_USER]
        for item in scopes:
            removed += len(self._data.get(item, {}))
            self._data[item] = {}
        if removed:
            self._flush()
        return removed

    @property
    def path(self) -> Path:
        return self._path
