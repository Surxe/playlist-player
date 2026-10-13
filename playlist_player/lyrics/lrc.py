"""Parse timed-lyric (LRC) text into :class:`LyricLine` objects.

The format, as it turns up in practice::

    [ar:Some Artist]              metadata tag: ignored
    [offset:+250]                 shifts every timestamp; see below
    [00:12.34] first line         minutes:seconds.fraction
    [00:15.5][01:02.500] chorus   one line of text at several times
    [00:20.00]                    no text: a deliberate gap, show nothing

Rules applied here:

* The fraction is a decimal fraction of a second with any number of digits, or absent.
  ``[mm:ss:xx]`` (a colon before the fraction) is accepted too.
* Only timestamps at the start of a line count. Brackets later in a line are text.
* A positive ``offset`` makes lyrics appear sooner, so it is subtracted from every
  timestamp. That is the format's own convention, and the opposite sign from this
  tool's per-track ``offset_ms`` (which is added to the playback position).
* Inline word timestamps (``<00:12.80>``) are dropped; word-level display is not built.
* A line with no leading timestamp carries no time and is skipped.
* Output is sorted by time; lines sharing a timestamp keep their order in the text.
"""

from __future__ import annotations

import re

from ..models import LyricLine

_TIMESTAMP = re.compile(r"\[(\d{1,3}):(\d{1,2})(?:[.:](\d{1,6}))?\]")
_OFFSET = re.compile(r"^\[offset:\s*([+-]?\d+)\s*\]$", re.IGNORECASE)
_WORD_TIMESTAMP = re.compile(r"<\d{1,3}:\d{1,2}(?:[.:]\d{1,6})?>")


def _to_ms(minutes: str, seconds: str, fraction: str | None) -> int:
    millis = int((fraction or "0").ljust(3, "0")[:3])
    return (int(minutes) * 60 + int(seconds)) * 1000 + millis


def parse_lrc(text: str) -> tuple[LyricLine, ...]:
    """Return the timed lines in ``text``, sorted by time. Never raises on bad input."""
    offset_ms = 0
    timed: list[tuple[int, str]] = []
    for raw in text.splitlines():
        line = raw.strip().lstrip("﻿")
        offset = _OFFSET.match(line)
        if offset:
            offset_ms = int(offset.group(1))
            continue

        stamps: list[int] = []
        rest = line
        while True:
            match = _TIMESTAMP.match(rest)
            if not match:
                break
            stamps.append(_to_ms(*match.groups()))
            rest = rest[match.end() :].lstrip()
        if not stamps:
            continue  # a metadata tag, a blank line, or untimed text
        words = _WORD_TIMESTAMP.sub("", rest)
        content = " ".join(words.split())
        timed.extend((stamp, content) for stamp in stamps)

    # The offset tag applies to the whole file wherever it appears, so shift at the end.
    lines = [LyricLine(max(0, stamp - offset_ms), content) for stamp, content in timed]
    lines.sort(key=lambda entry: entry.t_ms)  # stable: equal times keep file order
    return tuple(lines)
