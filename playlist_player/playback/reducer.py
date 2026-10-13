"""Fold mpv's stream of property changes and events into a :class:`PlaybackState`.

Pure: it holds what mpv last said and answers two questions. :meth:`apply` says whether
a message changed anything a client cares about, and :meth:`snapshot` builds the state
for a position the caller has just sampled. No sockets, no clock, so every rule below is
unit-tested by feeding it messages.

Rules:

* A snapshot is published when something discrete changes (status, pause, track,
  speed, volume, duration, error) and when mpv reports ``playback-restart`` (a seek
  finished, or a new file started producing audio). Position ticks alone never publish;
  clients extrapolate between snapshots. A seek during playback therefore shows as
  ``loading`` and then one ``playing`` snapshot at the new position.
* The track comes from ``playlist-pos`` through the queue. mpv's ``path`` is checked
  against it; when they disagree (events from a previous queue still arriving) the path
  wins if the queue knows it, otherwise there is no current track. Between files the
  path is unknown and the position alone decides.
* ``core-idle`` while not paused means buffering or stalled: reported as ``loading`` so
  clients do not advance the clock.
* The queue running out is ``ended``; an explicit stop is ``idle``.
"""

from __future__ import annotations

from typing import Any

from ..models import LyricsKind, PlaybackState, PlayerStatus
from .queue import EMPTY_QUEUE, PlayQueue, QueueEntry

OBSERVED_PROPERTIES = (
    "pause",
    "idle-active",
    "core-idle",
    "playlist-pos",
    "path",
    "duration",
    "speed",
    "volume",
)


class MpvStateReducer:
    def __init__(self) -> None:
        self.queue: PlayQueue = EMPTY_QUEUE
        self._props: dict[str, Any] = {"pause": False, "idle-active": True, "core-idle": True}
        self._loading = False  # between start-file and playback-restart
        self._ended = False  # the queue ran out by itself
        self._error: str | None = None  # the last file-level failure, cleared on the next start
        self._fatal: str | None = None  # the player itself is gone
        self._published: tuple[Any, ...] | None = None

    # ------------------------------- inputs -------------------------------- #

    def set_queue(self, queue: PlayQueue) -> None:
        self.queue = queue
        self._ended = False
        self._error = None

    def replace_queue(self, queue: PlayQueue) -> None:
        """The same tracks in a new order. Nothing about playback itself has changed."""
        self.queue = queue

    def set_fatal(self, message: str | None) -> None:
        """Mark the player as dead (or alive again). Resets what mpv had told us."""
        self._fatal = message
        if message is not None:
            self._props = {"pause": False, "idle-active": True, "core-idle": True}
            self._loading = False
            self._ended = False

    def apply(self, message: dict[str, Any]) -> bool:
        """Take one unsolicited mpv message. True when a snapshot should be published."""
        event = message.get("event")
        force = False
        if event == "property-change":
            self._props[message.get("name", "")] = message.get("data")
        elif event == "start-file":
            self._props.pop("path", None)  # the old file's path says nothing about this one
            self._loading = True
            self._ended = False
            self._error = None
        elif event == "playback-restart":
            self._loading = False
            # A seek finished (or a file started): clients need the new position even
            # though nothing discrete changed. Not while still buffering, though; the
            # change to playing follows at once and carries the position itself.
            force = self.status() is not PlayerStatus.LOADING
        elif event == "end-file":
            self._props.pop("path", None)
            self._loading = False
            reason = message.get("reason")
            if reason == "error":
                self._error = (
                    f"could not play a file: {message.get('file_error') or 'unknown error'}"
                )
            # mpv goes idle after the last file; remember that it got there by playing out.
            self._ended = reason in ("eof", "error")
        return force or self.signature() != self._published

    # ------------------------------- outputs ------------------------------- #

    @property
    def idle(self) -> bool:
        return bool(self._props.get("idle-active", True))

    @property
    def paused(self) -> bool:
        return bool(self._props.get("pause", False))

    def current_index(self) -> int | None:
        if self.idle:
            return None
        position = self._props.get("playlist-pos")
        index = position if isinstance(position, int) and position >= 0 else None
        entry = self.queue.entry_at(index)
        path = self._props.get("path")
        if path is not None and (entry is None or entry.path != path):
            return self.queue.index_of_path(path)
        return index if entry is not None else None

    def current_entry(self) -> QueueEntry | None:
        return self.queue.entry_at(self.current_index())

    def status(self) -> PlayerStatus:
        if self._fatal:
            return PlayerStatus.ERROR
        if self.idle:
            return PlayerStatus.ENDED if self._ended and len(self.queue) else PlayerStatus.IDLE
        if self.paused:
            return PlayerStatus.PAUSED
        if self._loading or self._props.get("core-idle"):
            return PlayerStatus.LOADING
        return PlayerStatus.PLAYING

    def _discrete(self) -> dict[str, Any]:
        """Every snapshot field except the position and its timestamp."""
        index = self.current_index()
        entry = self.queue.entry_at(index)
        duration = self._props.get("duration")
        speed = self._props.get("speed")
        volume = self._props.get("volume")
        known_duration = entry is not None and isinstance(duration, int | float)
        return {
            "status": self.status(),
            # With nothing loaded there is nothing to resume, so report it as paused.
            "paused": self.paused or self.idle,
            "playlist_id": self.queue.playlist_id,
            "track_id": entry.track_id if entry else None,
            "queue_index": index if entry else None,
            "queue_length": len(self.queue),
            # mpv refines a file's duration slightly as it reads; that is not news.
            "duration_s": round(float(duration), 2) if known_duration else None,
            "speed": float(speed) if isinstance(speed, int | float) else 1.0,
            "volume": float(volume) if isinstance(volume, int | float) else 100.0,
            "lyrics_kind": entry.lyrics_kind if entry else LyricsKind.MISSING,
            "offset_ms": entry.offset_ms if entry else 0,
            "error": self._fatal or self._error,
        }

    def signature(self) -> tuple[Any, ...]:
        """What a client would see change; a difference here is worth a snapshot."""
        return tuple(self._discrete().values())

    def snapshot(self, position_s: float | None, captured_at_epoch_ms: int) -> PlaybackState:
        """The state as of ``captured_at_epoch_ms``, when playback was at ``position_s``."""
        fields = self._discrete()
        self._published = tuple(fields.values())
        has_track = fields["track_id"] is not None
        return PlaybackState(
            position_s=max(0.0, float(position_s)) if has_track and position_s is not None else 0.0,
            captured_at_epoch_ms=captured_at_epoch_ms,
            **fields,
        )
