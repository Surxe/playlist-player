"""The contract between the pipeline and the per-track stages it runs.

A stage has one goal for one track (audio on disk, synced lyrics on disk, a cover on
disk). The pipeline asks whether the goal is already met, and if not runs the stage and
books the result. Stages never touch the negative cache or decide about retries; they
only report.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

from ..models import Track


class Outcome(StrEnum):
    DONE = "done"  # the stage ran and met its goal
    CACHED = "cached"  # the goal was already met; nothing ran
    MISS = "miss"  # the stage ran and the provider does not have it
    KNOWN_MISS = "known_miss"  # a recent miss is on record; nothing ran
    FAILED = "failed"  # the stage raised; nothing recorded, so the next sync tries again
    PLANNED = "planned"  # dry run: the stage would have run


@dataclass(frozen=True, slots=True)
class StageResult:
    outcome: Outcome
    note: str = ""  # a few words for the progress line; never lyric text
    reason: str | None = None  # machine-readable miss code, e.g. "not_found"
    detail: Any = None  # stored with a miss, e.g. the candidates that need review

    @classmethod
    def done(cls, note: str = "") -> StageResult:
        return cls(Outcome.DONE, note)

    @classmethod
    def miss(cls, reason: str, note: str = "", detail: Any = None) -> StageResult:
        return cls(Outcome.MISS, note or reason, reason, detail)


class Stage(Protocol):
    name: str

    def is_done(self, track: Track) -> bool:
        """Whether the goal is already met from what is on disk."""
        ...

    def run(self, track: Track) -> StageResult:
        """Do the work. Return ``done`` or ``miss``; raise a package error on failure."""
        ...
