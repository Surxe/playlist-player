"""The daemon's copy of the overlay's settings: configured values plus page overrides.

The overlay is a separate program. It learns its settings from the daemon's event
stream, so a change made on the remote-control page reaches a running overlay at once.
This object is what that stream reads: a versioned document that wakes its listeners
whenever it changes, in the same way the state bus does for playback.

Only the overrides are stored (``overlay.json`` in the state directory). The configured
values are whatever this daemon was started with. Two things in the document are never
stored: ``preview_until_ms``, which asks the overlay to show a sample line until that
instant so it can be positioned with nothing playing, and ``clients``, the number of
overlays listening right now.
"""

from __future__ import annotations

import contextlib
import sys
import threading
import time
from collections.abc import Callable, Mapping
from typing import Any

from ..cache import CacheStore
from ..errors import CacheError
from ..overlay_settings import (
    RULES,
    OverlaySettingError,
    OverlaySettings,
    clean,
    prune,
    salvage,
)

MAX_PREVIEW_S = 60.0

Listener = Callable[[], None]


class OverlayConfig:
    def __init__(
        self,
        defaults: OverlaySettings,
        store: CacheStore | None = None,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._defaults = defaults
        self._store = store
        self._clock = clock
        self._lock = threading.Lock()
        self._listeners: list[Listener] = []
        self._version = 1
        self._preview_until_ms = 0
        self._clients = 0
        self._overrides = self._load()

    # ------------------------------- storage ------------------------------- #

    def _load(self) -> dict[str, Any]:
        if self._store is None:
            return {}
        try:
            stored = self._store.read_json(self._store.overlay_json, {})
        except (CacheError, OSError) as exc:
            print(f"playlist_player: {exc}; starting without overlay overrides", file=sys.stderr)
            return {}
        kept, notes = salvage(stored.get("overrides", {}) if isinstance(stored, dict) else None)
        for note in notes:
            print(f"playlist_player: {note}", file=sys.stderr)
        return prune(self._defaults, kept)

    def _save(self, overrides: Mapping[str, Any]) -> None:
        if self._store is not None:
            self._store.write_json(self._store.overlay_json, {"overrides": dict(overrides)})

    # -------------------------------- reading ------------------------------- #

    @property
    def version(self) -> int:
        with self._lock:
            return self._version

    def snapshot(self) -> dict[str, Any]:
        """The document as the event stream sends it."""
        with self._lock:
            return {
                "version": self._version,
                "defaults": self._defaults.to_dict(),
                "overrides": dict(self._overrides),
                "settings": self._defaults.merged(self._overrides).to_dict(),
                "preview_until_ms": self._preview_until_ms,
                "clients": self._clients,
            }

    def describe(self) -> dict[str, Any]:
        """The snapshot plus what each setting accepts, for a client building a form."""
        return {**self.snapshot(), "rules": {key: rule.to_json() for key, rule in RULES.items()}}

    def subscribe(self, listener: Listener) -> Callable[[], None]:
        """Call ``listener`` after every change. Returns a function that unsubscribes."""
        with self._lock:
            self._listeners.append(listener)

        def unsubscribe() -> None:
            with self._lock, contextlib.suppress(ValueError):
                self._listeners.remove(listener)

        return unsubscribe

    # -------------------------------- changing ------------------------------ #

    def _changed(self) -> list[Listener]:
        """Bump the version. Call with the lock held; run the result after releasing it."""
        self._version += 1
        return list(self._listeners)

    @staticmethod
    def _notify(listeners: list[Listener]) -> None:
        for listener in listeners:
            with contextlib.suppress(Exception):
                listener()

    def update(self, changes: Mapping[str, Any]) -> dict[str, Any]:
        """Set overrides; a ``None`` value drops one. All of ``changes`` applies or none.

        Raises :class:`~playlist_player.overlay_settings.OverlaySettingError` for a value
        a setting cannot take, and :class:`OSError` or ``CacheError`` if the result
        cannot be saved (in which case nothing changes).
        """
        with self._lock:
            overrides = dict(self._overrides)
            for key, value in changes.items():
                if key not in RULES:
                    raise OverlaySettingError(f"{key!r} is not an overlay setting")
                if value is None:
                    overrides.pop(key, None)
                else:
                    overrides[key] = clean(key, value)
            overrides = prune(self._defaults, overrides)
            if overrides == self._overrides:
                listeners = []
            else:
                self._save(overrides)
                self._overrides = overrides
                listeners = self._changed()
        self._notify(listeners)
        return self.snapshot()

    def reset(self) -> dict[str, Any]:
        """Drop every override."""
        with self._lock:
            if self._overrides:
                self._save({})
                self._overrides = {}
                listeners = self._changed()
            else:
                listeners = []
        self._notify(listeners)
        return self.snapshot()

    def preview(self, seconds: float) -> dict[str, Any]:
        """Ask overlays to show a sample line for ``seconds`` from now; 0 ends it.

        It expires by itself, so a page that is closed mid-preview does not leave the
        sample on screen: the page keeps a preview alive by asking again.
        """
        seconds = max(0.0, min(MAX_PREVIEW_S, seconds))
        with self._lock:
            self._preview_until_ms = int((self._clock() + seconds) * 1000) if seconds else 0
            listeners = self._changed()
        self._notify(listeners)
        return self.snapshot()

    def attach(self) -> None:
        """An overlay started listening."""
        self._count(1)

    def detach(self) -> None:
        self._count(-1)

    def _count(self, step: int) -> None:
        with self._lock:
            self._clients = max(0, self._clients + step)
            listeners = self._changed()
        self._notify(listeners)
