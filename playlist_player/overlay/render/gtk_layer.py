"""The overlay as a GTK4 window on the compositor's overlay layer (wlr-layer-shell).

The layer-shell "overlay" layer is the one compositors stack above fullscreen windows;
it is what on-screen volume displays use. The surface takes no keyboard focus, reserves
no screen space, and has an empty input region, so every click passes through it to
whatever is underneath.

IMPORT ORDER MATTERS. ``libgtk4-layer-shell`` must be loaded before ``libwayland-client``
or it cannot hook surface creation. ``overlay.main`` loads it with ``ctypes`` before
this module is imported, and this is the only module in the package that imports ``gi``.
Importing this file first, or importing ``gi`` anywhere else first, gives an ordinary
window that the compositor treats like any other.
"""

from __future__ import annotations

import signal
import sys
from pathlib import Path
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gtk4LayerShell", "1.0")
gi.require_version("Graphene", "1.0")
gi.require_foreign("cairo")

import cairo  # noqa: E402 - gi must be configured first
from gi.repository import Gdk, GLib, Graphene, Gtk, Pango  # noqa: E402
from gi.repository import Gtk4LayerShell as LayerShell  # noqa: E402

from ...errors import PlaylistPlayerError  # noqa: E402
from ..presenter import OverlayView  # noqa: E402
from .base import Placement, Style, ViewSource  # noqa: E402

NAMESPACE = "playlist-player-lyrics"
TICK_MS = 30
# Room around the text so the outline and its soft shadow are not clipped by the surface.
_PADDING_PX = 14
_FADE_STEP_MS = 16
_NEXT_LINE_SCALE = 0.7
_NEXT_LINE_OPACITY = 0.6

_EDGES = {
    "top": LayerShell.Edge.TOP,
    "bottom": LayerShell.Edge.BOTTOM,
    "left": LayerShell.Edge.LEFT,
    "right": LayerShell.Edge.RIGHT,
}


class GtkLayerRenderer:
    def __init__(self, placement: Placement, style: Style, *, monitor: str = "") -> None:
        # init_check can report success with no display opened, so ask for the display too.
        if not Gtk.init_check() or Gdk.Display.get_default() is None:
            raise PlaylistPlayerError(
                "no display: the overlay needs a Wayland session (is WAYLAND_DISPLAY set?)"
            )
        if not LayerShell.is_supported():
            raise PlaylistPlayerError(
                "this compositor does not offer the layer-shell protocol, or "
                "libgtk4-layer-shell was loaded too late; the overlay cannot be placed"
            )
        self._placement = placement
        self._style = style
        self._monitor_name = monitor
        self._css = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), self._css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        self._loop = GLib.MainLoop()
        self._view_source: ViewSource | None = None
        self._shown = OverlayView(visible=False)
        self._tick_id = 0
        self._fade_id = 0

        self._line = self._label("line")
        self._next = self._label("next")
        self._next.set_opacity(_NEXT_LINE_OPACITY)
        self._next.set_visible(False)
        self._box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self._box.append(self._line)
        self._box.append(self._next)

        self._window = Gtk.Window()
        self._window.add_css_class("playlist-player-overlay")
        self._window.set_decorated(False)
        self._window.set_child(self._box)
        self._window.connect("map", self._on_map)
        self._init_layer()
        self._apply_style()
        self._apply_placement()

    # -------------------------------- set-up -------------------------------- #

    def _apply_style(self) -> None:
        """Colours, font and width limit, from ``self._style``. Repeatable."""
        style = self._style
        o = style.outline_color
        css = (
            "window.playlist-player-overlay { background: transparent; }\n"
            f"window.playlist-player-overlay label {{ color: {style.color}; "
            # Eight hard offsets make a solid outline; the blurred one softens its edge.
            f"text-shadow: -2px -2px 0 {o}, 2px -2px 0 {o}, -2px 2px 0 {o}, 2px 2px 0 {o}, "
            f"0 -2px 0 {o}, 0 2px 0 {o}, -2px 0 0 {o}, 2px 0 0 {o}, 0 0 6px {o}; }}\n"
        )
        self._css.load_from_string(css)
        self._set_font(self._line, 1.0)
        self._set_font(self._next, _NEXT_LINE_SCALE)

    @staticmethod
    def _label(css_class: str) -> Gtk.Label:
        label = Gtk.Label()
        label.add_css_class(css_class)
        label.set_wrap(True)
        label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        # Without this GTK wraps to a pleasing aspect ratio, turning one short line
        # into three. A line should stay on one row until it reaches the width limit.
        label.set_natural_wrap_mode(Gtk.NaturalWrapMode.NONE)
        label.set_justify(Gtk.Justification.CENTER)
        for side in ("top", "bottom", "start", "end"):
            getattr(label, f"set_margin_{side}")(_PADDING_PX)
        return label

    def _set_font(self, label: Gtk.Label, scale: float) -> None:
        description = Pango.FontDescription.from_string(self._style.font)
        description.set_size(int(self._style.font_size * scale * Pango.SCALE))
        attributes = Pango.AttrList()
        attributes.insert(Pango.attr_font_desc_new(description))
        label.set_attributes(attributes)
        # A wrapping label's natural width is its whole text, and the only cap GTK offers
        # is in characters. GTK sizes those characters with the widget's own style font,
        # not the font set through attributes above, so convert the pixel limit with the
        # same measure GTK will use.
        metrics = label.get_pango_context().get_metrics(None, None)
        unit = max(metrics.get_approximate_char_width(), metrics.get_approximate_digit_width())
        label.set_max_width_chars(max(1, round(self._style.max_width * Pango.SCALE / max(1, unit))))

    def _init_layer(self) -> None:
        window = self._window
        LayerShell.init_for_window(window)
        LayerShell.set_namespace(window, NAMESPACE)
        LayerShell.set_layer(window, LayerShell.Layer.OVERLAY)
        LayerShell.set_keyboard_mode(window, LayerShell.KeyboardMode.NONE)
        # -1: reserve nothing AND ignore the space panels reserve, so margins are measured
        # from the real screen edge whether or not a panel is showing.
        LayerShell.set_exclusive_zone(window, -1)

    def _apply_placement(self) -> None:
        """Edges, margins, shift and monitor, from ``self._placement``. Repeatable."""
        window = self._window
        for name, edge in _EDGES.items():
            LayerShell.set_anchor(window, edge, name in self._placement.edges)
            LayerShell.set_margin(window, edge, self._placement.margins.get(name, 0))
        # On an axis the surface is centred on, pad one side by twice the shift: the
        # surface grows and its centre stays put, so the text moves by the shift.
        x, y = self._placement.shift_x, self._placement.shift_y
        self._box.set_margin_start(2 * x if x > 0 else 0)
        self._box.set_margin_end(-2 * x if x < 0 else 0)
        self._box.set_margin_top(2 * y if y > 0 else 0)
        self._box.set_margin_bottom(-2 * y if y < 0 else 0)

        found = self._monitor(self._monitor_name) if self._monitor_name else None
        if self._monitor_name and found is None:
            names = ", ".join(self.monitor_names()) or "none"
            print(
                f"playlist_player: no monitor named {self._monitor_name!r} (found: {names}); "
                "leaving the choice to the compositor",
                file=sys.stderr,
            )
        if found != LayerShell.get_monitor(window):
            LayerShell.set_monitor(window, found)

    @staticmethod
    def monitor_names() -> list[str]:
        monitors = Gdk.Display.get_default().get_monitors()
        return [monitors.get_item(i).get_connector() or "" for i in range(monitors.get_n_items())]

    @staticmethod
    def _monitor(connector: str) -> Gdk.Monitor | None:
        monitors = Gdk.Display.get_default().get_monitors()
        for index in range(monitors.get_n_items()):
            monitor = monitors.get_item(index)
            if monitor.get_connector() == connector:
                return monitor
        return None

    def _on_map(self, window: Gtk.Window) -> None:
        # An empty input region: pointer events fall through to whatever is underneath.
        # The region belongs to the surface, which is new on every map, so set it each time.
        surface = window.get_surface()
        if surface is not None:
            surface.set_input_region(cairo.Region())

    # ------------------------------- the loop -------------------------------- #

    def run(self, view_source: ViewSource) -> None:
        self._view_source = view_source
        for number in (signal.SIGINT, signal.SIGTERM):
            GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, number, self._quit)
        GLib.idle_add(self._evaluate)
        self._loop.run()
        self._window.set_visible(False)

    def wake(self) -> None:
        GLib.idle_add(self._evaluate)

    def stop(self) -> None:
        GLib.idle_add(self._quit)

    def reconfigure(self, placement: Placement, style: Style, monitor: str) -> None:
        GLib.idle_add(self._reconfigure, placement, style, monitor)

    def _reconfigure(self, placement: Placement, style: Style, monitor: str) -> bool:
        # On the main loop's thread, like everything else that touches a widget.
        if style != self._style:
            self._style = style
            self._apply_style()
        if placement != self._placement or monitor != self._monitor_name:
            self._placement = placement
            self._monitor_name = monitor
            self._apply_placement()
        # A mapped window keeps the size it has when only its content's natural size
        # changes (a smaller font, a removed shift), leaving the text adrift inside a
        # surface that is too large. Forgetting the remembered size makes it fit again.
        self._window.set_default_size(-1, -1)
        return GLib.SOURCE_REMOVE

    def _quit(self) -> bool:
        self._loop.quit()
        return GLib.SOURCE_REMOVE

    def _evaluate(self) -> bool:
        if self._view_source is not None:
            view = self._view_source()
            self._apply(view)
            # Keep the timer only while time alone can change the answer.
            if view.ticking and not self._tick_id:
                self._tick_id = GLib.timeout_add(TICK_MS, self._tick)
            elif not view.ticking and self._tick_id:
                GLib.source_remove(self._tick_id)
                self._tick_id = 0
        return GLib.SOURCE_REMOVE

    def _tick(self) -> bool:
        self._evaluate()
        if not self._tick_id:
            return GLib.SOURCE_REMOVE  # _evaluate stopped the timer
        return GLib.SOURCE_CONTINUE

    def _apply(self, view: OverlayView) -> None:
        if view == self._shown:
            return
        previous, self._shown = self._shown, view
        if not view.visible:
            # Unmapped, not merely transparent: the compositor stops compositing it.
            self._window.set_visible(False)
            return
        self._line.set_label(view.text)
        self._next.set_label(view.next_text)
        self._next.set_visible(bool(view.next_text))
        if not previous.visible or previous.text != view.text:
            self._fade_in()
        self._window.set_visible(True)

    def _fade_in(self) -> None:
        if self._fade_id:
            GLib.source_remove(self._fade_id)
            self._fade_id = 0
        duration = self._style.fade_ms
        if duration <= 0:
            self._line.set_opacity(1.0)
            return
        self._line.set_opacity(0.0)
        started = GLib.get_monotonic_time()

        def step() -> bool:
            progress = (GLib.get_monotonic_time() - started) / 1000 / duration
            self._line.set_opacity(min(1.0, progress))
            if progress >= 1.0:
                self._fade_id = 0
                return GLib.SOURCE_REMOVE
            return GLib.SOURCE_CONTINUE

        self._fade_id = GLib.timeout_add(_FADE_STEP_MS, step)

    # ------------------------------ diagnostics ------------------------------ #

    def describe(self) -> dict[str, Any]:
        window = self._window
        return {
            "is_layer_window": bool(LayerShell.is_layer_window(window)),
            "layer": LayerShell.get_layer(window).value_nick,
            "namespace": LayerShell.get_namespace(window),
            "keyboard_mode": LayerShell.get_keyboard_mode(window).value_nick,
            "exclusive_zone": LayerShell.get_exclusive_zone(window),
            "anchors": sorted(n for n, e in _EDGES.items() if LayerShell.get_anchor(window, e)),
            "margins": {n: LayerShell.get_margin(window, e) for n, e in _EDGES.items()},
            "monitor": getattr(LayerShell.get_monitor(window), "get_connector", lambda: None)(),
            "mapped": bool(window.get_mapped()),
            "text": self._line.get_label() if window.get_mapped() else "",
            "next_text": self._next.get_label() if self._next.get_visible() else "",
            "width": window.get_width(),
            "height": window.get_height(),
        }

    def snapshot(self, view: OverlayView, path: Path) -> None:
        """Show ``view``, wait for it to be drawn, save what was drawn, and return.

        The overlay is transparent, so the picture is composed over a backdrop that is
        dark on the left and light on the right: the outline has to keep the text
        readable on both.
        """
        self._style_fade_off()
        self._apply(view if view.visible else OverlayView(True, view.text or " "))
        saved: list[bool] = []

        def capture() -> bool:
            width, height = self._window.get_width(), self._window.get_height()
            snapshot = Gtk.Snapshot()
            for index, colour in enumerate(("#20242b", "#d9dde3")):
                rgba = Gdk.RGBA()
                rgba.parse(colour)
                half = Graphene.Rect().init(index * width / 2, 0, width / 2, height)
                snapshot.append_color(rgba, half)
            Gtk.WidgetPaintable.new(self._window.get_child()).snapshot(snapshot, width, height)
            node = snapshot.to_node()
            texture = self._window.get_native().get_renderer().render_texture(node, None)
            saved.append(bool(texture.save_to_png(str(path))))
            self._loop.quit()
            return GLib.SOURCE_REMOVE

        GLib.timeout_add(400, capture)  # a few frames, so the first layout has settled
        self._loop.run()
        self._window.set_visible(False)
        if not saved or not saved[0]:
            raise PlaylistPlayerError(f"could not write the snapshot to {path}")

    def _style_fade_off(self) -> None:
        self._style = Style(
            font=self._style.font,
            font_size=self._style.font_size,
            color=self._style.color,
            outline_color=self._style.outline_color,
            max_width=self._style.max_width,
            fade_ms=0,
        )
