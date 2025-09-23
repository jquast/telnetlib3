"""Module provides class BaseServer."""

import traceback
import asyncio
import logging
import datetime
import weakref
import sys

from .stream_writer import TelnetWriter, TelnetWriterUnicode
from .stream_reader import TelnetReader, TelnetReaderUnicode

__all__ = ("BaseServer",)


logger = logging.getLogger("telnetlib3.server_base")


class BaseServer(asyncio.streams.FlowControlMixin, asyncio.Protocol):
    """Base Telnet Server Protocol."""

    _when_connected = None
    _last_received = None
    _transport = None
    _advanced = False
    _closing = False

    def __init__(
        self,
        shell=None,
        _waiter_connected=None,
        _waiter_closed=None,
        encoding="utf8",
        encoding_errors="strict",
        force_binary=False,
        connect_maxwait=4.0,
        limit=None,
        reader_factory=TelnetReader,
        reader_factory_encoding=TelnetReaderUnicode,
        writer_factory=TelnetWriter,
        writer_factory_encoding=TelnetWriterUnicode,
    ):
        """Class initializer."""
        super().__init__()
        self.default_encoding = encoding
        self._encoding_errors = encoding_errors
        self.force_binary = force_binary
        self._extra = dict()

        self._reader_factory = reader_factory
        self._reader_factory_encoding = reader_factory_encoding
        self._writer_factory = writer_factory
        self._writer_factory_encoding = writer_factory_encoding

        #: a future used for testing
        self._waiter_connected = _waiter_connected or asyncio.Future()
        #: a future used for testing
        self._waiter_closed = _waiter_closed or asyncio.Future()
        self._tasks = [self._waiter_connected]
        self.shell = shell
        self.reader = None
        self.writer = None
        #: maximum duration for :meth:`check_negotiation`.
        self.connect_maxwait = connect_maxwait
        self._limit = limit

    def timeout_connection(self):
        self.reader.feed_eof()
        self.writer.close()

    # Base protocol methods

    def eof_received(self):
        """
        Called when the other end calls write_eof() or equivalent.

        This callback may be exercised by the nc(1) client argument ``-z``.
        """
        logger.debug("EOF from client, closing.")
        self.connection_lost(None)

    def connection_lost(self, exc):
        """
        Called when the connection is lost or closed.

        :param Exception exc: exception.  ``None`` indicates close by EOF.
        """
        if self._closing:
            return
        self._closing = True

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
            except Exception:
                pass
        # drop references to scheduled tasks/callbacks
        self._tasks.clear()
        try:
            self._waiter_connected.remove_done_callback(self.begin_shell)
        except Exception:
            pass

        # close transport (may already be closed), set _waiter_closed and
        # cancel Future _waiter_connected.
        if self._transport is not None:
            # Detach protocol from transport to drop strong reference immediately.
            try:
                if hasattr(self._transport, "set_protocol"):
                    self._transport.set_protocol(asyncio.Protocol())
            except Exception:
                pass
            self._transport.close()
        if not self._waiter_connected.cancelled() and not self._waiter_connected.done():
            self._waiter_connected.cancel()
        if self.shell is None and self._waiter_closed is not None:
            # raise deprecation warning, _waiter_closed should not be used!
            self._waiter_closed.set_result(weakref.proxy(self))

        # break circular references for transport; keep reader/writer available
        # for inspection by tests after close.
        self._transport = None

    def connection_made(self, transport):
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

        self.writer = writer_factory(
            transport=transport,
            protocol=self,
            reader=self.reader,
            server=True,
            **writer_kwds
        )

        logger.info("Connection from %s", self)

        self._waiter_connected.add_done_callback(self.begin_shell)
        asyncio.get_event_loop().call_soon(self.begin_negotiation)

    def begin_shell(self, result):
        if self.shell is not None:
            coro = self.shell(self.reader, self.writer)
            if asyncio.iscoroutine(coro):
                loop = asyncio.get_event_loop()
                fut = loop.create_task(coro)
                # Avoid capturing self strongly in the callback to prevent
                # keeping the protocol instance alive after close. Although I
                # hope folks aren't using the 'waiter_closed' argument, we use
                # it in automatic tests, and, because it returns "self", we have
                # to ensure it is a "weak" reference -- in the future we should
                # migrate to more dynamic "await connection and/or negotiation
                # state"
                ref_self = weakref.ref(self)

                def _on_shell_done(_fut):
                    self_ = ref_self()
                    if self_ is not None and self_._waiter_closed is not None:
                        self_._waiter_closed.set_result(weakref.proxy(self_))

                fut.add_done_callback(_on_shell_done)

    def data_received(self, data):
        """Process bytes received by transport."""
        # This may seem strange; feeding all bytes received to the **writer**,
        # and, only if they test positive, duplicating to the **reader**.
        #
        # The writer receives a copy of all raw bytes because, as an IAC
        # interpreter, it may likely **write** a responding reply.
        self._last_received = datetime.datetime.now()

        cmd_received = False
        for byte in data:
            try:
                recv_inband = self.writer.feed_byte(bytes([byte]))
            except:
                self._log_exception(logger.warning, *sys.exc_info())
            else:
                if recv_inband:
                    # forward to reader (shell).
                    self.reader.feed_data(bytes([byte]))

                # becomes True if any out of band data is received.
                cmd_received = cmd_received or not recv_inband

        # until negotiation is complete, re-check negotiation aggressively
        # upon receipt of any command byte.
        if not self._waiter_connected.done() and cmd_received:
            self._check_negotiation_timer()

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
        """Get optional server protocol or transport information."""
        if self._transport:
            default = self._transport.get_extra_info(name, default)
        return self._extra.get(name, default)

    def begin_negotiation(self):
        """
        Begin on-connect negotiation.

        A Telnet server is expected to demand preferred session options
        immediately after connection.  Deriving implementations should always
        call ``super().begin_negotiation()``.
        """
        self._check_later = asyncio.get_event_loop().call_soon(
            self._check_negotiation_timer
        )
        self._tasks.append(self._check_later)

    def begin_advanced_negotiation(self):
        """
        Begin advanced negotiation.

        Callback method further requests advanced telnet options.  Called
        once on receipt of any ``DO`` or ``WILL`` acknowledgments
        received, indicating that the remote end is capable of negotiating
        further.

        Only called if sub-classing :meth:`begin_negotiation` causes
        at least one negotiation option to be affirmatively acknowledged.
        """
        pass

    def encoding(self, outgoing=False, incoming=False):
        """
        Encoding that should be used for the direction indicated.

        The base implementation **always** returns the encoding given to class
        initializer, or, when unset (None), ``US-ASCII``.
        """
        # pylint: disable=unused-argument
        return self.default_encoding or "US-ASCII"

    def negotiation_should_advance(self):
        """
        Whether advanced negotiation should commence.

        :rtype: bool
        :returns: True if advanced negotiation should be permitted.

        The base implementation returns True if any negotiation options
        were affirmatively acknowledged by client, more than likely
        options requested in callback :meth:`begin_negotiation`.
        """
        # Generally, this separates a bare TCP connect() from a True
        # RFC-compliant telnet client with responding IAC interpreter.
        server_do = sum(enabled for _, enabled in self.writer.remote_option.items())
        client_will = sum(enabled for _, enabled in self.writer.local_option.items())
        return bool(server_do or client_will)

    def check_negotiation(self, final=False):
        """
        Callback, return whether negotiation is complete.

        :param bool final: Whether this is the final time this callback
            will be requested to answer regarding protocol negotiation.
        :returns: Whether negotiation is over (server end is satisfied).
        :rtype: bool

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
        return not any(self.writer.pending_option.values())

    # private methods

    def _check_negotiation_timer(self):
        self._check_later.cancel()
        self._tasks.remove(self._check_later)

        later = self.connect_maxwait - self.duration
        final = bool(later < 0)

        if self.check_negotiation(final=final):
            logger.debug("negotiation complete after {:1.2f}s.".format(self.duration))
            self._waiter_connected.set_result(weakref.proxy(self))
        elif final:
            logger.debug("negotiation failed after {:1.2f}s.".format(self.duration))
            self._waiter_connected.set_result(weakref.proxy(self))
        else:
            # keep re-queuing until complete
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
