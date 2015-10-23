"""Module provides class BaseServer."""
import traceback
import asyncio
import datetime
import logging
import sys

from .stream_writer import TelnetWriter


class BaseServer(asyncio.protocols.Protocol):
    """Base Telnet Server Protocol API."""
    #: Minimum on-connect time to wait for the client to insist on any
    #: negotiations before completing :attr:`waiter_connected`.  This does
    #: not need to be very long, RFC does not require any time at all.
    CONNECT_MINWAIT = 0.15

    #: Maximum on-connect time to wait for all pending negotiation options to
    #: complete before negotiation is considered 'final', signaled by the
    #: completion of waiter :attr:`waiter_connected`.
    CONNECT_MAXWAIT = 4.00

    #: maximum timer length for ``_check_negotiation``
    #: re-scheduling (default: 50ms)
    CONNECT_DEFERRED = 0.05

    _when_connected = None
    _last_received = None
    _transport = None
    _advanced = False
    _closing = False
    _stream = None

    def __init__(self, reader_factory=None, writer_factory=None,
                 log=logging, loop=None):
        """Class initializer."""
        if reader_factory is None:
            reader_factory = asyncio.StreamReader
        self._reader_factory = reader_factory
        self.reader = None

        if writer_factory is None:
            writer_factory = TelnetWriter
        self._writer_factory = writer_factory
        self.writer = None

        self.log = log

        if loop is None:
            loop = asyncio.get_event_loop()
        self._loop = loop

        self.waiter_connected = asyncio.Future()
        self.waiter_closed = asyncio.Future()
        self._tasks = [self.waiter_connected, self._timer]

    # Base protocol methods

    def eof_received(self):
        """Called when the other end calls write_eof() or equivalent."""
        self.connection_lost('EOF')
        return False

    def connection_lost(self, exc):
        """Called when the connection is lost or closed."""
        if self._closing:
            return
        self._closing = True

        # inform about closed connection
        postfix = ''
        if exc:
            postfix = ': {0}'.format(exc)
        self.log.info('{0}{1}'.format(self, postfix))

        # cancel protocol tasks
        for task in self._tasks:
            task.cancel()

        self.waiter_closed.set_result(self)

    def connection_made(self, transport):
        """
        Called when a connection is made.

        Sets attributes :attr:`_transport`, :attr:`_when_connected`,
        :attr:`_last_received`, :attr:`reader` and :attr:`writer`.

        Ensure ``super().connection_made(transport)`` is called when derived.
        """
        peername = transport.get_extra_info('peername')
        self.log.info('Connection from {0}'.format(peername))
        self._transport = transport
        self._when_connected = datetime.datetime.now()
        self._last_received = datetime.datetime.now()

        self.reader = self.reader_factory(protocol=self, log=self.log)

        self.writer = self.writer_factory(transport=transport, protocol=self,
                                          reader=self.reader, loop=self._loop,
                                          log=self.log)

        self._loop.call_soon(self.begin_negotiation)

    def data_received(self, data):
        """Process bytes received by transport."""
        self.log.debug('data_received: {!r}'.format(data))
        self._last_received = datetime.datetime.now()

        for byte in (bytes([value]) for value in data):
            try:
                self._stream.feed_byte(byte)
            except (ValueError, AssertionError, NotImplementedError):
                e_type, e_value, e_tb = sys.exc_info()

                rows_tbk = [line for line in
                            '\n'.join(traceback.format_tb(e_tb)).split('\n')
                            if line]
                rows_exc = [line.rstrip() for line in
                            traceback.format_exception_only(e_type, e_value)]

                for line in rows_tbk + rows_exc:
                    self.log.debug(line)

            if not self.writer.is_oob:
                # If the given byte was determined not to be "out of band",
                # that is, intended for transmission to user, the byte is
                # forwarded to our reader's "feed_byte" method.
                _slc_function = self.writer.slc_received
                self.reader.feed_byte(byte, slc_function=_slc_function)

    # Our protocol methods

    def begin_negotiation(self):
        """
        Begin on-connect negotiation.

        A Telnet server is expected to assert the preferred session options
        immediately after connection.  Deriving implementations should always
        call ``super().begin_negotiation()``.
        """
        if self._closing:
            return

        self._loop.call_soon(self._check_negotiation)

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
        """Encoding that should be used for the direction indicated."""
        # pylint: disable=unused-argument,no-self-use
        return 'US-ASCII'

    @property
    def duration(self):
        """Time elapsed since client connected, in seconds as float."""
        return (datetime.datetime.now() - self._when_connected).total_seconds()

    @property
    def idle(self):
        """Time elapsed since data last received, in seconds as float."""
        return (datetime.datetime.now() - self._last_received).total_seconds()

    # private methods

    def _negotiation_should_advance(self):
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
                        self._stream.remote_option.items())
        client_will = sum(enabled for _, enabled in
                          self._stream.local_option.items())

        return server_do or client_will

    def _check_negotiation(self):
        """
        Scheduled callback determines when on-connect negotiation is complete.

        Method schedules itself for continual callback until negotiation is
        considered final, setting :attr:`waiter_connected` to value ``self``
        when complete.
        """
        if self._closing:
            return

        later = max(self.CONNECT_DEFERRED,
                    max(0, self.CONNECT_MAXWAIT - self.duration))

        if not self._advanced:
            if self._negotiation_should_advance():
                self._advanced = True
                self._loop.call_soon(self.begin_advanced_negotiation)
                self.debug('negotiation will advance')

        if not any(self._stream.pending_option.values()):
            self.log.debug('negotiation completed after {:0.01f}s.'
                           .format(self.duration))
            self.waiter_connected.set_result(self)
            return

        elif self.duration > self.CONNECT_MAXWAIT:
            self.log.debug('negotiation cancelled after {:0.01f}s.'
                           .format(self.duration))
            self.waiter_connected.set_result(self)
            return

        self._loop.call_later(later, self._check_negotiation)
