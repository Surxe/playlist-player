"""The Apify Spotify parser, on synthetic responses shaped like the real one."""

from __future__ import annotations

import json

import pytest

from spotifixed.errors import SourceError
from spotifixed.http import HttpClient
from spotifixed.sources.tracklist import build_tracklist_source, source_names
from spotifixed.sources.tracklist.apify_spotify import ACTOR, ApifySpotifySource
from tests.support.tracklists import PLAYLIST_ID, playlist_row, response, track_item


def _no_token() -> str:
    raise AssertionError("parsing must never ask for the token")


@pytest.fixture
def source() -> ApifySpotifySource:
    return ApifySpotifySource(
        HttpClient(), base_url="http://unused.invalid", token=_no_token, max_charge_usd=0.05
    )


def _body(tracks, **row) -> bytes:
    return json.dumps([playlist_row(tracks, **row)]).encode()


def test_parses_playlist_metadata_and_tracks_in_order(source):
    result = source.parse(response(3), PLAYLIST_ID)
    playlist = result.playlist
    assert playlist.id == PLAYLIST_ID
    assert playlist.name == "A Playlist That Does Not Exist"
    assert playlist.owner == "Some Curator"
    assert playlist.url == f"https://open.spotify.com/playlist/{PLAYLIST_ID}"
    assert playlist.image_url == "https://images.invalid/playlist.jpg"
    assert playlist.track_ids == ("TRK0000", "TRK0001", "TRK0002")
    assert (playlist.reported_total, playlist.skipped, playlist.shortfall) == (3, 0, 0)

    first = result.tracks[0]
    assert first.id == "TRK0000" and first.title == "Invented Song 0"
    assert first.artists == ("Made Up Artist 0",)
    assert first.duration_ms == 180_000
    assert first.album == "Imaginary Album 0"
    assert first.cover_url == "https://images.invalid/cover-0.jpg"
    assert first.explicit is False and result.tracks[1].explicit is True


def test_several_artists_keep_their_order(source):
    item = track_item(0, artists=[{"name": "First Act"}, {"name": " Second Act "}, {"uri": "x"}])
    assert source.parse(_body([item]), PLAYLIST_ID).tracks[0].artists == ("First Act", "Second Act")


def test_artists_as_one_string_are_split_for_display(source):
    item = track_item(0, artists="First Act, Second Act")
    assert source.parse(_body([item]), PLAYLIST_ID).tracks[0].artists == ("First Act", "Second Act")


@pytest.mark.parametrize(
    "overrides",
    [
        {"playable": False},
        {"id": None},
        {"id": ""},
        {"id": "../escape"},
        {"duration_ms": None},
        {"duration_ms": 0},
        {"type": "Episode"},
    ],
)
def test_unplayable_or_unusable_entries_are_skipped_and_counted(source, overrides):
    tracks = [track_item(0), track_item(1, **overrides), track_item(2)]
    playlist = source.parse(_body(tracks), PLAYLIST_ID).playlist
    assert playlist.track_ids == ("TRK0000", "TRK0002")
    assert playlist.skipped == 1
    assert playlist.shortfall == 0  # a skipped entry was returned, so it is not missing


def test_a_non_object_entry_is_skipped_not_fatal(source):
    playlist = source.parse(_body([track_item(0), None, "junk"]), PLAYLIST_ID).playlist
    assert playlist.track_ids == ("TRK0000",) and playlist.skipped == 2


def test_a_repeated_track_keeps_its_place_in_the_order_but_is_stored_once(source):
    tracks = [track_item(0), track_item(1), track_item(0)]
    result = source.parse(_body(tracks), PLAYLIST_ID)
    assert result.playlist.track_ids == ("TRK0000", "TRK0001", "TRK0000")
    assert [t.id for t in result.tracks] == ["TRK0000", "TRK0001"]


def test_truncation_shows_as_a_shortfall_against_the_playlists_own_total(source):
    # The lesson from the first Actor: 100 tracks returned for a playlist of 500.
    body = _body([track_item(i) for i in range(4)], total_tracks=10, track_count=4)
    playlist = source.parse(body, PLAYLIST_ID).playlist
    assert playlist.reported_total == 10 and playlist.shortfall == 6


def test_total_falls_back_to_track_count_then_to_unknown(source):
    body = _body([track_item(0)], total_tracks=None, track_count=1)
    assert source.parse(body, PLAYLIST_ID).playlist.reported_total == 1
    body = _body([track_item(0)], total_tracks=None, track_count=None)
    playlist = source.parse(body, PLAYLIST_ID).playlist
    assert playlist.reported_total is None and playlist.shortfall == 0


def test_optional_fields_may_be_absent(source):
    item = {"id": "TRK9", "name": "Bare Minimum", "duration_ms": 1000}
    row = {"tracks": [item]}
    result = source.parse(json.dumps([row]).encode(), PLAYLIST_ID)
    assert result.playlist.name == PLAYLIST_ID and result.playlist.owner is None
    track = result.tracks[0]
    assert (track.artists, track.album, track.cover_url, track.explicit) == ((), None, None, False)


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        (b"<html>gateway</html>", "not JSON"),
        (b"{}", "not a list"),
        (b"[]", "not a list"),
        (b'[{"error": "Playlist not found", "url": "x"}]', "Playlist not found"),
        (b'[{"name": "Empty", "tracks": []}]', "no tracks"),
    ],
)
def test_a_response_that_is_not_a_tracklist_is_a_source_error(source, raw, message):
    with pytest.raises(SourceError, match=message):
        source.parse(raw, PLAYLIST_ID)


def test_a_response_for_a_different_playlist_is_refused(source):
    with pytest.raises(SourceError, match="not 'SomeOtherPlaylist'"):
        source.parse(response(1), "SomeOtherPlaylist")


def test_registry_builds_the_source_by_actor_name():
    kwargs = {
        "http": HttpClient(),
        "apify_base_url": "http://unused.invalid",
        "apify_token": _no_token,
        "apify_max_charge_usd": 0.05,
    }
    assert source_names() == [ACTOR]
    assert build_tracklist_source(ACTOR, **kwargs).name == ACTOR
    assert build_tracklist_source(ACTOR.replace("~", "/"), **kwargs).name == ACTOR
    with pytest.raises(SourceError, match="no tracklist parser for Actor 'someone~else'"):
        build_tracklist_source("someone~else", **kwargs)
