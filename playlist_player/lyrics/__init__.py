"""Timed lyrics: parsing the text format and finding the line, and
estimating the word, for a playback position.

Pure functions over :mod:`playlist_player.models`; no I/O, so the daemon, the sync
commands and the tests all use them directly.
"""

from .lrc import parse_lrc
from .timeline import current_index, line_times
from .words import current_word, word_ends, word_ranges

__all__ = [
    "current_index",
    "current_word",
    "line_times",
    "parse_lrc",
    "word_ends",
    "word_ranges",
]
