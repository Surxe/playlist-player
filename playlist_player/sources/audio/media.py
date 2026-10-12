"""ffprobe and ffmpeg: measure an audio file, and rewrap it with tags.

Two rules. The duration that matters is the one measured from the file, because lyric
timestamps are matched against it; a provider's metadata is only a hint. And audio is
never re-encoded: the stream is copied into a container that can carry title and artist
tags, so the desktop's media widget shows a proper title.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from ...errors import SourceError

# Codec -> file extension of a container that holds it and carries tags. ffmpeg picks
# the muxer from the extension (".opus" is Ogg Opus). Anything else keeps its container.
_EXTENSIONS = {"opus": "opus", "vorbis": "ogg", "aac": "m4a", "mp3": "mp3", "flac": "flac"}

_TIMEOUT_S = 120


@dataclass(frozen=True, slots=True)
class MediaInfo:
    duration_s: float
    codec: str
    container: str


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=_TIMEOUT_S)
    except FileNotFoundError as exc:
        raise SourceError(f"{command[0]} is not installed") from exc
    except subprocess.TimeoutExpired as exc:
        raise SourceError(f"{command[0]} timed out after {_TIMEOUT_S} s") from exc


def probe(path: Path) -> MediaInfo:
    """Measure ``path``. Raises ``SourceError`` when it holds no readable audio stream."""
    result = _run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "format=duration,format_name:stream=codec_name,duration",
            "-of",
            "json",
            str(path),
        ]  # fmt: skip
    )
    try:
        data = json.loads(result.stdout or "{}")
        stream = data["streams"][0]
        duration = float(data["format"].get("duration") or stream["duration"])
    except (ValueError, KeyError, IndexError, TypeError):
        detail = " ".join(result.stderr.split())[:200]
        raise SourceError(f"not a readable audio file: {path.name} {detail}".rstrip()) from None
    if duration <= 0:
        raise SourceError(f"audio file has no duration: {path.name}")
    return MediaInfo(duration, str(stream.get("codec_name") or ""), data["format"]["format_name"])


def tagged_extension(codec: str, fallback: str) -> str:
    """The extension to rewrap ``codec`` into; ``fallback`` when the codec is unfamiliar."""
    return _EXTENSIONS.get(codec, fallback.lstrip(".") or "mka")


def remux(source: Path, dest: Path, tags: Mapping[str, str]) -> None:
    """Copy the audio stream of ``source`` into ``dest`` unchanged, replacing all tags."""
    command = [
        "ffmpeg", "-v", "error", "-nostdin", "-y", "-i", str(source),
        "-map", "0:a:0", "-vn", "-c:a", "copy", "-map_metadata", "-1",
    ]  # fmt: skip
    for key, value in tags.items():
        if value:
            command += ["-metadata", f"{key}={value}"]
    result = _run([*command, str(dest)])
    if result.returncode != 0 or not dest.is_file() or dest.stat().st_size == 0:
        detail = " ".join(result.stderr.split())[:200]
        raise SourceError(f"ffmpeg could not rewrap {source.name}: {detail}")
