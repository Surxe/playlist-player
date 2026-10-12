"""The audio source contract.

Searching and downloading are separate so that choosing between candidates stays pure
logic in ``sync.matching``, testable on canned lists with no network. A source hands
back the file exactly as the provider served it; probing, tagging and moving it into
the cache are the caller's business.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True, slots=True)
class AudioCandidate:
    """One search result: enough to score it, nothing fetched yet."""

    id: str
    url: str
    title: str
    channel: str = ""
    duration_s: float | None = None
    description: str = ""  # may be only the first line or two


@dataclass(frozen=True, slots=True)
class DownloadedAudio:
    path: Path  # inside the directory the caller supplied
    candidate: AudioCandidate  # what was actually fetched, with full metadata


class AudioSource(Protocol):
    name: str

    def search(self, query: str, limit: int = 10) -> list[AudioCandidate]:
        """Candidates for a free-text query, best first as the provider ranks them."""
        ...

    def download(self, url: str, directory: Path) -> DownloadedAudio:
        """Fetch the best audio-only stream at ``url`` into ``directory``.

        Raises ``SourceError`` when the provider refuses or the transfer fails.
        """
        ...
