"""File logging setup for on-device startup diagnostics.

Configures a single ``logging.FileHandler`` on the root logger writing to
``radio/log.txt`` (appended, matching ``Radio.sh``'s own stdout/stderr
``>>`` redirect into the same file) with an ISO-8601 timestamp, level,
module name and message per record.

:func:`setup_logging` is idempotent: it is called once from
``radio.main.main`` and does not require SDL2/Pillow/a display, so it can
be exercised directly by desktop unit tests. Calling it more than once in
the same process (e.g. if startup logic ever re-enters ``main()``) reuses
the existing handler instead of attaching a duplicate one, so relaunching
never doubles up log lines.
"""

from __future__ import annotations

import logging
from pathlib import Path

LOG_FILENAME = "log.txt"
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"
DATE_FORMAT = "%Y-%m-%dT%H:%M:%S"

_HANDLER_NAME = "radio.file_log_handler"


def log_path(package_root: "str | Path") -> Path:
    """Return the ``log.txt`` path for a given ``radio/`` package root."""
    return Path(package_root) / LOG_FILENAME


def setup_logging(package_root: "str | Path", level: int = logging.INFO) -> Path:
    """Attach the shared ``log.txt`` file handler to the root logger.

    Safe to call more than once per process: an existing handler carrying
    the ``_HANDLER_NAME`` marker is left in place rather than duplicated.
    Returns the resolved log file path.
    """
    path = log_path(package_root)
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    for handler in root_logger.handlers:
        if getattr(handler, "name", None) == _HANDLER_NAME:
            return path

    path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.set_name(_HANDLER_NAME)
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT))
    root_logger.addHandler(handler)
    return path
