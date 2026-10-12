"""A real HTTP server on a loopback port, standing in for a third-party API.

Tests register a handler per (method, path) and afterwards inspect the requests the code
under test actually sent: method, path, query, headers and body. Anything unregistered
answers 404, so an unexpected call fails loudly instead of hanging.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit


@dataclass(frozen=True)
class Received:
    method: str
    path: str
    query: dict[str, str]
    headers: dict[str, str]  # keys lower-cased
    body: bytes

    def json(self) -> Any:
        return json.loads(self.body)


# A handler returns (status, body); a dict or list body is sent as JSON.
Reply = tuple[int, Any]
Handler = Callable[[Received], Reply]


class FakeServer:
    def __init__(self) -> None:
        self.requests: list[Received] = []
        self._routes: dict[tuple[str, str], Handler] = {}
        self._lock = threading.Lock()
        outer = self

        class _Handler(BaseHTTPRequestHandler):
            def _serve(self) -> None:
                parts = urlsplit(self.path)
                length = int(self.headers.get("Content-Length") or 0)
                received = Received(
                    method=self.command,
                    path=parts.path,
                    query={k: v[-1] for k, v in parse_qs(parts.query).items()},
                    headers={k.lower(): v for k, v in self.headers.items()},
                    body=self.rfile.read(length) if length else b"",
                )
                with outer._lock:
                    outer.requests.append(received)
                    handler = outer._routes.get((received.method, received.path))
                status, body = handler(received) if handler else (404, {"error": "no route"})
                if isinstance(body, dict | list):
                    body = json.dumps(body).encode("utf-8")
                elif isinstance(body, str):
                    body = body.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            do_GET = do_POST = _serve  # noqa: N815 - names fixed by BaseHTTPRequestHandler

            def log_message(self, *_args: Any) -> None:  # keep test output quiet
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(
            target=self._server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
        )
        self._thread.start()

    @property
    def url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    def route(self, method: str, path: str, handler: Handler | Reply) -> None:
        """Answer ``method path`` with a handler, or with a fixed (status, body) reply."""
        self._routes[(method, path)] = handler if callable(handler) else (lambda _r: handler)

    def script(self, method: str, path: str, *replies: Reply) -> None:
        """Answer successive calls with ``replies`` in turn; the last one repeats."""
        remaining = list(replies)

        def handler(_request: Received) -> Reply:
            with self._lock:
                return remaining.pop(0) if len(remaining) > 1 else remaining[0]

        self._routes[(method, path)] = handler

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)
