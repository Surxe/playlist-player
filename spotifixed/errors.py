"""Exception hierarchy.

Everything the package raises on purpose derives from :class:`PlaylistPlayerError`, so
the CLI can turn an expected failure into one clean line and let anything else surface
as a traceback (which then means a bug, not a bad input).
"""

from __future__ import annotations


class PlaylistPlayerError(Exception):
    """Base class for every expected failure."""


class ConfigError(PlaylistPlayerError):
    """A setting is missing or invalid. The message names the setting."""


class InvalidIdError(PlaylistPlayerError):
    """A playlist or track ID failed validation before touching the filesystem."""


class CacheError(PlaylistPlayerError):
    """A cached file is missing or cannot be parsed."""


class NotInCacheError(CacheError):
    """The playlist, track or asset asked for has not been synced. The API answers 404."""


class HttpError(PlaylistPlayerError):
    """A request failed after retries. Carries the status and a short body excerpt."""

    def __init__(self, status: int | None, url: str, detail: str = "") -> None:
        self.status = status
        self.url = url
        self.detail = detail
        where = f"HTTP {status}" if status is not None else "network error"
        super().__init__(f"{where} for {url}" + (f": {detail}" if detail else ""))


class NotFoundError(HttpError):
    """The server answered 404. Sources treat this as "no such record", not a fault."""


class SourceError(PlaylistPlayerError):
    """A tracklist, lyrics or audio source could not produce a result."""


class PlaybackError(PlaylistPlayerError):
    """The player process or its control channel failed."""
