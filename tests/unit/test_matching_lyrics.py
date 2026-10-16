"""Lyric record matching and selection, on synthetic records."""

from __future__ import annotations

import pytest

from spotifixed.models import LyricsKind, Track
from spotifixed.sources.lyrics import LyricsRecord
from spotifixed.sync.matching import (
    choose_lyrics,
    is_same_song,
    matching_records,
    normalize_artist,
    normalize_title,
    record_kind,
)

SYNCED = "[00:01.00] an invented line\n[00:05.00] another invented line\n"
TRACK = Track(id="T1", title="Paper Lanterns", artists=("The Cartographers", "Ada Vale"),
              duration_ms=198_000)  # fmt: skip


def record(rid, duration, *, synced=SYNCED, plain="words", instrumental=False,
           title="Paper Lanterns", artist="The Cartographers"):  # fmt: skip
    return LyricsRecord(
        id=str(rid), title=title, artist=artist, duration_s=duration,
        synced=synced, plain=plain, instrumental=instrumental,
    )  # fmt: skip


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Paper Lanterns", "paper lanterns"),
        ("PAPER  LANTERNS.", "paper lanterns"),
        ("Paper Lanterns (Remastered 2011)", "paper lanterns"),
        ("Paper Lanterns - Single Version", "paper lanterns"),
        ("Paper Lanterns - Remastered 2011", "paper lanterns"),
        ("Paper Lanterns [Live at Nowhere]", "paper lanterns"),
        ("Paper Lanterns (feat. Ada Vale)", "paper lanterns"),
        ("Paper Lanterns feat. Ada Vale", "paper lanterns"),
        ("Paper Lanterns - Radio Edit", "paper lanterns"),
        ("Papér Lantérns", "paper lanterns"),
        ("Paper & Lanterns", "paper and lanterns"),
        ("Don't Stop...", "don t stop"),
        ("Night - Day", "night day"),  # a dash that is part of the title stays
        ("(Untitled)", "untitled"),  # nothing left after stripping: keep the original
    ],
)
def test_normalize_title(title, expected):
    assert normalize_title(title) == expected


def test_normalize_artist_keeps_brackets_and_folds_case():
    assert normalize_artist("The (Odd) Ones!") == "the odd ones"


@pytest.mark.parametrize(
    ("title", "artist", "same"),
    [
        ("Paper Lanterns", "The Cartographers", True),
        ("paper lanterns (Remastered)", "THE CARTOGRAPHERS", True),
        ("Paper Lanterns", "The Cartographers feat. Ada Vale", True),
        ("Paper Lanterns", "The Cartographers & Ada Vale", True),
        ("Paper Lanterns", "Ada Vale", True),  # credited to the second artist alone
        ("Paper Lanterns", "Someone Else", False),
        ("Paper Lanterns", "Cartographers", False),  # not the same name
        ("Paper Lanterns", "", False),
        ("Paper Lanterns II", "The Cartographers", False),
        ("Other Song", "The Cartographers", False),
    ],
)
def test_is_same_song(title, artist, same):
    assert is_same_song(TRACK, record(1, 198, title=title, artist=artist)) is same


def test_matching_records_filters():
    records = [record(1, 198), record(2, 198, artist="A Tribute Band"), record(3, 199)]
    assert [r.id for r in matching_records(TRACK, records)] == ["1", "3"]


def test_record_kind():
    assert record_kind(record(1, 1)) is LyricsKind.SYNCED
    assert record_kind(record(1, 1, synced=None)) is LyricsKind.PLAIN
    assert record_kind(record(1, 1, synced="no timestamps here")) is LyricsKind.PLAIN
    assert record_kind(record(1, 1, synced=None, plain=None, instrumental=True)) is (
        LyricsKind.INSTRUMENTAL
    )
    assert record_kind(record(1, 1, synced=None, plain="  ")) is LyricsKind.MISSING


def test_closest_duration_wins_among_synced_records():
    # The pattern seen on the real service: one song, records at 196, 198, 199 and 199 s.
    records = [record(1, 196), record(2, 198), record(3, 199), record(4, 199)]
    for reference, expected in [(196.2, "1"), (198.0, "2"), (198.4, "2"), (199.3, "3")]:
        choice = choose_lyrics(records, reference, 2.0)
        assert choice.record.id == expected
        assert choice.kind is LyricsKind.SYNCED and choice.timing_confidence == "high"


def test_a_tie_goes_to_the_earlier_record():
    assert choose_lyrics([record(3, 199), record(4, 199)], 199.0, 2.0).record.id == "3"


def test_synced_beats_a_closer_plain_record():
    records = [record(1, 198, synced=None), record(2, 196)]
    choice = choose_lyrics(records, 198.0, 2.0)
    assert choice.record.id == "2" and choice.kind is LyricsKind.SYNCED


def test_a_synced_record_outside_tolerance_is_kept_with_low_confidence():
    choice = choose_lyrics([record(1, 204)], 198.0, 2.0)
    assert choice.record.id == "1"
    assert choice.timing_confidence == "low" and choice.duration_delta_s == 6.0


def test_the_tolerance_bound_is_inclusive_and_delta_is_signed():
    assert choose_lyrics([record(1, 200)], 198.0, 2.0).timing_confidence == "high"
    low = choose_lyrics([record(1, 195.9)], 198.0, 2.0)
    assert low.timing_confidence == "low" and low.duration_delta_s == pytest.approx(-2.1)


def test_a_record_with_no_duration_loses_to_one_that_has_it():
    choice = choose_lyrics([record(1, None), record(2, 230)], 198.0, 2.0)
    assert choice.record.id == "2"
    alone = choose_lyrics([record(1, None)], 198.0, 2.0)
    assert alone.timing_confidence == "low" and alone.duration_delta_s is None


def test_plain_and_instrumental_fallbacks_carry_no_confidence():
    plain = choose_lyrics([record(1, 198, synced=None)], 198.0, 2.0)
    assert plain.kind is LyricsKind.PLAIN and plain.timing_confidence is None
    records = [record(1, 198, synced=None, plain=None, instrumental=True)]
    assert choose_lyrics(records, 198.0, 2.0).kind is LyricsKind.INSTRUMENTAL
    both = [*records, record(2, 190, synced=None)]
    assert choose_lyrics(both, 198.0, 2.0).kind is LyricsKind.PLAIN


def test_nothing_usable_is_none():
    assert choose_lyrics([], 198.0, 2.0) is None
    assert choose_lyrics([record(1, 198, synced=None, plain=None)], 198.0, 2.0) is None


def test_duplicate_ids_are_considered_once():
    assert choose_lyrics([record(1, 198), record(1, 198)], 198.0, 2.0).record.id == "1"
