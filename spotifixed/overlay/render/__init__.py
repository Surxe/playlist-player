"""Renderers: how an :class:`OverlayView` reaches the screen. One today (GTK layer-shell).

Nothing here is imported at package import time: the GTK renderer needs a display and a
library preloaded before its first import, which only ``overlay.main`` arranges.
"""
