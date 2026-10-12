"""Tracklists from the Apify Actor ``s-r/free-spotify-playlist-intelligence``.

One synchronous REST call per playlist: the Actor runs, and the response is its dataset,
a JSON array holding one row per playlist with every track nested in ``tracks``.

Why this Actor: it pages through the whole playlist. The first one tried read Spotify's
embed page, which stops at 100 tracks, and reported the length of its own array as the
track count, so the truncation was invisible. ``total_tracks`` here is Spotify's own
figure, which is what makes the completeness check in the sync layer meaningful.

Each call is a paid run, so it is never retried automatically: a 5xx or a timeout may
well have started (and charged for) a run already.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from ...errors import HttpError, SourceError
from ...http import HttpClient
from ...models import Playlist, Track
from .base import Tracklist
from .spotify import playlist_url

ACTOR = "s-r~free-spotify-playlist-intelligence"

# The run-sync endpoint holds the connection open until the run finishes, up to 300 s.
_RUN_TIMEOUT_S = 330.0

_HTTP_HINTS = {
    401: "Apify rejected the token (APIFY_TOKEN)",
    402: "Apify refused the run for payment reasons (no credit, or the charge cap is too low)",
    403: "Apify denied access to the Actor for this token (APIFY_TOKEN)",
    429: "Apify is rate-limiting this account",
}


class ApifySpotifySource:
    name = ACTOR

    def __init__(
        self,
        http: HttpClient,
        *,
        base_url: str,
        token: Callable[[], str],
        max_charge_usd: float,
    ) -> None:
        self._http = http
        self._base_url = base_url.rstrip("/")
        # A callable, so the token is only demanded when a fetch is really about to
        # happen; reading a cached tracklist must work without one.
        self._token = token
        self._max_charge_usd = max_charge_usd

    def fetch_raw(self, playlist_id: str) -> bytes:
        token = self._token()
        body = {
            "playlists": [playlist_url(playlist_id)],
            # Never let the Actor keep playlist snapshots on the account. Not a setting.
            "trackDiffs": False,
            "pageSize": 100,
        }
        try:
            raw = self._http.request(
                "POST",
                f"{self._base_url}/v2/acts/{ACTOR}/run-sync-get-dataset-items",
                params={
                    "clean": "true",
                    "format": "json",
                    "maxTotalChargeUsd": f"{self._max_charge_usd:g}",
                },
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                body=json.dumps(body).encode("utf-8"),
                timeout=_RUN_TIMEOUT_S,
                retries=1,
            )
        except HttpError as exc:
            hint = _HTTP_HINTS.get(exc.status or 0, "the Apify request failed")
            raise SourceError(f"{hint}; nothing was written. {exc}") from exc
        # A playlist the Actor could not read still comes back as a row with HTTP 2xx.
        # Refuse it here so it is never saved as if it were a tracklist.
        _playlist_row(raw)
        return raw

    def parse(self, raw: bytes, playlist_id: str) -> Tracklist:
        row = _playlist_row(raw)
        row_id = row.get("id") or row.get("playlist_id")
        if row_id and row_id != playlist_id:
            raise SourceError(
                f"the tracklist response is for playlist {row_id!r}, not {playlist_id!r}"
            )

        tracks: dict[str, Track] = {}
        order: list[str] = []
        skipped = 0
        for item in row["tracks"]:
            track = _track(item)
            if track is None:
                skipped += 1
                continue
            tracks.setdefault(track.id, track)
            order.append(track.id)

        owner = row.get("owner")
        playlist = Playlist(
            id=playlist_id,
            name=str(row.get("name") or playlist_id),
            track_ids=tuple(order),
            owner=(owner.get("name") if isinstance(owner, dict) else None) or row.get("owner_name"),
            url=playlist_url(playlist_id),
            image_url=row.get("image_url") or None,
            reported_total=_int(row.get("total_tracks"), _int(row.get("track_count"), None)),
            skipped=skipped,
        )
        return Tracklist(playlist=playlist, tracks=tuple(tracks.values()))


def _playlist_row(raw: bytes) -> dict[str, Any]:
    """The single playlist row of a dataset response, or ``SourceError`` if it is not one."""
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise SourceError(f"the tracklist response is not JSON: {exc}") from exc
    if not isinstance(data, list) or not data or not isinstance(data[0], dict):
        raise SourceError("the tracklist response is not a list holding one playlist row")
    row = data[0]
    tracks = row.get("tracks")
    if not isinstance(tracks, list) or not tracks:
        reason = row.get("error") or row.get("message") or "the row has no tracks"
        raise SourceError(f"the Actor returned no tracklist for this playlist: {reason}")
    return row


def _track(item: Any) -> Track | None:
    """Normalize one playlist entry; ``None`` for anything that cannot be played."""
    if not isinstance(item, dict):
        return None
    track_id = item.get("id")
    duration_ms = _int(item.get("duration_ms"), None)
    if not isinstance(track_id, str) or not track_id.isalnum() or not duration_ms:
        return None
    if item.get("playable") is False:
        return None
    # Playlists can also hold podcast episodes; only music tracks have lyrics to sync.
    if str(item.get("type") or "track").lower() != "track":
        return None
    album = item.get("album") if isinstance(item.get("album"), dict) else {}
    return Track(
        id=track_id,
        title=str(item.get("name") or ""),
        artists=_artist_names(item.get("artists")),
        duration_ms=duration_ms,
        album=album.get("name") or None,
        cover_url=album.get("cover_url") or None,
        explicit=bool(item.get("explicit", False)),
    )


def _artist_names(value: Any) -> tuple[str, ...]:
    # This Actor sends a list of {"name", "uri"} objects. A plain string (as other
    # Spotify Actors send) is tolerated so a shape change degrades instead of failing.
    if isinstance(value, str):
        return tuple(name.strip() for name in value.split(", ") if name.strip())
    names = []
    for entry in value or ():
        name = entry.get("name") if isinstance(entry, dict) else entry
        if isinstance(name, str) and name.strip():
            names.append(name.strip())
    return tuple(names)


def _int(value: Any, default: int | None) -> int | None:
    # bool is an int in Python; a stray true/false must not become 1/0.
    if isinstance(value, bool) or not isinstance(value, int | float):
        return default
    return int(value)
