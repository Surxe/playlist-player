"""The tracklist fetch over a real socket, against a stand-in for the Apify API.

These run the ``sync`` command end to end, so they also cover the wiring in ``cli``:
settings to source to store.
"""

from __future__ import annotations

import pytest

from playlist_player import cli
from playlist_player.cache import CacheStore
from playlist_player.sources.tracklist.apify_spotify import ACTOR
from tests.support.tracklists import PLAYLIST_ID, playlist_row, response, track_item

RUN_PATH = f"/v2/acts/{ACTOR}/run-sync-get-dataset-items"
TOKEN = "synthetic-token-not-a-real-one"
URL = f"https://open.spotify.com/intl-de/playlist/{PLAYLIST_ID}?si=tracking"


@pytest.fixture
def env(fake_server, tmp_path, monkeypatch):
    """Point the CLI at the fake API and a temp cache; return the store it will use."""
    monkeypatch.setenv("PLAYLIST_PLAYER_APIFY_BASE_URL", fake_server.url)
    monkeypatch.setenv("PLAYLIST_PLAYER_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("PLAYLIST_PLAYER_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("APIFY_TOKEN", TOKEN)
    return CacheStore(tmp_path / "cache", tmp_path / "state")


def test_sync_sends_the_expected_request_and_caches_the_result(env, fake_server, capsys):
    body = response(3)
    fake_server.route("POST", RUN_PATH, (201, body.decode()))

    assert cli.main(["sync", URL, "--only", "tracklist"]) == 0

    (request,) = fake_server.requests
    assert (request.method, request.path) == ("POST", RUN_PATH)
    assert request.query == {"clean": "true", "format": "json", "maxTotalChargeUsd": "0.05"}
    assert request.headers["authorization"] == f"Bearer {TOKEN}"
    assert request.headers["content-type"] == "application/json"
    assert request.headers["user-agent"].startswith("playlist-player/")
    # The canonical URL, with the tracking parameter and locale prefix stripped; and
    # snapshots off, always.
    assert request.json() == {
        "playlists": [f"https://open.spotify.com/playlist/{PLAYLIST_ID}"],
        "trackDiffs": False,
        "pageSize": 100,
    }

    assert env.tracklist_raw(PLAYLIST_ID).read_bytes() == body
    assert env.read_json(env.playlist_json(PLAYLIST_ID))["track_ids"] == [
        "TRK0000",
        "TRK0001",
        "TRK0002",
    ]
    assert env.has(env.track_json("TRK0002"))
    out = capsys.readouterr()
    assert "3 playable, 0 skipped, source total 3" in out.out
    assert TOKEN not in out.out + out.err


def test_second_sync_makes_zero_http_requests_and_needs_no_token(
    env, fake_server, monkeypatch, capsys
):
    fake_server.route("POST", RUN_PATH, (201, response(3).decode()))
    assert cli.main(["sync", URL]) == 0
    assert len(fake_server.requests) == 1

    monkeypatch.delenv("APIFY_TOKEN")
    assert cli.main(["sync", f"spotify:playlist:{PLAYLIST_ID}"]) == 0
    assert len(fake_server.requests) == 1
    assert "from cache" in capsys.readouterr().out


def test_missing_token_is_one_clear_line_and_no_request(env, fake_server, monkeypatch, capsys):
    monkeypatch.delenv("APIFY_TOKEN")
    assert cli.main(["sync", URL]) == 1
    assert fake_server.requests == []
    err = capsys.readouterr().err
    assert err.startswith("playlist_player: APIFY_TOKEN is not set") and err.count("\n") == 1
    assert not env.cache_dir.exists()


@pytest.mark.parametrize(
    ("status", "hint"),
    [(402, "payment"), (429, "rate-limiting"), (401, "rejected the token")],
)
def test_an_apify_error_is_reported_clearly_and_writes_nothing(
    env, fake_server, capsys, status, hint
):
    fake_server.route("POST", RUN_PATH, (status, {"error": {"type": "x", "message": "refused"}}))
    assert cli.main(["sync", URL]) == 1
    assert len(fake_server.requests) == 1  # never retried: each attempt can be a paid run
    err = capsys.readouterr().err
    assert hint in err and f"HTTP {status}" in err and "nothing was written" in err
    assert TOKEN not in err
    assert not env.cache_dir.exists()


def test_refresh_is_explicit_and_pays_again(env, fake_server):
    fake_server.script("POST", RUN_PATH, (201, response(2).decode()), (201, response(4).decode()))
    assert cli.main(["sync", PLAYLIST_ID]) == 0
    assert cli.main(["sync", PLAYLIST_ID]) == 0
    assert len(fake_server.requests) == 1
    assert cli.main(["sync", PLAYLIST_ID, "--refresh-tracklist"]) == 0
    assert len(fake_server.requests) == 2
    assert len(env.read_json(env.playlist_json(PLAYLIST_ID))["track_ids"]) == 4


def test_an_incomplete_tracklist_is_kept_but_warned_about_with_exit_2(env, fake_server, capsys):
    row = playlist_row([track_item(i) for i in range(3)], total_tracks=8)
    fake_server.route("POST", RUN_PATH, (201, [row]))
    assert cli.main(["sync", PLAYLIST_ID]) == 2
    assert "5 of 8 entries are missing" in capsys.readouterr().err
    assert env.has(env.tracklist_raw(PLAYLIST_ID))


def test_dry_run_neither_fetches_nor_writes(env, fake_server, capsys):
    assert cli.main(["sync", PLAYLIST_ID, "--dry-run"]) == 0
    assert fake_server.requests == [] and not env.cache_dir.exists()
    assert "would fetch the tracklist" in capsys.readouterr().out

    env.write_bytes(env.tracklist_raw(PLAYLIST_ID), response(2))
    assert cli.main(["sync", PLAYLIST_ID, "--dry-run"]) == 0
    assert fake_server.requests == [] and not env.playlist_json(PLAYLIST_ID).exists()


def test_a_reference_that_is_not_a_playlist_is_rejected(env, fake_server, capsys):
    assert cli.main(["sync", "https://open.spotify.com/track/abc"]) == 1
    assert "cannot find a playlist ID" in capsys.readouterr().err
    assert fake_server.requests == []
