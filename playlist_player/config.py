"""Settings: one typed, immutable object built from the OptionsConfig schema.

The rest of the package never touches OptionsConfig or ``os.environ`` directly. It asks
for a :class:`Settings` (or is handed one), which keeps every module testable with a
plain constructor call and keeps the resolution rules in one file.

Two departures from the usual OptionsConfig wiring, both deliberate:

* The schema is passed to OptionsConfig explicitly rather than discovered through
  ``pyproject.toml``. That lookup is relative to the working directory, and the daemon
  and overlay run from an installed copy that has no ``pyproject.toml`` beside it.
* Options marked ``sensitive`` are not registered as CLI arguments. A secret given on a
  command line is visible to every local user in the process list.
"""

from __future__ import annotations

import ipaddress
from argparse import ArgumentParser, Namespace
from dataclasses import dataclass, fields
from pathlib import Path

from optionsconfig import ArgumentWriter, init_options, logger

from .errors import ConfigError
from .options_schema import OPTIONS_SCHEMA

# OptionsConfig logs every option it resolves through loguru. That is noise for a CLI
# that speaks through print(), so drop loguru's default handler at import.
logger.remove()

# Relative storage paths resolve against this, not the working directory, so the same
# cache is used no matter where a command is started from.
REPO_ROOT = Path(__file__).resolve().parent.parent

_CLI_SCHEMA = {name: opt for name, opt in OPTIONS_SCHEMA.items() if not opt.get("sensitive")}


@dataclass(frozen=True, slots=True)
class Settings:
    cache_dir: Path
    state_dir: Path
    apify_token: str | None
    tracklist_actor: str
    apify_max_charge_usd: float
    apify_base_url: str
    lrclib_base_url: str
    audio_duration_tolerance_s: float
    audio_duration_reject_s: float
    lyrics_duration_tolerance_s: float
    sync_workers: int
    miss_retry_days: int
    host: str
    port: int
    mpv_bin: str
    mpv_audio_output: str
    mpv_mpris_plugin: str
    overlay_monitor: str
    overlay_anchor: str
    overlay_margin_x: int
    overlay_margin_y: int
    overlay_max_width: int
    overlay_font: str
    overlay_font_size: int
    overlay_color: str
    overlay_outline_color: str
    overlay_lines: int
    overlay_lead_ms: int
    overlay_fade_ms: int

    def __repr__(self) -> str:
        # A Settings object ends up in logs and tracebacks; never let the token ride along.
        def show(name: str) -> str:
            if name == "apify_token":
                return "<set>" if self.apify_token else "None"
            return repr(getattr(self, name))

        shown = ", ".join(f"{f.name}={show(f.name)}" for f in fields(self))
        return f"Settings({shown})"

    def require_apify_token(self) -> str:
        """Return the token, or fail naming the variable. Call only when about to use it."""
        if not self.apify_token:
            raise ConfigError(
                "APIFY_TOKEN is not set. It is needed only to fetch a playlist's track list "
                "that is not cached yet; see secrets.env.example for where it lives."
            )
        return self.apify_token

    @property
    def base_url(self) -> str:
        """The daemon's own origin, as clients should address it."""
        return f"http://{self.host}:{self.port}"


def add_settings_arguments(parser: ArgumentParser) -> None:
    """Register every non-secret setting as a ``--flag`` on ``parser``."""
    ArgumentWriter(schema=_CLI_SCHEMA).add_arguments(parser)


def _resolve_dir(value: str, root: Path) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else (root / path)


def load_settings(args: Namespace | None = None, *, root: Path = REPO_ROOT) -> Settings:
    """Resolve settings (CLI args, then environment / ``.env``, then defaults) and validate."""
    options = init_options(args=args, schema=OPTIONS_SCHEMA, setup_logger=False)
    values = {f.name: getattr(options, f.name) for f in fields(Settings)}
    values["cache_dir"] = _resolve_dir(values["cache_dir"], root)
    values["state_dir"] = _resolve_dir(values["state_dir"], root)
    values["apify_token"] = values["apify_token"] or None
    for name in ("apify_base_url", "lrclib_base_url"):
        values[name] = values[name].rstrip("/")
    settings = Settings(**values)
    _validate(settings)
    return settings


def _validate(s: Settings) -> None:
    try:
        loopback = s.host == "localhost" or ipaddress.ip_address(s.host).is_loopback
    except ValueError:
        loopback = False
    if not loopback:
        raise ConfigError(
            f"PLAYLIST_PLAYER_HOST must be a loopback address, got {s.host!r}. The daemon "
            "has no authentication and is not meant to be reachable from the network."
        )
    if not 1 <= s.port <= 65535:
        raise ConfigError(f"PLAYLIST_PLAYER_PORT must be 1-65535, got {s.port}")
    if s.sync_workers < 1:
        raise ConfigError(f"PLAYLIST_PLAYER_SYNC_WORKERS must be at least 1, got {s.sync_workers}")
    if s.audio_duration_reject_s < s.audio_duration_tolerance_s:
        raise ConfigError(
            "PLAYLIST_PLAYER_AUDIO_DURATION_REJECT_S must not be smaller than "
            "PLAYLIST_PLAYER_AUDIO_DURATION_TOLERANCE_S"
        )
    if s.overlay_lines not in (1, 2):
        raise ConfigError(f"PLAYLIST_PLAYER_OVERLAY_LINES must be 1 or 2, got {s.overlay_lines}")
