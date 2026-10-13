"""The pure parts of the playback engine: queue, state bus, and the mpv state reducer."""

from __future__ import annotations

import threading
import time

import pytest

from playlist_player.models import LyricsKind, PlaybackState, PlayerStatus
from playlist_player.playback.queue import PlayQueue, QueueEntry
from playlist_player.playback.reducer import MpvStateReducer
from playlist_player.playback.state import StateBus


def entries(count: int) -> list[QueueEntry]:
    return [QueueEntry(f"T{i}", f"/cache/T{i}/audio.opus", LyricsKind.SYNCED) for i in range(count)]


# ---------------------------------- queue ---------------------------------- #


def test_queue_keeps_order_and_maps_both_ways():
    queue = PlayQueue(entries(4), playlist_id="PL1")
    assert len(queue) == 4 and queue.start_index == 0 and queue.playlist_id == "PL1"
    assert [e.track_id for e in queue.entries] == ["T0", "T1", "T2", "T3"]
    assert queue.entry_at(2).track_id == "T2" and queue.index_of("T3") == 3
    assert queue.index_of_path("/cache/T1/audio.opus") == 1
    assert queue.entry_at(None) is None and queue.entry_at(-1) is None and queue.entry_at(4) is None
    assert queue.index_of("nope") is None and queue.index_of_path(None) is None


def test_queue_starts_at_the_requested_track():
    assert PlayQueue(entries(4), start_track_id="T2").start_index == 2
    assert PlayQueue(entries(4), start_track_id="unknown").start_index == 0


def test_the_queue_keeps_the_order_it_is_given_and_starts_where_asked():
    every = entries(20)
    queue = PlayQueue([every[i] for i in (7, 2, 19, 0)], start_track_id="T19")
    assert [e.track_id for e in queue.entries] == ["T7", "T2", "T19", "T0"]
    assert queue.start_index == 2


def test_a_repeated_track_keeps_both_positions_and_shares_its_offset():
    queue = PlayQueue([*entries(2), entries(1)[0]])
    assert queue.index_of("T0") == 0 and len(queue) == 3
    assert queue.set_offset("T0", 75) and not queue.set_offset("nope", 1)
    assert [e.offset_ms for e in queue.entries] == [75, 0, 75]


# --------------------------------- state bus -------------------------------- #


def test_bus_versions_increase_by_one_and_ignore_the_callers_version():
    bus = StateBus()
    assert bus.current.version == 0
    first = bus.publish(PlaybackState(version=99, status=PlayerStatus.PLAYING))
    second = bus.publish(PlaybackState())
    assert (first.version, second.version) == (1, 2) and bus.current is second


def test_bus_wait_newer_returns_at_once_blocks_and_times_out():
    bus = StateBus()
    bus.publish(PlaybackState())
    assert bus.wait_newer(0, timeout=0).version == 1
    started = time.monotonic()
    assert bus.wait_newer(1, timeout=0.05) is None
    assert time.monotonic() - started >= 0.04

    threading.Timer(0.05, lambda: bus.publish(PlaybackState())).start()
    assert bus.wait_newer(1, timeout=2).version == 2


def test_bus_listeners_get_every_snapshot_and_can_unsubscribe():
    bus = StateBus()
    seen = []
    unsubscribe = bus.subscribe(seen.append)
    bus.subscribe(lambda _state: 1 / 0)  # a broken listener must not break the bus
    bus.publish(PlaybackState())
    unsubscribe()
    unsubscribe()  # twice is harmless
    bus.publish(PlaybackState())
    assert [s.version for s in seen] == [1]


# ---------------------------------- reducer --------------------------------- #


def prop(name, value):
    return {"event": "property-change", "name": name, "data": value}


def start_playing(reducer: MpvStateReducer, index: int = 0) -> None:
    """The messages mpv sends when a queue entry starts and reaches audio."""
    path = reducer.queue.entry_at(index).path
    for message in (
        prop("idle-active", False),
        prop("playlist-pos", index),
        {"event": "start-file"},
        prop("path", path),
        prop("duration", 200.0),
        {"event": "file-loaded"},
        {"event": "playback-restart"},
        prop("core-idle", False),
    ):
        reducer.apply(message)
    reducer.snapshot(0.0, 0)


@pytest.fixture
def reducer():
    r = MpvStateReducer()
    r.set_queue(PlayQueue(entries(3), playlist_id="PL1"))
    r.snapshot(None, 0)  # what the player publishes at start
    return r


def test_reducer_starts_idle():
    state = MpvStateReducer().snapshot(None, 123)
    assert (state.status, state.track_id, state.queue_length) == (PlayerStatus.IDLE, None, 0)
    assert state.paused and state.position_s == 0 and state.captured_at_epoch_ms == 123


def test_reducer_goes_loading_then_playing_and_fills_the_snapshot(reducer):
    assert reducer.apply(prop("idle-active", False))
    reducer.apply(prop("playlist-pos", 1))
    reducer.apply({"event": "start-file"})
    reducer.apply(prop("path", "/cache/T1/audio.opus"))
    assert reducer.status() is PlayerStatus.LOADING
    reducer.apply(prop("duration", 196.5))
    reducer.snapshot(0.0, 0)
    # Still buffering when playback-restart arrives: no forced publish, the next change has it.
    assert not reducer.apply({"event": "playback-restart"})
    assert reducer.apply(prop("core-idle", False))
    assert reducer.status() is PlayerStatus.PLAYING

    state = reducer.snapshot(12.345, 1_700_000_000_000)
    assert (state.status, state.paused, state.track_id) == (PlayerStatus.PLAYING, False, "T1")
    assert (state.queue_index, state.queue_length, state.playlist_id) == (1, 3, "PL1")
    assert (state.position_s, state.duration_s) == (12.345, 196.5)
    assert state.captured_at_epoch_ms == 1_700_000_000_000
    assert state.lyrics_kind is LyricsKind.SYNCED and state.error is None


def test_reducer_publishes_on_discrete_changes_only(reducer):
    start_playing(reducer)
    assert not reducer.apply(prop("time-pos", 3.2))  # a tick is not news
    assert not reducer.apply(prop("duration", 200.001))  # nor is duration jitter
    assert not reducer.apply({"event": "seek"})
    assert reducer.apply(prop("pause", True))
    assert reducer.status() is PlayerStatus.PAUSED
    reducer.snapshot(3.2, 0)
    assert not reducer.apply(prop("pause", True))  # the same value again
    assert reducer.apply(prop("volume", 60.0))
    reducer.snapshot(3.2, 0)
    assert reducer.apply(prop("speed", 1.25))
    assert reducer.snapshot(3.2, 0).speed == 1.25


def test_reducer_forces_a_snapshot_when_a_seek_completes_while_paused(reducer):
    start_playing(reducer)
    reducer.apply(prop("pause", True))
    reducer.apply(prop("core-idle", True))
    reducer.snapshot(10.0, 0)
    assert not reducer.apply({"event": "seek"})
    assert reducer.apply({"event": "playback-restart"})  # nothing discrete changed, yet publish


def test_reducer_reports_buffering_as_loading(reducer):
    start_playing(reducer)
    assert reducer.apply(prop("core-idle", True))
    assert reducer.status() is PlayerStatus.LOADING
    assert reducer.snapshot(5.0, 0).paused is False


def test_reducer_follows_track_changes(reducer):
    start_playing(reducer, 0)
    reducer.apply({"event": "end-file", "reason": "eof"})
    assert reducer.apply(prop("playlist-pos", 1))
    reducer.apply({"event": "start-file"})
    reducer.apply(prop("path", "/cache/T1/audio.opus"))
    state = reducer.snapshot(0.0, 0)
    assert (state.track_id, state.queue_index, state.status) == ("T1", 1, PlayerStatus.LOADING)


def test_reducer_trusts_the_path_when_it_disagrees_with_the_position(reducer):
    start_playing(reducer, 0)
    reducer.apply(prop("path", "/cache/T2/audio.opus"))  # position not updated yet
    assert reducer.snapshot(0.0, 0).track_id == "T2"
    reducer.apply(prop("path", "/somewhere/else.opus"))  # not in this queue at all
    state = reducer.snapshot(0.0, 0)
    assert state.track_id is None and state.queue_index is None and state.position_s == 0


def test_reducer_distinguishes_ended_from_stopped(reducer):
    start_playing(reducer, 2)
    reducer.apply({"event": "end-file", "reason": "eof"})
    reducer.apply(prop("playlist-pos", -1))
    assert reducer.apply(prop("idle-active", True))
    ended = reducer.snapshot(None, 0)
    assert (ended.status, ended.track_id, ended.paused) == (PlayerStatus.ENDED, None, True)
    assert ended.queue_length == 3 and ended.duration_s is None

    start_playing(reducer, 0)
    reducer.apply({"event": "end-file", "reason": "stop"})
    reducer.apply(prop("idle-active", True))
    assert reducer.snapshot(None, 0).status is PlayerStatus.IDLE


def test_reducer_does_not_republish_when_mpv_forgets_properties_after_the_end(reducer):
    start_playing(reducer, 2)
    reducer.apply({"event": "end-file", "reason": "eof"})
    reducer.apply(prop("idle-active", True))
    reducer.snapshot(None, 0)
    # Going idle, mpv reports these as unavailable; the ended state looks the same.
    assert not reducer.apply(prop("duration", None))
    assert not reducer.apply(prop("path", None))
    assert not reducer.apply(prop("playlist-pos", -1))
    assert not reducer.apply(prop("core-idle", True))


def test_reducer_reports_a_file_error_until_the_next_file_starts(reducer):
    start_playing(reducer, 0)
    assert reducer.apply({"event": "end-file", "reason": "error", "file_error": "unrecognized"})
    assert "could not play a file: unrecognized" in reducer.snapshot(0.0, 0).error
    reducer.apply({"event": "start-file"})
    assert reducer.snapshot(0.0, 0).error is None


def test_reducer_fatal_error_and_recovery(reducer):
    start_playing(reducer, 0)
    reducer.set_fatal("mpv exited unexpectedly")
    dead = reducer.snapshot(None, 0)
    assert (dead.status, dead.error, dead.track_id) == (
        PlayerStatus.ERROR, "mpv exited unexpectedly", None,
    )  # fmt: skip
    reducer.set_queue(PlayQueue(()))
    reducer.set_fatal(None)
    alive = reducer.snapshot(None, 0)
    assert (alive.status, alive.error, alive.queue_length) == (PlayerStatus.IDLE, None, 0)


def test_reducer_offset_change_is_worth_a_snapshot(reducer):
    start_playing(reducer, 0)
    reducer.queue.set_offset("T0", -120)
    assert reducer.signature() != reducer._published
    assert reducer.snapshot(1.0, 0).offset_ms == -120


def test_reducer_ignores_messages_it_does_not_know(reducer):
    start_playing(reducer)
    assert not reducer.apply({"event": "audio-reconfig"})
    assert not reducer.apply({})
    assert not reducer.apply(prop("some-new-property", 1))


def test_a_dead_player_still_reports_the_volume_it_had():
    reducer = MpvStateReducer()
    reducer.apply({"event": "property-change", "name": "volume", "data": 35.0})
    reducer.apply({"event": "property-change", "name": "pause", "data": True})
    reducer.set_fatal("mpv exited unexpectedly")
    dead = reducer.snapshot(None, 0)
    assert dead.status is PlayerStatus.ERROR and dead.volume == 35.0
    reducer.set_fatal(None)
    assert reducer.snapshot(None, 0).volume == 35.0

    fresh = MpvStateReducer()  # one that never heard a volume has only the default to give
    fresh.set_fatal("mpv exited unexpectedly")
    assert fresh.snapshot(None, 0).volume == 100.0
