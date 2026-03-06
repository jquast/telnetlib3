"""Telnet client shell implementations for interactive terminal sessions."""

# std imports
import os
import sys
import asyncio
import logging
import threading
import collections
from typing import Any, Dict, Tuple, Union, Callable, Optional
from dataclasses import dataclass

# local
from . import slc as slc_module
from . import accessories
from ._session_context import TelnetSessionContext

log = logging.getLogger(__name__)

# local
from .telopt import LINEMODE  # noqa: E402
from .accessories import TRACE  # noqa: E402
from .stream_reader import TelnetReader, TelnetReaderUnicode  # noqa: E402
from .stream_writer import TelnetWriter, TelnetWriterUnicode  # noqa: E402

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
        0x7F: 0x7E,  # DEL -> ATASCII backspace (byte 0x7E)
        0x08: 0x7E,  # BS  -> ATASCII backspace (byte 0x7E)
        0x0D: 0x9B,  # CR  -> ATASCII EOL (byte 0x9B)
        0x0A: 0x9B,  # LF  -> ATASCII EOL (byte 0x9B)
    },
    "petscii": {
        0x7F: 0x14,  # DEL -> PETSCII DEL (byte 0x14)
        0x08: 0x14,  # BS  -> PETSCII DEL (byte 0x14)
    },
}

# ESC key delay
ESC_DELAY = float(os.getenv('ESC_DELAY', '0.35'))

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
        b"\x1b[3~": b"\x7e",  # delete -> ATASCII backspace
        b"\t": b"\x7f",  # tab -> ATASCII tab
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
        b"\x1b[3~": b"\x14",  # delete -> PETSCII DEL
        b"\x1b[H": b"\x13",  # home -> PETSCII HOME
        b"\x1b[2~": b"\x94",  # insert -> PETSCII INSERT
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

    :param map_mbs_esc: Multi-byte escape sequence -> replacement bytes.
    :param map_singlebyte: Single input byte -> replacement byte.
    :param esc_delay: Seconds to wait before flushing a buffered prefix
        (default 0.35, matching blessed's ``DEFAULT_ESCDELAY``).
    """

    def __init__(
        self,
        map_mbs_esc: Dict[bytes, bytes],
        map_singlebyte: Dict[int, int],
        esc_delay: float = ESC_DELAY
    ) -> None:
        """Initialize input filter with sequence and byte translation tables."""
        self._map_singlebyte = map_singlebyte
        self.esc_delay = esc_delay
        # Sort sequences longest-first so \x1b[3~ matches before \x1b[3
        self._seq_sorted: Tuple[Tuple[bytes, bytes], ...] = tuple(
            sorted(map_mbs_esc.items(), key=lambda kv: len(kv[0]), reverse=True)
        )
        # Prefix set for partial-match buffering (blessed's get_leading_prefixes)
        self._mbs_prefixes: frozenset[bytes] = frozenset(
            seq[:i] for seq in map_mbs_esc for i in range(1, len(seq))
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
            result.append(self._map_singlebyte.get(b, b))
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
            # Check if buffer is a prefix of any known sequence -- wait for more
            if self._buf in self._mbs_prefixes:
                break
            # No sequence match, emit single byte with translation
            b = self._buf[0]
            self._buf = self._buf[1:]
            result.append(self._map_singlebyte.get(b, b))
        return bytes(result)


@dataclass
class _RawLoopState:
    """
    Mutable state bundle for :func:`_raw_event_loop`.

    Initialised by :func:`telnet_client_shell` before the loop starts and mutated
    in-place as mid-session negotiation arrives (e.g. server WILL ECHO toggling
    after login, LINEMODE EDIT confirmed by server).  On loop exit,
    ``switched_to_raw`` and ``reactivate_repl`` reflect final state so the caller
    can decide whether to restart a REPL.
    """

    switched_to_raw: bool
    last_will_echo: bool
    local_echo: bool
    linesep: str
    reactivate_repl: bool = False


class LinemodeBuffer:
    """
    Client-side line buffer for LINEMODE EDIT mode (RFC 1184 §3.1).

    Accumulates characters typed by the user, applying local SLC editing functions (erase-char,
    erase-line, erase-word) and transmitting complete lines to the server.  When TRAPSIG is enabled,
    signal characters (^C etc.) are sent as IAC commands instead of buffered.

    :param slctab: The writer's current SLC character table.
    :param forwardmask: FORWARDMASK received from server, or None.
    :param trapsig: When True, signal characters are sent as IAC commands.
    """

    def __init__(
        self,
        slctab: Dict[bytes, slc_module.SLC],
        forwardmask: Optional[slc_module.Forwardmask] = None,
        trapsig: bool = False,
    ) -> None:
        """Initialize LinemodeBuffer."""
        from .telopt import IP, AYT, BRK, EOF, IAC, SUSP, ABORT

        self._buf: list[str] = []
        self.slctab = slctab
        self.forwardmask = forwardmask
        self.trapsig = trapsig
        self._trapsig_map: Dict[bytes, bytes] = {
            slc_module.SLC_IP: IAC + IP,
            slc_module.SLC_ABORT: IAC + ABORT,
            slc_module.SLC_SUSP: IAC + SUSP,
            slc_module.SLC_EOF: IAC + EOF,
            slc_module.SLC_BRK: IAC + BRK,
            slc_module.SLC_AYT: IAC + AYT,
        }

    def _slc_val(self, func: bytes) -> Optional[int]:
        """Return the active byte value for SLC function, or None if unsupported."""
        defn = self.slctab.get(func)
        if defn is None or defn.nosupport:
            return None
        v = defn.val
        return ord(v) if v and v != slc_module.theNULL else None

    def feed(self, char: str) -> Tuple[str, Optional[bytes]]:
        """
        Feed one character into the buffer.

        :returns: ``(echo, data)`` where ``echo`` is text to display locally
            (may be empty) and ``data`` is bytes to send to server, or None
            if buffering.
        """
        b = ord(char)
        if self.trapsig:
            for func, cmd in self._trapsig_map.items():
                if b == self._slc_val(func):
                    return ("", cmd)
        if b == self._slc_val(slc_module.SLC_EC):
            if self._buf:
                self._buf.pop()
                return ("\b \b", None)
            return ("", None)
        if b == self._slc_val(slc_module.SLC_EL):
            n = len(self._buf)
            self._buf.clear()
            return ("\b \b" * n, None)
        if b == self._slc_val(slc_module.SLC_EW):
            popped = 0
            # skip trailing spaces (POSIX VWERASE behaviour)
            while self._buf and self._buf[-1] == " ":
                self._buf.pop()
                popped += 1
            while self._buf and self._buf[-1] != " ":
                self._buf.pop()
                popped += 1
            return ("\b \b" * popped, None)
        if char in ("\r", "\n"):
            line = "".join(self._buf) + char
            self._buf.clear()
            return (char, line.encode())
        if self.forwardmask is not None and b in self.forwardmask:
            data = ("".join(self._buf) + char).encode()
            self._buf.clear()
            return (char, data)
        self._buf.append(char)
        return (char, None)


def _transform_output(
    out: str, writer: Union[TelnetWriter, TelnetWriterUnicode], in_raw_mode: bool
) -> str:
    r"""
    Apply ASCII EOL substitution and CRLF normalization.

    :param out: Server output text to transform.
    :param writer: Telnet writer (``ctx`` provides ascii_eol).
    :param in_raw_mode: When ``True``, normalize line endings to ``\r\n``.
    :returns: Transformed output string.
    """
    ctx: TelnetSessionContext = writer.ctx
    if ctx.ascii_eol:
        out = out.replace(_ATASCII_CR_CHAR, "\r").replace(_ATASCII_LF_CHAR, "\n")
    if in_raw_mode:
        out = out.replace("\r\n", "\n").replace("\n", "\r\n")
    else:
        # Cooked mode: PTY ONLCR converts \n -> \r\n, so strip \r before \n
        # to avoid doubling (\r\n -> \r\r\n).
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
    ctx: TelnetSessionContext = telnet_writer.ctx
    inf = ctx.input_filter
    pending = False
    new_timer: Optional[asyncio.Task[None]] = None
    if inf is not None:
        translated = inf.feed(inp)
        if translated:
            telnet_writer._write(translated)
        if inf.has_pending:
            pending = True
            new_timer = asyncio.ensure_future(asyncio.sleep(inf.esc_delay))
    else:
        telnet_writer._write(inp)
    if local_echo:
        echo_buf = bytearray()
        for b in inp:
            if b in (0x7F, 0x08):
                echo_buf.extend(b"\b \b")
            elif b == 0x0D:
                echo_buf.extend(b"\r\n")
            elif b >= 0x20:
                echo_buf.append(b)
        if echo_buf:
            stdout.write(bytes(echo_buf))
    return new_timer, pending


def _get_raw_mode(writer: Union[TelnetWriter, TelnetWriterUnicode]) -> "bool | None":
    """
    Return the raw-mode override from the writer's session context.

    ``None`` = auto-detect from server negotiation (default),
    ``True`` = force raw / character-at-a-time,
    ``False`` = force line mode.
    """
    return writer.ctx.raw_mode


def _ensure_autoreply_engine(
    telnet_writer: Union[TelnetWriter, TelnetWriterUnicode],
) -> "Optional[Any]":
    """
    Return the autoreply engine from the writer's session context, or ``None``.

    The autoreply engine is optional application-level machinery (e.g. a macro
    engine in a MUD client) that watches server output and sends pre-configured
    replies.  It is absent in standalone telnetlib3 and supplied by the host
    application via ``writer.ctx.autoreply_engine``.
    """
    return telnet_writer.ctx.autoreply_engine


def _get_linemode_buffer(writer: Union[TelnetWriter, TelnetWriterUnicode]) -> "LinemodeBuffer":
    """
    Return (or lazily create) the :class:`LinemodeBuffer` attached to *writer*.

    The buffer is stored as ``writer._linemode_buf`` so it persists across loop
    iterations and accumulates characters between :meth:`LinemodeBuffer.feed`
    calls.  Created on first use because LINEMODE negotiation may complete after
    the shell has already started.
    """
    buf: Optional[LinemodeBuffer] = getattr(writer, "_linemode_buf", None)
    if buf is None:
        buf = LinemodeBuffer(
            slctab=writer.slctab,
            forwardmask=writer.forwardmask,
            trapsig=writer.linemode.trapsig,
        )
        writer._linemode_buf = buf
    return buf


async def _raw_event_loop(
    telnet_reader: Union[TelnetReader, TelnetReaderUnicode],
    telnet_writer: Union[TelnetWriter, TelnetWriterUnicode],
    tty_shell,
    stdin: asyncio.StreamReader,
    stdout: asyncio.StreamWriter,
    keyboard_escape: str,
    state: _RawLoopState,
    handle_close: Callable[[str], None],
    want_repl: Callable[[], bool],
) -> None:
    """Standard byte-at-a-time event loop (mutates *state* in-place)."""
    stdin_task = accessories.make_reader_task(stdin)
    telnet_task = accessories.make_reader_task(telnet_reader, size=2**24)
    esc_timer_task: Optional[asyncio.Task[None]] = None
    wait_for: set[asyncio.Task[Any]] = {stdin_task, telnet_task}

    while wait_for:
        done, _ = await asyncio.wait(wait_for, return_when=asyncio.FIRST_COMPLETED)
        if stdin_task in done:
            task = stdin_task
            done.discard(task)
        else:
            task = done.pop()
        wait_for.discard(task)

        telnet_writer.log.log(TRACE, "task=%s, wait_for=%s", task, wait_for)

        # ESC_DELAY timer fired -- flush buffered partial sequence
        if task is esc_timer_task:
            esc_timer_task = None
            inf = telnet_writer.ctx.input_filter
            if inf is not None and inf.has_pending:
                flushed = inf.flush()
                if flushed:
                    telnet_writer._write(flushed)
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
                except Exception:
                    pass
                if telnet_task in wait_for:
                    telnet_task.cancel()
                    wait_for.remove(telnet_task)
                handle_close("Connection closed.")
                break
            linemode_edit = (
                telnet_writer.local_option.enabled(LINEMODE) and telnet_writer.linemode.edit
            )
            if linemode_edit and state.switched_to_raw:
                # Raw PTY or non-TTY: kernel not doing line editing, use LinemodeBuffer
                lmbuf = _get_linemode_buffer(telnet_writer)
                for ch in inp.decode(errors="replace"):
                    echo, data = lmbuf.feed(ch)
                    if echo:
                        stdout.write(echo.encode())
                    if data:
                        telnet_writer._write(data)
                new_timer, has_pending = None, False
            elif linemode_edit:
                # Cooked PTY: kernel already handled EC/EL/echo; forward line directly
                new_timer, has_pending = _send_stdin(inp, telnet_writer, stdout, False)
            else:
                new_timer, has_pending = _send_stdin(
                    inp, telnet_writer, stdout, state.local_echo
                )
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
            in_raw = raw_mode is True or (raw_mode is None and state.switched_to_raw)
            out = _transform_output(out, telnet_writer, in_raw)
            ar_engine = _ensure_autoreply_engine(telnet_writer)
            if ar_engine is not None:
                ar_engine.feed(out)
            if raw_mode is None or (raw_mode is True and state.switched_to_raw):
                mode_result = tty_shell.check_auto_mode(
                    state.switched_to_raw, state.last_will_echo
                )
                if mode_result is not None:
                    if not state.switched_to_raw:
                        state.linesep = "\r\n"
                    state.switched_to_raw, state.last_will_echo, state.local_echo = mode_result
                    # When transitioning cooked -> raw, the data was
                    # processed for ONLCR (\r\n -> \n) but the terminal
                    # now has ONLCR disabled.  Re-normalize so bare \n
                    # becomes \r\n for correct display.
                    if state.switched_to_raw and not in_raw:
                        out = out.replace("\n", "\r\n")
                if raw_mode is None and want_repl():
                    state.reactivate_repl = True
            stdout.write(out.encode())
            _ts_file = telnet_writer.ctx.typescript_file
            if _ts_file is not None:
                _ts_file.write(out)
                _ts_file.flush()
            if state.reactivate_repl:
                telnet_writer.log.debug("mode returned to local, reactivating REPL")
                if stdin_task in wait_for:
                    stdin_task.cancel()
                    wait_for.discard(stdin_task)
                state.switched_to_raw = False
                break
            telnet_task = accessories.make_reader_task(telnet_reader, size=2**24)
            wait_for.add(telnet_task)


async def _telnet_client_shell_impl(
    telnet_reader: Union[TelnetReader, TelnetReaderUnicode],
    telnet_writer: Union[TelnetWriter, TelnetWriterUnicode],
    tty_shell,
) -> None:
    """
    Shared implementation body for :func:`telnet_client_shell` on all platforms.

    Called with an already-entered terminal context manager (*tty_shell*).
    Handles mode negotiation, GA/EOR pacing, and the raw event loop.
    """
    keyboard_escape = "\x1d"
    linesep = "\n"
    switched_to_raw = False
    last_will_echo = False
    local_echo = tty_shell.software_echo
    if tty_shell._istty:
        raw_mode = _get_raw_mode(telnet_writer)
        if telnet_writer.will_echo or raw_mode is True:
            linesep = "\r\n"
    stdout = await tty_shell.make_stdout()
    tty_shell.setup_winch()

    # Prompt-pacing via IAC GA / IAC EOR.
    #
    # MUD servers emit IAC GA (Go-Ahead, RFC 854) or IAC EOR (End-of-Record, RFC 885) after
    # each prompt to signal "output is complete, awaiting your input."  The autoreply engine
    # uses this to pace its replies. It calls ctx.autoreply_wait_fn() before sending each
    # reply, preventing races where a reply arrives before the server has finished rendering
    # the prompt.
    #
    # 'server_uses_ga' becomes True on the first GA/EOR received.  _wait_for_prompt is does
    # nothing until 'server_uses_ga', so servers that never send GA/EOR (Most everything but
    # MUDs these days) are silently unaffected.
    #
    # prompt_event starts SET so the first autoreply fires immediately -- there is no prior
    # GA to wait for.  _on_ga_or_eor re-sets it on each prompt signal; _wait_for_prompt
    # clears it after consuming the signal so the next autoreply waits for the following
    # prompt.
    prompt_event = asyncio.Event()
    prompt_event.set()
    server_uses_ga = False

    # The session context is the decoupling point between this shell and the
    # autoreply engine (which may live in a separate module).  Storing
    # _wait_for_prompt on it lets the engine call back into our local event state
    # without a direct import or reference to this closure.
    ctx: TelnetSessionContext = telnet_writer.ctx

    def _on_ga_or_eor(_cmd: bytes) -> None:
        nonlocal server_uses_ga
        server_uses_ga = True
        prompt_event.set()
        ar = ctx.autoreply_engine
        if ar is not None:
            ar.on_prompt()

    from .telopt import GA, CMD_EOR

    telnet_writer.set_iac_callback(GA, _on_ga_or_eor)
    telnet_writer.set_iac_callback(CMD_EOR, _on_ga_or_eor)

    async def _wait_for_prompt() -> None:
        """
        Wait for the next prompt signal before the autoreply engine sends a reply.

        No-op until the first GA/EOR confirms this server uses prompt signalling.
        After that, blocks until :func:`_on_ga_or_eor` fires the event, then clears
        it to arm the wait for the following prompt.  A 2-second safety timeout
        prevents stalling if the server stops sending GA mid-session.
        """
        if not server_uses_ga:
            return
        try:
            await asyncio.wait_for(prompt_event.wait(), timeout=2.0)
        except asyncio.TimeoutError:
            pass
        prompt_event.clear()

    ctx.autoreply_wait_fn = _wait_for_prompt

    escape_name = accessories.name_unicode(keyboard_escape)
    banner_sep = "\r\n" if tty_shell._istty else linesep
    stdout.write(f"Escape character is '{escape_name}'.{banner_sep}".encode())

    def _handle_close(msg: str) -> None:
        # \033[m resets all SGR attributes so server-set colours do not
        # bleed into the terminal after disconnect.
        stdout.write(f"\033[m{linesep}{msg}{linesep}".encode())
        tty_shell.cleanup_winch()

    def _should_reactivate_repl() -> bool:
        # Extension point for callers that embed a REPL (e.g. a MUD client).
        # Return True to break _raw_event_loop and return to the REPL when
        # the server puts the terminal back into local mode.  The base shell
        # has no REPL, so this always returns False.
        return False

    # Wait up to 50 ms for subsequent WILL ECHO / WILL SGA packets to arrive before
    # committing to a terminal mode.
    #
    # check_negotiation() declares the handshake complete as soon as TTYPE and NEW_ENVIRON /
    # CHARSET are settled, without waiting for ECHO / SGA.  Those options typically travel
    # in the same "initial negotiation burst" but may not have not yet have "arrived" at
    # this point in our TCP read until a few milliseconds later. Servers that never send
    # WILL ECHO (rlogin, basically) simply time out and proceed correctly.
    raw_mode = _get_raw_mode(telnet_writer)
    if raw_mode is not False and tty_shell._istty:
        try:
            await asyncio.wait_for(
                telnet_writer.wait_for_condition(lambda w: w.mode != "local"), timeout=0.05
            )
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass

    # Commit the terminal to raw mode now that will_echo is stable.  suppress_echo=True
    # disables the kernel's local ECHO because the server will echo (or we handle it in
    # software).  local_echo is set to True only when the server will NOT echo, so we
    # reproduce keystrokes ourselves.
    if not switched_to_raw and tty_shell._istty and tty_shell._save_mode is not None:
        tty_shell.set_mode(tty_shell._make_raw(tty_shell._save_mode, suppress_echo=True))
        switched_to_raw = True
        local_echo = not telnet_writer.will_echo
        linesep = "\r\n"
    stdin = await tty_shell.connect_stdin()
    state = _RawLoopState(
        switched_to_raw=switched_to_raw,
        last_will_echo=last_will_echo,
        local_echo=local_echo,
        linesep=linesep,
    )
    await _raw_event_loop(
        telnet_reader,
        telnet_writer,
        tty_shell,
        stdin,
        stdout,
        keyboard_escape,
        state,
        _handle_close,
        _should_reactivate_repl,
    )
    tty_shell.disconnect_stdin(stdin)


if sys.platform == "win32":
    from .client_shell_win32 import Terminal, telnet_client_shell  # noqa: F401

else:
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
            self._resize_pending = threading.Event()
            self.on_resize: Optional[Callable[[int, int], None]] = None
            self._stdin_transport: Optional[asyncio.BaseTransport] = None

        def setup_winch(self) -> None:
            """Register SIGWINCH handler to set ``_resize_pending`` flag."""
            if not self._istty or not hasattr(signal, "SIGWINCH"):
                return
            from .telopt import NAWS

            writer = self.telnet_writer
            try:
                loop = asyncio.get_event_loop()

                def _on_winch() -> None:
                    self._resize_pending.set()
                    if writer.local_option.enabled(NAWS):
                        writer._send_naws()

                loop.add_signal_handler(signal.SIGWINCH, _on_winch)
                self._remove_winch = True
            except Exception:
                self._remove_winch = False

        def cleanup_winch(self) -> None:
            """Remove SIGWINCH handler."""
            if self._istty and self._remove_winch:
                try:
                    asyncio.get_event_loop().remove_signal_handler(signal.SIGWINCH)
                except Exception:
                    pass
                self._remove_winch = False

        def __enter__(self) -> "Terminal":
            self._save_mode = self.get_mode()
            if self._istty:
                assert self._save_mode is not None
                self.set_mode(self.determine_mode(self._save_mode))
            return self

        def __exit__(self, *_: Any) -> None:
            self.cleanup_winch()
            if self._istty:
                assert self._save_mode is not None
                termios.tcsetattr(self._fileno, termios.TCSADRAIN, list(self._save_mode))

        def get_mode(self) -> Optional["Terminal.ModeDef"]:
            """Return current terminal mode if attached to a tty, otherwise None."""
            if self._istty:
                return self.ModeDef(*termios.tcgetattr(self._fileno))
            return None

        def set_mode(self, mode: "Terminal.ModeDef") -> None:
            """Set terminal mode attributes."""
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, list(mode))

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
            """Whether SGA has been negotiated (either direction)."""
            from .telopt import SGA

            w = self.telnet_writer
            return bool(w.client and (w.remote_option.enabled(SGA) or w.local_option.enabled(SGA)))

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
            wecho = self.telnet_writer.will_echo
            wsga = self._server_will_sga()
            # LINEMODE EDIT: kernel must handle line editing; keep/restore cooked mode.
            # This takes priority over the SGA/ECHO raw-mode heuristics below.
            if (
                self.telnet_writer.local_option.enabled(LINEMODE)
                and self.telnet_writer.linemode.edit
            ):
                if switched_to_raw:
                    assert self._save_mode is not None
                    self.set_mode(self._save_mode)
                    self.telnet_writer.log.debug(
                        "auto: LINEMODE EDIT confirmed, restoring cooked mode"
                    )
                    return (False, wecho, False)
                return None
            # WILL ECHO alone = line mode with server echo (suppress local echo)
            # WILL SGA (with or without ECHO) = raw/character-at-a-time
            should_go_raw = not switched_to_raw and wsga
            should_suppress_echo = not switched_to_raw and wecho and not wsga
            echo_changed = switched_to_raw and wecho != last_will_echo
            if not (should_go_raw or should_suppress_echo or echo_changed):
                return None
            assert self._save_mode is not None
            if should_suppress_echo:
                self.set_mode(self._suppress_echo(self._save_mode))
                self.telnet_writer.log.debug(
                    "auto: server echo without SGA, line mode (server WILL ECHO)"
                )
                return (False, wecho, False)
            self.set_mode(self._make_raw(self._save_mode, suppress_echo=True))
            self.telnet_writer.log.debug(
                "auto: %s (server %s ECHO)",
                (
                    "switching to raw mode"
                    if should_go_raw
                    else ("disabling" if wecho else "enabling") + " software echo"
                ),
                "WILL" if wecho else "WONT",
            )
            return (True if should_go_raw else switched_to_raw, wecho, not wecho)

        def determine_mode(self, mode: "Terminal.ModeDef") -> "Terminal.ModeDef":
            """
            Return copy of 'mode' with changes suggested for telnet connection.

            Auto mode (``_raw_mode is None``): follows the server's negotiation.

            =================  ========  ==========  ========================================
            Server negotiates  ICANON    ECHO        Behavior
            =================  ========  ==========  ========================================
            Nothing            on        on          Line mode, local echo
            LINEMODE EDIT      **on**    on          Cooked mode, kernel handles EC/EL/echo
            LINEMODE remote    **off**   **off**     Raw, server echoes
            WILL SGA only      **off**   on          Character-at-a-time, local echo
            WILL ECHO only     on        **off**     Line mode, server echoes
            WILL SGA + ECHO    **off**   **off**     Full kludge mode (most common)
            =================  ========  ==========  ========================================
            """
            raw_mode = _get_raw_mode(self.telnet_writer)
            will_echo = self.telnet_writer.will_echo
            will_sga = self._server_will_sga()
            # Auto mode (None): follow server negotiation
            if raw_mode is None:
                if self.telnet_writer.local_option.enabled(LINEMODE):
                    linemode_mode = self.telnet_writer.linemode
                    if linemode_mode.edit:
                        # RFC 1184 / NetBSD reference: LINEMODE EDIT means ICANON on.
                        # The kernel line discipline handles EC (VERASE), EL (VKILL),
                        # EW (VWERASE), and echo.  No software line editing needed.
                        self.telnet_writer.log.debug(
                            "auto: LINEMODE EDIT, cooked mode (kernel line editing)"
                        )
                        self.software_echo = False
                        return mode  # keep ICANON on; kernel handles EC/EL/EW and echo
                    self.telnet_writer.log.debug("auto: LINEMODE remote, raw input server echo")
                    return self._make_raw(mode, suppress_echo=True)
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

            This does **not** connect stdin -- call :meth:`connect_stdin`
            separately when an asyncio stdin reader is needed (the REPL
            manages its own stdin via blessed async_inkey).
            """
            write_fobj = sys.stdout
            if self._istty:
                write_fobj = sys.stdin
            loop = asyncio.get_running_loop()
            writer_transport, writer_protocol = await loop.connect_write_pipe(
                asyncio.streams.FlowControlMixin, write_fobj
            )
            return asyncio.StreamWriter(writer_transport, writer_protocol, None, loop)

        async def connect_stdin(self) -> asyncio.StreamReader:
            """
            Connect sys.stdin to an asyncio StreamReader.

            Must be called **after** any REPL session has finished, because the REPL and asyncio
            cannot both own the stdin file descriptor at the same time.
            """
            reader = asyncio.StreamReader()
            reader_protocol = asyncio.StreamReaderProtocol(reader)
            transport, _ = await asyncio.get_running_loop().connect_read_pipe(
                lambda: reader_protocol, sys.stdin
            )
            self._stdin_transport = transport
            return reader

        def disconnect_stdin(self, reader: asyncio.StreamReader) -> None:
            """Disconnect stdin pipe so the REPL can reclaim it."""
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
        with Terminal(telnet_writer=telnet_writer) as tty_shell:
            await _telnet_client_shell_impl(telnet_reader, telnet_writer, tty_shell)
