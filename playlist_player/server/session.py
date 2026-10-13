"""What the daemon remembers between runs: track orders, the song, its position, the volume.

One small file in the state directory (``session.json``), rewritten whenever what it
holds changes: on every playback snapshot, and once more as the daemon stops.

Order. Shuffling a playlist draws a new random order of its playable tracks and keeps it
as that playlist's order: the track list shows it, the queue plays it, and it is the same
after a restart, until the playlist is shuffled again or put back in its own order. If
the playlist is the one playing, the queue is rearranged where it stands: the song that
is on carries on, and is placed first so that everything else is still to come. Tracks
that a later sync adds go to the end of a kept order; tracks it removes drop out.

Resume. On start the saved volume is put back, then the saved playlist is loaded in its
kept order at the saved track and position. It is loaded paused: audio does not begin on
its own at boot. A song whose saved position is at its very end starts again from the
beginning.

Nothing is written before the daemon has tried to resume. Until then the player is only
reporting what it started with, and that must not overwrite what was saved.
"""

from __future__ import annotations

import random
import sys
import threading
import time
from collections import Counter
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
# A shuffle that happens to repeat the order in force is drawn again, this many times.
_REDRAWS = 8


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
        self._lock = threading.Lock()  # guards _data and _saving
        self._ordering = threading.Lock()  # one change of order at a time, player included
        self._data: dict[str, Any] = self._read()
        self._saving = False
        player.bus.subscribe(self._on_state)

    # ------------------------------- storage ------------------------------- #

    def _read(self) -> dict[str, Any]:
        stored: Any = {}
        try:
            stored = self._store.read_json(self._store.session_json, {})
        except (CacheError, OSError) as exc:
            print(f"playlist_player: {exc}; starting without the saved session", file=sys.stderr)
        if not isinstance(stored, dict):
            print(
                "playlist_player: the saved session is not an object; ignoring it",
                file=sys.stderr,
            )
            stored = {}
        orders = stored.get("orders")
        stored["orders"] = orders if isinstance(orders, dict) else {}
        for retired in ("shuffle", "order", "order_playlist_id"):
            stored.pop(retired, None)  # written by a version that had shuffle as a switch
        return stored

    def _save(self) -> None:
        """Write what is held. A failure is reported, never raised: playback goes on."""
        try:
            self._store.write_json(self._store.session_json, self._data)
        except (CacheError, OSError) as exc:
            print(f"playlist_player: could not save the session: {exc}", file=sys.stderr)

    def document(self) -> dict[str, Any]:
        """What the page reads of the session."""
        with self._lock:
            return {
                "playlist_id": self._data.get("playlist_id"),
                "track_id": self._data.get("track_id"),
                "position_s": _number(self._data.get("position_s")) or 0.0,
                "volume": _number(self._data.get("volume")),
            }

    # --------------------------------- order -------------------------------- #

    def _kept_order(self, playlist_id: str, playable: list[str]) -> list[str] | None:
        """The playlist's kept order, brought up to date with ``playable``. Lock held.

        ``None`` when the playlist has never been shuffled. A kept order that no longer
        matches the tracks (a sync added or removed some) is repaired, not thrown away:
        what is still there keeps its place and anything new goes to the end.
        """
        kept = self._data["orders"].get(playlist_id)
        if not isinstance(kept, list):
            return None
        remaining = Counter(playable)
        order = []
        for track_id in kept:
            if remaining.get(track_id, 0) > 0:
                order.append(track_id)
                remaining[track_id] -= 1
        for track_id in playable:
            if remaining[track_id] > 0:
                order.append(track_id)
                remaining[track_id] -= 1
        if order != kept:
            self._data["orders"][playlist_id] = order
        return order

    def _apply(self, playlist_id: str, order: list[str] | None) -> None:
        """Rearrange the player's queue if it holds this playlist. Ordering lock held."""
        state = self._player.bus.current
        if state.playlist_id != playlist_id or not state.queue_length:
            return
        queue = self._library.queue_for(playlist_id, start_track_id=state.track_id, order=order)
        self._player.reorder(queue)

    def shuffle(self, playlist_id: str) -> list[str]:
        """Draw a new random order for a playlist, keep it, and play by it from now on."""
        with self._ordering:
            with self._lock:
                playable = self._library.playable_ids(playlist_id)
                before = self._kept_order(playlist_id, playable) or playable
                state = self._player.bus.current
                loaded_here = state.playlist_id == playlist_id and state.queue_length
                current = state.track_id if loaded_here else None
                order = list(playable)
                for _ in range(_REDRAWS):
                    self._rng.shuffle(order)
                    if current in order:
                        # What is playing goes first: the rest is all still to come.
                        order.remove(current)
                        order.insert(0, current)
                    if order != before:
                        break
                self._data["orders"][playlist_id] = order
                self._save()
            self._apply(playlist_id, order)
        return order

    def restore(self, playlist_id: str) -> None:
        """Forget a playlist's kept order: back to the order the playlist itself has."""
        with self._ordering:
            with self._lock:
                self._library.playable_ids(playlist_id)  # refuses a playlist that is not cached
                self._data["orders"].pop(playlist_id, None)
                self._save()
            self._apply(playlist_id, None)

    def arranged(self, playlist: dict[str, Any]) -> dict[str, Any]:
        """A playlist as the API describes it, with its tracks in the kept order.

        Tracks that cannot be played have no place in an order; they follow the rest.
        """
        tracks = playlist["tracks"]
        playable = [track["id"] for track in tracks if track["has_audio"]]
        with self._lock:
            order = self._kept_order(playlist["id"], playable)
        if order is None:
            return {**playlist, "shuffled": False}
        by_id = {track["id"]: track for track in tracks}
        unplayable = [track for track in tracks if not track["has_audio"]]
        return {
            **playlist,
            "shuffled": True,
            "tracks": [by_id[track_id] for track_id in order] + unplayable,
        }

    # -------------------------------- loading ------------------------------ #

    def queue_for(self, playlist_id: str, *, start_track_id: str | None = None) -> PlayQueue:
        """The queue for a request to play a playlist, in its kept order if it has one."""
        with self._lock:
            order = self._kept_order(playlist_id, self._library.playable_ids(playlist_id))
            queue = self._library.queue_for(playlist_id, start_track_id=start_track_id, order=order)
            self._data["playlist_id"] = playlist_id
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
                queue = self.queue_for(playlist_id, start_track_id=track_id)
                position = self._resume_position(saved, track_id)
                self._player.load_queue(queue, paused=True, position_s=position)
            except (NotInCacheError, CacheError, PlaybackError) as exc:
                print(f"playlist_player: not resuming the last song: {exc}", file=sys.stderr)
        with self._lock:
            self._saving = True

    def _resume_position(self, saved: dict[str, Any], track_id: str) -> float | None:
        position = _number(saved.get("position_s"))
        if position is None or position <= 0:
            return None
        duration_ms = _number(self._library.track(track_id).get("duration_ms"))
        if duration_ms is not None and position >= duration_ms / 1000 - _END_MARGIN_S:
            return None
        return position
