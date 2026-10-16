"""Tracklist sources, and the registry that maps a source name to its implementation.

The name is the Apify Actor ID. It is the value of the ``TRACKLIST_ACTOR`` setting and
is also written beside every saved response, because a response can only be read by the
parser that belongs to the Actor that produced it. Adding a source is one new module
plus one entry in ``_SOURCES``.
"""

from __future__ import annotations

from collections.abc import Callable

from ...errors import SourceError
from ...http import HttpClient
from . import apify_spotify
from .apify_spotify import ApifySpotifySource
from .base import Tracklist, TracklistSource
from .spotify import playlist_id_from, playlist_url

_SOURCES: dict[str, type[ApifySpotifySource]] = {
    apify_spotify.ACTOR: ApifySpotifySource,
}


def source_names() -> list[str]:
    return sorted(_SOURCES)


def build_tracklist_source(
    name: str,
    *,
    http: HttpClient,
    apify_base_url: str,
    apify_token: Callable[[], str],
    apify_max_charge_usd: float,
) -> TracklistSource:
    """Return the source registered as ``name`` (``user~actor``; ``user/actor`` also works)."""
    try:
        implementation = _SOURCES[name.strip().replace("/", "~")]
    except KeyError:
        raise SourceError(
            f"no tracklist parser for Actor {name!r}. Known: {', '.join(source_names())}. "
            "A different Actor returns a different shape and needs its own parser."
        ) from None
    return implementation(
        http, base_url=apify_base_url, token=apify_token, max_charge_usd=apify_max_charge_usd
    )


__all__ = [
    "ApifySpotifySource",
    "Tracklist",
    "TracklistSource",
    "build_tracklist_source",
    "playlist_id_from",
    "playlist_url",
    "source_names",
]
