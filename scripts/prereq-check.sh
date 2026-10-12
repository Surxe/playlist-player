#!/usr/bin/env bash
# prereq-check.sh -- report whether everything playlist-player needs from outside the
# repo is installed.
#
#   scripts/prereq-check.sh
#
# Prints one line per check. A missing REQUIRED item fails the script; a missing
# optional one is reported with what it is for and does not. Read-only.
# shellcheck disable=SC2317  # the check functions below are called indirectly, through check()
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${PLAYLIST_PLAYER_PYTHON:-$REPO_ROOT/.venv/bin/python}"
failures=0

check() { # check <required|optional> <label> <purpose> <command...>
    local need=$1 label=$2 purpose=$3
    shift 3
    local out
    if out="$("$@" 2>&1)"; then
        printf 'PASS  %-28s %s\n' "$label" "$(printf '%s' "$out" | tail -n1 | cut -c1-60)"
    elif [ "$need" = required ]; then
        printf 'FAIL  %-28s needed for %s\n' "$label" "$purpose"
        failures=$((failures + 1))
    else
        printf 'SKIP  %-28s optional: %s\n' "$label" "$purpose"
    fi
}

first_line() { # first_line <command...>: the first line of its output
    # Capture first: piping straight into head kills some programs with SIGPIPE, which
    # pipefail would report as a failure even though they are fine.
    local out
    out="$("$@" 2>&1)" || return 1
    printf '%s\n' "${out%%$'\n'*}" | cut -d' ' -f1-3
}
venv_python() { [ -x "$PY" ] && "$PY" -c 'import sys; print("python", sys.version.split()[0])'; }
venv_module() { "$PY" -c "import $1; print('$1 ok')"; }
ytdlp() { "$PY" -c 'import yt_dlp; print("yt-dlp", yt_dlp.version.__version__)'; }
mpris_plugin() {
    local path
    for path in /usr/lib/mpv-mpris/mpris.so /usr/lib/mpv/scripts/mpris.so \
        /usr/lib64/mpv-mpris/mpris.so /etc/mpv/scripts/mpris.so; do
        [ -e "$path" ] && {
            echo "$path"
            return 0
        }
    done
    return 1
}
layer_shell() {
    python3 -c "
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Gtk4LayerShell', '1.0')
from gi.repository import Gtk4LayerShell as L
print('gtk4-layer-shell', '.'.join(map(str, (L.get_major_version(), L.get_minor_version(), L.get_micro_version()))))
" 2>/dev/null
}
javascript_runtime() {
    command -v deno >/dev/null && { first_line deno --version; return; }
    command -v node >/dev/null && echo "node $(node --version)"
}

check required "python virtualenv" "everything (see the README's Setup)" venv_python
check required "ffmpeg" "sync: tagging downloaded audio" first_line ffmpeg -version
check required "ffprobe" "sync: measuring downloaded audio" first_line ffprobe -version
check required "yt-dlp (virtualenv)" "sync: downloading audio" ytdlp
check required "mpv" "serve: playback" first_line mpv --version
check optional "deno or node" "yt-dlp extracts more reliably with a JavaScript runtime" javascript_runtime
check optional "mpv MPRIS plugin" "keyboard media keys (set PLAYLIST_PLAYER_MPV_MPRIS_PLUGIN)" mpris_plugin
check optional "gtk4-layer-shell bindings" "the lyric overlay" layer_shell
check optional "pytest (virtualenv)" "running the tests" venv_module pytest
check optional "optionsconfig (virtualenv)" "build.py, which regenerates the settings docs" venv_module optionsconfig
check optional "kwin_wayland" "the overlay's compositor tests" command -v kwin_wayland

echo
if [ "$failures" -eq 0 ]; then
    echo "Everything required is in place."
else
    echo "$failures required item(s) missing."
fi
exit "$((failures > 0))"
