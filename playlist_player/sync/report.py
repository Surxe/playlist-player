"""What ``sync`` and ``status`` print. Counts, IDs and titles only; never lyric text."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence

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
