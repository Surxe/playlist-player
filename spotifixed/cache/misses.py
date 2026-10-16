"""The negative cache: what could not be fetched, why, and when.

Without it every ``sync`` would search again for lyrics that do not exist and audio that
has no acceptable match. A recorded miss is skipped until it is older than the retry
window, or until ``--retry-misses`` asks for another attempt.

A miss is also how a track gets flagged for attention: reason ``needs_review`` carries
the best rejected candidates in ``detail`` so they can be pinned by hand.
"""

from __future__ import annotations

import threading
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from .store import CacheStore


@dataclass(frozen=True, slots=True)
class Miss:
    track_id: str
    stage: str  # "audio" | "lyrics" | "cover"
    reason: str  # short machine-readable code, e.g. "not_found", "needs_review"
    at: str  # ISO 8601, UTC
    detail: Any = None

    def age(self, now: datetime) -> timedelta:
        return now - datetime.fromisoformat(self.at)


class MissLog:
    """Thread-safe view of ``misses.json``. Sync workers share one instance."""

    def __init__(self, store: CacheStore) -> None:
        self._store = store
        self._lock = threading.Lock()
        raw = store.read_json(store.misses_json, default={})
        self._entries: dict[str, Miss] = {key: Miss(**value) for key, value in raw.items()}

    @staticmethod
    def _key(track_id: str, stage: str) -> str:
        return f"{track_id}:{stage}"

    def get(self, track_id: str, stage: str) -> Miss | None:
        with self._lock:
            return self._entries.get(self._key(track_id, stage))

    def record(self, track_id: str, stage: str, reason: str, detail: Any = None) -> Miss:
        miss = Miss(
            track_id, stage, reason, datetime.now(UTC).isoformat(timespec="seconds"), detail
        )
        with self._lock:
            self._entries[self._key(track_id, stage)] = miss
            self._save()
        return miss

    def clear(self, track_id: str, stage: str) -> None:
        with self._lock:
            if self._entries.pop(self._key(track_id, stage), None) is not None:
                self._save()

    def should_skip(
        self, track_id: str, stage: str, retry_days: int, now: datetime | None = None
    ) -> bool:
        """True when a recent miss says there is no point trying this stage again yet."""
        miss = self.get(track_id, stage)
        if miss is None:
            return False
        return miss.age(now or datetime.now(UTC)) < timedelta(days=retry_days)

    def all(self) -> list[Miss]:
        with self._lock:
            return list(self._entries.values())

    def _save(self) -> None:
        self._store.write_json(
            self._store.misses_json,
            {key: asdict(miss) for key, miss in sorted(self._entries.items())},
        )
