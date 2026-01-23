"""
PTY shell implementation for telnetlib3.

This module provides the ability to spawn PTY-connected programs (bash, tmux, nethack, etc.) for
each telnet connection, with proper terminal negotiation forwarding.
"""

# std imports
import os
import pty
import sys
import errno
import fcntl
import codecs
import signal
import struct
import asyncio
import logging
import termios

# local
from .telopt import NAWS

__all__ = ("make_pty_shell", "pty_shell")

logger = logging.getLogger("telnetlib3.pty_shell")

# Synchronized Output sequences (DEC private mode 2026)
# https://gist.github.com/christianparpart/d8a62cc1ab659194337d73e399004036
_BSU = b"\x1b[?2026h"  # Begin Synchronized Update
_ESU = b"\x1b[?2026l"  # End Synchronized Update


def _platform_check():
    """Verify platform supports PTY operations."""
    if sys.platform == "win32":
        raise NotImplementedError("PTY support is not available on Windows")


class PTYSession:
    """Manages a PTY session lifecycle."""

    def __init__(self, reader, writer, program, args):
        """
        Initialize PTY session.

        :param reader: TelnetReader instance.
        :param writer: TelnetWriter instance.
        :param program: Path to program to execute.
        :param args: List of arguments for the program.
        """
        self.reader = reader
        self.writer = writer
        self.program = program
        self.args = args or []
        self.master_fd = None
        self.child_pid = None
        self._closing = False
        self._output_buffer = b""
        self._in_sync_update = False
        self._decoder = None
        self._decoder_charset = None

    def start(self):
        """Fork PTY, configure environment, and exec program."""
        _platform_check()

        env = self._build_environment()
        rows, cols = self._get_window_size()

        self.child_pid, self.master_fd = pty.fork()

        if self.child_pid == 0:
            self._setup_child(env, rows, cols)
        else:
            logger.debug(
                "forked PTY: program=%s pid=%d fd=%d",
                self.program,
                self.child_pid,
                self.master_fd,
            )
            self._setup_parent()
            pid, status = os.waitpid(self.child_pid, os.WNOHANG)
            if pid:
                logger.warning("child already exited: status=%d", status)

    def _build_environment(self):
        """Build environment dict from negotiated values."""
        env = os.environ.copy()

        term = self.writer.get_extra_info("TERM", "xterm")
        if term:
            # Terminfo entries are lowercase; telnet TTYPE may send uppercase
            env["TERM"] = term.lower()

        rows = self.writer.get_extra_info("rows")
        cols = self.writer.get_extra_info("cols")
        if rows:
            env["LINES"] = str(rows)
        if cols:
            env["COLUMNS"] = str(cols)

        lang = self.writer.get_extra_info("LANG")
        if lang:
            env["LANG"] = lang
            env["LC_ALL"] = lang
        else:
            charset = self.writer.get_extra_info("charset")
            if charset:
                env["LANG"] = f"en_US.{charset}"

        for key in ("DISPLAY", "USER", "COLORTERM", "HOME", "SHELL", "LOGNAME"):
            val = self.writer.get_extra_info(key)
            if val:
                env[key] = val

        return env

    def _get_window_size(self):
        """Get window size from negotiated values."""
        rows = self.writer.get_extra_info("rows", 25)
        cols = self.writer.get_extra_info("cols", 80)
        return rows, cols

    def _setup_child(self, env, rows, cols):
        """Child process setup before exec."""
        # Note: pty.fork() already calls setsid() for the child, so we don't need to

        if rows and cols:
            winsize = struct.pack("HHHH", rows, cols, 0, 0)
            fcntl.ioctl(sys.stdout.fileno(), termios.TIOCSWINSZ, winsize)

        # Configure PTY for telnet's character-at-a-time mode (WILL SGA, WILL ECHO).
        # Disable local echo and canonical mode, but keep output processing so
        # newlines are translated to CR-LF properly.
        attrs = termios.tcgetattr(sys.stdin.fileno())
        # c_lflag: disable ECHO (telnet handles echo) and ICANON (char-at-a-time)
        attrs[3] &= ~(termios.ECHO | termios.ICANON)
        # Keep c_oflag intact - OPOST and ONLCR translate \n to \r\n
        termios.tcsetattr(sys.stdin.fileno(), termios.TCSANOW, attrs)

        argv = [self.program] + self.args
        os.execvpe(self.program, argv, env)

    def _setup_parent(self):
        """Parent process setup after fork."""
        flags = fcntl.fcntl(self.master_fd, fcntl.F_GETFL)
        fcntl.fcntl(self.master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
        self.writer.set_ext_callback(NAWS, self._on_naws)

    def _on_naws(self, rows, cols):
        """Handle NAWS updates by resizing PTY."""
        self.writer.protocol.on_naws(rows, cols)
        self._set_window_size(rows, cols)

    def _set_window_size(self, rows, cols):
        """Set PTY window size and send SIGWINCH to child."""
        if self.master_fd is None or self.child_pid is None:
            return
        winsize = struct.pack("HHHH", rows, cols, 0, 0)
        fcntl.ioctl(self.master_fd, termios.TIOCSWINSZ, winsize)
        try:
            os.killpg(os.getpgid(self.child_pid), signal.SIGWINCH)
        except ProcessLookupError:
            pass

    async def run(self):
        """Bridge loop between telnet and PTY."""
        loop = asyncio.get_event_loop()
        pty_read_event = asyncio.Event()
        pty_data_queue = asyncio.Queue()

        pid, _ = os.waitpid(self.child_pid, os.WNOHANG)
        if pid:
            return

        def pty_readable():
            """Callback when PTY has data to read."""
            # Drain available data to reduce tearing, but cap at 256KB to avoid
            # buffering forever on continuous output (e.g., cat large_file)
            chunks = []
            total = 0
            max_batch = 262144  # 256KB
            while total < max_batch:
                try:
                    data = os.read(self.master_fd, 65536)
                    if data:
                        chunks.append(data)
                        total += len(data)
                    else:
                        self._closing = True
                        break
                except OSError as e:
                    if e.errno == errno.EAGAIN:
                        break  # No more data available
                    elif e.errno == errno.EIO:
                        self._closing = True
                        break
                    else:
                        logger.debug("PTY read error: %s", e)
                        self._closing = True
                        break
            if chunks:
                pty_data_queue.put_nowait(b"".join(chunks))
            pty_read_event.set()

        loop.add_reader(self.master_fd, pty_readable)

        try:
            await self._bridge_loop(pty_read_event, pty_data_queue)
        finally:
            try:
                loop.remove_reader(self.master_fd)
            except (ValueError, KeyError):
                pass

    async def _bridge_loop(self, pty_read_event, pty_data_queue):
        """Main bridge loop transferring data between telnet and PTY."""
        while not self._closing and not self.writer.is_closing():
            telnet_task = asyncio.create_task(self.reader.read(4096))
            pty_task = asyncio.create_task(pty_read_event.wait())

            done, pending = await asyncio.wait(
                {telnet_task, pty_task},
                return_when=asyncio.FIRST_COMPLETED,
            )

            for task in pending:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

            for task in done:
                try:
                    if task is telnet_task:
                        data = task.result()
                        if data:
                            self._write_to_pty(data)
                        else:
                            self._closing = True
                            break

                    elif task is pty_task:
                        task.result()
                        while not pty_data_queue.empty():
                            data = pty_data_queue.get_nowait()
                            self._write_to_telnet(data)
                        # EAGAIN was hit - flush any remaining partial line
                        self._flush_remaining()
                        pty_read_event.clear()
                except Exception as e:
                    logger.debug("bridge loop error: %s", e)
                    self._closing = True
                    break

    def _write_to_pty(self, data):
        """Write data from telnet to PTY."""
        if self.master_fd is None:
            return
        if isinstance(data, str):
            charset = self.writer.get_extra_info("charset") or "utf-8"
            data = data.encode(charset, errors="replace")
        try:
            os.write(self.master_fd, data)
        except OSError:
            self._closing = True

    def _write_to_telnet(self, data):
        """Write data from PTY to telnet, respecting synchronized update boundaries."""
        self._output_buffer += data

        # Process buffer, flushing on ESU or newline boundaries
        while True:
            if self._in_sync_update:
                # Look for End Synchronized Update
                esu_pos = self._output_buffer.find(_ESU)
                if esu_pos != -1:
                    # Flush up to and including ESU
                    end = esu_pos + len(_ESU)
                    self._flush_output(self._output_buffer[:end])
                    self._output_buffer = self._output_buffer[end:]
                    self._in_sync_update = False
                else:
                    # Still waiting for ESU, but flush if buffer too large
                    if len(self._output_buffer) > 262144:  # 256KB safety limit
                        self._flush_output(self._output_buffer)
                        self._output_buffer = b""
                    break
            else:
                # Look for Begin Synchronized Update
                bsu_pos = self._output_buffer.find(_BSU)
                if bsu_pos != -1:
                    # Flush everything before BSU (up to last newline if any)
                    if bsu_pos > 0:
                        self._flush_output(self._output_buffer[:bsu_pos])
                    self._output_buffer = self._output_buffer[bsu_pos:]
                    self._in_sync_update = True
                else:
                    # Flush up to and including last newline for line-oriented output
                    nl_pos = self._output_buffer.rfind(b"\n")
                    if nl_pos != -1:
                        end = nl_pos + 1
                        self._flush_output(self._output_buffer[:end])
                        self._output_buffer = self._output_buffer[end:]
                    # Keep any partial line in buffer (will flush on next newline,
                    # next sync boundary, or when more data arrives with EAGAIN)
                    break

    def _flush_output(self, data, final=False):
        """Send data to telnet client using incremental decoder."""
        if not data:
            return
        charset = self.writer.get_extra_info("charset") or "utf-8"

        # Get or create incremental decoder, recreating if charset changed
        if self._decoder is None or self._decoder_charset != charset:
            self._decoder = codecs.getincrementaldecoder(charset)(errors="replace")
            self._decoder_charset = charset

        # Decode using incremental decoder - it buffers incomplete sequences
        text = self._decoder.decode(data, final)
        if text:
            self.writer.write(text)

    def _flush_remaining(self):
        """Flush remaining buffer after EAGAIN (partial lines, prompts, etc.)."""
        if self._output_buffer and not self._in_sync_update:
            self._flush_output(self._output_buffer)
            self._output_buffer = b""

    def cleanup(self):
        """Kill child process and close PTY fd."""
        # Flush any remaining output buffer with final=True to emit buffered bytes
        if self._output_buffer:
            self._flush_output(self._output_buffer, final=True)
            self._output_buffer = b""

        if self.master_fd is not None:
            try:
                os.close(self.master_fd)
            except OSError:
                pass
            self.master_fd = None

        if self.child_pid is not None:
            try:
                os.kill(self.child_pid, signal.SIGTERM)
            except ProcessLookupError:
                pass

            try:
                os.waitpid(self.child_pid, os.WNOHANG)
            except ChildProcessError:
                pass
            self.child_pid = None


async def _wait_for_terminal_info(writer, timeout=2.0):
    """
    Wait for TERM and window size to be negotiated.

    :param writer: TelnetWriter instance.
    :param timeout: Maximum time to wait in seconds.
    """
    loop = asyncio.get_event_loop()
    start = loop.time()

    while loop.time() - start < timeout:
        term = writer.get_extra_info("TERM")
        rows = writer.get_extra_info("rows")
        if term and rows:
            return
        await asyncio.sleep(0.1)


async def pty_shell(reader, writer, program, args=None):
    """
    PTY shell callback for telnet server.

    :param reader: TelnetReader instance.
    :param writer: TelnetWriter instance.
    :param program: Path to program to execute.
    :param args: List of arguments for the program.
    """
    _platform_check()

    await _wait_for_terminal_info(writer, timeout=2.0)

    session = PTYSession(reader, writer, program, args)
    try:
        session.start()
        await session.run()
    finally:
        session.cleanup()
        if not writer.is_closing():
            writer.close()


def make_pty_shell(program, args=None):
    """
    Factory returning a shell callback for PTY execution.

    :param program: Path to program to execute.
    :param args: List of arguments for the program.
    :returns: Async shell callback suitable for use with create_server().

    Example usage::

        from telnetlib3 import create_server, make_pty_shell

        server = await create_server(
            host='localhost',
            port=6023,
            shell=make_pty_shell('/bin/bash', ['-l'])
        )
    """

    async def shell(reader, writer):
        await pty_shell(reader, writer, program, args)

    return shell
