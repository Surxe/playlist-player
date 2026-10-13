"""Cache store: path safety, atomic writes, and ordinary permissions."""

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


@pytest.mark.parametrize(
    ("umask", "file_mode", "dir_mode"),
    [(0o002, 0o664, 0o775), (0o022, 0o644, 0o755), (0o077, 0o600, 0o700)],
)
def test_permissions_follow_the_umask_like_any_ordinary_file(store, umask, file_mode, dir_mode):
    """Nothing is forced: a private umask gives private files, a shared one shared files."""
    old = os.umask(umask)
    try:
        path = store.track_json("T1")
        store.write_json(path, {"id": "T1"})
        store.write_json(path, {"id": "T1", "again": True})  # a replacement, same rule
    finally:
        os.umask(old)
    assert stat.S_IMODE(path.stat().st_mode) == file_mode
    for directory in (path.parent, path.parent.parent, store.cache_dir):
        assert stat.S_IMODE(directory.stat().st_mode) & 0o777 == dir_mode, directory


def test_a_setgid_cache_directory_passes_its_group_down(store):
    """How a cache is shared between accounts: set up outside this code, and respected."""
    store.cache_dir.mkdir(parents=True)
    store.cache_dir.chmod(0o2775)
    path = store.track_json("T1")
    store.write_json(path, {"id": "T1"})
    assert path.parent.stat().st_mode & stat.S_ISGID
    assert path.stat().st_gid == store.cache_dir.stat().st_gid


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
