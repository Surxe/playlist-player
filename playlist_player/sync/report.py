"""What ``sync`` and ``status`` print. Counts, IDs and titles only; never lyric text."""

from __future__ import annotations

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
