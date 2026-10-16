"""Audio through yt-dlp, used as a library (never by shelling out).

Search is a flat extraction: one request that returns titles, channels, durations and
the first lines of each description, and fetches no media. Download asks for an
audio-only stream, preferring Opus because that is what the provider stores natively,
so nothing is ever transcoded.

yt-dlp breaks whenever YouTube changes something, which is often. Every yt-dlp error is
turned into a ``SourceError`` so one bad track cannot stop a sync; the first remedy is
always ``.venv/bin/pip install -U yt-dlp``.

yt-dlp is imported inside the methods: the daemon and overlay must run on a Python
that does not have it, and nothing else in the package should pay for importing it.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from ...errors import SourceError
from .base import AudioCandidate, DownloadedAudio

_FORMAT = "bestaudio[acodec=opus]/bestaudio"


class _Silent:
    """yt-dlp logger that drops everything; failures surface as exceptions instead."""

    def debug(self, _message: str) -> None: ...
    def info(self, _message: str) -> None: ...
    def warning(self, _message: str) -> None: ...
    def error(self, _message: str) -> None: ...


def _js_runtimes() -> dict[str, dict[str, str]]:
    # YouTube extraction increasingly needs a JavaScript runtime. yt-dlp only looks for
    # deno by default; offer node too when it is installed. Neither being present still
    # works today, with fewer formats.
    found = {name: shutil.which(name) for name in ("deno", "node")}
    return {name: {"path": path} for name, path in found.items() if path}


class YtDlpSource:
    name = "ytdlp"

    def __init__(self, *, socket_timeout_s: float = 30.0) -> None:
        self._common: dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "logger": _Silent(),
            "noplaylist": True,
            "socket_timeout": socket_timeout_s,
            "js_runtimes": _js_runtimes(),
        }

    def search(self, query: str, limit: int = 10) -> list[AudioCandidate]:
        options = {**self._common, "extract_flat": True, "skip_download": True}
        info = self._extract(f"ytsearch{limit}:{query}", options, download=False)
        entries = (info or {}).get("entries") or []
        candidates = (_candidate(entry) for entry in entries)
        return [candidate for candidate in candidates if candidate is not None]

    def download(self, url: str, directory: Path) -> DownloadedAudio:
        options = {
            **self._common,
            "format": _FORMAT,
            "outtmpl": str(directory / "download.%(ext)s"),
            "retries": 3,
            "fragment_retries": 3,
            "overwrites": True,
        }
        info = self._extract(url, options, download=True) or {}
        files = [item.get("filepath") for item in info.get("requested_downloads") or []]
        path = Path(files[0]) if files and files[0] else None
        if path is None or not path.is_file() or path.stat().st_size == 0:
            raise SourceError(f"yt-dlp reported success for {url} but left no file")
        candidate = _candidate(info) or AudioCandidate(id="", url=url, title="")
        return DownloadedAudio(path=path, candidate=candidate)

    @staticmethod
    def _extract(target: str, options: dict[str, Any], *, download: bool) -> dict[str, Any] | None:
        import yt_dlp
        from yt_dlp.utils import YoutubeDLError

        try:
            with yt_dlp.YoutubeDL(options) as ydl:
                return ydl.extract_info(target, download=download)
        except YoutubeDLError as exc:
            message = " ".join(str(exc).split())[:300]
            raise SourceError(f"yt-dlp failed: {message}") from exc


def _candidate(entry: Any) -> AudioCandidate | None:
    if not isinstance(entry, dict) or not entry.get("id"):
        return None
    duration = entry.get("duration")
    url = entry.get("webpage_url") or entry.get("url") or entry.get("original_url") or ""
    return AudioCandidate(
        id=str(entry["id"]),
        url=str(url),
        title=str(entry.get("title") or ""),
        channel=str(entry.get("channel") or entry.get("uploader") or ""),
        duration_s=float(duration) if isinstance(duration, int | float) else None,
        description=str(entry.get("description") or ""),
    )
