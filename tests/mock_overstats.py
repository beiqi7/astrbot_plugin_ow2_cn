"""假的 Overstats 服务：验证"插件端零 token"这条链路。

行为按实测（真实 Overstats 服务）对齐，几处关键点：

- `/dashen-match` **忽略 mode/limit**，返回竞技 + 快速按时间混排的列表；
- `/dashen-match/detail` 传 `index` 时按混排列表定位（会串场），
  传 `match_id` 时必须同时给 `customer_token`；
- `/dashen-match/detail/replies` 支持 `match_id` 精确定位并返回 base64 图片；
- 查无此人是 **HTTP 404** + `{"error": "bnet_not_found"}`（不是 200）。
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

import fixtures


class _Handler(BaseHTTPRequestHandler):
    server_version = "MockOverstats/2.0"

    def _read_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except ValueError:
            return {}

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _png(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(fixtures.PNG_BYTES)))
        self.end_headers()
        self.wfile.write(fixtures.PNG_BYTES)

    def _known_match_ids(self) -> set[str]:
        return {str(item["matchId"]) for item in fixtures.pool_payload()}

    def do_POST(self) -> None:
        state: MockOverstatsState = self.server.state  # type: ignore[attr-defined]
        body = self._read_body()
        headers = {k.lower(): v for k, v in self.headers.items()}
        path = urlparse(self.path).path
        state.requests.append({"path": path, "body": body, "headers": headers})

        if state.require_api_key and headers.get("authorization") != f"Bearer {state.api_key}":
            self._json(401, {"ok": False, "error": "unauthorized", "message": "bad key"})
            return

        tag = str(body.get("bnet_id") or "")
        known = tag.strip().lower() == fixtures.TEST_TAG.lower()

        if path.endswith("/dashen-profile/image"):
            if not known:
                self._json(404, fixtures.NOT_FOUND_BODY)
                return
            self._png()
            return
        if path.endswith("/dashen-match/image"):
            self._png()
            return
        if path.endswith("/dashen-match/detail/replies"):
            self._detail_replies(body)
            return
        if path.endswith("/dashen-match/detail"):
            self._detail(body)
            return
        if path.endswith("/dashen-profile"):
            if not known:
                self._json(404, fixtures.NOT_FOUND_BODY)
                return
            self._json(
                200,
                {
                    "ok": True,
                    "customer_token": fixtures.TEST_TOKEN,
                    "resolved": {
                        "query": tag,
                        "full_id": tag,
                        "bnet_id": "663886420",
                        "has_customer_token": True,
                    },
                    "profile_card": {"data": fixtures.search_payload()},
                    "sport": {"data": fixtures.count_info_payload()},
                    "leisure": {"data": fixtures.quick_count_info_payload()},
                },
            )
            return
        if path.endswith("/dashen-match"):
            # 实测：忽略 mode 与 limit，永远返回混排列表
            entries = fixtures.pool_payload()
            self._json(
                200,
                {
                    "ok": True,
                    "customer_token": fixtures.TEST_TOKEN,
                    "count": len(entries),
                    "matches": entries,
                },
            )
            return
        self._json(404, {"ok": False, "error": "not_found", "message": path})

    def _detail(self, body: dict[str, Any]) -> None:
        match_id = str(body.get("match_id") or "")
        if match_id:
            if not body.get("customer_token"):
                self._json(
                    400,
                    {
                        "ok": False,
                        "error": "missing_customer_token",
                        "message": "customer_token is required when querying detail by match_id",
                    },
                )
                return
            if match_id not in self._known_match_ids():
                self._json(404, {"ok": False, "error": "not_found", "message": match_id})
                return
            self._json(200, fixtures.detail_payload(match_id))
            return
        if "index" not in body:
            self._json(
                400,
                {"ok": False, "error": "missing_match_selector", "message": "index required"},
            )
            return
        # 实测：服务端按混排列表定位 index
        entries = fixtures.pool_payload()
        index = int(body.get("index") or 0)
        if index < 0 or index >= len(entries):
            self._json(
                400,
                {
                    "ok": False,
                    "error": "match_index_out_of_range",
                    "message": f"Match index out of range: {index}",
                },
            )
            return
        self._json(200, fixtures.detail_payload(str(entries[index]["matchId"])))

    def _detail_replies(self, body: dict[str, Any]) -> None:
        match_id = str(body.get("match_id") or "")
        if not match_id:
            self._json(
                400,
                {"ok": False, "error": "missing_match_selector", "message": "index required"},
            )
            return
        if not body.get("customer_token"):
            self._json(
                400,
                {
                    "ok": False,
                    "error": "missing_customer_token",
                    "message": "customer_token is required when querying by match_id",
                },
            )
            return
        if match_id not in self._known_match_ids():
            self._json(404, {"ok": False, "error": "not_found", "message": match_id})
            return
        self._json(200, fixtures.detail_image_replies(match_id))

    def log_message(self, *_args: Any) -> None:
        return


class MockOverstatsState:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.require_api_key = False
        self.api_key = "demo-key"

    def count_path(self, suffix: str) -> int:
        return sum(1 for item in self.requests if item["path"].endswith(suffix))


class MockOverstatsServer:
    def __init__(self) -> None:
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.state = MockOverstatsState()

    @property
    def base_url(self) -> str:
        host, port = self._httpd.server_address[:2]  # type: ignore[union-attr]
        return f"http://{host}:{port}"

    def __enter__(self) -> MockOverstatsServer:
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
