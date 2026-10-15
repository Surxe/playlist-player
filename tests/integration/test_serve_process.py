"""The real ``serve`` and ``start`` commands as processes: stopped the ways a person stops them.

These run the daemon the way a terminal does, in its own process group with mpv as its
child, and stop it by signalling the whole group, which is what Ctrl-C and closing the
terminal do. Audio output is disabled; the tracks are generated tones.
"""

from __future__ import annotations

import http.client
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from playlist_player.cache import CacheStore
from playlist_player.config import REPO_ROOT
from tests.support.audio import tone
from tests.support.library import PLAYLIST_ID, build_library

pytestmark = [
    pytest.mark.mpv,
    pytest.mark.skipif(shutil.which("mpv") is None, reason="mpv is not installed"),
]


class Serve:
    """One run of ``serve``, in its own process group like a command typed at a terminal."""

    def __init__(
        self,
        store: CacheStore,
        runtime: Path,
        port: int,
        command: tuple[str, ...] = ("serve",),
        extra_env: dict[str, str] | None = None,
    ) -> None:
        self.store, self.port = store, port
        env = {k: v for k, v in os.environ.items() if not k.startswith("PLAYLIST_PLAYER_")}
        env.pop("APIFY_TOKEN", None)
        env.update(
            PYTHONPATH=str(REPO_ROOT),
            XDG_RUNTIME_DIR=str(runtime),
            PLAYLIST_PLAYER_CACHE_DIR=str(store.cache_dir),
            PLAYLIST_PLAYER_STATE_DIR=str(store.state_dir),
            PLAYLIST_PLAYER_PORT=str(port),
            PLAYLIST_PLAYER_MPV_AUDIO_OUTPUT="null",
            **(extra_env or {}),
        )
        self.socket_path = runtime / "playlist-player" / f"mpv-{port}.sock"
        self.process = subprocess.Popen(
            [sys.executable, "-m", "playlist_player", *command],
            cwd=runtime, env=env, start_new_session=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )  # fmt: skip
        self.output = ""
        deadline = time.monotonic() + 15
        while True:
            try:
                self.state()
                return
            except OSError:
                assert self.process.poll() is None, self.process.stdout.read()
                assert time.monotonic() < deadline, "the daemon did not start"
                time.sleep(0.05)

    def request(self, method: str, path: str, body: dict | None = None) -> dict:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            raw = None if body is None else json.dumps(body)
            headers = {} if body is None else {"Content-Type": "application/json"}
            connection.request(method, f"/api/v1{path}", raw, headers)
            response = connection.getresponse()
            data = json.loads(response.read())
            assert response.status == 200, data
            return data
        finally:
            connection.close()

    def state(self) -> dict:
        return self.request("GET", "/state")

    def wait_for(self, predicate, timeout: float = 8.0) -> dict:
        deadline = time.monotonic() + timeout
        while True:
            state = self.state()
            if predicate(state):
                return state
            assert time.monotonic() < deadline, state
            time.sleep(0.05)

    def mpv_pids(self) -> list[int]:
        """Every mpv using this daemon's socket, whoever its parent is now."""
        listing = subprocess.run(
            ["ps", "-ww", "-eo", "pid=,args="], capture_output=True, text=True, check=True
        ).stdout
        marker = f"--input-ipc-server={self.socket_path}"
        return [int(line.split()[0]) for line in listing.splitlines() if marker in line]

    def overlay_pids(self) -> list[int]:
        """Every overlay that ``start --port`` on this port launched, alive or a zombie."""
        listing = subprocess.run(
            ["ps", "-ww", "-eo", "pid=,args="], capture_output=True, text=True, check=True
        ).stdout
        marker = f"-m playlist_player overlay --port {self.port}"
        return [int(line.split()[0]) for line in listing.splitlines() if marker in line]

    def signal_group(self, number: int) -> int:
        """Signal the whole process group, as a terminal does. Returns the exit status."""
        return self._signal(os.killpg, number)

    def signal_daemon(self, number: int) -> int:
        """Signal the daemon alone, as ``kill`` does. Returns the exit status."""
        return self._signal(os.kill, number)

    def _signal(self, send, number: int) -> int:
        send(self.process.pid, number)
        try:
            self.output, _ = self.process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            self.kill()
            raise AssertionError("the daemon did not stop") from None
        return self.process.returncode

    def kill(self) -> None:
        for pid in self.mpv_pids() + self.overlay_pids():
            os.kill(pid, signal.SIGKILL)
        if self.process.poll() is None:
            os.killpg(self.process.pid, signal.SIGKILL)
            self.process.wait(timeout=5)


@pytest.fixture
def serve(store, tmp_path_factory):
    build_library(store, audio_file=tone(tmp_path_factory.mktemp("tones"), 30, "opus"), seconds=30)
    # Unix socket paths are limited to about 100 bytes; pytest's tmp_path is too deep.
    runtime = Path(tempfile.mkdtemp(prefix="pp-"))
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    runs: list[Serve] = []

    def start(*command: str, **extra_env: str) -> Serve:
        runs.append(Serve(store, runtime, port, command or ("serve",), extra_env))
        return runs[-1]

    start.port = port

    yield start
    for run in runs:
        run.kill()
    shutil.rmtree(runtime, ignore_errors=True)


def saved(store) -> dict:
    return json.loads(store.session_json.read_text())


def play_some(run: Serve) -> None:
    """A song part-way through, at a volume that is not the default."""
    run.request("POST", "/player/load", {"playlist_id": PLAYLIST_ID, "start_track_id": "TRK0001"})
    run.wait_for(lambda s: s["status"] == "playing")
    run.request("POST", "/player/seek", {"position_s": 9.0})
    run.request("POST", "/player/volume", {"volume": 33})
    run.wait_for(lambda s: s["volume"] == 33 and s["status"] == "playing" and s["position_s"] >= 9)
    time.sleep(0.4)


@pytest.mark.parametrize("stop_signal", [signal.SIGINT, signal.SIGHUP, signal.SIGTERM])
def test_stopping_from_a_terminal_saves_the_volume_and_the_place_and_a_restart_resumes_them(
    serve, store, stop_signal
):
    """Ctrl-C, closing the terminal, or a plain kill: each reaches mpv as well as the daemon."""
    first = serve()
    play_some(first)
    assert len(first.mpv_pids()) == 1  # the check below must be able to see an mpv
    position_before = first.state()["position_s"]
    stopped_at = time.time()
    assert first.signal_group(stop_signal) == 0
    assert first.mpv_pids() == [], "mpv outlived the daemon"
    assert "exited unexpectedly" not in first.output and "Traceback" not in first.output

    document = saved(store)
    assert document["volume"] == 33.0, "the volume was not saved"
    assert document["track_id"] == "TRK0001"
    # The place it was at when it stopped, not a snapshot from seconds before.
    assert document["position_s"] == pytest.approx(position_before, abs=1.5)
    assert document["position_s"] >= position_before - 0.1

    second = serve()
    state = second.wait_for(lambda s: s["track_id"] == "TRK0001" and s["position_s"] > 1)
    assert (state["volume"], state["status"], state["paused"]) == (33.0, "paused", True)
    assert state["position_s"] == pytest.approx(document["position_s"], abs=0.2)
    assert time.time() - stopped_at < 30
    assert second.signal_group(stop_signal) == 0
    assert saved(store)["volume"] == 33.0  # and stopping again does not lose it


def test_if_mpv_dies_its_replacement_has_the_same_volume(serve, store):
    """A player that crashes must not come back at full volume, nor save that as the volume."""
    run = serve()
    play_some(run)
    (mpv,) = run.mpv_pids()
    os.kill(mpv, signal.SIGKILL)
    deadline = time.monotonic() + 8
    volumes = set()
    while time.monotonic() < deadline:
        volumes.add(run.state()["volume"])
        pids = run.mpv_pids()
        if pids and pids != [mpv] and run.state()["status"] == "idle":
            break
        time.sleep(0.02)
    assert volumes == {33.0}, f"the volume was reported as {sorted(volumes)} while mpv restarted"
    assert saved(store)["volume"] == 33.0

    run.request("POST", "/player/load", {"playlist_id": PLAYLIST_ID})
    state = run.wait_for(lambda s: s["status"] == "playing")
    assert state["volume"] == 33.0
    assert run.signal_group(signal.SIGTERM) == 0
    assert saved(store)["volume"] == 33.0


def mpv_property(run: Serve, name: str):
    """Ask this daemon's mpv for a property directly, over its IPC socket."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as link:
        link.settimeout(3)
        link.connect(str(run.socket_path))
        link.sendall(
            json.dumps({"command": ["get_property", name], "request_id": 7}).encode() + b"\n"
        )
        buffer = b""
        while True:
            buffer += link.recv(4096)
            for line in buffer.split(b"\n")[:-1]:
                reply = json.loads(line)
                if reply.get("request_id") == 7:
                    assert reply["error"] == "success", reply
                    return reply["data"]


def test_with_the_fade_in_set_a_resume_starts_silent_and_comes_up_when_played(serve, store):
    first = serve()
    play_some(first)
    first.request("POST", "/session/settings", {"fade_in_s": 1.5})
    assert mpv_property(first, "volume-gain") == 0.0  # what is playing carries on as it is
    first.request("POST", "/player/pause", {})
    first.wait_for(lambda s: s["status"] == "paused")
    time.sleep(0.3)
    assert mpv_property(first, "volume-gain") == pytest.approx(-60.0)  # set for the resume
    assert first.signal_group(signal.SIGINT) == 0

    second = serve()
    state = second.wait_for(lambda s: s["track_id"] == "TRK0001" and s["status"] == "paused")
    assert state["volume"] == 33.0  # the volume is as it was left; the fade is separate
    assert mpv_property(second, "volume-gain") == pytest.approx(-60.0)

    second.request("POST", "/player/play", {})
    second.wait_for(lambda s: s["status"] == "playing")
    time.sleep(0.5)
    part_way = mpv_property(second, "volume-gain")
    assert -60 < part_way < 0
    time.sleep(1.6)
    assert mpv_property(second, "volume-gain") == 0.0
    assert second.state()["volume"] == 33.0
    assert second.signal_group(signal.SIGINT) == 0
    assert saved(store)["volume"] == 33.0 and saved(store)["settings"] == {"fade_in_s": 1.5}


def test_start_carries_on_without_an_overlay_that_cannot_run_and_stops_like_serve(serve, store):
    """``start`` where there is no display: the page still works, and the reason is said once."""
    run = serve(
        "start", "--no-browser", "--port", str(serve.port),
        WAYLAND_DISPLAY="wl-there-is-no-such-display", GDK_BACKEND="wayland", DISPLAY="",
    )  # fmt: skip
    deadline = time.monotonic() + 15
    while run.overlay_pids():
        assert time.monotonic() < deadline, "the overlay neither ran nor gave up"
        time.sleep(0.05)
    time.sleep(1.5)  # the report waits a moment, in case everything is stopping together
    play_some(run)
    assert run.signal_group(signal.SIGHUP) == 0
    assert run.mpv_pids() == []
    assert run.output.count("the overlay stopped (exit status 1)") == 1
    assert "closing this terminal, stops playlist-player" in run.output
    assert "Traceback" not in run.output
    assert saved(store)["track_id"] == "TRK0001"
