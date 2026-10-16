"""A client for mpv's JSON IPC: newline-delimited JSON over a Unix socket.

Requests carry a ``request_id`` and mpv's reply echoes it; everything else mpv sends
(events, observed property changes) is unsolicited. One reader thread routes replies to
whichever caller is waiting and hands everything else to ``on_message``.

``on_message`` and ``on_close`` are called on the reader thread, so they must not call
:meth:`command` (the reply could never be read). They should hand off and return.
"""

from __future__ import annotations

import contextlib
import itertools
import json
import socket
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..errors import PlaybackError


class MpvCommandError(PlaybackError):
    """mpv answered a command with an error status (e.g. "property unavailable")."""


class _Pending:
    __slots__ = ("done", "reply")

    def __init__(self) -> None:
        self.done = threading.Event()
        self.reply: dict[str, Any] | None = None


class MpvIpc:
    def __init__(
        self,
        socket_path: Path,
        *,
        on_message: Callable[[dict[str, Any]], None],
        on_close: Callable[[], None] | None = None,
    ) -> None:
        self._path = socket_path
        self._on_message = on_message
        self._on_close = on_close
        self._sock: socket.socket | None = None
        self._send_lock = threading.Lock()
        self._pending: dict[int, _Pending] = {}
        self._pending_lock = threading.Lock()
        self._ids = itertools.count(1)
        self._closed = threading.Event()
        self._reader: threading.Thread | None = None

    def connect(self, timeout: float = 5.0) -> None:
        """Connect, waiting up to ``timeout`` for mpv to create its socket."""
        deadline = time.monotonic() + timeout
        while True:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                sock.connect(str(self._path))
                break
            except (FileNotFoundError, ConnectionRefusedError):
                sock.close()
                if time.monotonic() >= deadline:
                    raise PlaybackError(f"mpv did not open its IPC socket: {self._path}") from None
                time.sleep(0.02)
        self._sock = sock
        self._reader = threading.Thread(target=self._read_loop, name="mpv-ipc", daemon=True)
        self._reader.start()

    @property
    def closed(self) -> bool:
        return self._closed.is_set()

    def command(self, *args: Any, timeout: float = 5.0) -> Any:
        """Send one command and return its ``data``. Raises on an error reply or timeout."""
        if self._sock is None or self.closed:
            raise PlaybackError("mpv IPC is not connected")
        request_id = next(self._ids)
        pending = _Pending()
        with self._pending_lock:
            self._pending[request_id] = pending
        line = json.dumps({"command": list(args), "request_id": request_id}) + "\n"
        try:
            with self._send_lock:
                self._sock.sendall(line.encode("utf-8"))
        except OSError as exc:
            self._forget(request_id)
            raise PlaybackError(f"mpv IPC write failed: {exc}") from exc

        if not pending.done.wait(timeout):
            self._forget(request_id)
            raise PlaybackError(f"mpv did not answer {args[0]!r} within {timeout} s")
        reply = pending.reply
        if reply is None:
            raise PlaybackError("mpv IPC closed while waiting for a reply")
        if reply.get("error") != "success":
            raise MpvCommandError(f"mpv refused {list(args)!r}: {reply.get('error')}")
        return reply.get("data")

    def close(self) -> None:
        self._closed.set()
        if self._sock is not None:
            with contextlib.suppress(OSError):
                self._sock.shutdown(socket.SHUT_RDWR)
            self._sock.close()
        if self._reader is not None and self._reader is not threading.current_thread():
            self._reader.join(timeout=2)

    def _forget(self, request_id: int) -> None:
        with self._pending_lock:
            self._pending.pop(request_id, None)

    def _read_loop(self) -> None:
        assert self._sock is not None
        buffer = b""
        try:
            while not self.closed:
                chunk = self._sock.recv(65536)
                if not chunk:
                    break
                buffer += chunk
                *lines, buffer = buffer.split(b"\n")
                for line in lines:
                    if line.strip():
                        self._dispatch(line)
        except OSError:
            pass
        finally:
            was_closed = self.closed
            self._closed.set()
            # Wake every caller still waiting; their reply is never coming.
            with self._pending_lock:
                waiting, self._pending = list(self._pending.values()), {}
            for pending in waiting:
                pending.done.set()
            if not was_closed and self._on_close:
                self._on_close()

    def _dispatch(self, line: bytes) -> None:
        try:
            message = json.loads(line)
        except ValueError:
            return
        if not isinstance(message, dict):
            return
        request_id = message.get("request_id")
        if "event" not in message and request_id is not None:
            with self._pending_lock:
                pending = self._pending.pop(request_id, None)
            if pending is not None:
                pending.reply = message
                pending.done.set()
            return
        self._on_message(message)
