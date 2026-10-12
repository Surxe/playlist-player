"""Negative cache: recording, the retry window, and persistence across instances."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from playlist_player.cache import MissLog


def test_record_then_skip_until_the_retry_window_passes(store):
    log = MissLog(store)
    assert not log.should_skip("T1", "lyrics", retry_days=14)
    miss = log.record("T1", "lyrics", "not_found")
    recorded = datetime.fromisoformat(miss.at)
    assert log.should_skip("T1", "lyrics", 14, now=recorded + timedelta(days=13))
    assert not log.should_skip("T1", "lyrics", 14, now=recorded + timedelta(days=15))
    assert not log.should_skip("T1", "audio", 14)  # a different stage is unaffected


def test_persists_and_reloads(store):
    MissLog(store).record("T1", "audio", "needs_review", detail=[{"id": "abc", "score": 0.4}])
    reloaded = MissLog(store).get("T1", "audio")
    assert reloaded.reason == "needs_review"
    assert reloaded.detail == [{"id": "abc", "score": 0.4}]
    assert datetime.fromisoformat(reloaded.at).tzinfo is UTC or reloaded.at.endswith("+00:00")


def test_clear_removes_only_that_entry(store):
    log = MissLog(store)
    log.record("T1", "audio", "not_found")
    log.record("T1", "lyrics", "not_found")
    log.clear("T1", "audio")
    log.clear("T1", "audio")  # clearing twice is harmless
    assert [(m.track_id, m.stage) for m in MissLog(store).all()] == [("T1", "lyrics")]


def test_rerecording_overwrites(store):
    log = MissLog(store)
    log.record("T1", "audio", "not_found")
    log.record("T1", "audio", "needs_review")
    assert len(log.all()) == 1 and log.get("T1", "audio").reason == "needs_review"
