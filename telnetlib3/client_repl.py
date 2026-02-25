"""REPL and TUI components for linemode telnet client sessions."""

# std imports
import os
import sys
import asyncio
import logging
import contextlib
import collections
from typing import TYPE_CHECKING, Any, List, Tuple, Union, Callable, Optional, Generator

# local
from .stream_reader import TelnetReader, TelnetReaderUnicode
from .stream_writer import TelnetWriter, TelnetWriterUnicode
from .session_context import SessionContext, _CommandQueue

# Re-export from sub-modules so existing ``from .client_repl import X``
# in tests and other modules continues to work without changes.
# pylint: disable=unused-import,useless-import-alias
from .client_repl_render import STOPLIGHT_WIDTH  # noqa: F401
from .client_repl_render import Stoplight  # noqa: F401
from .client_repl_render import (  # noqa: F401
    HOLD,
    PHASES,
    SEXTANT,
    WARM_UP,
    DURATION,
    IDLE_RGB,
    PEAK_RED,
    _DMZ_CHAR,
    _ELLIPSIS,
    GLOW_DOWN,
    _BAR_WIDTH,
    PEAK_GREEN,
    _FLASH_HOLD,
    CURSOR_HIDE,
    CURSOR_SHOW,
    IDLE_AR_RGB,
    PEAK_YELLOW,
    SEXTANT_BITS,
    _BAR_CAP_LEFT,
    _STYLE_NORMAL,
    _BAR_CAP_RIGHT,
    _CURSOR_STYLES,
    _FLASH_RAMP_UP,
    CURSOR_DEFAULT,
    _FLASH_DURATION,
    _FLASH_INTERVAL,
    _FLASH_RAMP_DOWN,
    _SEPARATOR_WIDTH,
    _STYLE_AUTOREPLY,
    CURSOR_STEADY_BAR,
    CURSOR_BLINKING_BAR,
    CURSOR_STEADY_BLOCK,
    _DEFAULT_CURSOR_STYLE,
    CURSOR_BLINKING_BLOCK,
    CURSOR_STEADY_UNDERLINE,
    CURSOR_BLINKING_UNDERLINE,
    ActivityDot,
    _sgr_bg,
    _sgr_fg,
    lerp_rgb,
    _dmz_line,
    _lerp_hsv,
    _wcswidth,
    _fmt_value,
    _segmented,
    _vital_bar,
    _hsv_to_rgb,
    _rgb_to_hsv,
    _flash_color,
    _make_styles,
    _ToolbarSlot,
    _vital_color,
    _layout_toolbar,
    _render_toolbar,
    _center_truncate,
    _render_input_line,
    _schedule_flash_frame,
)
from .client_repl_travel import (  # noqa: F401
    _DEFAULT_WALK_LIMIT,
    _autowander,
    _randomwalk,
    _fast_travel,
    _autodiscover,
    _handle_travel_commands,
)
from .client_repl_dialogs import (  # noqa: F401
    _show_help,
    _editor_active,
    _editor_buffer,
    _reload_macros,
    _confirm_dialog,
    _launch_tui_editor,
    _reload_autoreplies,
    _launch_room_browser,
)
from .client_repl_commands import (  # noqa: F401
    _REPEAT_RE,
    _TRAVEL_RE,
    _BACKTICK_RE,
    _MOVE_STEP_DELAY,
    _MOVE_MAX_RETRIES,
    _send_chained,
    _collapse_runs,
)
from .client_repl_commands import expand_commands as expand_commands  # noqa: F401
from .client_repl_commands import (  # noqa: F401
    _clear_command_queue,
    _render_command_queue,
    _render_active_command,
)
from .client_repl_commands import execute_macro_commands as execute_macro_commands  # noqa: F401

# pylint: enable=unused-import,useless-import-alias

if TYPE_CHECKING:
    import blessed
    import blessed.keyboard
    import blessed.line_editor

    from . import client_shell
    from .macros import Macro
    from .autoreply import AutoreplyEngine

PASSWORD_CHAR = "\u25cf"

log = logging.getLogger(__name__)


def _load_history(history: "blessed.line_editor.LineHistory", path: str) -> None:
    """
    Populate *history* entries from a newline-delimited file.

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
    """
    Append a single history *line* to the file at *path*.

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
    global blessed_term  # noqa: PLW0603
    if blessed_term is None:
        import blessed

        blessed_term = blessed.Terminal(force_styling=True)
    return blessed_term


@contextlib.contextmanager
def _blocking_fds() -> Generator[None, None, None]:
    """
    Context manager to ensure FDs 0/1/2 are blocking for a subprocess.

    asyncio's ``connect_write_pipe`` sets ``O_NONBLOCK`` on the PTY file
    description.  A Textual subprocess inherits non-blocking FDs, which can
    cause its ``WriterThread`` to silently fail mouse-enable escape sequences.
    This saves and restores the blocking state around subprocess calls.
    """
    saved = {}
    for fd in (0, 1, 2):
        try:
            saved[fd] = os.get_blocking(fd)
            if not saved[fd]:
                os.set_blocking(fd, True)
        except OSError:
            pass
    try:
        yield
    finally:
        for fd, was_blocking in saved.items():
            try:
                if not was_blocking:
                    os.set_blocking(fd, False)
            except OSError:
                pass


def _terminal_cleanup() -> str:
    """Reset SGR, cursor, alt-screen, mouse tracking, and bracketed paste."""
    t = _get_term()
    return (
        str(t.normal)
        + str(t.cursor_normal)
        + str(t.exit_fullscreen)
        + "\x1b[?1000l"  # xterm -- disable basic mouse
        + "\x1b[?1002l"  # xterm -- disable button-event mouse
        + "\x1b[?1003l"  # xterm -- disable any-event mouse
        + "\x1b[?1006l"  # xterm -- disable SGR mouse ext
        + "\x1b[?1016l"  # xterm -- disable SGR-Pixel mouse ext
        + "\x1b[?2004l"  # xterm -- disable bracketed paste
        + "\x1b[?2048l"  # xterm -- disable in-band resize
        + "\x1b[r"  # DECSTBM -- reset scroll region to default
        + "\x1b[<u"  # kitty -- disable kitty keyboard protocol
    )


def _safe_terminal_size() -> str:
    """Return ``os.get_terminal_size()`` as a string, or ``"?"`` on error."""
    try:
        sz = os.get_terminal_size()
        return f"{sz.columns}x{sz.lines}"
    except OSError:
        return "?"


# Maximum bytes retained in the output replay ring buffer for Ctrl-L repaint.
_REPLAY_BUFFER_MAX = 65536

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
    sys.stdout.write(CURSOR_HIDE)
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
        sys.stdout.write(t.move_yx(dmz, 0) + t.clear_eol + _dmz_line(_tsize.columns))
    for _r in range(_input_row, _tsize.lines):
        sys.stdout.write(t.move_yx(_r, 0) + t.clear_eol)
    # Re-enable in-band window resize notifications (DEC mode 2048) — the
    # subprocess may have reset terminal modes, disabling the notification
    # that blessed's notify_on_resize() context manager originally enabled.
    sys.stdout.write("\x1b[?2048h")
    sys.stdout.write(CURSOR_SHOW)
    sys.stdout.flush()


def _repaint_screen(
    replay_buf: Optional[OutputRingBuffer], scroll: Optional["ScrollRegion"] = None
) -> None:
    """
    Clear screen and replay recent output from the ring buffer.

    Re-establishes the DECSTBM scroll region and replays buffered output so recent MUD text
    reappears with colors intact.
    """
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
        sys.stdout.write(CURSOR_HIDE)
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
            sys.stdout.write(t.move_yx(dmz, 0) + t.clear_eol + _dmz_line(_tsize.columns))
        for _r in range(_input_row, _tsize.lines):
            sys.stdout.write(t.move_yx(_r, 0) + t.clear_eol)
        sys.stdout.write(t.move_yx(_input_row, 0))
        sys.stdout.write(CURSOR_SHOW)
        sys.stdout.flush()
    finally:
        os.set_blocking(fd, was_blocking)


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
                    (t.move_yx(dmz, 0) + t.clear_eol + _dmz_line(self._cols)).encode()
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
        from .telopt import NAWS

        rows, cols = _get_terminal_size()
        rows_cols = [rows, cols]
        scroll_region: Optional[ScrollRegion] = None

        orig_send_naws = getattr(telnet_writer, "handle_send_naws", None)

        def _adjusted_send_naws() -> Tuple[int, int]:
            if scroll_region is not None and scroll_region._active:
                _, cur_cols = _get_terminal_size()
                return (scroll_region.scroll_rows, cur_cols)
            return _get_terminal_size()

        telnet_writer.handle_send_naws = _adjusted_send_naws  # type: ignore[method-assign]

        try:
            if telnet_writer.local_option.enabled(NAWS) and not telnet_writer.is_closing():
                telnet_writer._send_naws()

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
            logger: logging.Logger,
        ) -> None:
            """Replace all macro bindings from a macro definition list."""
            from .macros import build_macro_dispatch

            macro_handlers = build_macro_dispatch(macros, writer, logger)
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
            telnet_reader,
            telnet_writer,
            tty_shell,
            stdout,
            history_file=history_file,
            banner_lines=banner_lines,
        )

    async def _repl_event_loop(
        telnet_reader: Union[TelnetReader, TelnetReaderUnicode],
        telnet_writer: Union[TelnetWriter, TelnetWriterUnicode],
        tty_shell: "client_shell.Terminal",
        stdout: asyncio.StreamWriter,
        history_file: Optional[str] = None,
        banner_lines: Optional[List[str]] = None,
    ) -> bool:
        """Unified REPL event loop using blessed LineEditor + async_inkey."""
        import blessed  # pylint: disable=import-outside-toplevel
        import blessed.line_editor  # pylint: disable=import-outside-toplevel,no-name-in-module

        import telnetlib3.client_repl_dialogs as _dialogs_mod
        from .client_shell import _transform_output, _flush_color_filter

        mode_switched = False
        loop = asyncio.get_event_loop()

        ctx: SessionContext = telnet_writer._ctx  # type: ignore[union-attr]
        _session_key = ctx.session_key
        _is_ssl = telnet_writer.get_extra_info("ssl_object") is not None
        _conn_info = _session_key + (" SSL" if _is_ssl else "")
        blessed_term = _get_term()
        _make_styles()

        replay_buf = OutputRingBuffer()

        history = blessed.line_editor.LineHistory()  # pylint: disable=no-member
        if history_file:
            _load_history(history, history_file)

        _term_cols = blessed_term.width
        editor = blessed.line_editor.LineEditor(  # pylint: disable=no-member
            history=history,
            password=bool(telnet_writer.will_echo),
            max_width=_term_cols,
            **_STYLE_NORMAL,
        )

        _stoplight = Stoplight.create()
        ctx.tx_dot = _stoplight.tx
        ctx.cx_dot = _stoplight.cx
        ctx.rx_dot = _stoplight.rx
        toolbar_state: dict[str, Any] = {"rprompt_text": _conn_info, "stoplight": _stoplight}

        dispatch = _KeyDispatch()
        macro_defs = ctx.macro_defs or None
        if macro_defs is not None:
            dispatch.set_macros(macro_defs, telnet_writer, telnet_writer.log)
        ctx.key_dispatch = dispatch

        _last_resize_size: list[int] = [0, 0]

        def _on_resize_repaint(_rows: int, _cols: int) -> None:
            if [_rows, _cols] == _last_resize_size:
                return
            _last_resize_size[:] = [_rows, _cols]
            t = _get_term()
            _sr = _scroll_ref[0]
            _reserve = _sr._reserve if _sr is not None else _RESERVE_WITH_TOOLBAR
            stdout.write(CURSOR_HIDE.encode())
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
                    stdout.write((t.move_yx(dmz, 0) + _dmz_line(_cols)).encode())
            _cs = ctx.cursor_style or _DEFAULT_CURSOR_STYLE
            stdout.write(_CURSOR_STYLES.get(_cs, CURSOR_STEADY_BLOCK).encode())
            stdout.write(CURSOR_SHOW.encode())
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
            _cursor_style_name = ctx.cursor_style or _DEFAULT_CURSOR_STYLE
            _cursor_seq = _CURSOR_STYLES.get(_cursor_style_name, CURSOR_STEADY_BLOCK)
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

            from .telopt import GA, CMD_EOR

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

            ctx.wait_for_prompt = _wait_for_prompt
            ctx.echo_command = _echo_autoreply
            ctx.prompt_ready = prompt_ready

            autoreply_engine: Optional["AutoreplyEngine"] = None
            _ar_rules_ref: object = None

            def _refresh_autoreply_engine() -> None:
                nonlocal autoreply_engine, _ar_rules_ref
                cur_rules = ctx.autoreply_rules or None
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
                    from .autoreply import AutoreplyEngine

                    autoreply_engine = AutoreplyEngine(
                        cur_rules,
                        ctx,
                        telnet_writer.log,
                        insert_fn=_insert_into_prompt,
                        echo_fn=_echo_autoreply,
                        wait_fn=_wait_for_prompt,
                    )
                    autoreply_engine.suppress_exclusive = prev_suppress
                ctx.autoreply_engine = autoreply_engine

            _refresh_autoreply_engine()

            server_done = False

            # Register builtin hotkeys.
            def _reg_close() -> None:
                nonlocal server_done
                server_done = True
                telnet_writer.close()

            dispatch.register_seq("\x1d", _reg_close)  # Ctrl+]
            dispatch.register_seq(
                "\x0c", lambda: _repaint_screen(replay_buf, scroll=scroll)
            )  # Ctrl+L

            _gmcp_keys_registered = False

            def _has_gmcp() -> bool:
                return bool(ctx.gmcp_data)

            dispatch.register(
                "KEY_F1",
                lambda: _show_help(macro_defs, replay_buf=replay_buf, has_gmcp=_has_gmcp()),
            )
            dispatch.register("KEY_F8", lambda: _launch_tui_editor("macros", ctx, replay_buf))
            dispatch.register("KEY_F9", lambda: _launch_tui_editor("autoreplies", ctx, replay_buf))

            def _toggle_autoreplies() -> None:
                if autoreply_engine is None:
                    return
                autoreply_engine.enabled = not autoreply_engine.enabled
                state = "ON" if autoreply_engine.enabled else "OFF"
                _echo_autoreply(f"AUTOREPLIES {state}")

            dispatch.register("KEY_F21", _toggle_autoreplies)  # Shift+F9

            def _discover_mode() -> None:
                if ctx.discover_active:
                    task = ctx.discover_task
                    if task is not None:
                        task.cancel()
                    return
                from .rooms import load_prefs, save_prefs

                skey = ctx.session_key
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
                t = asyncio.ensure_future(_autodiscover(ctx, telnet_writer.log))
                ctx.discover_task = t

            def _wander_mode() -> None:
                if ctx.wander_active:
                    task = ctx.wander_task
                    if task is not None:
                        task.cancel()
                    return
                from .rooms import load_prefs, save_prefs

                skey = ctx.session_key
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
                task = asyncio.ensure_future(_autowander(ctx, telnet_writer.log))
                ctx.wander_task = task

            def _randomwalk_mode() -> None:
                if ctx.randomwalk_active:
                    task = ctx.randomwalk_task
                    if task is not None:
                        task.cancel()
                    return
                from .rooms import load_prefs, save_prefs

                skey = ctx.session_key
                prefs = load_prefs(skey) if skey else {}
                if not prefs.get("skip_randomwalk_confirm"):
                    ok, dont_ask = _confirm_dialog(
                        "Random Walk",
                        "Random walk explores rooms by picking "
                        "random exits, preferring unvisited rooms. "
                        "It never returns through the entrance you "
                        "came from. Autoreplies fire in each room. "
                        "Stops when all reachable rooms are visited.",
                        replay_buf=replay_buf,
                    )
                    if not ok:
                        return
                    if dont_ask and skey:
                        prefs["skip_randomwalk_confirm"] = True
                        save_prefs(skey, prefs)
                task = asyncio.ensure_future(_randomwalk(ctx, telnet_writer.log))
                ctx.randomwalk_task = task

            def _register_gmcp_keys() -> None:
                nonlocal _gmcp_keys_registered
                if _gmcp_keys_registered:
                    return
                _gmcp_keys_registered = True
                dispatch.register("KEY_F3", _randomwalk_mode)
                dispatch.register("KEY_F4", _discover_mode)
                dispatch.register("KEY_F5", _wander_mode)
                dispatch.register("KEY_F7", lambda: _launch_room_browser(ctx, replay_buf))

            ctx.on_gmcp_ready = _register_gmcp_keys

            _last_input_style: list[Optional[dict[str, str]]] = [None]

            def _update_input_style() -> None:
                editor.set_password_mode(bool(telnet_writer.will_echo))
                if ctx.command_queue is not None:
                    return
                if ctx.active_command is not None:
                    return
                engine = autoreply_engine
                ar_active = engine is not None and (engine.exclusive_active or engine.reply_pending)
                wander = ctx.wander_active
                disc = ctx.discover_active
                rwalk = ctx.randomwalk_active
                style = (
                    _STYLE_AUTOREPLY if (wander or disc or rwalk or ar_active) else _STYLE_NORMAL
                )
                changed = _last_input_style[0] is not style
                _last_input_style[0] = style
                for attr, val in style.items():
                    setattr(editor, attr, val)
                if changed:
                    _render_input_line(editor.display, scroll, stdout)
                    _active = style is _STYLE_AUTOREPLY
                    _dmz_row = scroll.scroll_bottom + 1
                    if _dmz_row < scroll.input_row:
                        stdout.write(
                            (t.move_yx(_dmz_row, 0) + _dmz_line(scroll._cols, _active)).encode()
                        )

            _rx_dot = _stoplight.rx
            _tx_dot = _stoplight.tx

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
                    _rx_dot.trigger()
                    if isinstance(out, bytes):
                        out = out.decode("utf-8", errors="replace")
                    out = _transform_output(out, telnet_writer, True)
                    _refresh_autoreply_engine()
                    if autoreply_engine is not None:
                        autoreply_engine.feed(out)
                        if _prompt_pending:
                            _prompt_pending = False
                            autoreply_engine.on_prompt()
                    if _dialogs_mod._editor_active:
                        _dialogs_mod._editor_buffer.append(out.encode())
                        continue
                    stdout.write(CURSOR_HIDE.encode())
                    stdout.write(t.restore.encode())
                    if _dialogs_mod._editor_buffer:
                        for chunk in _dialogs_mod._editor_buffer:
                            stdout.write(chunk)
                            replay_buf.append(chunk)
                        _dialogs_mod._editor_buffer.clear()
                    encoded = _esc_hold + out.encode()
                    encoded, _esc_hold = _split_incomplete_esc(encoded)
                    if encoded:
                        stdout.write(encoded)
                        replay_buf.append(encoded)
                    stdout.write(t.save.encode())
                    _update_input_style()
                    _render_input_line(editor.display, scroll, stdout)
                    cursor_col = editor.display.cursor
                    stdout.write(t.move_yx(scroll.input_row, cursor_col).encode())
                    needs_reflash = _render_toolbar(
                        ctx, scroll, stdout, autoreply_engine, toolbar_state
                    )
                    if needs_reflash and not toolbar_state.get("_flash_active"):
                        toolbar_state["_flash_active"] = True
                        _schedule_flash_frame(
                            loop, ctx, scroll, stdout, autoreply_engine, toolbar_state, editor, t
                        )
                    stdout.write(t.move_yx(scroll.input_row, cursor_col).encode())
                    stdout.write(CURSOR_SHOW.encode())
                    if telnet_writer.mode == "kludge":
                        mode_switched = True
                        server_done = True
                        return

            def _fire_resize() -> None:
                bt = _get_term()
                new_rows, new_cols = bt.height, bt.width
                if tty_shell.on_resize is not None:
                    tty_shell.on_resize(new_rows, new_cols)
                from .telopt import NAWS

                if telnet_writer.local_option.enabled(NAWS) and not telnet_writer.is_closing():
                    telnet_writer._send_naws()
                stdout.write(CURSOR_HIDE.encode())
                _render_input_line(editor.display, scroll, stdout)
                _render_toolbar(ctx, scroll, stdout, autoreply_engine, toolbar_state)
                cursor_col = editor.display.cursor
                stdout.write(t.move_yx(scroll.input_row, cursor_col).encode())
                stdout.write(CURSOR_SHOW.encode())

            async def _read_input() -> None:
                nonlocal server_done
                _update_input_style()
                _render_input_line(editor.display, scroll, stdout)
                _chained_task: Optional[asyncio.Task[None]] = None
                with blessed_term.raw(), blessed_term.notify_on_resize():
                    while not server_done:
                        key = await blessed_term.async_inkey(timeout=0.1)

                        if key.name == "RESIZE_EVENT":
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

                        # Cancel active command queue on any keypress.
                        _cq = ctx.command_queue
                        if _cq is not None and not _cq.cancelled:
                            _cq.cancelled = True
                            _cq.cancel_event.set()
                            if _chained_task is not None and not _chained_task.done():
                                _chained_task.cancel()
                            ctx.command_queue = None
                            stdout.write(CURSOR_HIDE.encode())
                            _render_input_line(editor.display, scroll, stdout)
                            cursor_col = editor.display.cursor
                            stdout.write(t.move_yx(scroll.input_row, cursor_col).encode())
                            stdout.write(CURSOR_SHOW.encode())
                            continue

                        # Cancel active walk command on any keypress.
                        _ac = ctx.active_command
                        if _ac is not None:
                            ctx.active_command = None
                            _cancel_labels = []
                            for _wname, _wtask in (
                                ("AUTOWANDER", ctx.wander_task),
                                ("AUTODISCOVER", ctx.discover_task),
                                ("RANDOMWALK", ctx.randomwalk_task),
                            ):
                                if _wtask is not None and not _wtask.done():
                                    _wtask.cancel()
                                    _cancel_labels.append(_wname)
                            if _cancel_labels and _echo_autoreply is not None:
                                _label = _cancel_labels[0]
                                _echo_autoreply(f"{_label}: cancelled by keypress" f" {key!r}")
                            stdout.write(CURSOR_HIDE.encode())
                            _update_input_style()
                            _render_input_line(editor.display, scroll, stdout)
                            cursor_col = editor.display.cursor
                            stdout.write(t.move_yx(scroll.input_row, cursor_col).encode())
                            stdout.write(CURSOR_SHOW.encode())
                            continue

                        action = dispatch.lookup(key)
                        if action is not None:
                            result = action()
                            if asyncio.iscoroutine(result):
                                await result
                            stdout.write(CURSOR_HIDE.encode())
                            _update_input_style()
                            _render_input_line(editor.display, scroll, stdout)
                            _render_toolbar(ctx, scroll, stdout, autoreply_engine, toolbar_state)
                            cursor_col = editor.display.cursor
                            stdout.write(t.move_yx(scroll.input_row, cursor_col).encode())
                            stdout.write(CURSOR_SHOW.encode())
                            continue

                        result = editor.feed_key(key)

                        if result.eof:
                            server_done = True
                            telnet_writer.close()
                            return

                        if result.interrupt:
                            stdout.write(CURSOR_HIDE.encode())
                            _update_input_style()
                            _render_input_line(editor.display, scroll, stdout)
                            cursor_col = editor.display.cursor
                            stdout.write(t.move_yx(scroll.input_row, cursor_col).encode())
                            stdout.write(CURSOR_SHOW.encode())
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
                            _wander_task = ctx.wander_task
                            if _wander_task is not None and not _wander_task.done():
                                _wander_task.cancel()
                            _disc_task = ctx.discover_task
                            if _disc_task is not None and not _disc_task.done():
                                _disc_task.cancel()
                            _rw_task = ctx.randomwalk_task
                            if _rw_task is not None and not _rw_task.done():
                                _rw_task.cancel()

                            parts = expand_commands(line)
                            if parts and _TRAVEL_RE.match(parts[0]):
                                remainder = await _handle_travel_commands(
                                    parts, ctx, telnet_writer.log
                                )
                                if remainder:
                                    _tx_dot.trigger()
                                    telnet_writer.write(
                                        remainder[0] + "\r\n"  # type: ignore[arg-type]
                                    )
                                    if _ga_detected:
                                        prompt_ready.clear()
                                    if len(remainder) > 1:
                                        _q = _CommandQueue(
                                            remainder,
                                            render=lambda: _render_command_queue(
                                                ctx.command_queue, scroll, stdout
                                            ),
                                        )
                                        ctx.command_queue = _q
                                        _q.render()
                                        _chained_task = asyncio.ensure_future(
                                            _send_chained(
                                                remainder, ctx, telnet_writer.log, queue=_q
                                            )
                                        )
                                        _chained_task.add_done_callback(
                                            lambda _f: _clear_command_queue(ctx)
                                        )
                            elif parts:
                                _tx_dot.trigger()
                                telnet_writer.write(parts[0] + "\r\n")  # type: ignore[arg-type]
                                if _ga_detected:
                                    prompt_ready.clear()
                                if len(parts) > 1:
                                    _q = _CommandQueue(
                                        parts,
                                        render=lambda: _render_command_queue(
                                            ctx.command_queue, scroll, stdout
                                        ),
                                    )
                                    ctx.command_queue = _q
                                    _q.render()
                                    _chained_task = asyncio.ensure_future(
                                        _send_chained(parts, ctx, telnet_writer.log, queue=_q)
                                    )
                                    _chained_task.add_done_callback(
                                        lambda _f: _clear_command_queue(ctx)
                                    )
                            else:
                                _tx_dot.trigger()
                                telnet_writer.write("\r\n")  # type: ignore[arg-type]

                        if result.changed:
                            stdout.write(CURSOR_HIDE.encode())
                            _cq2 = ctx.command_queue
                            _ac2 = ctx.active_command
                            if _cq2 is not None:
                                _render_command_queue(_cq2, scroll, stdout)
                            elif _ac2 is not None:
                                _render_active_command(_ac2, scroll, stdout)
                            else:
                                _update_input_style()
                                _render_input_line(editor.display, scroll, stdout)
                            needs_reflash = _render_toolbar(
                                ctx, scroll, stdout, autoreply_engine, toolbar_state
                            )
                            if needs_reflash and not toolbar_state.get("_flash_active"):
                                toolbar_state["_flash_active"] = True
                                _schedule_flash_frame(
                                    loop,
                                    ctx,
                                    scroll,
                                    stdout,
                                    autoreply_engine,
                                    toolbar_state,
                                    editor,
                                    t,
                                )
                            cursor_col = editor.display.cursor
                            stdout.write(t.move_yx(scroll.input_row, cursor_col).encode())
                            stdout.write(CURSOR_SHOW.encode())

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
