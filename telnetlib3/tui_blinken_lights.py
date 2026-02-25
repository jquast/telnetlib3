"""Sextant stoplight -- activity indicators for the REPL toolbar.

A single-cell stoplight using Unicode sextant characters (2x3 grid)
with time-division multiplexing to show three independently colored
lights (TX, CX, RX) in one terminal cell.

The 2x3 sextant grid maps to::

    +---+---+
    | 5 | 4 |  row 0: TX (red)
    +---+---+
    | 3 | 2 |  row 1: CX (yellow)
    +---+---+
    | 1 | 0 |  row 2: RX (green)
    +---+---+

Each frame shows one light in one column.  The rapid cycling (~30 fps)
creates persistence-of-vision, and the L/R sweep adds a candlelight
flicker.  A "2 on, 1 off" phase rotation ensures visual variety::

    phase 0: TX + CX  (RX off)
    phase 1: CX + RX  (TX off)
    phase 2: TX + RX  (CX off)

Within each phase the two active lights alternate with L->R column
scanning, giving 4 sub-frames per phase and 12 per full cycle.
"""

from __future__ import annotations

import time
from typing import Tuple

WARM_UP = 0.050   # 50 ms ramp from idle to peak
HOLD = 0.050      # 50 ms hold at peak
GLOW_DOWN = 0.500  # 500 ms ramp from peak back to idle
DURATION = WARM_UP + HOLD + GLOW_DOWN  # 600 ms total

IDLE_RGB = (26, 0, 0)        # matches toolbar bg on_color_rgb(26,0,0)
IDLE_AR_RGB = (26, 18, 0)    # matches autoreply toolbar bg
PEAK_GREEN = (40, 200, 60)   # Rx (receive)
PEAK_RED = (220, 40, 30)     # Tx (transmit)
PEAK_YELLOW = (230, 190, 30)  # Cx (compute / command)

#: 6-bit pattern -> Unicode sextant character (index 0-63).
#: Encoding from blessed/bin/cellestial.py.
SEXTANT = [' '] * 64
SEXTANT[63] = '\u2588'  # FULL BLOCK
for _b in range(1, 63):
    _u = sum((1 << i) for i in range(6) if _b & (1 << (5 - i)))
    SEXTANT[_b] = (
        '\u258c' if _u == 21 else '\u2590' if _u == 42 else
        chr(0x1FB00 + _u - 1 - sum(1 for x in (21, 42) if x < _u))
    )
del _b, _u

#: Sextant bit patterns per light (stoplight order), per column (left, right).
SEXTANT_BITS = (
    (0x20, 0x10),  # TX: top-left, top-right
    (0x08, 0x04),  # CX: mid-left, mid-right
    (0x02, 0x01),  # RX: bot-left, bot-right
)

#: "2 on, 1 off" phase rotation -- which pair of lights is active.
PHASES = ((0, 1), (1, 2), (0, 2))

#: Width of the stoplight indicator in terminal columns.
WIDTH = 1


def lerp_rgb(
    c1: Tuple[int, int, int], c2: Tuple[int, int, int], t: float,
) -> Tuple[int, int, int]:
    """Linearly interpolate between two RGB colors."""
    return (
        int(c1[0] + t * (c2[0] - c1[0])),
        int(c1[1] + t * (c2[1] - c1[1])),
        int(c1[2] + t * (c2[2] - c1[2])),
    )


class ActivityDot:
    """Activity indicator with warm-up / hold / glow-down animation.

    Timing: 50 ms warm-up, 50 ms hold at peak, 500 ms glow-down.
    Re-triggering during any phase snaps proportionally faster toward hold.

    :param peak_rgb: Peak glow color as ``(r, g, b)`` tuple.
    """

    __slots__ = ("_trigger_time", "_phase_offset", "_peak_rgb")

    def __init__(
        self, peak_rgb: Tuple[int, int, int] = PEAK_RED,
    ) -> None:
        self._trigger_time: float = 0.0
        self._phase_offset: float = 0.0
        self._peak_rgb = peak_rgb

    def trigger(self) -> None:
        """Signal that activity occurred (bytes sent/received, etc.)."""
        now = time.monotonic()
        elapsed = now - self._trigger_time + self._phase_offset
        if WARM_UP <= elapsed < DURATION:
            intensity = self._intensity_at(elapsed)
            self._phase_offset = intensity * WARM_UP
        else:
            self._phase_offset = 0.0
        self._trigger_time = now

    def _intensity_at(self, elapsed: float) -> float:
        """Return 0.0 (idle) to 1.0 (peak) for the given elapsed time."""
        if elapsed < 0.0 or elapsed >= DURATION:
            return 0.0
        if elapsed < WARM_UP:
            return elapsed / WARM_UP
        if elapsed < WARM_UP + HOLD:
            return 1.0
        return (DURATION - elapsed) / GLOW_DOWN

    def intensity(self) -> float:
        """Return current intensity 0.0..1.0."""
        elapsed = time.monotonic() - self._trigger_time + self._phase_offset
        return self._intensity_at(elapsed)

    def is_animating(self) -> bool:
        """Return ``True`` if the dot is still in an animation phase."""
        elapsed = time.monotonic() - self._trigger_time + self._phase_offset
        return 0.0 < elapsed < DURATION

    def color(self, autoreply_bg: bool = False) -> Tuple[int, int, int]:
        """Return interpolated ``(r, g, b)`` for the current frame."""
        idle = IDLE_AR_RGB if autoreply_bg else IDLE_RGB
        t = self.intensity()
        if t <= 0.0:
            return idle
        if t >= 1.0:
            return self._peak_rgb
        return lerp_rgb(idle, self._peak_rgb, t)


class Stoplight:
    """Three-indicator sextant stoplight in a single terminal cell.

    Create with :meth:`create` for the standard TX/CX/RX configuration.
    Call :meth:`frame` each render tick to get the next sextant character
    and its foreground color.
    """

    __slots__ = ("tx", "cx", "rx", "_frame")

    def __init__(
        self,
        tx: ActivityDot,
        cx: ActivityDot,
        rx: ActivityDot,
    ) -> None:
        self.tx = tx
        self.cx = cx
        self.rx = rx
        self._frame: int = 0

    @classmethod
    def create(cls) -> "Stoplight":
        """Create a stoplight with the standard color configuration."""
        return cls(
            tx=ActivityDot(peak_rgb=PEAK_RED),
            cx=ActivityDot(peak_rgb=PEAK_YELLOW),
            rx=ActivityDot(peak_rgb=PEAK_GREEN),
        )

    def is_animating(self) -> bool:
        """Return ``True`` if any dot is still animating."""
        return (
            self.tx.is_animating()
            or self.cx.is_animating()
            or self.rx.is_animating()
        )

    def frame(self, autoreply_bg: bool = False) -> Tuple[str, Tuple[int, int, int]]:
        """Advance the frame counter and return ``(sextant_char, (r, g, b))``.

        Returns ``(" ", idle_rgb)`` when all lights are idle.
        """
        ar = autoreply_bg
        dots = (self.tx, self.cx, self.rx)

        f = self._frame
        self._frame = f + 1

        # Phase rotation: which 2 of 3 lights are active.
        phase = (f // 4) % 3
        sub = f % 4  # 0=A_L, 1=A_R, 2=B_L, 3=B_R
        a_idx, b_idx = PHASES[phase]
        li = a_idx if sub < 2 else b_idx
        col = sub % 2  # 0=left, 1=right

        intensity = dots[li].intensity()
        if intensity > 0.01:
            bits = SEXTANT_BITS[li][col]
            rgb = dots[li].color(ar)
            return SEXTANT[bits], rgb

        # This light is idle -- show the other active light instead.
        other = b_idx if li == a_idx else a_idx
        if dots[other].intensity() > 0.01:
            bits = SEXTANT_BITS[other][col]
            rgb = dots[other].color(ar)
            return SEXTANT[bits], rgb

        idle = IDLE_AR_RGB if ar else IDLE_RGB
        return " ", idle
