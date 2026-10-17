"""A stand-in for the desktop's global shortcuts portal, run as its own process.

    python -I tests/support/fake_portal.py

It owns ``org.freedesktop.portal.Desktop`` on the session bus in the environment and
answers ``CreateSession``, ``ListShortcuts`` and ``BindShortcuts`` as the real portal
does: a request object path at once, then a ``Response`` signal on it. Like the real
one it remembers what was bound, for as long as it runs, and lists it to every later
session. Each shortcut is described back with its preferred trigger as its trigger
description, except one whose description says "taken", which is left with no key. Once
a session has its shortcuts, by binding them or by listing ones already bound, it
presses every one once, in order, then a shortcut it never bound and one in another
session, which a client must ignore. Every call it receives is printed as one JSON line,
then ``ready`` once it owns its name.
"""

from __future__ import annotations

import json
import sys

from gi.repository import Gio, GLib

BUS_NAME = "org.freedesktop.portal.Desktop"
OBJECT_PATH = "/org/freedesktop/portal/desktop"
INTERFACE = "org.freedesktop.portal.GlobalShortcuts"
REQUEST = "org.freedesktop.portal.Request"

XML = f"""
<node>
  <interface name="{INTERFACE}">
    <method name="CreateSession">
      <arg type="a{{sv}}" name="options" direction="in"/>
      <arg type="o" name="handle" direction="out"/>
    </method>
    <method name="ListShortcuts">
      <arg type="o" name="session_handle" direction="in"/>
      <arg type="a{{sv}}" name="options" direction="in"/>
      <arg type="o" name="handle" direction="out"/>
    </method>
    <method name="BindShortcuts">
      <arg type="o" name="session_handle" direction="in"/>
      <arg type="a(sa{{sv}})" name="shortcuts" direction="in"/>
      <arg type="s" name="parent_window" direction="in"/>
      <arg type="a{{sv}}" name="options" direction="in"/>
      <arg type="o" name="handle" direction="out"/>
    </method>
    <signal name="Activated">
      <arg type="o" name="session_handle"/>
      <arg type="s" name="shortcut_id"/>
      <arg type="t" name="timestamp"/>
      <arg type="a{{sv}}" name="options"/>
    </signal>
  </interface>
</node>
"""


def say(record: object) -> None:
    print(json.dumps(record), flush=True)


def main() -> None:
    connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    loop = GLib.MainLoop()
    bound: list[tuple[str, dict]] = []  # what was bound, as the desktop keeps it

    def emit(destination, path, interface, name, signature, values) -> bool:
        connection.emit_signal(destination, path, interface, name, GLib.Variant(signature, values))
        return False

    def respond(sender: str, request: str, results: dict) -> None:
        GLib.idle_add(emit, sender, request, REQUEST, "Response", "(ua{sv})", (0, results))

    def call(_conn, sender, _path, _iface, method, params, invocation) -> None:
        args = params.unpack()
        say({"method": method, "args": args})
        unique = sender.lstrip(":").replace(".", "_")
        options = args[-1]
        request = f"{OBJECT_PATH}/request/{unique}/{options['handle_token']}"
        invocation.return_value(GLib.Variant("(o)", (request,)))
        if method == "CreateSession":
            session = f"{OBJECT_PATH}/session/{unique}/{options['session_handle_token']}"
            respond(sender, request, {"session_handle": GLib.Variant("s", session)})
            return
        session = args[0]
        if method == "BindShortcuts":
            bound[:] = []
            for sid, info in args[1]:
                keys = "" if "taken" in info["description"] else info["preferred_trigger"]
                bound.append((sid, {"trigger_description": GLib.Variant("s", keys)}))
        respond(sender, request, {"shortcuts": GLib.Variant("a(sa{sv})", list(bound))})
        if not bound:
            return  # nothing to press until the client binds
        presses = [(session, sid) for sid, _info in bound]
        presses += [(session, "never-bound"), (f"{OBJECT_PATH}/session/other/x", bound[0][0])]
        for when, (to_session, sid) in enumerate(presses, start=1):
            values = (to_session, sid, when, {})
            GLib.timeout_add(
                100 * when, emit, None, OBJECT_PATH, INTERFACE, "Activated", "(osta{sv})", values
            )

    info = Gio.DBusNodeInfo.new_for_xml(XML).interfaces[0]
    connection.register_object(OBJECT_PATH, info, call, None, None)

    def acquired(*_args) -> None:
        print("ready", flush=True)

    Gio.bus_own_name_on_connection(
        connection, BUS_NAME, Gio.BusNameOwnerFlags.NONE, acquired, lambda *_: loop.quit()
    )
    loop.run()


if __name__ == "__main__":
    sys.exit(main())
