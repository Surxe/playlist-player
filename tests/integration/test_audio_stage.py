"""The audio stage with a scripted source and real ffmpeg, on generated tones."""

from __future__ import annotations

import json
import subprocess

import pytest

from playlist_player import cli
from playlist_player.cache import MissLog, assets
from playlist_player.errors import PlaylistPlayerError, SourceError
from playlist_player.models import LyricsInfo, LyricsKind, Track, TrackState
from playlist_player.sources.audio import media
from playlist_player.sync.audio import AudioStage
from playlist_player.sync.pin import pin_audio
from playlist_player.sync.pipeline import Pipeline
from playlist_player.sync.stages import Outcome
from tests.support.audio import FakeAudioSource, candidate, make_tone

TRACK = Track(
    id="TRK0000",
    title="Invented Song 0",
    artists=("Made Up Artist 0", "A Guest"),
    duration_ms=5_000,
    album="Imaginary Album 0",
)
TOPIC = "Made Up Artist 0 - Topic"


@pytest.fixture(scope="session")
def tones(tmp_path_factory):
    return tmp_path_factory.mktemp("tones")


def stage_for(store, source) -> AudioStage:
    return AudioStage(store, source, tolerance_s=3.0, reject_s=7.0)


def tags(path) -> dict[str, str]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream_tags:format_tags", "-of", "json", path],
        capture_output=True, text=True, check=True,
    ).stdout  # fmt: skip
    data = json.loads(out)
    found = dict(data.get("format", {}).get("tags", {}))
    for stream in data.get("streams", []):
        found.update(stream.get("tags", {}))
    return {key.lower(): value for key, value in found.items()}


def test_downloads_the_best_candidate_and_stores_a_tagged_copy(store, tones):
    source = FakeAudioSource(tones, [[candidate("video", 5), candidate("album", 5, channel=TOPIC)]])
    stage = stage_for(store, source)

    result = stage.run(TRACK)

    assert result.outcome is Outcome.DONE and "album audio" in result.note
    assert source.searches == ["Made Up Artist 0 A Guest Invented Song 0"]  # one was enough
    assert source.downloads == ["https://video.invalid/watch?v=album"]

    asset = assets.read_audio(store, TRACK.id)
    assert (asset.file, asset.codec, asset.source, asset.source_id) == (
        "audio.opus", "opus", "fake", "album",
    )  # fmt: skip
    assert asset.duration_s == pytest.approx(5.0, abs=0.1)
    assert (asset.score, asset.pinned, asset.channel) == (110.0, False, TOPIC)

    path = assets.audio_path(store, TRACK.id, asset)
    info = media.probe(path)
    assert info.codec == "opus" and "ogg" in info.container  # rewrapped, not re-encoded
    assert tags(path)["title"] == "Invented Song 0"
    assert tags(path)["artist"] == "Made Up Artist 0, A Guest"
    assert tags(path)["album"] == "Imaginary Album 0"
    assert oct(path.stat().st_mode & 0o777) == "0o664"
    assert sorted(p.name for p in store.track_dir(TRACK.id).iterdir()) == [
        "audio.json",
        "audio.opus",
    ]  # no work directory left behind
    assert stage.is_done(TRACK)


def test_a_second_narrower_search_runs_when_the_first_has_no_album_audio(store, tones):
    source = FakeAudioSource(
        tones, [[candidate("video", 5)], [candidate("album", 5, channel=TOPIC)]]
    )
    stage_for(store, source).run(TRACK)
    assert source.searches == [
        "Made Up Artist 0 A Guest Invented Song 0",
        "Made Up Artist 0 - Topic Invented Song 0",
    ]
    assert source.downloads == ["https://video.invalid/watch?v=album"]


def test_nothing_above_the_threshold_is_a_needs_review_miss_listing_the_top_three(store, tones):
    weak = [
        candidate(f"c{i}", 5, title=f"Made Up Artist 0 - Invented Song 0 ({label})")
        for i, label in enumerate(["Live", "Cover", "Remix", "Karaoke"])
    ]
    source = FakeAudioSource(tones, [weak])
    result = stage_for(store, source).run(TRACK)

    assert (result.outcome, result.reason) == (Outcome.MISS, "needs_review")
    assert [entry["id"] for entry in result.detail] == ["c0", "c1", "c2"]
    assert result.detail[0]["url"] == "https://video.invalid/watch?v=c0"
    assert source.downloads == [] and assets.read_audio(store, TRACK.id) is None


def test_no_candidate_of_the_right_length_is_a_not_found_miss(store, tones):
    source = FakeAudioSource(tones, [[candidate("long", 300), candidate("unknown", None)]])
    result = stage_for(store, source).run(TRACK)
    assert (result.outcome, result.reason) == (Outcome.MISS, "not_found")
    assert source.downloads == []


def test_a_failed_download_falls_through_to_the_next_acceptable_candidate(store, tones):
    found = [candidate("gone", 5, channel=TOPIC), candidate("video", 5)]
    source = FakeAudioSource(tones, [found], broken={"https://video.invalid/watch?v=gone"})
    assert stage_for(store, source).run(TRACK).outcome is Outcome.DONE
    assert [url[-4:] for url in source.downloads] == ["gone", "ideo"]
    assert assets.read_audio(store, TRACK.id).source_id == "video"


def test_when_every_download_fails_the_stage_raises_and_leaves_nothing(store, tones):
    found = [candidate("a", 5, channel=TOPIC), candidate("b", 5)]
    source = FakeAudioSource(tones, [found], broken={c.url for c in found})
    with pytest.raises(SourceError, match="video unavailable"):
        stage_for(store, source).run(TRACK)
    assert assets.read_audio(store, TRACK.id) is None
    assert list(store.track_dir(TRACK.id).iterdir()) == []


def test_audio_whose_real_length_is_wrong_is_discarded(store, tones):
    # The search result claimed 5 s; the file that arrived is 14 s.
    found = [candidate("liar", 5, channel=TOPIC)]
    source = FakeAudioSource(tones, [found], lengths={found[0].url: 14})
    with pytest.raises(SourceError, match="expected about 5s"):
        stage_for(store, source).run(TRACK)
    assert assets.read_audio(store, TRACK.id) is None


def test_a_non_opus_download_keeps_its_codec_in_a_container_that_takes_tags(store, tones):
    source = FakeAudioSource(tones, [[candidate("a", 5, channel=TOPIC)]], extension="m4a")
    stage_for(store, source).run(TRACK)
    asset = assets.read_audio(store, TRACK.id)
    assert (asset.file, asset.codec) == ("audio.m4a", "aac")
    assert tags(assets.audio_path(store, TRACK.id, asset))["title"] == "Invented Song 0"


def test_a_pin_overrides_search_scoring_and_the_duration_check(store, tones):
    pin = "https://video.invalid/watch?v=chosen"
    assets.write_track_state(store, TRACK.id, TrackState(offset_ms=40, audio_pin=pin))
    source = FakeAudioSource(tones, [[candidate("album", 5, channel=TOPIC)]], lengths={pin: 14})
    stage = stage_for(store, source)

    result = stage.run(TRACK)

    assert result.outcome is Outcome.DONE and result.note.startswith("pinned")
    assert source.searches == [] and source.downloads == [pin]
    asset = assets.read_audio(store, TRACK.id)
    assert (asset.pinned, asset.url, asset.score) == (True, pin, None)
    assert asset.duration_s == pytest.approx(14.0, abs=0.1)
    assert stage.is_done(TRACK)


def test_audio_fetched_before_a_pin_was_set_is_not_done(store, tones):
    source = FakeAudioSource(tones, [[candidate("album", 5, channel=TOPIC)]])
    stage = stage_for(store, source)
    stage.run(TRACK)
    assets.write_track_state(
        store, TRACK.id, TrackState(audio_pin="https://video.invalid/watch?v=z")
    )
    assert not stage.is_done(TRACK)


def test_a_stale_work_directory_from_a_killed_run_is_swept(store, tones):
    stale = store.track_dir(TRACK.id) / ".work.killed"
    stale.mkdir(parents=True)
    (stale / "download.webm.part").write_bytes(b"half a file")
    stage_for(store, FakeAudioSource(tones, [[candidate("a", 5, channel=TOPIC)]])).run(TRACK)
    assert not stale.exists()


def test_pin_command_rebuilds_audio_and_lyrics_against_the_pinned_audio(
    store, tones, monkeypatch, capsys
):
    store.write_json(store.track_json(TRACK.id), TRACK.to_dict())
    source = FakeAudioSource(tones, [[candidate("album", 5, channel=TOPIC)]])
    stage_for(store, source).run(TRACK)
    assets.write_lyrics(
        store, TRACK.id, LyricsInfo(kind=LyricsKind.SYNCED, source="t"), "[00:01.00] invented"
    )
    assets.write_track_state(store, TRACK.id, TrackState(offset_ms=-80, notes="keep me"))
    misses = MissLog(store)
    misses.record(TRACK.id, "lyrics", "plain_only")

    monkeypatch.setenv("PLAYLIST_PLAYER_CACHE_DIR", str(store.cache_dir))
    monkeypatch.setenv("PLAYLIST_PLAYER_STATE_DIR", str(store.state_dir))
    pin = "https://video.invalid/watch?v=mine"
    assert cli.main(["pin", TRACK.id, pin]) == 0

    assert "pinned audio for TRK0000" in capsys.readouterr().out
    state = assets.read_track_state(store, TRACK.id)
    assert state == TrackState(offset_ms=-80, audio_pin=pin, notes="keep me")
    assert assets.read_audio(store, TRACK.id) is None
    assert assets.read_lyrics_info(store, TRACK.id) is None
    assert MissLog(store).get(TRACK.id, "lyrics") is None
    assert not store.track_file(TRACK.id, "audio.opus").exists()

    # The next sync rebuilds the audio from the pin.
    reports = Pipeline([stage_for(store, source)], MissLog(store), miss_retry_days=14).run([TRACK])
    assert reports[0].results["audio"].outcome is Outcome.DONE
    assert source.downloads[-1] == pin and assets.read_audio(store, TRACK.id).pinned

    assert cli.main(["pin", TRACK.id, "clear"]) == 0
    assert assets.read_track_state(store, TRACK.id).audio_pin is None
    assert assets.read_audio(store, TRACK.id) is None


def test_pin_refuses_an_unknown_track_and_a_non_http_url(store):
    misses = MissLog(store)
    with pytest.raises(PlaylistPlayerError, match="not in the cache"):
        pin_audio(store, misses, "NeverSynced", "https://video.invalid/v")
    store.write_json(store.track_json(TRACK.id), TRACK.to_dict())
    for bad in ("file:///etc/passwd", "ytsearch:anything", "not a url", "https://"):
        with pytest.raises(PlaylistPlayerError, match="not an http"):
            pin_audio(store, misses, TRACK.id, bad)
    assert assets.read_track_state(store, TRACK.id).audio_pin is None


def test_probe_rejects_a_file_that_is_not_audio(tmp_path):
    junk = tmp_path / "junk.webm"
    junk.write_bytes(b"this is not a media file")
    with pytest.raises(SourceError, match="not a readable audio file"):
        media.probe(junk)


def test_make_test_audio_script_generates_each_supported_format(tmp_path):
    for extension, codec in [("webm", "opus"), ("opus", "opus"), ("m4a", "aac"), ("ogg", "vorbis")]:
        info = media.probe(make_tone(tmp_path / f"t.{extension}", 2))
        assert info.codec == codec and info.duration_s == pytest.approx(2.0, abs=0.15)
