#!/usr/bin/env bash
# fetch-tracklist.sh -- fetch one Spotify playlist's track list into the cache, with the
# Apify token loaded from the secrets file.
#
#   scripts/fetch-tracklist.sh <playlist-url | spotify:playlist:ID | ID> [--refresh]
#
# A thin wrapper around
#   python -m playlist_player sync <playlist> --only tracklist
# which owns the request, the cache layout and the completeness check. All this script
# adds is the token: taken from the environment when set, otherwise sourced from the
# secrets file in the caller's home. It reaches Python through the environment, never as
# a command-line argument, and is never printed.
#
# This is the only step in the whole tool that needs the token, and it runs once per
# playlist. An existing tracklist is not fetched again unless --refresh is given, because
# every fetch is a paid Actor run (about $0.01 for one playlist).
#
# Settings (environment, all optional):
#   PLAYLIST_PLAYER_SECRETS   default ~/.config/playlist-player/secrets.env
#   PLAYLIST_PLAYER_PYTHON    default <repo>/.venv/bin/python
#   plus every PLAYLIST_PLAYER_* setting the sync command reads (see the README).
#
# Exit codes: 0 fetched (or already cached); 1 error, nothing written; 2 the tracklist
# holds fewer tracks than the playlist reports (it is kept for inspection).
set -euo pipefail

# Both ethan and dev write into the cache, so new files must stay group-writable.
umask 002

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SECRETS="${PLAYLIST_PLAYER_SECRETS:-$HOME/.config/playlist-player/secrets.env}"
PY="${PLAYLIST_PLAYER_PYTHON:-$REPO_ROOT/.venv/bin/python}"

die() {
    echo "fetch-tracklist: $*" >&2
    exit 1
}

usage() {
    sed -n '2,5p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

sync_args=(--only tracklist)
target=""
for arg in "$@"; do
    case "$arg" in
        --refresh) sync_args+=(--refresh-tracklist) ;;
        -h | --help)
            usage
            exit 0
            ;;
        -*) die "unknown option: $arg" ;;
        *)
            [ -z "$target" ] || die "expected one playlist, got a second argument: $arg"
            target=$arg
            ;;
    esac
done
[ -n "$target" ] || {
    usage >&2
    exit 1
}
[ -x "$PY" ] || die "no Python at $PY; create the virtualenv first (see the README)"

# The token is optional here: a cached tracklist needs none, and the sync command names
# the variable itself when a fetch needs it and it is missing.
if [ -z "${APIFY_TOKEN:-}" ] && [ -r "$SECRETS" ]; then
    set -a
    # shellcheck source=/dev/null
    . "$SECRETS"
    set +a
fi

# The package is run from the repo, not installed, so Python must start there.
cd "$REPO_ROOT"
exec "$PY" -m playlist_player sync "$target" "${sync_args[@]}"
