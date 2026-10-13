"""The pipeline's decisions, with stages that only count how often they run."""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta

import pytest

from playlist_player.cache import MissLog
from playlist_player.errors import SourceError
from playlist_player.models import Track
from playlist_player.sync import report
from playlist_player.sync.pipeline import Pipeline
from playlist_player.sync.stages import Outcome, StageResult


def tracks(count: int) -> list[Track]:
    return [
        Track(id=f"T{i}", title=f"Song {i}", artists=("Act",), duration_ms=1000)
        for i in range(count)
    ]


class CountingStage:
    """Done for a track once it has run successfully; behaviour scripted per track ID."""

    def __init__(self, name: str, behaviour=None):
        self.name = name
        self.behaviour = behaviour or {}
        self.runs: list[str] = []
        self.finished: set[str] = set()
        self._lock = threading.Lock()

    def is_done(self, track: Track) -> bool:
        return track.id in self.finished

    def run(self, track: Track) -> StageResult:
        with self._lock:
            self.runs.append(track.id)
        action = self.behaviour.get(track.id)
        if isinstance(action, Exception):
            raise action
        if isinstance(action, StageResult):
            return action
        with self._lock:
            self.finished.add(track.id)
        return StageResult.done()


def pipeline(store, stages, **kwargs) -> Pipeline:
    kwargs.setdefault("miss_retry_days", 14)
    return Pipeline(stages, MissLog(store), **kwargs)


def outcomes(reports, stage: str) -> list[Outcome]:
    return [r.results[stage].outcome for r in reports]


def test_stages_run_in_order_per_track_and_reports_keep_track_order(store):
    order = []
    audio, lyrics = CountingStage("audio"), CountingStage("lyrics")
    for stage in (audio, lyrics):
        original = stage.run
        stage.run = lambda track, name=stage.name, original=original: (
            order.append((track.id, name)),
            original(track),
        )[1]

    reports = pipeline(store, [audio, lyrics], workers=3).run(tracks(6))

    assert [r.track.id for r in reports] == [f"T{i}" for i in range(6)]
    assert all(list(r.results) == ["audio", "lyrics"] for r in reports)
    for i in range(6):
        assert order.index((f"T{i}", "audio")) < order.index((f"T{i}", "lyrics"))


def test_second_run_does_no_work(store):
    stage = CountingStage("audio")
    pipeline(store, [stage]).run(tracks(4))
    assert len(stage.runs) == 4
    reports = pipeline(store, [stage]).run(tracks(4))
    assert len(stage.runs) == 4 and outcomes(reports, "audio") == [Outcome.CACHED] * 4


def test_a_miss_is_recorded_and_skipped_next_time_until_asked_or_expired(store):
    stage = CountingStage("lyrics", {"T1": StageResult.miss("not_found", detail={"tried": 2})})
    reports = pipeline(store, [stage]).run(tracks(3))
    assert outcomes(reports, "lyrics") == [Outcome.DONE, Outcome.MISS, Outcome.DONE]
    miss = MissLog(store).get("T1", "lyrics")
    assert (miss.reason, miss.detail) == ("not_found", {"tried": 2})

    again = pipeline(store, [stage]).run(tracks(3))
    assert again[1].results["lyrics"] == StageResult(Outcome.KNOWN_MISS, "not_found")
    assert stage.runs.count("T1") == 1

    pipeline(store, [stage], retry_misses=True).run(tracks(3))
    assert stage.runs.count("T1") == 2

    # An old miss is retried without being asked.
    raw = store.read_json(store.misses_json)
    raw["T1:lyrics"]["at"] = (datetime.now(UTC) - timedelta(days=15)).isoformat(timespec="seconds")
    store.write_json(store.misses_json, raw)
    pipeline(store, [stage]).run(tracks(3))
    assert stage.runs.count("T1") == 3


def test_success_clears_an_old_miss(store):
    stage = CountingStage("lyrics", {"T0": StageResult.miss("not_found")})
    pipeline(store, [stage]).run(tracks(1))
    stage.behaviour.clear()
    reports = pipeline(store, [stage], retry_misses=True).run(tracks(1))
    assert outcomes(reports, "lyrics") == [Outcome.DONE]
    assert MissLog(store).get("T0", "lyrics") is None


def test_a_failure_is_reported_not_recorded_and_does_not_block_later_stages(store):
    audio = CountingStage("audio", {"T0": SourceError("the provider fell over")})
    lyrics = CountingStage("lyrics")
    reports = pipeline(store, [audio, lyrics]).run(tracks(2))

    failed = reports[0].results["audio"]
    assert failed.outcome is Outcome.FAILED and "fell over" in failed.note
    assert reports[0].results["lyrics"].outcome is Outcome.DONE  # still attempted
    assert reports[1].results["audio"].outcome is Outcome.DONE  # other tracks unaffected
    assert MissLog(store).all() == []  # nothing in the negative cache: retried next sync
    pipeline(store, [audio, lyrics]).run(tracks(2))
    assert audio.runs.count("T0") == 2


def test_a_bug_in_a_stage_is_not_swallowed(store):
    stage = CountingStage("audio", {"T0": RuntimeError("a real bug")})
    with pytest.raises(RuntimeError, match="a real bug"):
        pipeline(store, [stage]).run(tracks(1))


def test_dry_run_runs_nothing_and_writes_nothing(store):
    stage = CountingStage("audio")
    reports = pipeline(store, [stage], dry_run=True).run(tracks(3))
    assert outcomes(reports, "audio") == [Outcome.PLANNED] * 3
    assert stage.runs == [] and not store.cache_dir.exists()


def test_workers_bound_the_concurrency(store):
    active, peak, lock, release = [0], [0], threading.Lock(), threading.Event()

    class Slow(CountingStage):
        def run(self, track):
            with lock:
                active[0] += 1
                peak[0] = max(peak[0], active[0])
                if active[0] == 2:
                    release.set()
            release.wait(timeout=5)
            with lock:
                active[0] -= 1
            return super().run(track)

    pipeline(store, [Slow("audio")], workers=2).run(tracks(8))
    assert peak[0] == 2


def test_on_track_is_called_once_per_track_and_totals_add_up(store):
    seen = []
    stage = CountingStage("lyrics", {"T2": StageResult.miss("not_found")})
    reports = pipeline(store, [stage], on_track=seen.append, workers=2).run(tracks(4))
    assert sorted(r.track.id for r in seen) == ["T0", "T1", "T2", "T3"]

    line = report.progress_line(3, 500, reports[2])
    assert line.startswith("[  3/500] T2  lyrics: miss (not_found)") and "Act - Song 2" in line
    totals = report.sync_totals(reports, ["lyrics"])
    assert totals[0] == "Processed 4 tracks." and "3 done, 1 miss" in totals[1]


def test_interrupt_stops_new_work_lets_running_stages_finish_and_resumes(store):
    started = threading.Event()

    class Interrupting(CountingStage):
        def run(self, track):
            result = super().run(track)
            started.set()
            return result

    stage = Interrupting("audio")

    def interrupt_after_first(_report):
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        pipeline(store, [stage], workers=1, on_track=interrupt_after_first).run(tracks(10))
    assert started.is_set()
    ran_before = len(stage.runs)
    assert 1 <= ran_before < 10  # stopped early; nothing half-done by construction

    reports = pipeline(store, [stage], workers=2).run(tracks(10))
    assert len(stage.runs) == 10  # the second run did exactly the remainder
    assert outcomes(reports, "audio").count(Outcome.CACHED) == ran_before
