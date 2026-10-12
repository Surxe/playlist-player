"""A small JSON-over-HTTP client on top of ``urllib``.

Every outbound request in the package goes through :class:`HttpClient`, for three
reasons: one place decides retries and timeouts, one place sets the User-Agent the
sources ask for, and tests can swap the transport to assert that a cached run makes no
requests at all.

Headers are never logged or put in exception text: the Apify token travels in one.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from . import __version__
from .errors import HttpError, NotFoundError

USER_AGENT = f"playlist-player/{__version__} (+https://github.com/Surxe/playlist-player)"

_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
_BODY_EXCERPT = 300

# (request, timeout_s) -> (status, body). Raising urllib.error.HTTPError / URLError is allowed.
Transport = Callable[[urllib.request.Request, float], tuple[int, bytes]]


def _urllib_transport(request: urllib.request.Request, timeout: float) -> tuple[int, bytes]:
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.status, response.read()


def _strip_query(url: str) -> str:
    """URL without its query string, safe to show in errors and logs."""
    return url.split("?", 1)[0]


class HttpClient:
    def __init__(
        self,
        *,
        timeout: float = 30.0,
        retries: int = 3,
        backoff_s: float = 1.5,
        transport: Transport = _urllib_transport,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._timeout = timeout
        self._retries = retries
        self._backoff_s = backoff_s
        self._transport = transport
        self._sleep = sleep
        self.request_count = 0

    def request(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        body: bytes | None = None,
        timeout: float | None = None,
    ) -> bytes:
        """Send one request, retrying transient failures, and return the response body.

        Raises :class:`NotFoundError` on 404 and :class:`HttpError` on any other failure.
        """
        if params:
            clean = {k: v for k, v in params.items() if v is not None}
            url = f"{url}{'&' if '?' in url else '?'}{urllib.parse.urlencode(clean)}"
        all_headers = {"User-Agent": USER_AGENT, "Accept": "application/json", **(headers or {})}
        shown = _strip_query(url)

        for attempt in range(1, self._retries + 1):
            request = urllib.request.Request(url, data=body, method=method, headers=all_headers)
            self.request_count += 1
            try:
                status, payload = self._transport(request, timeout or self._timeout)
            except urllib.error.HTTPError as exc:
                status, payload = exc.code, exc.read() or b""
            except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
                if attempt < self._retries:
                    self._sleep(self._backoff_s * attempt)
                    continue
                raise HttpError(None, shown, str(getattr(exc, "reason", exc))) from exc

            if 200 <= status < 300:
                return payload
            if status == 404:
                raise NotFoundError(status, shown)
            if status in _RETRY_STATUSES and attempt < self._retries:
                self._sleep(self._backoff_s * attempt)
                continue
            excerpt = payload[:_BODY_EXCERPT].decode("utf-8", "replace").strip()
            raise HttpError(status, shown, excerpt)

        raise AssertionError("unreachable: the loop returns or raises")  # pragma: no cover

    def get_json(self, url: str, *, params: Mapping[str, Any] | None = None, **kwargs: Any) -> Any:
        return self._decode(self.request("GET", url, params=params, **kwargs), url)

    def post_json(
        self,
        url: str,
        payload: Any,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        **kwargs: Any,
    ) -> Any:
        body = json.dumps(payload).encode("utf-8")
        merged = {"Content-Type": "application/json", **(headers or {})}
        raw = self.request("POST", url, params=params, headers=merged, body=body, **kwargs)
        return self._decode(raw, url)

    def download(self, url: str, dest: Path, write: Callable[[Path, bytes], None]) -> int:
        """Fetch ``url`` and hand the bytes to ``write`` (the cache's atomic writer)."""
        data = self.request("GET", url, headers={"Accept": "*/*"})
        write(dest, data)
        return len(data)

    @staticmethod
    def _decode(raw: bytes, url: str) -> Any:
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise HttpError(200, _strip_query(url), f"response is not JSON: {exc}") from exc
