"""The mpv-backed player: spawn mpv, drive it over IPC, publish exact state.

Threads:

* The IPC reader (in :mod:`mpv_ipc`) only routes: replies to waiting callers, everything
  else into an inbox.
* The state thread drains the inbox through the reducer. When the reducer says a
  snapshot is due it samples the position from mpv and publishes on the bus. Because it
  is not the reader, it is free to make that synchronous request.
* Transport methods are called from HTTP handler threads and talk to mpv directly.

Position accuracy. mpv's ``time-pos`` only moves about every 100 ms for audio, so a
snapshot built on it can be tens of milliseconds stale before it is even sent.
``audio-pts`` is continuous and accounts for the audio driver's delay, which makes it
"what is being heard now": that is what snapshots carry, stamped with the wall-clock
midpoint of the request that fetched it.

mpv is started locked down: no config file, no scripts except the MPRIS plugin when one
is configured, no network fetching, no video, no input bindings. Its IPC socket can run
programs as whoever owns mpv, so it is created 0600 inside a 0700 directory.
"""

from __future__ import annotations

import contextlib
import os
import queue
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..errors import PlaybackError
from ..models import PlayerStatus
from .mpv_ipc import MpvCommandError, MpvIpc
from .queue import PlayQueue
from .reducer import OBSERVED_PROPERTIES, MpvStateReducer
from .state import StateBus

HEARTBEAT_S = 5.0
# "Previous" restarts the current track when further in than this, as players usually do.
_PREV_RESTARTS_AFTER_S = 3.0

# sun_path is 108 bytes including the terminating NUL.
_MAX_SOCKET_PATH = 108

_CLOSED = object()  # inbox token: the IPC connection ended
_REFRESH = object()  # inbox token: publish a fresh snapshot now


class MpvPlayer:
    def __init__(
        self,
        bus: StateBus,
        *,
        socket_path: Path,
        mpv_bin: str = "mpv",
        audio_output: str = "",
        mpris_plugin: str = "",
        heartbeat_s: float = HEARTBEAT_S,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.bus = bus
        self._socket_path = Path(socket_path)
        # The queue is handed to mpv as a playlist file beside the socket.
        self._playlist_file = self._socket_path.with_suffix(".m3u")
        self._mpv_bin = mpv_bin
        self._audio_output = audio_output
        self._mpris_plugin = mpris_plugin
        self._heartbeat_s = heartbeat_s
        self._clock = clock

        self._reducer = MpvStateReducer()
        self._inbox: queue.Queue[Any] = queue.Queue()
        self._lock = threading.RLock()  # serializes transport commands and restarts
        self._process: subprocess.Popen[bytes] | None = None
        self._ipc: MpvIpc | None = None
        self._closing = False
        self._restart_available = True
        # A position to seek to once mpv reports the first file open (see load_queue).
        self._resume_at: float | None = None
        # True while the playlist is being rearranged; nothing is published meanwhile.
        self._reordering = False
        self._thread: threading.Thread | None = None

    # ------------------------------ lifecycle ------------------------------ #

    def start(self) -> None:
        """Spawn mpv, connect, and start publishing. Call once before anything else."""
        with self._lock:
            self._spawn()
        self._thread = threading.Thread(target=self._state_loop, name="mpv-state", daemon=True)
        self._thread.start()
        self._inbox.put(_REFRESH)

    def command_line(self) -> list[str]:
        args = [
            self._mpv_bin,
            "--idle=yes",
            "--no-config",
            "--no-video",
            "--no-terminal",
            "--force-window=no",
            "--load-scripts=no",
            "--ytdl=no",
            "--no-input-default-bindings",
            "--audio-display=no",
            "--audio-client-name=playlist-player",
            f"--input-ipc-server={self._socket_path}",
        ]
        if self._audio_output:
            args.append(f"--ao={self._audio_output}")
        if self._mpris_plugin:
            # An explicit --script still loads with --load-scripts=no; that flag only
            # stops mpv scanning its script directories.
            args.append(f"--script={self._mpris_plugin}")
        return args

    def _spawn(self) -> None:
        # A Unix socket path is limited to 108 bytes; say so rather than fail inside mpv.
        if len(str(self._socket_path).encode()) >= _MAX_SOCKET_PATH:
            raise PlaybackError(
                f"the mpv socket path is too long ({self._socket_path}); "
                "set a shorter XDG_RUNTIME_DIR"
            )
        directory = self._socket_path.parent
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(directory, 0o700)
        with contextlib.suppress(FileNotFoundError):
            self._socket_path.unlink()  # a stale socket from a run that did not exit cleanly
        try:
            # umask 077 so the socket is 0600 from the moment mpv creates it.
            self._process = subprocess.Popen(
                self.command_line(),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                umask=0o077,
            )
        except OSError as exc:
            raise PlaybackError(f"could not start mpv ({self._mpv_bin}): {exc}") from exc
        ipc = MpvIpc(
            self._socket_path,
            on_message=self._inbox.put,
            on_close=lambda: self._inbox.put(_CLOSED),
        )
        try:
            ipc.connect()
            for index, name in enumerate(OBSERVED_PROPERTIES, start=1):
                ipc.command("observe_property", index, name)
        except PlaybackError:
            self._kill_process()
            raise
        self._ipc = ipc

    def close(self) -> None:
        with self._lock:
            self._closing = True
            ipc, self._ipc = self._ipc, None
            if ipc is not None:
                with contextlib.suppress(PlaybackError):
                    ipc.command("quit", timeout=1.0)
                ipc.close()
            self._kill_process(grace_s=2.0)
            for leftover in (self._socket_path, self._playlist_file):
                with contextlib.suppress(FileNotFoundError):
                    leftover.unlink()
        self._inbox.put(_CLOSED)
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=3)

    def _kill_process(self, grace_s: float = 0.0) -> None:
        process, self._process = self._process, None
        if process is None:
            return
        try:
            process.wait(timeout=grace_s)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)

    # ------------------------------ transport ------------------------------ #

    def _command(self, *args: Any) -> Any:
        ipc = self._ipc
        if ipc is None:
            raise PlaybackError("the player is not running")
        return ipc.command(*args)

    def load_queue(
        self, queue: PlayQueue, *, paused: bool = False, position_s: float | None = None
    ) -> None:
        with self._lock:
            self._reducer.set_queue(queue)
            self._resume_at = position_s if position_s and position_s > 0 else None
            if not len(queue):
                self._command("stop")
                self._inbox.put(_REFRESH)
                return
            # One atomic command. Building the playlist entry by entry (stop, clear,
            # append, set position) races with mpv's own play loop: a "stop" is only
            # acted on later and can swallow the start that follows it, leaving mpv idle
            # with a full playlist. "loadlist ... replace" swaps the playlist and starts
            # the entry named by playlist-start in a single step.
            self._playlist_file.write_text(
                "".join(f"{entry.path}\n" for entry in queue.entries), encoding="utf-8"
            )
            self._command("set_property", "pause", paused)
            self._command("set_property", "playlist-start", queue.start_index)
            self._command("loadlist", str(self._playlist_file), "replace")

    def reorder(self, queue: PlayQueue) -> None:
        with self._lock:
            current = [entry.path for entry in self._reducer.queue.entries]
            wanted = [entry.path for entry in queue.entries]
            if sorted(current) != sorted(wanted):
                # Not the tracks mpv holds (it was restarted, say): load them instead.
                self._reload_keeping_place(queue)
                return
            # mpv moves entries within its own playlist, so the file that is playing is
            # never touched. Each move can shift the current entry's index, which would
            # publish a snapshot per move; hold those back and publish once at the end.
            self._reordering = True
            try:
                self._reducer.replace_queue(queue)
                for index, path in enumerate(wanted):
                    found = current.index(path, index)
                    if found != index:
                        self._command("playlist-move", found, index)
                        current.insert(index, current.pop(found))
            except MpvCommandError:
                self._reordering = False
                self._reload_keeping_place(queue)  # half moved: start over from a known state
            finally:
                self._reordering = False
                self._inbox.put(_REFRESH)

    def _reload_keeping_place(self, queue: PlayQueue) -> None:
        """Load ``queue`` at the track and position that are playing now. Lock held."""
        entry = self._reducer.current_entry()
        position = self._position()[0] if entry is not None else None
        paused = self._reducer.paused or self._reducer.idle
        resumed = PlayQueue(
            queue.entries,
            playlist_id=queue.playlist_id,
            start_track_id=entry.track_id if entry is not None else None,
        )
        self.load_queue(resumed, paused=paused, position_s=position)

    def play(self) -> None:
        with self._lock:
            if self._reducer.idle and len(self._reducer.queue):
                self._command("set_property", "playlist-pos", 0)
            self._command("set_property", "pause", False)

    def pause(self) -> None:
        self._command("set_property", "pause", True)

    def toggle(self) -> None:
        with self._lock:
            if self._reducer.idle or self._reducer.paused:
                self.play()
            else:
                self.pause()

    def next(self) -> None:
        # "weak": at the last track do nothing rather than stop playback. mpv reports
        # that as a failed command, which is not an error for us.
        with contextlib.suppress(MpvCommandError):
            self._command("playlist-next", "weak")

    def prev(self) -> None:
        with self._lock:
            position = self._position()[0] or 0.0
            index = self._reducer.current_index()
            if position > _PREV_RESTARTS_AFTER_S or index in (None, 0):
                self._seek_command(0.0, "absolute+exact")
            else:
                with contextlib.suppress(MpvCommandError):
                    self._command("playlist-prev", "weak")

    def seek(self, *, position_s: float | None = None, delta_s: float | None = None) -> None:
        if (position_s is None) == (delta_s is None):
            raise ValueError("give exactly one of position_s and delta_s")
        if position_s is not None:
            self._seek_command(max(0.0, position_s), "absolute+exact")
        else:
            self._seek_command(delta_s, "relative+exact")

    def _seek_command(self, amount: float, mode: str) -> None:
        # Seeking with nothing loaded is not an error worth surfacing; ignore it.
        with contextlib.suppress(MpvCommandError):
            self._command("seek", amount, mode)

    def set_volume(self, volume: float) -> None:
        self._command("set_property", "volume", max(0.0, min(100.0, float(volume))))

    def set_offset(self, track_id: str, offset_ms: int) -> None:
        if self._reducer.queue.set_offset(track_id, offset_ms):
            self._inbox.put(_REFRESH)

    def stop(self) -> None:
        self._command("stop", "keep-playlist")

    # ------------------------------ publishing ------------------------------ #

    def _position(self) -> tuple[float | None, int]:
        """Where playback is right now, and the wall-clock time (epoch ms) that is true for."""
        ipc = self._ipc
        for name in ("audio-pts", "time-pos"):
            if ipc is None:
                break
            before = self._clock()
            try:
                value = ipc.command("get_property", name, timeout=1.0)
            except PlaybackError:  # unavailable while nothing is loaded, or mpv is gone
                continue
            after = self._clock()
            if isinstance(value, int | float):
                return float(value), int((before + after) / 2 * 1000)
        return None, int(self._clock() * 1000)

    def _publish(self) -> None:
        position, captured_at = self._position()
        state = self.bus.publish(self._reducer.snapshot(position, captured_at))
        if state.status is PlayerStatus.PLAYING:
            self._restart_available = True  # mpv is evidently healthy again

    def _state_loop(self) -> None:
        last_publish = time.monotonic()
        while True:
            try:
                item = self._inbox.get(timeout=max(0.05, self._heartbeat_s))
            except queue.Empty:
                item = None

            if item is _CLOSED:
                if self._closing:
                    return
                if not self._recover():
                    return
                last_publish = time.monotonic()
                continue

            due = item is _REFRESH
            if isinstance(item, dict):
                if item.get("event") == "playback-restart":
                    self._seek_to_resume_point()
                due = self._reducer.apply(item)
            elif item is None:
                # Heartbeat: while playing, a fresh snapshot now and then corrects drift.
                playing = self._reducer.status() is PlayerStatus.PLAYING
                due = playing and time.monotonic() - last_publish >= self._heartbeat_s
            if due and not self._closing and not self._reordering:
                self._publish()
                last_publish = time.monotonic()

    def _seek_to_resume_point(self) -> None:
        """Seek to a resumed position once its file is open.

        Seeking straight after the load is not enough: mpv may still be opening the file,
        and the seek would land on the file before it. mpv sends ``playback-restart`` once
        the file is open, even when it starts paused, so the seek waits for that.
        """
        with self._lock:
            position, self._resume_at = self._resume_at, None
            if position is not None:
                with contextlib.suppress(MpvCommandError, PlaybackError):
                    self._command("seek", position, "absolute+exact")

    def _recover(self) -> bool:
        """mpv died without being asked to. Report it, then restart it once."""
        self._reducer.set_fatal("mpv exited unexpectedly")
        self.bus.publish(self._reducer.snapshot(None, int(self._clock() * 1000)))
        with self._lock:
            if self._closing or not self._restart_available:
                return False
            self._restart_available = False
            old, self._ipc = self._ipc, None
            if old is not None:
                old.close()
            self._kill_process()
            try:
                self._spawn()
            except PlaybackError:
                return False
            # The new mpv has an empty playlist; the old queue is gone with the old process.
            self._reducer.set_queue(PlayQueue(()))
            self._resume_at = None
            self._reducer.set_fatal(None)
        self._publish()
        return True
