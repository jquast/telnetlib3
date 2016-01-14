"""Module provides class BaseServer."""
import traceback
import asyncio
import logging
import datetime
import sys

from .stream_writer import TelnetWriter
from .stream_reader import TelnetReader

__all__ = ('BaseServer',)


class BaseServer(asyncio.Protocol):
    """Base Telnet Server Protocol."""
    #: Maximum on-connect time to wait for all pending negotiation options to
    #: complete before negotiation is considered 'final', signaled by the
    #: completion of waiter :attr:`waiter_connected`.
    CONNECT_MAXWAIT = 1.80

    #: maximum timer length for ``check_negotiation``
    #: re-scheduling (default: 50ms)
    CONNECT_DEFERRED = 0.05

    _when_connected = None
    _last_received = None
    _transport = None
    _advanced = False
    _closing = False
    _reader_factory = TelnetReader
    _writer_factory = TelnetWriter

    def __init__(self, shell=None, log=None, loop=None,
                 waiter_connected=None, waiter_closed=None,
                 encoding='utf8', encoding_errors='strict',
                 force_binary=False):
        """Class initializer."""
        self.log = log or logging.getLogger(__name__)
        self._loop = loop or asyncio.get_event_loop()
        self.default_encoding = encoding
        self.encoding_errors = encoding_errors
        self.force_binary = force_binary
        self._extra = dict()
        self.waiter_connected = waiter_connected or asyncio.Future()
        self.waiter_closed = waiter_closed or asyncio.Future()
        self._tasks = [self.waiter_connected]
        self.shell = shell
        self.reader = None
        self.writer = None

    # Base protocol methods

    def eof_received(self):
        """
        Called when the other end calls write_eof() or equivalent.

        This callback may be exercised by the nc(1) client argument ``-z``.
        """
        self.log.debug('EOF from client, closing.')
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
            self.log.info('Connection closed for %s', self)
            self.reader.feed_eof()
        else:
            self.log.info('Connection lost for %s: %s', self, exc)
            self.reader.set_exception(exc)

        # cancel protocol tasks, namely on-connect negotiations
        for task in self._tasks:
            task.cancel()

        # close transport (may already be closed), set waiter_closed and
        # cancel waiter_connected.
        self._transport.close()
        self.waiter_connected.cancel()
        if self.shell is None:
            self.waiter_closed.set_result(self)

    def connection_made(self, transport):
        """
        Called when a connection is made.

        Sets attributes :attr:`_transport`, :attr:`_when_connected`,
        :attr:`_last_received`, :attr:`reader` and :attr:`writer`.

        Ensure ``super().connection_made(transport)`` is called when derived.
        """
        self._transport = transport
        self._when_connected = datetime.datetime.now()
        self._last_received = datetime.datetime.now()

        self.reader = self._reader_factory(
            protocol=self, log=self.log, loop=self._loop, server=True)

        self.writer = self._writer_factory(
            transport=transport, protocol=self,
            reader=self.reader, server=True,
            loop=self._loop, log=self.log)

        self.log.info('Connection from %s', self)

        self.waiter_connected.add_done_callback(self.begin_shell)
        self._loop.call_soon(self.begin_negotiation)

    def begin_shell(self, result):
        if self.shell is not None:
            coro = self.shell(self.reader, self.writer)
            if asyncio.iscoroutine(coro):
                fut = self._loop.create_task(coro)
                fut.add_done_callback(
                    lambda fut_obj: self.waiter_closed.set_result(self))

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
                recv = self.writer.feed_byte(bytes([byte]))
            except:
                self._log_exception(self.log.warn, *sys.exc_info())
            else:
                if recv:
                    # forward to reader (shell).
                    self.reader.feed_data(bytes([byte]))
                else:
                    cmd_received = True

        # until negotiation is complete, re-check negotiation aggressively
        # upon receipt of any command byte.
        if not self.waiter_connected.done() and cmd_received:
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
        hostport = self._transport.get_extra_info('peername')[:2]
        # _extra = ((key, val) for key, val in self._extra.items() if
        #           not key.startswith('ttype') and
        #           key not in ('timeout',))
        # extra = ' '.join('{0}={1}'.format(*item) for item in sorted(_extra))
        return '<Peer {0} {1}>'.format(*hostport)

    def get_extra_info(self, name, default=None):
        """Get optional server information."""
        return self._extra.get(name, default)

    def begin_negotiation(self):
        """
        Begin on-connect negotiation.

        A Telnet server is expected to demand preferred session options
        immediately after connection.  Deriving implementations should always
        call ``super().begin_negotiation()``.
        """
        self._check_later = self._loop.call_soon(self._check_negotiation_timer)
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

        Deriving implementations should always call
        ``super().begin_advanced_negotiation()``.
        """
        pass

    def encoding(self, outgoing=False, incoming=False):
        """
        Encoding that should be used for the direction indicated.

        The base implementation **always** returns :attr:`default_encoding`
        or, when unspecified, ``US-ASCII``.
        """
        # pylint: disable=unused-argument
        return self.default_encoding or 'US-ASCII'

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
        server_do = sum(enabled for _, enabled in
                        self.writer.remote_option.items())
        client_will = sum(enabled for _, enabled in
                          self.writer.local_option.items())
        return bool(server_do or client_will)

    def check_negotiation(self, final=False):
        """
        Callback, return whether negotiation is complete.

        :param bool final: Whether this is the final time this callback
            will be requested to answer regarding protocol negotiation.
        :returns: Whether negotiation is over (server end is satisfied).
        :rtype: bool

        Method is called on each new command byte processed until negotiation
        is considered final, or after :attr:`CONNECT_MAXWAIT` has elapsed,
        setting :attr:`waiter_connected` to value ``self`` when complete.

        Ensure ``super().check_negotiation()`` is called and conditionally
        combined when derived.
        """
        if not self._advanced and self.negotiation_should_advance():
            self._advanced = True
            self.log.debug('begin advanced negotiation')
            self._loop.call_soon(self.begin_advanced_negotiation)

        # negotiation is complete (returns True) when all negotiation options
        # that have been requested have been acknowledged.
        return not any(self.writer.pending_option.values())

    # private methods

    def _check_negotiation_timer(self):
        self._check_later.cancel()
        self._tasks.remove(self._check_later)

        later = self.CONNECT_MAXWAIT - self.duration
        final = bool(later < 0)

        if self.check_negotiation(final=final):
            self.log.debug('negotiation complete after {:1.2f}s.'
                           .format(self.duration))
            self.waiter_connected.set_result(self)
        elif final:
            self.log.debug('negotiation failed after {:1.2f}s.'
                           .format(self.duration))
            self.waiter_connected.set_result(self)
        else:
            # keep re-queueing until complete
            self._check_later = self._loop.call_later(
                later, self._check_negotiation_timer)
            self._tasks.append(self._check_later)

    @staticmethod
    def _log_exception(logger, e_type, e_value, e_tb):
        rows_tbk = [line for line in
                    '\n'.join(traceback.format_tb(e_tb)).split('\n')
                    if line]
        rows_exc = [line.rstrip() for line in
                    traceback.format_exception_only(e_type, e_value)]

        for line in rows_tbk + rows_exc:
            logger(line)
