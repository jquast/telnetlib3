"""Windows telnet client shell implementation using blessed/jinxed."""

# std imports
import os
import sys
import asyncio
import threading
import contextlib
import collections
from typing import Union, Callable, Optional

# local
from .client_shell import _get_raw_mode, _telnet_client_shell_impl
from .stream_reader import TelnetReader, TelnetReaderUnicode
from .stream_writer import TelnetWriter, TelnetWriterUnicode


class Terminal:
    """
    Context manager for terminal mode handling on Windows via blessed/jinxed.

    Blessed is a guaranteed dependency on Windows (pyproject.toml environment marker).
    Mirrors the interface of the POSIX ``Terminal`` class in :mod:`telnetlib3.client_shell`.
    """

    ModeDef = collections.namedtuple("ModeDef", ["raw", "echo"])

    def __init__(self, telnet_writer: Union[TelnetWriter, TelnetWriterUnicode]) -> None:
        """Class Initializer."""
        # imported locally, so that this module may be safely imported by non-windows sytems without
        # blessed, mainly just so that documentation (sphinx builds) work, doesn't matter otherwise.
        import blessed

        self.telnet_writer = telnet_writer
        self._bt = blessed.Terminal()
        self._istty = self._bt.is_a_tty
        self._save_mode: Optional[Terminal.ModeDef] = None
        self.software_echo = False
        self._raw_ctx = None
        self._resize_pending = threading.Event()
        self.on_resize: Optional[Callable] = None
        self._stop_resize = threading.Event()
        self._stop_stdin = threading.Event()
        self._resize_thread: Optional[threading.Thread] = None
        self._stdin_transport = None

    def __enter__(self) -> "Terminal":
        self._save_mode = self.get_mode()
        if self._istty and self._save_mode is not None:
            self.set_mode(self.determine_mode(self._save_mode))
        return self

    def __exit__(self, *_) -> None:
        self.cleanup_winch()
        if self._istty and self._save_mode is not None:
            self.set_mode(self._save_mode)

    def get_mode(self) -> Optional["Terminal.ModeDef"]:
        """Return current terminal mode if attached to a tty, otherwise None."""
        if not self._istty:
            return None
        return self.ModeDef(raw=False, echo=True)

    def set_mode(self, mode: "Terminal.ModeDef") -> None:
        """Switch terminal to raw or cooked mode using blessed context managers."""
        if mode is None:
            return
        ctx = self._raw_ctx
        if mode.raw and ctx is None:
            self._raw_ctx = contextlib.ExitStack()
            self._raw_ctx.enter_context(self._bt.raw())
        elif not mode.raw and ctx is not None:
            ctx.close()
            self._raw_ctx = None

    def _make_raw(self, mode: "Terminal.ModeDef", suppress_echo: bool = True) -> "Terminal.ModeDef":
        """Return a raw ModeDef (mirrors POSIX Terminal._make_raw interface)."""
        return self.ModeDef(raw=True, echo=not suppress_echo)

    @staticmethod
    def _suppress_echo(mode: "Terminal.ModeDef") -> "Terminal.ModeDef":
        """Return copy of *mode* with echo disabled."""
        return Terminal.ModeDef(raw=mode.raw, echo=False)

    def _server_will_sga(self) -> bool:
        """Whether SGA has been negotiated (either direction)."""
        from .telopt import SGA

        w = self.telnet_writer
        return bool(w.client and (w.remote_option.enabled(SGA) or w.local_option.enabled(SGA)))

    def determine_mode(self, mode: "Terminal.ModeDef") -> "Terminal.ModeDef":
        """
        Return the appropriate mode for the current telnet negotiation state.

        Mirrors :meth:`telnetlib3.client_shell.Terminal.determine_mode` using
        Windows ``ModeDef`` (raw/echo flags instead of termios bitfields).
        """
        raw_mode = _get_raw_mode(self.telnet_writer)
        will_echo = self.telnet_writer.will_echo
        will_sga = self._server_will_sga()
        if raw_mode is None:
            if will_echo and will_sga:
                return self._make_raw(mode)
            if will_echo:
                return self._suppress_echo(mode)
            if will_sga:
                self.software_echo = True
                return self._make_raw(mode, suppress_echo=False)
            return mode
        if not raw_mode:
            return mode
        return self._make_raw(mode)

    def check_auto_mode(
        self, switched_to_raw: bool, last_will_echo: bool
    ) -> "tuple[bool, bool, bool] | None":
        """
        Check if auto-mode switching is needed.

        Mirrors :meth:`telnetlib3.client_shell.Terminal.check_auto_mode`.

        :param switched_to_raw: Whether terminal has already switched to raw mode.
        :param last_will_echo: Previous value of server's WILL ECHO state.
        :returns: ``(switched_to_raw, last_will_echo, local_echo)`` tuple
            if mode changed, or ``None`` if no change needed.
        """
        if not self._istty:
            return None
        wecho = self.telnet_writer.will_echo
        wsga = self._server_will_sga()
        should_go_raw = not switched_to_raw and wsga
        should_suppress_echo = not switched_to_raw and wecho and not wsga
        echo_changed = switched_to_raw and wecho != last_will_echo
        if not (should_go_raw or should_suppress_echo or echo_changed):
            return None
        assert self._save_mode is not None
        if should_suppress_echo:
            self.set_mode(self._suppress_echo(self._save_mode))
            return (False, wecho, False)
        self.set_mode(self._make_raw(self._save_mode, suppress_echo=True))
        return (True if should_go_raw else switched_to_raw, wecho, not wecho)

    def setup_winch(self) -> None:
        """Poll for terminal size changes in a background thread."""
        if not self._istty:
            return
        self._stop_resize.clear()
        try:
            last_size = os.get_terminal_size()
        except OSError:
            return

        from .telopt import NAWS

        writer = self.telnet_writer
        loop = asyncio.get_running_loop()

        def _poll() -> None:
            nonlocal last_size
            while not self._stop_resize.wait(0.5):
                try:
                    new_size = os.get_terminal_size()
                    if new_size != last_size:
                        last_size = new_size
                        self._resize_pending.set()
                        if writer.local_option.enabled(NAWS):
                            loop.call_soon_threadsafe(writer._send_naws)
                except OSError:
                    pass

        self._resize_thread = threading.Thread(
            target=_poll, daemon=True, name="telnetlib3-resize-poll"
        )
        self._resize_thread.start()

    def cleanup_winch(self) -> None:
        """Stop the resize polling thread."""
        self._stop_resize.set()
        self._resize_thread = None

    async def make_stdout(self):
        """Return a StreamWriter-compatible wrapper for sys.stdout."""

        class _WindowsWriter:
            def write(self, data: bytes) -> None:
                """Write bytes to stdout and flush immediately."""
                sys.stdout.buffer.write(data)
                sys.stdout.buffer.flush()

            async def drain(self) -> None:
                """No-op drain; stdout writes are synchronous."""
                pass

        return _WindowsWriter()

    async def connect_stdin(self) -> asyncio.StreamReader:
        """
        Return an asyncio StreamReader fed by a blessed inkey() thread.

        Uses blessed/jinxed to read one keypress at a time in raw mode. Each keystroke is encoded as
        UTF-8 and fed to the reader.
        """
        reader = asyncio.StreamReader()
        loop = asyncio.get_running_loop()
        self._stop_stdin.clear()
        bt = self._bt

        def _reader_thread() -> None:
            while not self._stop_stdin.is_set():
                key = bt.inkey(timeout=0.1)
                if key:
                    data = str(key).encode("utf-8", errors="replace")
                    loop.call_soon_threadsafe(reader.feed_data, data)
            loop.call_soon_threadsafe(reader.feed_eof)

        t = threading.Thread(target=_reader_thread, daemon=True, name="telnetlib3-stdin-reader")
        t.start()
        return reader

    def disconnect_stdin(self, reader: asyncio.StreamReader) -> None:
        """Stop the stdin reader thread and signal EOF."""
        self._stop_stdin.set()
        reader.feed_eof()


async def telnet_client_shell(
    telnet_reader: Union[TelnetReader, TelnetReaderUnicode],
    telnet_writer: Union[TelnetWriter, TelnetWriterUnicode],
) -> None:
    """
    Windows telnet client shell using blessed/jinxed Terminal.

    Requires ``blessed>=1.20`` (installed automatically on Windows via the
    ``blessed; platform_system == 'Windows'`` dependency in pyproject.toml).
    """
    with Terminal(telnet_writer=telnet_writer) as tty_shell:
        await _telnet_client_shell_impl(telnet_reader, telnet_writer, tty_shell)
