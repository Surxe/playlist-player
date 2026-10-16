"""The LRCLIB source and the lyrics stage over a real socket, against a stand-in server."""

from __future__ import annotations

import pytest

from spotifixed.cache import assets
from spotifixed.http import USER_AGENT, HttpClient
from spotifixed.models import LyricsKind, Track
from spotifixed.sources.lyrics import LrclibSource
from spotifixed.sync.lyrics import LyricsStage
from spotifixed.sync.stages import Outcome
from tests.support.lrclib import PLAIN_TEXT, SYNCED_TEXT, api_record

TRACK = Track(
    id="TRK0000",
    title="Invented Song 0",
    artists=("Made Up Artist 0", "A Guest"),
    duration_ms=180_400,
    album="Imaginary Album 0",
)
NOT_FOUND = (404, {"code": 404, "name": "TrackNotFound", "message": "nope"})


@pytest.fixture
def slept():
    return []


@pytest.fixture
def source(fake_server, slept):
    http = HttpClient(sleep=slept.append)  # record backoff instead of waiting it out
    return LrclibSource(http, base_url=fake_server.url, min_interval_s=0)


@pytest.fixture
def stage(store, source):
    return LyricsStage(store, source, tolerance_s=2.0)


def test_exact_lookup_hit_sends_the_track_signature(fake_server, store, stage):
    fake_server.route("GET", "/api/get", (200, api_record(11, 180)))

    result = stage.run(TRACK)

    assert result.outcome is Outcome.DONE
    (request,) = fake_server.requests  # no search needed
    assert request.path == "/api/get"
    assert request.query == {
        "track_name": "Invented Song 0",
        "artist_name": "Made Up Artist 0",
        "album_name": "Imaginary Album 0",
        "duration": "180",
    }
    assert request.headers["user-agent"] == USER_AGENT

    info, text = assets.read_lyrics_text(store, TRACK.id)
    assert text == SYNCED_TEXT
    assert (info.kind, info.file, info.source) == (LyricsKind.SYNCED, "lyrics.lrc", "lrclib")
    assert (info.record_id, info.chosen_by, info.reference) == ("11", "exact", "spotify")
    assert info.timing_confidence == "high" and info.duration_delta_s == pytest.approx(-0.4)
    assert stage.is_done(TRACK)


def test_exact_miss_falls_back_to_search_and_picks_by_duration(fake_server, store, stage):
    fake_server.route("GET", "/api/get", NOT_FOUND)
    fake_server.route(
        "GET",
        "/api/search",
        (
            200,
            [
                api_record(1, 176),
                api_record(2, 181, artistName="Made Up Artist 0 feat. A Guest"),
                api_record(3, 180, artistName="A Tribute Act"),  # right length, wrong artist
                api_record(
                    4, 180, trackName="Invented Song 0 (Karaoke Hits Vol. 9)", syncedLyrics=None
                ),
            ],
        ),
    )

    assert stage.run(TRACK).outcome is Outcome.DONE

    assert [r.path for r in fake_server.requests] == ["/api/get", "/api/search"]
    assert fake_server.requests[1].query == {
        "track_name": "Invented Song 0",
        "artist_name": "Made Up Artist 0",
    }
    info = assets.read_lyrics_info(store, TRACK.id)
    assert (info.record_id, info.chosen_by, info.kind) == ("2", "search", LyricsKind.SYNCED)


def test_429_is_retried_with_backoff_then_succeeds(fake_server, store, stage, slept):
    fake_server.script(
        "GET", "/api/get", (429, {"message": "slow down"}), (200, api_record(5, 180))
    )

    assert stage.run(TRACK).outcome is Outcome.DONE
    assert [r.path for r in fake_server.requests] == ["/api/get", "/api/get"]
    assert len(slept) == 1 and slept[0] > 0
    assert assets.read_lyrics_info(store, TRACK.id).record_id == "5"


def test_exact_record_with_plain_text_only_still_searches_for_timings(fake_server, store, stage):
    fake_server.route("GET", "/api/get", (200, api_record(7, 180, syncedLyrics=None)))
    fake_server.route("GET", "/api/search", (200, [api_record(8, 183)]))

    assert stage.run(TRACK).outcome is Outcome.DONE
    info = assets.read_lyrics_info(store, TRACK.id)
    assert (info.record_id, info.kind, info.timing_confidence) == ("8", LyricsKind.SYNCED, "low")


def test_plain_text_only_is_saved_and_reported_as_a_miss(fake_server, store, stage):
    fake_server.route("GET", "/api/get", (200, api_record(7, 180, syncedLyrics=None)))
    fake_server.route("GET", "/api/search", (200, []))

    result = stage.run(TRACK)

    assert (result.outcome, result.reason) == (Outcome.MISS, "plain_only")
    info, text = assets.read_lyrics_text(store, TRACK.id)
    assert (info.kind, info.file, info.timing_confidence) == (LyricsKind.PLAIN, "lyrics.txt", None)
    assert text == PLAIN_TEXT
    assert not stage.is_done(TRACK)  # the goal is synced lyrics


def test_instrumental_is_recorded_without_a_text_file(fake_server, store, stage):
    record = api_record(9, 180, syncedLyrics=None, plainLyrics=None, instrumental=True)
    fake_server.route("GET", "/api/get", (200, record))
    fake_server.route("GET", "/api/search", (200, []))

    result = stage.run(TRACK)

    assert (result.outcome, result.reason) == (Outcome.MISS, "instrumental")
    info = assets.read_lyrics_info(store, TRACK.id)
    assert info.kind is LyricsKind.INSTRUMENTAL and info.file is None
    assert assets.read_lyrics_text(store, TRACK.id) is None
    assert not store.lyrics_lrc(TRACK.id).exists() and not store.lyrics_txt(TRACK.id).exists()


def test_nothing_found_is_a_miss_and_leaves_no_lyrics_behind(fake_server, store, stage):
    assets.write_lyrics(
        store, TRACK.id, assets.LyricsInfo(kind=LyricsKind.PLAIN, source="lrclib"), "old words"
    )
    fake_server.route("GET", "/api/get", NOT_FOUND)
    fake_server.route("GET", "/api/search", (200, []))

    result = stage.run(TRACK)

    assert (result.outcome, result.reason) == (Outcome.MISS, "not_found")
    assert assets.read_lyrics_info(store, TRACK.id) is None
    assert not store.lyrics_txt(TRACK.id).exists()


def test_the_audio_files_real_duration_is_the_reference_when_there_is_audio(
    fake_server, store, stage
):
    # Spotify says 180.4 s; the audio we hold is 184.2 s. The record timed for 184 wins.
    store.write_bytes(store.track_file(TRACK.id, "audio.opus"), b"not really audio")
    assets.write_audio(
        store, TRACK.id, assets.AudioAsset(file="audio.opus", duration_s=184.2, source="test")
    )
    fake_server.route("GET", "/api/get", NOT_FOUND)
    fake_server.route("GET", "/api/search", (200, [api_record(1, 180), api_record(2, 184)]))

    assert stage.run(TRACK).outcome is Outcome.DONE

    assert fake_server.requests[0].query["duration"] == "184"
    info = assets.read_lyrics_info(store, TRACK.id)
    assert (info.record_id, info.reference, info.reference_duration_s) == ("2", "audio", 184.2)
    assert stage.is_done(TRACK)


def test_lyrics_chosen_before_audio_existed_are_chosen_again(fake_server, store, stage):
    fake_server.route("GET", "/api/get", (200, api_record(11, 180)))
    stage.run(TRACK)
    assert stage.is_done(TRACK)

    store.write_bytes(store.track_file(TRACK.id, "audio.opus"), b"not really audio")
    assets.write_audio(
        store, TRACK.id, assets.AudioAsset(file="audio.opus", duration_s=184.2, source="test")
    )
    assert not stage.is_done(TRACK)


def test_lyrics_chosen_against_audio_since_replaced_are_chosen_again(fake_server, store, stage):
    store.write_bytes(store.track_file(TRACK.id, "audio.opus"), b"not really audio")
    assets.write_audio(
        store, TRACK.id, assets.AudioAsset(file="audio.opus", duration_s=184.2, source="test")
    )
    fake_server.route("GET", "/api/get", (200, api_record(2, 184)))
    stage.run(TRACK)
    assert stage.is_done(TRACK)

    assets.write_audio(
        store, TRACK.id, assets.AudioAsset(file="audio.opus", duration_s=181.0, source="test")
    )
    assert not stage.is_done(TRACK)


def test_requests_are_spaced_out_across_callers(fake_server):
    now = [100.0]
    waits = []

    def sleep(seconds):
        waits.append(round(seconds, 3))
        now[0] += seconds

    source = LrclibSource(
        HttpClient(),
        base_url=fake_server.url,
        min_interval_s=0.5,
        clock=lambda: now[0],
        sleep=sleep,
    )
    fake_server.route("GET", "/api/search", (200, []))
    for _ in range(3):
        source.search(TRACK)
    assert waits == [0.5, 0.5]  # the first goes at once; each later one waits its turn


def test_malformed_records_are_dropped_not_fatal(fake_server, source):
    fake_server.route(
        "GET", "/api/search", (200, [None, {"trackName": "no id"}, api_record(3, 180)])
    )
    assert [r.id for r in source.search(TRACK)] == ["3"]
    fake_server.route("GET", "/api/search", (200, {"unexpected": "object"}))
    assert source.search(TRACK) == []
