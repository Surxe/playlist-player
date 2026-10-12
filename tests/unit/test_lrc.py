"""The timed-lyric parser. Every line of text here is invented."""

from __future__ import annotations

from playlist_player.lyrics import parse_lrc
from playlist_player.models import LyricLine


def pairs(text: str) -> list[tuple[int, str]]:
    return [(line.t_ms, line.text) for line in parse_lrc(text)]


def test_basic_lines():
    assert pairs("[00:12.34] a teapot hums\n[01:02.50] the kettle answers\n") == [
        (12_340, "a teapot hums"),
        (62_500, "the kettle answers"),
    ]


def test_fraction_may_have_one_two_or_three_digits_or_be_absent():
    assert pairs("[00:01.5] a\n[00:02.50] b\n[00:03.500] c\n[00:04] d\n[00:05.007] e\n") == [
        (1500, "a"),
        (2500, "b"),
        (3500, "c"),
        (4000, "d"),
        (5007, "e"),
    ]


def test_colon_before_the_fraction_and_long_minutes():
    assert pairs("[00:07:25] a\n[100:00.00] b\n") == [(7250, "a"), (6_000_000, "b")]


def test_several_timestamps_on_one_line_emit_one_line_each():
    assert pairs("[00:10.00][00:40.00] [01:10.00] refrain of spoons\n[00:20.00] verse\n") == [
        (10_000, "refrain of spoons"),
        (20_000, "verse"),
        (40_000, "refrain of spoons"),
        (70_000, "refrain of spoons"),
    ]


def test_metadata_tags_are_skipped():
    text = "[ar:Nobody Real]\n[ti:No Such Song]\n[al:Nothing]\n[length:03:15]\n[by:x]\n[re:y]\n"
    assert pairs(text + "[00:01.00] only line\n") == [(1000, "only line")]


def test_offset_tag_shifts_every_line_wherever_it_appears():
    # Positive offset: lyrics come sooner, so timestamps move earlier.
    assert pairs("[offset:+250]\n[00:01.00] a\n[00:02.00] b\n") == [(750, "a"), (1750, "b")]
    assert pairs("[00:01.00] a\n[offset: -500]\n[00:02.00] b\n") == [(1500, "a"), (2500, "b")]
    assert pairs("[OFFSET:100]\n[00:01.00] a\n") == [(900, "a")]


def test_offset_never_produces_a_negative_time():
    assert pairs("[offset:5000]\n[00:01.00] a\n[00:09.00] b\n") == [(0, "a"), (4000, "b")]


def test_a_timestamp_with_no_text_is_a_gap():
    assert pairs("[00:01.00] sung\n[00:05.00]\n[00:09.00]   \n[00:12.00] sung again\n") == [
        (1000, "sung"),
        (5000, ""),
        (9000, ""),
        (12_000, "sung again"),
    ]


def test_output_is_sorted_and_stable_for_equal_timestamps():
    text = "[00:30.00] late\n[00:10.00] first of two\n[00:10.00] second of two\n[00:05.00] early\n"
    assert pairs(text) == [
        (5000, "early"),
        (10_000, "first of two"),
        (10_000, "second of two"),
        (30_000, "late"),
    ]


def test_windows_line_endings_and_a_byte_order_mark():
    assert pairs("﻿[00:01.00] one\r\n[00:02.00] two\r\n") == [(1000, "one"), (2000, "two")]


def test_brackets_inside_the_text_are_text():
    assert pairs("[00:01.00] sing it [softly] now\n[00:02.00] (echo) [x2]\n") == [
        (1000, "sing it [softly] now"),
        (2000, "(echo) [x2]"),
    ]


def test_inline_word_timestamps_are_dropped():
    assert pairs("[00:01.00] <00:01.00>each <00:01.40>word <00:01.80>timed\n") == [
        (1000, "each word timed")
    ]


def test_untimed_text_and_junk_are_ignored():
    assert pairs("just some words\n\n[not a tag\n[00:xx.00] bad\n[00:01.00] kept\n") == [
        (1000, "kept")
    ]
    assert parse_lrc("") == ()
    assert parse_lrc("no timestamps anywhere\n") == ()


def test_returns_lyric_lines():
    assert parse_lrc("[00:01.00] x") == (LyricLine(1000, "x"),)
