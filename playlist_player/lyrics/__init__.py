"""Timed lyrics: parsing the text format and finding the line for a playback position.

Pure functions over :mod:`playlist_player.models`; no I/O, so the daemon, the sync
commands and the tests all use them directly.
"""

from .lrc import parse_lrc
from .timeline import current_index, line_times

__all__ = ["current_index", "line_times", "parse_lrc"]
