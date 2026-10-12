"""The state bus: the latest :class:`PlaybackState`, and a way to wait for the next one.

The player publishes; the HTTP layer reads. Every published snapshot gets the next
``version``, so a client that sees events out of order can drop the stale one. Event
streams block in :meth:`wait_newer` on a condition variable rather than polling.
"""

from __future__ import annotations

import contextlib
import threading
from collections.abc import Callable
from dataclasses import replace

from ..models import PlaybackState

Listener = Callable[[PlaybackState], None]


class StateBus:
    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._state = PlaybackState()
        self._listeners: list[Listener] = []

    @property
    def current(self) -> PlaybackState:
        with self._condition:
            return self._state

    def publish(self, state: PlaybackState) -> PlaybackState:
        """Stamp ``state`` with the next version, make it current and wake every waiter."""
        with self._condition:
            stamped = replace(state, version=self._state.version + 1)
            self._state = stamped
            listeners = list(self._listeners)
            self._condition.notify_all()
        for listener in listeners:
            # One broken listener must not stop the others, or the player.
            with contextlib.suppress(Exception):
                listener(stamped)
        return stamped

    def wait_newer(self, version: int, timeout: float) -> PlaybackState | None:
        """Block until a snapshot newer than ``version`` exists; ``None`` on timeout."""
        with self._condition:
            if self._condition.wait_for(lambda: self._state.version > version, timeout):
                return self._state
            return None

    def subscribe(self, listener: Listener) -> Callable[[], None]:
        """Call ``listener`` with each new snapshot. Returns a function that unsubscribes."""
        with self._condition:
            self._listeners.append(listener)

        def unsubscribe() -> None:
            with self._condition, contextlib.suppress(ValueError):
                self._listeners.remove(listener)

        return unsubscribe
