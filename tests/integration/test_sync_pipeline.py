"""The whole sync with every source faked: complete, resumable, idempotent.

Audio comes from the scripted source (real ffmpeg on generated tones); lyrics and covers
come from a loopback server. Nothing here leaves the machine.
"""

from __future__ import annotations

import pytest

from spotifixed import cli
from spotifixed.cache import MissLog, assets
from spotifixed.http import HttpClient
from spotifixed.models import LyricsKind
from spotifixed.sources.lyrics import LrclibSource
from spotifixed.sources.tracklist import build_tracklist_source
from spotifixed.sources.tracklist.apify_spotify import ACTOR
from spotifixed.sync import report
from spotifixed.sync.audio import AudioStage
from spotifixed.sync.cover import CoverStage, fetch_playlist_cover
from spotifixed.sync.lyrics import LyricsStage
from spotifixed.sync.pipeline import Pipeline
from spotifixed.sync.stages import Outcome
from spotifixed.sync.tracklist import load_tracklist
from tests.support.audio import FakeAudioSource, candidate
from tests.support.lrclib import api_record
from tests.support.tracklists import PLAYLIST_ID, playlist_row, track_item

JPEG = b"\xff\xd8\xff\xe0" + b"synthetic image bytes"
TRACK_COUNT = 6


class InterruptingAudio(FakeAudioSource):
    """Raises KeyboardInterrupt in the middle of the Nth download, like Ctrl-C would."""

    def __init__(self, *args, interrupt_on: int | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.interrupt_on = interrupt_on

    def download(self, url, directory):
        if self.interrupt_on is not None and len(self.downloads) + 1 == self.interrupt_on:
            self.downloads.append(url)
            (directory / "download.webm.part").write_bytes(b"half a download")
            raise KeyboardInterrupt
        return super().download(url, directory)


@pytest.fixture(scope="session")
def tones(tmp_path_factory):
    return tmp_path_factory.mktemp("tones")


@pytest.fixture
def world(store, fake_server, tones):
    """A cached synthetic playlist plus fake lyrics and cover endpoints."""
    items = [
        track_item(
            i,
            duration_ms=5_000,
            album={"name": f"Imaginary Album {i}", "cover_url": f"{fake_server.url}/cover/{i}.jpg"},
        )
        for i in range(TRACK_COUNT)
    ]
    row = playlist_row(items, image_url=f"{fake_server.url}/cover/playlist.jpg")
    import json

    store.write_bytes(store.tracklist_raw(PLAYLIST_ID), json.dumps([row]).encode())

    def lyrics(request):
        index = int(request.query["track_name"].rsplit(" ", 1)[-1])
        return 200, api_record(
            100 + index,
            5,
            trackName=request.query["track_name"],
            artistName=f"Made Up Artist {index}",
        )

    fake_server.route("GET", "/api/get", lyrics)
    for i in range(TRACK_COUNT):
        fake_server.route("GET", f"/cover/{i}.jpg", (200, JPEG))
    fake_server.route("GET", "/cover/playlist.jpg", (200, JPEG))

    class World:
        http = HttpClient(sleep=lambda _s: None)

        def __init__(self):
            self.store = store
            self.server = fake_server

        def tracklist(self):
            def source_for(name):
                return build_tracklist_source(
                    name, http=self.http, apify_base_url="http://unused.invalid",
                    apify_token=lambda: "unused", apify_max_charge_usd=0.0,
                )  # fmt: skip

            return load_tracklist(store, PLAYLIST_ID, source_for, ACTOR).tracklist

        def audio_source(self, **kwargs):
            def results_for(query):
                index = query.rsplit(" ", 1)[-1]
                return [
                    candidate(
                        f"vid{index}",
                        5,
                        title=f"Invented Song {index}",
                        channel=f"Made Up Artist {index} - Topic",
                    )  # fmt: skip
                ]

            source = InterruptingAudio(tones, **kwargs)
            source.search = lambda query, limit=10: (
                source.searches.append(query),
                results_for(query),
            )[1]
            return source

        def stages(self, audio_source):
            return [
                AudioStage(store, audio_source, tolerance_s=3.0, reject_s=7.0),
                LyricsStage(
                    store,
                    LrclibSource(self.http, base_url=fake_server.url, min_interval_s=0),
                    tolerance_s=2.0,
                ),
                CoverStage(store, self.http),
            ]

        def run(self, audio_source, **kwargs):
            pipeline = Pipeline(
                self.stages(audio_source), MissLog(store), miss_retry_days=14, **kwargs
            )
            return pipeline.run(self.tracklist().tracks)

    return World()


def tree(store) -> dict[str, int]:
    """Every file under the cache with its mtime, for "nothing was rewritten" checks."""
    return {
        str(p.relative_to(store.cache_dir)): p.stat().st_mtime_ns
        for p in sorted(store.cache_dir.rglob("*"))
        if p.is_file()
    }


def assert_complete(store) -> None:
    for i in range(TRACK_COUNT):
        track_id = f"TRK{i:04d}"
        names = sorted(p.name for p in store.track_dir(track_id).iterdir())
        assert names == [
            "audio.json", "audio.opus", "cover.jpg", "lyrics.json", "lyrics.lrc", "track.json",
        ], names  # fmt: skip
        status = assets.track_status(store, track_id)
        assert status.playable and status.lyrics_kind is LyricsKind.SYNCED and status.has_cover
        # Chosen against the real audio, not Spotify's figure.
        assert status.lyrics.reference == "audio"


def test_full_run_then_a_second_run_that_calls_no_source(world, store):
    audio = world.audio_source()
    reports = world.run(audio, workers=2)

    assert all(result.outcome is Outcome.DONE for r in reports for result in r.results.values())
    assert_complete(store)
    assert len(audio.downloads) == TRACK_COUNT
    requests_after_first = len(world.server.requests)
    before = tree(store)

    second_audio = world.audio_source()
    again = world.run(second_audio, workers=2)

    assert all(result.outcome is Outcome.CACHED for r in again for result in r.results.values())
    assert second_audio.searches == [] and second_audio.downloads == []
    assert len(world.server.requests) == requests_after_first  # no lyric or cover request
    assert tree(store) == before  # and not one file rewritten


def test_interrupted_midway_then_resumed_completes_without_repeating_finished_work(world, store):
    first = world.audio_source(interrupt_on=4)
    with pytest.raises(KeyboardInterrupt):
        world.run(first, workers=1)

    # Nothing partial has a final name, and the scratch directory is gone.
    for track_dir in (store.cache_dir / "tracks").iterdir():
        names = {p.name for p in track_dir.iterdir()}
        assert not any(name.startswith(".work.") for name in names), names
        assert ("audio.opus" in names) == ("audio.json" in names)
    finished = {
        f"TRK{i:04d}" for i in range(TRACK_COUNT) if assets.read_audio(store, f"TRK{i:04d}")
    }
    assert len(finished) == 3  # the fourth download was the one interrupted
    snapshot = {
        name: mtime for name, mtime in tree(store).items() if name.split("/")[1] in finished
    }

    second = world.audio_source()
    world.run(second, workers=2)

    assert_complete(store)
    downloaded_again = {url.rsplit("vid", 1)[-1] for url in second.downloads}
    assert downloaded_again == {str(i) for i in range(TRACK_COUNT)} - {t[-1] for t in finished}
    after = tree(store)
    audio_files = {n: m for n, m in snapshot.items() if n.endswith(("audio.opus", "audio.json"))}
    assert audio_files and all(after[name] == mtime for name, mtime in audio_files.items())


def test_dry_run_writes_nothing_and_calls_no_source(world, store):
    world.tracklist()
    before = tree(store)
    requests_before = len(world.server.requests)
    audio = world.audio_source()

    reports = world.run(audio, dry_run=True)

    assert all(result.outcome is Outcome.PLANNED for r in reports for result in r.results.values())
    assert audio.searches == [] and audio.downloads == []
    assert len(world.server.requests) == requests_before
    assert tree(store) == before and not store.misses_json.exists()


def test_a_failed_stage_does_not_block_the_later_ones_for_that_track(world, store):
    audio = world.audio_source(broken={"https://video.invalid/watch?v=vid2"})
    reports = world.run(audio)

    broken = reports[2].results
    assert broken["audio"].outcome is Outcome.FAILED
    assert broken["lyrics"].outcome is Outcome.DONE and broken["cover"].outcome is Outcome.DONE
    # With no audio the lyrics were chosen against Spotify's duration, to be redone later.
    assert assets.read_lyrics_info(store, "TRK0002").reference == "spotify"

    world.run(world.audio_source())  # the download works this time
    assert_complete(store)


def test_cover_misses_are_quiet_and_recorded(world, store, fake_server):
    fake_server.route("GET", "/cover/1.jpg", (404, ""))
    fake_server.route("GET", "/cover/3.jpg", (200, "<html>not an image</html>"))
    reports = world.run(world.audio_source())

    assert reports[1].results["cover"].reason == "not_found"
    assert reports[3].results["cover"].reason == "not_an_image"
    assert not store.track_cover("TRK0003").exists()
    assert {m.track_id for m in MissLog(store).all() if m.stage == "cover"} == {
        "TRK0001",
        "TRK0003",
    }

    playlist = world.tracklist().playlist
    assert fetch_playlist_cover(store, world.http, playlist)
    assert store.playlist_cover(PLAYLIST_ID).read_bytes() == JPEG


def test_status_reports_the_synced_playlist_without_lyric_text(world, store, monkeypatch, capsys):
    audio = world.audio_source()
    audio.search = lambda query, limit=10: (
        []
        if query.endswith(" 4")
        else [
            candidate(
                f"vid{query.rsplit(' ', 1)[-1]}",
                5,
                title=f"Invented Song {query.rsplit(' ', 1)[-1]}",
                channel="x - Topic",
            )  # fmt: skip
        ]
    )
    world.run(audio)
    assets.write_track_state(store, "TRK0000", assets.TrackState(offset_ms=-150))
    # The miss log is shared by every playlist: another playlist's review is not this one's.
    MissLog(store).record("ElsewhereTrack", "audio", "needs_review", detail=[])
    monkeypatch.setenv("SPOTIFIXED_CACHE_DIR", str(store.cache_dir))
    monkeypatch.setenv("SPOTIFIXED_STATE_DIR", str(store.state_dir))

    assert cli.main(["status"]) == 0
    listing = capsys.readouterr().out
    assert f"{PLAYLIST_ID}     6 tracks     5 audio     6 synced     6 covers" in listing

    assert cli.main(["status", f"spotify:playlist:{PLAYLIST_ID}"]) == 0
    out = capsys.readouterr().out
    rows = [line for line in out.splitlines() if "TRK000" in line]
    assert len(rows) == TRACK_COUNT
    assert rows[0].split()[:4] == ["1", "TRK0000", "ok", "synced"] and "-150" in rows[0]
    assert rows[4].split()[:4] == ["5", "TRK0004", "miss", "synced"]
    assert "6 tracks: 5 ready (audio and synced lyrics)" in out
    assert "audio    5 cached (0 pinned), 0 need review, 1 missing" in out
    assert "lyrics   6 synced (0 low confidence), 0 plain, 0 instrumental, 0 none" in out
    assert "invented first line" not in out  # never lyric text

    statuses = assets.playlist_status(store, world.tracklist().playlist)
    assert report.coverage(statuses)["ready"] == 5

    assert cli.main(["status", "NotCachedAtAll"]) == 1
    assert "is not cached" in capsys.readouterr().err
