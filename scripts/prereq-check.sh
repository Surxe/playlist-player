#!/usr/bin/env bash
# prereq-check.sh -- confirm, from the dev account, that everything the build needs from
# outside the repo is in place: system packages, the virtualenv, and one cached tracklist.
#
#   scripts/prereq-check.sh [playlist-id]
#
# With a playlist ID it also checks that playlist's cached tracklist; without one it
# checks whichever playlists are cached. Prints one PASS/FAIL line per check and exits
# non-zero if any failed. Read-only apart from a create-and-delete write probe.
# shellcheck disable=SC2317  # the check functions below are called indirectly, through check()
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CACHE_DIR="${PLAYLIST_PLAYER_CACHE_DIR:-$REPO_ROOT/cache}"
PY="$REPO_ROOT/.venv/bin/python"
failures=0

check() { # check <label> <command...>
    local label=$1
    shift
    local out
    if out="$("$@" 2>&1)"; then
        printf 'PASS  %-34s %s\n' "$label" "$(printf '%s' "$out" | tail -n1 | cut -c1-70)"
    else
        printf 'FAIL  %-34s %s\n' "$label" "$(printf '%s' "$out" | tail -n1 | cut -c1-70)"
        failures=$((failures + 1))
    fi
}

mpv_version() {
    # Capture first: piping straight into head makes mpv die of SIGPIPE, which pipefail
    # would report as a failure even though mpv is fine.
    local out
    out="$(mpv --version)" || return 1
    printf '%s\n' "${out%%$'\n'*}" | cut -d' ' -f1-2
}
venv_present() { [ -x "$PY" ] && echo "${PY#"$REPO_ROOT"/}"; }
mpris_plugin() { dpkg -L mpv-mpris | grep -m1 'mpv-mpris/mpris\.so$'; }
layer_shell() {
    python3 -c "
import gi
gi.require_version('Gtk4LayerShell', '1.0')
from gi.repository import Gtk4LayerShell as L
print('gtk4-layer-shell', '.'.join(map(str, (L.get_major_version(), L.get_minor_version(), L.get_micro_version()))))
" 2>/dev/null
}
no_system_ytdlp() {
    # yt-dlp must come from the venv only; the Debian package goes stale.
    if dpkg -s yt-dlp >/dev/null 2>&1; then
        echo "Debian yt-dlp is installed; remove it"
        return 1
    fi
    echo "not installed system-wide (as intended)"
}
venv_imports() {
    "$PY" -c "
import gi, optionsconfig, pytest, yt_dlp
print('yt-dlp', yt_dlp.version.__version__)"
}
headless_compositor() { command -v kwin_wayland >/dev/null && echo "kwin_wayland present"; }
tracklist() { # tracklist <playlist-dir>
    local raw="$1/tracklist.raw.json"
    [ -s "$raw" ] || {
        echo "missing $raw"
        return 1
    }
    jq -er '.[0] | (.total_tracks // .track_count // .trackCount) as $total
        | (.tracks | length) as $got
        | if $got >= 1 and $total == $got then "\($got) of \($total) tracks"
          else ("incomplete: \($got) of \($total)" | halt_error(1)) end' "$raw"
}
writable() { # writable <dir>
    local probe="$1/.prereq-write-probe"
    touch "$probe" && rm -f "$probe" && echo "dev can write"
}

check "mpv" mpv_version
check "mpv MPRIS plugin" mpris_plugin
check "gtk4-layer-shell bindings" layer_shell
check "ffmpeg" bash -c 'ffmpeg -version | head -n1 | cut -d" " -f1-3'
check "ffprobe" bash -c 'ffprobe -version | head -n1 | cut -d" " -f1-3'
check "jq" jq --version
check "headless compositor" headless_compositor
check "no system yt-dlp" no_system_ytdlp
check "virtualenv" venv_present
check "virtualenv packages" venv_imports

if [ $# -ge 1 ]; then
    dirs=("$CACHE_DIR/playlists/$1")
else
    shopt -s nullglob
    dirs=("$CACHE_DIR"/playlists/*/)
    shopt -u nullglob
fi
if [ ${#dirs[@]} -eq 0 ]; then
    printf 'FAIL  %-34s %s\n' "cached tracklist" "none under $CACHE_DIR/playlists; run scripts/fetch-tracklist.sh"
    failures=$((failures + 1))
fi
for dir in "${dirs[@]}"; do
    dir="${dir%/}"
    id="$(basename "$dir")"
    check "tracklist $id" tracklist "$dir"
    check "cache writable $id" writable "$dir"
done

echo
if [ "$failures" -eq 0 ]; then
    echo "All prerequisites in place."
else
    echo "$failures check(s) failed."
fi
exit "$((failures > 0))"
