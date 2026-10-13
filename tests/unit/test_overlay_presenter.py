"""What the overlay shows and when: the presenter, the model, placement and style."""

from __future__ import annotations

import pytest

from playlist_player.errors import ConfigError
from playlist_player.lyrics import line_times, parse_lrc
from playlist_player.models import LyricsKind, PlaybackState, PlayerStatus
from playlist_player.overlay.clock import now_epoch_ms, position_ms
from playlist_player.overlay.presenter import (
    HIDDEN,
    SAMPLE_NEXT,
    SAMPLE_TEXT,
    OverlayModel,
    OverlayView,
    present,
    sample_view,
)
from playlist_player.overlay.render.base import Placement, Style

LINES = parse_lrc(
    "[00:10.00] an invented first line\n"
    "[00:20.00] an invented second line\n"
    "[00:30.00]\n"
    "[00:40.00] an invented last line\n"
)
TIMES = line_times(LINES)
T0 = 1_000_000  # the wall-clock instant the snapshot was captured


def playing(position_s: float = 0.0, **overrides) -> PlaybackState:
    fields = {
        "version": 5,
        "status": PlayerStatus.PLAYING,
        "paused": False,
        "track_id": "T1",
        "position_s": position_s,
        "duration_s": 60.0,
        "captured_at_epoch_ms": T0,
        "lyrics_kind": LyricsKind.SYNCED,
    }
    fields.update(overrides)
    return PlaybackState(**fields)


def view(state, *, now_ms=T0, connected=True, track="T1", lines=LINES, **kwargs) -> OverlayView:
    return present(
        connected=connected,
        state=state,
        lyrics_track_id=track,
        lines=lines,
        times=line_times(lines),
        now_ms=now_ms,
        **kwargs,
    )


# -------------------------------- presenter -------------------------------- #


def test_shows_the_current_line_while_playing():
    assert view(playing(12.0)) == OverlayView(True, "an invented first line", "", ticking=True)
    assert view(playing(25.0)).text == "an invented second line"


def test_the_position_is_extrapolated_from_the_snapshot():
    # Captured at 12 s; nine seconds of wall clock later it is at 21 s.
    assert view(playing(12.0), now_ms=T0 + 9_000).text == "an invented second line"
    assert view(playing(12.0, speed=2.0), now_ms=T0 + 5_000).text == "an invented second line"


def test_before_the_first_line_is_hidden_but_still_ticking():
    assert view(playing(3.0)) == OverlayView(False, ticking=True)


def test_a_gap_line_is_hidden_but_still_ticking():
    assert view(playing(33.0)) == OverlayView(False, ticking=True)


def test_the_last_line_stays_until_the_track_ends():
    assert view(playing(40.0)).text == "an invented last line"
    assert view(playing(59.9)).text == "an invented last line"
    assert view(playing(12.0), now_ms=T0 + 600_000).text == "an invented last line"  # clamped


@pytest.mark.parametrize(
    "state",
    [
        playing(12.0, status=PlayerStatus.PAUSED, paused=True),
        playing(12.0, paused=True),  # contradictory, but paused wins
        playing(12.0, status=PlayerStatus.LOADING),
        playing(12.0, status=PlayerStatus.IDLE, track_id=None),
        playing(12.0, status=PlayerStatus.ENDED),
        playing(12.0, status=PlayerStatus.ERROR),
        playing(12.0, lyrics_kind=LyricsKind.PLAIN),
        playing(12.0, lyrics_kind=LyricsKind.INSTRUMENTAL),
        playing(12.0, lyrics_kind=LyricsKind.MISSING),
        playing(12.0, track_id=None),
        None,
    ],
)
def test_anything_but_playing_with_synced_lyrics_is_hidden_and_stops_the_timer(state):
    assert view(state) == HIDDEN and not HIDDEN.ticking


def test_disconnected_is_hidden_whatever_the_last_state_said():
    assert view(playing(12.0), connected=False) == HIDDEN


def test_lines_for_another_track_or_none_yet_are_hidden():
    assert view(playing(12.0), track="T0") == HIDDEN
    assert view(playing(12.0), track=None) == HIDDEN
    assert view(playing(12.0), lines=()) == HIDDEN


def test_lead_shows_a_line_early_and_offset_shifts_it():
    assert view(playing(9.9)).visible is False
    assert view(playing(9.9), lead_ms=150).text == "an invented first line"
    assert view(playing(9.5, offset_ms=600)).text == "an invented first line"  # lyrics sooner
    assert view(playing(10.2, offset_ms=-600)).visible is False  # lyrics later


def test_the_next_line_is_offered_only_when_asked_for():
    assert view(playing(12.0)).next_text == ""
    assert view(playing(12.0), show_next=True).next_text == "an invented second line"
    assert view(playing(25.0), show_next=True).next_text == ""  # next is a gap
    assert view(playing(45.0), show_next=True).next_text == ""  # nothing follows


def test_a_preview_shows_the_sample_line_whatever_is_playing_until_it_ends():
    sample = OverlayView(True, SAMPLE_TEXT, "", ticking=True)
    assert view(None, preview_until_ms=T0 + 5000) == sample  # nothing loaded at all
    assert view(playing(12.0, paused=True), preview_until_ms=T0 + 5000) == sample
    assert view(playing(12.0), preview_until_ms=T0 + 5000) == sample  # over a real line
    assert view(None, preview_until_ms=T0 + 5000, show_next=True).next_text == SAMPLE_NEXT
    # It ends on time, by the clock alone, and what was true underneath shows again.
    assert view(None, now_ms=T0 + 5000, preview_until_ms=T0 + 5000) == HIDDEN
    underneath = view(playing(12.0), now_ms=T0 + 5000)
    assert underneath.visible and underneath.text != SAMPLE_TEXT
    assert view(playing(12.0), now_ms=T0 + 5000, preview_until_ms=T0 + 5000) == underneath
    # Without the connection nothing is known, a preview included.
    assert view(None, connected=False, preview_until_ms=T0 + 5000) == HIDDEN


def test_the_demo_view_is_the_same_sample_and_does_not_tick():
    assert sample_view() == OverlayView(True, SAMPLE_TEXT, "", ticking=False)
    assert sample_view(show_next=True).next_text == SAMPLE_NEXT


def test_clock_units():
    assert position_ms(playing(12.5), T0) == 12_500
    assert position_ms(playing(12.5), T0 + 250) == 12_750
    assert abs(now_epoch_ms() - __import__("time").time() * 1000) < 1000


# ---------------------------------- model ---------------------------------- #


def test_model_is_hidden_until_connected_with_state_and_lyrics():
    model = OverlayModel(lead_ms=0)
    assert model.view(T0) == HIDDEN and model.lyrics_wanted() is None
    model.set_connected(True)
    model.set_state(playing(12.0))
    assert model.view(T0) == HIDDEN  # no lines yet
    assert model.lyrics_wanted() == "T1"
    model.set_lyrics("T1", LINES)
    assert model.lyrics_wanted() is None
    assert model.view(T0).text == "an invented first line"
    assert model.view(T0 + 9_000).text == "an invented second line"


def test_model_asks_for_lines_once_per_track_and_only_for_synced_tracks():
    model = OverlayModel()
    model.set_connected(True)
    model.set_state(playing(0.0, track_id="A"))
    model.set_lyrics("A", LINES)
    model.set_state(playing(5.0, track_id="A", version=6))
    assert model.lyrics_wanted() is None
    model.set_state(playing(0.0, track_id="B", version=7))
    assert model.lyrics_wanted() == "B"
    assert model.view(T0 + 12_000) == HIDDEN  # still holding A's lines
    model.set_state(playing(0.0, track_id="C", lyrics_kind=LyricsKind.PLAIN, version=8))
    assert model.lyrics_wanted() is None
    model.set_state(playing(0.0, status=PlayerStatus.IDLE, track_id=None, version=9))
    assert model.lyrics_wanted() is None


def test_an_empty_lyrics_answer_is_remembered_so_it_is_not_fetched_in_a_loop():
    model = OverlayModel()
    model.set_connected(True)
    model.set_state(playing(12.0))
    model.set_lyrics("T1", ())
    assert model.lyrics_wanted() is None and model.view(T0) == HIDDEN


def test_disconnecting_forgets_the_state():
    model = OverlayModel()
    model.set_connected(True)
    model.set_state(playing(12.0))
    model.set_lyrics("T1", LINES)
    model.set_connected(False)
    assert model.view(T0) == HIDDEN
    model.set_connected(True)  # reconnected, but no fresh snapshot yet
    assert model.view(T0) == HIDDEN
    model.set_state(playing(12.0, version=1))
    assert model.view(T0).visible


def test_the_model_can_be_reconfigured_while_it_runs():
    model = OverlayModel(lead_ms=0, show_next=False)
    model.set_connected(True)
    model.set_state(playing(9.9))
    model.set_lyrics("T1", LINES)
    before = model.view(T0)
    model.configure(lead_ms=200, show_next=True)
    after = model.view(T0)
    assert (before.text, before.next_text) != (after.text, after.next_text)
    assert after.next_text  # the following line is offered now


def test_a_preview_ends_with_the_connection():
    model = OverlayModel()
    model.set_connected(True)
    model.set_preview_until(T0 + 5000)
    assert model.view(T0).text == SAMPLE_TEXT
    model.set_connected(False)
    model.set_connected(True)  # back, but the daemon has not asked for a preview again
    assert model.view(T0) == HIDDEN


def test_model_passes_lead_and_next_line_settings():
    model = OverlayModel(lead_ms=150, show_next=True)
    model.set_connected(True)
    model.set_state(playing(9.9))
    model.set_lyrics("T1", LINES)
    shown = model.view(T0)
    assert (shown.text, shown.next_text) == ("an invented first line", "an invented second line")


# ---------------------------- placement and style ---------------------------- #


@pytest.mark.parametrize(
    ("anchor", "edges", "margins", "shift"),
    [
        ("bottom", {"bottom"}, {"bottom": 120}, (40, 0)),
        ("top", {"top"}, {"top": 120}, (40, 0)),
        ("left", {"left"}, {"left": 40}, (0, 120)),
        ("right", {"right"}, {"right": 40}, (0, 120)),
        ("top-left", {"top", "left"}, {"top": 120, "left": 40}, (0, 0)),
        ("top-right", {"top", "right"}, {"top": 120, "right": 40}, (0, 0)),
        ("bottom-left", {"bottom", "left"}, {"bottom": 120, "left": 40}, (0, 0)),
        ("bottom-right", {"bottom", "right"}, {"bottom": 120, "right": 40}, (0, 0)),
        ("center", set(), {}, (40, 120)),
    ],
)
def test_placement_for_every_anchor(anchor, edges, margins, shift):
    placement = Placement.from_settings(anchor, 40, 120)
    assert placement.edges == edges
    assert {edge: px for edge, px in placement.margins.items() if px} == margins
    assert (placement.shift_x, placement.shift_y) == shift
    assert set(placement.margins) == {"top", "bottom", "left", "right"}


def test_placement_rejects_an_unknown_anchor():
    with pytest.raises(ConfigError, match="OVERLAY_ANCHOR"):
        Placement.from_settings("middle", 0, 0)


def style(**overrides) -> Style:
    fields = {"font": "Sans Bold", "font_size": 32, "color": "#FFFFFF",
              "outline_color": "#000000", "max_width": 1200, "fade_ms": 120}  # fmt: skip
    fields.update(overrides)
    return Style(**fields)


@pytest.mark.parametrize("colour", ["#fff", "#FFE066", "#11223344", "white", "Black"])
def test_style_accepts_plain_colours(colour):
    assert style(color=colour, outline_color=colour).color == colour


@pytest.mark.parametrize(
    "colour",
    ["", "#12", "#GGGGGG", "red; } * { color: blue", "rgb(1,2,3)", "url(x)", "#fff\n}", "a b"],
)
def test_style_refuses_anything_that_could_break_out_of_the_stylesheet(colour):
    with pytest.raises(ConfigError, match="OVERLAY_COLOR"):
        style(color=colour)
    with pytest.raises(ConfigError, match="OVERLAY_OUTLINE_COLOR"):
        style(outline_color=colour)


def test_style_refuses_nonsense_sizes():
    with pytest.raises(ConfigError):
        style(font_size=0)
    with pytest.raises(ConfigError):
        style(max_width=0)
