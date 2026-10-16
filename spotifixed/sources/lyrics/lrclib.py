"""Lyrics from LRCLIB (https://lrclib.net): a free, keyless, community database.

Two endpoints are used. ``/api/get`` takes the track's signature (title, artist, album,
duration) and answers with one record or 404. ``/api/search`` takes title and artist and
answers with a list. Records carry ``syncedLyrics`` (LRC text), ``plainLyrics``, or
neither when ``instrumental`` is true.

LRCLIB asks clients to identify themselves and to go easy on it. The shared HTTP client
sends the project's User-Agent and backs off on 429; this class additionally spaces its
own requests out, across threads, so a sync of 500 tracks is a slow trickle.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

from ...errors import NotFoundError
from ...http import HttpClient
from ...models import Track
from .base import LyricsRecord


class LrclibSource:
    name = "lrclib"

    def __init__(
        self,
        http: HttpClient,
        *,
        base_url: str,
        min_interval_s: float = 0.25,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._http = http
        self._base_url = base_url.rstrip("/")
        self._min_interval_s = min_interval_s
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._next_slot = 0.0

    def get(self, track: Track, duration_s: float) -> LyricsRecord | None:
        self._wait_for_slot()
        try:
            data = self._http.get_json(
                f"{self._base_url}/api/get",
                params={
                    "track_name": track.title,
                    "artist_name": track.primary_artist,
                    "album_name": track.album,
                    "duration": round(duration_s),
                },
            )
        except NotFoundError:
            return None
        return _record(data)

    def search(self, track: Track) -> list[LyricsRecord]:
        self._wait_for_slot()
        data = self._http.get_json(
            f"{self._base_url}/api/search",
            params={"track_name": track.title, "artist_name": track.primary_artist},
        )
        records = (_record(item) for item in data) if isinstance(data, list) else ()
        return [record for record in records if record is not None]

    def _wait_for_slot(self) -> None:
        """Block until this caller's turn; requests start at least ``min_interval_s`` apart."""
        with self._lock:
            now = self._clock()
            slot = max(now, self._next_slot)
            self._next_slot = slot + self._min_interval_s
        if slot > now:
            self._sleep(slot - now)


def _record(data: Any) -> LyricsRecord | None:
    if not isinstance(data, dict) or data.get("id") is None:
        return None
    duration = data.get("duration")
    return LyricsRecord(
        id=str(data["id"]),
        title=str(data.get("trackName") or data.get("name") or ""),
        artist=str(data.get("artistName") or ""),
        album=data.get("albumName") or None,
        duration_s=float(duration) if isinstance(duration, int | float) else None,
        instrumental=bool(data.get("instrumental", False)),
        synced=data.get("syncedLyrics") or None,
        plain=data.get("plainLyrics") or None,
    )
