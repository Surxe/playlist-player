"""Guards against the repo silently losing source files.

The data directory is called ``cache`` and so is a source package. An unanchored
``cache/`` rule in ``.gitignore`` once kept ``spotifixed/cache/`` out of a commit
while every test still passed, because the files were present on the author's disk.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from spotifixed.config import REPO_ROOT

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None or not (REPO_ROOT / ".git").exists(), reason="not a git checkout"
)

SOURCE_DIRS = ("spotifixed", "tests", "scripts")


def _source_files() -> list[str]:
    files = []
    for name in SOURCE_DIRS:
        for path in sorted((REPO_ROOT / name).rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                files.append(str(path.relative_to(REPO_ROOT)))
    return files


def test_no_source_file_is_gitignored():
    result = subprocess.run(
        ["git", "check-ignore", "--stdin"],
        input="\n".join(_source_files()),
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    ignored = result.stdout.split()
    assert not ignored, f"source files hidden by .gitignore: {ignored}"


def test_data_directories_are_still_ignored():
    for name in (
        "cache/playlists/x/tracklist.raw.json",
        "state/tracks/x.json",
        "implementation/x.md",
    ):
        result = subprocess.run(["git", "check-ignore", "-q", name], cwd=REPO_ROOT)
        assert result.returncode == 0, f"{name} must stay out of git"
    assert Path(REPO_ROOT / ".gitignore").read_text().count("\n/cache/\n") == 1
