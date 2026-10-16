"""Server-sent events: push playback state, and overlay settings, to a connected client.

Two kinds of event share the stream. ``state`` carries each new
:class:`PlaybackState`; ``overlay`` carries the overlay settings document whenever it
changes. A client gets the current value of both the moment it connects.

The handler thread sleeps until one of the two sources says it has something newer, so
an idle stream costs nothing. When nothing happens for a while a comment line is sent,
both to keep the connection from looking dead and because writing is the only way to
find out that the client has gone.
"""

from __future__ import annotations

import json
import threading
import time
from typing import Any, BinaryIO

from ..models import PlaybackState
from ..playback.state import StateBus
from .overlay_config import OverlayConfig

KEEPALIVE_S = 15.0
# How long a parked stream goes before checking whether the server is shutting down.
_POLL_S = 0.5


def format_event(state: PlaybackState) -> bytes:
    data = json.dumps(state.to_dict(), separators=(",", ":"))
    return f"event: state\nid: {state.version}\ndata: {data}\n\n".encode()


def format_overlay_event(document: dict[str, Any]) -> bytes:
    # No "id": a stream has one last-event ID, and it belongs to the playback state.
    data = json.dumps(document, separators=(",", ":"))
    return f"event: overlay\ndata: {data}\n\n".encode()


def stream_states(
    out: BinaryIO,
    bus: StateBus,
    stop: threading.Event,
    *,
    keepalive_s: float = KEEPALIVE_S,
    overlay: OverlayConfig | None = None,
) -> None:
    """Write events to ``out`` until the client disconnects or ``stop`` is set."""
    wake = threading.Event()
    unsubscribe = [bus.subscribe(lambda _state: wake.set())]
    if overlay is not None:
        unsubscribe.append(overlay.subscribe(wake.set))
    try:
        state = bus.current
        out.write(format_event(state))
        version = state.version
        overlay_version = 0
        if overlay is not None:
            document = overlay.snapshot()
            out.write(format_overlay_event(document))
            overlay_version = document["version"]
        out.flush()
        last_write = time.monotonic()
        while not stop.is_set():
            wake.wait(min(_POLL_S, keepalive_s))
            # Cleared before reading: anything published after this point sets it again,
            # and anything published before it is what the reads below pick up.
            wake.clear()
            wrote = False
            state = bus.current
            if state.version > version:
                out.write(format_event(state))
                version = state.version
                wrote = True
            if overlay is not None and overlay.version > overlay_version:
                document = overlay.snapshot()
                out.write(format_overlay_event(document))
                overlay_version = document["version"]
                wrote = True
            if not wrote:
                if time.monotonic() - last_write < keepalive_s:
                    continue
                out.write(b": keepalive\n\n")
            out.flush()
            last_write = time.monotonic()
    except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
        return  # the client went away; nothing to clean up but this thread
    finally:
        for stop_listening in unsubscribe:
            stop_listening()
