"""The desktop's global shortcuts portal, over the system GObject bindings.

``org.freedesktop.portal.GlobalShortcuts`` is how an application that is not part of the
desktop gets a key that works while another window, even a fullscreen game, has focus.
The application opens a session, binds its shortcuts with a preferred trigger each, and
is sent an ``Activated`` signal on every press. The desktop owns the actual keys: it may
show the binding to the user, let them change it, or leave a shortcut unassigned when
its trigger is already taken. What it settled on comes back in the bind response.

Every portal call answers with a request object whose ``Response`` signal carries the
result. The signal is subscribed before the call is made, on the path the portal will
use for the handle token given, so a fast answer cannot be missed.

KDE Plasma 6 and GNOME 48 offer this portal; Sway does not. Where it is missing the
session cannot be created and :func:`run` says so.
"""

from __future__ import annotations

import contextlib
import itertools
import signal
from collections.abc import Callable, Sequence
from typing import Any

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from ..shortcut_settings import Shortcut  # noqa: E402

BUS_NAME = "org.freedesktop.portal.Desktop"
OBJECT_PATH = "/org/freedesktop/portal/desktop"
INTERFACE = "org.freedesktop.portal.GlobalShortcuts"
REQUEST_INTERFACE = "org.freedesktop.portal.Request"
SESSION_INTERFACE = "org.freedesktop.portal.Session"

# The same token on every run. A desktop that cannot tell which application is asking
# names the binding after the session, and a fixed name keeps a key the user changed.
SESSION_TOKEN = "spotifixed"

# D-Bus errors that mean there is no such portal, rather than that it refused.
_MISSING = (
    "org.freedesktop.DBus.Error.ServiceUnknown",
    "org.freedesktop.DBus.Error.UnknownInterface",
    "org.freedesktop.DBus.Error.UnknownMethod",
    "org.freedesktop.DBus.Error.UnknownObject",
)
_CALL_TIMEOUT_MS = 30_000  # the desktop may ask the user before it answers
_RESPONSES = {1: "the request was cancelled", 2: "the desktop refused the request"}


class PortalError(Exception):
    """The shortcuts could not be bound. The message ends a sentence."""


def _reason(error: GLib.Error) -> str:
    remote = Gio.DBusError.get_remote_error(error)
    if remote in _MISSING:
        return "this desktop does not offer global shortcuts"
    Gio.DBusError.strip_remote_error(error)
    return error.message


class GlobalShortcuts:
    """One portal session holding ``shortcuts``; ``on_press`` gets each pressed shortcut.

    :meth:`start` returns at once. ``on_bound`` is called with each shortcut's id and the
    trigger the desktop describes for it (empty when it assigned none), or ``on_failed``
    with the reason it could not be done. Callbacks run on the GLib main loop.
    """

    def __init__(
        self,
        connection: Gio.DBusConnection,
        shortcuts: Sequence[Shortcut],
        *,
        on_press: Callable[[Shortcut], None],
        on_bound: Callable[[dict[str, str]], None],
        on_failed: Callable[[str], None],
    ) -> None:
        self._connection = connection
        self._shortcuts = {shortcut.id: shortcut for shortcut in shortcuts}
        self._on_press = on_press
        self._on_bound = on_bound
        self._on_failed = on_failed
        self._tokens = itertools.count(1)
        self._session: str | None = None
        self._activated: int | None = None

    def start(self) -> None:
        options = {"session_handle_token": GLib.Variant("s", SESSION_TOKEN)}
        self._request("CreateSession", lambda token: (self._options(token, options),), self._bind)

    def close(self) -> None:
        """End the session, which releases the shortcuts. Safe to call more than once."""
        if self._activated is not None:
            self._connection.signal_unsubscribe(self._activated)
            self._activated = None
        session, self._session = self._session, None
        if session is not None:
            self._connection.call_sync(
                BUS_NAME, session, SESSION_INTERFACE, "Close", None, None,
                Gio.DBusCallFlags.NONE, 1000, None,
            )  # fmt: skip

    @staticmethod
    def _options(token: str, extra: dict[str, GLib.Variant] | None = None) -> dict[str, Any]:
        return {"handle_token": GLib.Variant("s", token), **(extra or {})}

    def _bind(self, results: dict[str, Any]) -> None:
        self._session = str(results.get("session_handle") or "")
        if not self._session:
            self._on_failed("the desktop opened no session")
            return
        self._activated = self._connection.signal_subscribe(
            BUS_NAME, INTERFACE, "Activated", OBJECT_PATH, None,
            Gio.DBusSignalFlags.NONE, self._activated_signal,
        )  # fmt: skip
        wanted = [
            (
                shortcut.id,
                {
                    "description": GLib.Variant("s", shortcut.description),
                    "preferred_trigger": GLib.Variant("s", shortcut.trigger),
                },
            )
            for shortcut in self._shortcuts.values()
        ]
        self._request(
            "BindShortcuts",
            lambda token: (self._session, wanted, "", self._options(token)),
            self._bound,
            signature="(oa(sa{sv})sa{sv})",
        )

    def _bound(self, results: dict[str, Any]) -> None:
        bound = {
            shortcut_id: str(info.get("trigger_description", ""))
            for shortcut_id, info in results.get("shortcuts", [])
        }
        self._on_bound(bound)

    def _activated_signal(self, _conn, _sender, _path, _iface, _signal, params) -> None:
        session, shortcut_id, _timestamp, _options = params.unpack()
        shortcut = self._shortcuts.get(shortcut_id)
        if session == self._session and shortcut is not None:
            self._on_press(shortcut)

    def _request(
        self,
        method: str,
        parameters: Callable[[str], tuple[Any, ...]],
        on_response: Callable[[dict[str, Any]], None],
        *,
        signature: str = "(a{sv})",
    ) -> None:
        """Call ``method`` and hand the results of its ``Response`` to ``on_response``."""
        token = f"spotifixed_{next(self._tokens)}"
        sender = self._connection.get_unique_name().lstrip(":").replace(".", "_")
        path = f"{OBJECT_PATH}/request/{sender}/{token}"
        subscription = 0

        def response(_conn, _sender, _path, _iface, _signal, params) -> None:
            self._connection.signal_unsubscribe(subscription)
            code, results = params.unpack()
            if code == 0:
                on_response(results)
            else:
                self._on_failed(_RESPONSES.get(code, f"the desktop answered {code}"))

        def replied(connection: Gio.DBusConnection, result: Gio.AsyncResult) -> None:
            try:
                connection.call_finish(result)
            except GLib.Error as exc:
                self._connection.signal_unsubscribe(subscription)
                self._on_failed(_reason(exc))

        subscription = self._connection.signal_subscribe(
            BUS_NAME, REQUEST_INTERFACE, "Response", path, None,
            Gio.DBusSignalFlags.NONE, response,
        )  # fmt: skip
        self._connection.call(
            BUS_NAME, OBJECT_PATH, INTERFACE, method,
            GLib.Variant(signature, parameters(token)), GLib.VariantType("(o)"),
            Gio.DBusCallFlags.NONE, _CALL_TIMEOUT_MS, None, replied,
        )  # fmt: skip


def run(
    shortcuts: Sequence[Shortcut],
    *,
    on_press: Callable[[Shortcut], None],
    on_bound: Callable[[dict[str, str]], None],
) -> None:
    """Hold ``shortcuts`` until SIGINT or SIGTERM. Raises :class:`PortalError` if it cannot."""
    try:
        connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    except GLib.Error as exc:
        raise PortalError(f"there is no desktop session to register with ({exc.message})") from exc
    loop = GLib.MainLoop()
    failure: list[str] = []

    def failed(reason: str) -> None:
        failure.append(reason)
        loop.quit()

    portal = GlobalShortcuts(
        connection, shortcuts, on_press=on_press, on_bound=on_bound, on_failed=failed
    )
    for number in (signal.SIGINT, signal.SIGTERM):
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, number, lambda: loop.quit() or False)
    portal.start()
    try:
        loop.run()
    finally:
        with contextlib.suppress(GLib.Error):  # the desktop is going away too
            portal.close()
    if failure:
        raise PortalError(failure[0])
