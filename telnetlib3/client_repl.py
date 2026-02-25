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
    ToolbarRenderer,
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
    _center_truncate,
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
    blessed_term = _get_term()
    return (
        str(blessed_term.normal)
        + str(blessed_term.cursor_normal)
        + str(blessed_term.exit_fullscreen)
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

__all__ = ("ScrollRegion", "ReplSession", "repl_event_loop", "_split_incomplete_esc")


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
    blessed_term = _get_term()
    sys.stdout.write(CURSOR_HIDE)
    sys.stdout.write(_terminal_cleanup())
    try:
        tsize = os.get_terminal_size()
    except OSError:
        tsize = os.terminal_size((80, 24))
    scroll_bottom = max(0, tsize.lines - reserve - 2)
    sys.stdout.write(blessed_term.clear + blessed_term.home)
    sys.stdout.write(blessed_term.change_scroll_region(0, scroll_bottom))
    sys.stdout.write(blessed_term.move_yx(0, 0))
    if replay_buf is not None:
        data = replay_buf.replay()
        if data:
            sys.stdout.write(data.decode("utf-8", errors="replace"))
    sys.stdout.write(blessed_term.save)
    dmz = scroll_bottom + 1
    input_row = tsize.lines - reserve
    if dmz < input_row:
        sys.stdout.write(
            blessed_term.move_yx(dmz, 0) + blessed_term.clear_eol + _dmz_line(tsize.columns)
        )
    for r in range(input_row, tsize.lines):
        sys.stdout.write(blessed_term.move_yx(r, 0) + blessed_term.clear_eol)
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
        tsize = os.get_terminal_size()
    except OSError:
        return
    if scroll is not None:
        scroll.update_size(tsize.lines, tsize.columns)
    fd = sys.stdout.fileno()
    was_blocking = os.get_blocking(fd)
    os.set_blocking(fd, True)
    try:
        blessed_term = _get_term()
        scroll_bottom = max(0, tsize.lines - reserve - 2)
        sys.stdout.write(CURSOR_HIDE)
        sys.stdout.write(blessed_term.clear + blessed_term.home)
        sys.stdout.write(blessed_term.change_scroll_region(0, scroll_bottom))
        sys.stdout.write(blessed_term.move_yx(0, 0))
        if replay_buf is not None:
            data = replay_buf.replay()
            if data:
                sys.stdout.write(data.decode("utf-8", errors="replace"))
        sys.stdout.write(blessed_term.save)
        dmz = scroll_bottom + 1
        input_row = tsize.lines - reserve
        if dmz < input_row:
            sys.stdout.write(
                blessed_term.move_yx(dmz, 0) + blessed_term.clear_eol + _dmz_line(tsize.columns)
            )
        for r in range(input_row, tsize.lines):
            sys.stdout.write(blessed_term.move_yx(r, 0) + blessed_term.clear_eol)
        sys.stdout.write(blessed_term.move_yx(input_row, 0))
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
            blessed_term = _get_term()
            if self._active:
                old_bottom = self.scroll_bottom
                self._stdout.write(blessed_term.move_yx(old_bottom, 0).encode())
                self._stdout.write(b"\n" * extra)
            self._reserve = new_reserve
            if self._active:
                for r in range(old_input_row, old_input_row + new_reserve):
                    self._stdout.write(
                        (blessed_term.move_yx(r, 0) + blessed_term.clear_eol).encode()
                    )
                self._set_scroll_region()
                self._stdout.write(blessed_term.restore.encode())
                if extra > 0:
                    self._stdout.write(blessed_term.move_up(extra).encode())
                self._stdout.write(blessed_term.save.encode())
                for r in range(self.input_row, self.input_row + new_reserve):
                    self._stdout.write(
                        (blessed_term.move_yx(r, 0) + blessed_term.clear_eol).encode()
                    )
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
            blessed_term = _get_term()
            if self._active:
                for r in range(old_input_row, old_input_row + self._reserve):
                    self._stdout.write(
                        (blessed_term.move_yx(r, 0) + blessed_term.clear_eol).encode()
                    )
                self._set_scroll_region()
                self._stdout.write(blessed_term.save.encode())
                for r in range(self.input_row, self.input_row + self._reserve):
                    self._stdout.write(
                        (blessed_term.move_yx(r, 0) + blessed_term.clear_eol).encode()
                    )
                self._dirty = True

        def _set_scroll_region(self) -> None:
            """Write DECSTBM escape sequence to set scroll region."""
            blessed_term = _get_term()
            bottom = self.scroll_bottom
            self._stdout.write(blessed_term.change_scroll_region(0, bottom).encode())
            dmz = bottom + 1
            if dmz < self.input_row:
                self._stdout.write(
                    (
                        blessed_term.move_yx(dmz, 0)
                        + blessed_term.clear_eol
                        + _dmz_line(self._cols)
                    ).encode()
                )
            self._stdout.write(blessed_term.move_yx(bottom, 0).encode())

        def _reset_scroll_region(self) -> None:
            """Reset scroll region to full terminal height."""
            blessed_term = _get_term()
            self._stdout.write(blessed_term.change_scroll_region(0, self._rows - 1).encode())

        def save_and_goto_input(self) -> None:
            """Save cursor, move to input line, clear it."""
            blessed_term = _get_term()
            self._stdout.write(blessed_term.save.encode())
            self._stdout.write(blessed_term.move_yx(self.input_row, 0).encode())
            self._stdout.write(blessed_term.clear_eol.encode())

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
            blessed_term = _get_term()
            self._stdout.write(blessed_term.move_yx(self._rows - 1, 0).encode())

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
            self, macros: "list[Macro]", ctx: "SessionContext", logger: logging.Logger
        ) -> None:
            """Replace all macro bindings from a macro definition list."""
            from .macros import build_macro_dispatch

            macro_handlers = build_macro_dispatch(macros, ctx, logger)
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

    class ReplSession:
        """
        Encapsulates the REPL event loop state and logic.

        Replaces the former ``_repl_event_loop()`` monolithic function,
        converting captured locals and closures into explicit instance
        attributes and methods.

        :param telnet_reader: Server-side reader stream.
        :param telnet_writer: Server-side writer stream.
        :param tty_shell: ``Terminal`` instance from ``client_shell``.
        :param stdout: asyncio StreamWriter for local terminal output.
        :param history_file: Optional path for persistent line history.
        :param banner_lines: Lines to display after the scroll region is active.
        """

        def __init__(
            self,
            telnet_reader: Union[TelnetReader, TelnetReaderUnicode],
            telnet_writer: Union[TelnetWriter, TelnetWriterUnicode],
            tty_shell: "client_shell.Terminal",
            stdout: asyncio.StreamWriter,
            history_file: Optional[str] = None,
            banner_lines: Optional[List[str]] = None,
        ) -> None:
            self.telnet_reader = telnet_reader
            self.telnet_writer = telnet_writer
            self.tty_shell = tty_shell
            self.stdout = stdout
            self.history_file = history_file
            self.banner_lines = banner_lines

            self.ctx: SessionContext = telnet_writer._ctx
            self.is_ssl = telnet_writer.get_extra_info("ssl_object") is not None
            self.conn_info = self.ctx.session_key + (" SSL" if self.is_ssl else "")

            self.mode_switched = False
            self.server_done = False
            self.ga_detected = False
            self.prompt_pending = False
            self.gmcp_keys_registered = False
            self._last_resize_size: list[int] = [0, 0]
            self._last_input_style: Optional[dict[str, str]] = None
            self.scroll: Optional[ScrollRegion] = None
            self.autoreply_engine: Optional["AutoreplyEngine"] = None
            self.ar_rules_ref: object = None
            self.prompt_ready = asyncio.Event()
            self.prompt_ready.set()

            # Late-initialized in _init_* methods.
            self.blessed_term: "blessed.Terminal" = None  # type: ignore[assignment]
            self.replay_buf: OutputRingBuffer = None  # type: ignore[assignment]
            self.history: "blessed.line_editor.LineHistory" = None  # type: ignore[assignment]
            self.editor: "blessed.line_editor.LineEditor" = None  # type: ignore[assignment]
            self.stoplight: Stoplight = None  # type: ignore[assignment]
            self.toolbar: ToolbarRenderer = None  # type: ignore[assignment]
            self.dispatch: _KeyDispatch = None  # type: ignore[assignment]
            self.macro_defs: "Optional[list[Macro]]" = None
            self.loop: asyncio.AbstractEventLoop = None  # type: ignore[assignment]
            self._dialogs_mod: Any = None

        def _init_terminal(self) -> None:
            """Import blessed, create terminal singleton, styles, replay buffer."""
            import telnetlib3.client_repl_dialogs as _dialogs_mod

            self._dialogs_mod = _dialogs_mod
            self.loop = asyncio.get_event_loop()
            self.blessed_term = _get_term()
            _make_styles()
            self.replay_buf = OutputRingBuffer()

        def _init_editor(self) -> None:
            """Create line history and editor."""
            import blessed.line_editor  # pylint: disable=import-outside-toplevel,no-name-in-module

            self.history = blessed.line_editor.LineHistory()  # pylint: disable=no-member
            if self.history_file:
                _load_history(self.history, self.history_file)

            term_cols = self.blessed_term.width
            self.editor = blessed.line_editor.LineEditor(  # pylint: disable=no-member
                history=self.history,
                password=bool(self.telnet_writer.will_echo),
                max_width=term_cols,
                **_STYLE_NORMAL,
            )

        def _init_ui(self) -> None:
            """Create stoplight, toolbar, key dispatch, macros."""
            self.stoplight = Stoplight.create()
            self.ctx.tx_dot = self.stoplight.tx
            self.ctx.cx_dot = self.stoplight.cx
            self.ctx.rx_dot = self.stoplight.rx

            self.dispatch = _KeyDispatch()
            self.macro_defs = self.ctx.macro_defs or None
            if self.macro_defs is not None:
                self.dispatch.set_macros(self.macro_defs, self.ctx, self.telnet_writer.log)
            self.ctx.key_dispatch = self.dispatch

        def _echo_autoreply(self, cmd: str) -> None:
            """Echo an autoreply command into the scroll region."""
            assert self.scroll is not None
            self.stdout.write(self.blessed_term.restore.encode())
            colored = f"{self.blessed_term.cyan}{cmd}" f"{self.blessed_term.normal}\r\n"
            self.stdout.write(colored.encode())
            self.replay_buf.append(colored.encode())
            self.stdout.write(self.blessed_term.save.encode())
            cursor_col = self.editor.display.cursor
            self.stdout.write(self.blessed_term.move_yx(self.scroll.input_row, cursor_col).encode())

        def _insert_into_prompt(self, text: str) -> None:
            """Insert text into the line editor buffer."""
            self.editor.insert_text(text)

        def _on_prompt_signal(self, _cmd: bytes) -> None:
            """Handle GA / EOR prompt signals."""
            self.ga_detected = True
            self.prompt_ready.set()
            self.prompt_pending = True
            self.telnet_reader._wakeup_waiter()

        async def _wait_for_prompt(self) -> None:
            """Wait for a prompt signal if GA has been detected."""
            if not self.ga_detected:
                return
            try:
                await asyncio.wait_for(self.prompt_ready.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                pass
            self.prompt_ready.clear()

        def _refresh_autoreply_engine(self) -> None:
            """Rebuild the autoreply engine when rules change."""
            cur_rules = self.ctx.autoreply_rules or None
            if cur_rules is self.ar_rules_ref:
                return
            self.ar_rules_ref = cur_rules
            prev_suppress = (
                self.autoreply_engine.suppress_exclusive
                if self.autoreply_engine is not None
                else False
            )
            if self.autoreply_engine is not None:
                self.autoreply_engine.cancel()
                self.autoreply_engine = None
            if cur_rules:
                from .autoreply import AutoreplyEngine

                self.autoreply_engine = AutoreplyEngine(
                    cur_rules,
                    self.ctx,
                    self.telnet_writer.log,
                    insert_fn=self._insert_into_prompt,
                    echo_fn=self._echo_autoreply,
                    wait_fn=self._wait_for_prompt,
                )
                self.autoreply_engine.suppress_exclusive = prev_suppress
            self.ctx.autoreply_engine = self.autoreply_engine

        def _reg_close(self) -> None:
            """Handle Ctrl+] — close the connection."""
            self.server_done = True
            self.telnet_writer.close()

        def _has_gmcp(self) -> bool:
            """Return whether GMCP data is available."""
            return bool(self.ctx.gmcp_data)

        def _toggle_autoreplies(self) -> None:
            """Toggle the autoreply engine on/off."""
            if self.autoreply_engine is None:
                return
            self.autoreply_engine.enabled = not self.autoreply_engine.enabled
            state = "ON" if self.autoreply_engine.enabled else "OFF"
            self._echo_autoreply(f"AUTOREPLIES {state}")

        def _discover_mode(self) -> None:
            """Launch or cancel autodiscover mode."""
            if self.ctx.discover_active:
                task = self.ctx.discover_task
                if task is not None:
                    task.cancel()
                return
            from .rooms import load_prefs, save_prefs

            skey = self.ctx.session_key
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
                    replay_buf=self.replay_buf,
                )
                if not ok:
                    return
                if dont_ask and skey:
                    prefs["skip_autodiscover_confirm"] = True
                    save_prefs(skey, prefs)
            t = asyncio.ensure_future(_autodiscover(self.ctx, self.telnet_writer.log))
            self.ctx.discover_task = t

        def _wander_mode(self) -> None:
            """Launch or cancel autowander mode."""
            if self.ctx.wander_active:
                task = self.ctx.wander_task
                if task is not None:
                    task.cancel()
                return
            from .rooms import load_prefs, save_prefs

            skey = self.ctx.session_key
            prefs = load_prefs(skey) if skey else {}
            if not prefs.get("skip_autowander_confirm"):
                ok, dont_ask = _confirm_dialog(
                    "Autowander",
                    "Autowander visits all rooms with the same "
                    "name as the current room using slow travel. "
                    "Autoreplies fire in each room visited. The "
                    "route is optimised to minimise backtracking.",
                    replay_buf=self.replay_buf,
                )
                if not ok:
                    return
                if dont_ask and skey:
                    prefs["skip_autowander_confirm"] = True
                    save_prefs(skey, prefs)
            task = asyncio.ensure_future(_autowander(self.ctx, self.telnet_writer.log))
            self.ctx.wander_task = task

        def _randomwalk_mode(self) -> None:
            """Launch or cancel random walk mode."""
            if self.ctx.randomwalk_active:
                task = self.ctx.randomwalk_task
                if task is not None:
                    task.cancel()
                return
            from .rooms import load_prefs, save_prefs

            skey = self.ctx.session_key
            prefs = load_prefs(skey) if skey else {}
            if not prefs.get("skip_randomwalk_confirm"):
                ok, dont_ask = _confirm_dialog(
                    "Random Walk",
                    "Random walk explores rooms by picking "
                    "random exits, preferring unvisited rooms. "
                    "It never returns through the entrance you "
                    "came from. Autoreplies fire in each room. "
                    "Stops when all reachable rooms are visited.",
                    replay_buf=self.replay_buf,
                )
                if not ok:
                    return
                if dont_ask and skey:
                    prefs["skip_randomwalk_confirm"] = True
                    save_prefs(skey, prefs)
            task = asyncio.ensure_future(_randomwalk(self.ctx, self.telnet_writer.log))
            self.ctx.randomwalk_task = task

        def _register_gmcp_keys(self) -> None:
            """Register GMCP-dependent hotkeys (F3-F7) once."""
            if self.gmcp_keys_registered:
                return
            self.gmcp_keys_registered = True
            self.dispatch.register("KEY_F3", self._randomwalk_mode)
            self.dispatch.register("KEY_F4", self._discover_mode)
            self.dispatch.register("KEY_F5", self._wander_mode)
            self.dispatch.register(
                "KEY_F7", lambda: _launch_room_browser(self.ctx, self.replay_buf)
            )

        def _update_input_style(self) -> None:
            """Update editor style based on autoreply / walk state."""
            assert self.scroll is not None
            self.editor.set_password_mode(bool(self.telnet_writer.will_echo))
            engine = self.autoreply_engine
            ar_active = engine is not None and (engine.exclusive_active or engine.reply_pending)
            wander = self.ctx.wander_active
            disc = self.ctx.discover_active
            rwalk = self.ctx.randomwalk_active
            style = _STYLE_AUTOREPLY if (wander or disc or rwalk or ar_active) else _STYLE_NORMAL
            changed = self._last_input_style is not style
            self._last_input_style = style
            for attr, val in style.items():
                setattr(self.editor, attr, val)
            if changed:
                active = style is _STYLE_AUTOREPLY
                dmz_row = self.scroll.scroll_bottom + 1
                if dmz_row < self.scroll.input_row:
                    self.stdout.write(
                        (
                            self.blessed_term.move_yx(dmz_row, 0)
                            + _dmz_line(self.scroll._cols, active)
                        ).encode()
                    )
                if self.ctx.command_queue is None and self.ctx.active_command is None:
                    self.stdout.write(
                        self.editor.render(
                            self.blessed_term, self.scroll.input_row, self.blessed_term.width
                        ).encode()
                    )

        def _on_resize_repaint(self, _rows: int, _cols: int) -> None:
            """Repaint screen after terminal resize."""
            if [_rows, _cols] == self._last_resize_size:
                return
            self._last_resize_size[:] = [_rows, _cols]
            bt = _get_term()
            sr = self.scroll
            reserve = sr._reserve if sr is not None else _RESERVE_WITH_TOOLBAR
            self.stdout.write(CURSOR_HIDE.encode())
            self.stdout.write((bt.clear + bt.home + bt.move_yx(0, 0)).encode())
            data = self.replay_buf.replay()
            if data:
                self.stdout.write(data)
            self.stdout.write(bt.save.encode())
            input_row = _rows - reserve
            for r in range(input_row, _rows):
                self.stdout.write((bt.move_yx(r, 0) + bt.clear_eol).encode())
            if sr is not None:
                dmz = sr.scroll_bottom + 1
                if dmz < sr.input_row:
                    self.stdout.write((bt.move_yx(dmz, 0) + _dmz_line(_cols)).encode())
            cs = self.ctx.cursor_style or _DEFAULT_CURSOR_STYLE
            self.stdout.write(_CURSOR_STYLES.get(cs, CURSOR_STEADY_BLOCK).encode())
            self.stdout.write(CURSOR_SHOW.encode())
            self.editor.max_width = _cols

        def _fire_resize(self) -> None:
            """Handle resize: update scroll region, NAWS, re-render UI."""
            assert self.scroll is not None
            bt = _get_term()
            new_rows, new_cols = bt.height, bt.width
            if self.tty_shell.on_resize is not None:
                self.tty_shell.on_resize(new_rows, new_cols)
            from .telopt import NAWS

            if (
                self.telnet_writer.local_option.enabled(NAWS)
                and not self.telnet_writer.is_closing()
            ):
                self.telnet_writer._send_naws()
            self.stdout.write(CURSOR_HIDE.encode())
            self.stdout.write(self.editor.render(bt, self.scroll.input_row, bt.width).encode())
            self.toolbar.render(self.autoreply_engine)
            cursor_col = self.editor.display.cursor
            self.stdout.write(bt.move_yx(self.scroll.input_row, cursor_col).encode())
            self.stdout.write(CURSOR_SHOW.encode())

        def _register_callbacks(self) -> None:
            """Wire up IAC callbacks, hotkeys, and context hooks."""
            from .telopt import GA, CMD_EOR

            self.telnet_writer.set_iac_callback(GA, self._on_prompt_signal)
            self.telnet_writer.set_iac_callback(CMD_EOR, self._on_prompt_signal)

            self.ctx.wait_for_prompt = self._wait_for_prompt
            self.ctx.echo_command = self._echo_autoreply
            self.ctx.prompt_ready = self.prompt_ready

            self._refresh_autoreply_engine()

            self.dispatch.register_seq("\x1d", self._reg_close)  # Ctrl+]
            assert self.scroll is not None
            scroll = self.scroll
            replay_buf = self.replay_buf
            self.dispatch.register_seq(
                "\x0c", lambda: _repaint_screen(replay_buf, scroll=scroll)
            )  # Ctrl+L

            self.dispatch.register(
                "KEY_F1",
                lambda: _show_help(
                    self.macro_defs, replay_buf=self.replay_buf, has_gmcp=self._has_gmcp()
                ),
            )
            self.dispatch.register(
                "KEY_F8", lambda: _launch_tui_editor("macros", self.ctx, self.replay_buf)
            )
            self.dispatch.register(
                "KEY_F9", lambda: _launch_tui_editor("autoreplies", self.ctx, self.replay_buf)
            )
            self.dispatch.register("KEY_F21", self._toggle_autoreplies)  # Shift+F9

            self.ctx.on_gmcp_ready = self._register_gmcp_keys

        def _submit_command_queue(
            self, commands: list[str], chained_task_ref: list[Optional["asyncio.Task[None]"]]
        ) -> None:
            """Create a command queue and start chained send."""
            assert self.scroll is not None
            scroll = self.scroll
            q = _CommandQueue(
                commands,
                render=lambda: _render_command_queue(self.ctx.command_queue, scroll, self.stdout),
            )
            self.ctx.command_queue = q
            q.render()
            task = asyncio.ensure_future(
                _send_chained(commands, self.ctx, self.telnet_writer.log, queue=q)
            )
            task.add_done_callback(lambda _f: _clear_command_queue(self.ctx))
            chained_task_ref[0] = task

        async def _read_server(self) -> None:
            """Read and display server output until EOF or kludge switch."""
            from .client_shell import _transform_output, _flush_color_filter

            assert self.scroll is not None
            scroll = self.scroll
            bt = self.blessed_term
            esc_hold = b""
            rx_dot = self.stoplight.rx
            while not self.server_done:
                out = await self.telnet_reader.read(2**24)
                if not out:
                    if self.telnet_reader.at_eof():
                        self.server_done = True
                        if esc_hold:
                            self.stdout.write(bt.restore.encode())
                            self.stdout.write(esc_hold)
                            self.replay_buf.append(esc_hold)
                            self.stdout.write(bt.save.encode())
                        _flush_color_filter(self.telnet_writer, self.stdout)
                        self.stdout.write(bt.restore.encode())
                        self.stdout.write(b"\r\nConnection closed by foreign host.\r\n")
                        return
                    if self.prompt_pending and self.autoreply_engine is not None:
                        self.prompt_pending = False
                        self.autoreply_engine.on_prompt()
                        self._update_input_style()
                    continue
                rx_dot.trigger()
                if isinstance(out, bytes):
                    out = out.decode("utf-8", errors="replace")
                out = _transform_output(out, self.telnet_writer, True)
                self._refresh_autoreply_engine()
                if self.autoreply_engine is not None:
                    self.autoreply_engine.feed(out)
                    if self.prompt_pending:
                        self.prompt_pending = False
                        self.autoreply_engine.on_prompt()
                if self._dialogs_mod._editor_active:
                    self._dialogs_mod._editor_buffer.append(out.encode())
                    continue
                self.stdout.write(CURSOR_HIDE.encode())
                self.stdout.write(bt.restore.encode())
                if self._dialogs_mod._editor_buffer:
                    for chunk in self._dialogs_mod._editor_buffer:
                        self.stdout.write(chunk)
                        self.replay_buf.append(chunk)
                    self._dialogs_mod._editor_buffer.clear()
                encoded = esc_hold + out.encode()
                encoded, esc_hold = _split_incomplete_esc(encoded)
                if encoded:
                    self.stdout.write(encoded)
                    self.replay_buf.append(encoded)
                self.stdout.write(bt.save.encode())
                self._update_input_style()
                self.stdout.write(self.editor.render(bt, scroll.input_row, bt.width).encode())
                cursor_col = self.editor.display.cursor
                self.stdout.write(bt.move_yx(scroll.input_row, cursor_col).encode())
                needs_reflash = self.toolbar.render(self.autoreply_engine)
                if needs_reflash and not self.toolbar.flash_active:
                    self.toolbar.flash_active = True
                    self.toolbar.schedule_flash(self.loop, self.autoreply_engine, self.editor, bt)
                self.stdout.write(bt.move_yx(scroll.input_row, cursor_col).encode())
                self.stdout.write(CURSOR_SHOW.encode())
                if self.telnet_writer.mode == "kludge":
                    self.mode_switched = True
                    self.server_done = True
                    return

        async def _read_input(self) -> None:
            """Read keyboard input until server done or EOF."""
            assert self.scroll is not None
            scroll = self.scroll
            bt = self.blessed_term
            tx_dot = self.stoplight.tx
            self._update_input_style()
            self.stdout.write(self.editor.render(bt, scroll.input_row, bt.width).encode())
            chained_task_ref: list[Optional[asyncio.Task[None]]] = [None]
            with bt.raw(), bt.notify_on_resize():
                while not self.server_done:
                    key = await bt.async_inkey(timeout=0.1)

                    if key.name == "RESIZE_EVENT":
                        self.tty_shell._resize_pending.set()
                        continue

                    if not key:
                        if self.tty_shell._resize_pending.is_set():
                            self.tty_shell._resize_pending.clear()
                            self._fire_resize()
                        continue

                    if self.tty_shell._resize_pending.is_set():
                        self.tty_shell._resize_pending.clear()
                        self._fire_resize()

                    cq = self.ctx.command_queue
                    if cq is not None and not cq.cancelled:
                        cq.cancelled = True
                        cq.cancel_event.set()
                        chained = chained_task_ref[0]
                        if chained is not None and not chained.done():
                            chained.cancel()
                        self.ctx.command_queue = None
                        self.stdout.write(CURSOR_HIDE.encode())
                        self.stdout.write(
                            self.editor.render(bt, scroll.input_row, bt.width).encode()
                        )
                        cursor_col = self.editor.display.cursor
                        self.stdout.write(bt.move_yx(scroll.input_row, cursor_col).encode())
                        self.stdout.write(CURSOR_SHOW.encode())
                        continue

                    ac = self.ctx.active_command
                    if ac is not None:
                        self.ctx.active_command = None
                        for _wname, wtask in (
                            ("AUTOWANDER", self.ctx.wander_task),
                            ("AUTODISCOVER", self.ctx.discover_task),
                            ("RANDOMWALK", self.ctx.randomwalk_task),
                        ):
                            if wtask is not None and not wtask.done():
                                wtask.cancel()
                        self._update_input_style()

                    action = self.dispatch.lookup(key)
                    if action is not None:
                        result = action()
                        if asyncio.iscoroutine(result):
                            await result
                        self.stdout.write(CURSOR_HIDE.encode())
                        self._update_input_style()
                        self.stdout.write(
                            self.editor.render(bt, scroll.input_row, bt.width).encode()
                        )
                        self.toolbar.render(self.autoreply_engine)
                        cursor_col = self.editor.display.cursor
                        self.stdout.write(bt.move_yx(scroll.input_row, cursor_col).encode())
                        self.stdout.write(CURSOR_SHOW.encode())
                        continue

                    result = self.editor.feed_key(key)

                    if result.eof:
                        self.server_done = True
                        self.telnet_writer.close()
                        return

                    if result.interrupt:
                        self.stdout.write(CURSOR_HIDE.encode())
                        self._update_input_style()
                        self.stdout.write(
                            self.editor.render(bt, scroll.input_row, bt.width).encode()
                        )
                        cursor_col = self.editor.display.cursor
                        self.stdout.write(bt.move_yx(scroll.input_row, cursor_col).encode())
                        self.stdout.write(CURSOR_SHOW.encode())
                        continue

                    if result.line is not None:
                        line = result.line

                        if self.history_file and not self.telnet_writer.will_echo:
                            _save_history_entry(line, self.history_file)

                        is_pw = self.telnet_writer.will_echo
                        echo = "*" * len(line) if is_pw else line
                        self.stdout.write(bt.restore.encode())
                        colored = f"{bt.yellow}{echo}{bt.normal}\r\n"
                        self.stdout.write(colored.encode())
                        self.replay_buf.append(colored.encode())
                        self.stdout.write(bt.save.encode())

                        if self.ga_detected:
                            try:
                                await asyncio.wait_for(self.prompt_ready.wait(), timeout=2.0)
                            except asyncio.TimeoutError:
                                pass

                        if self.autoreply_engine is not None:
                            self.autoreply_engine.cancel()
                        wander_task = self.ctx.wander_task
                        if wander_task is not None and not wander_task.done():
                            wander_task.cancel()
                        disc_task = self.ctx.discover_task
                        if disc_task is not None and not disc_task.done():
                            disc_task.cancel()
                        rw_task = self.ctx.randomwalk_task
                        if rw_task is not None and not rw_task.done():
                            rw_task.cancel()

                        parts = expand_commands(line)
                        if parts and _TRAVEL_RE.match(parts[0]):
                            remainder = await _handle_travel_commands(
                                parts, self.ctx, self.telnet_writer.log
                            )
                            if remainder:
                                tx_dot.trigger()
                                self.telnet_writer.write(
                                    remainder[0] + "\r\n"  # type: ignore[arg-type]
                                )
                                if self.ga_detected:
                                    self.prompt_ready.clear()
                                if len(remainder) > 1:
                                    self._submit_command_queue(remainder, chained_task_ref)
                        elif parts:
                            tx_dot.trigger()
                            self.telnet_writer.write(parts[0] + "\r\n")  # type: ignore[arg-type]
                            if self.ga_detected:
                                self.prompt_ready.clear()
                            if len(parts) > 1:
                                self._submit_command_queue(parts, chained_task_ref)
                        else:
                            tx_dot.trigger()
                            self.telnet_writer.write("\r\n")  # type: ignore[arg-type]

                    if result.changed:
                        self.stdout.write(CURSOR_HIDE.encode())
                        cq2 = self.ctx.command_queue
                        ac2 = self.ctx.active_command
                        if cq2 is not None:
                            _render_command_queue(cq2, scroll, self.stdout)
                        elif ac2 is not None:
                            _render_active_command(ac2, scroll, self.stdout)
                        else:
                            self._update_input_style()
                            self.stdout.write(
                                self.editor.render(bt, scroll.input_row, bt.width).encode()
                            )
                        needs_reflash = self.toolbar.render(self.autoreply_engine)
                        if needs_reflash and not self.toolbar.flash_active:
                            self.toolbar.flash_active = True
                            self.toolbar.schedule_flash(
                                self.loop, self.autoreply_engine, self.editor, bt
                            )
                        cursor_col = self.editor.display.cursor
                        self.stdout.write(bt.move_yx(scroll.input_row, cursor_col).encode())
                        self.stdout.write(CURSOR_SHOW.encode())

        def _cleanup(self) -> None:
            """Cancel autoreply engine, restore cursor, clear kludge DMZ."""
            if self.autoreply_engine is not None:
                self.autoreply_engine.cancel()
            self.stdout.write(CURSOR_DEFAULT.encode())
            if self.mode_switched:
                assert self.scroll is not None
                dmz_row = self.scroll.scroll_bottom + 1
                self.stdout.write(self.blessed_term.save.encode())
                self.stdout.write(self.blessed_term.move_yx(dmz_row, 0).encode())
                self.stdout.write(self.blessed_term.normal.encode())
                self.stdout.write(self.blessed_term.clear_eos.encode())
                self.stdout.write(self.blessed_term.restore.encode())

        async def run(self) -> bool:
            """
            Run the REPL event loop.

            :returns: ``True`` if the server switched to kludge mode,
                ``False`` if the connection closed normally.
            """
            self._init_terminal()
            self._init_editor()
            self._init_ui()

            async with _repl_scaffold(
                self.telnet_writer,
                self.tty_shell,
                self.stdout,
                reserve_bottom=_RESERVE_INITIAL,
                on_resize=self._on_resize_repaint,
            ) as (scroll, _):
                self.scroll = scroll
                self.blessed_term = _get_term()
                self.toolbar = ToolbarRenderer(
                    ctx=self.ctx,
                    scroll=scroll,
                    out=self.stdout,
                    stoplight=self.stoplight,
                    rprompt_text=self.conn_info,
                )

                if self.banner_lines:
                    for bl in self.banner_lines:
                        self.stdout.write(f"{bl}\r\n".encode())

                self.stdout.write(self.blessed_term.save.encode())
                cs = self.ctx.cursor_style or _DEFAULT_CURSOR_STYLE
                self.stdout.write(_CURSOR_STYLES.get(cs, CURSOR_STEADY_BLOCK).encode())

                self._register_callbacks()

                try:
                    await _run_repl_tasks(self._read_server(), self._read_input())
                finally:
                    self._cleanup()

            return self.mode_switched

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
        :param banner_lines: Lines to display after scroll region is active.
        :returns: ``True`` if the server switched to kludge mode
            (caller should fall through to the standard event loop),
            ``False`` if the connection closed normally.
        """
        session = ReplSession(
            telnet_reader,
            telnet_writer,
            tty_shell,
            stdout,
            history_file=history_file,
            banner_lines=banner_lines,
        )
        return await session.run()
