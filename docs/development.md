# Developing spotifixed


```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest                 # loopback only; spawns mpv (silent), ffmpeg and node
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/python build.py                  # regenerate .env.example and setup.md's Configuration
scripts/verify.sh                          # the three checks above; --head tests a clean export
scripts/headless-kwin.sh run <command>     # run something in a display-less KWin, e.g. the overlay
```

Tests never reach a real service: every third-party API is replaced by a loopback
server, and a guard fails any test that connects elsewhere. The overlay tests start a
headless KWin themselves and are skipped where KWin or the layer-shell bindings are
missing. The page's settings panel is tested in a headless Chrome, driven by node over
the DevTools protocol, and skipped where either is missing. `overlay --demo --snapshot out.png` renders one frame to a PNG (over a half
dark, half light backdrop) for a look without a graphical session.

Everything under `cache/` is third-party content (audio, lyrics, track lists). Tests use
synthetic data only; never commit a real lyric, audio file or Actor response.

