"""OptionsConfig schema: every user-tunable setting, in one place.

OptionsConfig resolves each option from (in priority order) a CLI argument, then an
environment variable / ``.env`` entry in the working directory, then the ``default``
below. The same schema generates ``.env.example`` and the README's Configuration section
(run ``python build.py`` after editing this file).

Two things are deliberately NOT here:

* Per-track values (timing offsets, audio pins). Those are variable-length, hand-authored
  data and live as JSON under the state directory.
* The tracklist Actor's snapshot option. Snapshots stay off, always; it is not a setting.
"""

from __future__ import annotations

from typing import Literal

_STORAGE = "Storage"
_TRACKLIST = "Tracklist (Apify)"
_SYNC = "Sync"
_DAEMON = "Daemon"
_OVERLAY = "Overlay"

Anchor = Literal[
    "top", "bottom", "left", "right", "top-left", "top-right", "bottom-left", "bottom-right",
    "center",
]  # fmt: skip

OPTIONS_SCHEMA = {
    # ------------------------------ Storage ------------------------------ #
    "CACHE_DIR": {
        "env": "PLAYLIST_PLAYER_CACHE_DIR",
        "arg": "--cache-dir",
        "type": str,
        "default": "cache",
        "section": _STORAGE,
        "help": (
            "Directory for everything fetched: tracklists, audio, lyrics, cover art. "
            "A relative path resolves against the repo root, not the working directory."
        ),
    },
    "STATE_DIR": {
        "env": "PLAYLIST_PLAYER_STATE_DIR",
        "arg": "--state-dir",
        "type": str,
        "default": "state",
        "section": _STORAGE,
        "help": (
            "Directory for hand-authored per-track values (timing offsets, audio pins). "
            "Kept apart from the cache so wiping the cache never loses a correction."
        ),
    },
    # ----------------------------- Tracklist ----------------------------- #
    "APIFY_TOKEN": {
        "env": "APIFY_TOKEN",
        # Present because the schema requires an arg name, but config.py never registers
        # it on the parser: a token on a command line is visible in the process list.
        "arg": "--apify-token",
        "type": str,
        "default": None,
        "section": _TRACKLIST,
        "sensitive": True,
        "help": (
            "Apify API token. Environment only, never a CLI argument and never a "
            "committed file. Needed solely to fetch a playlist's track list that is not "
            "cached yet; see secrets.env.example for where the real value lives."
        ),
    },
    "TRACKLIST_ACTOR": {
        "env": "PLAYLIST_PLAYER_TRACKLIST_ACTOR",
        "arg": "--tracklist-actor",
        "type": str,
        "default": "s-r~free-spotify-playlist-intelligence",
        "section": _TRACKLIST,
        "help": (
            "Apify Actor that returns a playlist's tracks, as user~name. The response "
            "shape is Actor-specific, so a new Actor also needs a parser."
        ),
    },
    "APIFY_MAX_CHARGE_USD": {
        "env": "PLAYLIST_PLAYER_APIFY_MAX_CHARGE_USD",
        "arg": "--apify-max-charge-usd",
        "type": float,
        "default": 0.05,
        "section": _TRACKLIST,
        "help": "Charge cap sent with each Actor run, in US dollars.",
    },
    "APIFY_BASE_URL": {
        "env": "PLAYLIST_PLAYER_APIFY_BASE_URL",
        "arg": "--apify-base-url",
        "type": str,
        "default": "https://api.apify.com",
        "section": _TRACKLIST,
        "help": "Apify API origin. Tests point this at a local stub.",
    },
    # -------------------------------- Sync ------------------------------- #
    "LRCLIB_BASE_URL": {
        "env": "PLAYLIST_PLAYER_LRCLIB_BASE_URL",
        "arg": "--lrclib-base-url",
        "type": str,
        "default": "https://lrclib.net",
        "section": _SYNC,
        "help": "LRCLIB origin, the lyrics source.",
    },
    "AUDIO_DURATION_TOLERANCE_S": {
        "env": "PLAYLIST_PLAYER_AUDIO_DURATION_TOLERANCE_S",
        "arg": "--audio-duration-tolerance-s",
        "type": float,
        "default": 3.0,
        "section": _SYNC,
        "help": (
            "An audio candidate within this many seconds of Spotify's duration gets full "
            "marks for duration."
        ),
    },
    "AUDIO_DURATION_REJECT_S": {
        "env": "PLAYLIST_PLAYER_AUDIO_DURATION_REJECT_S",
        "arg": "--audio-duration-reject-s",
        "type": float,
        "default": 7.0,
        "section": _SYNC,
        "help": "An audio candidate further than this from Spotify's duration is rejected.",
    },
    "LYRICS_DURATION_TOLERANCE_S": {
        "env": "PLAYLIST_PLAYER_LYRICS_DURATION_TOLERANCE_S",
        "arg": "--lyrics-duration-tolerance-s",
        "type": float,
        "default": 2.0,
        "section": _SYNC,
        "help": (
            "A lyrics record further than this from the audio's real duration is still "
            "kept, but flagged as low timing confidence."
        ),
    },
    "SYNC_WORKERS": {
        "env": "PLAYLIST_PLAYER_SYNC_WORKERS",
        "arg": "--sync-workers",
        "type": int,
        "default": 2,
        "section": _SYNC,
        "help": "Tracks processed in parallel. Keep low to stay polite to the sources.",
    },
    "MISS_RETRY_DAYS": {
        "env": "PLAYLIST_PLAYER_MISS_RETRY_DAYS",
        "arg": "--miss-retry-days",
        "type": int,
        "default": 14,
        "section": _SYNC,
        "help": "A recorded miss (no lyrics, no audio match) is retried after this many days.",
    },
    # ------------------------------- Daemon ------------------------------ #
    "HOST": {
        "env": "PLAYLIST_PLAYER_HOST",
        "arg": "--host",
        "type": str,
        "default": "127.0.0.1",
        "section": _DAEMON,
        "help": "Address the daemon binds. Anything other than loopback is refused.",
    },
    "PORT": {
        "env": "PLAYLIST_PLAYER_PORT",
        "arg": "--port",
        "type": int,
        "default": 8765,
        "section": _DAEMON,
        "help": "Port for the daemon's HTTP API and remote-control page.",
    },
    "MPV_BIN": {
        "env": "PLAYLIST_PLAYER_MPV_BIN",
        "arg": "--mpv-bin",
        "type": str,
        "default": "mpv",
        "section": _DAEMON,
        "help": "mpv executable.",
    },
    "MPV_AUDIO_OUTPUT": {
        "env": "PLAYLIST_PLAYER_MPV_AUDIO_OUTPUT",
        "arg": "--mpv-audio-output",
        "type": str,
        "default": "",
        "section": _DAEMON,
        "help": "mpv --ao value. Empty lets mpv choose; tests use `null` for silence.",
    },
    "MPV_MPRIS_PLUGIN": {
        "env": "PLAYLIST_PLAYER_MPV_MPRIS_PLUGIN",
        "arg": "--mpv-mpris-plugin",
        "type": str,
        "default": "",
        "section": _DAEMON,
        "help": (
            "Path to mpv's MPRIS plugin (Debian: /usr/lib/mpv-mpris/mpris.so) so desktop "
            "media keys control playback. Empty disables it."
        ),
    },
    # ------------------------------- Overlay ----------------------------- #
    "OVERLAY_MONITOR": {
        "env": "PLAYLIST_PLAYER_OVERLAY_MONITOR",
        "arg": "--overlay-monitor",
        "type": str,
        "default": "",
        "section": _OVERLAY,
        "help": "Output connector to show the overlay on, e.g. DP-2. Empty: compositor's choice.",
    },
    "OVERLAY_ANCHOR": {
        "env": "PLAYLIST_PLAYER_OVERLAY_ANCHOR",
        "arg": "--overlay-anchor",
        "type": Anchor,
        "default": "bottom",
        "section": _OVERLAY,
        "help": "Screen edge or corner the overlay is attached to.",
    },
    "OVERLAY_MARGIN_X": {
        "env": "PLAYLIST_PLAYER_OVERLAY_MARGIN_X",
        "arg": "--overlay-margin-x",
        "type": int,
        "default": 0,
        "section": _OVERLAY,
        "help": "Horizontal distance in pixels from the anchored edge.",
    },
    "OVERLAY_MARGIN_Y": {
        "env": "PLAYLIST_PLAYER_OVERLAY_MARGIN_Y",
        "arg": "--overlay-margin-y",
        "type": int,
        "default": 120,
        "section": _OVERLAY,
        "help": "Vertical distance in pixels from the anchored edge.",
    },
    "OVERLAY_MAX_WIDTH": {
        "env": "PLAYLIST_PLAYER_OVERLAY_MAX_WIDTH",
        "arg": "--overlay-max-width",
        "type": int,
        "default": 1200,
        "section": _OVERLAY,
        "help": "Maximum line width in pixels; longer lines wrap.",
    },
    "OVERLAY_FONT": {
        "env": "PLAYLIST_PLAYER_OVERLAY_FONT",
        "arg": "--overlay-font",
        "type": str,
        "default": "Sans Bold",
        "section": _OVERLAY,
        "help": "Font family and style.",
    },
    "OVERLAY_FONT_SIZE": {
        "env": "PLAYLIST_PLAYER_OVERLAY_FONT_SIZE",
        "arg": "--overlay-font-size",
        "type": int,
        "default": 32,
        "section": _OVERLAY,
        "help": "Font size in points.",
    },
    "OVERLAY_COLOR": {
        "env": "PLAYLIST_PLAYER_OVERLAY_COLOR",
        "arg": "--overlay-color",
        "type": str,
        "default": "#FFFFFF",
        "section": _OVERLAY,
        "help": "Text colour.",
    },
    "OVERLAY_OUTLINE_COLOR": {
        "env": "PLAYLIST_PLAYER_OVERLAY_OUTLINE_COLOR",
        "arg": "--overlay-outline-color",
        "type": str,
        "default": "#000000",
        "section": _OVERLAY,
        "help": "Outline colour, for contrast against any background.",
    },
    "OVERLAY_LINES": {
        "env": "PLAYLIST_PLAYER_OVERLAY_LINES",
        "arg": "--overlay-lines",
        "type": int,
        "default": 1,
        "section": _OVERLAY,
        "help": "1 shows the current line; 2 adds the next line, dimmed.",
    },
    "OVERLAY_LEAD_MS": {
        "env": "PLAYLIST_PLAYER_OVERLAY_LEAD_MS",
        "arg": "--overlay-lead-ms",
        "type": int,
        "default": 150,
        "section": _OVERLAY,
        "help": "Show each line this many milliseconds before its timestamp.",
    },
    "OVERLAY_FADE_MS": {
        "env": "PLAYLIST_PLAYER_OVERLAY_FADE_MS",
        "arg": "--overlay-fade-ms",
        "type": int,
        "default": 120,
        "section": _OVERLAY,
        "help": "Fade duration when a line appears or disappears.",
    },
}
