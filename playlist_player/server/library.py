"""What the API says about the cache: playlists, tracks, lyrics, and a queue to play.

The single place where the daemon reads cached files. Every method takes IDs, which are
validated before they touch the filesystem, and returns plain data ready to be sent as
JSON. Nothing here returns a filesystem path to a client; the one method that produces
paths (:meth:`queue_for`) hands them to the player only.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from ..cache import CacheStore, assets
from ..cache.assets import TrackStatus
from ..errors import NotInCacheError
from ..lyrics import parse_lrc
from ..models import LyricsKind, Playlist
from ..playback.queue import PlayQueue, QueueEntry

MAX_OFFSET_MS = 30_000


class Library:
    def __init__(self, store: CacheStore) -> None:
        self._store = store

    # ------------------------------- reading ------------------------------- #

    def _playlist(self, playlist_id: str) -> Playlist:
        if not self._store.has(self._store.playlist_json(playlist_id)):
            raise NotInCacheError(f"playlist {playlist_id} is not cached")
        return assets.read_playlist(self._store, playlist_id)

    def _status(self, track_id: str) -> TrackStatus:
        if not self._store.has(self._store.track_json(track_id)):
            raise NotInCacheError(f"track {track_id} is not cached")
        return assets.track_status(self._store, track_id)

    @staticmethod
    def _track_json(status: TrackStatus) -> dict[str, Any]:
        track, lyrics = status.track, status.lyrics
        return {
            "id": track.id,
            "title": track.title,
            "artists": list(track.artists),
            "album": track.album,
            "duration_ms": track.duration_ms,
            "explicit": track.explicit,
            "has_audio": status.playable,
            "audio_duration_s": status.audio.duration_s if status.audio else None,
            "audio_pinned": bool(status.audio and status.audio.pinned),
            "lyrics_kind": status.lyrics_kind.value,
            "timing_confidence": lyrics.timing_confidence if lyrics else None,
            "has_cover": status.has_cover,
            "offset_ms": status.state.offset_ms,
        }

    @staticmethod
    def _coverage(statuses: list[TrackStatus]) -> dict[str, int]:
        return {
            "tracks": len(statuses),
            "audio": sum(1 for s in statuses if s.playable),
            "synced": sum(1 for s in statuses if s.lyrics_kind is LyricsKind.SYNCED),
            "ready": sum(1 for s in statuses if s.playable and s.lyrics_kind is LyricsKind.SYNCED),
        }

    def playlists(self) -> list[dict[str, Any]]:
        result = []
        for playlist_id in self._store.playlist_ids():
            playlist = assets.read_playlist(self._store, playlist_id)
            statuses = assets.playlist_status(self._store, playlist)
            result.append(
                {
                    "id": playlist.id,
                    "name": playlist.name,
                    "owner": playlist.owner,
                    "has_cover": self._store.has(self._store.playlist_cover(playlist.id)),
                    "coverage": self._coverage(statuses),
                }
            )
        return result

    def playlist(self, playlist_id: str) -> dict[str, Any]:
        playlist = self._playlist(playlist_id)
        statuses = assets.playlist_status(self._store, playlist)
        by_id = {status.track.id: self._track_json(status) for status in statuses}
        return {
            "id": playlist.id,
            "name": playlist.name,
            "owner": playlist.owner,
            "has_cover": self._store.has(self._store.playlist_cover(playlist.id)),
            "coverage": self._coverage(statuses),
            # Playlist order, repeats included, exactly as it will be queued.
            "tracks": [by_id[track_id] for track_id in playlist.track_ids],
        }

    def track(self, track_id: str) -> dict[str, Any]:
        return self._track_json(self._status(track_id))

    def lyrics(self, track_id: str) -> dict[str, Any]:
        status = self._status(track_id)
        payload: dict[str, Any] = {
            "track_id": track_id,
            "kind": status.lyrics_kind.value,
            "timing_confidence": status.lyrics.timing_confidence if status.lyrics else None,
            "offset_ms": status.state.offset_ms,
            "lines": [],
        }
        stored = assets.read_lyrics_text(self._store, track_id)
        if stored is not None:
            info, text = stored
            if info.kind is LyricsKind.SYNCED:
                payload["lines"] = [line.to_dict() for line in parse_lrc(text)]
            elif info.kind is LyricsKind.PLAIN:
                payload["plain"] = text
        return payload

    def track_cover(self, track_id: str) -> Path:
        path = self._store.track_cover(track_id)
        if not self._store.has(path):
            raise NotInCacheError(f"no cover for track {track_id}")
        return path

    def playlist_cover(self, playlist_id: str) -> Path:
        path = self._store.playlist_cover(playlist_id)
        if not self._store.has(path):
            raise NotInCacheError(f"no cover for playlist {playlist_id}")
        return path

    # ------------------------------- playing ------------------------------- #

    def queue_for(
        self, playlist_id: str, *, start_track_id: str | None = None, shuffle: bool = False
    ) -> PlayQueue:
        """The playlist's playable tracks, in order, as a queue for the player."""
        playlist = self._playlist(playlist_id)
        by_id = {s.track.id: s for s in assets.playlist_status(self._store, playlist)}
        entries = []
        for track_id in playlist.track_ids:
            status = by_id[track_id]
            if status.audio is None:
                continue
            entries.append(
                QueueEntry(
                    track_id=track_id,
                    path=str(assets.audio_path(self._store, track_id, status.audio).resolve()),
                    lyrics_kind=status.lyrics_kind,
                    offset_ms=status.state.offset_ms,
                )
            )
        if not entries:
            raise NotInCacheError(f"playlist {playlist_id} has no cached audio to play")
        if start_track_id is not None and all(e.track_id != start_track_id for e in entries):
            raise NotInCacheError(f"track {start_track_id} has no cached audio in this playlist")
        return PlayQueue(
            entries, playlist_id=playlist_id, start_track_id=start_track_id, shuffle=shuffle
        )

    def set_offset(self, track_id: str, offset_ms: int) -> int:
        """Persist a track's lyric timing offset. Returns the value stored."""
        status = self._status(track_id)
        clamped = max(-MAX_OFFSET_MS, min(MAX_OFFSET_MS, int(offset_ms)))
        assets.write_track_state(self._store, track_id, replace(status.state, offset_ms=clamped))
        return clamped
