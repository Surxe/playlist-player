"""scripts/fetch-tracklist.sh: the wrapper that adds the token to a tracklist sync."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

import pytest

from playlist_player.cache import CacheStore
from playlist_player.config import REPO_ROOT
from playlist_player.sources.tracklist.apify_spotify import ACTOR
from tests.support.tracklists import PLAYLIST_ID, response

SCRIPT = REPO_ROOT / "scripts" / "fetch-tracklist.sh"
RUN_PATH = f"/v2/acts/{ACTOR}/run-sync-get-dataset-items"
TOKEN = "synthetic-token-from-the-secrets-file"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


@pytest.fixture
def run(fake_server, tmp_path):
    secrets = tmp_path / "secrets.env"
    secrets.write_text(f"APIFY_TOKEN={TOKEN}\n")
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(tmp_path),
        "PLAYLIST_PLAYER_PYTHON": sys.executable,
        "PLAYLIST_PLAYER_SECRETS": str(secrets),
        "PLAYLIST_PLAYER_APIFY_BASE_URL": fake_server.url,
        "PLAYLIST_PLAYER_CACHE_DIR": str(tmp_path / "cache"),
        "PLAYLIST_PLAYER_STATE_DIR": str(tmp_path / "state"),
    }

    def run(*args, **extra):
        return subprocess.run(
            ["bash", str(SCRIPT), *args],
            env={**env, **extra},
            capture_output=True,
            text=True,
            cwd=tmp_path,
            timeout=60,
        )

    run.store = CacheStore(tmp_path / "cache", tmp_path / "state")
    run.secrets = secrets
    return run


def test_fetches_with_the_token_from_the_secrets_file(run, fake_server):
    fake_server.route("POST", RUN_PATH, (201, response(3).decode()))
    result = run(f"https://open.spotify.com/playlist/{PLAYLIST_ID}?si=x")
    assert result.returncode == 0, result.stderr
    assert fake_server.requests[0].headers["authorization"] == f"Bearer {TOKEN}"
    assert run.store.has(run.store.playlist_json(PLAYLIST_ID))
    assert TOKEN not in result.stdout + result.stderr

    again = run(PLAYLIST_ID)  # cached: no second request
    assert again.returncode == 0 and "from cache" in again.stdout
    assert len(fake_server.requests) == 1

    refreshed = run(PLAYLIST_ID, "--refresh")
    assert refreshed.returncode == 0 and len(fake_server.requests) == 2


def test_a_token_already_in_the_environment_wins(run, fake_server):
    fake_server.route("POST", RUN_PATH, (201, response(1).decode()))
    assert run(PLAYLIST_ID, APIFY_TOKEN="synthetic-from-env").returncode == 0
    assert fake_server.requests[0].headers["authorization"] == "Bearer synthetic-from-env"


def test_no_token_anywhere_names_the_variable(run, fake_server):
    run.secrets.unlink()
    result = run(PLAYLIST_ID)
    assert result.returncode == 1 and "APIFY_TOKEN is not set" in result.stderr
    assert fake_server.requests == []


def test_bad_arguments(run):
    assert run().returncode == 1
    assert run("--bogus").returncode == 1
    assert run("a", "b").returncode == 1
    assert run("--help").returncode == 0
