"""REPL and TUI components for linemode telnet client sessions."""

# pylint: disable=too-complex

# std imports
import sys
import json
import asyncio
import logging
from typing import Any, Tuple, Union, Callable, Optional

# local
from .stream_reader import TelnetReader, TelnetReaderUnicode
from .stream_writer import TelnetWriter, TelnetWriterUnicode

try:
    import prompt_toolkit
    import prompt_toolkit.filters
    import prompt_toolkit.history
    import prompt_toolkit.key_binding
    import prompt_toolkit.auto_suggest

    HAS_PROMPT_TOOLKIT = True
except ImportError:
    HAS_PROMPT_TOOLKIT = False

PASSWORD_CHAR = "\u25cf"

# Buffer for MUD data received while a TUI editor subprocess is running.
# The asyncio _read_server loop continues receiving MUD data during editor
# sessions; writing that data to the terminal fills the PTY buffer and
# deadlocks the editor's Textual WriterThread.  Data is queued here and
# replayed when the editor exits.
_editor_active = False
_editor_buffer: list[bytes] = []

__all__ = (
    "HAS_PROMPT_TOOLKIT",
    "PromptToolkitRepl",
    "BasicLineRepl",
    "ScrollRegion",
    "repl_event_loop",
)


if HAS_PROMPT_TOOLKIT:

    class _FilteredFileHistory(prompt_toolkit.history.FileHistory):  # type: ignore[misc]
        """
        FileHistory subclass that skips storing password inputs.

        :attr:`is_password` is a callable checked at store time — when
        it returns ``True`` the entry is silently discarded.
        """

        def __init__(
            self, filename: str, is_password: "Optional[Callable[[], bool]]" = None
        ) -> None:
            self._is_password = is_password
            super().__init__(filename)

        def store_string(self, string: str) -> None:
            """Skip storing the string when in password mode."""
            if self._is_password is not None and self._is_password():
                return
            super().store_string(string)

    def _make_history(
        history_file: Optional[str], is_password: "Optional[Callable[[], bool]]" = None
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
            """Initialize REPL with writer, logger, and optional history file."""
            self._writer = telnet_writer
            self._log = log
            self._history = _make_history(history_file, is_password=self._is_password_mode)
            kb = prompt_toolkit.key_binding.KeyBindings()

            @kb.add("c-]")
            def _escape_quit(event: Any) -> None:
                """Ctrl+] closes the connection, matching classic telnet."""
                event.app.exit(exception=EOFError)

            _macro_defs = getattr(telnet_writer, "_macro_defs", None)
            if _macro_defs is not None:
                from .macros import bind_macros  # pylint: disable=import-outside-toplevel

                bind_macros(kb, _macro_defs, telnet_writer, log)

            @kb.add("f8")
            def _edit_macros(event: Any) -> None:
                """F8 opens macro editor TUI in subprocess."""
                _launch_tui_editor(event, "macros", telnet_writer)

            @kb.add("f9")
            def _edit_autoreplies(event: Any) -> None:
                """F9 opens autoreply editor TUI in subprocess."""
                _launch_tui_editor(event, "autoreplies", telnet_writer)

            self._session: "prompt_toolkit.PromptSession[str]" = prompt_toolkit.PromptSession(
                history=self._history,
                auto_suggest=prompt_toolkit.auto_suggest.AutoSuggestFromHistory(),
                enable_history_search=True,
                key_bindings=kb,
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
                    "", is_password=prompt_toolkit.filters.Condition(self._is_password_mode)
                )
                return result
            except EOFError:
                return None
            except KeyboardInterrupt:
                return None


def _launch_tui_editor(
    event: Any, editor_type: str, writer: Union[TelnetWriter, TelnetWriterUnicode]
) -> None:
    """
    Launch a TUI editor for macros or autoreplies in a subprocess.

    Suspends the prompt_toolkit app, runs the editor, then reloads
    definitions on return.

    :param event: prompt_toolkit key event.
    :param editor_type: ``"macros"`` or ``"autoreplies"``.
    :param writer: Telnet writer with file path and definition attributes.
    """
    import os  # pylint: disable=import-outside-toplevel
    import subprocess  # pylint: disable=import-outside-toplevel

    _xdg = os.environ.get("XDG_CONFIG_HOME", os.path.join(os.path.expanduser("~"), ".config"))
    _config_dir = os.path.join(_xdg, "telnetlib3")

    _session_key = getattr(writer, "_session_key", "")

    if editor_type == "macros":
        path = getattr(writer, "_macros_file", None) or os.path.join(_config_dir, "macros.json")
        entry = "edit_macros_main"
    else:
        path = getattr(writer, "_autoreplies_file", None) or os.path.join(
            _config_dir, "autoreplies.json"
        )
        entry = "edit_autoreplies_main"

    cmd = [
        sys.executable, "-c",
        f"from telnetlib3.client_tui import {entry}; "
        f"{entry}({path!r}, {_session_key!r})",
    ]

    log = logging.getLogger(__name__)

    def _run_editor() -> None:
        global _editor_active  # noqa: PLW0603
        # Reset DECSTBM scroll region before launching TUI subprocess —
        # the Textual app uses the alternate screen buffer, but some
        # terminals share scroll region state across buffers, causing a
        # "doubling" effect where widgets render at the wrong row.
        sys.stdout.write("\x1b[r")
        sys.stdout.flush()
        # Flush and drain stderr — Textual writes all output to
        # sys.__stderr__, and leftover buffered data can fill the PTY
        # buffer, blocking the editor's WriterThread.
        sys.stderr.flush()
        sys.__stderr__.flush()
        _editor_active = True
        try:
            subprocess.run(cmd, check=False)
        except FileNotFoundError:
            log.warning("could not launch TUI editor subprocess")
        finally:
            _editor_active = False
            # Re-establish the scroll region that _repl_event_loop_pt set
            # via the ScrollRegion context manager (reserve_bottom=1).
            try:
                _tsize = os.get_terminal_size()
                scroll_bottom = max(1, _tsize.lines - 1)
                sys.stdout.write(f"\x1b[1;{scroll_bottom}r")
                sys.stdout.write(f"\x1b[{scroll_bottom};1H")
                sys.stdout.write("\x1b7")
                sys.stdout.write(f"\x1b[{_tsize.lines};1H\x1b[2K")
                sys.stdout.flush()
            except OSError:
                pass

        if editor_type == "macros":
            _reload_macros(writer, path, _session_key, log)
        else:
            _reload_autoreplies(writer, path, _session_key, log)

    from prompt_toolkit.application import run_in_terminal  # pylint: disable=import-outside-toplevel

    run_in_terminal(_run_editor)


def _reload_macros(
    writer: Union[TelnetWriter, TelnetWriterUnicode],
    path: str,
    session_key: str,
    log: logging.Logger,
) -> None:
    """Reload macro definitions from disk after editing."""
    import os  # pylint: disable=import-outside-toplevel

    if not os.path.exists(path):
        return
    from .macros import load_macros  # pylint: disable=import-outside-toplevel

    try:
        writer._macro_defs = load_macros(path, session_key)  # type: ignore[union-attr]
        writer._macros_file = path  # type: ignore[union-attr]
        log.info("reloaded %d macros from %s", len(writer._macro_defs), path)  # type: ignore[union-attr]
    except (ValueError, json.JSONDecodeError) as exc:
        log.warning("failed to reload macros: %s", exc)


def _reload_autoreplies(
    writer: Union[TelnetWriter, TelnetWriterUnicode],
    path: str,
    session_key: str,
    log: logging.Logger,
) -> None:
    """Reload autoreply rules from disk after editing."""
    import os  # pylint: disable=import-outside-toplevel

    if not os.path.exists(path):
        return
    from .autoreply import load_autoreplies  # pylint: disable=import-outside-toplevel

    try:
        writer._autoreply_rules = load_autoreplies(path, session_key)  # type: ignore[union-attr]
        writer._autoreplies_file = path  # type: ignore[union-attr]
        log.info("reloaded %d autoreplies from %s", len(writer._autoreply_rules), path)  # type: ignore[union-attr]
    except (ValueError, json.JSONDecodeError) as exc:
        log.warning("failed to reload autoreplies: %s", exc)


class BasicLineRepl:
    """
    Fallback REPL using asyncio stdin for linemode input.

    Terminal cooked mode (ICANON) provides basic line editing (backspace, ^U kill-line).  No history
    or autocomplete.

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
        """Initialize raw-mode REPL with writer, stdin reader, and logger."""
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


if sys.platform != "win32":
    import os
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
        def scroll_rows(self) -> int:
            """Number of rows in the scroll region."""
            return max(1, self._rows - self._reserve)

        @property
        def input_row(self) -> int:
            """1-indexed row number for the input line."""
            return self._rows

        @property
        def resize_pending(self) -> bool:
            """Check and clear the resize-pending flag."""
            if self._dirty:
                self._dirty = False
                return True
            return False

        def update_size(self, rows: int, cols: int) -> None:
            """Update dimensions and reapply scroll region."""
            old_input_row = self.input_row
            self._rows = rows
            self._cols = cols
            if self._active:
                self._stdout.write(f"\x1b[{old_input_row};1H\x1b[2K".encode())
                self._set_scroll_region()
                self._stdout.write(b"\x1b7")
                self._stdout.write(f"\x1b[{self.input_row};1H\x1b[2K".encode())
                self._dirty = True

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

    import contextlib

    @contextlib.asynccontextmanager
    async def _repl_scaffold(
        telnet_writer: Union[TelnetWriter, TelnetWriterUnicode],
        term: Any,
        stdout: asyncio.StreamWriter,
    ) -> "Any":
        """
        Set up NAWS patch, scroll region, and resize handler.

        Yields ``(scroll, rows_cols)`` where *rows_cols* is a mutable
        ``[rows, cols]`` list kept up-to-date by the resize handler.
        Restores the original ``handle_send_naws`` in a ``finally`` block.
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

            with ScrollRegion(stdout, rows, cols, reserve_bottom=1) as scroll:
                scroll_region = scroll

                def _on_resize(new_rows: int, new_cols: int) -> None:
                    rows_cols[0] = new_rows
                    rows_cols[1] = new_cols
                    scroll.update_size(new_rows, new_cols)

                term.on_resize = _on_resize
                try:
                    yield scroll, rows_cols
                finally:
                    term.on_resize = None
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
                telnet_reader, telnet_writer, term, stdout, history_file=history_file
            )
        return await _repl_event_loop_basic(telnet_reader, telnet_writer, term, stdout)

    async def _repl_event_loop_pt(
        telnet_reader: Union[TelnetReader, TelnetReaderUnicode],
        telnet_writer: Union[TelnetWriter, TelnetWriterUnicode],
        term: Any,
        stdout: asyncio.StreamWriter,
        history_file: Optional[str] = None,
    ) -> bool:
        """
        REPL event loop using prompt_toolkit.

        Server output is written directly to the raw terminal (asyncio
        ``stdout``) using DECSC/DECRC to save and restore cursor position
        between the scroll region and prompt_toolkit's input line.
        prompt_toolkit redraws its input line on every keystroke, so
        brief cursor disturbance is invisible.
        """
        from .client_shell import (  # pylint: disable=import-outside-toplevel,cyclic-import
            _transform_output,
            _flush_color_filter,
        )

        mode_switched = False

        async with _repl_scaffold(telnet_writer, term, stdout) as (scroll, _):
            # Save initial scroll-region cursor position (DECSC).
            # _read_server restores this before writing, then re-saves.
            stdout.write(b"\x1b7")
            stdout.write(f"\x1b[{scroll.input_row};1H\x1b[2K".encode())

            repl = PromptToolkitRepl(  # pylint: disable=possibly-used-before-assignment
                telnet_writer, telnet_writer.log, history_file=history_file
            )

            _ar_rules = getattr(telnet_writer, "_autoreply_rules", None)
            autoreply_engine = None
            if _ar_rules:
                from .autoreply import AutoreplyEngine  # pylint: disable=import-outside-toplevel

                def _insert_into_prompt(text: str) -> None:
                    app = repl._session.app  # pylint: disable=protected-access
                    if app and app.current_buffer:
                        app.current_buffer.insert_text(text)

                autoreply_engine = AutoreplyEngine(
                    _ar_rules, telnet_writer, telnet_writer.log,
                    insert_fn=_insert_into_prompt,
                )

            server_done = False

            async def _read_server() -> None:
                nonlocal server_done, mode_switched
                while not server_done:
                    out = await telnet_reader.read(2**24)
                    if not out:
                        if telnet_reader.at_eof():
                            server_done = True
                            _flush_color_filter(telnet_writer, stdout)
                            stdout.write(b"\x1b8")
                            stdout.write(b"\r\nConnection closed by foreign host.\r\n")
                            return
                        continue
                    if isinstance(out, bytes):
                        out = out.decode("utf-8", errors="replace")
                    out = _transform_output(out, telnet_writer, True)
                    if autoreply_engine is not None:
                        autoreply_engine.feed(out)
                    if _editor_active:
                        _editor_buffer.append(out.encode())
                        continue
                    stdout.write(b"\x1b8")
                    if _editor_buffer:
                        for chunk in _editor_buffer:
                            stdout.write(chunk)
                        _editor_buffer.clear()
                    stdout.write(out.encode())
                    stdout.write(b"\x1b7")
                    stdout.write(f"\x1b[{scroll.input_row};1H".encode())
                    if telnet_writer.mode == "kludge":
                        mode_switched = True
                        server_done = True
                        return

            async def _read_input() -> None:
                nonlocal server_done
                while not server_done:
                    stdout.write(f"\x1b[{scroll.input_row};1H\x1b[2K".encode())
                    line = await repl.prompt()
                    if line is None:
                        server_done = True
                        telnet_writer.close()
                        return
                    # pylint: disable-next=protected-access
                    is_pw = repl._is_password_mode()
                    echo_text = "*" * len(line) if is_pw else line
                    stdout.write(b"\x1b8")
                    stdout.write(f"\x1b[33m{echo_text}\x1b[m\r\n".encode())
                    stdout.write(b"\x1b7")
                    stdout.write(f"\x1b[{scroll.input_row};1H".encode())
                    telnet_writer.write(line + "\r\n")

            try:
                await _run_repl_tasks(_read_server(), _read_input())
            finally:
                if autoreply_engine is not None:
                    autoreply_engine.cancel()

        return mode_switched

    async def _repl_event_loop_basic(
        telnet_reader: Union[TelnetReader, TelnetReaderUnicode],
        telnet_writer: Union[TelnetWriter, TelnetWriterUnicode],
        term: Any,
        stdout: asyncio.StreamWriter,
    ) -> bool:
        """Fallback REPL event loop using ScrollRegion and BasicLineRepl."""
        from .client_shell import (  # pylint: disable=import-outside-toplevel,cyclic-import
            _transform_output,
            _flush_color_filter,
        )

        mode_switched = False

        async with _repl_scaffold(telnet_writer, term, stdout) as (scroll, _):
            stdout.write(f"\x1b[{scroll.input_row};1H\x1b[2K".encode())

            basic_stdin = await term.connect_stdin()
            repl: BasicLineRepl = BasicLineRepl(telnet_writer, basic_stdin, telnet_writer.log)

            _ar_rules_b = getattr(telnet_writer, "_autoreply_rules", None)
            autoreply_engine_b = None
            if _ar_rules_b:
                from .autoreply import AutoreplyEngine  # pylint: disable=import-outside-toplevel

                autoreply_engine_b = AutoreplyEngine(
                    _ar_rules_b, telnet_writer, telnet_writer.log
                )

            server_done = False

            async def _read_server() -> None:
                nonlocal server_done, mode_switched
                while not server_done:
                    out = await telnet_reader.read(2**24)
                    if not out:
                        if telnet_reader.at_eof():
                            server_done = True
                            _flush_color_filter(telnet_writer, stdout)
                            stdout.write(b"\r\nConnection closed by foreign host.\r\n")
                            return
                        continue
                    if isinstance(out, bytes):
                        out = out.decode("utf-8", errors="replace")
                    out = _transform_output(out, telnet_writer, False)
                    if autoreply_engine_b is not None:
                        autoreply_engine_b.feed(out)
                    scroll.save_and_goto_input()
                    scroll.restore_cursor()
                    stdout.write(out.encode())
                    if telnet_writer.mode == "kludge":
                        mode_switched = True
                        server_done = True
                        return

            async def _read_input() -> None:
                nonlocal server_done
                while not server_done:
                    stdout.write(f"\x1b[{scroll.input_row};1H\x1b[2K".encode())
                    line = await repl.prompt()
                    if line is None:
                        server_done = True
                        telnet_writer.close()
                        return
                    echo_text = "*" * len(line) if telnet_writer.will_echo else line
                    scroll.save_and_goto_input()
                    scroll.restore_cursor()
                    stdout.write(f"\x1b[33m{echo_text}\x1b[m\r\n".encode())
                    telnet_writer.write(line + "\r\n")

            try:
                await _run_repl_tasks(_read_server(), _read_input())
            finally:
                if autoreply_engine_b is not None:
                    autoreply_engine_b.cancel()

        return mode_switched
