"""Server-sent events: push every new :class:`PlaybackState` to a connected client.

The handler thread parks on the state bus's condition variable, so an idle stream costs
nothing. A client gets the current snapshot the moment it connects, then each newer one
as it is published. When nothing happens for a while a comment line is sent, both to
keep the connection from looking dead and because writing is the only way to find out
that the client has gone.
"""

from __future__ import annotations

import json
import threading
from typing import BinaryIO

from ..models import PlaybackState
from ..playback.state import StateBus

KEEPALIVE_S = 15.0
# How long a parked stream goes before checking whether the server is shutting down.
_POLL_S = 0.5


def format_event(state: PlaybackState) -> bytes:
    data = json.dumps(state.to_dict(), separators=(",", ":"))
    return f"event: state\nid: {state.version}\ndata: {data}\n\n".encode()


def stream_states(
    out: BinaryIO, bus: StateBus, stop: threading.Event, *, keepalive_s: float = KEEPALIVE_S
) -> None:
    """Write events to ``out`` until the client disconnects or ``stop`` is set."""
    try:
        state = bus.current
        out.write(format_event(state))
        out.flush()
        version = state.version
        quiet_s = 0.0
        while not stop.is_set():
            newer = bus.wait_newer(version, timeout=min(_POLL_S, keepalive_s))
            if newer is not None:
                out.write(format_event(newer))
                version = newer.version
                quiet_s = 0.0
            else:
                quiet_s += min(_POLL_S, keepalive_s)
                if quiet_s < keepalive_s:
                    continue
                out.write(b": keepalive\n\n")
                quiet_s = 0.0
            out.flush()
    except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
        return  # the client went away; nothing to clean up but this thread
