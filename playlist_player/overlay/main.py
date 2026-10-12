"""Wiring for the ``overlay`` command: settings to model to client to renderer.

The first statement below loads ``libgtk4-layer-shell`` and has to stay first: the
library must be in the process before ``libwayland-client`` is, which means before
anything imports ``gi``. That is also why the renderer is imported inside functions.
"""

from __future__ import annotations

import ctypes

try:
    ctypes.CDLL("libgtk4-layer-shell.so.0")
    _LAYER_SHELL_LOADED = True
except OSError:  # not installed; reported properly when the renderer is built
    _LAYER_SHELL_LOADED = False

from pathlib import Path  # noqa: E402

from ..config import Settings  # noqa: E402
from ..errors import PlaylistPlayerError  # noqa: E402
from .client import DaemonClient  # noqa: E402
from .clock import now_epoch_ms  # noqa: E402
from .presenter import OverlayModel, OverlayView  # noqa: E402
from .render.base import Placement, Renderer, Style  # noqa: E402

DEMO_TEXT = "This is where the current lyric line will appear"
DEMO_NEXT = "and this is the line that follows it"


def build_renderer(settings: Settings) -> Renderer:
    """The renderer for these settings. Fails with one clear line if it cannot work here."""
    placement = Placement.from_settings(
        settings.overlay_anchor, settings.overlay_margin_x, settings.overlay_margin_y
    )
    style = Style(
        font=settings.overlay_font,
        font_size=settings.overlay_font_size,
        color=settings.overlay_color,
        outline_color=settings.overlay_outline_color,
        max_width=settings.overlay_max_width,
        fade_ms=settings.overlay_fade_ms,
    )
    if not _LAYER_SHELL_LOADED:
        raise PlaylistPlayerError(
            "libgtk4-layer-shell is not installed (Debian: libgtk4-layer-shell0 and "
            "gir1.2-gtk4layershell-1.0)"
        )
    try:
        from .render.gtk_layer import GtkLayerRenderer
    except (ImportError, ValueError) as exc:
        raise PlaylistPlayerError(f"the GTK bindings are not available: {exc}") from exc
    return GtkLayerRenderer(placement, style, monitor=settings.overlay_monitor)


def demo_view(settings: Settings) -> OverlayView:
    following = DEMO_NEXT if settings.overlay_lines == 2 else ""
    return OverlayView(visible=True, text=DEMO_TEXT, next_text=following)


def run(settings: Settings, *, demo: bool = False, snapshot: str | None = None) -> int:
    renderer = build_renderer(settings)
    if snapshot:
        renderer.snapshot(demo_view(settings), Path(snapshot))
        print(f"wrote {snapshot}")
        return 0
    if demo:
        view = demo_view(settings)
        renderer.run(lambda: view)
        return 0

    model = OverlayModel(lead_ms=settings.overlay_lead_ms, show_next=settings.overlay_lines == 2)
    client = DaemonClient(settings.base_url, model, on_change=renderer.wake)
    client.start()
    try:
        renderer.run(lambda: model.view(now_epoch_ms()))
    finally:
        client.stop()
    return 0
