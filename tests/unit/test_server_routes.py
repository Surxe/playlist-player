"""The REST API, called directly through the router with a fake player."""

from __future__ import annotations

import json

import pytest

from playlist_player.cache import assets
from playlist_player.config import load_settings
from playlist_player.errors import PlaybackError
from playlist_player.overlay_settings import OverlaySettings
from playlist_player.server.library import Library
from playlist_player.server.overlay_config import OverlayConfig
from playlist_player.server.routes import API, Routes
from playlist_player.server.security import allowed_hosts, check_request
from tests.support.library import JPEG, PLAYLIST_ID, build_library
from tests.support.playback import FakePlayer


@pytest.fixture
def player():
    return FakePlayer()


@pytest.fixture
def routes(store, player):
    build_library(store)
    overlay = OverlayConfig(OverlaySettings.from_settings(load_settings()), store)
    return Routes(Library(store), player, overlay)


def get(routes, path):
    response = routes.dispatch("GET", API + path)
    body = json.loads(response.body) if response.content_type == "application/json" else None
    return response, body


def post(routes, path, body=None):
    raw = b"" if body is None else json.dumps(body).encode()
    response = routes.dispatch("POST", API + path, raw)
    return response, json.loads(response.body)


# ---------------------------------- reads ---------------------------------- #


def test_playlists_lists_coverage(routes):
    response, body = get(routes, "/playlists")
    assert response.status == 200
    assert body == {
        "playlists": [
            {
                "id": PLAYLIST_ID,
                "name": "A Playlist That Does Not Exist",
                "owner": "Some Curator",
                "has_cover": True,
                "coverage": {"tracks": 5, "audio": 4, "synced": 4, "ready": 3},
            }
        ]
    }


def test_playlist_detail_has_tracks_in_order_with_status(routes):
    response, body = get(routes, f"/playlists/{PLAYLIST_ID}")
    assert response.status == 200 and body["coverage"]["tracks"] == 5
    tracks = body["tracks"]
    assert [t["id"] for t in tracks] == [f"TRK000{i}" for i in range(5)]
    assert tracks[0] == {
        "id": "TRK0000",
        "title": "Invented Song 0",
        "artists": ["Made Up Artist 0", "A Guest"],
        "album": "Imaginary Album 0",
        "duration_ms": 5000,
        "explicit": False,
        "has_audio": True,
        "audio_duration_s": 5,
        "audio_pinned": False,
        "lyrics_kind": "synced",
        "timing_confidence": "high",
        "has_cover": True,
        "offset_ms": 0,
    }
    assert tracks[2]["timing_confidence"] == "low"
    assert (tracks[3]["lyrics_kind"], tracks[3]["has_cover"]) == ("plain", False)
    assert (tracks[4]["has_audio"], tracks[4]["audio_duration_s"]) == (False, None)
    # Nothing that looks like a filesystem path reaches a client.
    assert "/" not in json.dumps([{k: v for k, v in t.items() if k != "title"} for t in tracks])


def test_track_and_lyrics(routes):
    response, body = get(routes, "/tracks/TRK0001")
    assert response.status == 200 and body["explicit"] is True

    response, body = get(routes, "/tracks/TRK0000/lyrics")
    assert response.status == 200
    assert body == {
        "track_id": "TRK0000",
        "kind": "synced",
        "timing_confidence": "high",
        "offset_ms": 0,
        "lines": [
            {"t_ms": 1000, "text": "an invented first line"},
            {"t_ms": 2500, "text": ""},
            {"t_ms": 4000, "text": "an invented last line"},
        ],
    }
    _, plain = get(routes, "/tracks/TRK0003/lyrics")
    assert (plain["kind"], plain["lines"], plain["plain"]) == (
        "plain",
        [],
        "invented plain words\n",
    )


def test_lyrics_for_a_track_without_any(routes, store):
    assets.clear_lyrics(store, "TRK0004")
    response, body = get(routes, "/tracks/TRK0004/lyrics")
    assert response.status == 200 and (body["kind"], body["lines"]) == ("missing", [])


def test_covers_are_served_as_images(routes):
    for path in ("/tracks/TRK0000/cover", f"/playlists/{PLAYLIST_ID}/cover"):
        response = routes.dispatch("GET", API + path)
        assert (response.status, response.content_type, response.body) == (200, "image/jpeg", JPEG)
    assert routes.dispatch("GET", f"{API}/tracks/TRK0003/cover").status == 404


def test_a_cached_cover_that_is_not_an_image_is_not_served(routes, store):
    store.write_bytes(store.track_cover("TRK0000"), b"<script>alert(1)</script>")
    assert routes.dispatch("GET", f"{API}/tracks/TRK0000/cover").status == 404


def test_state_is_the_current_snapshot(routes, player):
    response, body = get(routes, "/state")
    assert response.status == 200
    assert (body["status"], body["version"], body["track_id"]) == ("idle", 1, None)
    assert set(body) >= {"position_s", "captured_at_epoch_ms", "lyrics_kind", "offset_ms", "speed"}


# --------------------------------- actions --------------------------------- #


def test_load_queues_only_playable_tracks(routes, player):
    response, body = post(routes, "/player/load", {"playlist_id": PLAYLIST_ID})
    assert (response.status, body) == (200, {"ok": True, "queue_length": 4})
    assert player.calls == [("load_queue", ["TRK0000", "TRK0001", "TRK0002", "TRK0003"], 0)]
    # mpv is handed absolute paths that the daemon resolved; the client never supplied one.
    assert all(entry.path.startswith("/") for entry in player.queue.entries)
    assert player.queue.entry_at(3).lyrics_kind.value == "plain"


def test_load_can_start_at_a_track_and_shuffle(routes, player):
    post(routes, "/player/load", {"playlist_id": PLAYLIST_ID, "start_track_id": "TRK0002"})
    assert player.calls[-1][2] == 2
    post(
        routes,
        "/player/load",
        {"playlist_id": PLAYLIST_ID, "start_track_id": "TRK0002", "shuffle": True},
    )
    order = player.calls[-1][1]
    assert order[0] == "TRK0002" and sorted(order) == [f"TRK000{i}" for i in range(4)]


@pytest.mark.parametrize(
    ("body", "status"),
    [
        ({}, 400),
        ({"playlist_id": 7}, 400),
        ({"playlist_id": PLAYLIST_ID, "shuffle": "yes"}, 400),
        ({"playlist_id": PLAYLIST_ID, "start_track_id": 3}, 400),
        ({"playlist_id": "NotCached"}, 404),
        ({"playlist_id": "../../etc"}, 400),
        ({"playlist_id": PLAYLIST_ID, "start_track_id": "TRK0004"}, 404),  # no audio
        ({"playlist_id": PLAYLIST_ID, "start_track_id": "Unknown"}, 404),
    ],
)
def test_load_rejects_bad_requests_without_touching_the_player(routes, player, body, status):
    response, payload = post(routes, "/player/load", body)
    assert response.status == status and "error" in payload
    assert player.calls == []


def test_a_playlist_with_no_audio_cannot_be_loaded(routes, player, store):
    for i in range(4):
        assets.clear_audio(store, f"TRK000{i}")
    response, body = post(routes, "/player/load", {"playlist_id": PLAYLIST_ID})
    assert response.status == 404 and "no cached audio" in body["error"]


@pytest.mark.parametrize("verb", ["play", "pause", "toggle", "next", "prev", "stop"])
def test_transport_verbs(routes, player, verb):
    response, body = post(routes, f"/player/{verb}")
    assert (response.status, body) == (200, {"ok": True})
    assert player.calls == [(verb,)]


def test_seek_absolute_and_relative(routes, player):
    assert post(routes, "/player/seek", {"position_s": 42.5})[0].status == 200
    assert post(routes, "/player/seek", {"delta_s": -10})[0].status == 200
    assert player.calls == [("seek", 42.5, None), ("seek", None, -10.0)]


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"position_s": 1, "delta_s": 1},
        {"position_s": "1"},
        {"position_s": -1},
        {"delta_s": True},
        {"delta_s": None},
        {"position_s": float("inf")},
    ],  # fmt: skip
)
def test_seek_rejects_bad_bodies(routes, player, body):
    raw = json.dumps(body).encode()
    assert routes.dispatch("POST", f"{API}/player/seek", raw).status == 400
    assert player.calls == []


def test_volume(routes, player):
    assert post(routes, "/player/volume", {"volume": 70})[0].status == 200
    assert player.calls == [("set_volume", 70.0)]
    for bad in ({"volume": 101}, {"volume": -1}, {"volume": "loud"}, {}):
        assert post(routes, "/player/volume", bad)[0].status == 400
    assert len(player.calls) == 1


def test_offset_is_persisted_to_state_and_pushed_to_the_player(routes, player, store):
    post(routes, "/player/load", {"playlist_id": PLAYLIST_ID})
    response, body = post(routes, "/tracks/TRK0000/offset", {"offset_ms": -150})
    assert (response.status, body) == (200, {"ok": True, "track_id": "TRK0000", "offset_ms": -150})
    assert assets.read_track_state(store, "TRK0000").offset_ms == -150
    assert store.track_state("TRK0000").is_file()  # under state/, not cache/
    assert player.calls[-1] == ("set_offset", "TRK0000", -150)
    assert player.bus.current.offset_ms == -150
    assert get(routes, "/tracks/TRK0000")[1]["offset_ms"] == -150
    assert get(routes, "/tracks/TRK0000/lyrics")[1]["offset_ms"] == -150


def test_offset_keeps_a_pin_is_clamped_and_validated(routes, store):
    assets.write_track_state(store, "TRK0001", assets.TrackState(audio_pin="https://v.invalid/x"))
    _, body = post(routes, "/tracks/TRK0001/offset", {"offset_ms": 999_999})
    assert body["offset_ms"] == 30_000
    assert assets.read_track_state(store, "TRK0001").audio_pin == "https://v.invalid/x"
    for bad in ({"offset_ms": 1.5}, {"offset_ms": "50"}, {"offset_ms": True}, {}):
        assert post(routes, "/tracks/TRK0001/offset", bad)[0].status == 400
    assert post(routes, "/tracks/Unknown/offset", {"offset_ms": 1})[0].status == 404


# ---------------------------------- errors --------------------------------- #


@pytest.mark.parametrize(
    "path",
    [
        "/tracks/..%2f..%2fetc",
        "/tracks/has space",
        "/tracks/a.b",
        "/playlists/x!y",
        "/tracks/" + "a" * 65,
        "/tracks/%00",
    ],  # fmt: skip
)
def test_malformed_ids_are_400_never_a_path(routes, path):
    response, body = get(routes, path)
    assert response.status == 400 and "not a valid" in body["error"]


def test_unknown_ids_and_routes_are_404(routes):
    assert get(routes, "/tracks/NeverSynced")[0].status == 404
    assert get(routes, "/playlists/NeverSynced")[0].status == 404
    assert get(routes, "/nothing/here")[0].status == 404
    assert routes.dispatch("GET", "/etc/passwd").status == 404
    assert routes.dispatch("GET", "/static/../routes.py").status == 404
    assert routes.dispatch("GET", "/static/secrets.env").status == 404


def test_wrong_method_is_405(routes, player):
    assert routes.dispatch("POST", f"{API}/playlists", b"{}").status == 405
    assert routes.dispatch("GET", f"{API}/player/play").status == 405
    assert routes.dispatch("POST", f"{API}/events", b"{}").status == 405
    assert routes.dispatch("DELETE", f"{API}/tracks/TRK0000").status == 405
    assert player.calls == []


@pytest.mark.parametrize("raw", [b"{not json", b"[1, 2]", b'"text"', b"\xff\xfe"])
def test_bad_json_is_400(routes, player, raw):
    response = routes.dispatch("POST", f"{API}/player/seek", raw)
    assert response.status == 400 and "JSON" in json.loads(response.body)["error"]
    assert player.calls == []


def test_a_player_failure_is_503_not_a_crash(routes, player):
    player.fail_with = PlaybackError("mpv IPC is not connected")
    response, body = post(routes, "/player/play")
    assert response.status == 503 and "mpv IPC is not connected" in body["error"]


def test_there_is_no_audio_or_sync_endpoint(routes):
    for path in ("/tracks/TRK0000/audio", "/sync", "/player/sync", "/tracks/TRK0000/pin"):
        assert routes.dispatch("GET", API + path).status == 404
        assert routes.dispatch("POST", API + path, b"{}").status == 404


def test_static_files_come_from_an_allowlist(routes):
    page = routes.dispatch("GET", "/")
    assert page.status == 200 and page.content_type.startswith("text/html")
    assert b'src="/static/app.js"' in page.body
    for name, kind in [("app.js", "text/javascript"), ("clock.js", "text/javascript"),
                       ("overlay.js", "text/javascript"), ("style.css", "text/css")]:  # fmt: skip
        response = routes.dispatch("GET", f"/static/{name}")
        assert response.status == 200 and response.content_type.startswith(kind)
    assert routes.dispatch("GET", "/static/index.html").status == 404
    # The page is served with a strict policy, so it must carry no inline script or style.
    assert b"<script>" not in page.body and b"style=" not in page.body


# --------------------------------- security -------------------------------- #

HOSTS = allowed_hosts("127.0.0.1", 8765)


def test_allowed_hosts():
    assert allowed_hosts("127.0.0.1", 8765) == {"127.0.0.1:8765", "localhost:8765"}
    assert "[::1]:9000" in allowed_hosts("::1", 9000)


@pytest.mark.parametrize(
    ("method", "headers", "status"),
    [
        ("GET", {"Host": "127.0.0.1:8765"}, None),
        ("GET", {"Host": "LOCALHOST:8765"}, None),
        ("GET", {"Host": "127.0.0.1:8765", "Origin": "http://localhost:8765"}, None),
        ("POST", {"Host": "127.0.0.1:8765", "Content-Type": "application/json"}, None),
        (
            "POST",
            {"Host": "127.0.0.1:8765", "Content-Type": "application/json; charset=utf-8"},
            None,
        ),
        ("GET", {}, 403),
        ("GET", {"Host": "evil.example:8765"}, 403),  # DNS rebinding
        ("GET", {"Host": "127.0.0.1:9999"}, 403),
        ("GET", {"Host": "127.0.0.1"}, 403),
        ("GET", {"Host": "127.0.0.1:8765", "Origin": "https://evil.example"}, 403),
        ("GET", {"Host": "127.0.0.1:8765", "Origin": "null"}, 403),
        (
            "POST",
            {
                "Host": "127.0.0.1:8765",
                "Origin": "http://evil.example",
                "Content-Type": "application/json",
            },
            403,
        ),
        ("POST", {"Host": "127.0.0.1:8765"}, 415),
        ("POST", {"Host": "127.0.0.1:8765", "Content-Type": "text/plain"}, 415),
        (
            "POST",
            {"Host": "127.0.0.1:8765", "Content-Type": "application/x-www-form-urlencoded"},
            415,
        ),
    ],  # fmt: skip
)
def test_check_request(method, headers, status):
    refusal = check_request(method, headers, HOSTS)
    assert (refusal[0] if refusal else None) == status


# ----------------------------- overlay settings ----------------------------- #


def test_overlay_settings_are_described_with_their_rules(routes):
    response, body = get(routes, "/overlay")
    assert response.status == 200
    assert body["settings"]["margin_y"] == 120 and body["overrides"] == {}
    assert body["rules"]["anchor"]["choices"][0] == "top"
    assert body["rules"]["font_size"] == {"kind": "int", "min": 4, "max": 300}
    assert body["rules"]["color"] == {"kind": "color"}


def test_overlay_settings_can_be_changed_dropped_and_reset(routes, store):
    response, body = post(routes, "/overlay", {"margin_y": 300, "anchor": "top-left"})
    assert response.status == 200
    assert body["overrides"] == {"margin_y": 300, "anchor": "top-left"}
    assert json.loads(store.overlay_json.read_text())["overrides"] == body["overrides"]

    _, body = post(routes, "/overlay", {"anchor": None})
    assert body["overrides"] == {"margin_y": 300}
    _, body = post(routes, "/overlay/reset")
    assert body["overrides"] == {} and body["settings"]["margin_y"] == 120


@pytest.mark.parametrize(
    ("body", "says"),
    [
        ({"margin_y": "300"}, "margin_y must be a whole number"),
        ({"margin_y": 300.5}, "margin_y must be a whole number"),
        ({"font": "Sans; rm -rf ~"}, "font must be a font name"),
        ({"color": "red; } body { display: none"}, "color must be a colour"),
        ({"monitor": "../../etc"}, "monitor must be an output connector"),
        ({"cache_dir": "/tmp"}, "'cache_dir' is not an overlay setting"),
        ({"margin_y": 300, "lines": 9}, "lines must be"),
    ],
)
def test_overlay_values_are_checked_and_a_refused_change_changes_nothing(routes, store, body, says):
    response, answer = post(routes, "/overlay", body)
    assert response.status == 400 and says in answer["error"]
    assert get(routes, "/overlay")[1]["overrides"] == {}
    assert not store.overlay_json.exists()


def test_overlay_preview_takes_a_bounded_number_of_seconds(routes):
    response, body = post(routes, "/overlay/preview", {"seconds": 8})
    assert response.status == 200 and body["preview_until_ms"] > 0
    assert post(routes, "/overlay/preview", {"seconds": 0})[1]["preview_until_ms"] == 0
    for bad in ({"seconds": -1}, {"seconds": 61}, {"seconds": "8"}, {"seconds": True}, {}):
        assert post(routes, "/overlay/preview", bad)[0].status == 400


def test_overlay_endpoints_refuse_other_methods_and_report_a_failed_save(
    routes, store, monkeypatch
):
    assert routes.dispatch("GET", API + "/overlay/reset").status == 405
    assert routes.dispatch("DELETE", API + "/overlay").status == 405

    def refuse(path, data):
        raise OSError("read-only file system")

    monkeypatch.setattr(store, "write_json", refuse)
    response, answer = post(routes, "/overlay", {"margin_y": 300})
    assert response.status == 500 and "read-only file system" in answer["error"]
    assert get(routes, "/overlay")[1]["overrides"] == {}


def test_without_an_overlay_store_the_endpoints_do_not_exist(store, player):
    bare = Routes(Library(store), player)
    assert bare.dispatch("GET", API + "/overlay").status == 404
