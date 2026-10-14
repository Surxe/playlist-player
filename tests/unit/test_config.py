"""Settings resolution: precedence, path handling, validation, and keeping the token off argv."""

from __future__ import annotations

from pathlib import Path

import pytest

from playlist_player.cli import build_parser
from playlist_player.config import REPO_ROOT, Settings, load_settings, read_env_file
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


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("true", True), ("On", True), ("1", True), ("false", False), ("no", False), ("", False)],
)
def test_a_switch_is_read_from_text(monkeypatch, raw, expected):
    monkeypatch.setenv("PLAYLIST_PLAYER_OVERLAY_UNDERLINE_WORD", raw)
    assert load_settings().overlay_underline_word is expected


def test_a_switch_that_is_neither_on_nor_off_is_refused(monkeypatch):
    monkeypatch.setenv("PLAYLIST_PLAYER_OVERLAY_UNDERLINE_WORD", "maybe")
    with pytest.raises(ConfigError, match="UNDERLINE_WORD must be true or false"):
        load_settings()


def test_dotenv_in_the_working_directory_is_loaded(tmp_path):
    Path(".env").write_text("PLAYLIST_PLAYER_SYNC_WORKERS=5\n")  # conftest chdir'd to tmp_path
    assert load_settings().sync_workers == 5


def test_precedence_is_flag_then_environment_then_dotenv_then_default(monkeypatch):
    Path(".env").write_text("PLAYLIST_PLAYER_PORT=9001\nPLAYLIST_PLAYER_SYNC_WORKERS=5\n")
    assert (load_settings().port, load_settings().sync_workers) == (9001, 5)
    monkeypatch.setenv("PLAYLIST_PLAYER_PORT", "9002")
    assert load_settings().port == 9002
    args = build_parser().parse_args(["serve", "--port", "9003"])
    settings = load_settings(args)
    assert (settings.port, settings.sync_workers, settings.miss_retry_days) == (9003, 5, 14)


def test_env_file_syntax(tmp_path):
    path = tmp_path / "some.env"
    path.write_text(
        "# a comment\n"
        "\n"
        'PLAYLIST_PLAYER_OVERLAY_FONT="Serif Bold Italic"\n'
        "PLAYLIST_PLAYER_OVERLAY_COLOR='#FFE066'\n"
        "export PLAYLIST_PLAYER_PORT=9100\n"
        "PLAYLIST_PLAYER_OVERLAY_ANCHOR=top   # where it sits\n"
        "PLAYLIST_PLAYER_MPV_AUDIO_OUTPUT=\n"
        "not a setting line\n"
    )
    assert read_env_file(path) == {
        "PLAYLIST_PLAYER_OVERLAY_FONT": "Serif Bold Italic",
        "PLAYLIST_PLAYER_OVERLAY_COLOR": "#FFE066",
        "PLAYLIST_PLAYER_PORT": "9100",
        "PLAYLIST_PLAYER_OVERLAY_ANCHOR": "top",
        "PLAYLIST_PLAYER_MPV_AUDIO_OUTPUT": "",
    }
    settings = load_settings(env_file=path)
    assert (settings.overlay_font, settings.overlay_color) == ("Serif Bold Italic", "#FFE066")
    assert (settings.port, settings.overlay_anchor) == (9100, "top")
    assert read_env_file(tmp_path / "absent.env") == {}


def test_the_generated_env_example_is_a_valid_env_file_of_defaults():
    """Copying .env.example to .env must change nothing."""
    assert load_settings(env_file=REPO_ROOT / ".env.example") == load_settings()


def test_an_explicit_environment_mapping_replaces_the_process_environment(monkeypatch):
    monkeypatch.setenv("PLAYLIST_PLAYER_PORT", "9001")
    assert load_settings(environ={"PLAYLIST_PLAYER_PORT": "9050"}).port == 9050
    assert load_settings(environ={}).port == 8765


def test_empty_values_mean_unset_for_numbers_and_empty_for_text(monkeypatch):
    monkeypatch.setenv("PLAYLIST_PLAYER_PORT", "")
    monkeypatch.setenv("PLAYLIST_PLAYER_OVERLAY_MONITOR", "")
    monkeypatch.setenv("APIFY_TOKEN", "")
    settings = load_settings()
    assert (settings.port, settings.overlay_monitor, settings.apify_token) == (8765, "", None)


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("PLAYLIST_PLAYER_PORT", "eighty", "PLAYLIST_PLAYER_PORT must be a whole number"),
        ("PLAYLIST_PLAYER_PORT", "80.5", "PLAYLIST_PLAYER_PORT must be a whole number"),
        ("PLAYLIST_PLAYER_AUDIO_DURATION_TOLERANCE_S", "x", "must be a number"),
        ("PLAYLIST_PLAYER_OVERLAY_ANCHOR", "middle", "must be one of top, bottom"),
    ],
)
def test_values_of_the_wrong_type_name_the_setting(monkeypatch, name, value, message):
    monkeypatch.setenv(name, value)
    with pytest.raises(ConfigError, match=message):
        load_settings()


def test_flags_are_generated_from_the_schema(capsys):
    with pytest.raises(SystemExit):
        build_parser().parse_args(["serve", "--help"])
    out = capsys.readouterr().out
    assert "--port PORT" in out and "--overlay-anchor" in out and "(default: 8765)" in out
    assert "apify-token" not in out
    with pytest.raises(SystemExit):
        build_parser().parse_args(["overlay", "--overlay-anchor", "middle"])
    assert "invalid choice" in capsys.readouterr().err
    # A badly typed flag is reported like a badly typed variable, by name.
    with pytest.raises(ConfigError, match="PLAYLIST_PLAYER_PORT must be a whole number"):
        load_settings(build_parser().parse_args(["serve", "--port", "abc"]))


def test_the_package_needs_nothing_outside_the_standard_library_to_load_settings():
    """serve, ctl and overlay must run on a bare interpreter."""
    import subprocess
    import sys

    code = (
        "import sys\n"
        f"sys.path.insert(0, {str(REPO_ROOT)!r})\n"
        "sys.modules['optionsconfig'] = None; sys.modules['dotenv'] = None\n"
        "sys.modules['loguru'] = None; sys.modules['yt_dlp'] = None\n"
        "import playlist_player.cli, playlist_player.server.app, playlist_player.server.ctl\n"
        "import playlist_player.overlay.main\n"
        "from playlist_player.config import load_settings\n"
        "print(load_settings().port)\n"
    )
    # -I and -S: no site-packages at all, neither the virtualenv's nor the system's.
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-c", code], capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "8765"


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
