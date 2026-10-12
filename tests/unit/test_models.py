"""Models: JSON round trips and position extrapolation."""

from __future__ import annotations

import json

from playlist_player.models import LyricsKind, PlaybackState, PlayerStatus, Playlist, Track


def test_track_round_trips_through_json():
    track = Track(
        "T1",
        "Invented Song",
        ("First Artist", "Second Artist"),
        201_500,
        "Made Up Album",
        "http://x/c.jpg",
        True,
    )
    restored = Track.from_dict(json.loads(json.dumps(track.to_dict())))
    assert restored == track
    assert restored.primary_artist == "First Artist" and restored.duration_s == 201.5


def test_track_tolerates_missing_optional_fields():
    track = Track.from_dict({"id": "T1", "title": "x", "duration_ms": "1000"})
    assert track.artists == () and track.primary_artist == "" and track.album is None


def test_playlist_round_trips_through_json():
    playlist = Playlist("PL1", "Synthetic List", ("T1", "T2"), "someone", "http://x", None, 2, 1)
    assert Playlist.from_dict(json.loads(json.dumps(playlist.to_dict()))) == playlist


def test_playback_state_round_trips_and_ignores_unknown_keys():
    state = PlaybackState(
        version=7,
        status=PlayerStatus.PLAYING,
        paused=False,
        track_id="T1",
        position_s=12.5,
        duration_s=200.0,
        lyrics_kind=LyricsKind.SYNCED,
    )
    wire = json.loads(json.dumps(state.to_dict()))
    assert wire["status"] == "playing" and wire["lyrics_kind"] == "synced"
    wire["added_in_a_newer_version"] = 1  # an older client must not choke on a newer daemon
    assert PlaybackState.from_dict(wire) == state


def test_position_extrapolates_only_while_playing():
    playing = PlaybackState(
        status=PlayerStatus.PLAYING,
        paused=False,
        position_s=10.0,
        duration_s=100.0,
        speed=1.0,
        captured_at_epoch_ms=1_000_000,
    )
    assert playing.position_at(1_002_500) == 12.5
    assert playing.position_at(999_000) == 10.0  # a clock that reads earlier never rewinds
    assert playing.position_at(1_000_000 + 500_000) == 100.0  # clamped to the duration

    double = PlaybackState(
        status=PlayerStatus.PLAYING,
        paused=False,
        position_s=10.0,
        speed=2.0,
        captured_at_epoch_ms=1_000_000,
    )
    assert double.position_at(1_001_000) == 12.0

    paused = PlaybackState(
        status=PlayerStatus.PAUSED, paused=True, position_s=10.0, captured_at_epoch_ms=1_000_000
    )
    assert paused.position_at(1_060_000) == 10.0
