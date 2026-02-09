"""Telnet client shell implementations for interactive terminal sessions."""

# pylint: disable=too-complex

# std imports
import sys
import asyncio
import collections
from typing import Any, Tuple, Union, Optional

# local
from . import accessories
from .stream_reader import TelnetReader, TelnetReaderUnicode
from .stream_writer import TelnetWriter, TelnetWriterUnicode

__all__ = ("telnet_client_shell",)


if sys.platform == "win32":

    async def telnet_client_shell(
        telnet_reader: Union[TelnetReader, TelnetReaderUnicode],
        telnet_writer: Union[TelnetWriter, TelnetWriterUnicode],
    ) -> None:
        """Win32 telnet client shell (not implemented)."""
        raise NotImplementedError("win32 not yet supported as telnet client. Please contribute!")

else:
    # std imports
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

        def __enter__(self) -> "Terminal":
            self._save_mode = self.get_mode()
            if self._istty:
                assert self._save_mode is not None
                self.set_mode(self.determine_mode(self._save_mode))
            return self

        def __exit__(self, *_: Any) -> None:
            if self._istty:
                assert self._save_mode is not None
                termios.tcsetattr(self._fileno, termios.TCSAFLUSH, list(self._save_mode))

        def get_mode(self) -> Optional["Terminal.ModeDef"]:
            """Return current terminal mode if attached to a tty, otherwise None."""
            if self._istty:
                return self.ModeDef(*termios.tcgetattr(self._fileno))
            return None

        def set_mode(self, mode: "Terminal.ModeDef") -> None:
            """Set terminal mode attributes."""
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSAFLUSH, list(mode))

        def determine_mode(self, mode: "Terminal.ModeDef") -> "Terminal.ModeDef":
            """Return copy of 'mode' with changes suggested for telnet connection."""
            if not self.telnet_writer.will_echo:
                # return mode as-is
                self.telnet_writer.log.debug("local echo, linemode")
                return mode
            self.telnet_writer.log.debug("server echo, kludge mode")

            # "Raw mode", see tty.py function setraw.  This allows sending
            # of ^J, ^C, ^S, ^\, and others, which might otherwise
            # interrupt with signals or map to another character.  We also
            # trust the remote server to manage CR/LF without mapping.
            #
            iflag = mode.iflag & ~(
                termios.BRKINT
                | termios.ICRNL  # Do not send INTR signal on break
                | termios.INPCK  # Do not map CR to NL on input
                | termios.ISTRIP  # Disable input parity checking
                | termios.IXON  # Do not strip input characters to 7 bits
            )  # Disable START/STOP output control

            # Disable parity generation and detection,
            # Select eight bits per byte character size.
            cflag = mode.cflag & ~(termios.CSIZE | termios.PARENB)
            cflag = cflag | termios.CS8

            # Disable canonical input (^H and ^C processing),
            # disable any other special control characters,
            # disable checking for INTR, QUIT, and SUSP input.
            lflag = mode.lflag & ~(termios.ICANON | termios.IEXTEN | termios.ISIG | termios.ECHO)

            # Disable post-output processing,
            # such as mapping LF('\n') to CRLF('\r\n') in output.
            oflag = mode.oflag & ~(termios.OPOST | termios.ONLCR)

            # "A pending read is not satisfied until MIN bytes are received
            #  (i.e., the pending read until MIN bytes are received), or a
            #  signal is received.  A program that uses this case to read
            #  record-based terminal I/O may block indefinitely in the read
            #  operation."
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

        async def make_stdio(self) -> Tuple[asyncio.StreamReader, asyncio.StreamWriter]:
            """Return (reader, writer) pair for sys.stdin, sys.stdout."""
            reader = asyncio.StreamReader()
            reader_protocol = asyncio.StreamReaderProtocol(reader)

            # Thanks:
            #
            #   https://gist.github.com/nathan-hoad/8966377
            #
            # After some experimentation, this 'sameopenfile' conditional seems
            # allow us to handle stdin as a pipe or a keyboard.  In the case of
            # a tty, 0 and 1 are the same open file, we use:
            #
            #    https://github.com/orochimarufan/.files/blob/master/bin/mpr
            write_fobj = sys.stdout
            if self._istty:
                write_fobj = sys.stdin
            loop = asyncio.get_event_loop()
            writer_transport, writer_protocol = await loop.connect_write_pipe(
                asyncio.streams.FlowControlMixin, write_fobj
            )

            writer = asyncio.StreamWriter(writer_transport, writer_protocol, None, loop)

            await loop.connect_read_pipe(lambda: reader_protocol, sys.stdin)

            return reader, writer

    # pylint: disable=too-many-locals,too-many-branches,too-many-statements,too-many-nested-blocks
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
            if term._istty and telnet_writer.will_echo:  # pylint: disable=protected-access
                linesep = "\r\n"
            stdin, stdout = await term.make_stdio()
            escape_name = accessories.name_unicode(keyboard_escape)
            stdout.write(f"Escape character is '{escape_name}'.{linesep}".encode())

            # Setup SIGWINCH handler to send NAWS on terminal resize (POSIX only).
            # We debounce to avoid flooding on continuous resizes.
            loop = asyncio.get_event_loop()
            winch_pending: dict[str, Optional[asyncio.TimerHandle]] = {"h": None}
            remove_winch = False
            if term._istty:  # pylint: disable=protected-access
                try:

                    def _send_naws() -> None:
                        # local
                        from .telopt import NAWS  # pylint: disable=import-outside-toplevel

                        try:
                            if (
                                telnet_writer.local_option.enabled(NAWS)
                                and not telnet_writer.is_closing()
                            ):
                                telnet_writer._send_naws()  # pylint: disable=protected-access
                        except Exception:  # pylint: disable=broad-exception-caught
                            pass

                    def _on_winch() -> None:
                        h = winch_pending.get("h")
                        if h is not None and not h.cancelled():
                            try:
                                h.cancel()
                            except Exception:  # pylint: disable=broad-exception-caught
                                pass
                        winch_pending["h"] = loop.call_later(0.05, _send_naws)

                    if hasattr(signal, "SIGWINCH"):
                        loop.add_signal_handler(signal.SIGWINCH, _on_winch)
                        remove_winch = True
                except Exception:  # pylint: disable=broad-exception-caught
                    remove_winch = False

            stdin_task = accessories.make_reader_task(stdin)
            telnet_task = accessories.make_reader_task(telnet_reader, size=2**24)
            wait_for = set([stdin_task, telnet_task])
            while wait_for:
                done, _ = await asyncio.wait(wait_for, return_when=asyncio.FIRST_COMPLETED)

                # Prefer handling stdin events first to avoid starvation under heavy output
                if stdin_task in done:
                    task = stdin_task
                    done.discard(task)
                else:
                    task = done.pop()
                wait_for.discard(task)

                telnet_writer.log.debug("task=%s, wait_for=%s", task, wait_for)

                # client input
                if task == stdin_task:
                    inp = task.result()
                    if inp:
                        if keyboard_escape in inp.decode():
                            # on ^], close connection to remote host
                            try:
                                telnet_writer.close()
                            except Exception:  # pylint: disable=broad-exception-caught
                                pass
                            if telnet_task in wait_for:
                                telnet_task.cancel()
                                wait_for.remove(telnet_task)
                            _cf = getattr(telnet_writer, '_color_filter', None)
                            if _cf is not None:
                                _flush = _cf.flush()
                                if _flush:
                                    stdout.write(_flush.encode())
                            stdout.write(f"\033[m{linesep}Connection closed.{linesep}".encode())
                            # Cleanup resize handler on local escape close
                            if term._istty and remove_winch:  # pylint: disable=protected-access
                                try:
                                    loop.remove_signal_handler(signal.SIGWINCH)
                                except Exception:  # pylint: disable=broad-exception-caught
                                    pass
                            h = winch_pending.get("h")
                            if h is not None:
                                try:
                                    h.cancel()
                                except Exception:  # pylint: disable=broad-exception-caught
                                    pass
                            break
                        telnet_writer.write(inp.decode())
                        stdin_task = accessories.make_reader_task(stdin)
                        wait_for.add(stdin_task)
                    else:
                        telnet_writer.log.debug("EOF from client stdin")

                # server output
                if task == telnet_task:
                    out = task.result()

                    # TODO: We should not require to check for '_eof' value,
                    # but for some systems, htc.zapto.org, it is required,
                    # where b'' is received even though connection is on?.
                    if not out and telnet_reader._eof:  # pylint: disable=protected-access
                        if stdin_task in wait_for:
                            stdin_task.cancel()
                            wait_for.remove(stdin_task)
                        _cf = getattr(telnet_writer, '_color_filter', None)
                        if _cf is not None:
                            _flush = _cf.flush()
                            if _flush:
                                stdout.write(_flush.encode())
                        stdout.write(
                            f"\033[m{linesep}Connection closed by foreign host.{linesep}".encode()
                        )
                        # Cleanup resize handler on remote close
                        if term._istty and remove_winch:  # pylint: disable=protected-access
                            try:
                                loop.remove_signal_handler(signal.SIGWINCH)
                            except Exception:  # pylint: disable=broad-exception-caught
                                pass
                        h = winch_pending.get("h")
                        if h is not None:
                            try:
                                h.cancel()
                            except Exception:  # pylint: disable=broad-exception-caught
                                pass
                    else:
                        _cf = getattr(telnet_writer, '_color_filter', None)
                        if _cf is not None:
                            out = _cf.filter(out)
                        stdout.write(out.encode() or b":?!?:")
                        telnet_task = accessories.make_reader_task(telnet_reader, size=2**24)
                        wait_for.add(telnet_task)
