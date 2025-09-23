"""Module provides class BaseClient."""

import logging
import datetime
import traceback
import asyncio
import collections
import weakref
import sys

from .stream_writer import TelnetWriter, TelnetWriterUnicode
from .stream_reader import TelnetReader, TelnetReaderUnicode
from .telopt import name_commands, theNULL

__all__ = ("BaseClient",)

# Pre-allocated single-byte cache to avoid per-byte bytes() allocations
_ONE_BYTE = [bytes([i]) for i in range(256)]


class BaseClient(asyncio.streams.FlowControlMixin, asyncio.Protocol):
    """Base Telnet Client Protocol."""

    _when_connected = None
    _last_received = None
    _transport = None
    _closing = False
    _reader_factory = TelnetReader
    _reader_factory_encoding = TelnetReaderUnicode
    _writer_factory = TelnetWriter
    _writer_factory_encoding = TelnetWriterUnicode

    def __init__(
        self,
        shell=None,
        encoding="utf8",
        encoding_errors="strict",
        force_binary=False,
        connect_minwait=1.0,
        connect_maxwait=4.0,
        limit=None,
        waiter_closed=None,
        _waiter_connected=None,
    ):
        """Class initializer."""
        super().__init__()
        self.log = logging.getLogger("telnetlib3.client")

        #: encoding for new connections
        self.default_encoding = encoding
        self._encoding_errors = encoding_errors
        self.force_binary = force_binary
        self._extra = dict()
        self.waiter_closed = waiter_closed or asyncio.Future()
        #: a future used for testing
        self._waiter_connected = _waiter_connected or asyncio.Future()
        self._tasks = []
        self.shell = shell
        #: minimum duration for :meth:`check_negotiation`.
        self.connect_minwait = connect_minwait
        #: maximum duration for :meth:`check_negotiation`.
        self.connect_maxwait = connect_maxwait
        self.reader = None
        self.writer = None
        self._limit = limit

        # High-throughput receive pipeline
        self._rx_queue = collections.deque()
        self._rx_bytes = 0
        self._rx_task = None
        self._reading_paused = False
        # Apply backpressure to transport when our queue grows too large
        self._read_high = 512 * 1024  # pause_reading() above this many buffered bytes
        self._read_low = 256 * 1024  # resume_reading() below this many buffered bytes

    # Base protocol methods

    def eof_received(self):
        """Called when the other end calls write_eof() or equivalent."""
        self.log.debug("EOF from server, closing.")
        self.connection_lost(None)

    def connection_lost(self, exc):
        """
        Called when the connection is lost or closed.

        :param Exception exc: exception.  ``None`` indicates
            a closing EOF sent by this end.
        """
        if self._closing:
            return
        self._closing = True

        # inform yielding readers about closed connection
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
        self._transport.close()
        if not self._waiter_connected.done():
            # strangely, for symmetry, our '_waiter_connected' must be set if
            # we are disconnected before negotiation may be considered
            # complete.  We set waiter_closed, and any function consuming
            # the StreamReader will receive eof.
            self._waiter_connected.set_result(weakref.proxy(self))

        if self.shell is None:
            # when a shell is defined, we allow the completion of the coroutine
            # to set the result of waiter_closed.
            self.waiter_closed.set_result(weakref.proxy(self))

        # break circular references.
        self._transport = None

    def connection_made(self, transport):
        """
        Called when a connection is made.

        Ensure ``super().connection_made(transport)`` is called when derived.
        """
        self._transport = transport
        self._when_connected = datetime.datetime.now()
        self._last_received = datetime.datetime.now()

        reader_factory = self._reader_factory
        writer_factory = self._writer_factory

        reader_kwds = {}
        writer_kwds = {}

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
            self.reader.set_transport(transport)
        except Exception:
            # Reader may not support transport coupling; ignore.
            pass

        self.writer = writer_factory(
            transport=transport,
            protocol=self,
            reader=self.reader,
            client=True,
            **writer_kwds
        )

        self.log.info("Connected to %s", self)

        self._waiter_connected.add_done_callback(self.begin_shell)
        asyncio.get_event_loop().call_soon(self.begin_negotiation)

    def begin_shell(self, result):
        if self.shell is not None:
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
                        else None
                    )
                )

    def data_received(self, data):
        """Process bytes received by transport."""
        # Buffer incoming data and schedule async processing to keep the event loop responsive.
        # Apply read-side backpressure using transport.pause_reading()/resume_reading().
        self._last_received = datetime.datetime.now()

        # Enqueue and account for buffered size
        self._rx_queue.append(data)
        self._rx_bytes += len(data)

        # Start processor task if not running
        if self._rx_task is None or self._rx_task.done():
            loop = asyncio.get_event_loop()
            self._rx_task = loop.create_task(self._process_rx())

        # Pause reading if buffered bytes exceed high watermark
        if not self._reading_paused and self._rx_bytes >= self._read_high:
            try:
                self._transport.pause_reading()
                self._reading_paused = True
            except Exception:
                # Some transports may not support pause_reading; ignore.
                pass

    # public properties

    @property
    def duration(self):
        """Time elapsed since client connected, in seconds as float."""
        return (datetime.datetime.now() - self._when_connected).total_seconds()

    @property
    def idle(self):
        """Time elapsed since data last received, in seconds as float."""
        return (datetime.datetime.now() - self._last_received).total_seconds()

    # public protocol methods

    def __repr__(self):
        hostport = self.get_extra_info("peername", ["-", "closing"])[:2]
        return "<Peer {0} {1}>".format(*hostport)

    def get_extra_info(self, name, default=None):
        """Get optional client protocol or transport information."""
        if self._transport:
            default = self._transport.get_extra_info(name, default)
        return self._extra.get(name, default)

    def begin_negotiation(self):
        """
        Begin on-connect negotiation.

        A Telnet client is expected to send only a minimal amount of client
        session options immediately after connection, it is generally the
        server which dictates server option support.

        Deriving implementations should always call
        ``super().begin_negotiation()``.
        """
        self._check_later = asyncio.get_event_loop().call_soon(
            self._check_negotiation_timer
        )
        self._tasks.append(self._check_later)

    def encoding(self, outgoing=False, incoming=False):
        """
        Encoding that should be used for the direction indicated.

        The base implementation **always** returns ``encoding`` argument
        given to class initializer or, when unset (``None``), ``US-ASCII``.
        """
        # pylint: disable=unused-argument
        return self.default_encoding or "US-ASCII"  # pragma: no cover

    def check_negotiation(self, final=False):
        """
        Callback, return whether negotiation is complete.

        :param bool final: Whether this is the final time this callback
            will be requested to answer regarding protocol negotiation.
        :returns: Whether negotiation is over (client end is satisfied).
        :rtype: bool

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
        from .telopt import TTYPE, NEW_ENVIRON, CHARSET, SB

        # First check if there are any pending options
        if any(self.writer.pending_option.values()):
            return False

        # Check if critical options are enabled (terminal type and encoding info)
        have_terminal_type = self.writer.local_option.enabled(TTYPE)
        have_environ = self.writer.local_option.enabled(NEW_ENVIRON)
        have_charset = self.writer.remote_option.enabled(
            CHARSET
        ) and self.writer.local_option.enabled(CHARSET)

        # If we have terminal type and either environment or charset info, we can bypass the minwait
        critical_options_negotiated = have_terminal_type and (
            have_environ or have_charset
        )

        if critical_options_negotiated:
            if final:
                self.log.debug("Critical options negotiated, bypassing connect_minwait")
            return True

        # Otherwise, ensure we wait the minimum time for server to batch commands
        return self.duration > self.connect_minwait

    # private methods

    def _process_chunk(self, data):
        """Process a chunk of received bytes; return True if any IAC/SB cmd observed."""
        # This mirrors the previous optimized logic, but is called from an async task.
        self._last_received = datetime.datetime.now()

        writer = self.writer
        reader = self.reader

        # Snapshot whether SLC snooping is required for this chunk
        try:
            mode = writer.mode  # property
        except Exception:
            mode = "local"
        slc_needed = (mode == "remote") or (mode == "kludge" and writer.slc_simulated)

        cmd_received = False

        # Precompute SLC trigger set if needed
        slc_vals = None
        if slc_needed:
            slc_vals = {
                defn.val[0] for defn in writer.slctab.values() if defn.val != theNULL
            }

        n = len(data)
        i = 0
        out_start = 0
        feeding_oob = False

        def is_special(b):
            return b == 255 or (slc_needed and slc_vals and b in slc_vals)

        while i < n:
            if not feeding_oob:
                # Scan forward until next special byte (IAC or SLC trigger)
                while i < n and not is_special(data[i]):
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
            except Exception:
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

    async def _process_rx(self):
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
                    try:
                        self._transport.resume_reading()
                        self._reading_paused = False
                    except Exception:
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

    def _check_negotiation_timer(self):
        self._check_later.cancel()
        self._tasks.remove(self._check_later)

        later = self.connect_maxwait - self.duration
        final = bool(later < 0)

        if self.check_negotiation(final=final):
            self.log.debug("negotiation complete after {:1.2f}s.".format(self.duration))
            self._waiter_connected.set_result(weakref.proxy(self))
        elif final:
            self.log.debug("negotiation failed after {:1.2f}s.".format(self.duration))
            _failed = [
                name_commands(cmd_option)
                for (cmd_option, pending) in self.writer.pending_option.items()
                if pending
            ]
            self.log.debug("failed-reply: {0!r}".format(", ".join(_failed)))
            self._waiter_connected.set_result(weakref.proxy(self))
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
    def _log_exception(logger, e_type, e_value, e_tb):
        rows_tbk = [
            line for line in "\n".join(traceback.format_tb(e_tb)).split("\n") if line
        ]
        rows_exc = [
            line.rstrip() for line in traceback.format_exception_only(e_type, e_value)
        ]

        for line in rows_tbk + rows_exc:
            logger(line)
