"""REPL and TUI components for linemode telnet client sessions."""

# pylint: disable=too-complex

import sys
import asyncio
import logging
from typing import Any, Callable, Optional, Tuple, Union

from .stream_reader import TelnetReader, TelnetReaderUnicode
from .stream_writer import TelnetWriter, TelnetWriterUnicode

try:
    import prompt_toolkit
    import prompt_toolkit.filters
    import prompt_toolkit.history
    import prompt_toolkit.auto_suggest

    HAS_PROMPT_TOOLKIT = True
except ImportError:
    HAS_PROMPT_TOOLKIT = False

PASSWORD_CHAR = "\u25cf"

__all__ = (
    "HAS_PROMPT_TOOLKIT",
    "PromptToolkitRepl",
    "BasicLineRepl",
    "ScrollRegion",
    "repl_event_loop",
)


if HAS_PROMPT_TOOLKIT:

    class _FilteredFileHistory(prompt_toolkit.history.FileHistory):  # type: ignore[misc]
        """FileHistory subclass that skips storing password inputs.

        :attr:`is_password` is a callable checked at store time — when
        it returns ``True`` the entry is silently discarded.
        """

        def __init__(
            self,
            filename: str,
            is_password: "Optional[Callable[[], bool]]" = None,
        ) -> None:
            self._is_password = is_password
            super().__init__(filename)

        def store_string(self, string: str) -> None:
            if self._is_password is not None and self._is_password():
                return
            super().store_string(string)

    def _make_history(
        history_file: Optional[str],
        is_password: "Optional[Callable[[], bool]]" = None,
    ) -> "Union[_FilteredFileHistory, prompt_toolkit.history.InMemoryHistory]":
        """Create a history instance, ensuring parent directories exist."""
        if history_file:
            import pathlib  # pylint: disable=import-outside-toplevel

            pathlib.Path(history_file).parent.mkdir(parents=True, exist_ok=True)
            return _FilteredFileHistory(history_file, is_password=is_password)
        return prompt_toolkit.history.InMemoryHistory()

    class PromptToolkitRepl:
        """
        REPL using prompt_toolkit's PromptSession for linemode input.

        Provides persistent or in-memory history, history-based autosuggestion,
        and password masking when the server negotiates WILL ECHO (password mode).
        Password inputs are never written to the history file.

        :param telnet_writer: Writer for sending input to the server.
        :param log: Logger instance.
        :param history_file: Path to a persistent history file, or ``None``
            to use in-memory history only.
        """

        def __init__(
            self,
            telnet_writer: Union[TelnetWriter, TelnetWriterUnicode],
            log: logging.Logger,
            history_file: Optional[str] = None,
        ) -> None:
            self._writer = telnet_writer
            self._log = log
            self._history = _make_history(
                history_file, is_password=self._is_password_mode
            )
            self._session: "prompt_toolkit.PromptSession[str]" = (
                prompt_toolkit.PromptSession(
                    history=self._history,
                    auto_suggest=prompt_toolkit.auto_suggest.AutoSuggestFromHistory(),
                    enable_history_search=True,
                )
            )

        def _is_password_mode(self) -> bool:
            """Return True when server has negotiated WILL ECHO."""
            return bool(self._writer.will_echo)

        async def prompt(self) -> Optional[str]:
            """
            Read one line of input from the user.

            ``is_password`` is passed as a callable so prompt_toolkit
            re-evaluates it on every render — if the server toggles
            ``WILL ECHO`` while the prompt is already waiting, the
            display switches to masked input immediately.

            :returns: Input string, or ``None`` on EOF.
            """
            try:
                result: str = await self._session.prompt_async(
                    "",
                    is_password=prompt_toolkit.filters.Condition(
                        self._is_password_mode
                    ),
                )
                return result
            except EOFError:
                return None
            except KeyboardInterrupt:
                return None


class BasicLineRepl:
    """
    Fallback REPL using asyncio stdin for linemode input.

    Terminal cooked mode (ICANON) provides basic line editing
    (backspace, ^U kill-line).  No history or autocomplete.

    :param telnet_writer: Writer for sending input to the server.
    :param stdin_reader: asyncio StreamReader connected to stdin.
    :param log: Logger instance.
    """

    def __init__(
        self,
        telnet_writer: Union[TelnetWriter, TelnetWriterUnicode],
        stdin_reader: asyncio.StreamReader,
        log: logging.Logger,
    ) -> None:
        self._writer = telnet_writer
        self._stdin = stdin_reader
        self._log = log

    async def prompt(self) -> Optional[str]:
        """
        Read one line from stdin.

        :returns: Input string, or ``None`` on EOF.
        """
        data = await self._stdin.readline()
        if not data:
            return None
        return data.decode("utf-8", errors="replace").rstrip("\n")


# ---------------------------------------------------------------------------
# POSIX-only TUI components: scroll region, terminal size, REPL event loop
# ---------------------------------------------------------------------------

if sys.platform != "win32":
    import os
    import struct
    import fcntl
    import termios

    def _get_terminal_size() -> Tuple[int, int]:
        """Return ``(rows, cols)`` of the controlling terminal."""
        try:
            fmt = "hhhh"
            buf = b"\x00" * struct.calcsize(fmt)
            val = fcntl.ioctl(sys.stdin.fileno(), termios.TIOCGWINSZ, buf)
            rows, cols, _, _ = struct.unpack(fmt, val)
            return rows, cols
        except (IOError, OSError):
            return (
                int(os.environ.get("LINES", "25")),
                int(os.environ.get("COLUMNS", "80")),
            )

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
            self,
            stdout: asyncio.StreamWriter,
            rows: int,
            cols: int,
            reserve_bottom: int = 1,
        ) -> None:
            self._stdout = stdout
            self._rows = rows
            self._cols = cols
            self._reserve = reserve_bottom
            self._active = False

        @property
        def scroll_rows(self) -> int:
            """Number of rows in the scroll region."""
            return max(1, self._rows - self._reserve)

        @property
        def input_row(self) -> int:
            """1-indexed row number for the input line."""
            return self._rows

        def update_size(self, rows: int, cols: int) -> None:
            """Update dimensions and reapply scroll region."""
            self._rows = rows
            self._cols = cols
            if self._active:
                self._set_scroll_region()

        def _set_scroll_region(self) -> None:
            """Write DECSTBM escape sequence to set scroll region."""
            top = 1
            bottom = self.scroll_rows
            self._stdout.write(f"\x1b[{top};{bottom}r".encode())
            self._stdout.write(f"\x1b[{bottom};1H".encode())

        def _reset_scroll_region(self) -> None:
            """Reset scroll region to full terminal height."""
            self._stdout.write(f"\x1b[1;{self._rows}r".encode())

        def save_and_goto_input(self) -> None:
            """Save cursor, move to input line, clear it."""
            self._stdout.write(b"\x1b7")
            self._stdout.write(f"\x1b[{self.input_row};1H".encode())
            self._stdout.write(b"\x1b[2K")

        def restore_cursor(self) -> None:
            """Restore cursor to saved position in scroll region."""
            self._stdout.write(b"\x1b8")

        def __enter__(self) -> "ScrollRegion":
            self._set_scroll_region()
            self._active = True
            return self

        def __exit__(self, *_: Any) -> None:
            self._active = False
            self._reset_scroll_region()
            self._stdout.write(f"\x1b[{self._rows};1H".encode())

    # pylint: disable=too-many-locals,too-many-branches,too-many-statements
    async def repl_event_loop(
        telnet_reader: Union[TelnetReader, TelnetReaderUnicode],
        telnet_writer: Union[TelnetWriter, TelnetWriterUnicode],
        term: Any,
        stdout: asyncio.StreamWriter,
        history_file: Optional[str] = None,
    ) -> bool:
        """
        Event loop with REPL input at the bottom of the screen.

        When ``prompt_toolkit`` is available, :func:`patch_stdout` handles
        all cursor management — server output is printed above the prompt
        automatically.  The fallback ``BasicLineRepl`` path uses a manual
        ``ScrollRegion`` instead.

        :param term: ``Terminal`` instance from ``client_shell``.
        :returns: ``True`` if the server switched to kludge mode
            (caller should fall through to the standard event loop),
            ``False`` if the connection closed normally.
        """
        if HAS_PROMPT_TOOLKIT:
            return await _repl_event_loop_pt(
                telnet_reader, telnet_writer, term, stdout,
                history_file=history_file,
            )
        return await _repl_event_loop_basic(
            telnet_reader, telnet_writer, term, stdout,
        )

    async def _repl_event_loop_pt(
        telnet_reader: Union[TelnetReader, TelnetReaderUnicode],
        telnet_writer: Union[TelnetWriter, TelnetWriterUnicode],
        term: Any,
        stdout: asyncio.StreamWriter,
        history_file: Optional[str] = None,
    ) -> bool:
        """REPL event loop using prompt_toolkit.

        Server output is written directly to the raw terminal (asyncio
        ``stdout``) using DECSC/DECRC to save and restore cursor position
        between the scroll region and prompt_toolkit's input line.
        prompt_toolkit redraws its input line on every keystroke, so
        brief cursor disturbance is invisible.
        """
        from .telopt import NAWS  # pylint: disable=import-outside-toplevel
        from .client_shell import (  # pylint: disable=import-outside-toplevel
            _transform_output,
            _flush_color_filter,
        )

        rows, cols = _get_terminal_size()
        scroll_region: Optional[ScrollRegion] = None

        orig_send_naws = getattr(telnet_writer, "handle_send_naws", None)

        def _adjusted_send_naws() -> Tuple[int, int]:
            if scroll_region is not None and scroll_region._active:
                _, cur_cols = _get_terminal_size()
                return (scroll_region.scroll_rows, cur_cols)
            return _get_terminal_size()

        telnet_writer.handle_send_naws = _adjusted_send_naws  # type: ignore[method-assign]

        if telnet_writer.local_option.enabled(NAWS) and not telnet_writer.is_closing():
            telnet_writer._send_naws()  # pylint: disable=protected-access

        mode_switched = False

        with ScrollRegion(stdout, rows, cols, reserve_bottom=1) as scroll:
            scroll_region = scroll

            def _on_resize(new_rows: int, new_cols: int) -> None:
                nonlocal rows, cols
                rows, cols = new_rows, new_cols
                scroll.update_size(rows, cols)

            term.on_resize = _on_resize

            # Save initial scroll-region cursor position (DECSC).
            # _read_server restores this before writing, then re-saves.
            stdout.write(b"\x1b7")
            stdout.write(f"\x1b[{scroll.input_row};1H\x1b[2K".encode())

            repl = PromptToolkitRepl(
                telnet_writer, telnet_writer.log, history_file=history_file
            )
            server_done = False

            async def _read_server() -> None:
                nonlocal server_done, mode_switched
                while not server_done:
                    out = await telnet_reader.read(2**24)
                    if not out:
                        if telnet_reader._eof:  # pylint: disable=protected-access
                            server_done = True
                            _flush_color_filter(telnet_writer, stdout)
                            stdout.write(b"\x1b8")
                            stdout.write(
                                b"\r\nConnection closed by foreign host.\r\n"
                            )
                            return
                        continue
                    if telnet_writer.mode == "kludge":
                        mode_switched = True
                        server_done = True
                        return
                    if isinstance(out, bytes):
                        out = out.decode("utf-8", errors="replace")
                    # prompt_toolkit puts the terminal in raw mode —
                    # ONLCR is off, so we need explicit \r\n.
                    out = _transform_output(out, telnet_writer, True)
                    # DECRC → write in scroll region → DECSC → back to input
                    stdout.write(b"\x1b8")
                    stdout.write(out.encode())
                    stdout.write(b"\x1b7")
                    stdout.write(
                        f"\x1b[{scroll.input_row};1H".encode()
                    )

            async def _read_input() -> None:
                nonlocal server_done
                while not server_done:
                    stdout.write(
                        f"\x1b[{scroll.input_row};1H\x1b[2K".encode()
                    )
                    line = await repl.prompt()
                    if line is None:
                        server_done = True
                        try:
                            telnet_writer.close()
                        except Exception:  # pylint: disable=broad-exception-caught
                            pass
                        return
                    # Echo submitted input into the scroll region so
                    # it appears in the output above the input line.
                    # SGR 33 (brown) distinguishes local echo from
                    # server output; SGR 0 resets before server resumes.
                    stdout.write(b"\x1b8")
                    stdout.write(
                        f"\x1b[33m{line}\x1b[m\r\n".encode()
                    )
                    stdout.write(b"\x1b7")
                    stdout.write(
                        f"\x1b[{scroll.input_row};1H".encode()
                    )
                    telnet_writer.write(line + "\r\n")

            server_task = asyncio.ensure_future(_read_server())
            input_task = asyncio.ensure_future(_read_input())

            done, pending = await asyncio.wait(
                [server_task, input_task],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

            term.on_resize = None

        if orig_send_naws is not None:
            telnet_writer.handle_send_naws = orig_send_naws  # type: ignore[method-assign]

        return mode_switched

    async def _repl_event_loop_basic(
        telnet_reader: Union[TelnetReader, TelnetReaderUnicode],
        telnet_writer: Union[TelnetWriter, TelnetWriterUnicode],
        term: Any,
        stdout: asyncio.StreamWriter,
    ) -> bool:
        """Fallback REPL event loop using ScrollRegion and BasicLineRepl."""
        from .telopt import NAWS  # pylint: disable=import-outside-toplevel
        from .client_shell import (  # pylint: disable=import-outside-toplevel
            _transform_output,
            _flush_color_filter,
        )

        rows, cols = _get_terminal_size()
        scroll_region: Optional[ScrollRegion] = None

        # Patch NAWS to report reduced height
        orig_send_naws = getattr(telnet_writer, "handle_send_naws", None)

        def _adjusted_send_naws() -> Tuple[int, int]:
            if scroll_region is not None and scroll_region._active:
                _, cur_cols = _get_terminal_size()
                return (scroll_region.scroll_rows, cur_cols)
            return _get_terminal_size()

        telnet_writer.handle_send_naws = _adjusted_send_naws  # type: ignore[method-assign]

        if telnet_writer.local_option.enabled(NAWS) and not telnet_writer.is_closing():
            telnet_writer._send_naws()  # pylint: disable=protected-access

        mode_switched = False

        with ScrollRegion(stdout, rows, cols, reserve_bottom=1) as scroll:
            scroll_region = scroll

            def _on_resize(new_rows: int, new_cols: int) -> None:
                nonlocal rows, cols
                rows, cols = new_rows, new_cols
                scroll.update_size(rows, cols)

            term.on_resize = _on_resize

            stdout.write(f"\x1b[{scroll.input_row};1H\x1b[2K".encode())

            basic_stdin = await term.connect_stdin()
            repl: BasicLineRepl = BasicLineRepl(
                telnet_writer, basic_stdin, telnet_writer.log
            )

            server_done = False

            async def _read_server() -> None:
                nonlocal server_done, mode_switched
                while not server_done:
                    out = await telnet_reader.read(2**24)
                    if not out:
                        if telnet_reader._eof:  # pylint: disable=protected-access
                            server_done = True
                            _flush_color_filter(telnet_writer, stdout)
                            stdout.write(
                                b"\r\nConnection closed by foreign host.\r\n"
                            )
                            return
                        continue
                    if telnet_writer.mode == "kludge":
                        mode_switched = True
                        server_done = True
                        return
                    if isinstance(out, bytes):
                        out = out.decode("utf-8", errors="replace")
                    out = _transform_output(out, telnet_writer, False)
                    scroll.save_and_goto_input()
                    scroll.restore_cursor()
                    stdout.write(out.encode())

            async def _read_input() -> None:
                nonlocal server_done
                while not server_done:
                    stdout.write(
                        f"\x1b[{scroll.input_row};1H\x1b[2K".encode()
                    )
                    line = await repl.prompt()
                    if line is None:
                        server_done = True
                        try:
                            telnet_writer.close()
                        except Exception:  # pylint: disable=broad-exception-caught
                            pass
                        return
                    scroll.save_and_goto_input()
                    scroll.restore_cursor()
                    stdout.write(
                        f"\x1b[33m{line}\x1b[m\r\n".encode()
                    )
                    telnet_writer.write(line + "\r\n")

            server_task = asyncio.ensure_future(_read_server())
            input_task = asyncio.ensure_future(_read_input())

            done, pending = await asyncio.wait(
                [server_task, input_task],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

            term.on_resize = None

        if orig_send_naws is not None:
            telnet_writer.handle_send_naws = orig_send_naws  # type: ignore[method-assign]

        return mode_switched
