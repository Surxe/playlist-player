"""The lyrics stage: find the record timed against the audio we actually have.

Goal: synced lyrics on disk. Order of attempts:

1. Ask the source for an exact match on title, artist, album and duration.
2. If that has no timed text, search, keep the records that are the same song, and pick
   the best (synced first, then closest duration).

The reference duration is the downloaded audio's measured length when there is audio,
because that is what the overlay will be timed against; Spotify's figure is the
fallback. Lyrics chosen against Spotify's figure, or against audio that has since been
replaced, are chosen again against the audio there now.

Anything short of synced lyrics (plain text, an instrumental marker, nothing at all) is
saved for what it is and reported as a miss, so the search is repeated after the retry
window in case someone has contributed timings since.
"""

from __future__ import annotations

from datetime import UTC, datetime

from ..cache import CacheStore, assets
from ..models import LyricsInfo, LyricsKind, Track
from ..sources.lyrics import LyricsSource
from .matching import LyricsChoice, choose_lyrics, matching_records, record_kind
from .stages import StageResult

_MISS_REASON = {LyricsKind.PLAIN: "plain_only", LyricsKind.INSTRUMENTAL: "instrumental"}


class LyricsStage:
    name = "lyrics"

    def __init__(self, store: CacheStore, source: LyricsSource, *, tolerance_s: float) -> None:
        self._store = store
        self._source = source
        self._tolerance_s = tolerance_s

    def is_done(self, track: Track) -> bool:
        info = assets.read_lyrics_info(self._store, track.id)
        if info is None or info.kind is not LyricsKind.SYNCED:
            return False
        audio = assets.read_audio(self._store, track.id)
        if audio is None:
            return True
        # Chosen before there was audio, or against audio since replaced (one found to have
        # no singing, say): choose again against the length of the file there now.
        return info.reference == "audio" and info.reference_duration_s == round(audio.duration_s, 3)

    def run(self, track: Track) -> StageResult:
        audio = assets.read_audio(self._store, track.id)
        reference = "audio" if audio else "spotify"
        duration_s = audio.duration_s if audio else track.duration_s

        chosen_by = "exact"
        exact = self._source.get(track, duration_s)
        choice = choose_lyrics([exact], duration_s, self._tolerance_s) if exact else None
        if choice is None or choice.kind is not LyricsKind.SYNCED:
            found = matching_records(track, self._source.search(track))
            searched = choose_lyrics(found, duration_s, self._tolerance_s)
            if searched and (choice is None or _better(searched, choice)):
                choice, chosen_by = searched, "search"

        if choice is None:
            assets.clear_lyrics(self._store, track.id)
            return StageResult.miss("not_found")

        record = choice.record
        text = {LyricsKind.SYNCED: record.synced, LyricsKind.PLAIN: record.plain}.get(choice.kind)
        info = LyricsInfo(
            kind=choice.kind,
            source=self._source.name,
            record_id=record.id,
            record_duration_s=record.duration_s,
            reference=reference,
            reference_duration_s=round(duration_s, 3),
            duration_delta_s=choice.duration_delta_s,
            timing_confidence=choice.timing_confidence,
            chosen_by=chosen_by,
            fetched_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        assets.write_lyrics(self._store, track.id, info, text)

        if choice.kind is LyricsKind.SYNCED:
            return StageResult.done(f"synced, {choice.timing_confidence} confidence")
        return StageResult.miss(_MISS_REASON[choice.kind], choice.kind.value)


def _better(candidate: LyricsChoice, current: LyricsChoice) -> bool:
    """Whether a searched record should replace the exact one: only for a better kind."""
    order = [LyricsKind.SYNCED, LyricsKind.PLAIN, LyricsKind.INSTRUMENTAL]
    return order.index(candidate.kind) < order.index(record_kind(current.record))
