"""Settings resolution: precedence, path handling, validation, and keeping the token off argv."""

from __future__ import annotations

from pathlib import Path

import pytest

from playlist_player.cli import build_parser
from playlist_player.config import REPO_ROOT, Settings, load_settings
from playlist_player.errors import ConfigError
from playlist_player.options_schema import OPTIONS_SCHEMA


def test_defaults_resolve_relative_dirs_against_the_repo_root():
    settings = load_settings()
    assert settings.cache_dir == REPO_ROOT / "cache"
    assert settings.state_dir == REPO_ROOT / "state"
    assert settings.host == "127.0.0.1" and settings.port == 8765
    assert settings.apify_token is None
    assert settings.base_url == "http://127.0.0.1:8765"


def test_every_schema_option_has_a_settings_field():
    """Adding an option without a field (or the reverse) should fail loudly, here."""
    assert {name.lower() for name in OPTIONS_SCHEMA} == set(Settings.__dataclass_fields__)


def test_environment_overrides_defaults_with_the_right_types(monkeypatch, tmp_path):
    monkeypatch.setenv("PLAYLIST_PLAYER_CACHE_DIR", str(tmp_path / "c"))
    monkeypatch.setenv("PLAYLIST_PLAYER_PORT", "9001")
    monkeypatch.setenv("PLAYLIST_PLAYER_AUDIO_DURATION_TOLERANCE_S", "1.5")
    monkeypatch.setenv("PLAYLIST_PLAYER_OVERLAY_ANCHOR", "top-left")
    monkeypatch.setenv("PLAYLIST_PLAYER_LRCLIB_BASE_URL", "http://127.0.0.1:1/")
    settings = load_settings()
    assert settings.cache_dir == tmp_path / "c"
    assert settings.port == 9001 and isinstance(settings.port, int)
    assert settings.audio_duration_tolerance_s == 1.5
    assert settings.overlay_anchor == "top-left"
    assert settings.lrclib_base_url == "http://127.0.0.1:1"  # trailing slash trimmed


def test_cli_arguments_beat_the_environment(monkeypatch):
    monkeypatch.setenv("PLAYLIST_PLAYER_PORT", "9001")
    args = build_parser().parse_args(["serve", "--port", "9002", "--overlay-lines", "2"])
    settings = load_settings(args)
    assert settings.port == 9002 and settings.overlay_lines == 2


def test_dotenv_in_the_working_directory_is_loaded(tmp_path):
    Path(".env").write_text("PLAYLIST_PLAYER_SYNC_WORKERS=5\n")  # conftest chdir'd to tmp_path
    assert load_settings().sync_workers == 5


def test_token_comes_from_the_environment_and_is_never_a_cli_flag(monkeypatch, capsys):
    monkeypatch.setenv("APIFY_TOKEN", "synthetic-token-value")
    settings = load_settings()
    assert settings.require_apify_token() == "synthetic-token-value"
    assert "synthetic-token-value" not in repr(settings) and "<set>" in repr(settings)
    with pytest.raises(SystemExit):
        build_parser().parse_args(["sync", "PL1", "--apify-token", "x"])
    assert "unrecognized arguments" in capsys.readouterr().err


def test_missing_token_fails_naming_the_variable():
    with pytest.raises(ConfigError, match="APIFY_TOKEN"):
        load_settings().require_apify_token()


@pytest.mark.parametrize(
    ("name", "value", "match"),
    [
        ("PLAYLIST_PLAYER_HOST", "0.0.0.0", "loopback"),
        ("PLAYLIST_PLAYER_HOST", "192.168.1.5", "loopback"),
        ("PLAYLIST_PLAYER_HOST", "example.com", "loopback"),
        ("PLAYLIST_PLAYER_PORT", "70000", "PORT"),
        ("PLAYLIST_PLAYER_SYNC_WORKERS", "0", "SYNC_WORKERS"),
        ("PLAYLIST_PLAYER_AUDIO_DURATION_REJECT_S", "1", "REJECT_S"),
        ("PLAYLIST_PLAYER_OVERLAY_LINES", "3", "OVERLAY_LINES"),
    ],
)
def test_invalid_settings_are_rejected(monkeypatch, name, value, match):
    monkeypatch.setenv(name, value)
    with pytest.raises(ConfigError, match=match):
        load_settings()


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1", "127.0.0.2"])
def test_loopback_hosts_are_accepted(monkeypatch, host):
    monkeypatch.setenv("PLAYLIST_PLAYER_HOST", host)
    assert load_settings().host == host
