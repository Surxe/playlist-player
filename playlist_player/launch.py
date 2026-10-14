"""Wiring for the ``start`` command: the daemon, the overlay and the page as one thing.

The daemon runs in this process, exactly as ``serve`` runs it. The overlay is a child
process: it has to load its layer-shell library before anything else is imported and it
owns a GTK main loop, so it cannot share this one. It is started once the daemon is
listening and stopped when the daemon stops, which makes one terminal hold the whole
application: Ctrl-C there, or closing it, ends all of it.
"""

from __future__ import annotations

import contextlib
import os
import socket
import subprocess
import sys
import threading
import webbrowser
from collections.abc import Callable, Sequence

from .config import Settings
from .server.app import Daemon
from .server.app import run as serve

# How long a stopping overlay gets before it is killed.
_STOP_TIMEOUT_S = 3.0


def overlay_command(settings_argv: Sequence[str]) -> list[str]:
    """The overlay as a command line, on this interpreter and with these settings flags."""
    return [sys.executable, "-m", "playlist_player", "overlay", *settings_argv]


class Overlay:
    """The overlay as a child process, for as long as the daemon runs.

    An overlay that cannot start, or that ends by itself, is reported and otherwise left
    alone: the daemon and the page are worth having without it.
    """

    def __init__(self, command: Sequence[str], stopping: threading.Event) -> None:
        self._command = list(command)
        self._stopping = stopping
        self._process: subprocess.Popen[bytes] | None = None

    def start(self) -> None:
        env = dict(os.environ)
        if sys.flags.no_user_site:  # the child gets no more to import than this process
            env["PYTHONNOUSERSITE"] = "1"
        self._process = subprocess.Popen(self._command, env=env)
        threading.Thread(target=self._watch, args=(self._process,), daemon=True).start()

    def _watch(self, process: subprocess.Popen[bytes]) -> None:
        status = process.wait()
        # Closing the terminal ends the overlay and the daemon together, and the daemon
        # takes a moment to say so; that is not an overlay that stopped by itself.
        if self._stopping.wait(1.0):
            return
        print(
            f"playlist_player: the overlay stopped (exit status {status}); "
            "the daemon and the page carry on without it",
            file=sys.stderr,
            flush=True,
        )

    def stop(self) -> None:
        self._stopping.set()
        process = self._process
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=_STOP_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def is_listening(settings: Settings) -> bool:
    """Whether something already answers on the daemon's address."""
    try:
        with socket.create_connection((settings.host, settings.port), timeout=0.5):
            return True
    except OSError:
        return False


def run(
    settings: Settings,
    *,
    settings_argv: Sequence[str] = (),
    overlay: bool = True,
    open_page: bool = True,
    open_url: Callable[[str], object] = webbrowser.open,
) -> int:
    """Run the daemon with the overlay beside it until stopped; open the page once it is up."""
    if open_page and is_listening(settings):
        # Started a second time, e.g. from a launcher: show what is already running.
        print(f"playlist-player is already running; opening {settings.base_url}/", flush=True)
        open_url(f"{settings.base_url}/")
        return 0

    children: list[Overlay] = []

    def ready(daemon: Daemon) -> None:
        if overlay:
            child = Overlay(overlay_command(settings_argv), daemon.stopping)
            children.append(child)
            child.start()
        if open_page:
            open_url(f"{daemon.url}/")
        print("Ctrl-C, or closing this terminal, stops playlist-player.", flush=True)

    try:
        return serve(settings, on_ready=ready)
    finally:
        for child in children:
            # After the terminal is gone there is nowhere to report a failure to.
            with contextlib.suppress(Exception):
                child.stop()
