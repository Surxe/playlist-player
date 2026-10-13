"""The player contract: what the daemon needs from whatever makes the sound.

One implementation today (mpv). The HTTP layer is written against this protocol, and
its tests use a fake that implements it, so nothing above this file knows about mpv.
State flows one way: the player publishes snapshots on the :class:`StateBus` it is
given, and callers read the bus. Transport methods return nothing.
"""

from __future__ import annotations

from typing import Protocol

from .queue import PlayQueue
from .state import StateBus


class Player(Protocol):
    bus: StateBus

    def load_queue(
        self, queue: PlayQueue, *, paused: bool = False, position_s: float | None = None
    ) -> None:
        """Replace the queue and start at ``queue.start_index``.

        ``position_s``, when given, is where the first track starts (a resumed song).
        """
        ...

    def play(self) -> None:
        """Resume; after the queue has ended or been stopped, start it again."""
        ...

    def pause(self) -> None: ...

    def toggle(self) -> None: ...

    def next(self) -> None: ...

    def prev(self) -> None:
        """Previous track, or the start of this one when more than a moment into it."""
        ...

    def seek(self, *, position_s: float | None = None, delta_s: float | None = None) -> None:
        """Jump to an absolute position, or by a relative amount. Exactly one is given."""
        ...

    def set_volume(self, volume: float) -> None:
        """0 to 100."""
        ...

    def set_offset(self, track_id: str, offset_ms: int) -> None:
        """Tell the player a track's lyric offset changed, so the next snapshot carries it."""
        ...

    def stop(self) -> None:
        """Stop playback but keep the queue."""
        ...

    def close(self) -> None:
        """Shut the player down for good."""
        ...
