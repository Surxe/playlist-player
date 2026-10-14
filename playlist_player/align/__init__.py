"""Word timing from the audio: where each word of a synced lyric line is really sung.

``plan`` is the arithmetic (pure, no dependencies), ``engine`` is the listening (PyTorch
and Demucs, imported only when used; see ``requirements-align.txt``), and ``run`` goes
through tracks and writes ``words.json`` into the cache.
"""

from .plan import MIN_CONFIDENCE, WordSpan, settle, spell, windows
from .run import AlignReport, Engine, align_tracks, summary

__all__ = [
    "MIN_CONFIDENCE",
    "AlignReport",
    "Engine",
    "WordSpan",
    "align_tracks",
    "settle",
    "spell",
    "summary",
    "windows",
]
