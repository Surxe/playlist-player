"""Synthetic tracklist responses in the shape the Apify Spotify Actor returns.

The field names mirror the real response; every value is invented.
"""

from __future__ import annotations

import json
from typing import Any

PLAYLIST_ID = "PLsynthetic0000000001"


def track_item(position: int, **overrides: Any) -> dict[str, Any]:
    track_id = f"TRK{position:04d}"
    item = {
        "position": position,
        "uri": f"spotify:track:{track_id}",
        "id": track_id,
        "type": "Track",
        "name": f"Invented Song {position}",
        "added_at": "2020-01-01T00:00:00Z",
        "added_by": "nobody",
        "duration_ms": 180_000 + position * 1000,
        "explicit": position % 2 == 1,
        "playable": True,
        "playcount": 1,
        "track_number": 1,
        "disc_number": 1,
        "artists": [{"name": f"Made Up Artist {position}", "uri": "spotify:artist:A"}],
        "album": {
            "uri": "spotify:album:B",
            "name": f"Imaginary Album {position}",
            "cover_url": f"https://images.invalid/cover-{position}.jpg",
        },
    }
    item.update(overrides)
    return item


def playlist_row(tracks: list[dict[str, Any]], **overrides: Any) -> dict[str, Any]:
    row = {
        "uri": f"spotify:playlist:{PLAYLIST_ID}",
        "id": PLAYLIST_ID,
        "playlist_id": PLAYLIST_ID,
        "name": "A Playlist That Does Not Exist",
        "description": "",
        "followers": 0,
        "owner": {"uri": "spotify:user:u", "name": "Some Curator", "username": "u"},
        "owner_name": "Some Curator",
        "image_url": "https://images.invalid/playlist.jpg",
        "total_tracks": len(tracks),
        "track_count": len(tracks),
        "url": f"https://open.spotify.com/playlist/{PLAYLIST_ID}",
        "tracks": tracks,
    }
    row.update(overrides)
    return row


def response(count: int = 3, **row_overrides: Any) -> bytes:
    """A complete dataset response body holding ``count`` ordinary tracks."""
    return json.dumps(
        [playlist_row([track_item(i) for i in range(count)], **row_overrides)]
    ).encode()
