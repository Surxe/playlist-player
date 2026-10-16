"""Which word of a lyric line is being sung at a given moment: an estimate.

Lyric files time lines, not words, so a word's moment is worked out from the line's. It
starts from the same span the line progress uses (this line's timestamp to the next
line's) and corrects the one thing that span gets wrong for words: it includes whatever
follows the singing, a breath or a whole instrumental.

* A word's share of its line is its syllable count (:func:`syllables`, a rough count of
  vowel groups; it only has to be right in proportion).
* A line's pace is its span divided by its syllables. The typical pace around a line is
  the median pace of the lines near it (``WINDOW`` either side), so a verse and a chorus
  are each judged by their own tempo. The median ignores the few lines that are followed
  by an instrumental, which is what makes it usable as the yardstick for them.
* The singing in a line is taken to last no longer than its syllables at ``SLACK`` times
  the typical pace. A line whose span is within that is sung across all of it; a longer
  one is sung for that long and the rest is its trail, during which no word is current.
* The last line has no following timestamp, so it is the estimate alone.

:func:`word_ends` gives, for every line, when each of its words ends, counted from the
line's own timestamp; :func:`current_word` looks a moment up in one line's ends. Both are
mirrored for the page in ``server/static/clock.js`` and tested against the same table,
``tests/fixtures/word_cases.json``.

What this cannot see: phrasing inside a line (every syllable gets the same time), and a
note held at the end of a line, which looks the same as a trail.

A line that has been aligned to the audio (``LyricLine.words``, from the ``align``
command) needs no estimate: :func:`aligned_word` looks the moment up in the times that
were heard. Those are in the audio's own time, so the track's offset does not move them.
"""

from __future__ import annotations

import re
import unicodedata
from bisect import bisect_right
from collections.abc import Sequence
from statistics import median

from ..models import LyricLine

# How much slower than the typical pace a line may be sung before the excess is a trail.
SLACK = 1.3
# Lines either side of a line whose pace says what is typical for it.
WINDOW = 4
# Fewer paces than this near a line and the whole track's are used instead.
_MIN_NEARBY = 3

# An aligned word is underlined this long before it is sung: a word that changes exactly
# on its first sound reads as late.
ALIGNED_LEAD_MS = 50

_WORD = re.compile(r"\S+")
_NOT_A_LETTER = re.compile(r"[^a-z]")
_VOWELS = re.compile(r"[aeiouy]+")


def syllables(word: str) -> int:
    """A rough syllable count for ``word``; at least 1, whatever it is made of."""
    letters = _NOT_A_LETTER.sub("", unicodedata.normalize("NFD", word).lower())
    count = len(_VOWELS.findall(letters))
    if count > 1 and letters.endswith("e") and not letters.endswith(("le", "ee")):
        count -= 1  # a silent final e: "love", "time"
    return max(1, count)


def word_ranges(text: str) -> list[tuple[int, int]]:
    """Where each word of ``text`` starts and ends, as character offsets."""
    return [match.span() for match in _WORD.finditer(text)]


def word_ends(lines: Sequence[LyricLine]) -> list[tuple[float, ...]]:
    """For each line, when each of its words ends, in ms after the line's timestamp.

    A line's last entry is where its singing is taken to end. A line gets no entries when
    it has no words, or when nothing can be said about its length.
    """
    weights = [[syllables(line.text[a:b]) for a, b in word_ranges(line.text)] for line in lines]
    # The last line runs until the track ends, which is not known here: no span.
    spans = [
        lines[index + 1].t_ms - line.t_ms if index + 1 < len(lines) else None
        for index, line in enumerate(lines)
    ]
    paces = [
        span / sum(line) if line and span is not None and span > 0 else None
        for line, span in zip(weights, spans, strict=True)
    ]
    known = [pace for pace in paces if pace is not None]
    overall = median(known) if known else None

    ends: list[tuple[float, ...]] = []
    for index, (line, span) in enumerate(zip(weights, spans, strict=True)):
        nearby = [p for p in paces[max(0, index - WINDOW) : index + WINDOW + 1] if p is not None]
        typical = median(nearby) if len(nearby) >= _MIN_NEARBY else overall
        total = sum(line)
        if not line or typical is None or (span is not None and span <= 0):
            ends.append(())
            continue
        sung = total * typical * SLACK
        if span is not None:
            sung = min(span, sung)
        done = 0
        line_ends = []
        for weight in line:
            done += weight
            line_ends.append(sung * done / total)
        ends.append(tuple(line_ends))
    return ends


def current_word(ends: Sequence[float], elapsed_ms: float) -> int:
    """Index of the word being sung ``elapsed_ms`` into its line, or -1 for none.

    ``ends`` is one line's entry from :func:`word_ends`. There is no current word before
    the line starts, or once its singing has ended (the trail).
    """
    if not ends or elapsed_ms < 0 or elapsed_ms >= ends[-1]:
        return -1
    return bisect_right(ends, elapsed_ms)


def aligned_word(words: Sequence[tuple[int, int]], position_ms: float) -> int:
    """Index of the word being sung at ``position_ms`` of the audio, or -1 for none.

    ``words`` are one line's ``(start_ms, end_ms)`` from the alignment, in order. A word
    is current from its start until the next one starts; there is none before the first
    starts or after the last ends.
    """
    moment = position_ms + ALIGNED_LEAD_MS
    if not words or moment < words[0][0] or moment >= words[-1][1]:
        return -1
    return bisect_right(words, moment, key=lambda word: word[0]) - 1
