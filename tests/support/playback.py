"""Helpers for tests that watch playback state."""

from __future__ import annotations

import time
from collections.abc import Callable

from playlist_player.models import PlaybackState
from playlist_player.playback.state import StateBus


class StateLog:
    """Records every snapshot published on a bus and waits for one that matches."""

    def __init__(self, bus: StateBus) -> None:
        self.bus = bus
        self.states: list[PlaybackState] = []
        bus.subscribe(self.states.append)

    def wait_for(
        self, predicate: Callable[[PlaybackState], bool], timeout: float = 10.0, *, after: int = 0
    ) -> PlaybackState:
        """The first snapshot newer than version ``after`` that satisfies ``predicate``."""
        deadline = time.monotonic() + timeout
        seen = after
        while True:
            for state in list(self.states):
                if state.version > after and predicate(state):
                    return state
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                trail = [(s.version, s.status.value, s.track_id) for s in self.states[-8:]]
                raise AssertionError(f"no matching snapshot within {timeout} s; last: {trail}")
            newer = self.bus.wait_newer(seen, min(remaining, 0.5))
            if newer is not None:
                seen = newer.version

    def statuses(self, *, after: int = 0) -> list[str]:
        """Status values in publication order, consecutive repeats collapsed."""
        out: list[str] = []
        for state in self.states:
            if state.version > after and (not out or out[-1] != state.status.value):
                out.append(state.status.value)
        return out
