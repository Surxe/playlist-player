"""What the overlay shows, decided in one place.

:func:`present` is pure: connection, state, lyrics and a time go in, an
:class:`OverlayView` comes out. The renderer draws whatever the view says and decides
nothing itself.

The overlay is visible only when ALL of these hold:

* the event stream is connected (a stale state must not keep a line on screen);
* the daemon reports ``playing`` (so it vanishes on pause, stop, buffering and at the end);
* the track has synced lyrics, and the lines held are for that track;
* the current line exists and has text (nothing before the first line or during a gap).

The one exception is a *preview*: while the daemon asks for one, a sample line is shown
whatever is or is not playing, so the overlay can be positioned from the remote-control
page. A preview still needs the connection, and ends by itself at the instant the daemon
named.

In every other case the view is hidden, and the renderer unmaps the surface rather than
drawing something transparent, so the compositor can go back to presenting the
fullscreen application directly.
"""

from __future__ import annotations

import threading
from collections.abc import Sequence
from dataclasses import dataclass

from ..lyrics import current_index, current_word, line_times, word_ends, word_ranges
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
    # The word of ``text`` to underline, as character offsets (start, end); None for none.
    underline: tuple[int, int] | None = None


HIDDEN = OverlayView(visible=False)

SAMPLE_TEXT = "This is where the current lyric line will appear"
SAMPLE_NEXT = "and this is the line that follows it"


_SAMPLE_WORD = (SAMPLE_TEXT.index("current"), SAMPLE_TEXT.index("current") + len("current"))


def sample_view(
    *, show_next: bool = False, ticking: bool = False, underline_word: bool = False
) -> OverlayView:
    """The stand-in line shown by ``--demo`` and by a preview."""
    return OverlayView(
        True,
        SAMPLE_TEXT,
        SAMPLE_NEXT if show_next else "",
        ticking,
        _SAMPLE_WORD if underline_word else None,
    )


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
    preview_until_ms: int = 0,
    ends: Sequence[Sequence[float]] | None = None,
) -> OverlayView:
    """The view for this instant. ``times`` are the timestamps of ``lines``.

    ``ends`` are the lines' word endings (:func:`~playlist_player.lyrics.word_ends`);
    given, the word being sung is underlined, and left out, no word is.
    """
    if not connected:
        return HIDDEN
    if now_ms < preview_until_ms:
        # ticking: it ends on time
        return sample_view(show_next=show_next, ticking=True, underline_word=ends is not None)
    if state is None:
        return HIDDEN
    if state.status is not PlayerStatus.PLAYING or state.paused:
        return HIDDEN
    if state.lyrics_kind is not LyricsKind.SYNCED or state.track_id is None:
        return HIDDEN
    if lyrics_track_id != state.track_id or not lines:
        return HIDDEN  # lines not fetched yet, or still those of the previous track

    position = position_ms(state, now_ms)
    index = current_index(times, position, lead_ms=lead_ms, offset_ms=state.offset_ms)
    text = lines[index].text if index >= 0 else ""
    if not text:
        return OverlayView(visible=False, ticking=True)
    following = lines[index + 1].text if show_next and index + 1 < len(lines) else ""
    underline = None
    if ends is not None:
        # The same shifted clock that chose the line, counted from the line's timestamp.
        elapsed = position + lead_ms + state.offset_ms - times[index]
        word = current_word(ends[index], elapsed)
        if word >= 0:
            underline = word_ranges(text)[word]
    return OverlayView(True, text, following, ticking=True, underline=underline)


class OverlayModel:
    """The overlay's whole knowledge of the world, shared between its two threads.

    The client thread writes (connection, state, lyric lines); the renderer's thread
    reads a view. A lock keeps each read consistent.
    """

    def __init__(
        self, *, lead_ms: int = 0, show_next: bool = False, underline_word: bool = False
    ) -> None:
        self._lead_ms = lead_ms
        self._show_next = show_next
        self._underline_word = underline_word
        self._lock = threading.Lock()
        self._connected = False
        self._state: PlaybackState | None = None
        self._lyrics_track_id: str | None = None
        self._lines: tuple[LyricLine, ...] = ()
        self._times: list[int] = []
        self._ends: list[tuple[float, ...]] = []
        self._preview_until_ms = 0

    def configure(self, *, lead_ms: int, show_next: bool, underline_word: bool = False) -> None:
        """Change how lines are timed, and whether the next line and the word are shown."""
        with self._lock:
            self._lead_ms = lead_ms
            self._show_next = show_next
            self._underline_word = underline_word

    def set_preview_until(self, epoch_ms: int) -> None:
        """Show the sample line until ``epoch_ms``; 0 (or any past instant) ends it."""
        with self._lock:
            self._preview_until_ms = epoch_ms

    def set_connected(self, connected: bool) -> None:
        with self._lock:
            self._connected = connected
            if not connected:
                self._state = None  # whatever we knew is no longer known to be true
                self._preview_until_ms = 0

    def set_state(self, state: PlaybackState) -> None:
        with self._lock:
            self._state = state

    def set_lyrics(self, track_id: str, lines: Sequence[LyricLine]) -> None:
        with self._lock:
            self._lyrics_track_id = track_id
            self._lines = tuple(lines)
            self._times = line_times(self._lines)
            self._ends = word_ends(self._lines)

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
                preview_until_ms=self._preview_until_ms,
                ends=self._ends if self._underline_word else None,
            )
