"""Spotify playlist references: every form a user might paste, reduced to the ID.

Pure string handling. The ID is what the cache is keyed by, and the canonical URL is
rebuilt from it so tracking parameters (``?si=...``) never reach a third party.
"""

from __future__ import annotations

import re

from ...errors import InvalidIdError

# Spotify IDs are 22 base-62 characters; the cache accepts 1 to 64 so tests can use
# short ones (see cache.store.validate_id).
_ID = r"[A-Za-z0-9]{1,64}"
# Matches a share URL (with or without an /intl-xx/ or /embed/ prefix, or a query string)
# and the spotify:playlist:<id> URI.
_IN_REFERENCE = re.compile(rf"(?:^|[/:])playlist[/:]({_ID})(?:$|[/?#])")
_BARE = re.compile(rf"^{_ID}$")


def playlist_id_from(reference: str) -> str:
    """Return the playlist ID in ``reference`` (a URL, a URI or a bare ID), else raise."""
    text = reference.strip()
    found = _IN_REFERENCE.search(text)
    if found:
        return found.group(1)
    if _BARE.fullmatch(text):
        return text
    raise InvalidIdError(
        f"cannot find a playlist ID in {reference!r}; expected a playlist URL, a "
        "spotify:playlist:<id> URI or a bare ID"
    )


def playlist_url(playlist_id: str) -> str:
    return f"https://open.spotify.com/playlist/{playlist_id}"
