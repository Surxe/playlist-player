#!/usr/bin/env bash
# make-test-audio.sh -- generate a synthetic tone for tests. No real audio is ever
# committed; tests that need a playable file make one with this.
#
#   scripts/make-test-audio.sh <out-file> [seconds=5] [frequency-hz=440]
#
# The container and codec follow the extension: .webm and .opus are Opus (what the audio
# source normally receives), .m4a is AAC, .mp3 is MP3, .ogg is Vorbis.
set -euo pipefail

out="${1:?usage: make-test-audio.sh <out-file> [seconds] [frequency-hz]}"
seconds="${2:-5}"
frequency="${3:-440}"

case "$out" in
    *.webm | *.opus) codec=(-c:a libopus -b:a 32k) ;;
    *.m4a) codec=(-c:a aac -b:a 48k) ;;
    *.mp3) codec=(-c:a libmp3lame -b:a 48k) ;;
    *.ogg) codec=(-c:a libvorbis -q:a 1) ;;
    *)
        echo "make-test-audio: unsupported extension: $out" >&2
        exit 1
        ;;
esac

exec ffmpeg -v error -nostdin -y \
    -f lavfi -i "sine=frequency=${frequency}:duration=${seconds}:sample_rate=48000" \
    -ac 2 "${codec[@]}" -metadata title="generated tone" "$out"
