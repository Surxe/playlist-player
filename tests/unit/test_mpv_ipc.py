"""The IPC client against a scripted Unix socket peer; no mpv involved."""

from __future__ import annotations

import json
import shutil
import socket
import tempfile
import threading
from pathlib import Path

import pytest

from spotifixed.errors import PlaybackError
from spotifixed.playback.mpv_ipc import MpvCommandError, MpvIpc


class Peer:
    """Plays mpv's side: answers each request through ``reply`` and can push events."""

    def __init__(self, path: Path, reply):
        self.reply = reply
        self.received = []
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(str(path))
        self.server.listen(1)
        self.connection = None
        self.ready = threading.Event()
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        self.connection, _ = self.server.accept()
        self.ready.set()
        buffer = b""
        while True:
            try:
                chunk = self.connection.recv(4096)
            except OSError:
                return
            if not chunk:
                return
            buffer += chunk
            *lines, buffer = buffer.split(b"\n")
            for line in lines:
                request = json.loads(line)
                self.received.append(request)
                answer = self.reply(request)
                if answer is not None:
                    self.send({**answer, "request_id": request["request_id"]})

    def send(self, message):
        self.ready.wait(2)
        self.connection.sendall((json.dumps(message) + "\n").encode())

    def hang_up(self):
        self.ready.wait(2)
        self.connection.shutdown(socket.SHUT_RDWR)
        self.connection.close()


@pytest.fixture
def sock_path():
    directory = Path(tempfile.mkdtemp(prefix="pp-"))
    yield directory / "s"
    shutil.rmtree(directory, ignore_errors=True)


def connect(sock_path, reply, **kwargs):
    peer = Peer(sock_path, reply)
    messages, closed = [], threading.Event()
    got_message = threading.Event()

    def on_message(message):
        messages.append(message)
        got_message.set()

    ipc = MpvIpc(sock_path, on_message=on_message, on_close=closed.set, **kwargs)
    ipc.connect(timeout=2)
    return peer, ipc, messages, closed, got_message


def test_command_returns_data_and_sends_newline_delimited_json(sock_path):
    peer, ipc, *_ = connect(sock_path, lambda r: {"error": "success", "data": 42.5})
    assert ipc.command("get_property", "audio-pts") == 42.5
    assert peer.received == [{"command": ["get_property", "audio-pts"], "request_id": 1}]
    assert ipc.command("set_property", "pause", True) == 42.5
    assert peer.received[1]["request_id"] == 2
    ipc.close()


def test_an_error_reply_raises_with_mpvs_reason(sock_path):
    _, ipc, *_ = connect(sock_path, lambda r: {"error": "property unavailable"})
    with pytest.raises(MpvCommandError, match="property unavailable"):
        ipc.command("get_property", "time-pos")
    ipc.close()


def test_events_go_to_the_handler_and_do_not_disturb_replies(sock_path):
    peer, ipc, messages, _, got = connect(sock_path, lambda r: {"error": "success", "data": "ok"})
    # An event carrying an id (observe_property's) must not be mistaken for a reply.
    peer.send({"event": "property-change", "id": 1, "name": "pause", "data": True})
    peer.send({"event": "playback-restart"})
    assert ipc.command("anything") == "ok"
    got.wait(2)
    for _ in range(100):
        if len(messages) == 2:
            break
        threading.Event().wait(0.01)
    assert [m["event"] for m in messages] == ["property-change", "playback-restart"]
    ipc.close()


def test_concurrent_callers_each_get_their_own_reply(sock_path):
    _, ipc, *_ = connect(sock_path, lambda r: {"error": "success", "data": r["command"][1]})
    results = {}

    def ask(n):
        results[n] = ipc.command("echo", n)

    threads = [threading.Thread(target=ask, args=(n,)) for n in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)
    assert results == {n: n for n in range(20)}
    ipc.close()


def test_no_reply_times_out(sock_path):
    _, ipc, *_ = connect(sock_path, lambda r: None)
    with pytest.raises(PlaybackError, match="did not answer 'stall'"):
        ipc.command("stall", timeout=0.1)
    ipc.close()


def test_peer_hanging_up_wakes_waiters_and_reports_close_once(sock_path):
    peer, ipc, _, closed, _ = connect(sock_path, lambda r: None)
    failures = []

    def waiter():
        try:
            ipc.command("never answered", timeout=5)
        except PlaybackError as exc:
            failures.append(str(exc))

    thread = threading.Thread(target=waiter)
    thread.start()
    threading.Event().wait(0.1)
    peer.hang_up()
    thread.join(3)
    assert failures == ["mpv IPC closed while waiting for a reply"]
    assert closed.wait(2) and ipc.closed
    with pytest.raises(PlaybackError, match="not connected"):
        ipc.command("anything")


def test_a_deliberate_close_does_not_report_itself(sock_path):
    _, ipc, _, closed, _ = connect(sock_path, lambda r: {"error": "success"})
    ipc.close()
    assert not closed.wait(0.2)


def test_connect_gives_up_when_there_is_no_socket(sock_path):
    ipc = MpvIpc(sock_path, on_message=lambda m: None)
    with pytest.raises(PlaybackError, match="did not open its IPC socket"):
        ipc.connect(timeout=0.1)


def test_garbage_lines_are_ignored(sock_path):
    peer, ipc, messages, *_ = connect(sock_path, lambda r: {"error": "success", "data": 1})
    peer.ready.wait(2)
    peer.connection.sendall(b"not json\n[1,2]\n\n")
    assert ipc.command("still works") == 1 and messages == []
    ipc.close()
