"""A synthetic synced playlist written straight into a cache, for API and page tests.

Five invented tracks: three fully cached (audio, synced lyrics, cover), one with plain
lyrics only and no cover, one with no audio at all.
"""

from __future__ import annotations

from pathlib import Path

from spotifixed.cache import CacheStore, assets
from spotifixed.models import AudioAsset, LyricsInfo, LyricsKind, Playlist, Track

PLAYLIST_ID = "PLsynthetic0000000001"
JPEG = b"\xff\xd8\xff\xe0" + b"synthetic image bytes"
SYNCED = "[00:01.00] an invented first line\n[00:02.50]\n[00:04.00] an invented last line\n"


def track_id(index: int) -> str:
    return f"TRK{index:04d}"


def build_library(store: CacheStore, *, audio_file: Path | None = None, seconds: float = 5) -> None:
    """Populate ``store``. Audio is a copy of ``audio_file``, or placeholder bytes without one."""
    ids = [track_id(i) for i in range(5)]
    for index, tid in enumerate(ids):
        track = Track(
            id=tid,
            title=f"Invented Song {index}",
            artists=(f"Made Up Artist {index}", "A Guest"),
            duration_ms=int(seconds * 1000),
            album=f"Imaginary Album {index}",
            explicit=index == 1,
        )
        store.write_json(store.track_json(tid), track.to_dict())
        if index != 4:
            name = "audio.opus"
            data = audio_file.read_bytes() if audio_file else b"placeholder, not audio"
            store.write_bytes(store.track_file(tid, name), data)
            assets.write_audio(
                store, tid, AudioAsset(file=name, duration_s=seconds, source="test", codec="opus")
            )
        if index == 3:
            assets.write_lyrics(
                store,
                tid,
                LyricsInfo(kind=LyricsKind.PLAIN, source="test"),
                "invented plain words\n",
            )
        else:
            confidence = "low" if index == 2 else "high"
            info = LyricsInfo(
                kind=LyricsKind.SYNCED,
                source="test",
                timing_confidence=confidence,
                reference="audio",
            )
            assets.write_lyrics(store, tid, info, SYNCED)
        if index != 3:
            store.write_bytes(store.track_cover(tid), JPEG)

    playlist = Playlist(
        id=PLAYLIST_ID,
        name="A Playlist That Does Not Exist",
        track_ids=tuple(ids),
        owner="Some Curator",
        reported_total=5,
    )
    store.write_json(store.playlist_json(PLAYLIST_ID), playlist.to_dict())
    store.write_bytes(store.playlist_cover(PLAYLIST_ID), JPEG)
