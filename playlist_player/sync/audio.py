"""The audio stage: one correctly matched, tagged audio file per track.

Goal: ``audio.<ext>`` in the track's cache directory with ``audio.json`` beside it.

Without a pin: search, score the candidates (``matching.rank_audio``), and download the
best one if it clears the review threshold. If the first search turns up no album-audio
upload, a second, narrower search asks for one by name. When nothing clears the
threshold the track is reported as ``needs_review`` with its three best candidates, so
one can be chosen by hand with the ``pin`` command.

With a pin (``audio_pin`` in the track's state file): download exactly that URL. No
scoring and no duration check; the person who pinned it has decided.

The downloaded stream is measured, then copied (never re-encoded) into a container that
carries title and artist tags, and only then moved to its final name.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from ..cache import CacheStore, assets
from ..errors import SourceError
from ..models import AudioAsset, Track
from ..sources.audio import AudioSource, media
from .matching import ScoredCandidate, rank_audio
from .stages import StageResult

_SEARCH_LIMIT = 10
# How many acceptable candidates to try when a download fails (removed, region-locked).
_MAX_DOWNLOAD_ATTEMPTS = 3
_REVIEW_LISTING = 3


class AudioStage:
    name = "audio"

    def __init__(
        self,
        store: CacheStore,
        source: AudioSource,
        *,
        tolerance_s: float,
        reject_s: float,
    ) -> None:
        self._store = store
        self._source = source
        self._tolerance_s = tolerance_s
        self._reject_s = reject_s

    def is_done(self, track: Track) -> bool:
        asset = assets.read_audio(self._store, track.id)
        if asset is None:
            return False
        pin = assets.read_track_state(self._store, track.id).audio_pin
        # A pin added or changed after the download means the file on disk is the wrong one.
        return pin is None or (asset.pinned and asset.url == pin)

    def run(self, track: Track) -> StageResult:
        pin = assets.read_track_state(self._store, track.id).audio_pin
        if pin:
            asset = self._fetch(track, pin, score=None, pinned=True)
            return StageResult.done(f"pinned, {asset.duration_s:.0f}s")

        ranked = self._candidates(track)
        acceptable = [item for item in ranked if item.acceptable]
        if not acceptable:
            listing = [item.summary() for item in ranked[:_REVIEW_LISTING]]
            if not ranked:
                return StageResult.miss("not_found", "no candidate of the right length")
            return StageResult.miss("needs_review", detail=listing)

        errors: list[str] = []
        for item in acceptable[:_MAX_DOWNLOAD_ATTEMPTS]:
            try:
                asset = self._fetch(track, item.candidate.url, score=item.score, pinned=False)
            except SourceError as exc:
                errors.append(str(exc))
                continue
            kind = "album audio" if item.album_audio else "video"
            return StageResult.done(f"{kind}, {asset.duration_s - track.duration_s:+.1f}s")
        raise SourceError("; ".join(errors))

    def _candidates(self, track: Track) -> list[ScoredCandidate]:
        def rank(found):
            return rank_audio(track, found, tolerance_s=self._tolerance_s, reject_s=self._reject_s)

        found = self._source.search(f"{' '.join(track.artists)} {track.title}", _SEARCH_LIMIT)
        ranked = rank(found)
        if not any(item.album_audio and item.acceptable for item in ranked):
            # Album uploads live on auto-generated "<artist> - Topic" channels, which a
            # plain search often ranks below the music video.
            found += self._source.search(
                f"{track.primary_artist} - Topic {track.title}", _SEARCH_LIMIT
            )
            ranked = rank(found)
        return ranked

    def _fetch(self, track: Track, url: str, *, score: float | None, pinned: bool) -> AudioAsset:
        track_dir = self._store.track_dir(track.id)
        with self._store.work_dir(track_dir) as work:
            downloaded = self._source.download(url, work)
            raw = media.probe(downloaded.path)
            if not pinned and abs(raw.duration_s - track.duration_s) > self._reject_s:
                raise SourceError(
                    f"downloaded audio is {raw.duration_s:.0f}s, expected about "
                    f"{track.duration_s:.0f}s; discarded"
                )
            extension = media.tagged_extension(raw.codec, downloaded.path.suffix)
            tagged = work / f"tagged.{extension}"
            media.remux(
                downloaded.path,
                tagged,
                {"title": track.title, "artist": ", ".join(track.artists), "album": track.album},
            )
            info = media.probe(tagged)
            if info.codec != raw.codec:
                raise SourceError(f"rewrapping changed the codec ({raw.codec} to {info.codec})")

            name = f"audio.{extension}"
            self._remove_other_audio(track_dir, keep=name)
            self._store.adopt(tagged, self._store.track_file(track.id, name))

        found = downloaded.candidate
        asset = AudioAsset(
            file=name,
            duration_s=round(info.duration_s, 3),
            source=self._source.name,
            source_id=found.id or None,
            url=url if pinned else (found.url or url),
            title=found.title or None,
            channel=found.channel or None,
            codec=info.codec,
            score=None if score is None else round(score, 1),
            pinned=pinned,
            fetched_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        assets.write_audio(self._store, track.id, asset)
        return asset

    def _remove_other_audio(self, track_dir: Path, *, keep: str) -> None:
        # A re-download can arrive in a different container; never leave two audio files.
        for old in track_dir.glob("audio.*"):
            if old.name not in (keep, "audio.json"):
                self._store.remove(old)
