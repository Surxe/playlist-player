"""``pin``: choose a track's audio by hand.

A pin is a URL kept in the track's state file (so it survives a cache wipe). Setting,
changing or clearing one throws away that track's cached audio and lyrics, and any miss
recorded for either, so the next sync rebuilds both: the lyrics are chosen against the
audio's real length, and a different recording needs a fresh choice.
"""

from __future__ import annotations

from dataclasses import replace
from urllib.parse import urlsplit

from ..cache import CacheStore, MissLog, assets
from ..errors import PlaylistPlayerError
from ..models import Track, TrackState


def pin_audio(store: CacheStore, misses: MissLog, track_id: str, url: str | None) -> Track:
    """Pin ``track_id`` to ``url``, or clear its pin when ``url`` is ``None``."""
    if not store.has(store.track_json(track_id)):
        raise PlaylistPlayerError(
            f"track {track_id} is not in the cache; sync a playlist that contains it first"
        )
    if url is not None:
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            raise PlaylistPlayerError(f"not an http(s) URL: {url!r}")

    state: TrackState = assets.read_track_state(store, track_id)
    assets.write_track_state(store, track_id, replace(state, audio_pin=url))
    assets.clear_audio(store, track_id)
    assets.clear_lyrics(store, track_id)
    for stage in ("audio", "lyrics"):
        misses.clear(track_id, stage)
    return assets.read_track(store, track_id)
