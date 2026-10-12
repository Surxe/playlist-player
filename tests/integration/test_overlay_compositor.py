"""The overlay inside a real compositor: a headless KWin with no display.

What this can show without a graphical session: the surface is a layer-shell surface on
the overlay layer, sits where the settings put it, is mapped while a line is due and
unmapped otherwise, and (asking KWin itself) lands on KWin's overlay layer at the
expected geometry. What it cannot show is how it looks above a real fullscreen game;
that needs a person.
"""

from __future__ import annotations

import json
import os
import shutil
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from playlist_player.config import REPO_ROOT, load_settings
from playlist_player.server.app import Daemon
from tests.support.library import PLAYLIST_ID, build_library
from tests.support.playback import FakePlayer

KWIN = REPO_ROOT / "scripts" / "headless-kwin.sh"
PROBE = REPO_ROOT / "tests" / "support" / "overlay_probe.py"
RUNTIME = Path(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}")
SCREEN = (1920, 1080)
KWIN_OVERLAY_LAYER = 9
KWIN_ACTIVE_LAYER = 5  # where KWin puts the active fullscreen window


def _layer_shell_typelib() -> bool:
    code = (
        "import gi; gi.require_version('Gtk4LayerShell', '1.0'); gi.require_version('Gtk', '4.0')"
    )
    return subprocess.run([sys.executable, "-c", code], capture_output=True).returncode == 0


pytestmark = [
    pytest.mark.wayland,
    pytest.mark.skipif(
        shutil.which("kwin_wayland") is None, reason="kwin_wayland is not installed"
    ),
    pytest.mark.skipif(not RUNTIME.is_dir(), reason="no XDG runtime directory for this user"),
    pytest.mark.skipif(not _layer_shell_typelib(), reason="GTK4 layer-shell bindings missing"),
]


def kwin(*args: str, **kwargs) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "XDG_RUNTIME_DIR": str(RUNTIME)}
    return subprocess.run(
        ["bash", str(KWIN), *args], capture_output=True, text=True, env=env, timeout=60, **kwargs
    )


@pytest.fixture(scope="module")
def compositor():
    already = kwin("status").returncode == 0
    started = kwin("start")
    if started.returncode != 0:
        pytest.skip(f"the headless compositor did not start: {started.stderr.strip()[-200:]}")
    yield
    if not already:
        kwin("stop")


def child_env(tmp_path, **settings: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("PLAYLIST_PLAYER_")}
    env.update(XDG_RUNTIME_DIR=str(RUNTIME), PYTHONPATH=str(REPO_ROOT))
    env.pop("APIFY_TOKEN", None)
    env.update({f"PLAYLIST_PLAYER_{name}": value for name, value in settings.items()})
    return env


def png_size(path: Path) -> tuple[int, int]:
    header = path.read_bytes()[:24]
    assert header[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    return struct.unpack(">II", header[16:24])


def wait_until(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "condition not met in time"
        time.sleep(0.01)


# --------------------------------- snapshot --------------------------------- #


def snapshot(tmp_path, name: str, **settings: str) -> tuple[int, int]:
    out = tmp_path / name
    result = subprocess.run(
        ["bash", str(KWIN), "run", sys.executable, "-m", "playlist_player", "overlay",
         "--demo", "--snapshot", str(out)],
        capture_output=True, text=True, timeout=60, cwd=tmp_path,
        env=child_env(tmp_path, **settings),
    )  # fmt: skip
    assert result.returncode == 0, result.stderr[-500:]
    assert f"wrote {out}" in result.stdout
    return png_size(out)


def test_snapshot_renders_one_row_within_the_width_limit(compositor, tmp_path):
    width, height = snapshot(tmp_path, "one.png")
    assert 600 < width <= 1200 + 40 and 60 < height < 120  # a single row of 32 pt text


def test_a_narrow_limit_wraps_and_two_line_mode_adds_a_row(compositor, tmp_path):
    width, height = snapshot(tmp_path, "narrow.png", OVERLAY_MAX_WIDTH="500")
    assert width <= 500 + 40 and height > 120
    two_width, two_height = snapshot(tmp_path, "two.png", OVERLAY_LINES="2")
    one_width, one_height = snapshot(tmp_path, "one.png")
    assert two_height > one_height + 30


def test_font_size_and_colours_are_applied_without_error(compositor, tmp_path):
    small = snapshot(tmp_path, "small.png", OVERLAY_FONT_SIZE="16", OVERLAY_COLOR="#FFE066",
                     OVERLAY_OUTLINE_COLOR="#102030", OVERLAY_FONT="Serif Italic")  # fmt: skip
    big = snapshot(tmp_path, "big.png", OVERLAY_FONT_SIZE="40")
    assert small[1] < big[1] and small[0] < big[0]


def test_bad_settings_and_no_display_are_one_clear_line(compositor, tmp_path):
    def run(env):
        command = [sys.executable, "-m", "playlist_player", "overlay", "--snapshot"]
        return subprocess.run(
            [*command, str(tmp_path / "x.png")],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=tmp_path,
            env=env,
        )

    bad_colour = child_env(tmp_path, OVERLAY_COLOR="red; } * { color: blue")
    bad_colour.update(WAYLAND_DISPLAY="wl-pp-test", GDK_BACKEND="wayland")
    result = run(bad_colour)
    assert result.returncode == 1 and "OVERLAY_COLOR must be a colour" in result.stderr
    assert "Traceback" not in result.stderr

    nowhere = child_env(tmp_path)
    nowhere.update(WAYLAND_DISPLAY="wl-there-is-no-such-display", GDK_BACKEND="wayland")
    nowhere.pop("DISPLAY", None)
    result = run(nowhere)
    assert result.returncode == 1 and "no display" in result.stderr
    assert "Traceback" not in result.stderr and not (tmp_path / "x.png").exists()


# ------------------------------ live behaviour ------------------------------ #


def test_the_overlay_follows_playback_on_the_overlay_layer(
    compositor, store, tmp_path, monkeypatch
):
    monkeypatch.setenv("PLAYLIST_PLAYER_CACHE_DIR", str(store.cache_dir))
    monkeypatch.setenv("PLAYLIST_PLAYER_STATE_DIR", str(store.state_dir))
    build_library(store)
    player = FakePlayer()
    daemon = Daemon(load_settings(), store, player, port=0, keepalive_s=0.5)
    threading.Thread(target=daemon.serve_forever, daemon=True).start()

    out = tmp_path / "probe.json"
    probe = subprocess.Popen(
        ["bash", str(KWIN), "run", sys.executable, str(PROBE), str(out), "10"],
        cwd=tmp_path, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        env=child_env(
            tmp_path, PORT=str(daemon.port), OVERLAY_ANCHOR="bottom-right",
            OVERLAY_MARGIN_X="40", OVERLAY_MARGIN_Y="90", OVERLAY_MONITOR="Virtual-0",
            OVERLAY_LEAD_MS="150", OVERLAY_FADE_MS="0",
        ),
    )  # fmt: skip
    marks: dict[str, float] = {}
    try:
        wait_until(lambda: daemon.open_streams == 1, timeout=15)
        time.sleep(0.3)  # idle: samples that must show nothing

        # The synthetic lyrics: a line at 1.0 s, a gap at 2.5 s, a line at 4.0 s.
        player.load_queue(daemon.routes._library.queue_for(PLAYLIST_ID))
        base = player.bus.current.captured_at_epoch_ms / 1000  # position 0 at this instant
        marks["base"] = base

        time.sleep(max(0, base + 1.6 - time.time()))
        windows = json.loads(kwin("windows", check=True).stdout)

        time.sleep(max(0, base + 4.5 - time.time()))
        marks["pause"] = time.time()
        player.pause()
        time.sleep(0.5)
        player.position = 4.2
        marks["resume"] = time.time()
        player.play()
        time.sleep(0.5)
        marks["drop"] = time.time()
        daemon.shutdown()
        time.sleep(0.6)
    finally:
        out.with_suffix(".stop").touch()
        try:
            _, stderr = probe.communicate(timeout=20)
        except subprocess.TimeoutExpired:
            probe.kill()
            _, stderr = probe.communicate()
        if not daemon.stopping.is_set():
            daemon.shutdown()
    assert probe.returncode == 0, stderr[-800:]

    recorded = json.loads(out.read_text())
    samples = recorded["samples"]
    assert recorded["monitors"] == ["Virtual-0"]
    base = marks["base"]

    def between(start: float, end: float) -> list[dict]:
        return [s for s in samples if start <= s["t"] <= end]

    def first(predicate, after: float) -> dict:
        return next(s for s in samples if s["t"] >= after and predicate(s))

    # What the surface is, from inside the client.
    shown = first(lambda s: s["mapped"], base)
    assert shown["is_layer_window"] is True
    assert (shown["layer"], shown["namespace"]) == ("overlay", "playlist-player-lyrics")
    assert (shown["keyboard_mode"], shown["exclusive_zone"]) == ("none", -1)
    assert shown["anchors"] == ["bottom", "right"]
    assert shown["margins"] == {"top": 0, "bottom": 90, "left": 0, "right": 40}
    assert shown["monitor"] == "Virtual-0"

    # Hidden while idle and before the first line; the line appears at 1.0 s less the lead.
    assert not any(s["mapped"] for s in samples if s["t"] < base + 0.75)
    assert base + 0.80 <= shown["t"] <= base + 1.10, shown["t"] - base
    assert shown["text"] == "an invented first line"
    assert all(s["mapped"] and s["text"] == "an invented first line"
               for s in between(base + 1.15, base + 2.25))  # fmt: skip
    # The gap line: unmapped, not an empty window.
    assert between(base + 2.50, base + 3.75) and not any(
        s["mapped"] for s in between(base + 2.50, base + 3.75)
    )
    assert all(s["mapped"] and s["text"] == "an invented last line"
               for s in between(base + 4.05, marks["pause"] - 0.02))  # fmt: skip

    # Pause hides it at once; resume brings it back; losing the daemon hides it.
    hidden = first(lambda s: not s["mapped"], marks["pause"])
    assert hidden["t"] - marks["pause"] < 0.15, hidden["t"] - marks["pause"]
    assert not any(s["mapped"] for s in between(hidden["t"], marks["resume"]))
    back = first(lambda s: s["mapped"], marks["resume"])
    assert back["t"] - marks["resume"] < 0.25 and back["text"] == "an invented last line"
    gone = first(lambda s: not s["mapped"], marks["drop"])
    assert gone["t"] - marks["drop"] < 0.8
    assert not any(s["mapped"] for s in samples if s["t"] > gone["t"])

    # And from outside: KWin's own account of its window stack, taken at 1.6 s.
    ours = [w for w in windows if w["layer"] == KWIN_OVERLAY_LAYER]
    assert len(ours) == 1, windows
    assert KWIN_OVERLAY_LAYER > KWIN_ACTIVE_LAYER
    x, y, width, height = ours[0]["geometry"]
    assert (x + width, y + height) == (SCREEN[0] - 40, SCREEN[1] - 90)
    # The size the client had been given by the time KWin was asked (the very first
    # mapped sample is taken before the first allocation).
    settled = first(lambda s: s["mapped"], base + 1.5)
    assert (width, height) == (settled["width"], settled["height"])
