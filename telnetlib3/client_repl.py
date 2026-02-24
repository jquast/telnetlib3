"""REPL and TUI components for linemode telnet client sessions."""

# pylint: disable=too-complex

# std imports
import os
import sys
import time
import asyncio
import logging
import collections
from typing import TYPE_CHECKING, Any, List, Tuple, Union, Callable, Optional

# local
from .stream_reader import TelnetReader, TelnetReaderUnicode
from .stream_writer import TelnetWriter, TelnetWriterUnicode

if TYPE_CHECKING:
    import blessed
    import blessed.keyboard
    import blessed.line_editor
    from . import client_shell
    from .autoreply import AutoreplyEngine
    from .macros import Macro

PASSWORD_CHAR = "\u25cf"

log = logging.getLogger(__name__)


def _load_history(history: "blessed.line_editor.LineHistory", path: str) -> None:
    """Populate *history* entries from a newline-delimited file.

    :param history: :class:`~blessed.line_editor.LineHistory` instance.
    :param path: Path to the history file.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.rstrip("\n")
                if line:
                    history.entries.append(line)
    except OSError:
        pass


def _save_history_entry(line: str, path: str) -> None:
    """Append a single history *line* to the file at *path*.

    :param line: The line to persist.
    :param path: Path to the history file (created if absent).
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(line + "\n")

# Number of bottom rows reserved for the input line + toolbar.
_RESERVE_INITIAL = 1
_RESERVE_WITH_TOOLBAR = 2

# Lazy blessed Terminal singleton — created on first use.
# Both blessed.Terminal and client_shell.Terminal are named "Terminal"
# in their respective modules; ``blessed_term`` and ``tty_shell`` are
# used throughout to distinguish the two when both are in scope.
blessed_term: Optional["blessed.Terminal"] = None


def _get_term() -> "blessed.Terminal":
    """Return the module-level blessed Terminal singleton."""
    global blessed_term  # noqa: PLW0603  # pylint: disable=global-statement
    if blessed_term is None:
        import blessed  # pylint: disable=import-outside-toplevel

        blessed_term = blessed.Terminal(force_styling=True)
    return blessed_term


def _terminal_cleanup() -> str:
    """Reset SGR, cursor, alt-screen, mouse tracking, and bracketed paste."""
    t = _get_term()
    return (
        t.normal
        + t.cursor_normal
        + t.exit_fullscreen
        + "\x1b[?1000l"  # xterm -- disable basic mouse
        + "\x1b[?1002l"  # xterm -- disable button-event mouse
        + "\x1b[?1003l"  # xterm -- disable any-event mouse
        + "\x1b[?1006l"  # xterm -- disable SGR mouse ext
        + "\x1b[?2004l"  # xterm -- disable bracketed paste
    )


# SGR style dicts keyed to LineEditor constructor / attribute names.
# Built lazily via _make_styles() so blessed color_rgb auto-downgrades
# on terminals that lack truecolor support.
_STYLE_NORMAL: dict[str, str] = {}
_STYLE_AUTOREPLY: dict[str, str] = {}


def _make_styles() -> None:
    """Populate style dicts using blessed color API."""
    global _STYLE_NORMAL, _STYLE_AUTOREPLY  # noqa: PLW0603  # pylint: disable=global-statement
    t = _get_term()
    _STYLE_NORMAL = {
        "text_sgr": t.color_rgb(255, 239, 213),
        "suggestion_sgr": t.color_rgb(0, 0, 0),
        "bg_sgr": t.on_color_rgb(26, 0, 0),
        "ellipsis_sgr": t.color_rgb(190, 190, 190),
        "cursor_seq": "\x1b[5 q",  # DECSCUSR -- blinking bar
    }
    _STYLE_AUTOREPLY = {
        "text_sgr": t.color_rgb(184, 134, 11),
        "suggestion_sgr": t.color_rgb(80, 60, 0),
        "bg_sgr": t.on_color_rgb(26, 18, 0),
        "ellipsis_sgr": t.color_rgb(80, 60, 0),
        "cursor_seq": "\x1b[5 q",  # DECSCUSR -- blinking bar
    }


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
_DEFAULT_CURSOR_STYLE = "blinking_bar"

# Default ellipsis for overflow indicator (used as fallback).
_ELLIPSIS = "\u2026"

# Maximum bytes retained in the output replay ring buffer for Ctrl-L repaint.
_REPLAY_BUFFER_MAX = 65536

# Buffer for MUD data received while a TUI editor subprocess is running.
# The asyncio _read_server loop continues receiving MUD data during editor
# sessions; writing that data to the terminal fills the PTY buffer and
# deadlocks the editor's Textual WriterThread.  Data is queued here and
# replayed when the editor exits.
_editor_active = False  # pylint: disable=invalid-name
_editor_buffer: list[bytes] = []

__all__ = ("ScrollRegion", "repl_event_loop", "_split_incomplete_esc")


def _split_incomplete_esc(data: bytes) -> tuple[bytes, bytes]:
    """
    Split *data* into (complete, holdback) at a trailing incomplete escape.

    If *data* ends mid-escape-sequence the incomplete tail is returned as
    *holdback* so the caller can buffer it until more bytes arrive.
    Handles CSI (``ESC [``) with arbitrarily long parameter/intermediate
    bytes, OSC (``ESC ]``), DCS (``ESC P``), and plain two-byte ``ESC X``
    sequences.

    :returns: ``(flush_now, hold_back)`` -- concatenation equals *data*.
    """
    n = len(data)
    if n == 0:
        return data, b""

    idx = data.rfind(0x1B)
    if idx == -1:
        return data, b""

    pos = idx + 1

    if pos >= n:
        # Lone ESC at the very end.
        return data[:idx], data[idx:]

    nxt = data[pos]

    if nxt == 0x5B:  # '[' -- CSI
        pos += 1
        # Parameter bytes 0x30-0x3F, intermediate bytes 0x20-0x2F.
        while pos < n and 0x20 <= data[pos] <= 0x3F:
            pos += 1
        # Final byte 0x40-0x7E completes the sequence.
        if pos < n and 0x40 <= data[pos] <= 0x7E:
            return data, b""
        return data[:idx], data[idx:]

    if nxt in (0x5D, 0x50):  # ']' OSC  /  'P' DCS
        # Terminated by BEL (0x07) or ST (ESC \).
        while pos < n:
            if data[pos] == 0x07:
                return data, b""
            if data[pos] == 0x1B and pos + 1 < n and data[pos + 1] == 0x5C:
                return data, b""
            pos += 1
        return data[:idx], data[idx:]

    if 0x40 <= nxt <= 0x5F:
        # Two-byte escape -- already complete (Fe sequence).
        return data, b""

    # Unknown sequence type; assume complete.
    return data, b""


class OutputRingBuffer:
    """Rolling buffer of raw display output for Ctrl-L screen repaint."""

    def __init__(self, max_bytes: int = _REPLAY_BUFFER_MAX) -> None:
        self._chunks: collections.deque[bytes] = collections.deque()
        self._total = 0
        self._max = max_bytes

    def append(self, data: bytes) -> None:
        """Append a chunk, discarding oldest data when over capacity."""
        self._chunks.append(data)
        self._total += len(data)
        while self._total > self._max and self._chunks:
            removed = self._chunks.popleft()
            self._total -= len(removed)

    def replay(self) -> bytes:
        """Return all buffered output concatenated."""
        return b"".join(self._chunks)


def _restore_after_subprocess(
    replay_buf: Optional["OutputRingBuffer"], reserve: int = _RESERVE_WITH_TOOLBAR
) -> None:
    """
    Restore terminal state after a TUI subprocess exits.

    Restores stdin blocking mode, resets SGR/mouse/alt-screen via
    :func:`_terminal_cleanup`, clears the screen, re-establishes the
    DECSTBM scroll region, replays the output ring buffer, and clears
    the reserved input rows.

    :param replay_buf: Ring buffer to replay, or ``None`` to skip replay.
    :param reserve: Number of bottom rows reserved for the input area.
    """
    try:
        os.set_blocking(sys.stdin.fileno(), True)
    except OSError:
        pass
    t = _get_term()
    sys.stdout.write(_terminal_cleanup())
    try:
        _tsize = os.get_terminal_size()
    except OSError:
        _tsize = os.terminal_size((80, 24))
    scroll_bottom = max(0, _tsize.lines - reserve - 2)
    sys.stdout.write(t.clear + t.home)
    sys.stdout.write(t.change_scroll_region(0, scroll_bottom))
    sys.stdout.write(t.move_yx(0, 0))
    if replay_buf is not None:
        data = replay_buf.replay()
        if data:
            sys.stdout.write(data.decode("utf-8", errors="replace"))
    sys.stdout.write(t.save)
    dmz = scroll_bottom + 1
    _input_row = _tsize.lines - reserve
    if dmz < _input_row:
        sys.stdout.write(t.move_yx(dmz, 0) + t.clear_eol + "\u2500" * _tsize.columns)
    for _r in range(_input_row, _tsize.lines):
        sys.stdout.write(t.move_yx(_r, 0) + t.clear_eol)
    sys.stdout.flush()


def _hsv_to_rgb(h: float, s: float, v: float) -> Tuple[int, int, int]:
    """Convert HSV (h in [0,360), s/v in [0,1]) to (r, g, b) in [0,255]."""
    import colorsys  # pylint: disable=import-outside-toplevel

    r, g, b = colorsys.hsv_to_rgb(h / 360.0, s, v)
    return (int(r * 255), int(g * 255), int(b * 255))


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
        # Stay red below 33%, then red->green over 33%-100%.
        hue = max(0.0, (fraction - 0.33) / 0.67) * 120.0
    elif kind == "xp":
        # Purple (270) -> cyan (180) as XP fills.
        hue = 270.0 - fraction * 90.0
    elif kind == "wander":
        # Cyan (180) -> yellow (60) as autowander progresses.
        hue = 180.0 - fraction * 120.0
    elif kind == "discover":
        # Green (120) -> magenta (300) as autodiscover progresses.
        hue = 120.0 + fraction * 180.0
    else:
        # Stay golden yellow below 33%, then golden-yellow->blue over 33%-100%.
        # hue 45=golden yellow, hue 240=blue.
        t = max(0.0, (fraction - 0.33) / 0.67)
        hue = 45.0 + t * 195.0
    r, g, b = _hsv_to_rgb(hue, 0.7, 0.8)
    return f"#{r:02x}{g:02x}{b:02x}"


def _wcswidth(text: str) -> int:
    """Return display width of *text*, handling wide chars."""
    from wcwidth import wcswidth  # pylint: disable=import-outside-toplevel

    w = wcswidth(text)
    return w if w >= 0 else len(text)


# Width of the inner progress bar (between the brackets).
_BAR_WIDTH = 16


_BAR_CAP_LEFT = "\U0001FB2B"   # 🬫 Block Sextant-2346
_BAR_CAP_RIGHT = "\U0001FB1B"  # 🬛 Block Sextant-1345


def _segmented(text: str) -> str:
    """Replace ASCII digits 0-9 with segmented digit glyphs U+1FBF0..U+1FBF9."""
    return text.translate(str.maketrans("0123456789", "\U0001FBF0\U0001FBF1"
                                        "\U0001FBF2\U0001FBF3\U0001FBF4\U0001FBF5"
                                        "\U0001FBF6\U0001FBF7\U0001FBF8\U0001FBF9"))


def _sgr_fg(hexcolor: str) -> str:
    """SGR foreground from ``#rrggbb`` hex via blessed (auto-downconverts)."""
    return _get_term().color_hex(hexcolor)


def _sgr_bg(hexcolor: str) -> str:
    """SGR background from ``#rrggbb`` hex via blessed (auto-downconverts)."""
    return _get_term().on_color_hex(hexcolor)


def _vital_bar(
    current: Any, maximum: Any, width: int, kind: str, flash: bool = False
) -> "List[Tuple[str, str]]":
    """
    Build a labelled progress-bar with sextant bookends and overlaid text.

    The label (e.g. ``513/514 100% HP``) is rendered *on top of* the bar
    using segmented digit glyphs.  Sextant block characters bookend the
    bar for a rounded appearance.

    :param flash: When ``True``, render the bar in white for attention.
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
    if flash:
        fill_bg = "#ffffff"
        empty_bg = "#888888"
        filled_sgr = _sgr_fg("#101010") + _sgr_bg("#ffffff")
        empty_sgr = _sgr_fg("#aaaaaa") + _sgr_bg("#888888")
    else:
        fill_bg = bar_color
        empty_bg = "#2a2a2a"
        filled_sgr = _sgr_fg("#101010") + _sgr_bg(bar_color)
        empty_sgr = _sgr_fg("#666666") + _sgr_bg("#2a2a2a")

    suffix = {"hp": " HP", "mp": " MP", "xp": " XP", "wander": " AW"}.get(kind, "")
    if mx > 0:
        label = _segmented(f"{_fmt_value(cur)}/{_fmt_value(mx)} {pct}%{suffix}")
    else:
        label = _segmented(f"{_fmt_value(cur)}{suffix}")

    lpad = max(0, width - len(label) - 1)

    bg = list(" " * width)
    for i, ch in enumerate(label, start=lpad):
        if i < width:
            bg[i] = ch
    bar_text = "".join(bg[:width])

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


def _repaint_screen(
    replay_buf: Optional[OutputRingBuffer], scroll: Optional["ScrollRegion"] = None
) -> None:
    """
    Clear screen and replay recent output from the ring buffer.

    Re-establishes the DECSTBM scroll region and replays buffered output so recent MUD text
    reappears with colors intact.
    """
    # pylint: disable-next=protected-access
    reserve = scroll._reserve if scroll is not None else _RESERVE_WITH_TOOLBAR
    try:
        _tsize = os.get_terminal_size()
    except OSError:
        return
    if scroll is not None:
        scroll.update_size(_tsize.lines, _tsize.columns)
    fd = sys.stdout.fileno()
    was_blocking = os.get_blocking(fd)
    os.set_blocking(fd, True)
    try:
        t = _get_term()
        scroll_bottom = max(0, _tsize.lines - reserve - 2)
        sys.stdout.write(t.clear + t.home)
        sys.stdout.write(t.change_scroll_region(0, scroll_bottom))
        sys.stdout.write(t.move_yx(0, 0))
        if replay_buf is not None:
            data = replay_buf.replay()
            if data:
                sys.stdout.write(data.decode("utf-8", errors="replace"))
        sys.stdout.write(t.save)
        dmz = scroll_bottom + 1
        _input_row = _tsize.lines - reserve
        if dmz < _input_row:
            sys.stdout.write(t.move_yx(dmz, 0) + t.clear_eol + "\u2500" * _tsize.columns)
        for _r in range(_input_row, _tsize.lines):
            sys.stdout.write(t.move_yx(_r, 0) + t.clear_eol)
        sys.stdout.write(t.move_yx(_input_row, 0))
        sys.stdout.flush()
    finally:
        os.set_blocking(fd, was_blocking)


def _confirm_dialog(
    title: str, body: str, warning: str = "", replay_buf: Optional["OutputRingBuffer"] = None
) -> tuple[bool, bool]:
    """
    Show a Textual confirmation dialog in a subprocess.

    Launches :func:`telnetlib3.client_tui.confirm_dialog_main` as a
    subprocess, reads the result from a temporary file, and restores
    terminal state on return.

    :param title: Dialog title.
    :param body: Body text.
    :param warning: Optional warning text displayed in red.
    :param replay_buf: Optional replay buffer for screen repaint.
    :returns: ``(confirmed, dont_ask_again)`` tuple.
    """
    import json as _json  # pylint: disable=import-outside-toplevel
    import tempfile  # pylint: disable=import-outside-toplevel
    import subprocess  # pylint: disable=import-outside-toplevel

    fd, result_path = tempfile.mkstemp(suffix=".json", prefix="confirm-")
    os.close(fd)

    cmd = [
        sys.executable, "-c",
        "import sys; from telnetlib3.client_tui import confirm_dialog_main; "
        "confirm_dialog_main(sys.argv[1], sys.argv[2],"
        " warning=sys.argv[3], result_file=sys.argv[4])",
        title, body, warning or "", result_path,
    ]

    global _editor_active  # noqa: PLW0603  # pylint: disable=global-statement
    t = _get_term()
    sys.stdout.write(t.change_scroll_region(0, t.height - 1))
    sys.stdout.flush()
    sys.stderr.flush()
    sys.__stderr__.flush()
    _editor_active = True
    try:
        subprocess.run(cmd, check=False)
    except FileNotFoundError:
        pass
    finally:
        _editor_active = False
        _restore_after_subprocess(replay_buf)

    confirmed = False
    dont_ask = False
    try:
        with open(result_path, "r", encoding="utf-8") as f:
            data = _json.load(f)
        confirmed = bool(data.get("confirmed", False))
        dont_ask = bool(data.get("dont_ask", False))
    except (OSError, ValueError):
        pass
    finally:
        try:
            os.unlink(result_path)
        except OSError:
            pass

    return confirmed, dont_ask


def _show_help(macro_defs: "Any" = None, replay_buf: Optional[OutputRingBuffer] = None) -> None:
    """
    Display keybinding help on the alternate screen buffer.

    :param macro_defs: Optional list of macro definitions to display.
    :param replay_buf: Optional replay buffer for screen repaint on return.
    """
    global _editor_active  # noqa: PLW0603  # pylint: disable=global-statement
    t = _get_term()
    sys.stdout.write(t.enter_fullscreen)
    sys.stdout.write(t.home + t.clear)
    lines = [
        "",
        "  telnetlib3 \u2014 Keybindings",
        "",
        "  F1          This help screen",
        "  F4          Autodiscover (explore unvisited exits)",
        "  F5          Wander mode (visit same-named rooms)",
        "  F7          Browse rooms / fast travel",
        "  F8          Edit macros (TUI editor)",
        "  F9          Edit autoreplies (TUI editor)",
        "  Shift+F9    Toggle autoreplies on/off",
        "  Ctrl+]      Disconnect",
        "",
        "  Command processing:",
        "  ;            Separator (e.g. get all;drop sword)",
        "  3n;2e        Repeat prefix (expands to n;n;n;e;e)",
        "",
    ]
    if macro_defs:
        lines.append("  User macros:")
        for m in macro_defs:
            key = m.key
            text = getattr(m, "text", "")
            display = text.replace("\r\n", "<CR>").replace("\r", "<CR>")
            if len(display) > 40:
                display = display[:37] + "..."
            lines.append(f"  {key:<12}{display}")
        lines.append("")
    lines.append("  Press any key to return.")
    lines.append("")
    sys.stdout.write("\r\n".join(lines))
    sys.stdout.flush()

    import select  # pylint: disable=import-outside-toplevel

    _editor_active = True
    try:
        with t.raw():
            os.set_blocking(sys.stdin.fileno(), True)
            select.select([sys.stdin.fileno()], [], [])
            os.read(sys.stdin.fileno(), 1)
    finally:
        _editor_active = False

    sys.stdout.write(t.exit_fullscreen)
    sys.stdout.flush()
    _restore_after_subprocess(replay_buf)


def _launch_tui_editor(
    editor_type: str,
    writer: Union[TelnetWriter, TelnetWriterUnicode],
    replay_buf: Optional[OutputRingBuffer] = None,
) -> None:
    """
    Launch a TUI editor for macros or autoreplies in a subprocess.

    :param editor_type: ``"macros"`` or ``"autoreplies"``.
    :param writer: Telnet writer with file path and definition attributes.
    :param replay_buf: Optional replay buffer for screen repaint on return.
    """
    import subprocess  # pylint: disable=import-outside-toplevel

    from ._paths import CONFIG_DIR as _config_dir  # pylint: disable=import-outside-toplevel

    _session_key = getattr(writer, "_session_key", "")

    if editor_type == "macros":
        path = getattr(writer, "_macros_file", None) or os.path.join(_config_dir, "macros.json")
        # pylint: disable=import-outside-toplevel
        from .rooms import rooms_path as _rooms_path_fn
        from .rooms import current_room_path as _current_room_path_fn

        # pylint: enable=import-outside-toplevel

        _rp = getattr(writer, "_rooms_file", None) or _rooms_path_fn(_session_key)
        _crp = getattr(writer, "_current_room_file", None) or _current_room_path_fn(_session_key)
        cmd = [
            sys.executable, "-c",
            "import sys; from telnetlib3.client_tui import edit_macros_main; "
            "edit_macros_main(sys.argv[1], sys.argv[2],"
            " rooms_file=sys.argv[3], current_room_file=sys.argv[4])",
            path, _session_key, _rp, _crp,
        ]
    else:
        path = getattr(writer, "_autoreplies_file", None) or os.path.join(
            _config_dir, "autoreplies.json"
        )
        engine = getattr(writer, "_autoreply_engine", None)
        _select = getattr(engine, "last_matched_pattern", "") if engine else ""
        cmd = [
            sys.executable, "-c",
            "import sys; from telnetlib3.client_tui import edit_autoreplies_main; "
            "edit_autoreplies_main(sys.argv[1], sys.argv[2],"
            " select_pattern=sys.argv[3])",
            path, _session_key, _select,
        ]

    log = logging.getLogger(__name__)

    global _editor_active  # noqa: PLW0603  # pylint: disable=global-statement
    t = _get_term()
    sys.stdout.write(t.change_scroll_region(0, t.height - 1))
    sys.stdout.flush()
    sys.stderr.flush()
    sys.__stderr__.flush()
    _editor_active = True
    try:
        subprocess.run(cmd, check=False)
    except FileNotFoundError:
        log.warning("could not launch TUI editor subprocess")
    finally:
        _editor_active = False
        _restore_after_subprocess(replay_buf)

    if editor_type == "macros":
        _reload_macros(writer, path, _session_key, log)
    else:
        _reload_autoreplies(writer, path, _session_key, log)


def _reload_macros(
    writer: Union[TelnetWriter, TelnetWriterUnicode],
    path: str,
    session_key: str,
    log: logging.Logger,
) -> None:
    """Reload macro definitions from disk and update dispatch."""
    if not os.path.exists(path):
        return
    from .macros import load_macros  # pylint: disable=import-outside-toplevel

    try:
        new_defs = load_macros(path, session_key)
        writer._macro_defs = new_defs  # pylint: disable=protected-access
        writer._macros_file = path  # pylint: disable=protected-access
        # Rebuild macro dispatch if available.
        dispatch = getattr(writer, "_key_dispatch", None)
        if dispatch is not None:
            dispatch.set_macros(new_defs, writer, log)
        log.info("reloaded %d macros from %s", len(new_defs), path)
    except ValueError as exc:
        log.warning("failed to reload macros: %s", exc)


def _reload_autoreplies(
    writer: Union[TelnetWriter, TelnetWriterUnicode],
    path: str,
    session_key: str,
    log: logging.Logger,
) -> None:
    """Reload autoreply rules from disk after editing."""
    if not os.path.exists(path):
        return
    from .autoreply import load_autoreplies  # pylint: disable=import-outside-toplevel

    try:
        # pylint: disable=protected-access
        writer._autoreply_rules = load_autoreplies(path, session_key)
        writer._autoreplies_file = path
        n_rules = len(writer._autoreply_rules)
        # pylint: enable=protected-access
        log.info("reloaded %d autoreplies from %s", n_rules, path)
    except ValueError as exc:
        log.warning("failed to reload autoreplies: %s", exc)


def _launch_room_browser(
    writer: Union[TelnetWriter, TelnetWriterUnicode],
    replay_buf: Optional["OutputRingBuffer"] = None,
) -> None:
    """
    Launch the room browser TUI in a subprocess.

    On return, check for a fast travel file and queue movement commands.

    :param writer: Telnet writer with session attributes.
    :param replay_buf: Optional replay buffer for screen repaint on return.
    """
    import subprocess  # pylint: disable=import-outside-toplevel

    _session_key = getattr(writer, "_session_key", "")
    if not _session_key:
        return

    # pylint: disable=import-outside-toplevel
    from .rooms import rooms_path as _rooms_path_fn
    from .rooms import fasttravel_path as _fasttravel_path_fn
    from .rooms import read_fasttravel
    from .rooms import current_room_path as _current_room_path_fn

    # pylint: enable=import-outside-toplevel

    _rp = getattr(writer, "_rooms_file", None) or _rooms_path_fn(_session_key)
    _crp = getattr(writer, "_current_room_file", None) or _current_room_path_fn(_session_key)
    _ftp = _fasttravel_path_fn(_session_key)

    cmd = [
        sys.executable, "-c",
        "import sys; from telnetlib3.client_tui import edit_rooms_main; "
        "edit_rooms_main(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4])",
        _rp, _session_key, _crp, _ftp,
    ]

    log = logging.getLogger(__name__)

    global _editor_active  # noqa: PLW0603  # pylint: disable=global-statement
    t = _get_term()
    sys.stdout.write(t.change_scroll_region(0, t.height - 1))
    sys.stdout.flush()
    sys.stderr.flush()
    sys.__stderr__.flush()
    _editor_active = True
    try:
        subprocess.run(cmd, check=False)
    except FileNotFoundError:
        log.warning("could not launch room browser subprocess")
    finally:
        _editor_active = False
        _restore_after_subprocess(replay_buf)

    room_graph = getattr(writer, "_room_graph", None)
    if room_graph is not None:
        room_graph._load_adjacency()  # pylint: disable=protected-access

    steps, slow = read_fasttravel(_ftp)
    if steps:
        log.debug("fast travel: scheduling %d steps (slow=%s)", len(steps), slow)
        asyncio.ensure_future(_fast_travel(steps, writer, log, slow=slow))


async def _fast_travel(
    steps: list[tuple[str, str]],
    writer: Union[TelnetWriter, TelnetWriterUnicode],
    log: logging.Logger,
    slow: bool = False,
    destination: str = "",
    correct_names: bool = True,
) -> None:
    """
    Execute fast travel by sending movement commands with GA/EOR pacing.

    Uses the same ``_wait_for_prompt`` / ``_echo_command`` functions that
    the autoreply engine and manual input use, so commands are paced by
    the server's GA/EOR prompt signal and echoed visibly.

    In fast mode (default), exclusive autoreplies are suppressed.
    Non-exclusive autoreplies still fire; travel pauses until they
    complete and then waits for a clean EOR with no match before
    sending the next direction.

    In slow mode, all autoreplies fire including exclusive ones.

    When the player arrives at an unexpected room, instead of aborting
    the function re-pathfinds from the actual position to *destination*
    and continues with the new route (up to 3 re-routes).

    :param steps: List of (direction, expected_room_num) pairs.
    :param writer: Telnet writer for sending commands.
    :param log: Logger.
    :param slow: If ``True``, allow exclusive autoreplies.
    :param destination: Final target room ID for re-pathfinding on detour.
    :param correct_names: If ``True`` (default), rewrite graph edges when
        arriving at a same-name room with a different ID.  Set to ``False``
        for autowander where distinct room IDs must be preserved.
    """
    # pylint: disable=too-many-locals,too-many-branches,too-many-statements,too-many-nested-blocks
    wait_fn = getattr(writer, "_wait_for_prompt", None)
    echo_fn = getattr(writer, "_echo_command", None)

    from .autoreply import AutoreplyEngine  # pylint: disable=import-outside-toplevel

    def _get_engine() -> Optional["AutoreplyEngine"]:
        """Find the active autoreply engine, if any."""
        # Stashed by _refresh_autoreply_engine via repl._autoreply_engine
        # which is the same object as the one feeding on server output.
        return getattr(writer, "_autoreply_engine", None)

    engine = _get_engine()
    if engine is not None and not slow:
        engine.suppress_exclusive = True

    mode = "slow travel" if slow else "fast travel"

    from .rooms import RoomGraph  # pylint: disable=import-outside-toplevel

    def _get_graph() -> Optional[RoomGraph]:
        return getattr(writer, "_room_graph", None)

    def _room_name(num: str) -> str:
        """Look up a human-readable room name from the writer's graph."""
        graph = _get_graph()
        if graph is not None:
            room = graph.rooms.get(num)
            if room is not None:
                return f"{room.name} ({num[:8]}...)"
        return num

    def _names_match(expected_num: str, actual_num: str) -> bool:
        """Check whether two room IDs refer to rooms with the same name."""
        graph = _get_graph()
        if graph is None:
            return False
        expected = graph.rooms.get(expected_num)
        actual = graph.rooms.get(actual_num)
        if expected is None or actual is None:
            return False
        return expected.name == actual.name and bool(expected.name)

    def _correct_edge(
        prev_num: str,
        direction: str,
        old_target: str,
        new_target: str,
        remaining_steps: list[tuple[str, str]],
    ) -> None:
        """Update the graph edge and rewrite remaining steps in-place."""
        graph = _get_graph()
        if graph is not None:
            prev = graph.rooms.get(prev_num)
            if prev is not None and prev.exits.get(direction) == old_target:
                prev.exits[direction] = new_target
                log.info(
                    "%s: corrected exit %s of %s: %s -> %s",
                    mode,
                    direction,
                    prev_num[:8],
                    old_target[:8],
                    new_target[:8],
                )
        for j, (d, r) in enumerate(remaining_steps):
            if r == old_target:
                remaining_steps[j] = (d, new_target)

    room_changed = getattr(writer, "_room_changed", None)
    _max_retries = 3
    _max_reroutes = 3

    if not destination and steps:
        destination = steps[-1][1]

    blocked_exits: list[tuple[str, str, str]] = []
    try:
        step_idx = 0
        reroute_count = 0
        while step_idx < len(steps):
            direction, expected_room = steps[step_idx]
            prev_room = getattr(writer, "_current_room_num", "")

            for attempt in range(_max_retries + 1):
                # Delay between steps (and retries) for server rate limits.
                if step_idx > 0 or attempt > 0:
                    await asyncio.sleep(_MOVE_STEP_DELAY)

                if room_changed is not None:
                    room_changed.clear()

                tag = f" [{step_idx + 1}/{len(steps)}]"
                if attempt == 0:
                    log.info("%s [%d/%d] %s", mode, step_idx + 1, len(steps), direction)
                    if echo_fn is not None:
                        echo_fn(direction + tag)
                else:
                    log.info(
                        "%s [%d/%d] %s (retry %d)",
                        mode,
                        step_idx + 1,
                        len(steps),
                        direction,
                        attempt,
                    )
                # Clear prompt_ready before sending so wait_fn waits
                # for a FRESH GA/EOR from this step's response.  The
                # server sends multiple GA/EORs per response (room
                # prompt + GMCP vitals updates), and stale signals
                # from the previous step cause wait_fn to return
                # before the current room output has been received.
                _prompt_ready = getattr(writer, "_prompt_ready", None)
                if _prompt_ready is not None:
                    _prompt_ready.clear()

                writer.write(direction + "\r\n")  # type: ignore[arg-type]

                if wait_fn is not None:
                    await wait_fn()

                # Yield to let _read_server feed the room output to the
                # autoreply engine before we check reply_pending.
                await asyncio.sleep(0)

                engine = _get_engine()
                _cond_cancelled = False
                if engine is not None:
                    while engine.reply_pending:
                        await asyncio.sleep(0.05)
                    if slow:
                        failed = engine.pop_condition_failed()
                        if failed is not None:
                            rule_idx, desc = failed
                            msg = (
                                f"Travel mode cancelled - failed "
                                f"conditional in AUTOREPLY "
                                f"#{rule_idx} [{desc}]"
                            )
                            log.warning("%s", msg)
                            if echo_fn is not None:
                                echo_fn(msg)
                            _cond_cancelled = True
                    # In slow mode, exclusive rules enter exclusive mode
                    # (e.g. "kill" sent, waiting for "died\.").  Wait for
                    # combat to finish before moving to the next room.
                    # After exclusive/reply_pending clear, wait for a
                    # fresh prompt so the server response to the last
                    # autoreply command is processed -- it may trigger
                    # new matches (cascading always-rules).
                    if slow and (engine.exclusive_active or engine.reply_pending):
                        _settle_passes = 0
                        _max_settle = 20  # safety cap
                        while _settle_passes < _max_settle:
                            if engine.exclusive_active:
                                while engine.exclusive_active:
                                    engine.check_timeout()
                                    await asyncio.sleep(0.05)
                            while engine.reply_pending:
                                await asyncio.sleep(0.05)
                            # Wait for server to respond to whatever the
                            # autoreply just sent.  The prompt signal
                            # drives on_prompt() which may queue new
                            # replies.
                            if wait_fn is not None:
                                await wait_fn()
                            await asyncio.sleep(0)
                            # If neither exclusive nor reply_pending
                            # after the prompt, we've converged.
                            if not engine.exclusive_active and not engine.reply_pending:
                                break
                            _settle_passes += 1
                if _cond_cancelled:
                    break

                # GMCP Room.Info may arrive after the EOR.  Wait for it.
                actual = getattr(writer, "_current_room_num", "")
                if expected_room and actual != expected_room and room_changed is not None:
                    try:
                        await asyncio.wait_for(room_changed.wait(), timeout=2.0)
                    except asyncio.TimeoutError:
                        pass
                    actual = getattr(writer, "_current_room_num", "")

                if actual == expected_room:
                    break
                # Same-name room with different ID -- correct the edge
                # and continue as if we arrived at the expected room.
                # Skipped when correct_names=False (autowander) to preserve
                # distinct room IDs in grids of same-named rooms.
                if (
                    correct_names
                    and expected_room
                    and actual
                    and actual != expected_room
                    and _names_match(expected_room, actual)
                ):
                    log.info(
                        "%s: room ID changed for %s (%s -> %s), correcting",
                        mode,
                        _room_name(actual),
                        expected_room[:8],
                        actual[:8],
                    )
                    _correct_edge(prev_room, direction, expected_room, actual, steps)
                    expected_room = actual
                    break
                # Room didn't change -- server likely rejected move (rate limit).
                # Retry unless we've exhausted attempts.
                if actual == prev_room and attempt < _max_retries:
                    continue
                # Arrived at wrong room -- try to re-route.
                break

            if _cond_cancelled:
                break
            if expected_room and actual and actual != expected_room:
                move_blocked = actual == prev_room
                if move_blocked:
                    # Exit is impassable (server rejected the move after
                    # all retries).  Temporarily remove it from the graph
                    # so re-routing won't try it again.
                    graph = _get_graph()
                    if graph is not None:
                        prev = graph.rooms.get(prev_room)
                        if prev is not None and direction in prev.exits:
                            blocked_exits.append((prev_room, direction, prev.exits[direction]))
                            del prev.exits[direction]
                            log.info(
                                "%s: blocked exit %s of %s (impassable)",
                                mode,
                                direction,
                                prev_room[:8],
                            )
                else:
                    # Update graph edge to reflect actual connection.
                    graph = _get_graph()
                    if graph is not None:
                        prev = graph.rooms.get(prev_room)
                        if prev is not None:
                            prev.exits[direction] = actual
                            log.info(
                                "%s: updated edge %s of %s: -> %s",
                                mode,
                                direction,
                                prev_room[:8],
                                actual[:8],
                            )

                # Try re-pathfinding from actual position.
                if (
                    destination
                    and actual
                    and actual != destination
                    and reroute_count < _max_reroutes
                    and graph is not None
                ):
                    new_steps = graph.find_path_with_rooms(actual, destination)
                    if new_steps is not None:
                        reroute_count += 1
                        msg = (
                            f"{mode}: re-routing from "
                            f"{_room_name(actual)}"
                            f" ({reroute_count}/{_max_reroutes})"
                        )
                        log.info("%s", msg)
                        if echo_fn is not None:
                            echo_fn(msg)
                        steps = new_steps
                        step_idx = 0
                        continue

                expected_name = _room_name(expected_room)
                actual_name = _room_name(actual)
                msg = (
                    f"{mode} stopped: expected {expected_name} after "
                    f"'{direction}', got {actual_name}"
                )
                log.warning("%s", msg)
                if echo_fn is not None:
                    echo_fn(msg)
                break
            step_idx += 1
    finally:
        # Restore temporarily blocked exits so the graph stays accurate
        # for future pathfinding (the block may be transient, e.g. a
        # quest gate that opens later).
        if blocked_exits:
            graph = _get_graph()
            if graph is not None:
                for room_num, exit_dir, target in blocked_exits:
                    prev = graph.rooms.get(room_num)
                    if prev is not None and exit_dir not in prev.exits:
                        prev.exits[exit_dir] = target
        engine = _get_engine()
        if engine is not None:
            engine.suppress_exclusive = False


async def _autowander(
    writer: Union[TelnetWriter, TelnetWriterUnicode], log: logging.Logger
) -> None:
    """
    Visit up to 99 same-named rooms using slow travel.

    Computes a list of rooms with the same name as the current room, sorted by least-recently-
    visited, then walks through them one leg at a time with slow travel (autoreplies fire in each
    room).

    :param writer: Telnet writer with room graph and session attributes.
    :param log: Logger.
    """
    if getattr(writer, "_wander_active", False):
        return

    current = getattr(writer, "_current_room_num", "")
    graph = getattr(writer, "_room_graph", None)
    echo_fn = getattr(writer, "_echo_command", None)
    if not current or graph is None:
        if echo_fn is not None:
            echo_fn("AUTOWANDER: no room data")
        return

    targets = graph.find_same_name(current)
    if not targets:
        if echo_fn is not None:
            echo_fn("AUTOWANDER: no matching rooms")
        return

    # Nearest-neighbor ordering: greedily pick the closest unvisited
    # target from the current position to minimise backtracking.
    ordered: list[type(targets[0])] = []
    remaining = list(targets)
    pos = current
    while remaining:
        best_idx = 0
        best_dist = float("inf")
        for idx, candidate in enumerate(remaining):
            path = graph.find_path_with_rooms(pos, candidate.num)
            dist = len(path) if path is not None else float("inf")
            if dist < best_dist:
                best_dist = dist
                best_idx = idx
        chosen = remaining.pop(best_idx)
        ordered.append(chosen)
        pos = chosen.num
    targets = ordered

    _leg_retries = 3
    visited: set[str] = {current}
    # pylint: disable=protected-access
    writer._wander_active = True
    writer._wander_total = len(targets)
    try:
        for i, target_room in enumerate(targets):
            writer._wander_current = i + 1
            pos = getattr(writer, "_current_room_num", "")
            if pos == target_room.num or target_room.num in visited:
                visited.add(target_room.num)
                continue

            arrived = False
            for leg_attempt in range(_leg_retries + 1):
                pos = getattr(writer, "_current_room_num", "")
                steps = graph.find_path_with_rooms(pos, target_room.num)
                if steps is None:
                    if echo_fn is not None:
                        echo_fn(
                            f"AUTOWANDER [{i + 1}/{len(targets)}]: "
                            f"no path to {target_room.name} "
                            f"({target_room.num[:8]})"
                        )
                    break
                if echo_fn is not None:
                    tag = "" if leg_attempt == 0 else f" (retry {leg_attempt})"
                    echo_fn(
                        f"AUTOWANDER [{i + 1}/{len(targets)}]: "
                        f"heading to {target_room.name} "
                        f"({target_room.num[:8]}){tag}"
                    )
                await _fast_travel(
                    steps, writer, log, slow=True, destination=target_room.num, correct_names=False
                )
                # Wait for any autoreply combat triggered by the
                # arrival glance to finish before moving on.  The
                # post_command "glance" from the previous kill can
                # trigger a new "kill X" that enters exclusive mode
                # AFTER _fast_travel's settle loop has returned.
                _ar = getattr(writer, "_autoreply_engine", None)
                if _ar is not None:
                    _settle = 0
                    while _settle < 60:
                        if _ar.exclusive_active:
                            while _ar.exclusive_active:
                                _ar.check_timeout()
                                await asyncio.sleep(0.1)
                        while _ar.reply_pending:
                            await asyncio.sleep(0.05)
                        await asyncio.sleep(0.1)
                        if not _ar.exclusive_active and not _ar.reply_pending:
                            break
                        _settle += 1
                actual = getattr(writer, "_current_room_num", "")
                if actual == target_room.num:
                    arrived = True
                    visited.add(actual)
                    break
                # Mark any intermediate room we passed through.
                if actual:
                    visited.add(actual)
                if leg_attempt < _leg_retries:
                    log.info(
                        "AUTOWANDER: leg to %s failed, retrying (%d/%d)",
                        target_room.num[:8],
                        leg_attempt + 1,
                        _leg_retries,
                    )
                    await asyncio.sleep(1.0)
            actual = getattr(writer, "_current_room_num", "")
            if not arrived and actual != target_room.num:
                if echo_fn is not None:
                    echo_fn(
                        f"AUTOWANDER [{i + 1}/{len(targets)}]: "
                        f"could not reach {target_room.name} "
                        f"({target_room.num[:8]})"
                    )
                break
    finally:
        writer._wander_active = False
        writer._wander_current = 0
        writer._wander_total = 0
        writer._wander_task = None


async def _autodiscover(
    writer: Union[TelnetWriter, TelnetWriterUnicode], log: logging.Logger
) -> None:
    """
    Explore unvisited exits reachable from the current room.

    BFS-discovers frontier exits (leading to unvisited or unknown rooms),
    travels to each, then returns to the starting room before trying the
    next.  Maintains an in-memory ``tried`` set to avoid retrying exits
    that failed or led to unexpected rooms.

    :param writer: Telnet writer with room graph and session attributes.
    :param log: Logger.
    """
    if getattr(writer, "_discover_active", False):
        return

    current = getattr(writer, "_current_room_num", "")
    graph = getattr(writer, "_room_graph", None)
    echo_fn = getattr(writer, "_echo_command", None)
    if not current or graph is None:
        if echo_fn is not None:
            echo_fn("AUTODISCOVER: no room data")
        return

    tried: set[tuple[str, str]] = set()
    inaccessible: set[str] = set()

    branches = graph.find_branches(current)
    if not branches:
        if echo_fn is not None:
            echo_fn("AUTODISCOVER: no unvisited exits nearby")
        return

    # pylint: disable=protected-access
    writer._discover_active = True
    writer._discover_total = len(branches)
    writer._discover_current = 0
    step_count = 0
    try:
        while True:
            pos = getattr(writer, "_current_room_num", "")
            # Re-discover from current position each iteration — picks up
            # newly revealed exits from rooms we just visited, nearest-first.
            branches = [
                (gw, d, t)
                for gw, d, t in graph.find_branches(pos)
                if (gw, d) not in tried and t not in inaccessible
            ]
            if not branches:
                break

            writer._discover_total = step_count + len(branches)
            gw_room, direction, target_num = branches[0]
            step_count += 1
            writer._discover_current = step_count

            # Travel to the gateway room (nearest-first, so usually short).
            if pos != gw_room:
                steps = graph.find_path_with_rooms(pos, gw_room)
                if steps is None:
                    tried.add((gw_room, direction))
                    if target_num:
                        inaccessible.add(target_num)
                    if echo_fn is not None:
                        echo_fn(
                            f"AUTODISCOVER [{step_count}]: " f"no path to gateway {gw_room[:8]}"
                        )
                    continue
                if echo_fn is not None:
                    echo_fn(f"AUTODISCOVER [{step_count}]: " f"heading to gateway {gw_room[:8]}")
                await _fast_travel(steps, writer, log, slow=False, destination=gw_room)
                actual = getattr(writer, "_current_room_num", "")
                if actual != gw_room:
                    tried.add((gw_room, direction))
                    if target_num:
                        inaccessible.add(target_num)
                    log.info("AUTODISCOVER: failed to reach gateway %s", gw_room[:8])
                    if echo_fn is not None:
                        echo_fn(
                            f"AUTODISCOVER [{step_count}]: "
                            f"gateway {gw_room[:8]} inaccessible, skipping"
                        )
                    continue

            # Step through the frontier exit.
            if echo_fn is not None:
                echo_fn(
                    f"AUTODISCOVER [{step_count}]: " f"exploring {direction} from {gw_room[:8]}"
                )
            _send = getattr(writer, "_send_line", None)
            if _send is not None:
                _send(direction)
            elif isinstance(writer, TelnetWriterUnicode):
                writer.write(direction + "\r\n")
            else:
                writer.write((direction + "\r\n").encode("utf-8"))
            # Wait for room arrival.
            for _wait in range(30):
                await asyncio.sleep(0.3)
                new_room = getattr(writer, "_current_room_num", "")
                if new_room != gw_room:
                    break
            else:
                tried.add((gw_room, direction))
                if target_num:
                    inaccessible.add(target_num)
                if echo_fn is not None:
                    echo_fn(f"AUTODISCOVER [{step_count}]: " f"no room change after {direction}")
                continue

            tried.add((gw_room, direction))
            actual = getattr(writer, "_current_room_num", "")
            if target_num and actual != target_num and target_num in graph.rooms:
                if echo_fn is not None:
                    echo_fn(
                        f"AUTODISCOVER [{step_count}]: "
                        f"unexpected room {actual[:8]} "
                        f"(expected {target_num[:8]})"
                    )

            # Wait for any autoreply to settle.
            _ar = getattr(writer, "_autoreply_engine", None)
            if _ar is not None:
                _settle = 0
                while _settle < 60:
                    if _ar.exclusive_active:
                        while _ar.exclusive_active:
                            _ar.check_timeout()
                            await asyncio.sleep(0.1)
                    while _ar.reply_pending:
                        await asyncio.sleep(0.05)
                    await asyncio.sleep(0.1)
                    if not _ar.exclusive_active and not _ar.reply_pending:
                        break
                    _settle += 1

            # Stay where we are — next iteration re-discovers branches
            # from current position, so nearby clusters get swept without
            # backtracking.
    except asyncio.CancelledError:
        pass
    finally:
        writer._discover_active = False
        writer._discover_current = 0
        writer._discover_total = 0
        writer._discover_task = None


# std imports
import re as _re  # noqa: E402  # pylint: disable=wrong-import-position

_REPEAT_RE = _re.compile(r"^(\d+)([A-Za-z].*)$")
_BACKTICK_RE = _re.compile(r"`[^`]*`")


def expand_commands(line: str) -> list[str]:
    """
    Split *line* on ``;`` (outside backticks) and expand repeat prefixes.

    Backtick-enclosed tokens (e.g. ```fast travel 123```, ```delay 1s```)
    are preserved verbatim -- they are not split on ``;`` and repeat
    expansion is not applied.

    A segment like ``5e`` becomes ``['e', 'e', 'e', 'e', 'e']``.
    Only a leading integer followed immediately by an alphabetic
    character triggers expansion (e.g. ``5east`` -> 5 × ``east``).
    Segments without a leading digit are passed through unchanged.

    :param line: Raw user input line.
    :returns: Flat list of individual commands.
    """
    # Replace backtick tokens with placeholders to protect from ; splitting.
    placeholders: list[str] = []

    def _replace_bt(m: _re.Match[str]) -> str:
        placeholders.append(m.group(0))
        return f"\x00BT{len(placeholders) - 1}\x00"

    protected = _BACKTICK_RE.sub(_replace_bt, line)
    parts = protected.split(";") if ";" in protected else [protected]
    result: list[str] = []
    for part in parts:
        stripped = part.strip()
        if not stripped:
            continue
        # Restore backtick placeholders.
        while "\x00BT" in stripped:
            for i, orig in enumerate(placeholders):
                stripped = stripped.replace(f"\x00BT{i}\x00", orig)
        if stripped.startswith("`") and stripped.endswith("`"):
            result.append(stripped)
            continue
        m = _REPEAT_RE.match(stripped)
        if m:
            count = min(int(m.group(1)), 200)
            cmd = m.group(2)
            result.extend([cmd] * count)
        else:
            result.append(stripped)
    return result


_TRAVEL_RE = _re.compile(
    r"^`(fast travel|slow travel|return fast|return slow|autowander|autodiscover)\s*(.*?)`$",
    _re.IGNORECASE,
)


async def _handle_travel_commands(
    parts: list[str], writer: Union[TelnetWriter, TelnetWriterUnicode], log: logging.Logger
) -> list[str]:
    """
    Scan *parts* for travel commands, execute them, and return remaining parts.

    Recognised commands (case-insensitive, enclosed in backticks):

    - ```fast travel <id>``` -- fast travel to room *id*
    - ```slow travel <id>``` -- slow travel to room *id*
    - ```return fast``` -- fast travel to the current room (snapshot)
    - ```return slow``` -- slow travel to the current room (snapshot)
    - ```autowander``` -- visit all same-named rooms via slow travel
    - ```autodiscover``` -- explore unvisited exits from nearby rooms

    Only the **first** travel command in the list is handled; everything
    before it is returned as-is (already sent by the caller), and everything
    after it is returned for the caller to send as chained commands once
    travel finishes.

    :param parts: Expanded command list from :func:`expand_commands`.
    :param writer: Telnet writer with room graph attributes.
    :param log: Logger.
    :returns: Commands that still need to be sent to the server.
    """
    for idx, cmd in enumerate(parts):
        m = _TRAVEL_RE.match(cmd)
        if not m:
            continue
        verb = m.group(1).lower()
        arg = m.group(2).strip()

        if verb == "autowander":
            await _autowander(writer, log)
            return parts[idx + 1 :]

        if verb == "autodiscover":
            await _autodiscover(writer, log)
            return parts[idx + 1 :]

        slow = "slow" in verb
        is_return = verb.startswith("return")

        if is_return:
            room_id = getattr(writer, "_current_room_num", "")
        else:
            room_id = arg

        if not room_id:
            log.warning("travel command with no room id: %r", cmd)
            break

        current = getattr(writer, "_current_room_num", "")
        if not current:
            log.warning("no current room -- cannot travel")
            break

        graph = getattr(writer, "_room_graph", None)
        if graph is None:
            log.warning("no room graph -- cannot travel")
            break

        path = graph.find_path_with_rooms(current, room_id)
        if path is None:
            log.warning("no path from %s to %s", current, room_id)
            break

        await _fast_travel(path, writer, log, slow=slow, destination=room_id)
        return parts[idx + 1 :]

    return parts


_MOVE_STEP_DELAY = 0.15
_MOVE_MAX_RETRIES = 3


async def _send_chained(
    commands: list[str], writer: Union[TelnetWriter, TelnetWriterUnicode], log: logging.Logger
) -> None:
    """
    Send multiple commands with GA/EOR pacing between each.

    The first command is assumed to have already been sent by the caller.
    This coroutine sends commands 2..N, waiting for the server prompt
    signal before each one.

    When all commands in the list are identical (e.g. ``9e`` expanded to
    nine ``e`` commands), movement retry logic is applied: if the room
    does not change after a command, the same command is retried up to
    :data:`_MOVE_MAX_RETRIES` times with a delay between attempts.

    :param commands: List of commands (index 1+ will be sent).
    :param writer: Telnet writer.
    :param log: Logger.
    """
    wait_fn = getattr(writer, "_wait_for_prompt", None)
    echo_fn = getattr(writer, "_echo_command", None)
    prompt_ready = getattr(writer, "_prompt_ready", None)
    room_changed = getattr(writer, "_room_changed", None)

    is_repeated = len(commands) > 1 and len(set(commands)) == 1

    for _idx, cmd in enumerate(commands[1:], 1):
        prev_room = getattr(writer, "_current_room_num", "") if is_repeated else ""

        if not is_repeated:
            # Mixed commands: GA/EOR pacing only.
            if prompt_ready is not None:
                prompt_ready.clear()
            if wait_fn is not None:
                await wait_fn()
            log.debug("chained command: %r", cmd)
            if echo_fn is not None:
                echo_fn(cmd)
            writer.write(cmd + "\r\n")  # type: ignore[arg-type]
            continue

        # Repeated commands: delay + room-change pacing with retry.
        for attempt in range(_MOVE_MAX_RETRIES + 1):
            # Always delay -- the first repeated command needs spacing
            # from the caller's initial send, and retries need a longer
            # back-off to respect the server's rate limit.
            if attempt == 0:
                await asyncio.sleep(_MOVE_STEP_DELAY)
            else:
                await asyncio.sleep(1.0)
            if room_changed is not None:
                room_changed.clear()
            if prompt_ready is not None:
                prompt_ready.clear()
            if attempt == 0:
                log.debug("chained command: %r", cmd)
                if echo_fn is not None:
                    echo_fn(cmd)
            else:
                log.info("chained retry %d: %r", attempt, cmd)
            writer.write(cmd + "\r\n")  # type: ignore[arg-type]

            if not prev_room:
                break

            # Wait briefly for room change -- GMCP typically arrives
            # within 100-200ms.  A short timeout keeps movement brisk
            # while still detecting rate-limit rejections.
            actual = getattr(writer, "_current_room_num", "")
            if actual != prev_room:
                break
            if room_changed is not None:
                try:
                    await asyncio.wait_for(room_changed.wait(), timeout=0.5)
                except asyncio.TimeoutError:
                    pass
                actual = getattr(writer, "_current_room_num", "")
            if actual != prev_room:
                break
            if attempt < _MOVE_MAX_RETRIES:
                log.info(
                    "room unchanged after %r, retrying (%d/%d)", cmd, attempt + 1, _MOVE_MAX_RETRIES
                )
            else:
                log.warning(
                    "room unchanged after %r, giving up after %d retries", cmd, _MOVE_MAX_RETRIES
                )
                return


async def execute_macro_commands(
    text: str, writer: Union[TelnetWriter, TelnetWriterUnicode], log: logging.Logger
) -> None:
    """
    Execute a macro text string, handling travel and delay commands.

    Expands the text with :func:`expand_commands`, then processes each
    part -- backtick-enclosed travel commands are routed through
    :func:`_handle_travel_commands`, delay commands pause execution,
    and plain commands are sent to the server with GA/EOR pacing.

    :param text: Raw macro text with ``;`` separators.
    :param writer: Telnet writer.
    :param log: Logger.
    """
    from .autoreply import _DELAY_RE  # pylint: disable=import-outside-toplevel

    parts = expand_commands(text)
    if not parts:
        return

    wait_fn = getattr(writer, "_wait_for_prompt", None)
    echo_fn = getattr(writer, "_echo_command", None)
    prompt_ready = getattr(writer, "_prompt_ready", None)

    idx = 0
    while idx < len(parts):
        cmd = parts[idx]

        # Travel command -- hand off the rest to _handle_travel_commands.
        if _TRAVEL_RE.match(cmd):
            remainder = await _handle_travel_commands(parts[idx:], writer, log)
            # remainder contains post-travel commands; continue processing.
            parts = remainder
            idx = 0
            continue

        # Delay command.
        dm = _DELAY_RE.match(cmd)
        if dm:
            value = float(dm.group(1))
            unit = dm.group(2)
            delay = value / 1000.0 if unit == "ms" else value
            if delay > 0:
                await asyncio.sleep(delay)
            idx += 1
            continue

        # Plain command -- send with pacing.
        if idx > 0:
            if prompt_ready is not None:
                prompt_ready.clear()
            if wait_fn is not None:
                await wait_fn()
        log.info("macro: sending %r", cmd)
        if echo_fn is not None:
            echo_fn(cmd)
        writer.write(cmd + "\r\n")  # type: ignore[arg-type]
        idx += 1


if sys.platform != "win32":
    import fcntl
    import struct
    import termios

    def _get_terminal_size() -> Tuple[int, int]:
        """Return ``(rows, cols)`` of the controlling terminal."""
        try:
            fmt = "hhhh"
            buf = b"\x00" * struct.calcsize(fmt)
            val = fcntl.ioctl(sys.stdin.fileno(), termios.TIOCGWINSZ, buf)
            rows, cols, _, _ = struct.unpack(fmt, val)
            return rows, cols
        except OSError:
            return (int(os.environ.get("LINES", "25")), int(os.environ.get("COLUMNS", "80")))

    class ScrollRegion:
        """
        Context manager that sets a VT100 scroll region (DECSTBM).

        Confines terminal output to the top portion, reserving
        the bottom line(s) for the REPL input.  Follows the same
        pattern as ``blessed.Terminal.scroll_region``.

        :param stdout: asyncio StreamWriter for local terminal output.
        :param rows: Total terminal height.
        :param cols: Total terminal width.
        :param reserve_bottom: Number of bottom lines to reserve.
        """

        def __init__(
            self, stdout: asyncio.StreamWriter, rows: int, cols: int, reserve_bottom: int = 1
        ) -> None:
            """Initialize scroll region with output stream and dimensions."""
            self._stdout = stdout
            self._rows = rows
            self._cols = cols
            self._reserve = reserve_bottom
            self._active = False
            self._dirty = False

        @property
        def scroll_bottom(self) -> int:
            """0-indexed last row of the scroll region."""
            return max(0, self._rows - self._reserve - 2)

        @property
        def scroll_rows(self) -> int:
            """0-indexed last row of the scroll region (alias for scroll_bottom)."""
            return self.scroll_bottom

        @property
        def input_row(self) -> int:
            """0-indexed row for the input line."""
            return self._rows - self._reserve

        @property
        def resize_pending(self) -> bool:
            """Check and clear the resize-pending flag."""
            if self._dirty:
                self._dirty = False
                return True
            return False

        def grow_reserve(self, new_reserve: int) -> None:
            """
            Increase the reserved bottom area and reapply scroll region.

            Emits newlines inside the current scroll region first so that any server text on the
            rows about to be claimed is scrolled up rather than silently overwritten (e.g. a
            password prompt arriving just as the GMCP status bar appears).
            """
            if new_reserve <= self._reserve:
                return
            extra = new_reserve - self._reserve
            old_input_row = self.input_row
            t = _get_term()
            if self._active:
                old_bottom = self.scroll_bottom
                self._stdout.write(t.move_yx(old_bottom, 0).encode())
                self._stdout.write(b"\n" * extra)
            self._reserve = new_reserve
            if self._active:
                for _r in range(old_input_row, old_input_row + new_reserve):
                    self._stdout.write((t.move_yx(_r, 0) + t.clear_eol).encode())
                self._set_scroll_region()
                self._stdout.write(t.restore.encode())
                if extra > 0:
                    self._stdout.write(t.move_up(extra).encode())
                self._stdout.write(t.save.encode())
                for _r in range(self.input_row, self.input_row + new_reserve):
                    self._stdout.write((t.move_yx(_r, 0) + t.clear_eol).encode())
                self._dirty = True

        def update_size(self, rows: int, cols: int) -> None:
            """
            Update dimensions and reapply scroll region.

            No content scrolling occurs here — ``_on_resize_repaint``
            replays the buffer and saves the cursor at the correct
            position afterward.
            """
            old_input_row = self.input_row
            self._rows = rows
            self._cols = cols
            t = _get_term()
            if self._active:
                for _r in range(old_input_row, old_input_row + self._reserve):
                    self._stdout.write((t.move_yx(_r, 0) + t.clear_eol).encode())
                self._set_scroll_region()
                self._stdout.write(t.save.encode())
                for _r in range(self.input_row, self.input_row + self._reserve):
                    self._stdout.write((t.move_yx(_r, 0) + t.clear_eol).encode())
                self._dirty = True

        def _set_scroll_region(self) -> None:
            """Write DECSTBM escape sequence to set scroll region."""
            t = _get_term()
            bottom = self.scroll_bottom
            self._stdout.write(t.change_scroll_region(0, bottom).encode())
            dmz = bottom + 1
            if dmz < self.input_row:
                self._stdout.write(
                    (t.move_yx(dmz, 0) + t.clear_eol + "\u2500" * self._cols).encode()
                )
            self._stdout.write(t.move_yx(bottom, 0).encode())

        def _reset_scroll_region(self) -> None:
            """Reset scroll region to full terminal height."""
            t = _get_term()
            self._stdout.write(t.change_scroll_region(0, self._rows - 1).encode())

        def save_and_goto_input(self) -> None:
            """Save cursor, move to input line, clear it."""
            t = _get_term()
            self._stdout.write(t.save.encode())
            self._stdout.write(t.move_yx(self.input_row, 0).encode())
            self._stdout.write(t.clear_eol.encode())

        def restore_cursor(self) -> None:
            """Restore cursor to saved position in scroll region."""
            self._stdout.write(_get_term().restore.encode())

        def __enter__(self) -> "ScrollRegion":
            self._set_scroll_region()
            self._active = True
            return self

        def __exit__(self, *_: Any) -> None:
            self._active = False
            self._reset_scroll_region()
            t = _get_term()
            self._stdout.write(t.move_yx(self._rows - 1, 0).encode())

    import contextlib

    @contextlib.asynccontextmanager
    async def _repl_scaffold(
        telnet_writer: Union[TelnetWriter, TelnetWriterUnicode],
        tty_shell: "client_shell.Terminal",
        stdout: asyncio.StreamWriter,
        reserve_bottom: int = 1,
        on_resize: "Optional[Callable[[int, int], None]]" = None,
    ) -> "Any":
        """
        Set up NAWS patch, scroll region, and resize handler.

        Yields ``(scroll, rows_cols)`` where *rows_cols* is a mutable
        ``[rows, cols]`` list kept up-to-date by the resize handler.
        Restores the original ``handle_send_naws`` in a ``finally`` block.

        :param on_resize: Optional extra callback invoked after scroll
            region update, receiving ``(new_rows, new_cols)``.
        """
        from .telopt import NAWS  # pylint: disable=import-outside-toplevel

        rows, cols = _get_terminal_size()
        rows_cols = [rows, cols]
        scroll_region: Optional[ScrollRegion] = None

        orig_send_naws = getattr(telnet_writer, "handle_send_naws", None)

        def _adjusted_send_naws() -> Tuple[int, int]:
            # pylint: disable-next=protected-access
            if scroll_region is not None and scroll_region._active:
                _, cur_cols = _get_terminal_size()
                return (scroll_region.scroll_rows, cur_cols)
            return _get_terminal_size()

        telnet_writer.handle_send_naws = _adjusted_send_naws  # type: ignore[method-assign]

        try:
            if telnet_writer.local_option.enabled(NAWS) and not telnet_writer.is_closing():
                telnet_writer._send_naws()  # pylint: disable=protected-access

            with ScrollRegion(stdout, rows, cols, reserve_bottom=reserve_bottom) as scroll:
                scroll_region = scroll

                def _handle_resize(new_rows: int, new_cols: int) -> None:
                    rows_cols[0] = new_rows
                    rows_cols[1] = new_cols
                    scroll.update_size(new_rows, new_cols)
                    if on_resize is not None:
                        on_resize(new_rows, new_cols)

                tty_shell.on_resize = _handle_resize
                try:
                    yield scroll, rows_cols
                finally:
                    tty_shell.on_resize = None
        finally:
            if orig_send_naws is not None:
                telnet_writer.handle_send_naws = orig_send_naws  # type: ignore[method-assign]

    async def _run_repl_tasks(server_coro: "Any", input_coro: "Any") -> None:
        """Run server and input coroutines; cancel the other when one finishes."""
        server_task = asyncio.ensure_future(server_coro)
        input_task = asyncio.ensure_future(input_coro)
        _, pending = await asyncio.wait(
            [server_task, input_task], return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    def _render_toolbar(  # pylint: disable=too-many-locals,too-many-branches,too-many-statements
        writer: Union[TelnetWriter, TelnetWriterUnicode],
        scroll: "ScrollRegion",
        out: asyncio.StreamWriter,
        autoreply_engine: Any,
        toolbar_state: dict[str, Any],
    ) -> bool:
        """
        Render GMCP vitals toolbar at ``scroll.input_row + 1``.

        :returns: ``True`` if a flash is active and the caller should
            schedule a re-render.
        """
        gmcp_data: Optional[dict[str, Any]] = getattr(writer, "_gmcp_data", None)
        if not toolbar_state.get("has_gmcp"):
            if not gmcp_data:
                return False
            toolbar_state["has_gmcp"] = True
            scroll.grow_reserve(_RESERVE_WITH_TOOLBAR)

        engine = autoreply_engine
        ar_active = engine is not None and (engine.exclusive_active or engine.reply_pending)
        wander_active = getattr(writer, "_wander_active", False)
        discover_active = getattr(writer, "_discover_active", False)

        bars: List[Tuple[str, str]] = []
        room_name = ""
        now = time.monotonic()
        needs_reflash = False
        if gmcp_data:  # pylint: disable=too-many-nested-blocks
            status = gmcp_data.get("Char.Status")
            if isinstance(status, dict):
                level = status.get("level")
                if level is not None:
                    bars.append((_sgr_fg("#aaaaaa"), _segmented(f"Lv:{level}")))

                money = status.get("money")
                if money is not None:
                    try:
                        money_int = int(money)
                        money_str = _segmented(f"${money_int:,}")
                    except (TypeError, ValueError):
                        money_str = _segmented(f"${money}")
                    if bars:
                        bars.append(("", "   "))
                    bars.append((_sgr_fg("#aaaaaa"), money_str))

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
                    hp_flashing = (now - hp_flash) < 0.1
                    if hp_flashing:
                        needs_reflash = True
                    if bars:
                        bars.append(("", "   "))
                    bars.extend(_vital_bar(hp, maxhp, _BAR_WIDTH, "hp", flash=hp_flashing))
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
                    try:
                        mp_int = int(mp)
                    except (TypeError, ValueError):
                        mp_int = 0
                    last_mp = toolbar_state.get("last_mp")
                    if last_mp is not None and mp_int != last_mp:
                        toolbar_state["mp_flash"] = now
                    toolbar_state["last_mp"] = mp_int
                    mp_flash = toolbar_state.get("mp_flash", 0.0)
                    mp_flashing = (now - mp_flash) < 0.1
                    if mp_flashing:
                        needs_reflash = True
                    if bars:
                        bars.append(("", "   "))
                    bars.extend(_vital_bar(mp, maxmp, _BAR_WIDTH, "mp", flash=mp_flashing))

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
                    xp_flashing = (now - xp_flash) < 2.0
                    if xp_flashing:
                        needs_reflash = True
                    if bars:
                        bars.append(("", "   "))
                    bars.extend(_vital_bar(xp_raw, maxxp, _BAR_WIDTH, "xp", flash=xp_flashing))

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
                                bars.append(("", "   "))
                                bars.append((_sgr_fg("#888888"), eta_text))

            room_info = gmcp_data.get("Room.Info", gmcp_data.get("Room.Name"))
            if isinstance(room_info, dict):
                room_name = str(room_info.get("name", room_info.get("Name", "")))
            elif isinstance(room_info, str):
                room_name = room_info

        if room_name:
            toolbar_state["rprompt_text"] = room_name

        right_bar: List[Tuple[str, str]] = []
        if wander_active:
            wcur = getattr(writer, "_wander_current", 0)
            wtot = getattr(writer, "_wander_total", 0)
            right_bar = _vital_bar(wcur, wtot, 12, "wander")
            right_text = ""
        elif discover_active:
            dcur = getattr(writer, "_discover_current", 0)
            dtot = getattr(writer, "_discover_total", 0)
            right_bar = _vital_bar(dcur, dtot, 12, "discover")
            right_text = ""
        elif ar_active:
            idx = getattr(engine, "exclusive_rule_index", None)
            ar_label = f"Autoreply #{idx}" if idx is not None else "Autoreply"
            right_text = " " + ar_label
        else:
            right_text = " " + toolbar_state.get("rprompt_text", "")
        right_width = len(right_text)
        right_bar_width = sum(_wcswidth(txt) if txt else 0 for _, txt in right_bar)

        bt = _get_term()
        cols = bt.width

        bars_width = sum(_wcswidth(txt) if txt else 0 for _, txt in bars)

        toolbar_row = scroll.input_row + 1
        out.write(bt.move_yx(toolbar_row, 0).encode())

        is_autoreply_bg = wander_active or discover_active or ar_active
        if is_autoreply_bg:
            bg_sgr = bt.on_color_rgb(26, 18, 0) + bt.color_rgb(184, 134, 11)
        else:
            bg_sgr = bt.on_color_rgb(26, 0, 0)

        out.write(bg_sgr.encode())
        for sgr, text in bars:
            out.write(f"{sgr}{text}".encode())
            out.write(bg_sgr.encode())

        used = bars_width + right_width + right_bar_width
        pad = max(1, cols - used)
        out.write((" " * pad).encode())

        right_sgr = _sgr_fg("#dddddd") if not is_autoreply_bg else ""
        out.write(f"{right_sgr}{right_text}".encode())

        for sgr, text in right_bar:
            out.write(f"{sgr}{text}".encode())
            out.write(bg_sgr.encode())

        out.write(bt.normal.encode())
        return needs_reflash

    def _render_input_line(
        display: "blessed.line_editor.DisplayState",
        scroll: "ScrollRegion",
        out: asyncio.StreamWriter,
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
            out.write(
                f"{display.suggestion_sgr}{display.suggestion}".encode()
            )

        if display.overflow_right:
            out.write(f"{display.ellipsis_sgr}{_ELLIPSIS}".encode())

        text_w = _wcswidth(display.text) + _wcswidth(display.suggestion)
        rendered = (
            (1 if display.overflow_left else 0)
            + text_w
            + (1 if display.overflow_right else 0)
        )
        pad = cols - rendered
        if pad > 0:
            out.write(f"{display.bg_sgr}{' ' * pad}".encode())
        out.write(bt.normal.encode())

        out.write(bt.move_yx(scroll.input_row, display.cursor).encode())

    class _KeyDispatch:
        """Route blessed keystrokes to hotkey handlers before the line editor."""

        def __init__(self) -> None:
            self._by_name: dict[str, Callable[..., Any]] = {}
            self._by_seq: dict[str, Callable[..., Any]] = {}

        def register(self, blessed_name: str, handler: Callable[..., Any]) -> None:
            """Register a handler for a blessed key name."""
            self._by_name[blessed_name] = handler

        def register_seq(self, char: str, handler: Callable[..., Any]) -> None:
            """Register a handler for a raw character sequence."""
            self._by_seq[char] = handler

        def set_macros(
            self,
            macros: "list[Macro]",
            writer: Union[TelnetWriter, TelnetWriterUnicode],
            log: logging.Logger,
        ) -> None:
            """Replace all macro bindings from a macro definition list."""
            from .macros import build_macro_dispatch  # pylint: disable=import-outside-toplevel

            macro_handlers = build_macro_dispatch(macros, writer, log)
            for key_name, handler in macro_handlers.items():
                if len(key_name) == 1:
                    self._by_seq[key_name] = handler
                else:
                    self._by_name[key_name] = handler

        def lookup(self, key: "blessed.keyboard.Keystroke") -> Optional[Callable[..., Any]]:
            """Look up a handler for a blessed Keystroke, or ``None``."""
            name = getattr(key, "name", None)
            if name and name in self._by_name:
                return self._by_name[name]
            key_str = str(key)
            if key_str in self._by_seq:
                return self._by_seq[key_str]
            return None

    async def repl_event_loop(
        telnet_reader: Union[TelnetReader, TelnetReaderUnicode],
        telnet_writer: Union[TelnetWriter, TelnetWriterUnicode],
        tty_shell: "client_shell.Terminal",
        stdout: asyncio.StreamWriter,
        history_file: Optional[str] = None,
        banner_lines: Optional[List[str]] = None,
    ) -> bool:
        """
        Event loop with REPL input at the bottom of the screen.

        Uses blessed ``async_inkey()`` for keystroke input and a headless
        :class:`~blessed.line_editor.LineEditor` for line editing with
        history and auto-suggest.

        :param tty_shell: ``Terminal`` instance from ``client_shell``.
        :param banner_lines: Lines to display after the scroll region is active.
        :returns: ``True`` if the server switched to kludge mode
            (caller should fall through to the standard event loop),
            ``False`` if the connection closed normally.
        """
        return await _repl_event_loop(
            telnet_reader, telnet_writer, tty_shell, stdout,
            history_file=history_file, banner_lines=banner_lines,
        )

    async def _repl_event_loop(  # pylint: disable=too-many-locals,too-many-statements
        telnet_reader: Union[TelnetReader, TelnetReaderUnicode],
        telnet_writer: Union[TelnetWriter, TelnetWriterUnicode],
        tty_shell: "client_shell.Terminal",
        stdout: asyncio.StreamWriter,
        history_file: Optional[str] = None,
        banner_lines: Optional[List[str]] = None,
    ) -> bool:
        """Unified REPL event loop using blessed LineEditor + async_inkey."""
        # pylint: disable=import-outside-toplevel,cyclic-import
        import blessed
        import blessed.line_editor

        from .client_shell import _transform_output, _flush_color_filter

        mode_switched = False
        loop = asyncio.get_event_loop()

        # pylint: disable=protected-access
        _session_key = getattr(telnet_writer, "_session_key", "")
        _is_ssl = telnet_writer.get_extra_info("ssl_object") is not None
        _conn_info = _session_key + (" SSL" if _is_ssl else "")
        blessed_term = _get_term()
        _make_styles()

        replay_buf = OutputRingBuffer()

        history = blessed.line_editor.LineHistory()
        if history_file:
            _load_history(history, history_file)

        _term_cols = blessed_term.width
        editor = blessed.line_editor.LineEditor(
            history=history,
            is_password=lambda: bool(telnet_writer.will_echo),
            max_width=_term_cols,
            **_STYLE_NORMAL,
        )

        toolbar_state: dict[str, Any] = {"rprompt_text": _conn_info}

        dispatch = _KeyDispatch()
        macro_defs = getattr(telnet_writer, "_macro_defs", None)
        if macro_defs is not None:
            dispatch.set_macros(macro_defs, telnet_writer, telnet_writer.log)
        telnet_writer._key_dispatch = dispatch

        _last_resize_size: list[int] = [0, 0]

        def _on_resize_repaint(_rows: int, _cols: int) -> None:
            if [_rows, _cols] == _last_resize_size:
                return
            _last_resize_size[:] = [_rows, _cols]
            t = _get_term()
            _sr = _scroll_ref[0]
            _reserve = _sr._reserve if _sr is not None else _RESERVE_WITH_TOOLBAR
            stdout.write((t.clear + t.home + t.move_yx(0, 0)).encode())
            data = replay_buf.replay()
            if data:
                stdout.write(data)
            stdout.write(t.save.encode())
            _input_row = _rows - _reserve
            for _r in range(_input_row, _rows):
                stdout.write((t.move_yx(_r, 0) + t.clear_eol).encode())
            if _sr is not None:
                dmz = _sr.scroll_bottom + 1
                if dmz < _sr.input_row:
                    stdout.write(
                        (t.move_yx(dmz, 0) + "\u2500" * _cols).encode()
                    )
            _cs = getattr(telnet_writer, "_cursor_style", _DEFAULT_CURSOR_STYLE)
            stdout.write(_CURSOR_STYLES.get(_cs, CURSOR_BLINKING_BAR).encode())
            editor.max_width = _cols

        _scroll_ref: list[Any] = [None]

        async with _repl_scaffold(
            telnet_writer,
            tty_shell,
            stdout,
            reserve_bottom=_RESERVE_INITIAL,
            on_resize=_on_resize_repaint,
        ) as (scroll, _):
            _scroll_ref[0] = scroll
            t = _get_term()

            if banner_lines:
                for _bl in banner_lines:
                    stdout.write(f"{_bl}\r\n".encode())

            stdout.write(t.save.encode())
            _cursor_style_name = getattr(telnet_writer, "_cursor_style", _DEFAULT_CURSOR_STYLE)
            _cursor_seq = _CURSOR_STYLES.get(_cursor_style_name, CURSOR_BLINKING_BAR)
            stdout.write(_cursor_seq.encode())

            def _echo_autoreply(cmd: str) -> None:
                stdout.write(t.restore.encode())
                _colored = f"{t.cyan}{cmd}{t.normal}\r\n"
                stdout.write(_colored.encode())
                replay_buf.append(_colored.encode())
                stdout.write(t.save.encode())
                cursor_col = editor.display.cursor
                stdout.write(t.move_yx(scroll.input_row, cursor_col).encode())

            def _insert_into_prompt(text: str) -> None:
                editor.insert_text(text)

            prompt_ready = asyncio.Event()
            prompt_ready.set()
            _ga_detected = False
            _prompt_pending = False

            def _on_prompt_signal(_cmd: bytes) -> None:
                nonlocal _ga_detected, _prompt_pending
                _ga_detected = True
                prompt_ready.set()
                _prompt_pending = True
                telnet_reader._wakeup_waiter()  # type: ignore[union-attr]

            from .telopt import GA, CMD_EOR  # pylint: disable=import-outside-toplevel

            telnet_writer.set_iac_callback(GA, _on_prompt_signal)
            telnet_writer.set_iac_callback(CMD_EOR, _on_prompt_signal)

            async def _wait_for_prompt() -> None:
                if not _ga_detected:
                    return
                try:
                    await asyncio.wait_for(prompt_ready.wait(), timeout=2.0)
                except asyncio.TimeoutError:
                    pass
                prompt_ready.clear()

            telnet_writer._wait_for_prompt = _wait_for_prompt
            telnet_writer._echo_command = _echo_autoreply
            telnet_writer._prompt_ready = prompt_ready

            autoreply_engine: Optional["AutoreplyEngine"] = None
            _ar_rules_ref: object = None

            def _refresh_autoreply_engine() -> None:
                nonlocal autoreply_engine, _ar_rules_ref
                cur_rules = getattr(telnet_writer, "_autoreply_rules", None)
                if cur_rules is _ar_rules_ref:
                    return
                _ar_rules_ref = cur_rules
                prev_suppress = (
                    autoreply_engine.suppress_exclusive if autoreply_engine is not None else False
                )
                if autoreply_engine is not None:
                    autoreply_engine.cancel()
                    autoreply_engine = None
                if cur_rules:
                    from .autoreply import (  # pylint: disable=import-outside-toplevel
                        AutoreplyEngine,
                    )

                    autoreply_engine = AutoreplyEngine(
                        cur_rules,
                        telnet_writer,
                        telnet_writer.log,
                        insert_fn=_insert_into_prompt,
                        echo_fn=_echo_autoreply,
                        wait_fn=_wait_for_prompt,
                    )
                    autoreply_engine.suppress_exclusive = prev_suppress
                telnet_writer._autoreply_engine = autoreply_engine

            _refresh_autoreply_engine()

            # Register builtin hotkeys.
            def _reg_close() -> None:
                nonlocal server_done
                server_done = True
                telnet_writer.close()

            dispatch.register_seq("\x1d", lambda: _reg_close())  # Ctrl+]
            dispatch.register_seq(
                "\x0c", lambda: _repaint_screen(replay_buf, scroll=scroll)
            )  # Ctrl+L

            dispatch.register("KEY_F1", lambda: _show_help(macro_defs, replay_buf=replay_buf))
            dispatch.register(
                "KEY_F8", lambda: _launch_tui_editor("macros", telnet_writer, replay_buf)
            )
            dispatch.register("KEY_F7", lambda: _launch_room_browser(telnet_writer, replay_buf))
            dispatch.register(
                "KEY_F9", lambda: _launch_tui_editor("autoreplies", telnet_writer, replay_buf)
            )

            def _toggle_autoreplies() -> None:
                if autoreply_engine is None:
                    return
                autoreply_engine.enabled = not autoreply_engine.enabled
                state = "ON" if autoreply_engine.enabled else "OFF"
                _echo_autoreply(f"AUTOREPLIES {state}")

            dispatch.register("KEY_F21", _toggle_autoreplies)  # Shift+F9

            def _discover_mode() -> None:
                if getattr(telnet_writer, "_discover_active", False):
                    task = getattr(telnet_writer, "_discover_task", None)
                    if task is not None:
                        task.cancel()
                    return
                from .rooms import load_prefs, save_prefs  # pylint: disable=import-outside-toplevel

                skey = getattr(telnet_writer, "_session_key", "")
                prefs = load_prefs(skey) if skey else {}
                if not prefs.get("skip_autodiscover_confirm"):
                    ok, dont_ask = _confirm_dialog(
                        "Autodiscover",
                        "Autodiscover explores exits from nearby rooms "
                        "that lead to unvisited places. It will travel "
                        "to each frontier exit, check the room, then "
                        "return before trying the next branch.",
                        warning=(
                            "WARNING: This can lead to dangerous areas, "
                            "death traps, or aggressive monsters! Your "
                            "character may die. Use with caution."
                        ),
                        replay_buf=replay_buf,
                    )
                    if not ok:
                        return
                    if dont_ask and skey:
                        prefs["skip_autodiscover_confirm"] = True
                        save_prefs(skey, prefs)
                t = asyncio.ensure_future(_autodiscover(telnet_writer, telnet_writer.log))
                telnet_writer._discover_task = t

            dispatch.register("KEY_F4", _discover_mode)

            def _wander_mode() -> None:
                if getattr(telnet_writer, "_wander_active", False):
                    task = getattr(telnet_writer, "_wander_task", None)
                    if task is not None:
                        task.cancel()
                    return
                from .rooms import load_prefs, save_prefs  # pylint: disable=import-outside-toplevel

                skey = getattr(telnet_writer, "_session_key", "")
                prefs = load_prefs(skey) if skey else {}
                if not prefs.get("skip_autowander_confirm"):
                    ok, dont_ask = _confirm_dialog(
                        "Autowander",
                        "Autowander visits all rooms with the same "
                        "name as the current room using slow travel. "
                        "Autoreplies fire in each room visited. The "
                        "route is optimised to minimise backtracking.",
                        replay_buf=replay_buf,
                    )
                    if not ok:
                        return
                    if dont_ask and skey:
                        prefs["skip_autowander_confirm"] = True
                        save_prefs(skey, prefs)
                task = asyncio.ensure_future(_autowander(telnet_writer, telnet_writer.log))
                telnet_writer._wander_task = task

            dispatch.register("KEY_F5", _wander_mode)

            server_done = False

            _last_input_style: list[Optional[dict[str, str]]] = [None]

            def _update_input_style() -> None:
                engine = autoreply_engine
                ar_active = engine is not None and (engine.exclusive_active or engine.reply_pending)
                wander = getattr(telnet_writer, "_wander_active", False)
                disc = getattr(telnet_writer, "_discover_active", False)
                style = _STYLE_AUTOREPLY if (wander or disc or ar_active) else _STYLE_NORMAL
                changed = _last_input_style[0] is not style
                _last_input_style[0] = style
                for attr, val in style.items():
                    setattr(editor, attr, val)
                if changed:
                    _render_input_line(editor.display, scroll, stdout)

            async def _read_server() -> None:
                nonlocal server_done, mode_switched, _prompt_pending
                _esc_hold = b""
                while not server_done:
                    out = await telnet_reader.read(2**24)
                    if not out:
                        if telnet_reader.at_eof():
                            server_done = True
                            if _esc_hold:
                                stdout.write(t.restore.encode())
                                stdout.write(_esc_hold)
                                replay_buf.append(_esc_hold)
                                stdout.write(t.save.encode())
                            _flush_color_filter(telnet_writer, stdout)
                            stdout.write(t.restore.encode())
                            stdout.write(b"\r\nConnection closed by foreign host.\r\n")
                            return
                        if _prompt_pending and autoreply_engine is not None:
                            _prompt_pending = False
                            autoreply_engine.on_prompt()
                        continue
                    if isinstance(out, bytes):
                        out = out.decode("utf-8", errors="replace")
                    out = _transform_output(out, telnet_writer, True)
                    _refresh_autoreply_engine()
                    if autoreply_engine is not None:
                        autoreply_engine.feed(out)
                        if _prompt_pending:
                            _prompt_pending = False
                            autoreply_engine.on_prompt()
                    if _editor_active:
                        _editor_buffer.append(out.encode())
                        continue
                    stdout.write(t.restore.encode())
                    if _editor_buffer:
                        for chunk in _editor_buffer:
                            stdout.write(chunk)
                            replay_buf.append(chunk)
                        _editor_buffer.clear()
                    encoded = _esc_hold + out.encode()
                    encoded, _esc_hold = _split_incomplete_esc(encoded)
                    if encoded:
                        stdout.write(encoded)
                        replay_buf.append(encoded)
                    stdout.write(t.save.encode())
                    cursor_col = editor.display.cursor
                    stdout.write(t.move_yx(scroll.input_row, cursor_col).encode())
                    _render_toolbar(telnet_writer, scroll, stdout, autoreply_engine, toolbar_state)
                    stdout.write(t.move_yx(scroll.input_row, cursor_col).encode())
                    if telnet_writer.mode == "kludge":
                        mode_switched = True
                        server_done = True
                        return

            def _fire_resize() -> None:
                bt = _get_term()
                new_rows, new_cols = bt.height, bt.width
                if tty_shell.on_resize is not None:
                    tty_shell.on_resize(new_rows, new_cols)
                from .telopt import NAWS  # pylint: disable=import-outside-toplevel
                if (telnet_writer.local_option.enabled(NAWS)
                        and not telnet_writer.is_closing()):
                    telnet_writer._send_naws()  # pylint: disable=protected-access
                _render_input_line(editor.display, scroll, stdout)
                _render_toolbar(
                    telnet_writer, scroll, stdout, autoreply_engine, toolbar_state
                )
                cursor_col = editor.display.cursor
                stdout.write(t.move_yx(scroll.input_row, cursor_col).encode())

            async def _read_input() -> None:
                nonlocal server_done
                _update_input_style()
                _render_input_line(editor.display, scroll, stdout)
                with blessed_term.raw(), blessed_term.notify_on_resize():
                    while not server_done:
                        key = await blessed_term.async_inkey(timeout=0.1)

                        if key.name == 'RESIZE_EVENT':
                            tty_shell._resize_pending.set()
                            continue

                        if not key:
                            if tty_shell._resize_pending.is_set():
                                tty_shell._resize_pending.clear()
                                _fire_resize()
                            continue

                        if tty_shell._resize_pending.is_set():
                            tty_shell._resize_pending.clear()
                            _fire_resize()

                        action = dispatch.lookup(key)
                        if action is not None:
                            result = action()
                            if asyncio.iscoroutine(result):
                                await result
                            _update_input_style()
                            _render_input_line(editor.display, scroll, stdout)
                            _render_toolbar(
                                telnet_writer, scroll, stdout, autoreply_engine, toolbar_state
                            )
                            cursor_col = editor.display.cursor
                            stdout.write(t.move_yx(scroll.input_row, cursor_col).encode())
                            continue

                        result = editor.feed_key(key)

                        if result.eof:
                            server_done = True
                            telnet_writer.close()
                            return

                        if result.interrupt:
                            _update_input_style()
                            _render_input_line(editor.display, scroll, stdout)
                            continue

                        if result.line is not None:
                            line = result.line

                            if history_file and not telnet_writer.will_echo:
                                _save_history_entry(line, history_file)

                            is_pw = telnet_writer.will_echo
                            echo = "*" * len(line) if is_pw else line
                            stdout.write(t.restore.encode())
                            _colored = f"{t.yellow}{echo}{t.normal}\r\n"
                            stdout.write(_colored.encode())
                            replay_buf.append(_colored.encode())
                            stdout.write(t.save.encode())

                            if _ga_detected:
                                try:
                                    await asyncio.wait_for(prompt_ready.wait(), timeout=2.0)
                                except asyncio.TimeoutError:
                                    pass

                            if autoreply_engine is not None:
                                autoreply_engine.cancel()
                            _wander_task = getattr(telnet_writer, "_wander_task", None)
                            if _wander_task is not None and not _wander_task.done():
                                _wander_task.cancel()
                            _disc_task = getattr(telnet_writer, "_discover_task", None)
                            if _disc_task is not None and not _disc_task.done():
                                _disc_task.cancel()

                            parts = expand_commands(line)
                            if parts and _TRAVEL_RE.match(parts[0]):
                                remainder = await _handle_travel_commands(
                                    parts, telnet_writer, telnet_writer.log
                                )
                                if remainder:
                                    telnet_writer.write(
                                        remainder[0] + "\r\n"  # type: ignore[arg-type]
                                    )
                                    if _ga_detected:
                                        prompt_ready.clear()
                                    if len(remainder) > 1:
                                        await _send_chained(
                                            remainder, telnet_writer, telnet_writer.log
                                        )
                            elif parts:
                                telnet_writer.write(parts[0] + "\r\n")  # type: ignore[arg-type]
                                if _ga_detected:
                                    prompt_ready.clear()
                                if len(parts) > 1:
                                    await _send_chained(parts, telnet_writer, telnet_writer.log)
                            else:
                                telnet_writer.write("\r\n")  # type: ignore[arg-type]

                        if result.changed:
                            _update_input_style()
                            _render_input_line(editor.display, scroll, stdout)
                            needs_reflash = _render_toolbar(
                                telnet_writer, scroll, stdout, autoreply_engine, toolbar_state
                            )
                            if needs_reflash:
                                loop.call_later(
                                    0.12,
                                    _render_toolbar,
                                    telnet_writer,
                                    scroll,
                                    stdout,
                                    autoreply_engine,
                                    toolbar_state,
                                )
                            cursor_col = editor.display.cursor
                            stdout.write(t.move_yx(scroll.input_row, cursor_col).encode())

            try:
                await _run_repl_tasks(_read_server(), _read_input())
            finally:
                if autoreply_engine is not None:
                    autoreply_engine.cancel()
                stdout.write(CURSOR_DEFAULT.encode())
                if mode_switched:
                    _dmz_row = scroll.scroll_bottom + 1
                    stdout.write(t.save.encode())
                    stdout.write(t.move_yx(_dmz_row, 0).encode())
                    stdout.write(t.normal.encode())
                    stdout.write(t.clear_eos.encode())
                    stdout.write(t.restore.encode())

        return mode_switched
