"""
ANSI color palette translation for telnet client output.

Most modern terminals use custom palette colors for ANSI colors 0-15 (e.g.
Solarized, Dracula, Gruvbox themes).  When connecting to MUDs and BBS systems,
the artwork and text colors were designed for specific hardware palettes such as
IBM EGA, VGA, or Amiga.  The terminal's custom palette distorts the intended
colors, often ruining ANSI artwork.

By translating basic 16-color SGR codes into their exact 24-bit RGB equivalents
from named hardware palettes, we bypass the terminal's palette entirely and
display the colors the artist intended.

This feature is enabled by default using the EGA palette.  Use
``--colormatch=none`` on the ``telnetlib3-client`` command line to disable it.

Example usage::

    # Default EGA palette with brightness/contrast adjustment
    telnetlib3-client mud.example.com 4000

    # Use VGA palette instead
    telnetlib3-client --colormatch=vga mud.example.com

    # Disable color translation entirely
    telnetlib3-client --colormatch=none mud.example.com

    # Custom brightness and contrast
    telnetlib3-client --color-brightness=0.7 --color-contrast=0.6 mud.example.com

    # White-background terminal (reverse video)
    telnetlib3-client --reverse-video mud.example.com
"""

from __future__ import annotations

# std imports
import re
from typing import Dict, List, Match, Tuple, Optional, NamedTuple

# 3rd party
from wcwidth.sgr_state import _SGR_PATTERN

__all__ = ("ColorConfig", "ColorFilter", "PALETTES")

# Type alias for a 16-color palette: 16 (R, G, B) tuples indexed 0-15.
# Index 0-7: normal colors (black, red, green, yellow, blue, magenta, cyan, white)
# Index 8-15: bright variants of the same order.
PaletteRGB = Tuple[
    Tuple[int, int, int],
    Tuple[int, int, int],
    Tuple[int, int, int],
    Tuple[int, int, int],
    Tuple[int, int, int],
    Tuple[int, int, int],
    Tuple[int, int, int],
    Tuple[int, int, int],
    Tuple[int, int, int],
    Tuple[int, int, int],
    Tuple[int, int, int],
    Tuple[int, int, int],
    Tuple[int, int, int],
    Tuple[int, int, int],
    Tuple[int, int, int],
    Tuple[int, int, int],
]

# Hardware color palettes.  Each defines exact RGB values for ANSI colors 0-15.
PALETTES: Dict[str, PaletteRGB] = {
    # IBM Enhanced Graphics Adapter -- the classic DOS palette used by most
    # BBS and MUD ANSI artwork.
    "ega": (
        (0, 0, 0),
        (170, 0, 0),
        (0, 170, 0),
        (170, 85, 0),
        (0, 0, 170),
        (170, 0, 170),
        (0, 170, 170),
        (170, 170, 170),
        (85, 85, 85),
        (255, 85, 85),
        (85, 255, 85),
        (255, 255, 85),
        (85, 85, 255),
        (255, 85, 255),
        (85, 255, 255),
        (255, 255, 255),
    ),
    # IBM Color Graphics Adapter -- earlier, more saturated palette.
    "cga": (
        (0, 0, 0),
        (170, 0, 0),
        (0, 170, 0),
        (170, 170, 0),
        (0, 0, 170),
        (170, 0, 170),
        (0, 170, 170),
        (170, 170, 170),
        (85, 85, 85),
        (255, 85, 85),
        (85, 255, 85),
        (255, 255, 85),
        (85, 85, 255),
        (255, 85, 255),
        (85, 255, 255),
        (255, 255, 255),
    ),
    # VGA / DOS standard palette -- the most common DOS palette, very close
    # to EGA but with a brighter dark yellow.
    "vga": (
        (0, 0, 0),
        (170, 0, 0),
        (0, 170, 0),
        (170, 85, 0),
        (0, 0, 170),
        (170, 0, 170),
        (0, 170, 170),
        (170, 170, 170),
        (85, 85, 85),
        (255, 85, 85),
        (85, 255, 85),
        (255, 255, 85),
        (85, 85, 255),
        (255, 85, 255),
        (85, 255, 255),
        (255, 255, 255),
    ),
    # Amiga Workbench 1.x palette -- warmer tones characteristic of the
    # Commodore Amiga.
    "amiga": (
        (0, 0, 0),
        (170, 0, 0),
        (0, 170, 0),
        (170, 170, 0),
        (0, 0, 170),
        (170, 0, 170),
        (0, 170, 170),
        (187, 187, 187),
        (85, 85, 85),
        (255, 85, 85),
        (85, 255, 85),
        (255, 255, 85),
        (85, 85, 255),
        (255, 85, 255),
        (85, 255, 255),
        (255, 255, 255),
    ),
    # xterm default palette -- the standard xterm color table.
    "xterm": (
        (0, 0, 0),
        (205, 0, 0),
        (0, 205, 0),
        (205, 205, 0),
        (0, 0, 238),
        (205, 0, 205),
        (0, 205, 205),
        (229, 229, 229),
        (127, 127, 127),
        (255, 0, 0),
        (0, 255, 0),
        (255, 255, 0),
        (92, 92, 255),
        (255, 0, 255),
        (0, 255, 255),
        (255, 255, 255),
    ),
}

# Detect potentially incomplete escape sequence at end of a chunk.
_TRAILING_ESC = re.compile(r"\x1b(\[[\d;:]*)?$")


class ColorConfig(NamedTuple):
    """
    Configuration for ANSI color palette translation.

    :param palette_name: Name of the hardware palette to use (key in PALETTES).
    :param brightness: Brightness scale factor [0.0..1.0], where 1.0 is original.
    :param contrast: Contrast scale factor [0.0..1.0], where 1.0 is original.
    :param background_color: Forced background RGB as (R, G, B) tuple.
    :param reverse_video: When True, swap fg/bg for light-background terminals.
    """

    palette_name: str = "ega"
    brightness: float = 0.9
    contrast: float = 0.8
    background_color: Tuple[int, int, int] = (16, 16, 16)
    reverse_video: bool = False


def _sgr_code_to_palette_index(code: int) -> Optional[int]:
    """
    Map a basic SGR color code to a palette index (0-15).

    :param code: SGR parameter value (30-37, 40-47, 90-97, or 100-107).
    :returns: Palette index 0-15, or None if not a basic color code.
    """
    if 30 <= code <= 37:
        return code - 30
    if 40 <= code <= 47:
        return code - 40
    if 90 <= code <= 97:
        return code - 90 + 8
    if 100 <= code <= 107:
        return code - 100 + 8
    return None


def _is_foreground_code(code: int) -> bool:
    """
    Return True if *code* is a foreground color SGR parameter.

    :param code: SGR parameter value.
    :returns: True for foreground codes (30-37, 90-97).
    """
    return (30 <= code <= 37) or (90 <= code <= 97)


def _adjust_color(
    r: int, g: int, b: int, brightness: float, contrast: float
) -> Tuple[int, int, int]:
    """
    Apply brightness and contrast scaling to an RGB color.

    Brightness scales linearly toward black (0.0 = black, 1.0 = original).
    Contrast scales linearly toward mid-gray (0.0 = flat gray, 1.0 = original).
    Result is clamped to 0-255.

    :param r: Red channel (0-255).
    :param g: Green channel (0-255).
    :param b: Blue channel (0-255).
    :param brightness: Brightness factor [0.0..1.0].
    :param contrast: Contrast factor [0.0..1.0].
    :returns: Adjusted (R, G, B) tuple.
    """
    mid = 127.5
    r_f = mid + (r * brightness - mid) * contrast
    g_f = mid + (g * brightness - mid) * contrast
    b_f = mid + (b * brightness - mid) * contrast
    return (
        max(0, min(255, int(r_f + 0.5))),
        max(0, min(255, int(g_f + 0.5))),
        max(0, min(255, int(b_f + 0.5))),
    )


class ColorFilter:
    """
    Stateful ANSI color palette translation filter.

    Translates basic 16-color ANSI SGR codes to 24-bit RGB equivalents from a named hardware
    palette, with brightness/contrast adjustment and background color enforcement.

    The filter is designed to process chunked text (as received from a telnet connection) and
    correctly handles escape sequences split across chunk boundaries.

    :param config: Color configuration parameters.
    """

    def __init__(self, config: ColorConfig) -> None:
        """Initialize with the given color configuration."""
        self._config = config
        palette = PALETTES[config.palette_name]
        self._adjusted: List[Tuple[int, int, int]] = [
            _adjust_color(r, g, b, config.brightness, config.contrast) for r, g, b in palette
        ]
        bg = config.background_color
        if config.reverse_video:
            bg = (255 - bg[0], 255 - bg[1], 255 - bg[2])
        self._bg_sgr = f"\x1b[48;2;{bg[0]};{bg[1]};{bg[2]}m"
        self._buffer = ""
        self._initial = True
        self._bold = False

    def filter(self, text: str) -> str:
        """
        Transform SGR sequences in *text* using the configured palette.

        Handles chunked input by buffering incomplete trailing escape sequences across calls.  On
        the very first non-empty output, the configured background color is injected.

        :param text: Input text, possibly containing ANSI escape sequences.
        :returns: Text with basic colors replaced by 24-bit RGB equivalents.
        """
        if self._buffer:
            text = self._buffer + text
            self._buffer = ""

        match = _TRAILING_ESC.search(text)
        if match:
            self._buffer = match.group()
            text = text[: match.start()]

        if not text:
            return ""

        result = _SGR_PATTERN.sub(self._replace_sgr, text)

        if self._initial:
            self._initial = False
            result = self._bg_sgr + result
        return result

    # pylint: disable-next=too-complex,too-many-branches,too-many-statements
    def _replace_sgr(self, match: Match[str]) -> str:  # noqa: C901
        r"""
        Regex replacement callback for a single SGR sequence.

        Tracks bold state across calls so that ``\x1b[1;30m`` (bold + black) uses the bright palette
        entry (index 8) instead of pure black.  This preserves the traditional "bold as bright"
        rendering that legacy systems rely on, which would otherwise be lost when converting to
        24-bit RGB (terminals do not brighten true-color values for bold).
        """
        params_str = match.group(1)

        # Empty params or bare "0" → reset
        if not params_str:
            self._bold = False
            return f"\x1b[0m{self._bg_sgr}"

        # Colon-separated extended colors (ITU T.416) — pass through unchanged
        if ":" in params_str:
            return match.group()

        parts = params_str.split(";")
        output_parts: List[str] = []
        i = 0
        has_reset = False

        # Pre-scan: check if bold (1) appears in this sequence so that a
        # color code *before* the bold in the same sequence still gets the
        # bright treatment, e.g. \x1b[31;1m should brighten red.
        seq_sets_bold = False
        for part in parts:
            try:
                val = int(part) if part else 0
            except ValueError:
                continue
            if val == 1:
                seq_sets_bold = True
                break

        # Effective bold for color lookups in this sequence
        bold = self._bold or seq_sets_bold

        while i < len(parts):
            try:
                p = int(parts[i]) if parts[i] else 0
            except ValueError:
                output_parts.append(parts[i])
                i += 1
                continue

            if p == 0:
                has_reset = True
                bold = False
                output_parts.append("0")
                i += 1
                continue

            # Track bold state
            if p == 1:
                output_parts.append("1")
                i += 1
                continue
            if p == 22:
                bold = False
                output_parts.append("22")
                i += 1
                continue

            # Extended color — pass through 38;5;N or 38;2;R;G;B verbatim
            if p in (38, 48):
                start_i = i
                i += 1
                if i < len(parts):
                    try:
                        mode = int(parts[i]) if parts[i] else 0
                    except ValueError:
                        mode = 0
                    i += 1
                    if mode == 5 and i < len(parts):
                        i += 1
                    elif mode == 2 and i + 2 < len(parts):
                        i += 3
                output_parts.extend(parts[start_i:i])
                continue

            # Default fg/bg — pass through
            if p in (39, 49):
                output_parts.append(str(p))
                i += 1
                continue

            idx = _sgr_code_to_palette_index(p)
            if idx is not None:
                is_fg = _is_foreground_code(p)
                # Bold-as-bright: promote normal fg 30-37 to bright 8-15
                if is_fg and bold and 30 <= p <= 37:
                    idx += 8
                r, g, b = self._adjusted[idx]
                if self._config.reverse_video:
                    is_fg = not is_fg
                if is_fg:
                    output_parts.extend(["38", "2", str(r), str(g), str(b)])
                else:
                    output_parts.extend(["48", "2", str(r), str(g), str(b)])
            else:
                output_parts.append(str(p))
            i += 1

        # Update persistent bold state for subsequent sequences
        self._bold = bold

        result = f"\x1b[{';'.join(output_parts)}m" if output_parts else ""
        if has_reset:
            result += self._bg_sgr
        return result

    def flush(self) -> str:
        """
        Flush any buffered partial escape sequence.

        Call this when the stream closes to emit any remaining buffered bytes.

        :returns: Buffered content (may be an incomplete escape sequence).
        """
        result = self._buffer
        self._buffer = ""
        return result
