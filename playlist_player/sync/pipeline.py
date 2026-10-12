"""Run the stages over a playlist's tracks: resumable, bounded, and safe to interrupt.

For each track the stages run in the order given (audio, then lyrics, then cover: the
lyrics are chosen against the real audio's length). For each stage the pipeline decides,
in this order:

* already done on disk: nothing runs (``cached``);
* a recent miss is on record and retries were not asked for: nothing runs (``known_miss``);
* dry run: nothing runs (``planned``);
* otherwise the stage runs. A miss is written to the negative cache, success clears any
  old miss, and an exception is reported as ``failed`` and recorded nowhere, so the next
  sync simply tries again.

One stage failing never stops the later stages for that track, or any other track.

Tracks are spread over a small thread pool; the stages of one track run in sequence on
one thread. On Ctrl-C no new work starts, stages already running are allowed to finish
(each writes through a temp file, so nothing partial ever has a final name), and the
interrupt is then re-raised. Because "done" is read from disk, running again resumes
exactly where it stopped.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

from ..cache import MissLog
from ..errors import PlaylistPlayerError
from ..models import Track
from .stages import Outcome, Stage, StageResult


@dataclass(frozen=True, slots=True)
class TrackReport:
    track: Track
    results: dict[str, StageResult]  # by stage name, in the order the stages ran


class Pipeline:
    def __init__(
        self,
        stages: Sequence[Stage],
        misses: MissLog,
        *,
        miss_retry_days: int,
        workers: int = 1,
        retry_misses: bool = False,
        dry_run: bool = False,
        on_track: Callable[[TrackReport], None] | None = None,
    ) -> None:
        self._stages = list(stages)
        self._misses = misses
        self._miss_retry_days = miss_retry_days
        self._workers = max(1, workers)
        self._retry_misses = retry_misses
        self._dry_run = dry_run
        self._on_track = on_track
        self._stop = threading.Event()

    def run(self, tracks: Sequence[Track]) -> list[TrackReport]:
        """Process ``tracks`` and return one report per track, in the order given."""
        self._stop.clear()
        reports: dict[int, TrackReport] = {}
        executor = ThreadPoolExecutor(max_workers=self._workers, thread_name_prefix="sync")
        try:
            futures = {
                executor.submit(self._run_track, track): index for index, track in enumerate(tracks)
            }
            for future in as_completed(futures):
                report = future.result()
                if report is None:  # skipped because a stop was requested
                    continue
                reports[futures[future]] = report
                if self._on_track:
                    self._on_track(report)
        except KeyboardInterrupt:
            self._stop.set()
            executor.shutdown(wait=True, cancel_futures=True)
            raise
        finally:
            executor.shutdown(wait=True)
        return [reports[index] for index in sorted(reports)]

    def _run_track(self, track: Track) -> TrackReport | None:
        if self._stop.is_set():
            return None
        results: dict[str, StageResult] = {}
        for stage in self._stages:
            if self._stop.is_set():
                break
            results[stage.name] = self._run_stage(stage, track)
        return TrackReport(track, results)

    def _run_stage(self, stage: Stage, track: Track) -> StageResult:
        try:
            if stage.is_done(track):
                return StageResult(Outcome.CACHED)
            if not self._retry_misses and self._misses.should_skip(
                track.id, stage.name, self._miss_retry_days
            ):
                known = self._misses.get(track.id, stage.name)
                return StageResult(Outcome.KNOWN_MISS, known.reason if known else "")
            if self._dry_run:
                return StageResult(Outcome.PLANNED)
            result = stage.run(track)
        except PlaylistPlayerError as exc:
            return StageResult(Outcome.FAILED, str(exc))

        if result.outcome is Outcome.MISS:
            self._misses.record(track.id, stage.name, result.reason or "miss", result.detail)
        else:
            self._misses.clear(track.id, stage.name)
        return result
