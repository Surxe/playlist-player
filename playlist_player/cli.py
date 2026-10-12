"""Command-line entry point: ``python -m playlist_player <command>``.

This module only parses arguments and wires objects together. Each command's work lives
in the package it belongs to, imported inside the handler so that, for example, ``sync``
never imports GTK and ``overlay`` never imports yt-dlp.

Commands:

* ``sync``     fetch a playlist's tracklist, audio, lyrics and cover art into the cache
* ``status``   show what is cached for a playlist
* ``pin``      choose the audio for a track by hand
* ``serve``    run the daemon: mpv, the HTTP API and the remote-control page
* ``ctl``      send a transport command to a running daemon
* ``overlay``  show the current lyric line above other windows
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable

from . import __version__
from .config import Settings, add_settings_arguments, load_settings
from .errors import PlaylistPlayerError

Handler = Callable[[argparse.Namespace, Settings], int]

# Exit status for a command that exists but is not built yet.
_NOT_BUILT = 3


def _pending(stage: int) -> Handler:
    def handler(args: argparse.Namespace, settings: Settings) -> int:
        print(f"{args.command}: not built yet (planned for stage {stage})", file=sys.stderr)
        return _NOT_BUILT

    return handler


def build_parser() -> argparse.ArgumentParser:
    # Settings flags are shared by every command, so they hang off a parent parser.
    common = argparse.ArgumentParser(add_help=False)
    add_settings_arguments(common.add_argument_group("settings"))

    parser = argparse.ArgumentParser(
        prog="playlist_player",
        description=(
            "Cache a Spotify playlist's audio and synced lyrics, play it, and overlay the lyrics."
        ),
    )
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", metavar="<command>", required=True)

    def add(name: str, summary: str, handler: Handler) -> argparse.ArgumentParser:
        sub = commands.add_parser(name, help=summary, description=summary, parents=[common])
        sub.set_defaults(handler=handler)
        return sub

    sync = add("sync", "Fetch tracklist, audio, lyrics and cover art into the cache.", _pending(5))
    sync.add_argument("playlist", help="playlist URL, spotify:playlist:ID URI, or bare ID")
    sync.add_argument("--limit", type=int, metavar="N", help="process only the first N tracks")
    sync.add_argument("--only", choices=("audio", "lyrics", "cover"), help="run a single stage")
    sync.add_argument("--track", metavar="ID", help="process a single track")
    sync.add_argument("--retry-misses", action="store_true", help="retry stages recorded as misses")
    sync.add_argument(
        "--refresh-tracklist", action="store_true", help="re-fetch the tracklist (a paid Actor run)"
    )
    sync.add_argument(
        "--dry-run", action="store_true", help="report what would be done; write nothing"
    )

    status = add("status", "Show what is cached for a playlist.", _pending(5))
    status.add_argument("playlist", nargs="?", help="playlist ID; omit to list cached playlists")

    pin = add("pin", "Choose the audio for a track by hand.", _pending(4))
    pin.add_argument("track", help="track ID")
    pin.add_argument("url", help="URL of the audio to use, or `clear` to remove the pin")

    add("serve", "Run the daemon: mpv, the HTTP API and the remote-control page.", _pending(7))

    ctl = add("ctl", "Send a transport command to a running daemon.", _pending(7))
    ctl.add_argument(
        "verb", choices=("toggle", "play", "pause", "next", "prev", "stop", "seek", "volume")
    )
    ctl.add_argument("value", nargs="?", help="seconds for seek (+10, -5, 90), 0-100 for volume")

    overlay = add("overlay", "Show the current lyric line above other windows.", _pending(8))
    overlay.add_argument(
        "--demo", action="store_true", help="show a placeholder line, for positioning"
    )
    overlay.add_argument("--snapshot", metavar="FILE", help="render one frame to a PNG and exit")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        settings = load_settings(args)
        return args.handler(args, settings)
    except PlaylistPlayerError as exc:
        print(f"playlist_player: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
