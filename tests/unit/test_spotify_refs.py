"""Playlist references: every pasted form reduces to the same ID."""

from __future__ import annotations

import pytest

from playlist_player.errors import InvalidIdError
from playlist_player.sources.tracklist import playlist_id_from, playlist_url

ID = "AbCdEfGhIjKlMnOpQrStUv"


@pytest.mark.parametrize(
    "reference",
    [
        ID,
        f"  {ID}\n",
        f"https://open.spotify.com/playlist/{ID}",
        f"https://open.spotify.com/playlist/{ID}?si=abcdef0123456789&pt=1",
        f"https://open.spotify.com/intl-de/playlist/{ID}",
        f"https://open.spotify.com/intl-pt/playlist/{ID}?si=x",
        f"https://open.spotify.com/embed/playlist/{ID}",
        f"https://open.spotify.com/playlist/{ID}/",
        f"https://open.spotify.com/playlist/{ID}#fragment",
        f"open.spotify.com/playlist/{ID}",
        f"spotify:playlist:{ID}",
        f"spotify:user:someone:playlist:{ID}",
    ],
)
def test_every_form_yields_the_id(reference):
    assert playlist_id_from(reference) == ID


@pytest.mark.parametrize(
    "reference",
    [
        "",
        "https://open.spotify.com/track/AbCdEfGhIjKlMnOpQrStUv",
        "https://open.spotify.com/album/AbCdEfGhIjKlMnOpQrStUv",
        "spotify:track:AbCdEfGhIjKlMnOpQrStUv",
        "../../etc/passwd",
        "has spaces",
        "https://open.spotify.com/playlist/",
    ],
)
def test_anything_else_is_rejected(reference):
    with pytest.raises(InvalidIdError):
        playlist_id_from(reference)


def test_the_canonical_url_carries_no_tracking_parameters():
    assert playlist_url(ID) == f"https://open.spotify.com/playlist/{ID}"
