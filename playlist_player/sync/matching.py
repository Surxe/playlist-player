"""Choosing between candidates. Pure scoring: no I/O, tested on canned lists.

Lyrics: a provider returns several records for one song, typically the same words timed
against different releases whose lengths differ by a few seconds. The record whose
duration is closest to the audio actually being played is the one whose timestamps will
line up, so duration is the tie-breaker after "has timed lyrics at all".
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass

from ..lyrics import parse_lrc
from ..models import LyricsKind, Track
from ..sources.lyrics import LyricsRecord

# ------------------------------ name matching ------------------------------ #

_BRACKETED = re.compile(r"\s*[(\[][^)\]]*[)\]]")
# " - Remastered 2011", " - Single Version", " - Radio Edit", " - Live at ..."
_DASH_SUFFIX = re.compile(
    r"\s+[-–—]\s+(?=[^-–—]*\b("
    r"remaster(?:ed)?|version|edit|mix|remix|mono|stereo|single|radio|bonus|deluxe|live|"
    r"acoustic|demo|feat|featuring|from|with|soundtrack|anniversary"
    r")\b).*$",
    re.IGNORECASE,
)
_FEATURING = re.compile(r"\s+(?:feat\.?|ft\.?|featuring)\s+.*$", re.IGNORECASE)


def _fold(text: str) -> str:
    """Case-fold, drop accents and punctuation, collapse spaces."""
    decomposed = unicodedata.normalize("NFKD", text.replace("&", " and "))
    kept = (c if c.isalnum() else " " for c in decomposed if not unicodedata.combining(c))
    return " ".join("".join(kept).casefold().split())


def normalize_title(title: str) -> str:
    """A title reduced to what identifies the song, for comparing across catalogues.

    Drops bracketed suffixes ("(Remastered 2011)", "[feat. X]"), release qualifiers
    after a dash ("- Single Version"), a trailing "feat. X", accents, punctuation and
    case. A title that is nothing but such decoration is kept as it is.
    """
    stripped = _FEATURING.sub("", _DASH_SUFFIX.sub("", _BRACKETED.sub("", title)))
    return _fold(stripped) or _fold(title)


def normalize_artist(name: str) -> str:
    return _fold(name)


def is_same_song(track: Track, record: LyricsRecord) -> bool:
    """Whether a provider's record names the same song and artist as ``track``."""
    if normalize_title(track.title) != normalize_title(record.title):
        return False
    theirs = normalize_artist(record.artist)
    ours = [normalize_artist(name) for name in track.artists]
    if not theirs or not ours:
        return False
    # Providers credit collaborations in one string ("A feat. B", "A, B", "A & B"), so
    # the primary artist appearing in it as whole words is a match, as is the record
    # crediting any one of the track's artists alone.
    return theirs in ours or f" {ours[0]} " in f" {theirs} "


def matching_records(track: Track, records: Iterable[LyricsRecord]) -> list[LyricsRecord]:
    return [record for record in records if is_same_song(track, record)]


# ----------------------------- lyric selection ----------------------------- #

_KIND_RANK = {LyricsKind.SYNCED: 0, LyricsKind.PLAIN: 1, LyricsKind.INSTRUMENTAL: 2}


@dataclass(frozen=True, slots=True)
class LyricsChoice:
    record: LyricsRecord
    kind: LyricsKind
    duration_delta_s: float | None  # record minus reference; None when the record has none
    timing_confidence: str | None  # "high" | "low" for synced lyrics, else None


def record_kind(record: LyricsRecord) -> LyricsKind:
    """What a record can actually provide. Timed text that parses to nothing is not synced."""
    if record.synced and parse_lrc(record.synced):
        return LyricsKind.SYNCED
    if record.plain and record.plain.strip():
        return LyricsKind.PLAIN
    if record.instrumental:
        return LyricsKind.INSTRUMENTAL
    return LyricsKind.MISSING


def choose_lyrics(
    records: Iterable[LyricsRecord], reference_duration_s: float, tolerance_s: float
) -> LyricsChoice | None:
    """Pick the best of ``records`` (already known to be the right song), or ``None``.

    Synced beats plain beats instrumental; within a kind the smallest distance from
    ``reference_duration_s`` wins, and a record with no duration loses to one that has
    it. A synced record outside ``tolerance_s`` is still chosen, flagged low confidence:
    slightly late lyrics that can be nudged are better than none.
    """
    best: tuple[tuple[int, float], LyricsChoice] | None = None
    seen: set[str] = set()
    for record in records:
        kind = record_kind(record)
        if kind is LyricsKind.MISSING or record.id in seen:
            continue
        seen.add(record.id)
        delta = None if record.duration_s is None else record.duration_s - reference_duration_s
        distance = float("inf") if delta is None else abs(delta)
        confidence = None
        if kind is LyricsKind.SYNCED:
            confidence = "high" if distance <= tolerance_s else "low"
        key = (_KIND_RANK[kind], distance)
        if best is None or key < best[0]:  # strict: an earlier record wins a tie
            rounded = None if delta is None else round(delta, 3)
            best = (key, LyricsChoice(record, kind, rounded, confidence))
    return best[1] if best else None
