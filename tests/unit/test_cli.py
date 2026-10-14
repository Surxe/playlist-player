"""CLI wiring: every command parses, and expected failures become one clean line."""

from __future__ import annotations

import subprocess
import sys

import pytest

from playlist_player import cli
from playlist_player.config import REPO_ROOT
from playlist_player.errors import ConfigError

COMMANDS = ["sync", "status", "pin", "serve", "ctl", "overlay", "start"]


def test_help_lists_every_command(capsys):
    with pytest.raises(SystemExit) as info:
        cli.main(["--help"])
    assert info.value.code == 0
    out = capsys.readouterr().out
    assert all(f"    {name} " in out for name in COMMANDS)


@pytest.mark.parametrize(
    "argv",
    [
        ["sync", "PL1", "--limit", "3", "--only", "audio", "--dry-run"],
        ["status"],
        ["status", "PL1"],
        ["pin", "T1", "http://example.invalid/v"],
        ["serve", "--port", "9100"],
        ["ctl", "seek", "+10"],
        ["overlay", "--demo"],
        ["start", "--no-overlay", "--no-browser", "--port", "9100"],
    ],
)
def test_each_command_parses_and_reaches_a_handler(argv):
    args = cli.build_parser().parse_args(argv)
    assert args.command == argv[0] and callable(args.handler)


def test_a_command_is_required(capsys):
    with pytest.raises(SystemExit) as info:
        cli.main([])
    assert info.value.code == 2


def test_expected_errors_print_one_line_and_exit_1(monkeypatch, capsys):
    def explode(_args):
        raise ConfigError("PLAYLIST_PLAYER_PORT must be 1-65535, got 0")

    monkeypatch.setattr(cli, "load_settings", explode)
    assert cli.main(["status"]) == 1
    err = capsys.readouterr().err
    assert err.strip() == "playlist_player: PLAYLIST_PLAYER_PORT must be 1-65535, got 0"


def test_runs_as_a_module():
    # The package is run from the repo, not installed, so the child needs the repo root as
    # its working directory (conftest moves this process into a temp directory).
    result = subprocess.run(
        [sys.executable, "-m", "playlist_player", "--version"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert result.returncode == 0 and result.stdout.strip() == "0.1.0"
