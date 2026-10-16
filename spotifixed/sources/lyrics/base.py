"""The lyrics source contract.

A source answers two questions: "do you have exactly this recording?" and "what do you
have that might be this song?". It returns its records as they are; choosing between
them is pure logic in ``sync.matching``, so it can be tested on canned lists.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ...models import Track


@dataclass(frozen=True, slots=True)
class LyricsRecord:
    """One lyrics entry as a provider holds it. Either text may be absent."""

    id: str
    title: str
    artist: str
    album: str | None = None
    duration_s: float | None = None
    instrumental: bool = False
    synced: str | None = None  # timed-lyric (LRC) text
    plain: str | None = None


class LyricsSource(Protocol):
    name: str

    def get(self, track: Track, duration_s: float) -> LyricsRecord | None:
        """The provider's own best match for this exact recording, or ``None``."""
        ...

    def search(self, track: Track) -> list[LyricsRecord]:
        """Every record that might be this song, unfiltered."""
        ...
