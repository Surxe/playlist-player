"""Time for the overlay: the wall clock, and where playback is on it.

The daemon sends a snapshot only when something changes. Each one says "playback was at
this position at this wall-clock instant", and both ends run on one machine, so the
position right now is that position plus the time since. The arithmetic is
:meth:`PlaybackState.position_at`; this module only fixes the units.
"""

from __future__ import annotations

import time

from ..models import PlaybackState


def now_epoch_ms() -> int:
    return int(time.time() * 1000)


def position_ms(state: PlaybackState, now_ms: int) -> float:
    """Playback position at ``now_ms``, in milliseconds."""
    return state.position_at(now_ms) * 1000
