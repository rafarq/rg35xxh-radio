"""Button/axis code mapping and raw-value normalization for /dev/input/event1.

Codes below were confirmed against a real event trace captured on the
RG35XX H (REQUISITOS.md §2 "Mapeo exacto ... [POR VERIFICAR]" is now
resolved for this device/firmware combination):

    304 A, 305 B, 306 Y, 307 X, 308 L1, 309 R1,
    17 DY, 16 DX, 310 SELECT, 311 START, 312 MENU

A second capture against ``/dev/input/event1`` confirmed the dedicated
physical volume buttons as ``EV_KEY`` codes too: 114 is VOL- and 115 is
VOL+.

``EV_KEY`` (buttons) reports 0=release, 1=press, 2=autorepeat (while
held). ``EV_ABS`` (the D-pad, exposed as a hat axis) reports -1/0/1 in
the sample trace, but the device also emits ``2`` for a still-held
direction the same way ``EV_KEY`` emits ``2`` for a still-held button —
so for axis codes a raw value of ``2`` is normalized to ``-1``, matching
the confirmed sample rather than being treated as a fourth, meaningless
axis position.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

EV_KEY = 0x01
EV_ABS = 0x03

BUTTON_CODES = {
    304: "A",
    305: "B",
    306: "Y",
    307: "X",
    308: "L1",
    309: "R1",
    310: "SELECT",
    311: "START",
    312: "MENU",
    114: "VOLUME_DOWN",
    115: "VOLUME_UP",
}

AXIS_CODES = {
    16: "DX",
    17: "DY",
}


@dataclass(frozen=True)
class ControlEvent:
    """A decoded, normalized physical-control event."""

    name: str  # "A", "B", ..., "DX", "DY"
    kind: str  # "button" or "axis"
    value: int  # normalized value (button: 0/1, axis: -1/0/1)
    pressed: bool  # True while a button is down (press or repeat)
    repeat: bool  # True only when the raw value was the autorepeat marker (2)


def normalize(code: int, raw_value: int) -> Optional[ControlEvent]:
    """Map one raw ``(code, value)`` pair to a :class:`ControlEvent`.

    Returns ``None`` for codes outside the confirmed mapping (ignored).
    """
    if code in BUTTON_CODES:
        name = BUTTON_CODES[code]
        if raw_value == 0:
            return ControlEvent(name, "button", 0, pressed=False, repeat=False)
        if raw_value == 1:
            return ControlEvent(name, "button", 1, pressed=True, repeat=False)
        if raw_value == 2:
            return ControlEvent(name, "button", 1, pressed=True, repeat=True)
        return None

    if code in AXIS_CODES:
        name = AXIS_CODES[code]
        is_repeat = raw_value == 2
        value = -1 if is_repeat else raw_value
        return ControlEvent(name, "axis", value, pressed=value != 0, repeat=is_repeat)

    return None
