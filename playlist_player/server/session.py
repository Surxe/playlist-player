"""What the daemon remembers between runs: the shuffle, the song, its position and the volume.

One small file in the state directory (``session.json``), rewritten whenever what it
holds changes: on every playback snapshot, and once more as the daemon stops.

Shuffle. The order is drawn when shuffle is turned on, or when a different playlist is
asked for while it is on. It is kept, so the same order plays again after a restart and
when a track is started part-way through, until shuffle is turned off or the playlist's
tracks change. Turning shuffle off forgets the order.

Resume. On start the saved volume is put back, then the saved playlist is loaded (in its
saved order if shuffled), at the saved track and position. It is loaded paused: audio
does not begin on its own at boot. A song whose saved position is at its very end starts
again from the beginning.

Nothing is written before the daemon has tried to resume. Until then the player is only
reporting what it started with, and that must not overwrite what was saved.
"""

from __future__ import annotations

import contextlib
import random
import sys
import threading
import time
from collections.abc import Callable
from typing import Any

from ..cache import CacheStore
from ..errors import CacheError, NotInCacheError, PlaybackError
from ..models import PlaybackState
from ..playback.base import Player
from ..playback.queue import PlayQueue
from .library import Library

# A saved song this close to its end starts again instead, rather than ending at once.
_END_MARGIN_S = 1.0


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


class PlaybackSession:
    def __init__(
        self,
        store: CacheStore,
        library: Library,
        player: Player,
        *,
        rng: random.Random | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._store = store
        self._library = library
        self._player = player
        self._rng = rng or random.Random()
        self._clock = clock
        self._lock = threading.Lock()
        self._data: dict[str, Any] = self._read()
        self._saving = False
        player.bus.subscribe(self._on_state)

    # ------------------------------- storage ------------------------------- #

    def _read(self) -> dict[str, Any]:
        try:
            stored = self._store.read_json(self._store.session_json, {})
        except (CacheError, OSError) as exc:
            print(f"playlist_player: {exc}; starting without the saved session", file=sys.stderr)
            return {}
        if not isinstance(stored, dict):
            print(
                "playlist_player: the saved session is not an object; ignoring it", file=sys.stderr
            )
            return {}
        return stored

    def _save(self) -> None:
        """Write what is held. A failure is reported, never raised: playback goes on."""
        try:
            self._store.write_json(self._store.session_json, self._data)
        except (CacheError, OSError) as exc:
            print(f"playlist_player: could not save the session: {exc}", file=sys.stderr)

    # ------------------------------- shuffle ------------------------------- #

    def _draw(self, playlist_id: str, playable: list[str]) -> list[str]:
        """A new random order for the playable tracks. Call with the lock held."""
        order = list(playable)
        self._rng.shuffle(order)
        self._data["order"] = order
        self._data["order_playlist_id"] = playlist_id
        return order

    def _order_for(self, playlist_id: str, playable: list[str]) -> list[str]:
        """The kept order when it still fits this playlist, else a new one. Lock held."""
        kept = self._data.get("order")
        fits = (
            self._data.get("order_playlist_id") == playlist_id
            and isinstance(kept, list)
            and sorted(kept) == sorted(playable)
        )
        return list(kept) if fits else self._draw(playlist_id, playable)

    def document(self) -> dict[str, Any]:
        """What the page shows of the session."""
        with self._lock:
            return {
                "shuffle": self._data.get("shuffle") is True,
                "playlist_id": self._data.get("playlist_id"),
                "track_id": self._data.get("track_id"),
                "position_s": _number(self._data.get("position_s")) or 0.0,
                "volume": _number(self._data.get("volume")),
            }

    def set_shuffle(self, enabled: bool) -> dict[str, Any]:
        """Turn shuffle on (drawing a fresh order) or off (forgetting the order)."""
        with self._lock:
            self._data["shuffle"] = enabled
            playlist_id = self._data.get("playlist_id")
            if not enabled:
                self._data.pop("order", None)
                self._data.pop("order_playlist_id", None)
            elif isinstance(playlist_id, str):
                # With nothing playable there is no order yet; the next load draws one.
                with contextlib.suppress(NotInCacheError):
                    self._draw(playlist_id, self._library.playable_ids(playlist_id))
            self._save()
        return self.document()

    # -------------------------------- loading ------------------------------ #

    def queue_for(
        self, playlist_id: str, *, start_track_id: str | None, shuffle: bool
    ) -> PlayQueue:
        """The queue for a request to play a playlist, recorded as the session's."""
        with self._lock:
            order = None
            if shuffle:
                order = self._order_for(playlist_id, self._library.playable_ids(playlist_id))
            queue = self._library.queue_for(playlist_id, start_track_id=start_track_id, order=order)
            self._data["shuffle"] = shuffle
            self._data["playlist_id"] = playlist_id
            if not shuffle:
                self._data.pop("order", None)
                self._data.pop("order_playlist_id", None)
            self._save()
        return queue

    # ------------------------------- recording ----------------------------- #

    def _on_state(self, state: PlaybackState) -> None:
        with self._lock:
            if self._saving:
                self._record(state)

    def _record(self, state: PlaybackState) -> None:
        """Keep what ``state`` says about the song, its position and the volume. Lock held."""
        if state.playlist_id is not None:
            self._data["playlist_id"] = state.playlist_id
        if state.track_id is not None:
            now_ms = int(self._clock() * 1000)
            self._data["track_id"] = state.track_id
            self._data["position_s"] = round(state.position_at(now_ms), 2)
        self._data["volume"] = round(float(state.volume), 2)
        self._save()

    def flush(self) -> None:
        """Record where playback is now, as the daemon stops."""
        with self._lock:
            if self._saving:
                self._record(self._player.bus.current)

    # -------------------------------- resuming ----------------------------- #

    def resume(self) -> None:
        """Put back the saved volume, playlist, song and position, paused. Then save again."""
        with self._lock:
            saved = dict(self._data)
        volume = _number(saved.get("volume"))
        if volume is not None and 0 <= volume <= 100:
            self._player.set_volume(volume)
        playlist_id, track_id = saved.get("playlist_id"), saved.get("track_id")
        if isinstance(playlist_id, str) and isinstance(track_id, str):
            try:
                queue, position = self._resume_point(saved, playlist_id, track_id)
                self._player.load_queue(queue, paused=True, position_s=position)
            except (NotInCacheError, CacheError, PlaybackError) as exc:
                print(f"playlist_player: not resuming the last song: {exc}", file=sys.stderr)
        with self._lock:
            self._saving = True

    def _resume_point(
        self, saved: dict[str, Any], playlist_id: str, track_id: str
    ) -> tuple[PlayQueue, float | None]:
        with self._lock:
            order = None
            if saved.get("shuffle") is True:
                order = self._order_for(playlist_id, self._library.playable_ids(playlist_id))
            queue = self._library.queue_for(playlist_id, start_track_id=track_id, order=order)
            self._save()  # the order may have been drawn just now
        position = _number(saved.get("position_s"))
        if position is None or position <= 0:
            return queue, None
        duration = _number(self._library.track(track_id).get("duration_ms"))
        if duration is not None and position >= duration / 1000 - _END_MARGIN_S:
            return queue, None
        return queue, position
