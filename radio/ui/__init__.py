"""Fullscreen PySDL2 + Pillow UI (640x480). Nothing here is imported eagerly.

``radio.ui.app`` and ``radio.ui.render`` both import SDL2/Pillow lazily
inside functions, never at module scope, so desktop unit tests never
need those packages installed. This package's ``__init__`` intentionally
imports nothing from either submodule for the same reason.
"""
