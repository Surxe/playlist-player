"""Which lyric line is current at a given playback position.

One formula, used by the overlay and mirrored for the page in
``server/static/clock.js``; both are tested against the same table of cases::

    index = bisect_right(times, position_ms + lead_ms + offset_ms) - 1

``lead_ms`` shows a line slightly before its timestamp, which reads better than exactly
on it. ``offset_ms`` is the per-track correction: positive when the lyrics are late
against the audio and need to come sooner. An index of -1 means "before the first line".
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Iterable, Sequence

from ..models import LyricLine


def line_times(lines: Iterable[LyricLine]) -> list[int]:
    """The timestamps of ``lines`` (already sorted), ready for :func:`current_index`."""
    return [line.t_ms for line in lines]


def current_index(
    times: Sequence[int], position_ms: float, *, lead_ms: int = 0, offset_ms: int = 0
) -> int:
    """Index of the line showing at ``position_ms``, or -1 before the first line.

    When several lines share a timestamp the last of them is current.
    """
    return bisect_right(times, position_ms + lead_ms + offset_ms) - 1
