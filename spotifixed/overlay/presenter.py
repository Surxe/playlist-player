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

The volume indicator rides on top of whatever the line logic decided. For
:data:`VOLUME_SHOWN_MS` after the volume changes, the view is visible and carries the
volume, paused or not, with or without a line. The first state after connecting sets
the baseline and shows nothing, so a restart does not flash it.

The fade-in on resume shows too, as the level rising to the volume and then held for
:data:`VOLUME_SHOWN_MS`. The daemon publishes no level while it fades; the snapshot that
starts playback says where the fade began and how long it is, and the level is worked
out here from the playback position on the overlay's own timer. mpv's volume is cubic
(the gain is the volume over 100, cubed) while the fade raises the gain evenly, so the
level shown, on the volume slider's scale, is the volume times the cube root of the
fade's progress.

In every other case the view is hidden, and the renderer unmaps the surface rather than
drawing something transparent, so the compositor can go back to presenting the
fullscreen application directly.
"""

from __future__ import annotations

import threading
from collections.abc import Sequence
from dataclasses import dataclass, replace

from ..lyrics import (
    aligned_word,
    current_index,
    current_word,
    line_times,
    word_ends,
    word_ranges,
)
from ..models import LyricLine, LyricsKind, PlaybackState, PlayerStatus
from .clock import now_epoch_ms, position_ms

# How long the volume stays on screen after it last changed.
VOLUME_SHOWN_MS = 1500


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
    # The volume to show (0-100) while the indicator is up; None for no indicator.
    volume: int | None = None


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
    volume_until_ms: int = 0,
    show_fade: bool = False,
) -> OverlayView:
    """The view for this instant. ``times`` are the timestamps of ``lines``.

    ``ends`` are the lines' word endings (:func:`~spotifixed.lyrics.word_ends`);
    given, the word being sung is underlined, and left out, no word is. A line that
    carries aligned word times uses those; any other line uses the estimate in ``ends``.
    Until ``volume_until_ms`` the state's volume is shown as well, and with
    ``show_fade`` so is the level of a fade-in under way.
    """
    if not connected:
        return HIDDEN
    view = _line_view(
        state=state,
        lyrics_track_id=lyrics_track_id,
        lines=lines,
        times=times,
        now_ms=now_ms,
        lead_ms=lead_ms,
        show_next=show_next,
        preview_until_ms=preview_until_ms,
        ends=ends,
    )
    if state is None:
        return view
    level = fade_level(state, now_ms) if show_fade else None
    if level is None and now_ms < volume_until_ms:
        level = state.volume
    if level is None:
        return view
    # ticking: the level rises, and the indicator ends, on time
    return replace(view, visible=True, ticking=True, volume=round(level))


def fade_level(state: PlaybackState, now_ms: int) -> float | None:
    """The level a fade-in has reached, on the volume's scale; None when none is shown.

    Shown while the fade rises and for :data:`VOLUME_SHOWN_MS` after it ends, counted
    in playback time, which is what the fade itself counts.
    """
    if state.fade_in_s <= 0 or state.status is not PlayerStatus.PLAYING or state.paused:
        return None
    played = state.position_at(now_ms) - state.fade_from_s
    if played < 0 or played >= state.fade_in_s + VOLUME_SHOWN_MS / 1000:
        return None
    share = min(1.0, played / state.fade_in_s)
    return state.volume * share ** (1 / 3)


def _line_view(
    *,
    state: PlaybackState | None,
    lyrics_track_id: str | None,
    lines: Sequence[LyricLine],
    times: Sequence[int],
    now_ms: int,
    lead_ms: int,
    show_next: bool,
    preview_until_ms: int,
    ends: Sequence[Sequence[float]] | None,
) -> OverlayView:
    """The lyric line part of :func:`present`, for a connected overlay."""
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
        aligned = lines[index].words
        if aligned is not None:
            # Heard in the audio itself: its own time, which no offset shifts.
            word = aligned_word(aligned, position)
        else:
            # The same shifted clock that chose the line, counted from its timestamp.
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
        self,
        *,
        lead_ms: int = 0,
        show_next: bool = False,
        underline_word: bool = False,
        volume_indicator: bool = False,
    ) -> None:
        self._lead_ms = lead_ms
        self._show_next = show_next
        self._underline_word = underline_word
        self._volume_indicator = volume_indicator
        self._volume_until_ms = 0
        self._lock = threading.Lock()
        self._connected = False
        self._state: PlaybackState | None = None
        self._lyrics_track_id: str | None = None
        self._lines: tuple[LyricLine, ...] = ()
        self._times: list[int] = []
        self._ends: list[tuple[float, ...]] = []
        self._preview_until_ms = 0

    def configure(
        self,
        *,
        lead_ms: int,
        show_next: bool,
        underline_word: bool = False,
        volume_indicator: bool = False,
    ) -> None:
        """Change how lines are timed, and whether the next line, the word and the volume
        are shown."""
        with self._lock:
            self._lead_ms = lead_ms
            self._show_next = show_next
            self._underline_word = underline_word
            self._volume_indicator = volume_indicator
            if not volume_indicator:
                self._volume_until_ms = 0

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
                self._volume_until_ms = 0

    def set_state(self, state: PlaybackState, now_ms: int | None = None) -> None:
        """Take the latest state. A volume different from the last one (not the first one
        after connecting) puts the indicator up, or keeps it up, from ``now_ms``."""
        with self._lock:
            previous, self._state = self._state, state
            if self._volume_indicator and previous is not None and state.volume != previous.volume:
                self._volume_until_ms = (now_epoch_ms() if now_ms is None else now_ms) + (
                    VOLUME_SHOWN_MS
                )

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
                volume_until_ms=self._volume_until_ms,
                show_fade=self._volume_indicator,
            )
