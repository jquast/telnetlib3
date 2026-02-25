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

# ---------------------------------------------------------------------------
# Sextant stoplight -- activity indicators for the REPL toolbar.
#
# A single-cell stoplight using Unicode sextant characters (2x3 grid)
# with time-division multiplexing to show three independently colored
# lights (TX, CX, RX) in one terminal cell.
# ---------------------------------------------------------------------------

WARM_UP = 0.050  # 50 ms ramp from idle to peak
HOLD = 0.050  # 50 ms hold at peak
GLOW_DOWN = 0.500  # 500 ms ramp from peak back to idle
DURATION = WARM_UP + HOLD + GLOW_DOWN  # 600 ms total

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

    Timing: 50 ms warm-up, 50 ms hold at peak, 500 ms glow-down.
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

    __slots__ = ("tx", "cx", "rx", "_frame")

    def __init__(self, tx: ActivityDot, cx: ActivityDot, rx: ActivityDot) -> None:
        """Initialize stoplight with three activity dots."""
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
            return SEXTANT[bits], rgb

        # This light is idle -- show the other active light instead.
        other = b_idx if li == a_idx else a_idx
        if dots[other].intensity() > 0.01:
            bits = SEXTANT_BITS[other][col]
            rgb = dots[other].color(ar)
            return SEXTANT[bits], rgb

        idle = IDLE_AR_RGB if ar else IDLE_RGB
        return " ", idle


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

# Default ellipsis for overflow indicator (used as fallback).
_ELLIPSIS = "\u2026"

# SGR style dicts keyed to LineEditor constructor / attribute names.
# Built lazily via _make_styles() so blessed color_rgb auto-downgrades
# on terminals that lack truecolor support.
_STYLE_NORMAL: dict[str, str] = {}
_STYLE_AUTOREPLY: dict[str, str] = {}


def _make_styles() -> None:
    """Populate style dicts using blessed color API."""
    t = _get_term()
    _STYLE_NORMAL.clear()
    _STYLE_NORMAL.update(
        {
            "text_sgr": t.color_rgb(255, 239, 213),
            "suggestion_sgr": t.color_rgb(60, 40, 40),
            "bg_sgr": t.on_color_rgb(26, 0, 0),
            "ellipsis_sgr": t.color_rgb(190, 190, 190),
        }
    )
    _STYLE_AUTOREPLY.clear()
    _STYLE_AUTOREPLY.update(
        {
            "text_sgr": t.color_rgb(184, 134, 11),
            "suggestion_sgr": t.color_rgb(80, 60, 0),
            "bg_sgr": t.on_color_rgb(26, 18, 0),
            "ellipsis_sgr": t.color_rgb(80, 60, 0),
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
    elif kind == "wander":
        # Cyan (180) -> yellow (60) as autowander progresses.
        hue = 180.0 - fraction * 120.0
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


_BAR_CAP_LEFT = "\U0001fb2b"  # 🬫 Block Sextant-2346
_BAR_CAP_RIGHT = "\U0001fb1b"  # 🬛 Block Sextant-1345
_DMZ_CHAR = "\u2581"  # ▁ Lower One Eighth Block


def _dmz_line(cols: int, active: bool = False) -> str:
    """
    Return a styled DMZ divider line of *cols* width.

    :param cols: Terminal width.
    :param active: Use gold color when autoreply/wander/discover is active.
    """
    t = _get_term()
    color = t.color_rgb(184, 134, 11) if active else t.color_rgb(50, 10, 10)
    return str(color) + (_DMZ_CHAR * cols) + str(t.normal)


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
    if flash_elapsed >= 0.0 and flash_elapsed < _FLASH_DURATION:
        fill_bg = _flash_color(bar_color, flash_elapsed)
        empty_bg = _flash_color("#2a2a2a", flash_elapsed)
        filled_sgr = _sgr_fg("#101010") + _sgr_bg(fill_bg)
        empty_sgr = _sgr_fg("#666666") + _sgr_bg(empty_bg)
    else:
        fill_bg = bar_color
        empty_bg = "#2a2a2a"
        filled_sgr = _sgr_fg("#101010") + _sgr_bg(bar_color)
        empty_sgr = _sgr_fg("#666666") + _sgr_bg("#2a2a2a")

    suffix = {"hp": " hp", "mp": " mp", "xp": " xp", "wander": " AW", "randomwalk": " rndwlk"}.get(
        kind, ""
    )
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


_SEPARATOR_WIDTH = 3


def _layout_toolbar(
    slots: List["_ToolbarSlot"], cols: int
) -> Tuple[List[List[Tuple[str, str]]], List[List[Tuple[str, str]]]]:
    """
    Fit toolbar slots into *cols* columns by priority.

    :returns: ``(left_items, right_items)`` — each a list of fragment
        lists, ordered by ``display_order``.
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
    return ([s.fragments for s in left], [s.fragments for s in right])


def _render_toolbar(
    ctx: "SessionContext",
    scroll: Any,
    out: asyncio.StreamWriter,
    autoreply_engine: Any,
    toolbar_state: dict[str, Any],
) -> bool:
    """
    Render GMCP vitals toolbar at ``scroll.input_row + 1``.

    :returns: ``True`` if a flash is active and the caller should
        schedule a re-render.
    """
    from .client_repl import _RESERVE_WITH_TOOLBAR

    gmcp_data: Optional[dict[str, Any]] = ctx.gmcp_data or None
    if not toolbar_state.get("has_gmcp"):
        if not gmcp_data:
            return False
        toolbar_state["has_gmcp"] = True
        scroll.grow_reserve(_RESERVE_WITH_TOOLBAR)
        if ctx.on_gmcp_ready is not None:
            ctx.on_gmcp_ready()
            ctx.on_gmcp_ready = None

    engine = autoreply_engine
    ar_active = engine is not None and (engine.exclusive_active or engine.reply_pending)
    wander_active = ctx.wander_active
    discover_active = ctx.discover_active
    randomwalk_active = ctx.randomwalk_active

    slots: List[_ToolbarSlot] = []
    room_name = ""
    now = time.monotonic()
    needs_reflash = False

    if gmcp_data:
        status = gmcp_data.get("Char.Status")
        if isinstance(status, dict):
            level = status.get("level")
            if level is not None:
                lv_text = _segmented(f"Lv.{level}")
                lv_frags: List[Tuple[str, str]] = [(_sgr_fg("#aaaaaa"), lv_text)]
                slots.append(
                    _ToolbarSlot(
                        priority=7,
                        display_order=0,
                        width=_wcswidth(lv_text),
                        fragments=lv_frags,
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
                cash_frags: List[Tuple[str, str]] = [(_sgr_fg("#aaaaaa"), money_str)]
                slots.append(
                    _ToolbarSlot(
                        priority=6,
                        display_order=1,
                        width=_wcswidth(money_str),
                        fragments=cash_frags,
                        side="left",
                        min_width=0,
                        label="",
                    )
                )

        vitals = gmcp_data.get("Char.Vitals")
        if isinstance(vitals, dict):
            hp = vitals.get("hp", vitals.get("HP"))
            maxhp = vitals.get("maxhp", vitals.get("maxHP", vitals.get("max_hp")))
            if hp is not None:
                try:
                    hp_int = int(hp)
                except (TypeError, ValueError):
                    hp_int = 0
                last_hp = toolbar_state.get("last_hp")
                if last_hp is not None and hp_int != last_hp:
                    toolbar_state["hp_flash"] = now
                toolbar_state["last_hp"] = hp_int
                hp_flash = toolbar_state.get("hp_flash", 0.0)
                hp_elapsed = now - hp_flash
                if hp_elapsed < _FLASH_DURATION:
                    needs_reflash = True
                hp_frags = _vital_bar(
                    hp,
                    maxhp,
                    _BAR_WIDTH,
                    "hp",
                    flash_elapsed=hp_elapsed if hp_elapsed < _FLASH_DURATION else -1.0,
                )
                hp_w = sum(_wcswidth(t) for _, t in hp_frags)
                slots.append(
                    _ToolbarSlot(
                        priority=1,
                        display_order=2,
                        width=hp_w,
                        fragments=hp_frags,
                        side="left",
                        min_width=0,
                        label="",
                    )
                )

            mp = vitals.get(
                "mp", vitals.get("MP", vitals.get("mana", vitals.get("sp", vitals.get("SP"))))
            )
            maxmp = vitals.get(
                "maxmp",
                vitals.get("maxMP", vitals.get("max_mp", vitals.get("maxsp", vitals.get("maxSP")))),
            )
            if mp is not None:
                try:
                    mp_int = int(mp)
                except (TypeError, ValueError):
                    mp_int = 0
                last_mp = toolbar_state.get("last_mp")
                if last_mp is not None and mp_int != last_mp:
                    toolbar_state["mp_flash"] = now
                toolbar_state["last_mp"] = mp_int
                mp_flash = toolbar_state.get("mp_flash", 0.0)
                mp_elapsed = now - mp_flash
                if mp_elapsed < _FLASH_DURATION:
                    needs_reflash = True
                mp_frags = _vital_bar(
                    mp,
                    maxmp,
                    _BAR_WIDTH,
                    "mp",
                    flash_elapsed=mp_elapsed if mp_elapsed < _FLASH_DURATION else -1.0,
                )
                mp_w = sum(_wcswidth(t) for _, t in mp_frags)
                slots.append(
                    _ToolbarSlot(
                        priority=4,
                        display_order=3,
                        width=mp_w,
                        fragments=mp_frags,
                        side="left",
                        min_width=0,
                        label="",
                    )
                )

        if isinstance(status, dict):
            xp_raw = status.get("xp", status.get("XP", status.get("experience")))
            maxxp = status.get(
                "maxxp", status.get("maxXP", status.get("max_xp", status.get("maxexp")))
            )
            if xp_raw is not None:
                try:
                    xp_int = int(xp_raw)
                except (TypeError, ValueError):
                    xp_int = 0
                last_xp = toolbar_state.get("last_xp")
                xp_history = toolbar_state.setdefault("xp_history", collections.deque())
                if last_xp is not None and xp_int != last_xp:
                    toolbar_state["xp_flash"] = now
                    xp_history.append((now, xp_int))
                elif last_xp is None:
                    xp_history.append((now, xp_int))
                toolbar_state["last_xp"] = xp_int

                cutoff = now - 300.0
                while xp_history and xp_history[0][0] < cutoff:
                    xp_history.popleft()

                xp_flash = toolbar_state.get("xp_flash", 0.0)
                xp_elapsed = now - xp_flash
                if xp_elapsed < _FLASH_DURATION:
                    needs_reflash = True
                xp_frags = _vital_bar(
                    xp_raw,
                    maxxp,
                    _BAR_WIDTH,
                    "xp",
                    flash_elapsed=xp_elapsed if xp_elapsed < _FLASH_DURATION else -1.0,
                )
                xp_w = sum(_wcswidth(t) for _, t in xp_frags)
                slots.append(
                    _ToolbarSlot(
                        priority=5,
                        display_order=4,
                        width=xp_w,
                        fragments=xp_frags,
                        side="left",
                        min_width=0,
                        label="",
                    )
                )

                if len(xp_history) >= 2 and maxxp is not None:
                    oldest_t, oldest_xp = xp_history[0]
                    span = now - oldest_t
                    if span > 0:
                        rate_per_sec = (xp_int - oldest_xp) / span
                        try:
                            remaining = int(maxxp) - xp_int
                        except (TypeError, ValueError):
                            remaining = 0
                        if rate_per_sec > 0 and remaining > 0:
                            eta_sec = remaining / rate_per_sec
                            eta_hr = eta_sec / 3600.0
                            if eta_hr >= 1.0:
                                eta_text = _segmented(f"ETA {eta_hr:.1f}h")
                            else:
                                eta_min = int(eta_sec / 60.0)
                                eta_text = _segmented(f"ETA {eta_min}m")
                            eta_frags: List[Tuple[str, str]] = [(_sgr_fg("#888888"), eta_text)]
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

        room_info = gmcp_data.get("Room.Info", gmcp_data.get("Room.Name"))
        if isinstance(room_info, dict):
            room_name = str(room_info.get("name", room_info.get("Name", "")))
        elif isinstance(room_info, str):
            room_name = room_info

    if room_name:
        toolbar_state["rprompt_text"] = room_name

    is_autoreply_bg = wander_active or discover_active or randomwalk_active or ar_active

    if randomwalk_active:
        rwcur = ctx.randomwalk_current
        rwtot = ctx.randomwalk_total
        mode_frags = _vital_bar(rwcur, rwtot, 16, "randomwalk")
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
            )
        )
    elif wander_active:
        wcur = ctx.wander_current
        wtot = ctx.wander_total
        mode_frags = _vital_bar(wcur, wtot, 12, "wander")
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
            )
        )
    elif discover_active:
        dcur = ctx.discover_current
        dtot = ctx.discover_total
        mode_frags = _vital_bar(dcur, dtot, 12, "discover")
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
            )
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
        loc_text = toolbar_state.get("rprompt_text", "")
        if loc_text:
            full_text = " " + loc_text
            full_w = _wcswidth(full_text)
            loc_sgr = _sgr_fg("#dddddd")
            slots.append(
                _ToolbarSlot(
                    priority=2,
                    display_order=10,
                    width=full_w,
                    fragments=[(loc_sgr, full_text)],
                    side="right",
                    min_width=5,
                    label=full_text,
                )
            )

    bt = _get_term()
    cols = bt.width
    left_items, right_items = _layout_toolbar(slots, cols)

    toolbar_row = scroll.input_row + 1
    out.write(bt.move_yx(toolbar_row, 0).encode())

    if is_autoreply_bg:
        bg_sgr = bt.on_color_rgb(26, 18, 0) + bt.color_rgb(184, 134, 11)
    else:
        bg_sgr = bt.on_color_rgb(26, 0, 0)
    out.write(bg_sgr.encode())

    left_total = 0
    for i, frags in enumerate(left_items):
        if i > 0:
            out.write("   ".encode())
            left_total += _SEPARATOR_WIDTH
        for sgr, text in frags:
            out.write(f"{sgr}{text}".encode())
            out.write(bg_sgr.encode())
            left_total += _wcswidth(text)

    right_total = 0
    for i, frags in enumerate(right_items):
        if i > 0:
            right_total += _SEPARATOR_WIDTH
        right_total += sum(_wcswidth(t) for _, t in frags)

    stoplight: Optional[_Stoplight] = toolbar_state.get("stoplight")
    modem_w = _MODEM_WIDTH if stoplight is not None else 0

    pad = max(1, cols - left_total - right_total - modem_w)
    out.write((" " * pad).encode())

    right_sgr = _sgr_fg("#dddddd") if not is_autoreply_bg else ""
    for i, frags in enumerate(right_items):
        if i > 0:
            out.write("   ".encode())
        for sgr, text in frags:
            effective_sgr = sgr if sgr else right_sgr
            out.write(f"{effective_sgr}{text}".encode())
            out.write(bg_sgr.encode())

    if stoplight is not None:
        ch, (r, g, b) = stoplight.frame(autoreply_bg=is_autoreply_bg)
        out.write(f"{bt.color_rgb(r, g, b)}{ch}".encode())
        out.write(bg_sgr.encode())
        if stoplight.is_animating():
            needs_reflash = True

    out.write(bt.normal.encode())
    return needs_reflash


def _schedule_flash_frame(
    loop: asyncio.AbstractEventLoop,
    ctx: "SessionContext",
    scroll: Any,
    out: asyncio.StreamWriter,
    autoreply_engine: Any,
    toolbar_state: dict[str, Any],
    editor: "blessed.line_editor.LiveLineEditor",
    bt: "blessed.Terminal",
) -> None:
    """Schedule repeating flash animation frames via ``loop.call_later``."""

    def _tick() -> None:
        out.write(CURSOR_HIDE.encode())
        still = _render_toolbar(ctx, scroll, out, autoreply_engine, toolbar_state)
        cursor_col = editor.display.cursor
        out.write(bt.move_yx(scroll.input_row, cursor_col).encode())
        out.write(CURSOR_SHOW.encode())
        if still:
            loop.call_later(_FLASH_INTERVAL, _tick)
        else:
            toolbar_state["_flash_active"] = False

    loop.call_later(_FLASH_INTERVAL, _tick)


def _render_input_line(
    display: "blessed.line_editor.DisplayState", scroll: Any, out: asyncio.StreamWriter
) -> None:
    """
    Render editor display state at ``scroll.input_row``.

    Horizontal scrolling is handled by the blessed :class:`LineEditor`
    via its ``max_width`` parameter.  The ``display`` object provides
    already-clipped text, suggestion, cursor position,
    ``clipped_left`` / ``clipped_right`` flags for ellipsis indicators,
    and SGR style fields.
    """
    bt = _get_term()
    cols = bt.width

    out.write(bt.move_yx(scroll.input_row, 0).encode())
    out.write(display.bg_sgr.encode())

    if display.overflow_left:
        out.write(f"{display.ellipsis_sgr}{_ELLIPSIS}".encode())
        out.write(display.bg_sgr.encode())

    if display.text_sgr:
        out.write(display.text_sgr.encode())
    out.write(display.text.encode())

    if display.suggestion:
        out.write(f"{display.suggestion_sgr}{display.suggestion}".encode())

    if display.overflow_right:
        out.write(f"{display.ellipsis_sgr}{_ELLIPSIS}".encode())

    text_w = _wcswidth(display.text) + _wcswidth(display.suggestion)
    rendered = (1 if display.overflow_left else 0) + text_w + (1 if display.overflow_right else 0)
    pad = cols - rendered
    if pad > 0:
        out.write(f"{display.bg_sgr}{' ' * pad}".encode())
    out.write(bt.normal.encode())

    out.write(bt.move_yx(scroll.input_row, display.cursor).encode())
