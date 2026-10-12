"""Synthetic LRCLIB records, in the field names the real API uses. All text is invented."""

from __future__ import annotations

from typing import Any

SYNCED_TEXT = "[00:01.00] an invented first line\n[00:04.50] an invented second line\n"
PLAIN_TEXT = "an invented first line\nan invented second line\n"


def api_record(record_id: int, duration: float, **overrides: Any) -> dict[str, Any]:
    record = {
        "id": record_id,
        "name": "Invented Song 0",
        "trackName": "Invented Song 0",
        "artistName": "Made Up Artist 0",
        "albumName": "Imaginary Album 0",
        "duration": duration,
        "instrumental": False,
        "plainLyrics": PLAIN_TEXT,
        "syncedLyrics": SYNCED_TEXT,
    }
    record.update(overrides)
    return record
