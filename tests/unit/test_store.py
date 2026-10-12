"""Cache store: path safety, atomic writes, and modes that survive a strict umask."""

from __future__ import annotations

import os
import stat

import pytest

from playlist_player.cache import CacheStore, atomic_write_bytes, validate_id
from playlist_player.errors import CacheError, InvalidIdError


@pytest.mark.parametrize(
    "bad", ["", "..", "a/b", "a.b", "a b", "x" * 65, "../etc", "id\n", None, 7]
)
def test_validate_id_rejects_anything_path_like(bad):
    with pytest.raises(InvalidIdError):
        validate_id(bad)


def test_validate_id_accepts_spotify_shaped_ids():
    assert validate_id("AbCdEfGhIjKlMnOpQrStUv") == "AbCdEfGhIjKlMnOpQrStUv"


def test_paths_are_built_from_the_two_roots(store):
    assert (
        store.tracklist_raw("PL1") == store.cache_dir / "playlists" / "PL1" / "tracklist.raw.json"
    )
    assert store.audio_json("T1") == store.cache_dir / "tracks" / "T1" / "audio.json"
    assert store.track_state("T1") == store.state_dir / "tracks" / "T1.json"
    with pytest.raises(InvalidIdError):
        store.track_dir("../escape")


def test_modes_are_forced_even_under_a_strict_umask(store):
    """ethan and dev both write here; a 077 umask must not lock the other account out."""
    old = os.umask(0o077)
    try:
        path = store.track_json("T1")
        store.write_json(path, {"id": "T1"})
    finally:
        os.umask(old)
    assert stat.S_IMODE(path.stat().st_mode) == 0o664
    for directory in (path.parent, path.parent.parent, store.cache_dir):
        assert stat.S_IMODE(directory.stat().st_mode) == 0o2775, directory


def test_write_replaces_atomically_and_leaves_no_temp_files(store):
    path = store.track_json("T1")
    store.write_json(path, {"v": 1})
    store.write_json(path, {"v": 2})
    assert store.read_json(path) == {"v": 2}
    assert [p.name for p in path.parent.iterdir()] == ["track.json"]


def test_failed_write_keeps_the_previous_file(store, monkeypatch):
    path = store.track_json("T1")
    store.write_json(path, {"v": 1})

    def boom(*_args):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError, match="disk full"):
        atomic_write_bytes(path, b"partial")
    assert store.read_json(path) == {"v": 1}
    assert [p.name for p in path.parent.iterdir()] == ["track.json"]


def test_refuses_to_write_outside_its_roots(store, tmp_path):
    with pytest.raises(CacheError, match="outside"):
        store.write_text(tmp_path / "elsewhere.txt", "x")


def test_read_json_missing_and_corrupt(store):
    missing = store.track_json("T9")
    assert store.read_json(missing, default=None) is None
    with pytest.raises(CacheError, match="missing"):
        store.read_json(missing)
    store.write_text(missing, "{not json")
    with pytest.raises(CacheError, match="corrupt"):
        store.read_json(missing)


def test_has_treats_an_empty_file_as_absent(store):
    path = store.lyrics_lrc("T1")
    assert not store.has(path)
    store.write_text(path, "")
    assert not store.has(path)
    store.write_text(path, "x")
    assert store.has(path)


def test_playlist_ids_lists_only_normalized_playlists(store):
    store.write_json(store.playlist_json("B2"), {})
    store.write_json(store.playlist_json("A1"), {})
    store.write_json(store.tracklist_raw("C3"), [])  # fetched but not normalized yet
    assert store.playlist_ids() == ["A1", "B2"]
    assert CacheStore(store.cache_dir / "nope", store.state_dir).playlist_ids() == []
