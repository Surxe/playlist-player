"""The tracklist step: fetch once, then live off the saved response."""

from __future__ import annotations

import json

import pytest

from playlist_player.errors import ConfigError, SourceError
from playlist_player.http import HttpClient
from playlist_player.models import Playlist, Track
from playlist_player.sources.tracklist import build_tracklist_source
from playlist_player.sources.tracklist.apify_spotify import ACTOR
from playlist_player.sync import report
from playlist_player.sync.tracklist import load_tracklist, tracklist_is_cached
from tests.support.tracklists import PLAYLIST_ID, response


class Harness:
    """A store plus a source wired to a scripted transport that counts what is sent."""

    def __init__(self, store, *replies, token="synthetic-token"):
        self.store = store
        self.replies = list(replies)
        self.requests = []
        self.http = HttpClient(transport=self._transport, sleep=lambda _s: None)
        self._token = token

    def _transport(self, request, timeout):
        self.requests.append(request)
        return self.replies.pop(0)

    def _require_token(self) -> str:
        if not self._token:
            raise ConfigError("APIFY_TOKEN is not set.")
        return self._token

    def source_for(self, name: str):
        return build_tracklist_source(
            name,
            http=self.http,
            apify_base_url="http://apify.invalid",
            apify_token=self._require_token,
            apify_max_charge_usd=0.05,
        )

    def load(self, **kwargs):
        return load_tracklist(self.store, PLAYLIST_ID, self.source_for, ACTOR, **kwargs)


def test_first_run_fetches_saves_the_raw_response_and_normalizes(store):
    body = response(3)
    harness = Harness(store, (201, body))
    result = harness.load()

    assert result.fetched_now and result.source == ACTOR
    assert len(harness.requests) == 1
    # Verbatim: the very bytes the provider sent, not a re-serialization.
    assert store.tracklist_raw(PLAYLIST_ID).read_bytes() == body
    meta = store.read_json(store.tracklist_meta(PLAYLIST_ID))
    assert meta["actor"] == ACTOR
    assert meta["playlist_url"] == f"https://open.spotify.com/playlist/{PLAYLIST_ID}"
    assert meta["fetched_at"].endswith("Z") and result.fetched_at == meta["fetched_at"]

    playlist = Playlist.from_dict(store.read_json(store.playlist_json(PLAYLIST_ID)))
    assert playlist == result.tracklist.playlist
    assert playlist.track_ids == ("TRK0000", "TRK0001", "TRK0002")
    for track in result.tracklist.tracks:
        assert Track.from_dict(store.read_json(store.track_json(track.id))) == track
    assert store.playlist_ids() == [PLAYLIST_ID]


def test_second_run_makes_no_request_needs_no_token_and_rewrites_nothing(store):
    Harness(store, (200, response(3))).load()
    assert tracklist_is_cached(store, PLAYLIST_ID)
    before = {p: p.stat().st_mtime_ns for p in store.cache_dir.rglob("*") if p.is_file()}

    again = Harness(store, token=None)  # no replies scripted: any request would IndexError
    result = again.load()

    assert not result.fetched_now and again.requests == [] and again.http.request_count == 0
    assert result.tracklist.playlist.track_ids == ("TRK0000", "TRK0001", "TRK0002")
    assert {p: p.stat().st_mtime_ns for p in store.cache_dir.rglob("*") if p.is_file()} == before


def test_a_cached_response_is_reparsed_so_normalized_files_heal(store):
    Harness(store, (200, response(2))).load()
    store.playlist_json(PLAYLIST_ID).unlink()
    store.write_json(store.track_json("TRK0001"), {"id": "TRK0001", "title": "stale"})

    Harness(store, token=None).load()

    assert store.read_json(store.playlist_json(PLAYLIST_ID))["track_ids"] == ["TRK0000", "TRK0001"]
    assert store.read_json(store.track_json("TRK0001"))["title"] == "Invented Song 1"


def test_missing_token_fails_before_any_request_and_names_the_variable(store):
    harness = Harness(store, token=None)
    with pytest.raises(ConfigError, match="APIFY_TOKEN"):
        harness.load()
    assert harness.requests == [] and not store.cache_dir.exists()


@pytest.mark.parametrize("status", [401, 402, 429, 500, 503])
def test_a_failed_paid_request_is_never_retried_and_writes_nothing(store, status):
    harness = Harness(store, (status, b'{"error": {"message": "nope"}}'))
    with pytest.raises(SourceError, match="nothing was written") as info:
        harness.load()
    assert len(harness.requests) == 1  # a retry could start a second paid run
    assert f"HTTP {status}" in str(info.value) and "synthetic-token" not in str(info.value)
    assert not store.cache_dir.exists()


def test_an_error_row_with_http_200_is_not_saved_as_a_tracklist(store):
    harness = Harness(store, (200, b'[{"error": "Playlist is private"}]'))
    with pytest.raises(SourceError, match="Playlist is private"):
        harness.load()
    assert not tracklist_is_cached(store, PLAYLIST_ID)


def test_refresh_fetches_again_and_replaces_the_saved_response(store):
    Harness(store, (200, response(2))).load()
    harness = Harness(store, (200, response(4)))
    result = harness.load(refresh=True)
    assert result.fetched_now and len(harness.requests) == 1
    assert len(result.tracklist.playlist.track_ids) == 4
    assert json.loads(store.tracklist_raw(PLAYLIST_ID).read_bytes())[0]["total_tracks"] == 4


def test_a_failed_refresh_leaves_the_old_tracklist_intact(store):
    original = response(2)
    Harness(store, (200, original)).load()
    with pytest.raises(SourceError):
        Harness(store, (402, b"{}")).load(refresh=True)
    assert store.tracklist_raw(PLAYLIST_ID).read_bytes() == original
    assert len(Harness(store, token=None).load().tracklist.playlist.track_ids) == 2


def test_a_cached_response_is_read_by_the_source_that_produced_it(store):
    Harness(store, (200, response(1))).load()
    store.write_json(store.tracklist_meta(PLAYLIST_ID), {"actor": "someone~else"})
    with pytest.raises(SourceError, match="no tracklist parser for Actor 'someone~else'"):
        Harness(store, token=None).load()


def test_a_cached_response_without_a_meta_file_uses_the_default_source(store):
    Harness(store, (200, response(1))).load()
    store.tracklist_meta(PLAYLIST_ID).unlink()
    result = Harness(store, token=None).load()
    assert result.source == ACTOR and result.fetched_at is None


def test_dry_run_reads_a_cached_tracklist_without_writing(store):
    store.write_bytes(store.tracklist_raw(PLAYLIST_ID), response(2))
    result = Harness(store, token=None).load(write=False)
    assert len(result.tracklist.tracks) == 2
    assert not store.playlist_json(PLAYLIST_ID).exists()
    assert not (store.cache_dir / "tracks").exists()


def test_dry_run_never_fetches(store):
    harness = Harness(store, (200, response(2)))
    with pytest.raises(SourceError, match="not cached"):
        harness.load(write=False)
    assert harness.requests == []


def test_summary_and_shortfall_warning(store):
    result = Harness(store, (200, response(3, total_tracks=5))).load()
    text = "\n".join(report.tracklist_summary(result))
    assert PLAYLIST_ID in text and "fetched now" in text
    assert "3 playable, 0 skipped, source total 5" in text
    assert "2 of 5 entries are missing" in report.shortfall_warning(result)

    complete = Harness(store, token=None).load()  # same file, now from cache
    assert "from cache" in "\n".join(report.tracklist_summary(complete))
    store.write_bytes(store.tracklist_raw(PLAYLIST_ID), response(3))
    assert report.shortfall_warning(Harness(store, token=None).load()) is None
