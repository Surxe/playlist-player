"""Wiring for the ``start`` command: the daemon, the overlay and the page as one thing.

The daemon runs in this process, exactly as ``serve`` runs it. The overlay is a child
process: it has to load its layer-shell library before anything else is imported and it
owns a GTK main loop, so it cannot share this one. It is started once the daemon is
listening and stopped when the daemon stops, which makes one terminal hold the whole
application: Ctrl-C there, or closing it, ends all of it. In between it follows the
overlay switch on the page: switched off, the overlay process is stopped altogether, and
switched on, it is started again.
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
from .server.overlay_config import OverlayConfig

# How long a stopping overlay gets before it is killed.
_STOP_TIMEOUT_S = 3.0


def overlay_command(settings_argv: Sequence[str]) -> list[str]:
    """The overlay as a command line, on this interpreter and with these settings flags."""
    return [sys.executable, "-m", "spotifixed", "overlay", *settings_argv]


class Overlay:
    """The overlay as a child process that follows the daemon's on/off switch.

    It runs while the switch is on, for as long as the daemon runs. Switched off, the
    process is stopped, not hidden, so nothing of the overlay is left running; switched on
    again, a new one is started. An overlay that cannot start, or that ends by itself, is
    reported (on the terminal and on the page) and left alone until it is switched off
    and on again: the daemon and the page are worth having without it.
    """

    def __init__(
        self, command: Sequence[str], config: OverlayConfig, stopping: threading.Event
    ) -> None:
        self._command = list(command)
        self._config = config
        self._stopping = stopping
        # One start or stop at a time; reentrant because a report wakes the listener.
        self._acting = threading.RLock()
        self._following = False  # the switch position last acted on
        self._process: subprocess.Popen[bytes] | None = None
        self._unsubscribe: Callable[[], None] | None = None

    def follow(self) -> None:
        """Follow the switch from now on, starting the overlay at once if it is on."""
        self._config.set_managed(True)
        self._unsubscribe = self._config.subscribe(self._sync)
        if not self._config.enabled:
            print(
                "The lyric overlay is switched off; it can be switched on in the page's Settings.",
                flush=True,
            )
        self._sync()

    def _sync(self) -> None:
        # Called after every change to the overlay document, so it acts only on the switch.
        enabled = self._config.enabled
        with self._acting:
            if self._stopping.is_set() or enabled == self._following:
                return
            self._following = enabled
            if enabled:
                self._start()
            else:
                self._end()

    def _start(self) -> None:
        env = dict(os.environ)
        if sys.flags.no_user_site:  # the child gets no more to import than this process
            env["PYTHONNOUSERSITE"] = "1"
        process = subprocess.Popen(self._command, env=env)
        self._process = process
        threading.Thread(target=self._watch, args=(process,), daemon=True).start()

    def _watch(self, process: subprocess.Popen[bytes]) -> None:
        status = process.wait()
        # Closing the terminal ends the overlay and the daemon together, and the daemon
        # takes a moment to say so; that is not an overlay that stopped by itself.
        if self._stopping.wait(1.0):
            return
        with self._acting:
            if process is not self._process:
                return  # switched off, and so stopped on purpose
            self._process = None
        print(
            f"spotifixed: the overlay stopped (exit status {status}); "
            "the daemon and the page carry on without it",
            file=sys.stderr,
            flush=True,
        )
        self._config.report_problem(
            "The overlay stopped unexpectedly; the spotifixed window says why. "
            "Switch it off and on again to retry."
        )

    def _end(self) -> None:
        """Stop the overlay process, if one is running. Called with ``_acting`` held."""
        process, self._process = self._process, None
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=_STOP_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()

    def stop(self) -> None:
        """Stop following the switch and stop the overlay, as the daemon stops."""
        self._stopping.set()
        if self._unsubscribe is not None:
            self._unsubscribe()
        with self._acting:
            self._end()


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
    """Run the daemon with the overlay beside it until stopped; open the page once it is up.

    With ``overlay`` false the overlay is neither started nor switchable from the page.
    """
    if open_page and is_listening(settings):
        # Started a second time, e.g. from a launcher: show what is already running.
        print(f"spotifixed is already running; opening {settings.base_url}/", flush=True)
        open_url(f"{settings.base_url}/")
        return 0

    children: list[Overlay] = []

    def ready(daemon: Daemon) -> None:
        if overlay:
            child = Overlay(overlay_command(settings_argv), daemon.overlay, daemon.stopping)
            children.append(child)
            child.follow()
        if open_page:
            open_url(f"{daemon.url}/")
        print("Ctrl-C, or closing this terminal, stops spotifixed.", flush=True)

    try:
        return serve(settings, on_ready=ready)
    finally:
        for child in children:
            # After the terminal is gone there is nowhere to report a failure to.
            with contextlib.suppress(Exception):
                child.stop()
