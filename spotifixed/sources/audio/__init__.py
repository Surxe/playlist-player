"""Audio sources. One today: yt-dlp. ``media`` wraps ffprobe/ffmpeg for any of them."""

from . import media
from .base import AudioCandidate, AudioSource, DownloadedAudio
from .media import MediaInfo
from .ytdlp import YtDlpSource

__all__ = [
    "AudioCandidate",
    "AudioSource",
    "DownloadedAudio",
    "MediaInfo",
    "YtDlpSource",
    "media",
]
