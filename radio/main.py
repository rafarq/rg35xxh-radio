"""Entrypoint: ``python3 -m radio.main`` (invoked by ``Radio.sh``).

Wires the graphics-free pieces (playlist, storage, playback controller,
app state) together, then hands off to the SDL2 frame loop in
``radio/ui/app.py``. SDL2/Pillow are only imported once execution
actually reaches that hand-off, so ``python -m compileall`` and
``pytest`` never need them.

Every path referenced here (playlist, data dir, engine binary, CA
bundle) is resolved relative to this package's own directory, never the
process's current working directory, so the app behaves the same
whether launched via ``Radio.sh`` from the firmware's menu or run
directly for development (REQUISITOS.md §1: never write outside
``radio/``).

Every stage of startup is logged to ``radio/log.txt`` (see
:mod:`radio.log`) so an on-device crash — where the firmware menu gives
no visible console — can be diagnosed after the fact. Any unhandled
exception is logged with its full traceback before being re-raised, so
the process still exits non-zero and ``Radio.sh``'s stderr redirect
still captures the traceback too.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent

PLAYLIST_PATH = PACKAGE_ROOT / "data" / "playlist.m3u"
DATA_DIR = PACKAGE_ROOT / "data"
PLAYLISTS_DIR = PACKAGE_ROOT / "playlists"
PLAYLIST_CACHE_DIR = DATA_DIR / "playlist-cache"
ENGINE_PATH = PACKAGE_ROOT / "engine" / "ffmpeg-aarch64"
CACERT_PATH = PACKAGE_ROOT / "assets" / "cacert.pem"
INPUT_DEVICE_PATH = "/dev/input/event1"

logger = logging.getLogger(__name__)


def _log_path_state(name: str, path: "str | Path") -> None:
    path = Path(path)
    exists = path.exists()
    logger.info(
        "path %s=%s exists=%s is_file=%s executable=%s",
        name,
        path,
        exists,
        path.is_file() if exists else False,
        os.access(path, os.X_OK) if exists else False,
    )


def main(argv=None) -> int:
    from radio.log import setup_logging

    log_file = setup_logging(PACKAGE_ROOT)
    logger.info("process start pid=%d argv=%s log_file=%s", os.getpid(), sys.argv, log_file)

    try:
        _log_path_state("playlist", PLAYLIST_PATH)
        _log_path_state("playlists_dir", PLAYLISTS_DIR)
        _log_path_state("playlist_cache_dir", PLAYLIST_CACHE_DIR)
        _log_path_state("data_dir", DATA_DIR)
        _log_path_state("engine", ENGINE_PATH)
        _log_path_state("cacert", CACERT_PATH)
        _log_path_state("input_device", INPUT_DEVICE_PATH)

        from radio.app.state import RadioApp
        from radio.audio.volume import SystemVolume
        from radio.input.reader import InputReader
        from radio.playback.controller import PlayerController
        from functools import partial

        from radio.playlist import sources
        from radio.storage import DataStore

        logger.info("datastore init: data_dir=%s", DATA_DIR)
        store = DataStore(DATA_DIR)
        logger.info("datastore init: done")

        configured_source = sources.source_from_value(store.load_config().get("playlist_source"))
        playlist_loader = partial(
            sources.load_source,
            playlists_dir=PLAYLISTS_DIR,
            cache_dir=PLAYLIST_CACHE_DIR,
            cacert_path=CACERT_PATH,
            default_playlist_path=PLAYLIST_PATH,
        )
        playlist_lister = partial(sources.list_available_sources, playlists_dir=PLAYLISTS_DIR)
        playlist_result = playlist_loader(configured_source)
        logger.info(
            "playlist source requested=%s:%s resolved=%s:%s status=%s stations=%d groups=%d error=%s",
            configured_source.kind.value,
            configured_source.id,
            playlist_result.source.kind.value,
            playlist_result.source.id,
            playlist_result.status,
            len(playlist_result.parse_result.stations),
            len(playlist_result.parse_result.groups),
            playlist_result.error,
        )

        logger.info("player init: engine=%s cacert=%s", ENGINE_PATH, CACERT_PATH)
        player = PlayerController(engine_path=ENGINE_PATH, cacert_path=CACERT_PATH)
        logger.info("player init: done")

        volume_control = SystemVolume()
        logger.info(
            "volume control init: amixer=%s numid=%r digital_numid=%r",
            volume_control.amixer_path,
            volume_control.numid,
            volume_control.digital_numid,
        )

        app = RadioApp(
            playlist_result.parse_result,
            store,
            player,
            volume_control=volume_control,
            playlist_load_result=playlist_result,
            playlist_loader=playlist_loader,
            playlist_source_lister=playlist_lister,
        )
        logger.info("app state init: done")

        input_reader = InputReader(INPUT_DEVICE_PATH)
        logger.info("input reader constructed: device=%s", INPUT_DEVICE_PATH)

        from radio.ui.app import run

        logger.info("handing off to ui.app.run (SDL2 main loop)")
        run(app, input_reader)
        logger.info("ui.app.run returned normally: normal exit")
        return 0
    except Exception:
        logger.exception("unhandled exception during startup/run")
        raise


if __name__ == "__main__":
    sys.exit(main())
