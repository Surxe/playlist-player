"""Run the real overlay inside a compositor and record what is on screen over time.

Started by the compositor tests as a subprocess (under ``scripts/headless-kwin.sh
run``), so the layer-shell library is loaded first exactly as it is for the real
command. It wires the overlay the way ``overlay.main.run`` does, adds a timer that
samples ``renderer.describe()``, runs until a stop file appears, and writes the samples
as JSON.

    python tests/support/overlay_probe.py <out.json> [interval-ms]
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

# First, and before anything that could import gi: this import loads libgtk4-layer-shell.
from playlist_player.overlay import main as overlay_main  # isort: skip
from playlist_player.config import load_settings
from playlist_player.overlay.clock import now_epoch_ms

MAX_SECONDS = 30


def main() -> int:
    out = Path(sys.argv[1])
    interval_ms = int(sys.argv[2]) if len(sys.argv) > 2 else 10
    stop_file = out.with_suffix(".stop")
    settings = load_settings()
    renderer = overlay_main.build_renderer(settings)
    model, client = overlay_main.connect(settings, renderer)

    from gi.repository import GLib

    samples: list[dict] = []
    started = time.time()

    def sample() -> bool:
        seen = renderer.describe()
        seen["t"] = time.time()
        samples.append(seen)
        if stop_file.exists() or time.time() - started > MAX_SECONDS:
            renderer.stop()
            return False
        return True

    GLib.timeout_add(interval_ms, sample)
    client.start()
    try:
        renderer.run(lambda: model.view(now_epoch_ms()))
    finally:
        client.stop()
    out.write_text(json.dumps({"monitors": renderer.monitor_names(), "samples": samples}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
