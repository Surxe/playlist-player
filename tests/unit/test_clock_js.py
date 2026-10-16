"""Runs the page's clock.js tests under node, so the usual test command covers them.

clock.js mirrors the Python timeline and position extrapolation; both sides are checked
against tests/fixtures/timeline_cases.json. The matching Python-side check for
``position_at`` is here too, on the same numbers the JavaScript test uses.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from spotifixed.config import REPO_ROOT
from spotifixed.models import PlaybackState, PlayerStatus


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
@pytest.mark.parametrize("name", ["clock", "overlay"])
def test_the_pages_modules_pass_their_node_tests(name):
    result = subprocess.run(
        ["node", "--test", f"tests/js/{name}.test.mjs"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-1000:]
    assert "# fail 0" in result.stdout and "# pass 0" not in result.stdout


def test_python_position_at_matches_the_numbers_in_the_js_test():
    playing = PlaybackState(
        status=PlayerStatus.PLAYING, paused=False, position_s=10, duration_s=60, speed=1,
        captured_at_epoch_ms=1_000_000,
    )  # fmt: skip
    assert playing.position_at(1_000_000) == 10
    assert playing.position_at(1_002_500) == 12.5
    assert playing.position_at(999_000) == 10
    assert playing.position_at(1_900_000) == 60
    paused = PlaybackState(status=PlayerStatus.PAUSED, paused=True, position_s=10)
    assert paused.position_at(1_005_000) == 10
