"""``ctl``: one transport command sent to a running daemon.

Small on purpose. It exists so a desktop global shortcut can pause playback from inside
a fullscreen application, where the page is out of reach.
"""

from __future__ import annotations

from typing import Any

from ..errors import HttpError, PlaylistPlayerError
from ..http import HttpClient

VERBS = ("toggle", "play", "pause", "next", "prev", "stop", "seek", "volume")


def _body(verb: str, value: str | None) -> dict[str, Any]:
    if verb not in ("seek", "volume"):
        return {}
    try:
        number = float(value or "")
    except ValueError:
        raise PlaylistPlayerError(f"ctl {verb} needs a number, got {value!r}") from None
    # A sign makes it relative: "seek +10", "volume -5". A bare number is a position or level.
    relative = (value or "").lstrip().startswith(("+", "-"))
    if verb == "volume":
        return {"delta": number} if relative else {"volume": number}
    return {"delta_s": number} if relative else {"position_s": number}


def send(base_url: str, verb: str, value: str | None = None) -> None:
    """POST ``verb`` to the daemon at ``base_url``. Raises with a one-line reason on failure."""
    client = HttpClient(timeout=5.0, retries=1)
    try:
        client.post_json(f"{base_url}/api/v1/player/{verb}", _body(verb, value))
    except HttpError as exc:
        if exc.status is None:
            raise PlaylistPlayerError(
                f"no daemon is answering at {base_url}; start it with the serve command"
            ) from exc
        raise PlaylistPlayerError(f"the daemon refused {verb}: {exc.detail or exc}") from exc
