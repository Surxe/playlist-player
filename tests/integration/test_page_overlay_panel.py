"""The overlay settings panel in a real browser, against a daemon with invented data.

Headless Chrome loads the page and is driven over the DevTools protocol (see
``tests/js/drive_page.mjs``): it clicks, drags and types as a person would, and the
assertions are on what the daemon then holds, since that is what a running overlay
follows. Nothing here needs a display.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

import pytest

from playlist_player.config import REPO_ROOT, load_settings
from playlist_player.server.app import Daemon
from tests.support.library import build_library
from tests.support.playback import FakePlayer

DRIVER = REPO_ROOT / "tests" / "js" / "drive_page.mjs"
CHROME = shutil.which("google-chrome") or shutil.which("chromium")
NODE = shutil.which("node")


def _node_has_websocket() -> bool:
    if NODE is None:
        return False
    check = subprocess.run(
        [NODE, "-e", "process.exit(typeof WebSocket === 'function' ? 0 : 1)"],
        capture_output=True,
    )
    return check.returncode == 0


pytestmark = [
    pytest.mark.skipif(CHROME is None, reason="no Chrome or Chromium to load the page in"),
    pytest.mark.skipif(not _node_has_websocket(), reason="node 22 or newer is needed"),
]


@pytest.fixture
def daemon(store, monkeypatch):
    monkeypatch.setenv("PLAYLIST_PLAYER_CACHE_DIR", str(store.cache_dir))
    monkeypatch.setenv("PLAYLIST_PLAYER_STATE_DIR", str(store.state_dir))
    monkeypatch.setenv("PLAYLIST_PLAYER_OVERLAY_MARGIN_Y", "120")
    build_library(store)
    daemon = Daemon(load_settings(), store, FakePlayer(), port=0, keepalive_s=0.5)
    threading.Thread(target=daemon.serve_forever, daemon=True).start()
    yield daemon
    daemon.shutdown()


def drive(daemon: Daemon, tmp_path: Path, steps: list[dict]) -> list:
    env = {k: v for k, v in os.environ.items() if not k.startswith("PLAYLIST_PLAYER_")}
    env.update(CHROME=CHROME, HOME=str(tmp_path), XDG_RUNTIME_DIR=str(tmp_path))
    result = subprocess.run(
        [NODE, str(DRIVER), f"{daemon.url}/", str(tmp_path / "chrome-profile")],
        input=json.dumps(steps),
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )
    assert result.returncode == 0, result.stderr[-1500:]
    return json.loads(result.stdout)


# Helpers installed in the page: act on a control the way a browser reports a person
# doing it, and read back what the panel shows.
HELPERS = """
window.t = {
  el: (id) => document.getElementById(id),
  field: (key) => document.getElementById('overlay-field-' + key),
  row: (key) => document.getElementById('overlay-field-' + key).closest('.row'),
  beside: (label) => document.querySelector(`#overlay-form input[aria-label="${label}"]`),
  text: (id) => document.getElementById(id).textContent,
  values: (...keys) => keys.map((key) => document.getElementById('overlay-field-' + key).value),
  overridden: (...keys) => keys.map((key) => t.row(key).classList.contains('overridden')),
  type: (element, value) => {
    element.focus();
    element.value = value;
    element.dispatchEvent(new Event('input', { bubbles: true }));
    element.dispatchEvent(new Event('change', { bubbles: true }));
    element.blur();
  },
  drag: (element, value) => {
    element.value = value;
    element.dispatchEvent(new Event('input', { bubbles: true }));
  },
  pause: (ms) => new Promise((done) => setTimeout(done, ms)),
};
"""
OPEN = [
    {"wait": "document.getElementById('playlists').options.length"},
    {"eval": HELPERS},
    {"eval": "t.el('overlay-open').click()"},
    {"wait": "document.querySelectorAll('#overlay-form .row').length === 12"},
]


def test_the_panel_shows_the_settings_and_changes_reach_the_daemon(daemon, tmp_path, store):
    shot = tmp_path / "panel.png"
    steps = [
        {"eval": "document.getElementById('overlay-pane').hidden"},
        *OPEN,
        {"eval": "[t.values('margin_y')[0], t.text('overlay-status')]"},
        {"eval": "t.el('overlay-reset').disabled"},
        # A number box, a select, a text box, then a slider and a colour picker.
        {"eval": "t.type(t.field('margin_y'), '260')"},
        {"eval": "t.type(t.field('anchor'), 'top-right')"},
        {"eval": "t.type(t.field('font'), 'Serif Italic')"},
        {"eval": "t.drag(t.beside('Font size'), '48')"},
        {"eval": "t.drag(t.beside('Text colour'), '#ffe066')"},
        {"wait": "t.text('overlay-lines').includes('COLOR=#ffe066')"},
        {"eval": "t.text('overlay-lines')"},
        {"eval": "t.values('font_size', 'color')"},  # the slider's and the picker's partners
        {"eval": "t.overridden('margin_y', 'margin_x', 'font')"},
        {"eval": "t.el('overlay-reset').disabled"},
        {"screenshot": str(shot)},
    ]  # fmt: skip
    results = drive(daemon, tmp_path, steps)
    assert results[0] is True
    assert results[len(OPEN) + 1 : len(OPEN) + 3] == [["120", "overlay not running"], True]

    lines, partners, overridden, reset_disabled, _shot = results[-5:]
    assert lines.splitlines() == [
        "PLAYLIST_PLAYER_OVERLAY_ANCHOR=top-right",
        "PLAYLIST_PLAYER_OVERLAY_MARGIN_Y=260",
        "PLAYLIST_PLAYER_OVERLAY_FONT_SIZE=48",
        'PLAYLIST_PLAYER_OVERLAY_FONT="Serif Italic"',
        "PLAYLIST_PLAYER_OVERLAY_COLOR=#ffe066",
    ]
    assert partners == ["48", "#ffe066"]
    assert overridden == [True, False, True] and reset_disabled is False
    assert shot.stat().st_size > 10_000

    held = daemon.overlay.snapshot()
    assert held["overrides"] == {
        "anchor": "top-right", "margin_y": 260, "font": "Serif Italic", "font_size": 48,
        "color": "#ffe066",
    }  # fmt: skip
    assert json.loads(store.overlay_json.read_text())["overrides"] == held["overrides"]


def test_dropping_one_override_resetting_all_and_a_refused_value(daemon, tmp_path):
    daemon.overlay.update({"margin_x": 75, "lines": 2, "fade_ms": 0})
    steps = [
        *OPEN,
        {"eval": "t.values('margin_x', 'lines', 'fade_ms')"},
        # The x on a row puts that one setting back.
        {"eval": "t.row('lines').querySelector('.drop').click()"},
        {"wait": "!t.overridden('lines')[0]"},
        {"eval": "t.values('lines')"},
        # A font name the daemon refuses: the reason is shown and the box goes back.
        {"eval": "t.type(t.field('font'), 'Sans; rm -rf')"},
        {"wait": "t.text('overlay-note').includes('font must be')"},
        {"eval": "t.values('font')"},
        {"eval": "t.el('overlay-reset').click()"},
        {"wait": "t.el('overlay-reset').disabled"},
        {"eval": "t.values('margin_x', 'fade_ms')"},
        {"eval": "t.text('overlay-lines')"},
    ]
    results = drive(daemon, tmp_path, steps)[len(OPEN) :]
    assert results[0] == ["75", "2", "0"]
    assert results[3] == ["1"]
    assert results[6] == ["Sans Bold"]
    assert results[-2] == ["0", "120"]
    assert results[-1].startswith("Nothing is overridden")
    assert daemon.overlay.snapshot()["overrides"] == {}


def test_a_change_made_elsewhere_and_the_overlay_count_appear_without_reloading(daemon, tmp_path):
    def later():
        time.sleep(1.5)
        daemon.overlay.attach()
        daemon.overlay.update({"margin_y": 333})

    threading.Thread(target=later, daemon=True).start()
    steps = [
        *OPEN,
        {"wait": "t.values('margin_y')[0] === '333'"},
        {"eval": "t.text('overlay-status')"},
    ]
    assert drive(daemon, tmp_path, steps)[-1] == "overlay running"


def test_the_sample_line_is_asked_for_kept_alive_and_withdrawn(daemon, tmp_path):
    seen: list[int] = []
    daemon.overlay.subscribe(lambda: seen.append(daemon.overlay.snapshot()["preview_until_ms"]))
    started_ms = time.time() * 1000
    space = "new KeyboardEvent('keydown', { key: ' ', bubbles: true })"
    steps = [
        *OPEN,
        {"eval": "t.el('overlay-preview').click()"},
        {"eval": "t.pause(3600)"},  # long enough for one renewal
        # Space in a text box types a space; it must not reach the transport keys.
        {"eval": f"(t.field('font').focus(), t.field('font').dispatchEvent({space}))"},
        {"eval": "t.el('overlay-close').click()"},
        {"wait": "t.el('overlay-pane').hidden"},
        {"eval": "t.pause(300)"},
    ]
    drive(daemon, tmp_path, steps)
    asked = [value for value in seen if value]
    assert len(asked) >= 2 and asked == sorted(asked)  # asked, then renewed
    assert 6000 < asked[0] - started_ms < 14000  # a few seconds ahead, never open-ended
    assert seen[-1] == 0  # closing the panel takes the sample line down
    assert daemon.player.calls == []  # the space did not toggle playback
