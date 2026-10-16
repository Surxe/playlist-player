"""The tracklist source contract.

Fetching and parsing are separate methods on purpose. Fetching can cost money, so its
result is saved verbatim before anything tries to understand it; parsing is pure and is
run again on every sync from that saved copy, which means a parser fix never needs a
second paid fetch.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ...models import Playlist, Track


@dataclass(frozen=True, slots=True)
class Tracklist:
    """A normalized playlist and the tracks it names, each track once, in first-seen order."""

    playlist: Playlist
    tracks: tuple[Track, ...]


class TracklistSource(Protocol):
    # Recorded beside the saved response so the right parser is used to read it back.
    name: str

    def fetch_raw(self, playlist_id: str) -> bytes:
        """Ask the provider for the playlist and return its response body, unparsed.

        Raises ``ConfigError`` when a credential is missing and ``SourceError`` when the
        provider fails or answers with something that is plainly not a tracklist.
        """
        ...

    def parse(self, raw: bytes, playlist_id: str) -> Tracklist:
        """Normalize a response body previously returned by :meth:`fetch_raw`."""
        ...
