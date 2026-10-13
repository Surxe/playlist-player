"""What the overlay shows, decided in one place.

:func:`present` is pure: connection, state, lyrics and a time go in, an
:class:`OverlayView` comes out. The renderer draws whatever the view says and decides
nothing itself.

The overlay is visible only when ALL of these hold:

* the event stream is connected (a stale state must not keep a line on screen);
* the daemon reports ``playing`` (so it vanishes on pause, stop, buffering and at the end);
* the track has synced lyrics, and the lines held are for that track;
* the current line exists and has text (nothing before the first line or during a gap).

In every other case the view is hidden, and the renderer unmaps the surface rather than
drawing something transparent, so the compositor can go back to presenting the
fullscreen application directly.
"""

from __future__ import annotations

import threading
from collections.abc import Sequence
from dataclasses import dataclass

from ..lyrics import current_index, line_times
from ..models import LyricLine, LyricsKind, PlaybackState, PlayerStatus
from .clock import position_ms


@dataclass(frozen=True, slots=True)
class OverlayView:
    visible: bool
    text: str = ""
    next_text: str = ""
    # Whether the answer can change with time alone (playing, with lyrics in hand). The
    # renderer only keeps a timer running while this is true.
    ticking: bool = False


HIDDEN = OverlayView(visible=False)


def present(
    *,
    connected: bool,
    state: PlaybackState | None,
    lyrics_track_id: str | None,
    lines: Sequence[LyricLine],
    times: Sequence[int],
    now_ms: int,
    lead_ms: int = 0,
    show_next: bool = False,
) -> OverlayView:
    """The view for this instant. ``times`` are the timestamps of ``lines``."""
    if not connected or state is None:
        return HIDDEN
    if state.status is not PlayerStatus.PLAYING or state.paused:
        return HIDDEN
    if state.lyrics_kind is not LyricsKind.SYNCED or state.track_id is None:
        return HIDDEN
    if lyrics_track_id != state.track_id or not lines:
        return HIDDEN  # lines not fetched yet, or still those of the previous track

    index = current_index(
        times, position_ms(state, now_ms), lead_ms=lead_ms, offset_ms=state.offset_ms
    )
    text = lines[index].text if index >= 0 else ""
    if not text:
        return OverlayView(visible=False, ticking=True)
    following = lines[index + 1].text if show_next and index + 1 < len(lines) else ""
    return OverlayView(visible=True, text=text, next_text=following, ticking=True)


class OverlayModel:
    """The overlay's whole knowledge of the world, shared between its two threads.

    The client thread writes (connection, state, lyric lines); the renderer's thread
    reads a view. A lock keeps each read consistent.
    """

    def __init__(self, *, lead_ms: int = 0, show_next: bool = False) -> None:
        self._lead_ms = lead_ms
        self._show_next = show_next
        self._lock = threading.Lock()
        self._connected = False
        self._state: PlaybackState | None = None
        self._lyrics_track_id: str | None = None
        self._lines: tuple[LyricLine, ...] = ()
        self._times: list[int] = []

    def set_connected(self, connected: bool) -> None:
        with self._lock:
            self._connected = connected
            if not connected:
                self._state = None  # whatever we knew is no longer known to be true

    def set_state(self, state: PlaybackState) -> None:
        with self._lock:
            self._state = state

    def set_lyrics(self, track_id: str, lines: Sequence[LyricLine]) -> None:
        with self._lock:
            self._lyrics_track_id = track_id
            self._lines = tuple(lines)
            self._times = line_times(self._lines)

    def lyrics_wanted(self) -> str | None:
        """The track whose lines should be fetched now, or ``None`` when none are needed."""
        with self._lock:
            state = self._state
            if state is None or state.track_id is None:
                return None
            if state.lyrics_kind is not LyricsKind.SYNCED:
                return None
            return None if self._lyrics_track_id == state.track_id else state.track_id

    def view(self, now_ms: int) -> OverlayView:
        with self._lock:
            return present(
                connected=self._connected,
                state=self._state,
                lyrics_track_id=self._lyrics_track_id,
                lines=self._lines,
                times=self._times,
                now_ms=now_ms,
                lead_ms=self._lead_ms,
                show_next=self._show_next,
            )
