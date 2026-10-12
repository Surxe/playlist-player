"""Shared fixtures. Everything here is synthetic: invented IDs, titles and text."""

from __future__ import annotations

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
