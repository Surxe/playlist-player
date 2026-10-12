"""Synthetic audio for tests: ffmpeg-generated tones and a stand-in audio source."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from playlist_player.config import REPO_ROOT
from playlist_player.errors import SourceError
from playlist_player.sources.audio import AudioCandidate, DownloadedAudio

MAKE_TONE = REPO_ROOT / "scripts" / "make-test-audio.sh"
_made: dict[tuple[float, str], Path] = {}


def make_tone(dest: Path, seconds: float = 5, *, frequency: int = 440) -> Path:
    """Write a tone to ``dest`` (format from its extension) with the repo's script."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["bash", str(MAKE_TONE), str(dest), str(seconds), str(frequency)],
        check=True,
        capture_output=True,
        timeout=60,
    )
    return dest


def tone(cache_dir: Path, seconds: float = 5, extension: str = "webm") -> Path:
    """A tone of the given length, generated once per test session and reused."""
    key = (seconds, extension)
    if key not in _made or not _made[key].exists():
        _made[key] = make_tone(cache_dir / f"tone-{seconds}.{extension}", seconds)
    return _made[key]


def candidate(cid: str, duration: float | None, **overrides) -> AudioCandidate:
    fields = {
        "id": cid,
        "url": f"https://video.invalid/watch?v={cid}",
        "title": "Made Up Artist 0 - Invented Song 0",
        "channel": "Made Up Artist 0",
        "duration_s": duration,
        "description": "",
    }
    fields.update(overrides)
    return AudioCandidate(**fields)


class FakeAudioSource:
    """Scripted search results; "downloads" copy a generated tone of a chosen length.

    ``lengths`` maps a URL to the real length in seconds of the file it serves, so a
    test can make the delivered audio differ from what the search result claimed.
    A URL in ``broken`` fails to download.
    """

    name = "fake"

    def __init__(self, tones_dir: Path, results=(), *, lengths=None, broken=(), extension="webm"):
        self.tones_dir = tones_dir
        self.results = list(results)  # one list of candidates per successive search
        self.lengths = dict(lengths or {})
        self.broken = set(broken)
        self.extension = extension
        self.searches: list[str] = []
        self.downloads: list[str] = []

    def search(self, query: str, limit: int = 10) -> list[AudioCandidate]:
        self.searches.append(query)
        index = min(len(self.searches), len(self.results)) - 1
        return list(self.results[index]) if self.results else []

    def download(self, url: str, directory: Path) -> DownloadedAudio:
        self.downloads.append(url)
        if url in self.broken:
            raise SourceError(f"yt-dlp failed: video unavailable ({url})")
        seconds = self.lengths.get(url, 5)
        path = directory / f"download.{self.extension}"
        shutil.copyfile(tone(self.tones_dir, seconds, self.extension), path)
        cid = url.rsplit("=", 1)[-1]
        found = candidate(cid, seconds, url=url, channel="Made Up Artist 0 - Topic")
        return DownloadedAudio(path=path, candidate=found)
