"""SDL2 fullscreen frame loop entrypoint (640x480, PySDL2 + Pillow).

SDL2 and Pillow are only ever imported inside :func:`run` — never at
module scope — so importing ``radio.ui.app`` itself stays cheap and
doesn't require a display or these packages to be installed. Only the
real device (or a machine with PySDL2/Pillow set up) can actually call
:func:`run`; desktop unit tests exercise :mod:`radio.app.state` and
:mod:`radio.input` directly instead.

Input reading, UI redraw and playback-subprocess polling all happen in
this single loop each frame (PLAN.md Fase 2): none of the three ever
blocks on the others, since ``InputReader.poll()`` and
``PlayerController.poll()`` are both non-blocking and rendering is pure
CPU work against already-known state.

Window creation follows the approach proven on-device by a known-good
reference app on this same RG35XX H unit: ``SDL_CreateWindow`` is
called with ``width=0, height=0`` and
``SDL_WINDOW_FULLSCREEN_DESKTOP | SDL_WINDOW_SHOWN`` (letting SDL pick
the desktop's actual size/mode) instead of an explicit-size fullscreen
mode switch. On this device's GLES/EGL setup, the explicit-size
``SDL_WINDOW_FULLSCREEN`` variant fails with ``SDL_CreateWindow
failed: Could not initialize EGL``, while the desktop-fullscreen
variant does not. The 640x480 logical Pillow render surface is still
preserved: ``SDL_RenderSetLogicalSize`` scales it up to whatever real
window size SDL picks, instead of requesting a 640x480 window
directly.
"""

from __future__ import annotations

import logging
import time

from radio.app.state import RadioApp
from radio.input.reader import InputReader

WIDTH = 640
HEIGHT = 480
FRAME_INTERVAL = 1.0 / 30.0

logger = logging.getLogger(__name__)


def run(app: RadioApp, input_reader: InputReader) -> None:
    """Run the fullscreen SDL2 UI loop until ``app.should_exit`` is set."""
    try:
        logger.info("importing sdl2")
        import sdl2

        logger.info("sdl2 imported: version=%s", getattr(sdl2, "__version__", "?"))

        logger.info("importing Pillow via radio.ui.render")
        from radio.ui import render

        logger.info("Pillow/render imported")

        logger.info("SDL_Init(SDL_INIT_VIDEO) attempt")
        if sdl2.SDL_Init(sdl2.SDL_INIT_VIDEO) != 0:
            raise RuntimeError(f"SDL_Init failed: {sdl2.SDL_GetError()}")
        logger.info("SDL_Init: ok")

        try:
            logger.info("SDL_CreateWindow attempt (fullscreen-desktop, size=auto)")
            window = sdl2.SDL_CreateWindow(
                b"Radio",
                sdl2.SDL_WINDOWPOS_UNDEFINED,
                sdl2.SDL_WINDOWPOS_UNDEFINED,
                0,
                0,
                sdl2.SDL_WINDOW_FULLSCREEN_DESKTOP | sdl2.SDL_WINDOW_SHOWN,
            )
            if not window:
                raise RuntimeError(f"SDL_CreateWindow failed: {sdl2.SDL_GetError()}")
            logger.info("SDL_CreateWindow: ok")

            try:
                logger.info("SDL_CreateRenderer attempt (accelerated)")
                renderer = sdl2.SDL_CreateRenderer(window, -1, sdl2.SDL_RENDERER_ACCELERATED)
                if not renderer:
                    logger.warning(
                        "Accelerated SDL renderer unavailable (%s); falling back to software",
                        sdl2.SDL_GetError(),
                    )
                    logger.info("SDL_CreateRenderer attempt (software fallback)")
                    renderer = sdl2.SDL_CreateRenderer(window, -1, sdl2.SDL_RENDERER_SOFTWARE)
                if not renderer:
                    raise RuntimeError(f"SDL_CreateRenderer failed: {sdl2.SDL_GetError()}")
                logger.info("SDL_CreateRenderer: ok")

                try:
                    sdl2.SDL_RenderSetLogicalSize(renderer, WIDTH, HEIGHT)
                    logger.info("SDL_RenderSetLogicalSize: width=%d height=%d", WIDTH, HEIGHT)

                    logger.info("SDL_CreateTexture attempt: width=%d height=%d", WIDTH, HEIGHT)
                    texture = sdl2.SDL_CreateTexture(
                        renderer,
                        sdl2.SDL_PIXELFORMAT_RGB24,
                        sdl2.SDL_TEXTUREACCESS_STREAMING,
                        WIDTH,
                        HEIGHT,
                    )
                    if not texture:
                        raise RuntimeError(f"SDL_CreateTexture failed: {sdl2.SDL_GetError()}")
                    logger.info("SDL_CreateTexture: ok")

                    try:
                        logger.info("input_reader.open attempt: device=%s", input_reader.device_path)
                        try:
                            input_reader.open()
                            logger.info("input_reader.open: ok")
                        except OSError as exc:
                            logger.warning("No se pudo abrir el dispositivo de entrada: %s", exc)
                            app.status_message = f"No se pudo abrir el dispositivo de entrada: {exc}"

                        try:
                            logger.info("main loop enter")
                            _loop(app, input_reader, sdl2, renderer, texture, render)
                            logger.info("main loop exit: should_exit=%s", app.should_exit)
                        finally:
                            logger.info("cleanup: input_reader.close")
                            input_reader.close()
                            logger.info("cleanup: app.stop_playback")
                            app.stop_playback()
                    finally:
                        logger.info("cleanup: SDL_DestroyTexture")
                        sdl2.SDL_DestroyTexture(texture)
                finally:
                    logger.info("cleanup: SDL_DestroyRenderer")
                    sdl2.SDL_DestroyRenderer(renderer)
            finally:
                logger.info("cleanup: SDL_DestroyWindow")
                sdl2.SDL_DestroyWindow(window)
        finally:
            logger.info("cleanup: SDL_Quit")
            sdl2.SDL_Quit()
    except Exception:
        logger.exception("unhandled exception in ui.app.run")
        raise


def _loop(app: RadioApp, input_reader: InputReader, sdl2, renderer, texture, render) -> None:
    event = sdl2.SDL_Event()
    while not app.should_exit:
        frame_start = time.monotonic()

        while sdl2.SDL_PollEvent(event) != 0:
            if event.type == sdl2.SDL_QUIT:
                app.request_exit()

        for control_event in input_reader.poll():
            if control_event.kind == "button":
                app.handle_button(control_event.name, control_event.pressed, control_event.repeat)
            else:
                app.handle_axis(control_event.name, control_event.value)

        app.tick()

        frame = render.render_frame(app)
        pixels = frame.tobytes("raw", "RGB")
        sdl2.SDL_UpdateTexture(texture, None, pixels, WIDTH * 3)
        sdl2.SDL_RenderClear(renderer)
        sdl2.SDL_RenderCopy(renderer, texture, None, None)
        sdl2.SDL_RenderPresent(renderer)

        elapsed = time.monotonic() - frame_start
        remaining = FRAME_INTERVAL - elapsed
        if remaining > 0:
            time.sleep(remaining)
