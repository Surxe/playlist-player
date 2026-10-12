"""The overlay's link to the daemon: follow the event stream, fetch lyric lines.

One thread. It connects to the event stream, feeds each snapshot to the model, and when
the model says a track's lines are missing it fetches them over the REST API before
reading on. If the daemon is not running, or goes away, the model is told it is
disconnected (which hides the overlay) and the thread keeps retrying with a growing
pause between attempts.
"""

from __future__ import annotations

import contextlib
import http.client
import json
import socket
import threading
from collections.abc import Callable
from urllib.parse import urlsplit

from ..models import LyricLine, PlaybackState
from .presenter import OverlayModel

EVENTS_PATH = "/api/v1/events"
# The daemon sends a keepalive comment every 15 s; silence much longer than that means
# the connection is dead even though nothing closed it.
READ_TIMEOUT_S = 40.0


class DaemonClient:
    def __init__(
        self,
        base_url: str,
        model: OverlayModel,
        *,
        on_change: Callable[[], None],
        reconnect_s: tuple[float, float] = (0.5, 5.0),
        read_timeout_s: float = READ_TIMEOUT_S,
    ) -> None:
        parts = urlsplit(base_url)
        self._host = parts.hostname or "127.0.0.1"
        self._port = parts.port or 80
        self._model = model
        self._on_change = on_change
        self._reconnect_first_s, self._reconnect_max_s = reconnect_s
        self._read_timeout_s = read_timeout_s
        self._stopping = threading.Event()
        self._socket: socket.socket | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="overlay-client", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stopping.set()
        current = self._socket
        if current is not None:
            with contextlib.suppress(OSError):
                current.shutdown(socket.SHUT_RDWR)  # wakes the blocked read
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=3)

    def _run(self) -> None:
        delay = self._reconnect_first_s
        while not self._stopping.is_set():
            try:
                self._follow()
                delay = self._reconnect_first_s  # it was a real connection; start over
            except (OSError, http.client.HTTPException, ValueError):
                pass
            self._model.set_connected(False)
            self._on_change()
            if self._stopping.wait(delay):
                return
            delay = min(delay * 2, self._reconnect_max_s)

    def _follow(self) -> None:
        """Read the event stream until it ends. Raises when it cannot be read at all."""
        connection = http.client.HTTPConnection(
            self._host, self._port, timeout=self._read_timeout_s
        )
        try:
            connection.request("GET", EVENTS_PATH, headers={"Accept": "text/event-stream"})
            self._socket = connection.sock
            response = connection.getresponse()
            if response.status != 200:
                raise OSError(f"event stream answered HTTP {response.status}")
            self._model.set_connected(True)
            self._on_change()

            version = 0
            data: list[str] = []
            for raw in response:
                if self._stopping.is_set():
                    return
                line = raw.decode("utf-8").rstrip("\r\n")
                if line.startswith("data:"):
                    data.append(line[5:].lstrip())
                elif not line and data:
                    state = PlaybackState.from_dict(json.loads("\n".join(data)))
                    data = []
                    if state.version <= version:
                        continue  # stale or repeated
                    version = state.version
                    self._apply(state)
        finally:
            self._socket = None
            connection.close()

    def _apply(self, state: PlaybackState) -> None:
        self._model.set_state(state)
        wanted = self._model.lyrics_wanted()
        if wanted is not None:
            self._model.set_lyrics(wanted, self.fetch_lines(wanted))
        self._on_change()

    def fetch_lines(self, track_id: str) -> tuple[LyricLine, ...]:
        """The track's timed lines from the API; empty when there are none or it fails."""
        connection = http.client.HTTPConnection(self._host, self._port, timeout=5.0)
        try:
            connection.request("GET", f"/api/v1/tracks/{track_id}/lyrics")
            response = connection.getresponse()
            body = response.read()
            if response.status != 200:
                return ()
            lines = json.loads(body).get("lines") or []
            return tuple(LyricLine(int(line["t_ms"]), str(line["text"])) for line in lines)
        except (OSError, http.client.HTTPException, ValueError, KeyError, TypeError):
            return ()
        finally:
            connection.close()
