"""Request checks that make a loopback-only, unauthenticated API safe to run.

Binding to 127.0.0.1 keeps other machines out, but not web pages: any site open in the
user's browser can make that browser send requests to localhost. Three checks close
that off, and no CORS header is ever sent, so a foreign page cannot read a response.

* ``Host`` must be the daemon's own address. A page using DNS rebinding reaches the
  port under its own hostname, and is refused here.
* ``Origin``, when a browser sends one, must be the daemon's own origin. That refuses a
  cross-site form post or fetch outright.
* A request with a body must be ``application/json``. A cross-site form cannot send
  that without a preflight, which is never answered.
"""

from __future__ import annotations

from collections.abc import Mapping

JSON_TYPE = "application/json"


def allowed_hosts(host: str, port: int) -> frozenset[str]:
    """Every spelling of the daemon's own address a legitimate client may use."""
    names = {"127.0.0.1", "localhost", host}
    hosts = set()
    for name in names:
        bracketed = f"[{name}]" if ":" in name else name
        hosts.add(f"{bracketed}:{port}")
    return frozenset(hosts)


def check_request(
    method: str, headers: Mapping[str, str], hosts: frozenset[str]
) -> tuple[int, str] | None:
    """``None`` when the request may proceed, else the (status, message) to refuse it with.

    ``headers`` is looked up case-insensitively by the caller's mapping (the standard
    library's message object does that).
    """
    host = (headers.get("Host") or "").strip().lower()
    if host not in hosts:
        return 403, "this API only answers to its own loopback address"

    origin = headers.get("Origin")
    if origin is not None and origin.strip().lower() not in {f"http://{h}" for h in hosts}:
        return 403, "cross-origin requests are not allowed"

    if method in ("POST", "PUT", "PATCH", "DELETE"):
        content_type = (headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        if content_type != JSON_TYPE:
            return 415, "send JSON with Content-Type: application/json"
    return None
