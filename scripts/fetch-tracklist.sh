#!/usr/bin/env bash
# fetch-tracklist.sh -- fetch one Spotify playlist's track list through Apify and save
# the raw response into the cache.
#
#   scripts/fetch-tracklist.sh <playlist-url | spotify:playlist:ID | ID> [--refresh]
#
# This is the only step in the whole tool that needs the Apify token, and it runs once
# per playlist: the response is written verbatim to
#   cache/playlists/<id>/tracklist.raw.json
# and everything downstream (parsing, lyrics, audio) reads that file. An existing file is
# never overwritten unless --refresh is given, because every run is a paid Actor run
# (about $0.01 for one playlist, hard-capped by the max-charge setting below).
#
# Token handling: APIFY_TOKEN is taken from the environment when set (the launcher
# route, where an ethan-owned launcher injects it into a dev-side run), otherwise it is
# sourced from the ethan-owned secrets file. Either way it is handed to curl through a
# file descriptor, never as a command-line argument, so it cannot show up in `ps`. The
# token is never printed and never written anywhere.
#
# Settings (environment, all optional):
#   PLAYLIST_PLAYER_SECRETS               default ~/.config/playlist-player/secrets.env
#   PLAYLIST_PLAYER_CACHE_DIR             default <repo>/cache
#   PLAYLIST_PLAYER_TRACKLIST_ACTOR       default automation-lab~spotify-scraper
#   PLAYLIST_PLAYER_APIFY_MAX_CHARGE_USD  default 0.05
#   PLAYLIST_PLAYER_APIFY_BASE_URL        default https://api.apify.com (tests point this
#                                         at a local stub)
#
# Exit codes: 0 fetched (or already cached); 1 error, nothing written; 2 fetched, but the
# response holds fewer tracks than the playlist reports (the file is kept for inspection).
set -euo pipefail

# Both ethan and dev write into the cache, so new files must stay group-writable.
umask 002

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SECRETS="${PLAYLIST_PLAYER_SECRETS:-$HOME/.config/playlist-player/secrets.env}"
CACHE_DIR="${PLAYLIST_PLAYER_CACHE_DIR:-$REPO_ROOT/cache}"
ACTOR="${PLAYLIST_PLAYER_TRACKLIST_ACTOR:-automation-lab~spotify-scraper}"
MAX_CHARGE="${PLAYLIST_PLAYER_APIFY_MAX_CHARGE_USD:-0.05}"
API_BASE="${PLAYLIST_PLAYER_APIFY_BASE_URL:-https://api.apify.com}"

die() {
    echo "fetch-tracklist: $*" >&2
    exit 1
}

usage() {
    sed -n '2,5p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

# Accepts every form Spotify hands out: a share URL (with or without the ?si= tracking
# suffix or an /intl-xx/ prefix), a spotify:playlist:<id> URI, or the bare ID.
playlist_id() {
    local s=$1
    if [[ $s =~ playlist[/:]([A-Za-z0-9]+) ]]; then
        printf '%s\n' "${BASH_REMATCH[1]}"
    elif [[ $s =~ ^[A-Za-z0-9]+$ ]]; then
        printf '%s\n' "$s"
    else
        return 1
    fi
}

refresh=0
target=""
for arg in "$@"; do
    case "$arg" in
        --refresh) refresh=1 ;;
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

command -v curl >/dev/null || die "curl is not installed"
command -v jq >/dev/null || die "jq is not installed"

id="$(playlist_id "$target")" || die "cannot find a playlist ID in: $target"
# Rebuild the URL from the ID so tracking parameters never reach the Actor.
url="https://open.spotify.com/playlist/$id"
out_dir="$CACHE_DIR/playlists/$id"
out="$out_dir/tracklist.raw.json"

summarize() {
    jq -r '.[0] | "  type:        \(.type)\n  name:        \(.name)\n  trackCount:  \(.trackCount)\n  tracks got:  \(.tracks | length)"' "$1"
}

# Exit 2 when the Actor returned fewer tracks than the playlist says it has.
check_complete() {
    if jq -e '.[0] | (.trackCount // -1) == (.tracks | length)' "$1" >/dev/null; then
        return 0
    fi
    echo "fetch-tracklist: WARNING: track array length differs from trackCount; the Actor may truncate long playlists." >&2
    return 2
}

if [ -s "$out" ] && [ "$refresh" -eq 0 ]; then
    echo "Already cached (pass --refresh to pay for a new fetch): $out"
    summarize "$out"
    check_complete "$out"
    exit
fi

if [ -z "${APIFY_TOKEN:-}" ]; then
    [ -r "$SECRETS" ] || die "APIFY_TOKEN is not set and the secrets file is not readable: $SECRETS
  Create it (mode 600) with one line: APIFY_TOKEN=<token>. See secrets.env.example."
    set -a
    # shellcheck source=/dev/null
    . "$SECRETS"
    set +a
fi
[ -n "${APIFY_TOKEN:-}" ] || die "APIFY_TOKEN is empty after loading $SECRETS"

mkdir -p "$out_dir"
tmp="$(mktemp "$out_dir/.tracklist.raw.XXXXXX")"
trap 'rm -f "$tmp"' EXIT

endpoint="$API_BASE/v2/acts/$ACTOR/run-sync-get-dataset-items?clean=true&format=json&maxTotalChargeUsd=$MAX_CHARGE"
# One playlist is one dataset item whose `tracks` array holds every track.
body="$(jq -cn --arg url "$url" '{mode: "urls", urls: [$url], maxResults: 1}')"

echo "Starting a paid Actor run ($ACTOR, capped at \$$MAX_CHARGE) for playlist $id ..."
# The run-sync endpoint holds the connection open until the run finishes (up to 300 s).
http_code="$(
    curl -sS -X POST "$endpoint" \
        -H @<(printf 'Authorization: Bearer %s\n' "$APIFY_TOKEN") \
        -H 'Content-Type: application/json' \
        --data "$body" \
        --max-time 330 \
        -o "$tmp" -w '%{http_code}'
)" || die "request failed (network error or timeout); nothing written"

case "$http_code" in
    200 | 201) ;;
    *)
        echo "fetch-tracklist: Apify answered HTTP $http_code; nothing written. Response:" >&2
        head -c 2000 "$tmp" >&2
        echo >&2
        exit 1
        ;;
esac

jq -e 'type == "array" and length >= 1 and (.[0].tracks | type == "array")' "$tmp" >/dev/null ||
    die "response is not a playlist record with a tracks array; nothing written. First bytes: $(head -c 300 "$tmp")"

# mktemp creates the file 0600 regardless of umask; open it up before it takes its name.
chmod 664 "$tmp"
mv "$tmp" "$out"
trap - EXIT

echo "Saved: $out"
summarize "$out"
check_complete "$out"
