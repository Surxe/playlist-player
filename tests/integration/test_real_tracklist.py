"""Parse whatever real tracklists are in the gitignored cache.

Skipped when there are none (a fresh clone, CI, a clean export). It reads the saved
response only and writes nothing, so it is safe to run against the working cache. Run
with ``-s`` to see the summary lines.
"""

from __future__ import annotations

import pytest

from spotifixed.cache import CacheStore
from spotifixed.config import REPO_ROOT
from spotifixed.http import HttpClient
from spotifixed.sources.tracklist import build_tracklist_source
from spotifixed.sources.tracklist.apify_spotify import ACTOR
from spotifixed.sync.tracklist import load_tracklist

pytestmark = pytest.mark.localcache

STORE = CacheStore(REPO_ROOT / "cache", REPO_ROOT / "state")
CACHED = (
    sorted(p.parent.name for p in (STORE.cache_dir / "playlists").glob("*/tracklist.raw.json"))
    if (STORE.cache_dir / "playlists").is_dir()
    else []
)


def _forbidden(*_args):
    raise AssertionError("reading a cached tracklist must not touch the network or the token")


@pytest.mark.skipif(not CACHED, reason="no real tracklist in cache/")
@pytest.mark.parametrize("playlist_id", CACHED)
def test_real_cached_tracklist_parses(playlist_id):
    http = HttpClient(transport=_forbidden)

    def source_for(name):
        return build_tracklist_source(
            name,
            http=http,
            apify_base_url="http://unused.invalid",
            apify_token=_forbidden,
            apify_max_charge_usd=0.0,
        )

    result = load_tracklist(STORE, playlist_id, source_for, ACTOR, write=False)
    playlist, tracks = result.tracklist.playlist, result.tracklist.tracks
    print(f"\n{playlist.name}: {len(playlist.track_ids)} tracks, first {playlist.track_ids[:3]}")

    assert playlist.id == playlist_id and playlist.name
    assert playlist.track_ids and playlist.shortfall == 0
    assert len({t.id for t in tracks}) == len(tracks)
    assert set(playlist.track_ids) == {t.id for t in tracks}
    for track in tracks:
        assert track.title and track.artists and track.duration_ms > 0
    assert http.request_count == 0
