"""Adapter over the RG35XX H's ALSA mixer for real hardware volume control.

On-device probing found that the simple-control names normally used with
``amixer sset``/``sget`` (e.g. ``"digital volume"``) are invalid on this
firmware. What actually works is addressing controls by their numeric id
directly via ``amixer -q cset numid=N RAW`` / ``amixer cget numid=N``.

There are two controls involved, and *both* must be set to change the
audible speaker/lineout level:

- ``numid=2`` — "Playback Digital Volume", raw range 0-63. This is a
  digital gain stage; on its own it does not change the analog output
  attenuation.
- ``numid=3`` — "Playback Lineout Volume", raw range 0-31, mapped by the
  hardware to an actual analog output attenuation of roughly -43.5dB to
  +1.5dB. This is what actually changes what comes out of the speaker, so
  it is the authoritative control for reading back "the" volume shown in
  the UI.

All commands are built as argument lists and launched with ``subprocess``
(``shell=False``, the default) against ``/usr/bin/amixer`` directly — never
a shell — matching the convention already used by ``playback/controller.py``
for the decoder/aplay pair.

Every method degrades gracefully: a missing binary, missing control or
unparsable output logs an actionable warning and returns ``None``/``False``
instead of raising, so a device with no working mixer (or a desktop dev
environment) never crashes the UI loop over volume control.
"""

from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path
from typing import Optional

DEFAULT_AMIXER_PATH = "/usr/bin/amixer"

DEFAULT_DIGITAL_NUMID = 2
DIGITAL_RAW_MIN = 0
DIGITAL_RAW_MAX = 63

DEFAULT_LINEOUT_NUMID = 3
LINEOUT_RAW_MIN = 0
LINEOUT_RAW_MAX = 31

logger = logging.getLogger(__name__)

_VALUES_RE = re.compile(r":\s*values=(-?\d+)")


def raw_to_percent(raw: int, raw_max: int = DIGITAL_RAW_MAX) -> int:
    """Convert a raw 0-``raw_max`` mixer value to a rounded 0-100 percentage."""
    clamped = max(0, min(raw_max, raw))
    return round(clamped * 100 / raw_max)


def percent_to_raw(percent: int, raw_max: int = DIGITAL_RAW_MAX) -> int:
    """Convert a 0-100 percentage to the nearest raw 0-``raw_max`` mixer value."""
    clamped = max(0, min(100, percent))
    return round(clamped * raw_max / 100)


class SystemVolume:
    """Reads/writes the hardware volume via the digital and lineout ALSA controls.

    ``numid=3`` (lineout) is the authoritative control for the UI: it is what
    the read path parses and what percent-to-raw conversions target. Every
    write sets both controls, since the digital gain alone does not move the
    audible speaker level on this hardware.
    """

    def __init__(
        self,
        amixer_path: "str | Path" = DEFAULT_AMIXER_PATH,
        numid: int = DEFAULT_LINEOUT_NUMID,
        digital_numid: int = DEFAULT_DIGITAL_NUMID,
        subprocess_module=subprocess,
        timeout: float = 2.0,
    ):
        self.amixer_path = str(amixer_path)
        self.numid = numid
        self.digital_numid = digital_numid
        self._subprocess = subprocess_module
        self.timeout = timeout

    def build_get_command(self) -> list:
        """Argv for reading the lineout control's current state via ``amixer cget``."""
        return [self.amixer_path, "cget", f"numid={self.numid}"]

    def build_set_digital_command(self, percent: int) -> list:
        """Argv for setting the digital control (0-63) to ``percent`` (clamped 0-100)."""
        raw = percent_to_raw(percent, DIGITAL_RAW_MAX)
        return [self.amixer_path, "-q", "cset", f"numid={self.digital_numid}", str(raw)]

    def build_set_lineout_command(self, percent: int) -> list:
        """Argv for setting the lineout control (0-31) to ``percent`` (clamped 0-100)."""
        raw = percent_to_raw(percent, LINEOUT_RAW_MAX)
        return [self.amixer_path, "-q", "cset", f"numid={self.numid}", str(raw)]

    def _run(self, command: list):
        try:
            return self._subprocess.run(
                command,
                shell=False,
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            logger.warning("no se pudo ejecutar %s: %s", command, exc)
            return None

    def get_volume_percent(self) -> Optional[int]:
        """Current hardware volume as 0-100 from the lineout control, or None."""
        command = self.build_get_command()
        result = self._run(command)
        if result is None:
            return None

        if result.returncode != 0:
            logger.warning(
                "amixer cget salió con código %d: %s",
                result.returncode,
                (result.stderr or "").strip(),
            )
            return None

        match = _VALUES_RE.search(result.stdout or "")
        if match is None:
            logger.warning(
                "no se pudo interpretar el volumen en la salida de amixer: %r",
                result.stdout,
            )
            return None

        return raw_to_percent(int(match.group(1)), LINEOUT_RAW_MAX)

    def _set_control(self, label: str, command: list) -> bool:
        result = self._run(command)
        if result is None:
            return False

        if result.returncode != 0:
            logger.warning(
                "amixer cset (%s) salió con código %d: %s",
                label,
                result.returncode,
                (result.stderr or "").strip(),
            )
            return False

        return True

    def set_volume_percent(self, percent: int) -> bool:
        """Set the audible hardware volume through the lineout control.

        ``numid=2`` is intentionally not written here: on the RG35XX H
        firmware it is locked after the app's graphics/audio context starts
        and rejects writes with ``Operation not permitted``. ``numid=3`` is
        the analog output attenuator and remains writable; it is therefore
        the sole authoritative control for both the real volume and UI.
        """
        return self._set_control("lineout", self.build_set_lineout_command(percent))
