"""Module provides class BaseClient."""

from __future__ import annotations

# std imports
import sys
import types
import asyncio
import logging
import weakref
import datetime
import traceback
import collections
from typing import Any, Type, Union, Callable, Optional, cast

# local
from ._types import ShellCallback
from .accessories import TRACE, hexdump
from .telopt import DO, WILL, theNULL, name_commands
from .stream_reader import TelnetReader, TelnetReaderUnicode
from .stream_writer import TelnetWriter, TelnetWriterUnicode

__all__ = ("BaseClient",)

# Pre-allocated single-byte cache to avoid per-byte bytes() allocations
_ONE_BYTE = [bytes([i]) for i in range(256)]


class BaseClient(asyncio.streams.FlowControlMixin, asyncio.Protocol):
    """Base Telnet Client Protocol."""

    _when_connected: Optional[datetime.datetime] = None
    _last_received: Optional[datetime.datetime] = None
    _transport: Optional[asyncio.Transport] = None
    _closing = False
    _reader_factory = TelnetReader
    _reader_factory_encoding = TelnetReaderUnicode
    _writer_factory = TelnetWriter
    _writer_factory_encoding = TelnetWriterUnicode
    _check_later: Optional[asyncio.Handle] = None

    def __init__(  # pylint: disable=too-many-positional-arguments
        self,
        shell: Optional[ShellCallback] = None,
        encoding: Union[str, bool] = "utf8",
        encoding_errors: str = "strict",
        force_binary: bool = False,
        connect_minwait: float = 0,
        connect_maxwait: float = 4.0,
        limit: Optional[int] = None,
        waiter_closed: Optional[asyncio.Future[None]] = None,
        _waiter_connected: Optional[asyncio.Future[None]] = None,
    ) -> None:
        """Class initializer."""
        super().__init__()
        self.log = logging.getLogger("telnetlib3.client")

        #: encoding for new connections
        self.default_encoding = encoding
        self._encoding_errors = encoding_errors
        self.force_binary = force_binary
        self._extra: dict[str, Any] = {}
        self.waiter_closed = waiter_closed or asyncio.Future()
        #: a future used for testing
        self._waiter_connected = _waiter_connected or asyncio.Future()
        self._tasks: list[Any] = []
        self.shell = shell
        #: minimum duration for :meth:`check_negotiation`.
        self.connect_minwait = connect_minwait
        #: maximum duration for :meth:`check_negotiation`.
        self.connect_maxwait = connect_maxwait
        self.reader: Optional[Union[TelnetReader, TelnetReaderUnicode]] = None
        self.writer: Optional[Union[TelnetWriter, TelnetWriterUnicode]] = None
        self._limit = limit

        # High-throughput receive pipeline
        self._rx_queue: collections.deque[bytes] = collections.deque()
        self._rx_bytes = 0
        self._rx_task: Optional[asyncio.Task[Any]] = None
        self._reading_paused = False
        # Apply backpressure to transport when our queue grows too large
        self._read_high = 512 * 1024  # pause_reading() above this many buffered bytes
        self._read_low = 256 * 1024  # resume_reading() below this many buffered bytes

    # Base protocol methods

    def eof_received(self) -> None:
        """Called when the other end calls write_eof() or equivalent."""
        self.log.debug("EOF from server, closing.")
        self.connection_lost(None)

    def connection_lost(self, exc: Optional[Exception]) -> None:
        """
        Called when the connection is lost or closed.

        :param exc: Exception instance, or ``None`` to indicate
            a closing EOF sent by this end.
        """
        if self._closing:
            return
        self._closing = True

        # inform yielding readers about closed connection
        assert self.reader is not None
        if exc is None:
            self.log.info("Connection closed to %s", self)
            self.reader.feed_eof()
        else:
            self.log.info("Connection lost to %s: %s", self, exc)
            self.reader.set_exception(exc)

        # cancel protocol tasks, namely on-connect negotiations
        for task in self._tasks:
            task.cancel()

        # close transport (may already be closed), set waiter_closed and
        # cancel Future _waiter_connected.
        assert self._transport is not None
        self._transport.close()
        if not self._waiter_connected.done():
            # strangely, for symmetry, our '_waiter_connected' must be set if
            # we are disconnected before negotiation may be considered
            # complete.  We set waiter_closed, and any function consuming
            # the StreamReader will receive eof.
            self._waiter_connected.set_result(None)

        if self.shell is None and not self.waiter_closed.done():
            # when a shell is defined, we allow the completion of the coroutine
            # to set the result of waiter_closed.
            self.waiter_closed.set_result(weakref.proxy(self))

        # break circular references.
        self._transport = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        """
        Called when a connection is made.

        Ensure ``super().connection_made(transport)`` is called when derived.
        """
        _transport = cast(asyncio.Transport, transport)
        self._transport = _transport
        self._when_connected = datetime.datetime.now()
        self._last_received = datetime.datetime.now()

        reader_factory: type[TelnetReader] | type[TelnetReaderUnicode] = self._reader_factory
        writer_factory: type[TelnetWriter] | type[TelnetWriterUnicode] = self._writer_factory

        reader_kwds: dict[str, Any] = {}
        writer_kwds: dict[str, Any] = {}

        if self.default_encoding:
            reader_kwds["fn_encoding"] = self.encoding
            writer_kwds["fn_encoding"] = self.encoding
            reader_kwds["encoding_errors"] = self._encoding_errors
            writer_kwds["encoding_errors"] = self._encoding_errors
            reader_factory = self._reader_factory_encoding
            writer_factory = self._writer_factory_encoding

        if self._limit:
            reader_kwds["limit"] = self._limit

        self.reader = reader_factory(**reader_kwds)
        # Attach transport so TelnetReader can apply pause_reading/resume_reading
        try:
            self.reader.set_transport(_transport)
        except Exception:  # pylint: disable=broad-exception-caught
            # Reader may not support transport coupling; ignore.
            pass

        self.writer = writer_factory(
            transport=_transport, protocol=self, reader=self.reader, client=True, **writer_kwds
        )

        self.log.info("Connected to %s", self)

        self._waiter_connected.add_done_callback(self.begin_shell)
        asyncio.get_event_loop().call_soon(self.begin_negotiation)

    def begin_shell(self, future: asyncio.Future[None]) -> None:
        """Start the shell coroutine after negotiation completes."""
        # Don't start shell if the connection was cancelled or errored
        if future.cancelled() or future.exception() is not None:
            return
        if self.shell is not None:
            assert self.reader is not None and self.writer is not None
            coro = self.shell(self.reader, self.writer)
            if asyncio.iscoroutine(coro):
                # When a shell is defined as a coroutine, we must ensure
                # that self.waiter_closed is not closed until the shell
                # has had an opportunity to respond to EOF.  Because
                # feed_eof() occurs in connection_lost(), we must allow
                # the event loop to return to our shell coroutine before
                # the waiter_closed future is set.
                #
                # We accomplish this by chaining the completion of the
                # shell future to set the result of the waiter_closed
                # future.
                fut = asyncio.get_event_loop().create_task(coro)
                fut.add_done_callback(
                    lambda fut_obj: (
                        self.waiter_closed.set_result(weakref.proxy(self))
                        if self.waiter_closed is not None
                        and not self.waiter_closed.done()
                        else None
                    )
                )

    def data_received(self, data: bytes) -> None:
        """
        Process bytes received by transport.

        Buffer incoming data and schedule async processing to keep the event loop responsive. Apply
        read-side backpressure using transport.pause_reading()/resume_reading().
        """
        if self.log.isEnabledFor(TRACE):
            self.log.log(TRACE, "recv %d bytes\n%s", len(data), hexdump(data, prefix="<<  "))
        self._last_received = datetime.datetime.now()

        # Detect SyncTERM font switching sequences and auto-switch encoding.
        self._detect_syncterm_font(data)

        # Enqueue and account for buffered size
        self._rx_queue.append(data)
        self._rx_bytes += len(data)

        # Start processor task if not running
        if self._rx_task is None or self._rx_task.done():
            loop = asyncio.get_event_loop()
            self._rx_task = loop.create_task(self._process_rx())

        # Pause reading if buffered bytes exceed high watermark
        if not self._reading_paused and self._rx_bytes >= self._read_high:
            if self._transport is not None:
                try:
                    self._transport.pause_reading()
                    self._reading_paused = True
                except Exception:  # pylint: disable=broad-exception-caught
                    # Some transports may not support pause_reading; ignore.
                    pass

    def _detect_syncterm_font(self, data: bytes) -> None:
        """Scan *data* for SyncTERM font selection and switch encoding.

        When :attr:`_encoding_explicit` is set on the writer (indicating
        the user passed ``--encoding``), the font switch is logged but
        does not override the encoding.
        """
        if self.writer is None:
            return
        from .server_fingerprinting import (  # pylint: disable=import-outside-toplevel
            detect_syncterm_font,
            _SYNCTERM_BINARY_ENCODINGS,
        )
        encoding = detect_syncterm_font(data)
        if encoding is not None:
            self.log.debug("SyncTERM font switch: %s", encoding)
            if getattr(self.writer, '_encoding_explicit', False):
                self.log.debug(
                    "ignoring font switch, explicit encoding: %s",
                    self.writer.environ_encoding)
            else:
                self.writer.environ_encoding = encoding
            if encoding in _SYNCTERM_BINARY_ENCODINGS:
                self.force_binary = True

    # public properties

    @property
    def duration(self) -> float:
        """Time elapsed since client connected, in seconds as float."""
        assert self._when_connected is not None
        return (datetime.datetime.now() - self._when_connected).total_seconds()

    @property
    def idle(self) -> float:
        """Time elapsed since data last received, in seconds as float."""
        assert self._last_received is not None
        return (datetime.datetime.now() - self._last_received).total_seconds()

    # public protocol methods

    def __repr__(self) -> str:
        hostport = self.get_extra_info("peername", ["-", "closing"])[:2]
        return f"<Peer {hostport[0]} {hostport[1]}>"

    def get_extra_info(self, name: str, default: Any = None) -> Any:
        """Get optional client protocol or transport information."""
        if self._transport:
            default = self._transport.get_extra_info(name, default)
        return self._extra.get(name, default)

    def begin_negotiation(self) -> None:
        """
        Begin on-connect negotiation.

        A Telnet client is expected to send only a minimal amount of client
        session options immediately after connection, it is generally the
        server which dictates server option support.

        Deriving implementations should always call
        ``super().begin_negotiation()``.
        """
        self._check_later = asyncio.get_event_loop().call_soon(self._check_negotiation_timer)
        self._tasks.append(self._check_later)

        # Send proactive WILL/DO for any "always" options
        if self.writer is not None:
            for opt in self.writer.always_will:
                self.writer.iac(WILL, opt)
            for opt in self.writer.always_do:
                self.writer.iac(DO, opt)

    def encoding(self, outgoing: bool = False, incoming: bool = False) -> Union[str, bool]:
        """
        Encoding that should be used for the direction indicated.

        The base implementation **always** returns ``encoding`` argument
        given to class initializer or, when unset (``None``), ``US-ASCII``.
        """
        # pylint: disable=unused-argument
        return self.default_encoding or "US-ASCII"  # pragma: no cover

    def check_negotiation(self, final: bool = False) -> bool:
        """
        Callback, return whether negotiation is complete.

        :param final: Whether this is the final time this callback
            will be requested to answer regarding protocol negotiation.
        :returns: Whether negotiation is over (client end is satisfied).

        Method is called on each new command byte processed until negotiation is
        considered final, or after :attr:`connect_maxwait` has elapsed, setting
        the ``_waiter_connected`` attribute to value ``self`` when complete.

        If critical negotiations have completed (TTYPE and either NEW_ENVIRON or CHARSET),
        negotiation is considered complete immediately without waiting for connect_minwait.
        Otherwise, this method returns False until :attr:`connect_minwait` has elapsed,
        ensuring the server may batch telnet negotiation demands without
        prematurely entering the callback shell.

        Ensure ``super().check_negotiation()`` is called and conditionally
        combined when derived.
        """
        # pylint: disable=import-outside-toplevel
        # local
        from .telopt import TTYPE, CHARSET, NEW_ENVIRON

        # First check if there are any pending options
        assert self.writer is not None
        if any(self.writer.pending_option.values()):
            return False

        # Check if critical options are enabled (terminal type and encoding info)
        have_terminal_type = self.writer.local_option.enabled(TTYPE)
        have_environ = self.writer.local_option.enabled(NEW_ENVIRON)
        have_charset = self.writer.remote_option.enabled(
            CHARSET
        ) and self.writer.local_option.enabled(CHARSET)

        # If we have terminal type and either environment or charset info, we can bypass the minwait
        critical_options_negotiated = have_terminal_type and (have_environ or have_charset)

        if critical_options_negotiated:
            if final:
                self.log.debug("Critical options negotiated, bypassing connect_minwait")
            return True

        # Otherwise, ensure we wait the minimum time for server to batch commands
        return self.duration > self.connect_minwait

    # private methods

    def _process_chunk(self, data: bytes) -> bool:  # pylint: disable=too-many-branches,too-complex
        """Process a chunk of received bytes; return True if any IAC/SB cmd observed."""
        # This mirrors the previous optimized logic, but is called from an async task.
        self._last_received = datetime.datetime.now()

        assert self.writer is not None
        assert self.reader is not None
        writer = self.writer
        reader = self.reader

        # Snapshot whether SLC snooping is required for this chunk
        try:
            mode = writer.mode  # property
        except Exception:  # pylint: disable=broad-exception-caught
            mode = "local"
        slc_needed = (mode == "remote") or (mode == "kludge" and writer.slc_simulated)

        cmd_received = False

        # Precompute SLC trigger set if needed
        slc_vals = None
        if slc_needed:
            slc_vals = {defn.val[0] for defn in writer.slctab.values() if defn.val != theNULL}

        n = len(data)
        i = 0
        out_start = 0
        feeding_oob = bool(writer.is_oob)

        # Build set of special bytes for fast lookup
        special_bytes = frozenset({255} | (slc_vals or set()))

        while i < n:
            if not feeding_oob:
                # Scan forward until next special byte (IAC or SLC trigger)
                if not slc_vals:
                    # Fast path: only IAC (255) is special - use C-level find
                    next_iac = data.find(255, i)
                    if next_iac == -1:
                        # No IAC found, consume rest of chunk
                        if n > out_start:
                            reader.feed_data(data[out_start:])
                        return cmd_received
                    i = next_iac
                else:
                    # Slow path: SLC bytes also special - scan byte by byte
                    while i < n and data[i] not in special_bytes:
                        i += 1
                # Flush non-special run
                if i > out_start:
                    reader.feed_data(data[out_start:i])
                if i >= n:
                    out_start = i
                    break
            # At a special byte or in the middle of an IAC sequence
            b = data[i]
            try:
                recv_inband = writer.feed_byte(_ONE_BYTE[b])
            except Exception:  # pylint: disable=broad-exception-caught
                self._log_exception(self.log.warning, *sys.exc_info())
            else:
                if recv_inband:
                    # Only forward the single-byte SLC or in-band special
                    reader.feed_data(data[i : i + 1])
                else:
                    cmd_received = True
            i += 1
            out_start = i
            # Continue per-byte feeding while writer indicates out-of-band processing
            feeding_oob = bool(writer.is_oob)

        # Any trailing non-special bytes
        if out_start < n:
            reader.feed_data(data[out_start:])

        return cmd_received

    async def _process_rx(self) -> None:
        """Async processor for receive queue that yields control and applies backpressure."""
        processed = 0
        any_cmd = False
        try:
            while self._rx_queue:
                chunk = self._rx_queue.popleft()
                self._rx_bytes -= len(chunk)

                cmd = self._process_chunk(chunk)
                any_cmd = any_cmd or cmd
                processed += len(chunk)

                # Resume reading when we've drained below low watermark
                if self._reading_paused and self._rx_bytes <= self._read_low:
                    if self._transport is not None:
                        try:
                            self._transport.resume_reading()
                            self._reading_paused = False
                        except Exception:  # pylint: disable=broad-exception-caught
                            pass

                # Yield periodically to keep loop responsive without excessive context switching
                if processed >= 128 * 1024:
                    await asyncio.sleep(0)
                    processed = 0
        finally:
            self._rx_task = None
            # Aggressively re-check negotiation if any command was seen and not yet connected
            if any_cmd and not self._waiter_connected.done():
                self._check_negotiation_timer()

    def _check_negotiation_timer(self) -> None:
        assert self._check_later is not None
        self._check_later.cancel()
        self._tasks.remove(self._check_later)

        later = self.connect_maxwait - self.duration
        final = bool(later < 0)

        if self.check_negotiation(final=final):
            self.log.debug("negotiation complete after %1.2fs.", self.duration)
            self._waiter_connected.set_result(None)
        elif final:
            self.log.debug("negotiation failed after %1.2fs.", self.duration)
            assert self.writer is not None
            _failed = [
                name_commands(cmd_option)
                for (cmd_option, pending) in self.writer.pending_option.items()
                if pending
            ]
            self.log.debug("failed-reply: %r", ", ".join(_failed))
            self._waiter_connected.set_result(None)
        else:
            # keep re-queuing until complete.  Aggressively re-queue until
            # connect_minwait, or connect_maxwait, whichever occurs next
            # in our time-series.
            sooner = self.connect_minwait - self.duration
            if sooner > 0:
                later = sooner
            self._check_later = asyncio.get_event_loop().call_later(
                later, self._check_negotiation_timer
            )
            self._tasks.append(self._check_later)

    @staticmethod
    def _log_exception(
        logger: Callable[..., Any],
        e_type: Optional[Type[BaseException]],
        e_value: Optional[BaseException],
        e_tb: Optional[types.TracebackType],
    ) -> None:
        rows_tbk = [line for line in "\n".join(traceback.format_tb(e_tb)).split("\n") if line]
        rows_exc = [line.rstrip() for line in traceback.format_exception_only(e_type, e_value)]

        for line in rows_tbk + rows_exc:
            logger(line)
