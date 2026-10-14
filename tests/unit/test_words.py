"""The current-word estimate, against the case table shared with the page's clock.js."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from playlist_player.lyrics import aligned_word, current_word, parse_lrc, word_ends, word_ranges
from playlist_player.lyrics.words import SLACK, syllables
from playlist_player.models import LyricLine

TABLE = json.loads((Path(__file__).parent.parent / "fixtures" / "word_cases.json").read_text())


@pytest.mark.parametrize("case", TABLE["syllables"], ids=lambda case: repr(case["word"]))
def test_shared_syllable_table(case):
    assert syllables(case["word"]) == case["expected"]


@pytest.mark.parametrize("case", TABLE["tracks"], ids=lambda case: case["name"])
def test_shared_track_table(case):
    ends = word_ends([LyricLine(**line) for line in case["lines"]])
    assert len(ends) == len(case["expected"])
    for got, expected in zip(ends, case["expected"], strict=True):
        assert got == pytest.approx(expected, abs=1e-6)


@pytest.mark.parametrize("case", TABLE["moments"], ids=lambda case: case["name"])
def test_shared_moment_table(case):
    assert current_word(case["ends"], case["elapsed_ms"]) == case["expected"]


@pytest.mark.parametrize("case", TABLE["aligned"], ids=lambda case: case["name"])
def test_shared_aligned_table(case):
    words = [tuple(word) for word in case["words"]]
    assert aligned_word(words, case["position_ms"]) == case["expected"]


def test_a_line_at_the_usual_pace_is_sung_until_the_next_line():
    lines = parse_lrc("[00:00] one two\n[00:02] three four\n[00:04] five six\n[00:06]\n")
    assert word_ends(lines) == [(1000, 2000), (1000, 2000), (1000, 2000), ()]


def test_an_instrumental_after_a_line_is_not_sung_through():
    lrc = "[00:00] one two\n[00:02] three four\n[00:04] five six\n[00:34] seven eight\n[00:36]\n"
    ends = word_ends(parse_lrc(lrc))
    # The third line's span is 30 s. Its two syllables at the typical second each, with
    # the slack, come to 2.6 s; the other 27.4 s are its trail.
    assert ends[2] == pytest.approx((1000 * SLACK, 2000 * SLACK))
    assert current_word(ends[2], 2500) == 1
    assert current_word(ends[2], 2700) == -1


def test_word_ranges_are_character_offsets_into_the_text():
    text = "Señor, don't  stop"
    assert [text[a:b] for a, b in word_ranges(text)] == ["Señor,", "don't", "stop"]
    assert word_ranges("") == []
