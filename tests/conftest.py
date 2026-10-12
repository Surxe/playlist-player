"""Shared fixtures. Everything here is synthetic: invented IDs, titles and text."""

from __future__ import annotations

import socket

import pytest

from playlist_player.cache import CacheStore


@pytest.fixture
def store(tmp_path) -> CacheStore:
    return CacheStore(tmp_path / "cache", tmp_path / "state")


@pytest.fixture(autouse=True)
def _isolated_settings(monkeypatch, tmp_path):
    """Keep the developer's environment and any repo-root .env out of the tests."""
    import os

    for name in list(os.environ):
        if name.startswith("PLAYLIST_PLAYER_") or name == "APIFY_TOKEN":
            monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)  # OptionsConfig loads .env from the working directory


@pytest.fixture(autouse=True)
def _loopback_only(request, monkeypatch):
    """Fail any test that reaches for a real host.

    Tests stand in for every third-party service with a loopback server. A test that
    forgets to (and so would query a real API with made-up titles) fails here instead of
    quietly depending on the network. Tests marked ``network`` are exempt; they never run
    by default.
    """
    if request.node.get_closest_marker("network"):
        return
    real_connect = socket.socket.connect

    def guarded(sock, address, *args):
        host = address[0] if isinstance(address, tuple) else None
        if host is not None and host not in ("127.0.0.1", "::1", "localhost"):
            raise AssertionError(f"test tried to connect to a real host: {host!r}")
        return real_connect(sock, address, *args)

    monkeypatch.setattr(socket.socket, "connect", guarded)


@pytest.fixture
def fake_server():
    """A loopback HTTP server standing in for a third-party API; see tests/support."""
    from tests.support.fake_http import FakeServer

    server = FakeServer()
    yield server
    server.close()
