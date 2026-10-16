"""Lyrics sources. One today: LRCLIB."""

from .base import LyricsRecord, LyricsSource
from .lrclib import LrclibSource

__all__ = ["LrclibSource", "LyricsRecord", "LyricsSource"]
