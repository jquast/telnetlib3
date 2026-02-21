"""Telnet client shell implementations for interactive terminal sessions."""

# pylint: disable=too-complex

# std imports
import sys
import asyncio
import collections
from typing import Any, Dict, Tuple, Union, Callable, Optional

# local
from . import accessories
from .accessories import TRACE
from .stream_reader import TelnetReader, TelnetReaderUnicode
from .stream_writer import TelnetWriter, TelnetWriterUnicode

__all__ = ("InputFilter", "telnet_client_shell")

# ATASCII graphics characters that map to byte 0x0D and 0x0A respectively.
# When --ascii-eol is active, these are replaced with \r and \n before
# terminal display so that BBSes using ASCII CR/LF render correctly.
_ATASCII_CR_CHAR = "\U0001fb82"  # UPPER ONE QUARTER BLOCK (from byte 0x0D)
_ATASCII_LF_CHAR = "\u25e3"  # BLACK LOWER LEFT TRIANGLE (from byte 0x0A)

# Input byte translation tables for retro encodings in raw mode.
# Maps terminal keyboard bytes to the raw bytes the BBS expects.
# Applied BEFORE decoding/encoding, bypassing the codec entirely for
# characters that can't round-trip through Unicode (e.g. ATASCII 0x7E
# shares its Unicode codepoint U+25C0 with 0xFE).
_INPUT_XLAT: Dict[str, Dict[int, int]] = {
    "atascii": {
        0x7F: 0x7E,  # DEL → ATASCII backspace (byte 0x7E)
        0x08: 0x7E,  # BS  → ATASCII backspace (byte 0x7E)
        0x0D: 0x9B,  # CR  → ATASCII EOL (byte 0x9B)
        0x0A: 0x9B,  # LF  → ATASCII EOL (byte 0x9B)
    },
    "petscii": {
        0x7F: 0x14,  # DEL → PETSCII DEL (byte 0x14)
        0x08: 0x14,  # BS  → PETSCII DEL (byte 0x14)
    },
}

# Multi-byte escape sequence translation tables for retro encodings.
# Maps common ANSI terminal escape sequences (arrow keys, delete, etc.)
# to the raw bytes the BBS expects.  Inspired by blessed's
# DEFAULT_SEQUENCE_MIXIN but kept minimal for the sequences that matter.
_INPUT_SEQ_XLAT: Dict[str, Dict[bytes, bytes]] = {
    "atascii": {
        b"\x1b[A": b"\x1c",  # cursor up (CSI)
        b"\x1b[B": b"\x1d",  # cursor down
        b"\x1b[C": b"\x1f",  # cursor right
        b"\x1b[D": b"\x1e",  # cursor left
        b"\x1bOA": b"\x1c",  # cursor up (SS3 / application mode)
        b"\x1bOB": b"\x1d",  # cursor down
        b"\x1bOC": b"\x1f",  # cursor right
        b"\x1bOD": b"\x1e",  # cursor left
        b"\x1b[3~": b"\x7e",  # delete → ATASCII backspace
        b"\t": b"\x7f",  # tab → ATASCII tab
    },
    "petscii": {
        b"\x1b[A": b"\x91",  # cursor up (CSI)
        b"\x1b[B": b"\x11",  # cursor down
        b"\x1b[C": b"\x1d",  # cursor right
        b"\x1b[D": b"\x9d",  # cursor left
        b"\x1bOA": b"\x91",  # cursor up (SS3 / application mode)
        b"\x1bOB": b"\x11",  # cursor down
        b"\x1bOC": b"\x1d",  # cursor right
        b"\x1bOD": b"\x9d",  # cursor left
        b"\x1b[3~": b"\x14",  # delete → PETSCII DEL
        b"\x1b[H": b"\x13",  # home → PETSCII HOME
        b"\x1b[2~": b"\x94",  # insert → PETSCII INSERT
    },
}


class InputFilter:
    """
    Translate terminal escape sequences and single bytes to retro encoding bytes.

    Combines single-byte translation (backspace, delete) with multi-byte
    escape sequence matching (arrow keys, function keys).  Uses prefix-based
    buffering inspired by blessed's ``get_leading_prefixes`` to handle
    sequences split across reads.

    When a partial match is buffered (e.g. a bare ESC), :attr:`has_pending`
    becomes ``True``.  The caller should start an ``esc_delay`` timer and
    call :meth:`flush` if no further input arrives before the timer fires.

    :param seq_xlat: Multi-byte escape sequence → replacement bytes.
    :param byte_xlat: Single input byte → replacement byte.
    :param esc_delay: Seconds to wait before flushing a buffered prefix
        (default 0.35, matching blessed's ``DEFAULT_ESCDELAY``).
    """

    def __init__(
        self, seq_xlat: Dict[bytes, bytes], byte_xlat: Dict[int, int], esc_delay: float = 0.35
    ) -> None:
        """Initialize input filter with sequence and byte translation tables."""
        self._byte_xlat = byte_xlat
        self.esc_delay = esc_delay
        # Sort sequences longest-first so \x1b[3~ matches before \x1b[3
        self._seq_sorted: Tuple[Tuple[bytes, bytes], ...] = tuple(
            sorted(seq_xlat.items(), key=lambda kv: len(kv[0]), reverse=True)
        )
        # Prefix set for partial-match buffering (blessed's get_leading_prefixes)
        self._prefixes: frozenset[bytes] = frozenset(
            seq[:i] for seq in seq_xlat for i in range(1, len(seq))
        )
        self._buf = b""

    @property
    def has_pending(self) -> bool:
        """Return ``True`` when the internal buffer holds a partial sequence."""
        return bool(self._buf)

    def flush(self) -> bytes:
        """
        Flush buffered bytes, applying single-byte translation.

        Called when the ``esc_delay`` timer fires without new input,
        meaning the buffered prefix is not a real escape sequence.

        :returns: Translated bytes from the buffer (may be empty).
        """
        result = bytearray()
        while self._buf:
            b = self._buf[0]
            self._buf = self._buf[1:]
            result.append(self._byte_xlat.get(b, b))
        return bytes(result)

    def feed(self, data: bytes) -> bytes:
        """
        Process input bytes, returning raw bytes to send to the remote host.

        Escape sequences are matched against the configured table and replaced. Partial sequences
        are buffered until the next call.  Single bytes are translated via the byte translation
        table.

        :param data: Raw bytes from terminal stdin.
        :returns: Translated bytes ready to send to the remote BBS.
        """
        self._buf += data
        result = bytearray()
        while self._buf:
            # Try multi-byte sequence match at current position
            matched = False
            for seq, repl in self._seq_sorted:
                if self._buf[: len(seq)] == seq:
                    result.extend(repl)
                    self._buf = self._buf[len(seq) :]
                    matched = True
                    break
            if matched:
                continue
            # Check if buffer is a prefix of any known sequence — wait for more
            if self._buf in self._prefixes:
                break
            # No sequence match, emit single byte with translation
            b = self._buf[0]
            self._buf = self._buf[1:]
            result.append(self._byte_xlat.get(b, b))
        return bytes(result)


if sys.platform == "win32":

    async def telnet_client_shell(
        telnet_reader: Union[TelnetReader, TelnetReaderUnicode],
        telnet_writer: Union[TelnetWriter, TelnetWriterUnicode],
    ) -> None:
        """Win32 telnet client shell (not implemented)."""
        raise NotImplementedError("win32 not yet supported as telnet client. Please contribute!")

else:
    import os
    import signal
    import termios

    class Terminal:
        """
        Context manager for terminal mode handling on POSIX systems.

        When sys.stdin is attached to a terminal, it is configured for the matching telnet modes
        negotiated for the given telnet_writer.
        """

        ModeDef = collections.namedtuple(
            "ModeDef", ["iflag", "oflag", "cflag", "lflag", "ispeed", "ospeed", "cc"]
        )

        def __init__(self, telnet_writer: Union[TelnetWriter, TelnetWriterUnicode]) -> None:
            self.telnet_writer = telnet_writer
            self._fileno = sys.stdin.fileno()
            self._istty = os.path.sameopenfile(0, 1)
            self._save_mode: Optional[Terminal.ModeDef] = None
            self.software_echo = False
            self._remove_winch = False
            self._winch_handle: Optional[asyncio.TimerHandle] = None
            self.on_resize: Optional[Callable[[int, int], None]] = None
            self._stdin_transport: Optional[asyncio.BaseTransport] = None

        def setup_winch(self) -> None:
            """Register SIGWINCH handler to send NAWS on terminal resize."""
            if not self._istty or not hasattr(signal, "SIGWINCH"):
                return
            try:
                loop = asyncio.get_event_loop()
                writer = self.telnet_writer

                def _handle_resize() -> None:
                    from .telopt import NAWS  # pylint: disable=import-outside-toplevel

                    # pylint: disable-next=import-outside-toplevel,cyclic-import
                    from .client_repl import _get_terminal_size

                    try:
                        if self.on_resize is not None:
                            rows, cols = _get_terminal_size()
                            self.on_resize(rows, cols)
                    except Exception:  # pylint: disable=broad-exception-caught
                        pass
                    try:
                        if writer.local_option.enabled(NAWS) and not writer.is_closing():
                            writer._send_naws()  # pylint: disable=protected-access
                    except Exception:  # pylint: disable=broad-exception-caught
                        pass

                def _on_winch() -> None:
                    if self._winch_handle is not None and not self._winch_handle.cancelled():
                        try:
                            self._winch_handle.cancel()
                        except Exception:  # pylint: disable=broad-exception-caught
                            pass
                    self._winch_handle = loop.call_later(0.05, _handle_resize)

                loop.add_signal_handler(signal.SIGWINCH, _on_winch)
                self._remove_winch = True
            except Exception:  # pylint: disable=broad-exception-caught
                self._remove_winch = False

        def cleanup_winch(self) -> None:
            """Remove SIGWINCH handler and cancel pending timer."""
            if self._istty and self._remove_winch:
                try:
                    asyncio.get_event_loop().remove_signal_handler(signal.SIGWINCH)
                except Exception:  # pylint: disable=broad-exception-caught
                    pass
                self._remove_winch = False
            if self._winch_handle is not None:
                try:
                    self._winch_handle.cancel()
                except Exception:  # pylint: disable=broad-exception-caught
                    pass
                self._winch_handle = None

        def __enter__(self) -> "Terminal":
            self._save_mode = self.get_mode()
            if self._istty:
                self.set_mode(self.determine_mode(self._save_mode))
            return self

        def __exit__(self, *_: Any) -> None:
            self.cleanup_winch()
            if self._istty:
                termios.tcsetattr(self._fileno, termios.TCSAFLUSH, list(self._save_mode))

        def get_mode(self) -> Optional["Terminal.ModeDef"]:
            """Return current terminal mode if attached to a tty, otherwise None."""
            if self._istty:
                return self.ModeDef(*termios.tcgetattr(self._fileno))
            return None

        def set_mode(self, mode: "Terminal.ModeDef") -> None:
            """Set terminal mode attributes."""
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSAFLUSH, list(mode))

        @staticmethod
        def _suppress_echo(mode: "Terminal.ModeDef") -> "Terminal.ModeDef":
            """Return copy of *mode* with local ECHO disabled, keeping ICANON."""
            return Terminal.ModeDef(
                iflag=mode.iflag,
                oflag=mode.oflag,
                cflag=mode.cflag,
                lflag=mode.lflag & ~termios.ECHO,
                ispeed=mode.ispeed,
                ospeed=mode.ospeed,
                cc=mode.cc,
            )

        def _make_raw(
            self, mode: "Terminal.ModeDef", suppress_echo: bool = True
        ) -> "Terminal.ModeDef":
            """
            Return copy of *mode* with raw terminal attributes set.

            :param suppress_echo: When True, disable local ECHO (server echoes). When False, keep
                  local ECHO enabled (character-at-a-time with local echo, e.g. SGA without ECHO).
            """
            iflag = mode.iflag & ~(
                termios.BRKINT | termios.ICRNL | termios.INPCK | termios.ISTRIP | termios.IXON
            )
            cflag = mode.cflag & ~(termios.CSIZE | termios.PARENB)
            cflag = cflag | termios.CS8
            lflag_mask = termios.ICANON | termios.IEXTEN | termios.ISIG
            if suppress_echo:
                lflag_mask |= termios.ECHO
            lflag = mode.lflag & ~lflag_mask
            oflag = mode.oflag & ~(termios.OPOST | termios.ONLCR)
            cc = list(mode.cc)
            cc[termios.VMIN] = 1
            cc[termios.VTIME] = 0
            return self.ModeDef(
                iflag=iflag,
                oflag=oflag,
                cflag=cflag,
                lflag=lflag,
                ispeed=mode.ispeed,
                ospeed=mode.ospeed,
                cc=cc,
            )

        def _server_will_sga(self) -> bool:
            """Whether server has negotiated WILL SGA."""
            from .telopt import SGA  # pylint: disable=import-outside-toplevel

            return bool(self.telnet_writer.client and self.telnet_writer.remote_option.enabled(SGA))

        def check_auto_mode(
            self, switched_to_raw: bool, last_will_echo: bool
        ) -> "tuple[bool, bool, bool] | None":
            """
            Check if auto-mode switching is needed.

            :param switched_to_raw: Whether terminal has already switched to raw mode.
            :param last_will_echo: Previous value of server's WILL ECHO state.
            :returns: ``(switched_to_raw, last_will_echo, local_echo)`` tuple
                if mode changed, or ``None`` if no change needed.
            """
            if not self._istty:
                return None
            _wecho = self.telnet_writer.will_echo
            _wsga = self._server_will_sga()
            # WILL ECHO alone = line mode with server echo (suppress local echo)
            # WILL SGA (with or without ECHO) = raw/character-at-a-time
            _should_go_raw = not switched_to_raw and _wsga
            _should_suppress_echo = not switched_to_raw and _wecho and not _wsga
            _echo_changed = switched_to_raw and _wecho != last_will_echo
            if not (_should_go_raw or _should_suppress_echo or _echo_changed):
                return None
            if _should_suppress_echo:
                self.set_mode(self._suppress_echo(self._save_mode))
                self.telnet_writer.log.debug(
                    "auto: server echo without SGA, line mode (server WILL ECHO)"
                )
                return (False, _wecho, False)
            self.set_mode(self._make_raw(self._save_mode, suppress_echo=True))
            self.telnet_writer.log.debug(
                "auto: %s (server %s ECHO)",
                (
                    "switching to raw mode"
                    if _should_go_raw
                    else ("disabling" if _wecho else "enabling") + " software echo"
                ),
                "WILL" if _wecho else "WONT",
            )
            return (True if _should_go_raw else switched_to_raw, _wecho, not _wecho)

        def determine_mode(self, mode: "Terminal.ModeDef") -> "Terminal.ModeDef":
            """
            Return copy of 'mode' with changes suggested for telnet connection.

            Auto mode (``_raw_mode is None``): follows the server's negotiation.

            =================  ========  ==========  ================================
            Server negotiates  ICANON    ECHO        Behavior
            =================  ========  ==========  ================================
            Nothing            on        on          Line mode, local echo
            WILL SGA only      **off**   on          Character-at-a-time, local echo
            WILL ECHO only     on        **off**     Line mode, server echoes
            WILL SGA + ECHO    **off**   **off**     Full kludge mode (most common)
            =================  ========  ==========  ================================
            """
            raw_mode = _get_raw_mode(self.telnet_writer)
            will_echo = self.telnet_writer.will_echo
            will_sga = self._server_will_sga()
            # Auto mode (None): follow server negotiation
            if raw_mode is None:
                if will_echo and will_sga:
                    self.telnet_writer.log.debug("auto: server echo + SGA, kludge mode")
                    return self._make_raw(mode)
                if will_echo:
                    self.telnet_writer.log.debug("auto: server echo without SGA, line mode")
                    return self._suppress_echo(mode)
                if will_sga:
                    self.telnet_writer.log.debug("auto: SGA without echo, character-at-a-time")
                    self.software_echo = True
                    return self._make_raw(mode, suppress_echo=True)
                self.telnet_writer.log.debug("auto: no server echo yet, line mode")
                return mode
            # Explicit line mode (False)
            if not raw_mode:
                self.telnet_writer.log.debug("local echo, linemode")
                return mode
            # Explicit raw mode (True)
            if not will_echo:
                self.telnet_writer.log.debug("raw mode forced, no server echo")
            else:
                self.telnet_writer.log.debug("server echo, kludge mode")
            return self._make_raw(mode)

        async def make_stdout(self) -> asyncio.StreamWriter:
            """
            Return an asyncio StreamWriter for local terminal output.

            This does **not** connect stdin — call :meth:`connect_stdin`
            separately when an asyncio stdin reader is needed (the REPL
            manages its own stdin via prompt_toolkit).
            """
            write_fobj = sys.stdout
            if self._istty:
                write_fobj = sys.stdin
            loop = asyncio.get_event_loop()
            writer_transport, writer_protocol = await loop.connect_write_pipe(
                asyncio.streams.FlowControlMixin, write_fobj
            )
            return asyncio.StreamWriter(writer_transport, writer_protocol, None, loop)

        async def connect_stdin(self) -> asyncio.StreamReader:
            """
            Connect sys.stdin to an asyncio StreamReader.

            Must be called **after** any prompt_toolkit session has finished, because prompt_toolkit
            and asyncio cannot both own the stdin file descriptor at the same time.
            """
            reader = asyncio.StreamReader()
            reader_protocol = asyncio.StreamReaderProtocol(reader)
            transport, _ = await asyncio.get_event_loop().connect_read_pipe(
                lambda: reader_protocol, sys.stdin
            )
            self._stdin_transport = transport
            return reader

        def disconnect_stdin(self, reader: asyncio.StreamReader) -> None:
            """Disconnect stdin pipe so prompt_toolkit can reclaim it."""
            transport = getattr(self, "_stdin_transport", None)
            if transport is not None:
                transport.close()
                self._stdin_transport = None
            reader.feed_eof()

        async def make_stdio(self) -> Tuple[asyncio.StreamReader, asyncio.StreamWriter]:
            """Return (reader, writer) pair for sys.stdin, sys.stdout."""
            stdout = await self.make_stdout()
            stdin = await self.connect_stdin()
            return stdin, stdout

    def _transform_output(
        out: str, writer: Union[TelnetWriter, TelnetWriterUnicode], in_raw_mode: bool
    ) -> str:
        r"""
        Apply color filter, ASCII EOL substitution, and CRLF normalization.

        :param out: Server output text to transform.
        :param writer: Telnet writer (checked for ``_color_filter`` and ``_ascii_eol``).
        :param in_raw_mode: When ``True``, normalize line endings to ``\r\n``.
        :returns: Transformed output string.
        """
        _cf = getattr(writer, "_color_filter", None)
        if _cf is not None:
            out = _cf.filter(out)
        if getattr(writer, "_ascii_eol", False):
            out = out.replace(_ATASCII_CR_CHAR, "\r").replace(_ATASCII_LF_CHAR, "\n")
        if in_raw_mode:
            out = out.replace("\r\n", "\n").replace("\n", "\r\n")
        else:
            # Cooked mode: PTY ONLCR converts \n → \r\n, so strip \r before \n
            # to avoid doubling (\r\n → \r\r\n).
            out = out.replace("\r\n", "\n")
        return out

    def _send_stdin(
        inp: bytes,
        telnet_writer: Union[TelnetWriter, TelnetWriterUnicode],
        stdout: asyncio.StreamWriter,
        local_echo: bool,
    ) -> "tuple[Optional[asyncio.Task[None]], bool]":
        """
        Send stdin input to server and optionally echo locally.

        :param inp: Raw bytes from terminal stdin.
        :param telnet_writer: Telnet writer for sending to server.
        :param stdout: Local stdout writer for software echo.
        :param local_echo: When ``True``, echo input bytes to stdout.
        :returns: ``(esc_timer_task_or_None, has_pending)`` tuple.
        """
        _inf = getattr(telnet_writer, "_input_filter", None)
        pending = False
        new_timer: Optional[asyncio.Task[None]] = None
        if _inf is not None:
            translated = _inf.feed(inp)
            if translated:
                telnet_writer._write(translated)  # pylint: disable=protected-access
            if _inf.has_pending:
                pending = True
                new_timer = asyncio.ensure_future(asyncio.sleep(_inf.esc_delay))
        else:
            telnet_writer._write(inp)  # pylint: disable=protected-access
        if local_echo:
            _echo_buf = bytearray()
            for _b in inp:
                if _b in (0x7F, 0x08):
                    _echo_buf.extend(b"\b \b")
                elif _b == 0x0D:
                    _echo_buf.extend(b"\r\n")
                elif _b >= 0x20:
                    _echo_buf.append(_b)
            if _echo_buf:
                stdout.write(bytes(_echo_buf))
        return new_timer, pending

    def _get_raw_mode(writer: Union[TelnetWriter, TelnetWriterUnicode]) -> "bool | None":
        """Return the writer's ``_raw_mode`` attribute (``None``, ``True``, or ``False``)."""
        return getattr(writer, "_raw_mode", False)

    def _flush_color_filter(
        writer: Union[TelnetWriter, TelnetWriterUnicode], stdout: asyncio.StreamWriter
    ) -> None:
        """Flush any pending color filter output to stdout."""
        _cf = getattr(writer, "_color_filter", None)
        if _cf is not None:
            _flush = _cf.flush()
            if _flush:
                stdout.write(_flush.encode())

    # pylint: disable=too-many-positional-arguments,too-many-locals,too-many-branches
    async def _raw_event_loop(
        telnet_reader: Union[TelnetReader, TelnetReaderUnicode],
        telnet_writer: Union[TelnetWriter, TelnetWriterUnicode],
        term: "Terminal",
        stdin: asyncio.StreamReader,
        stdout: asyncio.StreamWriter,
        keyboard_escape: str,
        local_echo: bool,
        switched_to_raw: bool,
        last_will_echo: bool,
        linesep: str,
        handle_close: Callable[[str], None],
        want_repl: Callable[[], bool],
    ) -> "tuple[bool, bool, bool, bool, str]":
        """
        Standard byte-at-a-time event loop.

        :returns: ``(reactivate_repl, switched_to_raw, last_will_echo,
            local_echo, linesep)`` tuple.
        """
        stdin_task = accessories.make_reader_task(stdin)
        telnet_task = accessories.make_reader_task(telnet_reader, size=2**24)
        esc_timer_task: Optional[asyncio.Task[None]] = None
        wait_for: set[asyncio.Task[Any]] = {stdin_task, telnet_task}
        reactivate_repl = False

        while wait_for:
            done, _ = await asyncio.wait(wait_for, return_when=asyncio.FIRST_COMPLETED)
            if stdin_task in done:
                task = stdin_task
                done.discard(task)
            else:
                task = done.pop()
            wait_for.discard(task)

            telnet_writer.log.log(TRACE, "task=%s, wait_for=%s", task, wait_for)

            # ESC_DELAY timer fired — flush buffered partial sequence
            if task is esc_timer_task:
                esc_timer_task = None
                _inf = getattr(telnet_writer, "_input_filter", None)
                if _inf is not None and _inf.has_pending:
                    flushed = _inf.flush()
                    if flushed:
                        telnet_writer._write(flushed)  # pylint: disable=protected-access
                continue

            # client input
            if task == stdin_task:
                if esc_timer_task is not None and esc_timer_task in wait_for:
                    esc_timer_task.cancel()
                    wait_for.discard(esc_timer_task)
                    esc_timer_task = None
                inp = task.result()
                if not inp:
                    telnet_writer.log.debug("EOF from client stdin")
                    continue
                if keyboard_escape in inp.decode():
                    try:
                        telnet_writer.close()
                    except Exception:  # pylint: disable=broad-exception-caught
                        pass
                    if telnet_task in wait_for:
                        telnet_task.cancel()
                        wait_for.remove(telnet_task)
                    handle_close("Connection closed.")
                    break
                new_timer, has_pending = _send_stdin(inp, telnet_writer, stdout, local_echo)
                if has_pending and esc_timer_task not in wait_for:
                    esc_timer_task = new_timer
                    if esc_timer_task is not None:
                        wait_for.add(esc_timer_task)
                stdin_task = accessories.make_reader_task(stdin)
                wait_for.add(stdin_task)

            # server output
            elif task == telnet_task:
                out = task.result()
                if not out and telnet_reader.at_eof():
                    if stdin_task in wait_for:
                        stdin_task.cancel()
                        wait_for.remove(stdin_task)
                    handle_close("Connection closed by foreign host.")
                    continue
                raw_mode = _get_raw_mode(telnet_writer)
                in_raw = raw_mode is True or (raw_mode is None and switched_to_raw)
                out = _transform_output(out, telnet_writer, in_raw)
                _ar_engine = getattr(telnet_writer, "_autoreply_engine", None)
                if _ar_engine is None:
                    _ar_rules = getattr(telnet_writer, "_autoreply_rules", None)
                    if _ar_rules:
                        from .autoreply import (  # pylint: disable=import-outside-toplevel
                            AutoreplyEngine,
                        )

                        _ar_wait = getattr(
                            telnet_writer, "_autoreply_wait_fn", None
                        )
                        _ar_engine = AutoreplyEngine(
                            _ar_rules, telnet_writer, telnet_writer.log,
                            wait_fn=_ar_wait,
                        )
                        # pylint: disable-next=protected-access
                        telnet_writer._autoreply_engine = _ar_engine
                if _ar_engine is not None:
                    _ar_engine.feed(out)
                if raw_mode is None:
                    mode_result = term.check_auto_mode(switched_to_raw, last_will_echo)
                    if mode_result is not None:
                        if not switched_to_raw:
                            linesep = "\r\n"
                        switched_to_raw, last_will_echo, local_echo = mode_result
                        # When transitioning cooked → raw, the data was
                        # processed for ONLCR (\r\n → \n) but the terminal
                        # now has ONLCR disabled.  Re-normalize so bare \n
                        # becomes \r\n for correct display.
                        if switched_to_raw and not in_raw:
                            out = out.replace("\n", "\r\n")
                    if want_repl():
                        reactivate_repl = True
                stdout.write(out.encode())
                if reactivate_repl:
                    telnet_writer.log.debug("mode returned to local, reactivating REPL")
                    if stdin_task in wait_for:
                        stdin_task.cancel()
                        wait_for.discard(stdin_task)
                    switched_to_raw = False
                    break
                telnet_task = accessories.make_reader_task(telnet_reader, size=2**24)
                wait_for.add(telnet_task)

        return (reactivate_repl, switched_to_raw, last_will_echo, local_echo, linesep)

    async def telnet_client_shell(
        telnet_reader: Union[TelnetReader, TelnetReaderUnicode],
        telnet_writer: Union[TelnetWriter, TelnetWriterUnicode],
    ) -> None:
        """
        Minimal telnet client shell for POSIX terminals.

        This shell performs minimal tty mode handling when a terminal is attached to standard in
        (keyboard), notably raw mode is often set and this shell may exit only by disconnect from
        server, or the escape character, ^].

        stdin or stdout may also be a pipe or file, behaving much like nc(1).
        """
        keyboard_escape = "\x1d"

        with Terminal(telnet_writer=telnet_writer) as term:
            linesep = "\n"
            switched_to_raw = False
            last_will_echo = False
            local_echo = term.software_echo
            if term._istty:  # pylint: disable=protected-access
                raw_mode = _get_raw_mode(telnet_writer)
                if telnet_writer.will_echo or raw_mode is True:
                    linesep = "\r\n"
            stdout = await term.make_stdout()
            _banner_sep = "\r\n" if term._istty else linesep  # pylint: disable=protected-access
            _n_macros = len(getattr(telnet_writer, "_macro_defs", []) or [])
            _n_autoreplies = len(getattr(telnet_writer, "_autoreply_rules", []) or [])
            if _n_macros:
                _mf = getattr(telnet_writer, "_macros_file", "")
                stdout.write(f"{_n_macros} macros loaded from {_mf}.{_banner_sep}".encode())
            if _n_autoreplies:
                _af = getattr(telnet_writer, "_autoreplies_file", "")
                stdout.write(
                    f"{_n_autoreplies} autoreplies loaded from {_af}.{_banner_sep}".encode()
                )
            escape_name = accessories.name_unicode(keyboard_escape)
            stdout.write(
                f"Escape character is '{escape_name}'"
                f" - Press F1 for help!{_banner_sep}".encode()
            )
            term.setup_winch()

            # EOR/GA-based command pacing for raw-mode autoreplies.
            _prompt_ready_raw = asyncio.Event()
            _prompt_ready_raw.set()
            _ga_detected_raw = False

            def _on_prompt_signal_raw(_cmd: bytes) -> None:
                nonlocal _ga_detected_raw
                _ga_detected_raw = True
                _prompt_ready_raw.set()
                _ar = getattr(telnet_writer, "_autoreply_engine", None)
                if _ar is not None:
                    _ar.on_prompt()

            from .telopt import GA, CMD_EOR  # pylint: disable=import-outside-toplevel

            telnet_writer.set_iac_callback(GA, _on_prompt_signal_raw)
            telnet_writer.set_iac_callback(CMD_EOR, _on_prompt_signal_raw)

            async def _wait_for_prompt_raw() -> None:
                if not _ga_detected_raw:
                    return
                try:
                    await asyncio.wait_for(_prompt_ready_raw.wait(), timeout=2.0)
                except asyncio.TimeoutError:
                    pass
                _prompt_ready_raw.clear()

            # Attach wait_fn to writer so _raw_event_loop can pick it up.
            telnet_writer._autoreply_wait_fn = _wait_for_prompt_raw

            repl_enabled = getattr(telnet_writer, "_repl_enabled", False)
            raw_mode_val = _get_raw_mode(telnet_writer)
            _can_repl = (
                repl_enabled
                and raw_mode_val is not True
                and term._istty  # pylint: disable=protected-access
            )

            def _handle_close(msg: str) -> None:
                _flush_color_filter(telnet_writer, stdout)
                stdout.write(f"\033[m{linesep}{msg}{linesep}".encode())
                term.cleanup_winch()

            def _want_repl() -> bool:
                return _can_repl and telnet_writer.mode == "local"

            # -- outer loop: alternate between REPL and raw event loops --
            while True:
                if _want_repl():
                    # pylint: disable-next=import-outside-toplevel,cyclic-import
                    from .client_repl import repl_event_loop

                    history_file = getattr(telnet_writer, "_history_file", None)
                    telnet_writer.log.debug("entering REPL (line mode)")
                    mode_switched = await repl_event_loop(
                        telnet_reader, telnet_writer, term, stdout, history_file=history_file
                    )
                    if not mode_switched:
                        return
                    telnet_writer.log.debug("REPL deactivated, switching to standard event loop")
                    continue

                # Standard event loop (byte-at-a-time).
                stdin = await term.connect_stdin()
                _reactivate_repl, switched_to_raw, last_will_echo, local_echo, linesep = (
                    await _raw_event_loop(
                        telnet_reader,
                        telnet_writer,
                        term,
                        stdin,
                        stdout,
                        keyboard_escape,
                        local_echo,
                        switched_to_raw,
                        last_will_echo,
                        linesep,
                        _handle_close,
                        _want_repl,
                    )
                )
                term.disconnect_stdin(stdin)
                if _reactivate_repl:
                    continue
                break
