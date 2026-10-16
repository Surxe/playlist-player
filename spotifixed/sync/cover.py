"""The cover stage: one image per track, and one for the playlist.

Covers come straight from the URLs in the tracklist. They are decoration for the remote
page, so a track with no usable cover is a quiet miss, not a problem.
"""

from __future__ import annotations

from pathlib import Path

from ..cache import CacheStore
from ..errors import NotFoundError
from ..http import HttpClient
from ..models import Playlist, Track
from .stages import StageResult

# First bytes of the formats image hosts serve: JPEG, PNG, WebP (RIFF....WEBP), GIF.
_MAGIC = (b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n", b"RIFF", b"GIF8")


def _fetch_image(http: HttpClient, store: CacheStore, url: str, dest: Path) -> str | None:
    """Download ``url`` to ``dest``. Returns a miss reason, or ``None`` on success."""
    if not url.startswith(("http://", "https://")):
        return "no_url"
    try:
        data = http.request("GET", url, headers={"Accept": "image/*"})
    except NotFoundError:
        return "not_found"
    if not data.startswith(_MAGIC):
        return "not_an_image"
    store.write_bytes(dest, data)
    return None


class CoverStage:
    name = "cover"

    def __init__(self, store: CacheStore, http: HttpClient) -> None:
        self._store = store
        self._http = http

    def is_done(self, track: Track) -> bool:
        return self._store.has(self._store.track_cover(track.id))

    def run(self, track: Track) -> StageResult:
        reason = _fetch_image(
            self._http, self._store, track.cover_url or "", self._store.track_cover(track.id)
        )
        return StageResult.miss(reason) if reason else StageResult.done()


def fetch_playlist_cover(store: CacheStore, http: HttpClient, playlist: Playlist) -> bool:
    """Make sure the playlist's own cover is cached. Returns whether it is there now."""
    dest = store.playlist_cover(playlist.id)
    if store.has(dest):
        return True
    return _fetch_image(http, store, playlist.image_url or "", dest) is None
