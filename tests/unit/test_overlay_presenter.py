"""What the overlay shows and when: the presenter, the model, placement and style."""

from __future__ import annotations

import pytest

from spotifixed.errors import ConfigError
from spotifixed.lyrics import line_times, parse_lrc, word_ends
from spotifixed.models import LyricsKind, PlaybackState, PlayerStatus
from spotifixed.overlay.clock import now_epoch_ms, position_ms
from spotifixed.overlay.presenter import (
    HIDDEN,
    SAMPLE_NEXT,
    SAMPLE_TEXT,
    VOLUME_SHOWN_MS,
    OverlayModel,
    OverlayView,
    fade_level,
    present,
    sample_view,
)
from spotifixed.overlay.render.base import Placement, Style

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


# ------------------------------ the current word ---------------------------- #

# Three lines sung at a syllable a second, then a line left hanging for 26 s.
WORD_LINES = parse_lrc(
    "[00:10.00] one two\n[00:12.00] three four\n[00:14.00] five six\n"
    "[00:16.00] seven eight\n[00:44.00]\n"
)


def word_view(position_s: float, **kwargs) -> OverlayView:
    kwargs.setdefault("ends", word_ends(WORD_LINES))
    return view(playing(position_s), lines=WORD_LINES, **kwargs)


def underlined(shown: OverlayView) -> str | None:
    return None if shown.underline is None else shown.text[slice(*shown.underline)]


def test_no_word_is_underlined_unless_asked_for():
    assert word_view(10.5, ends=None).underline is None


def test_the_word_being_sung_is_underlined():
    assert underlined(word_view(10.5)) == "one"
    assert underlined(word_view(11.5)) == "two"
    assert underlined(word_view(12.0)) == "three"


def test_the_underline_follows_the_lead_and_the_offset_like_the_line_does():
    assert underlined(word_view(10.9, lead_ms=150)) == "two"
    shifted = view(playing(10.5, offset_ms=600), lines=WORD_LINES, ends=word_ends(WORD_LINES))
    assert underlined(shifted) == "two"


def test_no_word_is_underlined_while_a_line_trails_off():
    assert underlined(word_view(19.0)) == "eight"
    trailing = word_view(30.0)
    assert trailing.text == "seven eight" and trailing.underline is None


def test_the_sample_line_shows_the_underline_when_it_is_on():
    assert sample_view().underline is None
    assert underlined(sample_view(underline_word=True)) == "current"
    previewed = view(None, preview_until_ms=T0 + 5000, ends=())
    assert underlined(previewed) == "current"


def test_the_model_underlines_only_when_configured_to():
    model = OverlayModel()
    model.set_connected(True)
    model.set_state(playing(10.5))
    model.set_lyrics("T1", WORD_LINES)
    assert model.view(T0).underline is None
    model.configure(lead_ms=0, show_next=False, underline_word=True)
    assert underlined(model.view(T0)) == "one"


def test_a_line_aligned_to_the_audio_uses_its_own_word_times():
    """Heard times replace the estimate, and neither the lead nor the offset moves them."""
    first = WORD_LINES[0]
    heard = (first.__class__(first.t_ms, first.text, ((10_300, 11_800), (11_800, 12_400))),)
    lines = heard + WORD_LINES[1:]

    def shown(position_s: float, **state) -> str | None:
        made = view(playing(position_s, **state), lines=lines, ends=word_ends(lines), lead_ms=150)
        return underlined(made)

    assert shown(10.1) is None  # the line is up, the singing has not begun
    assert shown(10.3) == "one" and shown(11.7) == "one"  # the estimate says "two" by now
    assert shown(11.8) == "two"
    assert shown(10.3, offset_ms=-200) == "one"  # still on the audio's clock
    assert shown(12.1) == "three"  # the next line was not aligned: the estimate again


# ----------------------------- volume indicator ----------------------------- #


def indicator_model(**state) -> OverlayModel:
    """A connected model that has seen its first state (the baseline) at T0."""
    model = OverlayModel(volume_indicator=True)
    model.set_connected(True)
    model.set_state(playing(10.5, volume=60.0, **state), now_ms=T0)
    model.set_lyrics("T1", LINES)
    return model


def test_the_first_state_after_connecting_shows_no_volume():
    assert indicator_model().view(T0).volume is None


def test_a_volume_change_shows_it_above_the_line_for_a_while():
    model = indicator_model()
    model.set_state(playing(10.5, volume=65.0), now_ms=T0)
    shown = model.view(T0 + 100)
    assert (shown.visible, shown.text, shown.volume) == (True, "an invented first line", 65)
    assert shown.ticking  # it has to come down on time
    assert model.view(T0 + VOLUME_SHOWN_MS).volume is None
    assert model.view(T0 + VOLUME_SHOWN_MS).text  # the line stays


def test_while_paused_the_volume_alone_is_shown_then_the_overlay_goes_away():
    model = indicator_model(status=PlayerStatus.PAUSED, paused=True)
    model.set_state(playing(10.5, volume=55.0, status=PlayerStatus.PAUSED, paused=True), now_ms=T0)
    shown = model.view(T0 + 100)
    assert (shown.visible, shown.text, shown.volume) == (True, "", 55)
    assert model.view(T0 + VOLUME_SHOWN_MS) == HIDDEN


def test_held_presses_keep_it_up_from_the_last_change():
    model = indicator_model()
    for step, volume in enumerate((65.0, 70.0, 75.0)):
        model.set_state(playing(10.5, volume=volume), now_ms=T0 + 500 * step)
    last = T0 + 1000
    assert model.view(last + VOLUME_SHOWN_MS - 1).volume == 75
    assert model.view(last + VOLUME_SHOWN_MS).volume is None


def test_anything_but_the_volume_changing_shows_nothing():
    """The fade-in on resume works below the published volume, so it cannot show either."""
    model = indicator_model()
    for state in (playing(20.5, volume=60.0), playing(0.0, volume=60.0, track_id="T2")):
        model.set_state(state, now_ms=T0)
        assert model.view(T0 + 10).volume is None


def test_switched_off_or_reconnected_there_is_no_indicator():
    model = indicator_model()
    model.set_state(playing(10.5, volume=65.0), now_ms=T0)
    model.configure(lead_ms=0, show_next=False, volume_indicator=False)
    assert model.view(T0 + 10).volume is None
    model.set_state(playing(10.5, volume=70.0), now_ms=T0)
    assert model.view(T0 + 10).volume is None

    model = indicator_model()
    model.set_state(playing(10.5, volume=65.0), now_ms=T0)
    model.set_connected(False)
    model.set_connected(True)
    model.set_state(playing(10.5, volume=90.0), now_ms=T0)  # a new baseline, not a change
    assert model.view(T0 + 10).volume is None


def test_the_volume_shown_is_rounded_to_a_whole_number():
    model = indicator_model()
    model.set_state(playing(10.5, volume=64.6), now_ms=T0)
    assert model.view(T0).volume == 65


# ------------------------------- fade-in level ------------------------------- #


def fading(position_s: float, **state) -> PlaybackState:
    """Playing at ``position_s``, in a 2 s fade-in that began at 10 s, at volume 64."""
    return playing(position_s, volume=64.0, fade_from_s=10.0, fade_in_s=2.0, **state)


def test_a_fade_in_shows_its_level_rising_to_the_volume_on_the_sliders_scale():
    # Gain rises evenly; mpv's volume is cubic, so the level is volume * cbrt(progress).
    assert fade_level(fading(10.0), T0) == 0
    assert fade_level(fading(10.25), T0) == pytest.approx(32.0)  # 1/8 of the way: half
    assert fade_level(fading(11.0), T0) == pytest.approx(64.0 * 0.5 ** (1 / 3))
    assert fade_level(fading(12.0), T0) == 64.0


def test_the_level_follows_playback_time_on_the_overlays_own_clock():
    state = fading(10.25)
    assert fade_level(state, T0 + 750) == pytest.approx(64.0 * 0.5 ** (1 / 3))


def test_after_the_fade_the_volume_is_held_for_a_while_then_goes():
    end = 12.0
    assert fade_level(fading(end + VOLUME_SHOWN_MS / 1000 - 0.01), T0) == 64.0
    assert fade_level(fading(end + VOLUME_SHOWN_MS / 1000), T0) is None


def test_no_level_without_a_fade_or_while_paused():
    assert fade_level(playing(10.5), T0) is None
    assert fade_level(fading(10.5, status=PlayerStatus.PAUSED, paused=True), T0) is None


def test_the_fade_shows_in_the_view_only_with_the_indicator_on():
    lit = view(fading(10.25), show_fade=True)
    assert (lit.visible, lit.volume, lit.ticking) == (True, 32, True)
    assert view(fading(10.25)).volume is None
    model = OverlayModel(volume_indicator=True)
    model.set_connected(True)
    model.set_state(fading(10.25), now_ms=T0)  # the first state: a fade is no baseline
    assert model.view(T0).volume == 32
