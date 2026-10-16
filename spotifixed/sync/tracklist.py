"""Sync step one: a playlist reference becomes a normalized playlist on disk.

The provider's response is kept verbatim as ``tracklist.raw.json`` and is the source of
truth. While it exists nothing is fetched and no token is needed: every sync re-parses
it and brings ``playlist.json`` and the per-track ``track.json`` files in line, so a
parser fix reaches the normalized files without paying for another fetch. Only an
explicit refresh replaces it, and a refresh that fails leaves the old copy untouched.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..cache import CacheStore
from ..errors import SourceError
from ..sources.tracklist import Tracklist, TracklistSource, playlist_url

SourceFactory = Callable[[str], TracklistSource]


@dataclass(frozen=True, slots=True)
class TracklistResult:
    tracklist: Tracklist
    source: str
    fetched_at: str | None
    # True when this call paid for a fetch rather than reading the saved response.
    fetched_now: bool


def tracklist_is_cached(store: CacheStore, playlist_id: str) -> bool:
    return store.has(store.tracklist_raw(playlist_id))


def load_tracklist(
    store: CacheStore,
    playlist_id: str,
    source_for: SourceFactory,
    default_source: str,
    *,
    refresh: bool = False,
    write: bool = True,
) -> TracklistResult:
    """Return the playlist's tracklist, fetching it only when it is not cached.

    ``source_for`` builds the source registered under a name. ``default_source`` is used
    for a fetch; a cached response is read by the source that produced it. With
    ``write=False`` (a dry run) nothing on disk changes, so the tracklist must already be
    cached.
    """
    raw_path = store.tracklist_raw(playlist_id)
    meta_path = store.tracklist_meta(playlist_id)

    if tracklist_is_cached(store, playlist_id) and not refresh:
        meta = store.read_json(meta_path, default={})
        # A response saved before the meta file existed can only be from the default.
        source = source_for(meta.get("actor") or default_source)
        raw = raw_path.read_bytes()
        fetched_now = False
    elif not write:
        raise SourceError(
            f"the tracklist for {playlist_id} is not cached, and fetching it writes to the cache"
        )
    else:
        source = source_for(default_source)
        raw = source.fetch_raw(playlist_id)
        # Saved before parsing: whatever the parser makes of it, the paid result is kept.
        store.write_bytes(raw_path, raw)
        meta = {
            "actor": source.name,
            "playlist_url": playlist_url(playlist_id),
            "fetched_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        store.write_json(meta_path, meta)
        fetched_now = True

    tracklist = source.parse(raw, playlist_id)
    if write:
        _write_normalized(store, tracklist)
    return TracklistResult(tracklist, source.name, meta.get("fetched_at"), fetched_now)


def _write_normalized(store: CacheStore, tracklist: Tracklist) -> None:
    for track in tracklist.tracks:
        _write_if_changed(store, store.track_json(track.id), track.to_dict())
    playlist = tracklist.playlist
    _write_if_changed(store, store.playlist_json(playlist.id), playlist.to_dict())


def _write_if_changed(store: CacheStore, path: Path, data: Any) -> None:
    # A re-sync of an unchanged playlist then touches no files at all.
    if store.read_json(path, default=None) != data:
        store.write_json(path, data)
