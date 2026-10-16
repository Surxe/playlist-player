"""Typed cache access: provenance only counts while the file it names is there."""

from __future__ import annotations

import pytest

from spotifixed.cache import assets
from spotifixed.errors import CacheError
from spotifixed.models import AudioAsset, LyricsInfo, LyricsKind, TrackState


def put_audio(store, track_id="T1", name="audio.opus", data=b"tone"):
    store.write_bytes(store.track_file(track_id, name), data)
    asset = AudioAsset(
        file=name, duration_s=5.0, source="test", source_id="vid1", channel="A - Topic"
    )
    assets.write_audio(store, track_id, asset)
    return asset


def test_audio_round_trip(store):
    asset = put_audio(store)
    assert assets.read_audio(store, "T1") == asset
    assert assets.audio_path(store, "T1", asset) == store.track_dir("T1") / "audio.opus"


def test_audio_provenance_without_its_file_counts_as_absent(store):
    put_audio(store)
    store.track_file("T1", "audio.opus").unlink()
    assert assets.read_audio(store, "T1") is None
    put_audio(store, data=b"")  # a zero-byte file is not audio either
    assert assets.read_audio(store, "T1") is None
    assert assets.read_audio(store, "NeverSeen") is None


def test_corrupt_or_hostile_provenance_counts_as_absent(store):
    store.write_json(store.audio_json("T1"), {"duration_s": 5})  # no file named
    assert assets.read_audio(store, "T1") is None
    store.write_json(store.audio_json("T1"), {"file": "../../x", "duration_s": 5, "source": "t"})
    assert assets.read_audio(store, "T1") is None
    store.write_json(store.audio_json("T1"), ["not", "an", "object"])
    assert assets.read_audio(store, "T1") is None


@pytest.mark.parametrize("name", ["../x", "a/b", ".hidden", "", "x" * 80, "sp ace.opus"])
def test_track_file_rejects_anything_but_a_plain_name(store, name):
    with pytest.raises(CacheError):
        store.track_file("T1", name)


def test_clear_audio_removes_file_and_provenance(store):
    put_audio(store)
    assets.clear_audio(store, "T1")
    assert not store.track_file("T1", "audio.opus").exists()
    assert not store.audio_json("T1").exists()
    assets.clear_audio(store, "T1")  # idempotent


def test_lyrics_round_trip_and_replacement(store):
    info = LyricsInfo(
        kind=LyricsKind.SYNCED, source="test", record_id="9", timing_confidence="high"
    )
    saved = assets.write_lyrics(store, "T1", info, "[00:01.00] invented")
    assert saved.file == "lyrics.lrc"
    assert assets.read_lyrics_text(store, "T1") == (saved, "[00:01.00] invented\n")

    plain = assets.write_lyrics(
        store, "T1", LyricsInfo(kind=LyricsKind.PLAIN, source="test"), "invented words\n"
    )
    assert plain.file == "lyrics.txt" and not store.lyrics_lrc("T1").exists()
    assert assets.read_lyrics_text(store, "T1") == (plain, "invented words\n")

    none = assets.write_lyrics(
        store, "T1", LyricsInfo(kind=LyricsKind.INSTRUMENTAL, source="test"), None
    )
    assert none.file is None and not store.lyrics_txt("T1").exists()
    assert assets.read_lyrics_info(store, "T1") == none
    assert assets.read_lyrics_text(store, "T1") is None


def test_lyrics_provenance_without_its_text_counts_as_absent(store):
    assets.write_lyrics(store, "T1", LyricsInfo(kind=LyricsKind.SYNCED, source="t"), "[00:01.00] x")
    store.lyrics_lrc("T1").unlink()
    assert assets.read_lyrics_info(store, "T1") is None


def test_track_state_defaults_and_round_trip(store):
    assert assets.read_track_state(store, "T1") == TrackState()
    state = TrackState(offset_ms=-120, audio_pin="https://video.invalid/watch?v=abc", notes="late")
    assets.write_track_state(store, "T1", state)
    assert assets.read_track_state(store, "T1") == state
    assert store.track_state("T1").parent == store.state_dir / "tracks"
