"""假的网易大神上游：在本地起一个 HTTP 服务，行为与真实上游对齐。

要点：
- 路径与真实上游一致（/v1/a19ld5tool/...），所以插件的 URL 拼装会被真实校验
- token 不对 → HTTP 401 + {"code":401,"errMsg":"Token Invalid"}（与真机行为一致）
- 记录收到的请求头与路径，测试可以断言 GL-* 头是否齐全
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

import fixtures

EXPECTED_TOKEN = "ds_demo_token_123"
EXPECTED_ROLE_ID = "823456789"


class _Handler(BaseHTTPRequestHandler):
    server_version = "MockDashen/1.0"

    # --- 工具 ---

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _record(self) -> None:
        state: MockState = self.server.state  # type: ignore[attr-defined]
        state.requests.append(
            {
                "method": self.command,
                "path": urlparse(self.path).path,
                "query": {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()},
                "headers": {k: v for k, v in self.headers.items()},
                "body": self._read_body(),
            },
        )

    def _read_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except ValueError:
            return {}

    def _authorized(self) -> bool:
        headers = {k.lower(): v for k, v in self.headers.items()}
        if headers.get("gl-bigdata-auth-token") != EXPECTED_TOKEN:
            self._json(401, {"code": 401, "errMsg": "Token Invalid"})
            return False
        return True

    # --- 路由 ---

    def do_POST(self) -> None:
        self._record()
        if not self._authorized():
            return
        path = urlparse(self.path).path
        if path.endswith("/searchBnetAccount"):
            body = self.server.state.last_body  # type: ignore[attr-defined]
            name = str(body.get("name") or "")
            if name.strip().lower() != fixtures.TEST_TAG.lower():
                self._json(200, {"code": 0, "data": {}, "success": True})
                return
            if body.get("token") != EXPECTED_TOKEN or str(body.get("roleId")) != EXPECTED_ROLE_ID:
                self._json(200, {"code": 20001, "errMsg": "参数非法", "data": None})
                return
            self._json(200, {"code": 0, "data": fixtures.search_payload(), "success": True})
            return
        self._json(404, {"code": 404, "errMsg": "Not Found"})

    def do_GET(self) -> None:
        self._record()
        if not self._authorized():
            return
        parsed = urlparse(self.path)
        path = parsed.path
        query = {k: v[0] for k, v in parse_qs(parsed.query).items()}

        if path.endswith("/customer/queryCard"):
            self._json(200, {"code": 0, "data": fixtures.card_payload(), "success": True})
            return
        if path.endswith("/customer/queryCountInfo"):
            self._json(200, {"code": 0, "data": fixtures.count_info_payload(), "success": True})
            return
        if path.endswith("/customer/queryMatchList"):
            state: MockState = self.server.state  # type: ignore[attr-defined]
            page = int(query.get("page") or 1)
            items = fixtures.match_list_payload()
            if state.ignore_page:
                # 有的上游版本会忽略 page，每页都回同一份列表
                data = items
            else:
                # 按固定页大小分页：8 条对局、每页 3 条 → 3 / 3 / 2，第 4 页为空
                start = (page - 1) * state.page_size
                data = items[start : start + state.page_size]
            self._json(200, {"code": 0, "data": data, "success": True})
            return
        if path.endswith("/customer/queryMatchInfo"):
            self._json(
                200,
                {
                    "code": 0,
                    "data": fixtures.match_info_payload(query.get("matchId") or "m-0001"),
                    "success": True,
                },
            )
            return
        self._json(404, {"code": 404, "errMsg": "Not Found"})

    def log_message(self, *_args: Any) -> None:  # 静音
        return


class MockState:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        # queryMatchList 的分页行为（上游页大小没有文档，测试里故意和插件的 5 条/页错开）
        self.page_size = 3
        self.ignore_page = False

    @property
    def last_body(self) -> dict[str, Any]:
        for item in reversed(self.requests):
            if item.get("body"):
                return item["body"]
        return {}

    def paths(self) -> list[str]:
        return [item["path"] for item in self.requests]

    def count_path(self, suffix: str) -> int:
        return sum(1 for path in self.paths() if path.endswith(suffix))

    def clear(self) -> None:
        self.requests.clear()
        self.page_size = 3
        self.ignore_page = False


class MockDashenServer:
    """上下文管理器：with MockDashenServer() as server: server.api_root"""

    def __init__(self, token: str = EXPECTED_TOKEN) -> None:
        self.token = token
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.state = MockState()

    @property
    def api_root(self) -> str:
        host, port = self._httpd.server_address[:2]  # type: ignore[union-attr]
        return f"http://{host}:{port}/v1/a19ld5tool"

    def __enter__(self) -> MockDashenServer:
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._httpd.state = self.state  # type: ignore[attr-defined]
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_exc) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2)
