"""The play queue: which track sits at which position, and in what order.

mpv's own internal playlist holds the queue, so desktop media keys (through the MPRIS
plugin) move through it with no help from us. This module is the daemon's record of
what it loaded: it maps mpv's ``playlist-pos`` back to a track ID. Pure data; no I/O.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from dataclasses import dataclass, replace

from ..models import LyricsKind


@dataclass(frozen=True, slots=True)
class QueueEntry:
    """One playable track: the file mpv opens, and what clients need to know about it."""

    track_id: str
    path: str
    lyrics_kind: LyricsKind = LyricsKind.MISSING
    offset_ms: int = 0


class PlayQueue:
    """An ordered list of entries plus where playback should start."""

    def __init__(
        self,
        entries: Sequence[QueueEntry],
        *,
        playlist_id: str | None = None,
        start_track_id: str | None = None,
        shuffle: bool = False,
        rng: random.Random | None = None,
    ) -> None:
        ordered = list(entries)
        if shuffle:
            (rng or random.Random()).shuffle(ordered)
            # A shuffled queue still starts with the track that was asked for.
            first = next((e for e in ordered if e.track_id == start_track_id), None)
            if first is not None:
                ordered.remove(first)
                ordered.insert(0, first)
        self.playlist_id = playlist_id
        self._entries = ordered
        self.start_index = 0
        if start_track_id is not None and not shuffle:
            self.start_index = self.index_of(start_track_id) or 0

    def __len__(self) -> int:
        return len(self._entries)

    @property
    def entries(self) -> tuple[QueueEntry, ...]:
        return tuple(self._entries)

    def entry_at(self, index: int | None) -> QueueEntry | None:
        if index is None or not 0 <= index < len(self._entries):
            return None
        return self._entries[index]

    def index_of(self, track_id: str) -> int | None:
        """Position of the first entry for ``track_id``, or ``None``."""
        for index, entry in enumerate(self._entries):
            if entry.track_id == track_id:
                return index
        return None

    def index_of_path(self, path: str | None) -> int | None:
        for index, entry in enumerate(self._entries):
            if entry.path == path:
                return index
        return None

    def set_offset(self, track_id: str, offset_ms: int) -> bool:
        """Update the offset on every entry for ``track_id``. Returns whether any matched."""
        changed = False
        for index, entry in enumerate(self._entries):
            if entry.track_id == track_id:
                self._entries[index] = replace(entry, offset_ms=offset_ms)
                changed = True
        return changed


EMPTY_QUEUE = PlayQueue(())
