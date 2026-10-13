"""The daemon's session: the shuffle order, what is saved, and what a restart resumes."""

from __future__ import annotations

import json
import random
import threading

import pytest

from playlist_player.errors import NotInCacheError
from playlist_player.models import PlaybackState, PlayerStatus
from playlist_player.server.app import Daemon
from playlist_player.server.library import Library
from playlist_player.server.session import PlaybackSession
from tests.support.library import PLAYLIST_ID, build_library
from tests.support.playback import FakePlayer

PLAYABLE = ["TRK0000", "TRK0001", "TRK0002", "TRK0003"]  # TRK0004 has no audio


@pytest.fixture
def library(store):
    build_library(store)
    return Library(store)


def session_on(
    store, library, player=None, *, seed=1, clock=None
) -> tuple[PlaybackSession, FakePlayer]:
    player = player or FakePlayer()
    kwargs = {"rng": random.Random(seed)}
    if clock is not None:
        kwargs["clock"] = clock
    return PlaybackSession(store, library, player, **kwargs), player


def saved(store) -> dict:
    return json.loads(store.session_json.read_text())


# ------------------------------- shuffle -------------------------------- #


def test_a_shuffle_draws_one_order_of_the_playable_tracks_and_keeps_it(store, library):
    session, _ = session_on(store, library)
    first = session.queue_for(PLAYLIST_ID, start_track_id=None, shuffle=True)
    order = [e.track_id for e in first.entries]
    assert sorted(order) == PLAYABLE and order != PLAYABLE
    assert saved(store)["order"] == order and saved(store)["order_playlist_id"] == PLAYLIST_ID

    again = session.queue_for(PLAYLIST_ID, start_track_id=None, shuffle=True)
    assert [e.track_id for e in again.entries] == order


def test_the_order_survives_a_restart(store, library):
    session, _ = session_on(store, library)
    order = [e.track_id for e in session.queue_for(PLAYLIST_ID, start_track_id=None,
                                                   shuffle=True).entries]  # fmt: skip
    later, _ = session_on(store, library, seed=99)  # a different random source, same file
    assert [e.track_id for e in later.queue_for(PLAYLIST_ID, start_track_id=None,
                                                shuffle=True).entries] == order  # fmt: skip


def test_starting_a_track_in_a_shuffle_plays_on_from_its_place(store, library):
    session, _ = session_on(store, library)
    queue = session.queue_for(PLAYLIST_ID, start_track_id="TRK0002", shuffle=True)
    order = [e.track_id for e in queue.entries]
    assert queue.start_index == order.index("TRK0002")


def test_turning_shuffle_off_plays_in_playlist_order_and_forgets_the_order(store, library):
    session, _ = session_on(store, library)
    session.queue_for(PLAYLIST_ID, start_track_id=None, shuffle=True)
    session.set_shuffle(False)
    queue = session.queue_for(PLAYLIST_ID, start_track_id=None, shuffle=False)
    assert [e.track_id for e in queue.entries] == PLAYABLE
    assert "order" not in saved(store) and saved(store)["shuffle"] is False


def test_turning_shuffle_on_draws_a_fresh_order_for_the_playlist(store, library):
    session, _ = session_on(store, library, seed=3)
    session.queue_for(PLAYLIST_ID, start_track_id=None, shuffle=True)
    before = saved(store)["order"]
    session.set_shuffle(False)
    session.set_shuffle(True)
    assert saved(store)["order"] != before  # the next draw, from the same seeded source
    assert sorted(saved(store)["order"]) == PLAYABLE


def test_turning_shuffle_on_with_nothing_loaded_waits_for_the_first_load(store, library):
    session, _ = session_on(store, library)
    assert session.set_shuffle(True) == {
        "shuffle": True, "playlist_id": None, "track_id": None, "position_s": 0.0, "volume": None,
    }  # fmt: skip
    assert "order" not in saved(store)
    session.queue_for(PLAYLIST_ID, start_track_id=None, shuffle=True)
    assert sorted(saved(store)["order"]) == PLAYABLE


def test_a_stale_order_is_replaced_not_played(store, library):
    store.write_json(store.session_json, {
        "shuffle": True, "playlist_id": PLAYLIST_ID,
        "order": ["TRK0000", "TRK0009"], "order_playlist_id": PLAYLIST_ID,
    })  # fmt: skip
    session, _ = session_on(store, library)
    queue = session.queue_for(PLAYLIST_ID, start_track_id=None, shuffle=True)
    assert sorted(e.track_id for e in queue.entries) == PLAYABLE


def test_an_order_for_another_playlist_is_not_reused(store, library):
    store.write_json(store.session_json, {
        "shuffle": True, "order": PLAYABLE[::-1], "order_playlist_id": "SOMEONE_ELSE",
    })  # fmt: skip
    session, _ = session_on(store, library)
    queue = session.queue_for(PLAYLIST_ID, start_track_id=None, shuffle=True)
    assert [e.track_id for e in queue.entries] != PLAYABLE[::-1]
    assert saved(store)["order_playlist_id"] == PLAYLIST_ID


def test_a_playlist_with_no_audio_is_refused_and_saves_nothing(store, library):
    session, _ = session_on(store, library)
    with pytest.raises(NotInCacheError):
        session.queue_for("NotCached", start_track_id=None, shuffle=True)
    assert not store.session_json.exists()


# ------------------------------- recording ------------------------------ #


def test_nothing_is_saved_before_the_daemon_has_tried_to_resume(store, library):
    _, player = session_on(store, library)
    player.load_queue(library.queue_for(PLAYLIST_ID))  # a snapshot of playing, say
    player.set_volume(40)
    assert not store.session_json.exists()


def test_what_plays_is_saved_as_it_changes(store, library):
    now = [1000.0]
    session, player = session_on(store, library, clock=lambda: now[0])
    session.resume()  # nothing saved; enables saving
    player.load_queue(session.queue_for(PLAYLIST_ID, start_track_id="TRK0001", shuffle=False))
    player.position = 12.5
    player.set_volume(35)
    document = saved(store)
    assert (document["playlist_id"], document["track_id"]) == (PLAYLIST_ID, "TRK0001")
    assert document["volume"] == 35.0


def test_the_position_is_where_playback_is_now_not_where_the_snapshot_was(store, library):
    now = [1000.0]
    session, player = session_on(store, library, clock=lambda: now[0])
    session.resume()
    player.bus.publish(PlaybackState(
        status=PlayerStatus.PLAYING, paused=False, playlist_id=PLAYLIST_ID, track_id="TRK0000",
        position_s=10.0, captured_at_epoch_ms=1_000_000, volume=100.0, duration_s=60.0,
    ))  # fmt: skip
    now[0] = 1003.0  # three seconds later, nothing new published
    session.flush()
    assert saved(store)["position_s"] == pytest.approx(13.0)


def test_a_stopped_or_empty_player_does_not_forget_the_song(store, library):
    session, player = session_on(store, library)
    session.resume()
    player.load_queue(session.queue_for(PLAYLIST_ID, start_track_id="TRK0002", shuffle=False))
    player.bus.publish(PlaybackState(status=PlayerStatus.IDLE, paused=True, volume=60.0))
    document = saved(store)
    assert document["track_id"] == "TRK0002" and document["volume"] == 60.0


def test_a_failed_save_is_reported_and_playback_goes_on(store, library, monkeypatch, capsys):
    session, player = session_on(store, library)
    session.resume()

    def refuse(path, data):
        raise OSError("disk full")

    monkeypatch.setattr(store, "write_json", refuse)
    player.set_volume(20)  # must not raise
    assert "could not save the session: disk full" in capsys.readouterr().err


# -------------------------------- resuming ------------------------------ #


def test_a_restart_puts_back_the_volume_and_loads_the_song_paused_at_its_position(store, library):
    store.write_json(store.session_json, {
        "shuffle": False, "playlist_id": PLAYLIST_ID, "track_id": "TRK0001",
        "position_s": 2.5, "volume": 45.0,
    })  # fmt: skip
    session, player = session_on(store, library)
    session.resume()
    assert ("set_volume", 45.0) in player.calls
    assert player.index == 1 and player.paused is True
    assert player.loaded_at == 2.5
    assert player.bus.current.status is PlayerStatus.PAUSED


def test_a_shuffled_playlist_comes_back_in_its_saved_order(store, library):
    order = ["TRK0003", "TRK0000", "TRK0002", "TRK0001"]
    store.write_json(store.session_json, {
        "shuffle": True, "playlist_id": PLAYLIST_ID, "track_id": "TRK0002", "position_s": 0,
        "order": order, "order_playlist_id": PLAYLIST_ID,
    })  # fmt: skip
    session, player = session_on(store, library, seed=77)
    session.resume()
    assert [e.track_id for e in player.queue.entries] == order
    assert player.index == 2


def test_a_song_saved_at_its_very_end_starts_again(store, library):
    store.write_json(store.session_json, {
        "playlist_id": PLAYLIST_ID, "track_id": "TRK0000", "position_s": 4.9,
    })  # fmt: skip
    session, player = session_on(store, library)
    session.resume()
    assert player.index == 0 and player.loaded_at == 0.0


def test_a_saved_song_that_is_no_longer_cached_is_skipped_with_a_note(store, library, capsys):
    store.write_json(store.session_json, {
        "playlist_id": PLAYLIST_ID, "track_id": "TRK0004", "position_s": 3, "volume": 30.0,
    })  # fmt: skip
    session, player = session_on(store, library)
    session.resume()
    assert ("set_volume", 30.0) in player.calls  # the volume is still put back
    assert not any(call[0] == "load_queue" for call in player.calls)
    assert "not resuming the last song" in capsys.readouterr().err


def test_a_damaged_session_file_starts_with_nothing_remembered(store, library, capsys):
    store.session_json.parent.mkdir(parents=True, exist_ok=True)
    store.session_json.write_text("{ not json")
    session, player = session_on(store, library)
    session.resume()
    assert not any(call[0] == "load_queue" for call in player.calls)
    assert "starting without the saved session" in capsys.readouterr().err
    assert session.document()["playlist_id"] is None


def test_the_document_the_page_reads_is_what_is_saved(store, library):
    session, _ = session_on(store, library)
    session.queue_for(PLAYLIST_ID, start_track_id=None, shuffle=True)
    document = session.document()
    assert document["shuffle"] is True and document["playlist_id"] == PLAYLIST_ID
    assert "order" not in document  # the order is kept, not sent


def test_a_daemon_that_stops_and_starts_again_plays_where_it_was(store, monkeypatch):
    monkeypatch.setenv("PLAYLIST_PLAYER_CACHE_DIR", str(store.cache_dir))
    monkeypatch.setenv("PLAYLIST_PLAYER_STATE_DIR", str(store.state_dir))
    build_library(store)
    from playlist_player.config import load_settings

    settings = load_settings()
    first_player = FakePlayer()
    first = Daemon(settings, store, first_player, port=0, keepalive_s=0.5)
    threading.Thread(target=first.serve_forever, daemon=True).start()  # shutdown() waits for it
    first.session.resume()
    first.routes.dispatch("POST", "/api/v1/session/shuffle", b'{"enabled": true}')
    body = json.dumps({"playlist_id": PLAYLIST_ID, "shuffle": True}).encode()
    first.routes.dispatch("POST", "/api/v1/player/load", body)
    first_player.set_volume(25)
    first_player.pause()  # paused, so the saved position is exact rather than extrapolated
    first_player.index, first_player.position = 2, 1.75
    first_player._publish()
    shuffled = [e.track_id for e in first_player.queue.entries]
    first.session.flush()
    first.shutdown()

    second_player = FakePlayer()
    second = Daemon(settings, store, second_player, port=0, keepalive_s=0.5)
    threading.Thread(target=second.serve_forever, daemon=True).start()
    second.session.resume()
    second.shutdown()
    assert [e.track_id for e in second_player.queue.entries] == shuffled
    assert second_player.index == 2 and second_player.loaded_at == pytest.approx(1.75)
    assert ("set_volume", 25.0) in second_player.calls
    assert second_player.paused is True


def test_saving_from_two_threads_leaves_a_whole_file(store, library):
    session, player = session_on(store, library)
    session.resume()

    def hammer(n):
        for i in range(40):
            player.set_volume(float((n * 40 + i) % 100))

    threads = [threading.Thread(target=hammer, args=(n,)) for n in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert 0 <= saved(store)["volume"] <= 100
