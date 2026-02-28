"""Color math, vital-bar rendering, toolbar layout, activity stoplight, and display helpers."""

# std imports
import time
import asyncio
import logging
import collections
from typing import TYPE_CHECKING, Any, List, Tuple, Optional, NamedTuple

if TYPE_CHECKING:
    import blessed
    import blessed.line_editor

    from .session_context import SessionContext

log = logging.getLogger(__name__)

WARM_UP = 0.025  # 25 ms ramp from idle to peak
HOLD = 0.025  # 25 ms hold at peak
GLOW_DOWN = 0.250  # 250 ms ramp from peak back to idle
DURATION = WARM_UP + HOLD + GLOW_DOWN  # 300 ms total

IDLE_RGB = (26, 0, 0)  # matches toolbar bg on_color_rgb(26,0,0)
IDLE_AR_RGB = (26, 18, 0)  # matches autoreply toolbar bg
PEAK_GREEN = (40, 200, 60)  # Rx (receive)
PEAK_RED = (220, 40, 30)  # Tx (transmit)
PEAK_YELLOW = (230, 190, 30)  # Cx (compute / command)

#: 6-bit pattern -> Unicode sextant character (index 0-63).
SEXTANT: list[str] = [" "] * 64
SEXTANT[63] = "\u2588"  # FULL BLOCK
for _b in range(1, 63):
    _u = sum((1 << i) for i in range(6) if _b & (1 << (5 - i)))
    SEXTANT[_b] = (
        "\u258c"
        if _u == 21
        else "\u2590" if _u == 42 else chr(0x1FB00 + _u - 1 - sum(1 for x in (21, 42) if x < _u))
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
STOPLIGHT_WIDTH = 1


def _activity_hint(engine: Any) -> str:
    """
    Build a short autoreply status hint from *engine*.

    :returns: Hint string like ``"#3 | until /pat/  [return to cancel]"``,
        or ``""`` when there is nothing to display.
    """
    if engine is None:
        return ""
    parts: list[str] = []
    idx = getattr(engine, "exclusive_rule_index", None)
    if idx is not None and idx != 0:
        parts.append(f"#{idx}")
    st = getattr(engine, "status_text", "")
    if st:
        parts.append(st)
    if parts:
        return " | ".join(parts) + "  [return to cancel]"
    return ""


def _until_progress(engine: Any) -> Optional[float]:
    """
    Return the until timer progress fraction, or ``None``.

    :returns: Float in ``0.0..1.0`` when an until/untils timer is active.
    """
    if engine is None:
        return None
    return getattr(engine, "until_progress", None)


def _write_hint(
    hint: str,
    out: "asyncio.StreamWriter",
    bt: "blessed.Terminal",
    progress: Optional[float] = None,
    bg_sgr: str = "",
) -> None:
    """
    Write *hint* at the current cursor position with optional progress bar.

    When *progress* is not ``None``, the hint is split into a reverse-video
    left portion (elapsed) and a normal dim right portion (remaining).

    :param hint: Plain hint text.
    :param out: Stream to write SGR-encoded bytes to.
    :param bt: blessed Terminal instance.
    :param progress: ``0.0..1.0`` fraction, or ``None`` for plain dim text.
    :param bg_sgr: Optional background SGR prefix (e.g. autoreply bg color).
    """
    if not hint:
        return
    dim = str(bt.color_rgb(60, 40, 40))
    normal = bt.normal
    if progress is not None:
        split = int(len(hint) * progress + 0.5)
        left = hint[:split]
        right = hint[split:]
        rev = str(bt.reverse)
        out.write(f"{bg_sgr}{dim}{rev}{left}{normal}{bg_sgr}{dim}{right}{normal}".encode())
    else:
        out.write(f"{bg_sgr}{dim}{hint}{normal}".encode())


def lerp_rgb(c1: Tuple[int, int, int], c2: Tuple[int, int, int], t: float) -> Tuple[int, int, int]:
    """Linearly interpolate between two RGB colors."""
    return (
        int(c1[0] + t * (c2[0] - c1[0])),
        int(c1[1] + t * (c2[1] - c1[1])),
        int(c1[2] + t * (c2[2] - c1[2])),
    )


class ActivityDot:
    """
    Activity indicator with warm-up / hold / glow-down animation.

    Timing: 25 ms warm-up, 25 ms hold at peak, 250 ms glow-down.
    Re-triggering during any phase snaps proportionally faster toward hold.

    :param peak_rgb: Peak glow color as ``(r, g, b)`` tuple.
    """

    __slots__ = ("_trigger_time", "_phase_offset", "_peak_rgb")

    def __init__(self, peak_rgb: Tuple[int, int, int] = PEAK_RED) -> None:
        """Initialize activity dot with given peak color."""
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
    """
    Three-indicator sextant stoplight in a single terminal cell.

    Create with :meth:`create` for the standard TX/CX/RX configuration.
    Call :meth:`frame` each render tick to get the next sextant character
    and its foreground color.
    """

    __slots__ = ("tx", "cx", "rx", "_frame", "_last_result")

    def __init__(self, tx: ActivityDot, cx: ActivityDot, rx: ActivityDot) -> None:
        """Initialize stoplight with three activity dots."""
        self.tx = tx
        self.cx = cx
        self.rx = rx
        self._frame: int = 0
        self._last_result: Tuple[str, Tuple[int, int, int]] = (" ", IDLE_RGB)

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
        return self.tx.is_animating() or self.cx.is_animating() or self.rx.is_animating()

    def frame(self, autoreply_bg: bool = False) -> Tuple[str, Tuple[int, int, int]]:
        """
        Advance the frame counter and return ``(sextant_char, (r, g, b))``.

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
            self._last_result = SEXTANT[bits], rgb
            return self._last_result

        # This light is idle -- show the other active light instead.
        other = b_idx if li == a_idx else a_idx
        if dots[other].intensity() > 0.01:
            bits = SEXTANT_BITS[other][col]
            rgb = dots[other].color(ar)
            self._last_result = SEXTANT[bits], rgb
            return self._last_result

        idle = IDLE_AR_RGB if ar else IDLE_RGB
        self._last_result = " ", idle
        return self._last_result


def _get_term() -> "blessed.Terminal":
    """Return the module-level blessed Terminal singleton."""
    from .client_repl import _get_term as _gt

    return _gt()


# DECTCEM cursor visibility.
CURSOR_HIDE: str = "\x1b[?25l"
CURSOR_SHOW: str = "\x1b[?25h"

# DECSCUSR cursor shape escapes (xterm extension, no terminfo equivalent).
CURSOR_BLINKING_BLOCK: str = "\x1b[1 q"  # DECSCUSR 1
CURSOR_STEADY_BLOCK: str = "\x1b[2 q"  # DECSCUSR 2
CURSOR_BLINKING_UNDERLINE: str = "\x1b[3 q"  # DECSCUSR 3
CURSOR_STEADY_UNDERLINE: str = "\x1b[4 q"  # DECSCUSR 4
CURSOR_BLINKING_BAR: str = "\x1b[5 q"  # DECSCUSR 5
CURSOR_STEADY_BAR: str = "\x1b[6 q"  # DECSCUSR 6
CURSOR_DEFAULT: str = "\x1b[0 q"  # DECSCUSR 0 -- terminal default
_CURSOR_STYLES: dict[str, str] = {
    "blinking_bar": CURSOR_BLINKING_BAR,
    "steady_bar": CURSOR_STEADY_BAR,
    "blinking_block": CURSOR_BLINKING_BLOCK,
    "steady_block": CURSOR_STEADY_BLOCK,
    "blinking_underline": CURSOR_BLINKING_UNDERLINE,
    "steady_underline": CURSOR_STEADY_UNDERLINE,
}
_DEFAULT_CURSOR_STYLE = "steady_block"

# Cursor color: medium red-brown foreground on the input-line background.
# Cursor color via OSC 12 (xterm/kitty/foot/iTerm2).  Uses X11 rgb: format.
CURSOR_COLOR_RGB = (50, 32, 14)
CURSOR_COLOR_OSC: str = (
    f"\x1b]12;rgb:{CURSOR_COLOR_RGB[0]:02x}/{CURSOR_COLOR_RGB[1]:02x}"
    f"/{CURSOR_COLOR_RGB[2]:02x}\x07"
)
CURSOR_COLOR_RESET_OSC: str = "\x1b]112\x07"  # OSC 112 -- reset to default

# Default ellipsis for overflow indicator (used as fallback).
_ELLIPSIS = "\u2026"

# SGR style dicts keyed to LineEditor constructor / attribute names.
# Built lazily via _make_styles() so blessed color_rgb auto-downgrades
# on terminals that lack truecolor support.
_STYLE_NORMAL: dict[str, str] = {}
_STYLE_AUTOREPLY: dict[str, str] = {}


def _make_styles() -> None:
    """Populate style dicts using blessed color API."""
    blessed_term = _get_term()
    cr, cg, cb = CURSOR_COLOR_RGB
    cursor_fg = blessed_term.color_rgb(cr, cg, cb)
    _STYLE_NORMAL.clear()
    _STYLE_NORMAL.update(
        {
            "text_sgr": blessed_term.color_rgb(255, 239, 213),
            "suggestion_sgr": blessed_term.color_rgb(60, 40, 40),
            "bg_sgr": blessed_term.on_color_rgb(26, 0, 0),
            "ellipsis_sgr": blessed_term.color_rgb(190, 190, 190),
            "cursor_sgr": cursor_fg + blessed_term.on_color_rgb(26, 0, 0),
        }
    )
    _STYLE_AUTOREPLY.clear()
    _STYLE_AUTOREPLY.update(
        {
            "text_sgr": blessed_term.color_rgb(184, 134, 11),
            "suggestion_sgr": blessed_term.color_rgb(80, 60, 0),
            "bg_sgr": blessed_term.on_color_rgb(26, 18, 0),
            "ellipsis_sgr": blessed_term.color_rgb(80, 60, 0),
            "cursor_sgr": cursor_fg + blessed_term.on_color_rgb(26, 18, 0),
        }
    )


def _hsv_to_rgb(h: float, s: float, v: float) -> Tuple[int, int, int]:
    """Convert HSV (h in [0,360), s/v in [0,1]) to (r, g, b) in [0,255]."""
    import colorsys

    r, g, b = colorsys.hsv_to_rgb(h / 360.0, s, v)
    return (int(r * 255), int(g * 255), int(b * 255))


def _rgb_to_hsv(r: int, g: int, b: int) -> Tuple[float, float, float]:
    """Convert (r, g, b) in [0,255] to HSV (h in [0,360), s/v in [0,1])."""
    import colorsys

    h, s, v = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
    return (h * 360.0, s, v)


def _lerp_hsv(
    hsv1: Tuple[float, float, float], hsv2: Tuple[float, float, float], t: float
) -> Tuple[float, float, float]:
    """Linearly interpolate between two HSV colors using shortest-arc hue."""
    h1, s1, v1 = hsv1
    h2, s2, v2 = hsv2
    dh = (h2 - h1) % 360.0
    if dh > 180.0:
        dh -= 360.0
    h = (h1 + t * dh) % 360.0
    return (h, s1 + t * (s2 - s1), v1 + t * (v2 - v1))


# Width of the inner progress bar (between the sextant caps).
_BAR_WIDTH = 20

_FLASH_RAMP_UP = 0.100  # 100ms linear ramp original -> inverse
_FLASH_HOLD = 0.250  # 250ms freeze at inverse
_FLASH_RAMP_DOWN = 0.350  # 350ms linear ramp inverse -> original
_FLASH_DURATION = 0.700  # total: ramp_up + hold + ramp_down = 700ms
_FLASH_INTERVAL = 0.033  # ~33ms between frames (~30fps)


def _flash_color(base_hex: str, elapsed: float) -> str:
    """
    Compute the flash-animated color for *base_hex* at *elapsed* seconds.

    :param base_hex: Original ``#rrggbb`` hex color.
    :param elapsed: Seconds since flash started; negative means no flash.
    :returns: Interpolated ``#rrggbb`` hex color.
    """
    if elapsed < 0.0 or elapsed >= _FLASH_DURATION:
        return base_hex
    r = int(base_hex[1:3], 16)
    g = int(base_hex[3:5], 16)
    b = int(base_hex[5:7], 16)
    hsv_orig = _rgb_to_hsv(r, g, b)
    # Flash toward white (same hue, zero saturation, full brightness)
    # to avoid hue-interpolation artifacts (e.g. green→magenta goes through cyan).
    hsv_inv = (hsv_orig[0], 0.0, 1.0)
    if elapsed < _FLASH_RAMP_UP:
        t = elapsed / _FLASH_RAMP_UP
    elif elapsed < _FLASH_RAMP_UP + _FLASH_HOLD:
        t = 1.0
    else:
        t = (_FLASH_DURATION - elapsed) / _FLASH_RAMP_DOWN
    h, s, v = _lerp_hsv(hsv_orig, hsv_inv, t)
    cr, cg, cb = _hsv_to_rgb(h, s, v)
    return f"#{cr:02x}{cg:02x}{cb:02x}"


def _flash_bg_rgb(base_hex: str, elapsed: float) -> tuple[int, int, int] | None:
    """
    Return the inverse-RGB background ``(r, g, b)`` for a flash frame.

    During the ramp-up / hold / ramp-down window the background interpolates
    from black ``(0, 0, 0)`` toward the inverse of *base_hex*
    ``(255-R, 255-G, 255-B)`` and back.

    :returns: ``(r, g, b)`` tuple or ``None`` when outside the flash window.
    """
    if elapsed < 0.0 or elapsed >= _FLASH_DURATION:
        return None
    r = int(base_hex[1:3], 16)
    g = int(base_hex[3:5], 16)
    b = int(base_hex[5:7], 16)
    inv_r, inv_g, inv_b = 255 - r, 255 - g, 255 - b
    if elapsed < _FLASH_RAMP_UP:
        t = elapsed / _FLASH_RAMP_UP
    elif elapsed < _FLASH_RAMP_UP + _FLASH_HOLD:
        t = 1.0
    else:
        t = (_FLASH_DURATION - elapsed) / _FLASH_RAMP_DOWN
    return (int(inv_r * t), int(inv_g * t), int(inv_b * t))


def _fmt_value(n: int) -> str:
    """
    Format a numeric value with k/m suffixes for compact display.

    :param n: Integer value.
    :returns: Formatted string, e.g. ``1.2k``, ``3.5m``.
    """
    if n >= 1_000_000:
        v = n / 1_000_000
        return f"{v:.1f}m"
    if n >= 1_000:
        v = n / 1_000
        return f"{v:.1f}k"
    return str(n)


def _vital_color(fraction: float, kind: str) -> str:
    """
    Return an RGB hex color for a vitals bar.

    :param fraction: 0.0 (empty) to 1.0 (full).
    :param kind: ``"hp"`` for red-to-green, ``"mp"`` for golden-yellow-to-blue,
        ``"xp"`` for purple-to-violet.
    """
    fraction = max(0.0, min(1.0, fraction))
    if kind == "hp":
        # Stay red below 33%, then red -> pastel forest green over 33%-100%.
        hue = max(0.0, (fraction - 0.33) / 0.67) * 138.0
        sat, val = 0.50, 0.75
    elif kind == "xp":
        # Purple (270) -> cyan (180) as XP fills.
        hue = 270.0 - fraction * 90.0
        sat, val = 0.7, 0.8
    elif kind == "discover":
        # Green (120) -> magenta (300) as autodiscover progresses.
        hue = 120.0 + fraction * 180.0
        sat, val = 0.7, 0.8
    elif kind == "randomwalk":
        # Orange (30) -> teal (170) as randomwalk progresses.
        hue = 30.0 + fraction * 140.0
        sat, val = 0.7, 0.8
    else:
        # Stay golden yellow below 33%, then golden-yellow->blue over 33%-100%.
        # hue 45=golden yellow, hue 240=blue.
        t = max(0.0, (fraction - 0.33) / 0.67)
        hue = 45.0 + t * 195.0
        sat, val = 0.7, 0.8
    r, g, b = _hsv_to_rgb(hue, sat, val)
    return f"#{r:02x}{g:02x}{b:02x}"


def _wcswidth(text: str) -> int:
    """Return display width of *text*, handling wide chars."""
    from wcwidth import wcswidth

    w = wcswidth(text)
    return w if w >= 0 else len(text)


_Stoplight = Stoplight
_MODEM_WIDTH = STOPLIGHT_WIDTH


_BAR_CAP_LEFT = "\U0001fb38"  # 🬸 Block Sextant-2345
_BAR_CAP_RIGHT = "\U0001fb1b"  # 🬛 Block Sextant-1345
_DMZ_CHAR = "\u2581"  # ▁ Lower One Eighth Block


def _dmz_line(cols: int, active: bool = False) -> str:
    """
    Return a styled DMZ divider line of *cols* width.

    :param cols: Terminal width.
    :param active: Use gold color when autoreply/wander/discover is active.
    """
    blessed_term = _get_term()
    color = blessed_term.color_rgb(184, 134, 11) if active else blessed_term.color_rgb(50, 10, 10)
    return str(color) + (_DMZ_CHAR * cols) + str(blessed_term.normal)


def _segmented(text: str) -> str:
    """Replace ASCII digits 0-9 with segmented digit glyphs U+1FBF0..U+1FBF9."""
    return text.translate(
        str.maketrans(
            "0123456789",
            "\U0001fbf0\U0001fbf1"
            "\U0001fbf2\U0001fbf3\U0001fbf4\U0001fbf5"
            "\U0001fbf6\U0001fbf7\U0001fbf8\U0001fbf9",
        )
    )


def _sgr_fg(hexcolor: str) -> str:
    """SGR foreground from ``#rrggbb`` hex via blessed (auto-downconverts)."""
    return str(_get_term().color_hex(hexcolor))


def _sgr_bg(hexcolor: str) -> str:
    """SGR background from ``#rrggbb`` hex via blessed (auto-downconverts)."""
    return str(_get_term().on_color_hex(hexcolor))


def _vital_bar(
    current: Any, maximum: Any, width: int, kind: str, flash_elapsed: float = -1.0
) -> "List[Tuple[str, str]]":
    """
    Build a labelled progress-bar with sextant bookends and overlaid text.

    The label (e.g. ``513/514 100% HP``) is rendered *on top of* the bar
    using segmented digit glyphs.  Sextant block characters bookend the
    bar for a rounded appearance.

    :param flash_elapsed: Seconds since flash start; negative means no flash.
    """
    try:
        cur = int(current)
    except (TypeError, ValueError):
        cur = 0
    if maximum is not None:
        try:
            mx = int(maximum)
        except (TypeError, ValueError):
            mx = 0
    else:
        mx = 0

    if mx > 0:
        frac = max(0.0, min(1.0, cur / mx))
    else:
        frac = 1.0

    filled = int(round(frac * width))
    pct = int(round(frac * 100))

    bar_color = _vital_color(frac, kind)
    if 0.0 <= flash_elapsed < _FLASH_DURATION:
        fill_bg = _flash_color(bar_color, flash_elapsed)
        empty_bg = _flash_color("#2a2a2a", flash_elapsed)
        filled_sgr = _sgr_fg("#101010") + _sgr_bg(fill_bg)
        empty_sgr = _sgr_fg("#666666") + _sgr_bg(empty_bg)
    else:
        fill_bg = bar_color
        empty_bg = "#2a2a2a"
        filled_sgr = _sgr_fg("#101010") + _sgr_bg(bar_color)
        empty_sgr = _sgr_fg("#666666") + _sgr_bg("#2a2a2a")

    suffix = {
        "hp": " hp",
        "mp": " mp",
        "xp": " xp",
        "wander": " AW",
        "randomwalk": " random walk",
        "discover": " discover",
    }.get(kind, "")
    if mx > 0:
        left_part = _segmented(f"{_fmt_value(cur)}/{_fmt_value(mx)}")
        right_part = _segmented(f"{pct}%") + suffix
    else:
        left_part = _segmented(f"{_fmt_value(cur)}")
        right_part = suffix.lstrip()

    # Left-align values, right-align pct+suffix, gap in the middle
    # where the filled/empty boundary is most visible.
    gap = max(1, width - len(left_part) - len(right_part))
    bar_text = (left_part + " " * gap + right_part)[:width]
    if len(bar_text) < width:
        bar_text += " " * (width - len(bar_text))

    filled_text = bar_text[:filled]
    empty_text = bar_text[filled:]

    left_color = fill_bg if filled > 0 else empty_bg
    right_color = fill_bg if filled >= width else empty_bg

    return [
        (_sgr_fg(left_color), _BAR_CAP_LEFT),
        (filled_sgr, filled_text),
        (empty_sgr, empty_text),
        (_sgr_fg(right_color), _BAR_CAP_RIGHT),
    ]


def _center_truncate(text: str, avail: int) -> str:
    """Truncate *text* to fit *avail* display columns."""
    if avail <= 0:
        return ""
    w = _wcswidth(text)
    if w <= avail:
        return text
    # Truncate character by character.
    result = []
    total = 0
    for ch in text:
        cw = _wcswidth(ch)
        if total + cw + 1 > avail:
            break
        result.append(ch)
        total += cw
    return "".join(result) + "\u2026"


class _ToolbarSlot(NamedTuple):
    """A single toolbar item with layout metadata."""

    priority: int
    display_order: int
    width: int
    fragments: List[Tuple[str, str]]
    side: str
    min_width: int
    label: str
    growable: bool = False
    grow_params: Optional[Tuple[Any, ...]] = None


_SEPARATOR_WIDTH = 3
_BAR_GAP_WIDTH = 1


def _layout_toolbar(
    slots: List["_ToolbarSlot"], cols: int
) -> Tuple[List["_ToolbarSlot"], List["_ToolbarSlot"]]:
    """
    Fit toolbar slots into *cols* columns by priority.

    :returns: ``(left_slots, right_slots)`` ordered by ``display_order``.
    """
    left: List[_ToolbarSlot] = []
    right: List[_ToolbarSlot] = []
    left_used = 0
    right_used = 0
    has_left = False
    has_right = False

    for slot in sorted(slots, key=lambda s: s.priority):
        sep = (
            _SEPARATOR_WIDTH
            if ((slot.side == "left" and has_left) or (slot.side == "right" and has_right))
            else 0
        )
        need = slot.width + sep
        avail = cols - left_used - right_used - 1  # 1 char min pad

        if need <= avail:
            if slot.side == "left":
                left.append(slot)
                left_used += need
                has_left = True
            else:
                right.append(slot)
                right_used += need
                has_right = True
        elif slot.min_width > 0 and slot.min_width < slot.width:
            fit = avail - sep
            if fit >= slot.min_width:
                trimmed_text = _center_truncate(slot.label, fit)
                trimmed_w = _wcswidth(trimmed_text)
                trimmed_frags = [(slot.fragments[0][0], trimmed_text)]
                trimmed = slot._replace(width=trimmed_w, fragments=trimmed_frags)
                if slot.side == "left":
                    left.append(trimmed)
                    left_used += trimmed_w + sep
                    has_left = True
                else:
                    right.append(trimmed)
                    right_used += trimmed_w + sep
                    has_right = True

    left.sort(key=lambda s: s.display_order)
    right.sort(key=lambda s: s.display_order)
    return (left, right)


def _left_sep_widths(left: List["_ToolbarSlot"]) -> List[int]:
    """
    Return per-gap separator widths for left slots.

    Adjacent growable (vital-bar) slots use ``_BAR_GAP_WIDTH``; all other
    gaps use ``_SEPARATOR_WIDTH``.
    """
    gaps: List[int] = []
    for i in range(1, len(left)):
        if left[i - 1].growable and left[i].growable:
            gaps.append(_BAR_GAP_WIDTH)
        else:
            gaps.append(_SEPARATOR_WIDTH)
    return gaps


def _fill_toolbar(
    left: List["_ToolbarSlot"], right: List["_ToolbarSlot"], cols: int
) -> Tuple[List["_ToolbarSlot"], List["_ToolbarSlot"], int]:
    """
    Distribute extra horizontal space across growable slots and separators.

    :returns: ``(left_slots, right_slots, sep_width)`` with expanded bars.
    """
    left_gaps = _left_sep_widths(left)
    n_right_seps = max(0, len(right) - 1)
    used = sum(s.width for s in left) + sum(s.width for s in right)
    used += sum(left_gaps) + n_right_seps * _SEPARATOR_WIDTH
    min_pad = 1 if left and right else 0
    extra = cols - used - min_pad

    growable = [s for s in left + right if s.growable and s.grow_params is not None]
    if not growable or extra <= 0:
        return (left, right, _SEPARATOR_WIDTH)

    n_expandable_seps = n_right_seps
    if n_expandable_seps > 0:
        sep_bonus = extra // 4
        per_sep = sep_bonus // n_expandable_seps
        sep_width = _SEPARATOR_WIDTH + per_sep
        remaining = extra - per_sep * n_expandable_seps
    else:
        sep_width = _SEPARATOR_WIDTH
        remaining = extra

    per_bar = remaining // len(growable)
    leftover = remaining - per_bar * len(growable)

    grow_set = set(id(s) for s in growable)
    grow_idx = 0

    def _expand(slot: _ToolbarSlot) -> _ToolbarSlot:
        nonlocal grow_idx
        if id(slot) not in grow_set:
            return slot
        bonus = per_bar
        if grow_idx < leftover:
            bonus += 1
        grow_idx += 1
        params = slot.grow_params
        assert params is not None
        if len(params) == 4:
            raw, maxval, kind, flash_elapsed = params
            inner = slot.width - 2 + bonus
            frags = _vital_bar(raw, maxval, inner, kind, flash_elapsed=flash_elapsed)
            new_w = sum(_wcswidth(t) for _, t in frags)
            return slot._replace(width=new_w, fragments=frags)
        if len(params) == 1:
            full_text = params[0]
            new_w = slot.width + bonus
            trimmed = _center_truncate(full_text, new_w)
            trimmed_w = _wcswidth(trimmed)
            sgr = slot.fragments[0][0] if slot.fragments else ""
            return slot._replace(width=trimmed_w, fragments=[(sgr, trimmed)])
        return slot

    new_left = [_expand(s) for s in left]
    new_right = [_expand(s) for s in right]
    return (new_left, new_right, sep_width)


class VitalTracker:
    """Track one vital stat (HP, MP) with flash-on-change timing."""

    __slots__ = ("last_value", "flash_time")

    def __init__(self) -> None:
        """Initialize tracker with no previous value."""
        self.last_value: Optional[int] = None
        self.flash_time: float = 0.0

    def update(self, raw: Any, now: float) -> float:
        """
        Update tracker with *raw* value at time *now*.

        :returns: Elapsed seconds since last flash (or negative if no flash).
        """
        try:
            val = int(raw)
        except (TypeError, ValueError):
            val = 0
        if self.last_value is not None and val != self.last_value:
            self.flash_time = now
        self.last_value = val
        elapsed = now - self.flash_time
        return elapsed if elapsed < _FLASH_DURATION else -1.0


class XPTracker(VitalTracker):
    """Vital tracker with XP history for ETA calculation."""

    __slots__ = ("history",)

    _HISTORY_WINDOW: float = 300.0

    def __init__(self) -> None:
        """Initialize XP tracker with empty history deque."""
        super().__init__()
        self.history: collections.deque[Tuple[float, int]] = collections.deque()

    def update(self, raw: Any, now: float) -> float:
        """Update tracker, also maintaining XP history for ETA."""
        try:
            val = int(raw)
        except (TypeError, ValueError):
            val = 0
        if self.last_value is not None and val != self.last_value:
            self.flash_time = now
            self.history.append((now, val))
        elif self.last_value is None:
            self.history.append((now, val))
        self.last_value = val

        cutoff = now - self._HISTORY_WINDOW
        while self.history and self.history[0][0] < cutoff:
            self.history.popleft()

        elapsed = now - self.flash_time
        return elapsed if elapsed < _FLASH_DURATION else -1.0

    def eta_fragments(self, maxxp: Any, now: float) -> Optional[List[Tuple[str, str]]]:
        """
        Compute ETA fragments from XP history.

        :returns: Fragment list for the ETA slot, or ``None`` if unavailable.
        """
        if len(self.history) < 2 or maxxp is None or self.last_value is None:
            return None
        oldest_t, oldest_xp = self.history[0]
        span = now - oldest_t
        if span <= 0:
            return None
        rate_per_sec = (self.last_value - oldest_xp) / span
        try:
            remaining = int(maxxp) - self.last_value
        except (TypeError, ValueError):
            return None
        if rate_per_sec <= 0 or remaining <= 0:
            return None
        eta_sec = remaining / rate_per_sec
        eta_hr = eta_sec / 3600.0
        if eta_hr >= 1.0:
            eta_text = _segmented(f"ETA {eta_hr:.1f}h")
        else:
            eta_min = int(eta_sec / 60.0)
            eta_text = _segmented(f"ETA {eta_min}m")
        return [(_sgr_fg("#888888"), eta_text)]


class ToolbarRenderer:
    """
    Encapsulates GMCP vitals toolbar state and rendering.

    Replaces the former ``_render_toolbar`` function and its ``toolbar_state``
    dict with typed attributes and small focused methods.
    """

    def __init__(
        self,
        ctx: "SessionContext",
        scroll: Any,
        out: asyncio.StreamWriter,
        stoplight: Optional[Stoplight],
        rprompt_text: str = "",
    ) -> None:
        """Initialize toolbar renderer for *ctx*."""
        self.ctx = ctx
        self.scroll = scroll
        self.out = out
        self.stoplight = stoplight
        self.rprompt_text = rprompt_text
        self.flash_active: bool = False
        self._has_gmcp: bool = False
        self._eta_refresh_active: bool = False
        self._until_progress_active: bool = False
        self._last_progress_col: int = -1
        self._last_eta_text: str = ""
        self._cached_eta_frags: Optional[List[Tuple[str, str]]] = None
        self.hp = VitalTracker()
        self.mp = VitalTracker()
        self.xp = XPTracker()

    def render(self, autoreply_engine: Any) -> bool:
        """
        Render GMCP vitals toolbar at ``scroll.input_row + 1``.

        :returns: ``True`` if a flash is active and the caller should
            schedule a re-render.
        """
        if not self._ensure_gmcp_ready():
            return False

        engine = autoreply_engine
        ar_active = engine is not None and (engine.exclusive_active or engine.reply_pending)
        discover_active = self.ctx.discover_active
        randomwalk_active = self.ctx.randomwalk_active

        now = time.monotonic()
        slots, needs_reflash = self._build_slots(
            engine, ar_active, discover_active, randomwalk_active, now
        )
        is_autoreply_bg = discover_active or randomwalk_active or ar_active
        return self._paint(slots, is_autoreply_bg, needs_reflash)

    def _ensure_gmcp_ready(self) -> bool:
        """Initialize toolbar on first GMCP data; return ``False`` if no data yet."""
        from .client_repl import _RESERVE_WITH_TOOLBAR

        gmcp_data: Optional[dict[str, Any]] = self.ctx.gmcp_data or None
        if not self._has_gmcp:
            if not gmcp_data:
                return False
            self._has_gmcp = True
            self.scroll.grow_reserve(_RESERVE_WITH_TOOLBAR)
            if self.ctx.on_gmcp_ready is not None:
                self.ctx.on_gmcp_ready()
                self.ctx.on_gmcp_ready = None
        return True

    def _build_slots(
        self,
        engine: Any,
        ar_active: bool,
        discover_active: bool,
        randomwalk_active: bool,
        now: float,
    ) -> Tuple[List[_ToolbarSlot], bool]:
        """Build all toolbar slots and return ``(slots, needs_reflash)``."""
        slots: List[_ToolbarSlot] = []
        needs_reflash = False
        gmcp_data: Optional[dict[str, Any]] = self.ctx.gmcp_data or None

        if gmcp_data:
            status = gmcp_data.get("Char.Status")
            if isinstance(status, dict):
                self._status_slots(status, slots)

            vitals = gmcp_data.get("Char.Vitals")
            if isinstance(vitals, dict):
                hp = vitals.get("hp", vitals.get("HP"))
                maxhp = vitals.get("maxhp", vitals.get("maxHP", vitals.get("max_hp")))
                if hp is not None:
                    if self._vital_slot(self.hp, hp, maxhp, _BAR_WIDTH, "hp", 1, 2, now, slots):
                        needs_reflash = True

                mp = vitals.get(
                    "mp", vitals.get("MP", vitals.get("mana", vitals.get("sp", vitals.get("SP"))))
                )
                maxmp = vitals.get(
                    "maxmp",
                    vitals.get(
                        "maxMP", vitals.get("max_mp", vitals.get("maxsp", vitals.get("maxSP")))
                    ),
                )
                if mp is not None:
                    if self._vital_slot(self.mp, mp, maxmp, _BAR_WIDTH, "mp", 4, 3, now, slots):
                        needs_reflash = True

            if isinstance(status, dict):
                xp_raw = status.get("xp", status.get("XP", status.get("experience")))
                maxxp = status.get(
                    "maxxp", status.get("maxXP", status.get("max_xp", status.get("maxexp")))
                )
                if xp_raw is not None:
                    if self._vital_slot(self.xp, xp_raw, maxxp, _BAR_WIDTH, "xp", 5, 4, now, slots):
                        needs_reflash = True
                    self._xp_eta_slot(maxxp, now, slots)

            room_info = gmcp_data.get("Room.Info", gmcp_data.get("Room.Name"))
            if isinstance(room_info, dict):
                room_name = str(room_info.get("name", room_info.get("Name", "")))
            elif isinstance(room_info, str):
                room_name = room_info
            else:
                room_name = ""
            if room_name:
                self.rprompt_text = room_name

        if self.ctx.chat_unread > 0:
            badge = f"F10-Chat:{self.ctx.chat_unread}"
            slots.append(
                _ToolbarSlot(
                    priority=3,
                    display_order=8,
                    width=_wcswidth(badge),
                    fragments=[(_sgr_fg("#ffff00"), badge)],
                    side="left",
                    min_width=0,
                    label="",
                )
            )

        self._right_slot(engine, ar_active, discover_active, randomwalk_active, slots)
        return slots, needs_reflash

    def _status_slots(self, status: dict[str, Any], slots: List[_ToolbarSlot]) -> None:
        """Add Level and Money slots from ``Char.Status``."""
        level = status.get("level")
        if level is not None:
            lv_text = _segmented(f"Lv.{level}")
            slots.append(
                _ToolbarSlot(
                    priority=7,
                    display_order=0,
                    width=_wcswidth(lv_text),
                    fragments=[(_sgr_fg("#aaaaaa"), lv_text)],
                    side="left",
                    min_width=0,
                    label="",
                )
            )
        money = status.get("money")
        if money is not None:
            try:
                money_int = int(money)
                money_str = _segmented(f"${money_int:,}")
            except (TypeError, ValueError):
                money_str = _segmented(f"${money}")
            slots.append(
                _ToolbarSlot(
                    priority=6,
                    display_order=1,
                    width=_wcswidth(money_str),
                    fragments=[(_sgr_fg("#aaaaaa"), money_str)],
                    side="left",
                    min_width=0,
                    label="",
                )
            )

    @staticmethod
    def _vital_slot(
        tracker: VitalTracker,
        raw: Any,
        maxval: Any,
        width: int,
        kind: str,
        priority: int,
        order: int,
        now: float,
        slots: List[_ToolbarSlot],
    ) -> bool:
        """
        Update *tracker* and append a vital bar slot.

        :returns: ``True`` if a flash animation is active.
        """
        flash_elapsed = tracker.update(raw, now)
        needs_reflash = flash_elapsed >= 0.0
        frags = _vital_bar(raw, maxval, width, kind, flash_elapsed=flash_elapsed)
        frags_w = sum(_wcswidth(t) for _, t in frags)
        slots.append(
            _ToolbarSlot(
                priority=priority,
                display_order=order,
                width=frags_w,
                fragments=frags,
                side="left",
                min_width=0,
                label="",
                growable=True,
                grow_params=(raw, maxval, kind, flash_elapsed),
            )
        )
        return needs_reflash

    def _xp_eta_slot(self, maxxp: Any, now: float, slots: List[_ToolbarSlot]) -> None:
        """
        Append an ETA slot from cached fragments.

        ETA is recomputed only by the 1-second ``schedule_eta_refresh`` timer
        to avoid jittery numbers on every line of server output.
        """
        if self._cached_eta_frags is None:
            self._cached_eta_frags = self.xp.eta_fragments(maxxp, now)
        eta_frags = self._cached_eta_frags
        if eta_frags is not None:
            eta_text = eta_frags[0][1]
            slots.append(
                _ToolbarSlot(
                    priority=8,
                    display_order=5,
                    width=_wcswidth(eta_text),
                    fragments=eta_frags,
                    side="left",
                    min_width=0,
                    label="",
                )
            )

    def _right_slot(
        self,
        engine: Any,
        ar_active: bool,
        discover_active: bool,
        randomwalk_active: bool,
        slots: List[_ToolbarSlot],
    ) -> None:
        """Append the right-side slot (walk mode, autoreply, or room name)."""
        if randomwalk_active:
            self._mode_bar_slot(
                self.ctx.randomwalk_current, self.ctx.randomwalk_total, 24, "randomwalk", slots
            )
        elif discover_active:
            self._mode_bar_slot(
                self.ctx.discover_current, self.ctx.discover_total, 20, "discover", slots
            )
        elif ar_active:
            idx = getattr(engine, "exclusive_rule_index", None)
            ar_label = f"Autoreply #{idx}" if idx is not None else "Autoreply"
            ar_text = " " + ar_label
            slots.append(
                _ToolbarSlot(
                    priority=3,
                    display_order=10,
                    width=len(ar_text),
                    fragments=[("", ar_text)],
                    side="right",
                    min_width=0,
                    label="",
                )
            )
        else:
            loc_text = self.rprompt_text
            if loc_text:
                full_text = " " + loc_text
                full_w = _wcswidth(full_text)
                slots.append(
                    _ToolbarSlot(
                        priority=2,
                        display_order=10,
                        width=full_w,
                        fragments=[(_sgr_fg("#dddddd"), full_text)],
                        side="right",
                        min_width=5,
                        label=full_text,
                        growable=True,
                        grow_params=(full_text,),
                    )
                )

    @staticmethod
    def _mode_bar_slot(
        cur: Any, tot: Any, width: int, kind: str, slots: List[_ToolbarSlot]
    ) -> None:
        """Append a walk-mode progress bar slot."""
        mode_frags = _vital_bar(cur, tot, width, kind)
        mode_w = sum(_wcswidth(t) for _, t in mode_frags)
        slots.append(
            _ToolbarSlot(
                priority=3,
                display_order=10,
                width=mode_w,
                fragments=mode_frags,
                side="right",
                min_width=0,
                label="",
                growable=True,
                grow_params=(cur, tot, kind, -1.0),
            )
        )

    def _paint(self, slots: List[_ToolbarSlot], is_autoreply_bg: bool, needs_reflash: bool) -> bool:
        """Write ANSI sequences for the toolbar row."""
        blessed_term = _get_term()
        cols = blessed_term.width
        left_slots, right_slots = _layout_toolbar(slots, cols)
        left_slots, right_slots, sep_width = _fill_toolbar(left_slots, right_slots, cols)
        sep_str = " " * sep_width

        toolbar_row = self.scroll.input_row + 1
        self.out.write(blessed_term.move_yx(toolbar_row, 0).encode())

        if is_autoreply_bg:
            bg_sgr = blessed_term.on_color_rgb(26, 18, 0) + blessed_term.color_rgb(184, 134, 11)
        else:
            bg_sgr = blessed_term.on_color_rgb(26, 0, 0)
        self.out.write(bg_sgr.encode())

        left_total = 0
        for i, slot in enumerate(left_slots):
            if i > 0:
                prev_growable = left_slots[i - 1].growable
                gap = _BAR_GAP_WIDTH if prev_growable and slot.growable else sep_width
                self.out.write((" " * gap).encode())
                left_total += gap
            for sgr, text in slot.fragments:
                self.out.write(f"{sgr}{text}".encode())
                self.out.write(bg_sgr.encode())
                left_total += _wcswidth(text)

        right_total = 0
        for i, slot in enumerate(right_slots):
            if i > 0:
                right_total += sep_width
            right_total += sum(_wcswidth(t) for _, t in slot.fragments)

        pad = max(1, cols - left_total - right_total)
        self.out.write((" " * pad).encode())

        right_sgr = _sgr_fg("#dddddd") if not is_autoreply_bg else ""
        for i, slot in enumerate(right_slots):
            if i > 0:
                self.out.write(sep_str.encode())
            for sgr, text in slot.fragments:
                effective_sgr = sgr if sgr else right_sgr
                self.out.write(f"{effective_sgr}{text}".encode())
                self.out.write(bg_sgr.encode())

        # Drive stoplight animation via frame() so cursor_light stays in
        # sync, but don't render the glyph here — it only appears at the
        # cursor position.
        if self.stoplight is not None:
            self.stoplight.frame(autoreply_bg=is_autoreply_bg)
            if self.stoplight.is_animating():
                needs_reflash = True

        self.out.write(blessed_term.normal.encode())
        return needs_reflash

    def cursor_light(
        self, bt: "blessed.Terminal", row: int, col: int, is_autoreply_bg: bool
    ) -> bool:
        """
        Draw the stoplight sextant at the cursor position.

        When the stoplight is animating, this replaces the terminal block cursor
        with the modem-lights glyph.  The terminal cursor must be hidden
        (DECTCEM off) by the caller while this is active.

        Uses the cached frame from the last :meth:`Stoplight.frame` call
        so that the cursor light stays in sync with the toolbar stoplight.

        :returns: ``True`` if the light was drawn, ``False`` if idle.
        """
        if self.stoplight is None or not self.stoplight.is_animating():
            return False
        ch, (r, g, b) = self.stoplight._last_result
        if ch == " ":
            return False
        bg = _STYLE_AUTOREPLY["bg_sgr"] if is_autoreply_bg else _STYLE_NORMAL["bg_sgr"]
        self.out.write(bt.move_yx(row, col).encode())
        self.out.write(f"{bg}{bt.color_rgb(r, g, b)}{ch}{bt.normal}".encode())
        self.out.write(bt.move_yx(row, col).encode())
        return True

    def schedule_flash(
        self,
        loop: asyncio.AbstractEventLoop,
        autoreply_engine: Any,
        editor: "blessed.line_editor.LiveLineEditor",
        bt: "blessed.Terminal",
    ) -> None:
        """Schedule repeating flash animation frames via ``loop.call_later``."""

        def _tick() -> None:
            self.out.write(CURSOR_HIDE.encode())
            still = self.render(autoreply_engine)
            input_row = self.scroll.input_row
            engine = autoreply_engine
            ar = engine is not None and (engine.exclusive_active or engine.reply_pending)
            is_ar_bg = self.ctx.discover_active or self.ctx.randomwalk_active or ar

            cq = self.ctx.command_queue
            ac = self.ctx.active_command
            from time import monotonic as _mono

            ac_elapsed = _mono() - self.ctx.active_command_time
            show_cmd = cq is not None or (ac is not None and ac_elapsed < _FLASH_DURATION)
            if show_cmd:
                from .client_repl_commands import _render_command_queue, _render_active_command

                hint = _activity_hint(engine)
                prog = _until_progress(engine)
                if cq is not None:
                    cursor_col = _render_command_queue(
                        cq,
                        self.scroll,
                        self.out,
                        flash_elapsed=ac_elapsed,
                        hint=hint,
                        progress=prog,
                    )
                else:
                    cursor_col = _render_active_command(
                        ac,
                        self.scroll,
                        self.out,
                        flash_elapsed=ac_elapsed,
                        hint=hint,
                        progress=prog,
                    )
                if prog is not None:
                    still = True
                if ac_elapsed < _FLASH_DURATION:
                    still = True
                drew = self.cursor_light(bt, input_row, cursor_col, is_ar_bg)
                if not drew:
                    self.out.write(bt.move_yx(input_row, cursor_col).encode())
                    self.out.write(CURSOR_SHOW.encode())
            else:
                hint = _activity_hint(engine)
                hint_w = len(hint) if hint else 0
                edit_w = max(2, bt.width - hint_w)
                if not still:
                    self.out.write(editor.render(bt, input_row, edit_w).encode())
                if hint:
                    prog = _until_progress(engine)
                    col = bt.width - hint_w
                    bg = _STYLE_AUTOREPLY["bg_sgr"] if is_ar_bg else _STYLE_NORMAL["bg_sgr"]
                    self.out.write(bt.move_yx(input_row, col).encode())
                    _write_hint(hint, self.out, bt, progress=prog, bg_sgr=bg)
                    if prog is not None:
                        still = True
                cursor_col = editor.display.cursor
                drew = self.cursor_light(bt, input_row, cursor_col, is_ar_bg)
                if not drew:
                    style = _STYLE_AUTOREPLY if is_ar_bg else _STYLE_NORMAL
                    self.out.write(bt.move_yx(input_row, cursor_col).encode())
                    self.out.write(CURSOR_COLOR_OSC.encode())
                    self.out.write(style["cursor_sgr"].encode())
                    self.out.write(CURSOR_SHOW.encode())
                    self.out.write(bt.normal.encode())

            if still:
                loop.call_later(_FLASH_INTERVAL, _tick)
            else:
                self.flash_active = False

        loop.call_later(_FLASH_INTERVAL, _tick)

    _ETA_REFRESH_INTERVAL = 1.0

    def schedule_eta_refresh(
        self,
        loop: asyncio.AbstractEventLoop,
        autoreply_engine: Any,
        editor: "blessed.line_editor.LiveLineEditor",
        bt: "blessed.Terminal",
    ) -> None:
        """
        Schedule a periodic ETA refresh at 1-second intervals.

        Only redraws the toolbar when the ETA text has changed since the last render, to avoid
        unnecessary flicker.
        """
        if self._eta_refresh_active:
            return
        self._eta_refresh_active = True

        def _eta_tick() -> None:
            if not self._has_gmcp:
                self._eta_refresh_active = False
                return
            gmcp_data = self.ctx.gmcp_data or {}
            status = gmcp_data.get("Char.Vitals", gmcp_data.get("Char.Status", {}))
            if isinstance(status, dict):
                maxxp = status.get(
                    "maxxp", status.get("maxXP", status.get("max_xp", status.get("maxexp")))
                )
            else:
                maxxp = None
            now = time.monotonic()
            frags = self.xp.eta_fragments(maxxp, now)
            self._cached_eta_frags = frags
            eta_text = frags[0][1] if frags else ""
            if eta_text != self._last_eta_text:
                self._last_eta_text = eta_text
                self.out.write(CURSOR_HIDE.encode())
                self.render(autoreply_engine)
                has_command = (
                    self.ctx.command_queue is not None or self.ctx.active_command is not None
                )
                if not has_command:
                    cursor_col = editor.display.cursor
                    input_row = self.scroll.input_row
                    engine = autoreply_engine
                    ar = engine is not None and (engine.exclusive_active or engine.reply_pending)
                    is_ar_bg = self.ctx.discover_active or self.ctx.randomwalk_active or ar
                    drew = self.cursor_light(bt, input_row, cursor_col, is_ar_bg)
                    if not drew:
                        style = _STYLE_AUTOREPLY if is_ar_bg else _STYLE_NORMAL
                        self.out.write(bt.move_yx(input_row, cursor_col).encode())
                        self.out.write(CURSOR_COLOR_OSC.encode())
                        self.out.write(style["cursor_sgr"].encode())
                        self.out.write(CURSOR_SHOW.encode())
                        self.out.write(bt.normal.encode())
            loop.call_later(self._ETA_REFRESH_INTERVAL, _eta_tick)

        loop.call_later(self._ETA_REFRESH_INTERVAL, _eta_tick)

    _PROGRESS_REFRESH_INTERVAL = 0.1

    def schedule_until_progress(
        self,
        loop: asyncio.AbstractEventLoop,
        autoreply_engine: Any,
        editor: "blessed.line_editor.LiveLineEditor",
        bt: "blessed.Terminal",
    ) -> None:
        """
        Schedule a 100 ms ticker to redraw the until progress bar.

        Only redraws when the integer progress-bar split column changes, to avoid unnecessary
        flicker.
        """
        if self._until_progress_active:
            return
        self._until_progress_active = True
        self._last_progress_col = -1

        def _progress_tick() -> None:
            engine = autoreply_engine
            prog = _until_progress(engine)
            if prog is None:
                self._until_progress_active = False
                self._last_progress_col = -1
                return
            hint = _activity_hint(engine)
            if not hint:
                self._until_progress_active = False
                return
            split = int(len(hint) * prog + 0.5)
            if split == self._last_progress_col:
                loop.call_later(self._PROGRESS_REFRESH_INTERVAL, _progress_tick)
                return
            self._last_progress_col = split
            hint_w = len(hint)
            col = bt.width - hint_w
            if col < 2:
                loop.call_later(self._PROGRESS_REFRESH_INTERVAL, _progress_tick)
                return
            ar = engine is not None and (engine.exclusive_active or engine.reply_pending)
            is_ar_bg = self.ctx.discover_active or self.ctx.randomwalk_active or ar
            bg = _STYLE_AUTOREPLY["bg_sgr"] if is_ar_bg else _STYLE_NORMAL["bg_sgr"]
            self.out.write(CURSOR_HIDE.encode())
            self.out.write(bt.move_yx(self.scroll.input_row, col).encode())
            _write_hint(hint, self.out, bt, progress=prog, bg_sgr=bg)
            has_command = self.ctx.command_queue is not None or self.ctx.active_command is not None
            if not has_command:
                cursor_col = editor.display.cursor
                input_row = self.scroll.input_row
                drew = self.cursor_light(bt, input_row, cursor_col, is_ar_bg)
                if not drew:
                    style = _STYLE_AUTOREPLY if is_ar_bg else _STYLE_NORMAL
                    self.out.write(bt.move_yx(input_row, cursor_col).encode())
                    self.out.write(CURSOR_COLOR_OSC.encode())
                    self.out.write(style["cursor_sgr"].encode())
                    self.out.write(CURSOR_SHOW.encode())
                    self.out.write(bt.normal.encode())
            loop.call_later(self._PROGRESS_REFRESH_INTERVAL, _progress_tick)

        loop.call_later(self._PROGRESS_REFRESH_INTERVAL, _progress_tick)
