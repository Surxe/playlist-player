"""The ``start`` command's wiring: what it launches, and that it takes it down again."""

from __future__ import annotations

import sys
import threading
import time
from types import SimpleNamespace

import pytest

from playlist_player import cli, launch
from playlist_player.config import load_settings, settings_arguments

SLEEP = [sys.executable, "-c", "import time; time.sleep(60)"]


def test_settings_flags_given_to_start_are_handed_on_as_given():
    args = cli.build_parser().parse_args(
        ["start", "--no-browser", "--port", "9100", "--overlay-anchor", "top-left"]
    )
    assert settings_arguments(args) == ["--port", "9100", "--overlay-anchor", "top-left"]
    command = launch.overlay_command(settings_arguments(args))
    assert command[:4] == [sys.executable, "-m", "playlist_player", "overlay"]
    # What is handed on parses as the overlay's own flags, to the same settings.
    assert load_settings(cli.build_parser().parse_args(command[3:])) == load_settings(args)


def test_a_running_overlay_is_stopped_and_that_is_not_reported(capfd):
    overlay = launch.Overlay(SLEEP, threading.Event())
    overlay.start()
    process = overlay._process
    assert process.poll() is None
    overlay.stop()
    assert process.poll() is not None
    time.sleep(0.1)
    assert capfd.readouterr().err == ""


def test_an_overlay_that_ends_by_itself_is_reported(capfd, monkeypatch):
    stopping = threading.Event()
    monkeypatch.setattr(stopping, "wait", lambda _timeout: False)  # nothing else is stopping
    overlay = launch.Overlay([sys.executable, "-c", "raise SystemExit(3)"], stopping)
    overlay.start()
    overlay._process.wait()
    deadline = time.monotonic() + 5
    err = ""
    while "overlay stopped" not in err and time.monotonic() < deadline:
        err += capfd.readouterr().err
        time.sleep(0.02)
    assert "the overlay stopped (exit status 3)" in err
    overlay.stop()  # nothing left to stop; must not raise


@pytest.fixture
def fake_serve(monkeypatch):
    """Stands in for the daemon: reports ready, then returns as if it had been stopped."""
    daemon = SimpleNamespace(stopping=threading.Event(), url="http://127.0.0.1:9100")
    seen = SimpleNamespace(daemon=daemon, calls=0, overlays=[])

    def serve(settings, *, on_ready):
        seen.calls += 1
        on_ready(daemon)
        seen.overlays = [o._process for o in started]
        assert all(p.poll() is None for p in seen.overlays)
        return 0

    started: list[launch.Overlay] = []
    real_start = launch.Overlay.start

    def start(self):
        started.append(self)
        real_start(self)

    monkeypatch.setattr(launch, "serve", serve)
    monkeypatch.setattr(launch.Overlay, "start", start)
    monkeypatch.setattr(launch, "overlay_command", lambda argv: [*SLEEP, *argv])
    monkeypatch.setattr(launch, "is_listening", lambda _settings: False)
    return seen


def test_start_launches_the_overlay_and_the_page_and_stops_the_overlay_with_the_daemon(
    fake_serve,
):
    opened: list[str] = []
    status = launch.run(load_settings(), settings_argv=["--port", "9100"], open_url=opened.append)
    assert status == 0 and opened == ["http://127.0.0.1:9100/"]
    (process,) = fake_serve.overlays
    assert process.args[-2:] == ["--port", "9100"]
    assert process.poll() is not None, "the overlay was left running"


def test_start_can_leave_out_the_overlay_and_the_page(fake_serve):
    opened: list[str] = []
    assert launch.run(load_settings(), overlay=False, open_page=False, open_url=opened.append) == 0
    assert fake_serve.calls == 1 and fake_serve.overlays == [] and opened == []


def test_started_a_second_time_it_only_opens_the_page(fake_serve, monkeypatch):
    monkeypatch.setattr(launch, "is_listening", lambda _settings: True)
    settings = load_settings()
    opened: list[str] = []
    assert launch.run(settings, open_url=opened.append) == 0
    assert fake_serve.calls == 0 and opened == [f"{settings.base_url}/"]
