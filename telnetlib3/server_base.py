"""Module provides class BaseServer."""

from __future__ import annotations

# std imports
import sys
import types
import asyncio
import logging
import datetime
import traceback
from typing import Any, Type, Union, Callable, Optional

# local
from ._types import ShellCallback
from .telopt import theNULL
from .accessories import TRACE, hexdump
from .stream_reader import TelnetReader, TelnetReaderUnicode
from .stream_writer import TelnetWriter, TelnetWriterUnicode

__all__ = ("BaseServer",)

# Pre-allocated single-byte cache to avoid per-byte bytes() allocations
_ONE_BYTE = [bytes([i]) for i in range(256)]


logger = logging.getLogger("telnetlib3.server_base")


class BaseServer(asyncio.streams.FlowControlMixin, asyncio.Protocol):
    """Base Telnet Server Protocol."""

    _when_connected: Optional[datetime.datetime] = None
    _last_received: Optional[datetime.datetime] = None
    _transport = None
    _advanced = False
    _closing = False
    _check_later = None
    _rx_bytes = 0
    _tx_bytes = 0

    def __init__(  # pylint: disable=too-many-positional-arguments
        self,
        shell: Optional[ShellCallback] = None,
        _waiter_connected: Optional[asyncio.Future[None]] = None,
        encoding: Union[str, bool] = "utf8",
        encoding_errors: str = "strict",
        force_binary: bool = False,
        never_send_ga: bool = False,
        connect_maxwait: float = 4.0,
        limit: Optional[int] = None,
        reader_factory: type = TelnetReader,
        reader_factory_encoding: type = TelnetReaderUnicode,
        writer_factory: type = TelnetWriter,
        writer_factory_encoding: type = TelnetWriterUnicode,
    ) -> None:
        """Class initializer."""
        super().__init__()
        self.default_encoding = encoding
        self._encoding_errors = encoding_errors
        self.force_binary = force_binary
        self.never_send_ga = never_send_ga
        self._extra: dict[str, Any] = {}

        self._reader_factory = reader_factory
        self._reader_factory_encoding = reader_factory_encoding
        self._writer_factory = writer_factory
        self._writer_factory_encoding = writer_factory_encoding

        #: a future used for testing
        self._waiter_connected = _waiter_connected or asyncio.Future()
        self._tasks: list[Any] = [self._waiter_connected]
        self.shell = shell
        self.reader: Optional[Union[TelnetReader, TelnetReaderUnicode]] = None
        self.writer: Optional[Union[TelnetWriter, TelnetWriterUnicode]] = None
        #: maximum duration for :meth:`check_negotiation`.
        self.connect_maxwait = connect_maxwait
        self._limit = limit

    def timeout_connection(self) -> None:
        """Close the connection due to timeout."""
        assert self.reader is not None
        assert self.writer is not None
        self.reader.feed_eof()
        self.writer.close()

    # Base protocol methods

    def eof_received(self) -> None:
        """
        Called when the other end calls write_eof() or equivalent.

        This callback may be exercised by the nc(1) client argument ``-z``.
        """
        logger.debug("EOF from client, closing.")
        self.connection_lost(None)

    def connection_lost(self, exc: Optional[Exception]) -> None:
        """
        Called when the connection is lost or closed.

        :param exc: Exception instance, or ``None`` to indicate close by EOF.
        """
        if self._closing:
            return
        self._closing = True
        assert self.reader is not None

        # inform yielding readers about closed connection
        if exc is None:
            logger.info("Connection closed for %s", self)
            self.reader.feed_eof()
        else:
            logger.info("Connection lost for %s: %s", self, exc)
            self.reader.set_exception(exc)

        # cancel protocol tasks, namely on-connect negotiations
        for task in self._tasks:
            try:
                task.cancel()
            except Exception:  # pylint: disable=broad-exception-caught
                pass
        # drop references to scheduled tasks/callbacks
        self._tasks.clear()
        try:
            self._waiter_connected.remove_done_callback(self.begin_shell)
        except Exception:  # pylint: disable=broad-exception-caught
            pass

        # close transport (may already be closed), cancel Future _waiter_connected.
        if self._transport is not None:
            # Detach protocol from transport to drop strong reference immediately.
            try:
                if hasattr(self._transport, "set_protocol"):
                    self._transport.set_protocol(asyncio.Protocol())
            except Exception:  # pylint: disable=broad-exception-caught
                pass
            self._transport.close()
        if not self._waiter_connected.cancelled() and not self._waiter_connected.done():
            self._waiter_connected.cancel()

        # break circular references for transport; keep reader/writer available
        # for inspection by tests after close.
        self._transport = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        """
        Called when a connection is made.

        Sets attributes ``_transport``, ``_when_connected``, ``_last_received``,
        ``reader`` and ``writer``.

        Ensure ``super().connection_made(transport)`` is called when derived.
        """
        self._transport = transport
        self._when_connected = datetime.datetime.now()
        self._last_received = datetime.datetime.now()

        reader_factory = self._reader_factory
        writer_factory = self._writer_factory
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

        self.writer = writer_factory(
            transport=transport, protocol=self, reader=self.reader, server=True, **writer_kwds
        )

        logger.info("Connection from %s", self)

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
                loop = asyncio.get_event_loop()
                loop.create_task(coro)

    def data_received(self, data: bytes) -> None:  # pylint: disable=too-complex
        """
        Process bytes received by transport.

        This may seem strange; feeding all bytes received to the **writer**, and, only if they test
        positive, duplicating to the **reader**.

        The writer receives a copy of all raw bytes because, as an IAC interpreter, it may likely
        **write** a responding reply.
        """
        # pylint: disable=too-many-branches
        # This is a "hot path" method, and so it is not broken into "helper functions" to help with
        # performance.  Uses batched processing: scans for IAC (255) and SLC bytes, batching regular
        # data into single feed_data() calls for performance.  This can be done, and previously was,
        # more simply by processing a "byte at a time", but, this "batch and seek" solution can be
        # hundreds of times faster though much more complicated.
        #
        if logger.isEnabledFor(TRACE):
            logger.log(TRACE, "recv %d bytes\n%s", len(data), hexdump(data, prefix="<<  "))
        self._last_received = datetime.datetime.now()
        self._rx_bytes += len(data)
        assert self.writer is not None
        assert self.reader is not None
        writer = self.writer
        reader = self.reader

        # Build set of special bytes: IAC + SLC values when simulation enabled
        if writer.slc_simulated:
            slc_vals = {defn.val[0] for defn in writer.slctab.values() if defn.val != theNULL}
            special = frozenset({255} | slc_vals)
        else:
            special = None  # Only IAC is special

        cmd_received = False
        n = len(data)
        i = 0
        out_start = 0
        feeding_oob = bool(writer.is_oob)

        while i < n:
            if not feeding_oob:
                # Scan forward to next special byte
                if special is None:
                    # Fast path: only IAC (255) is special
                    next_special = data.find(255, i)
                    if next_special == -1:
                        # No IAC found - batch entire remainder
                        if n > out_start:
                            reader.feed_data(data[out_start:])
                        break
                    i = next_special
                else:
                    # SLC bytes also special
                    while i < n and data[i] not in special:
                        i += 1
                # Flush non-special bytes
                if i > out_start:
                    reader.feed_data(data[out_start:i])
                if i >= n:
                    break

            # Process special byte
            try:
                recv_inband = writer.feed_byte(_ONE_BYTE[data[i]])
            except ValueError as exc:
                logger.debug("Invalid telnet byte from %s: %s", self, exc)
            except BaseException:  # pylint: disable=broad-exception-caught
                self._log_exception(logger.warning, *sys.exc_info())
            else:
                if recv_inband:
                    reader.feed_data(data[i : i + 1])
                else:
                    cmd_received = True
            i += 1
            out_start = i
            feeding_oob = bool(writer.is_oob)

        # Re-check negotiation on command receipt
        if not self._waiter_connected.done() and cmd_received:
            self._check_negotiation_timer()

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

    @property
    def rx_bytes(self) -> int:
        """Total bytes received from client."""
        return self._rx_bytes

    @property
    def tx_bytes(self) -> int:
        """Total bytes sent to client."""
        return self._tx_bytes

    # public protocol methods

    def __repr__(self) -> str:
        hostport = self.get_extra_info("peername", ["-", "closing"])[:2]
        return f"<Peer {hostport[0]} {hostport[1]}>"

    def get_extra_info(self, name: str, default: Any = None) -> Any:
        """Get optional server protocol or transport information."""
        if self._transport:
            default = self._transport.get_extra_info(name, default)
        return self._extra.get(name, default)

    def begin_negotiation(self) -> None:
        """
        Begin on-connect negotiation.

        A Telnet server is expected to demand preferred session options
        immediately after connection.  Deriving implementations should always
        call ``super().begin_negotiation()``.
        """
        self._check_later = asyncio.get_event_loop().call_soon(self._check_negotiation_timer)
        self._tasks.append(self._check_later)

    def begin_advanced_negotiation(self) -> None:
        """
        Begin advanced negotiation.

        Callback method further requests advanced telnet options.  Called
        once on receipt of any ``DO`` or ``WILL`` acknowledgments
        received, indicating that the remote end is capable of negotiating
        further.

        Only called if sub-classing :meth:`begin_negotiation` causes
        at least one negotiation option to be affirmatively acknowledged.
        """

    def encoding(self, outgoing: bool = False, incoming: bool = False) -> Union[str, bool]:
        """
        Encoding that should be used for the direction indicated.

        The base implementation **always** returns the encoding given to class
        initializer, or, when unset (None), ``US-ASCII``.
        """
        # pylint: disable=unused-argument
        return self.default_encoding or "US-ASCII"

    def negotiation_should_advance(self) -> bool:
        """
        Whether advanced negotiation should commence.

        :returns: ``True`` if advanced negotiation should be permitted.

        The base implementation returns True if any negotiation options
        were affirmatively acknowledged by client, more than likely
        options requested in callback :meth:`begin_negotiation`.
        """
        # Generally, this separates a bare TCP connect() from a True
        # RFC-compliant telnet client with responding IAC interpreter.
        assert self.writer is not None
        server_do = sum(enabled for _, enabled in self.writer.remote_option.items())
        client_will = sum(enabled for _, enabled in self.writer.local_option.items())
        return bool(server_do or client_will)

    def check_negotiation(self, final: bool = False) -> bool:  # pylint: disable=unused-argument
        """
        Callback, return whether negotiation is complete.

        :param final: Whether this is the final time this callback
            will be requested to answer regarding protocol negotiation.
        :returns: Whether negotiation is over (server end is satisfied).

        Method is called on each new command byte processed until negotiation is
        considered final, or after ``connect_maxwait`` has elapsed, setting
        attribute ``_waiter_connected`` to value ``self`` when complete.

        Ensure ``super().check_negotiation()`` is called and conditionally
        combined when derived.
        """
        if not self._advanced and self.negotiation_should_advance():
            self._advanced = True
            logger.debug("begin advanced negotiation")
            asyncio.get_event_loop().call_soon(self.begin_advanced_negotiation)

        # negotiation is complete (returns True) when all negotiation options
        # that have been requested have been acknowledged.
        assert self.writer is not None
        return not any(self.writer.pending_option.values())

    # private methods

    def _check_negotiation_timer(self) -> None:
        if self._check_later is not None:
            self._check_later.cancel()
            if self._check_later in self._tasks:
                self._tasks.remove(self._check_later)

        later = self.connect_maxwait - self.duration
        final = bool(later < 0)

        if self.check_negotiation(final=final):
            logger.debug("negotiation complete after %1.2fs.", self.duration)
            self._waiter_connected.set_result(None)
        elif final:
            logger.debug("negotiation failed after %1.2fs.", self.duration)
            self._waiter_connected.set_result(None)
        else:
            # keep re-queuing until complete
            self._check_later = asyncio.get_event_loop().call_later(
                later, self._check_negotiation_timer
            )
            self._tasks.append(self._check_later)

    @staticmethod
    def _log_exception(
        log: Callable[..., Any],
        e_type: Optional[Type[BaseException]],
        e_value: Optional[BaseException],
        e_tb: Optional[types.TracebackType],
    ) -> None:
        rows_tbk = [line for line in "\n".join(traceback.format_tb(e_tb)).split("\n") if line]
        rows_exc = [line.rstrip() for line in traceback.format_exception_only(e_type, e_value)]

        for line in rows_tbk + rows_exc:
            log(line)
