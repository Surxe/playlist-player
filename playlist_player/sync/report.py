"""What ``sync`` and ``status`` print. Counts, IDs and titles only; never lyric text."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence

from ..cache import Miss
from ..cache.assets import TrackStatus
from ..models import LyricsKind, Playlist
from .pipeline import TrackReport
from .stages import Outcome
from .tracklist import TracklistResult


def tracklist_summary(result: TracklistResult) -> list[str]:
    playlist = result.tracklist.playlist
    origin = "fetched now" if result.fetched_now else "from cache"
    when = f", fetched {result.fetched_at}" if result.fetched_at else ""
    total = "unknown" if playlist.reported_total is None else str(playlist.reported_total)
    lines = [
        f"Playlist {playlist.id}: {playlist.name}",
        f"  tracklist     {origin} ({result.source}{when})",
        f"  tracks        {len(playlist.track_ids)} playable, {playlist.skipped} skipped, "
        f"source total {total}",
    ]
    duplicates = len(playlist.track_ids) - len(result.tracklist.tracks)
    if duplicates:
        lines.append(f"  duplicates    {duplicates} repeated entries share a cached track")
    return lines


def shortfall_warning(result: TracklistResult) -> str | None:
    """A line for stderr when the source returned fewer entries than the playlist holds."""
    playlist = result.tracklist.playlist
    if not playlist.shortfall:
        return None
    return (
        f"WARNING: the tracklist is incomplete: {playlist.shortfall} of "
        f"{playlist.reported_total} entries are missing from the source's response."
    )


def progress_line(index: int, total: int, report: TrackReport) -> str:
    """One line per finished track: what each stage did, then which track it was."""
    width = len(str(total))
    stages = "  ".join(
        f"{name}: {result.outcome.value}" + (f" ({result.note})" if result.note else "")
        for name, result in report.results.items()
    )
    track = report.track
    return (
        f"[{index:>{width}}/{total}] {track.id}  {stages}  | {track.primary_artist} - {track.title}"
    )


def sync_totals(reports: Sequence[TrackReport], stage_names: Sequence[str]) -> list[str]:
    """Per stage, how many tracks ended in each outcome."""
    lines = [f"Processed {len(reports)} tracks."]
    for name in stage_names:
        counts = Counter(r.results[name].outcome for r in reports if name in r.results)
        parts = [f"{counts[outcome]} {outcome.value}" for outcome in Outcome if counts[outcome]]
        lines.append(f"  {name:<13} {', '.join(parts) or 'nothing to do'}")
    return lines


def _audio_cell(status: TrackStatus, miss: Miss | None) -> str:
    if status.audio:
        return "pinned" if status.audio.pinned else "ok"
    if miss:
        return "review" if miss.reason == "needs_review" else "miss"
    return "-"


def _lyrics_cell(status: TrackStatus) -> str:
    if status.lyrics is None:
        return "-"
    if status.lyrics.kind is LyricsKind.SYNCED and status.lyrics.timing_confidence == "low":
        return "synced?"
    return status.lyrics.kind.value


def coverage(statuses: Sequence[TrackStatus]) -> dict[str, int]:
    """Counts for one playlist: how many tracks have audio, synced lyrics, and so on."""
    kinds = Counter(status.lyrics_kind for status in statuses)
    synced = [s for s in statuses if s.lyrics_kind is LyricsKind.SYNCED]
    return {
        "tracks": len(statuses),
        "audio": sum(1 for s in statuses if s.audio),
        "pinned": sum(1 for s in statuses if s.audio and s.audio.pinned),
        "synced": kinds[LyricsKind.SYNCED],
        "synced_low_confidence": sum(1 for s in synced if s.lyrics.timing_confidence == "low"),
        "plain": kinds[LyricsKind.PLAIN],
        "instrumental": kinds[LyricsKind.INSTRUMENTAL],
        "no_lyrics": kinds[LyricsKind.MISSING],
        "cover": sum(1 for s in statuses if s.has_cover),
        "ready": sum(1 for s in statuses if s.audio and s.lyrics_kind is LyricsKind.SYNCED),
    }


def coverage_line(playlist: Playlist, statuses: Sequence[TrackStatus]) -> str:
    """One line per playlist, for ``status`` with no argument."""
    c = coverage(statuses)
    return (
        f"{playlist.id}  {c['tracks']:>4} tracks  {c['audio']:>4} audio  "
        f"{c['synced']:>4} synced  {c['cover']:>4} covers  {playlist.name}"
    )


def status_table(
    playlist: Playlist, statuses: Sequence[TrackStatus], misses: Sequence[Miss]
) -> list[str]:
    """Per track: audio state, lyrics kind, the audio's length against Spotify's; then totals.

    ``synced?`` marks synced lyrics whose timing confidence is low. Track IDs and titles
    only; no lyric text.
    """
    audio_miss = {miss.track_id: miss for miss in misses if miss.stage == "audio"}
    width = len(str(len(statuses)))
    lines = [
        f"Playlist {playlist.id}: {playlist.name}",
        f"{'#':>{width}}  {'track':<22}  {'audio':<6}  {'lyrics':<12}  {'delta':>6}  "
        f"{'offset':>6}  title",
    ]
    for index, status in enumerate(statuses, start=1):
        track = status.track
        delta = f"{status.audio.duration_s - track.duration_s:+.1f}s" if status.audio else ""
        offset = f"{status.state.offset_ms:+d}" if status.state.offset_ms else ""
        audio = _audio_cell(status, audio_miss.get(track.id))
        lines.append(
            f"{index:>{width}}  {track.id:<22}  {audio:<6}  "
            f"{_lyrics_cell(status):<12}  {delta:>6}  {offset:>6}  "
            f"{track.primary_artist} - {track.title}"
        )

    c = coverage(statuses)
    review = sum(1 for miss in audio_miss.values() if miss.reason == "needs_review")
    lines += [
        "",
        f"{c['tracks']} tracks: {c['ready']} ready (audio and synced lyrics)",
        f"  audio    {c['audio']} cached ({c['pinned']} pinned), {review} need review, "
        f"{c['tracks'] - c['audio'] - review} missing",
        f"  lyrics   {c['synced']} synced ({c['synced_low_confidence']} low confidence), "
        f"{c['plain']} plain, {c['instrumental']} instrumental, {c['no_lyrics']} none",
        f"  covers   {c['cover']}",
    ]
    return lines
