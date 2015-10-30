"""Module provides class BaseServer."""
import traceback
import asyncio
import datetime
import sys

from .stream_writer import TelnetWriter
from .stream_reader import StreamReader
from .shell import TelnetShell


class BaseServer(asyncio.protocols.Protocol):
    """Base Telnet Server Protocol API."""
    #: Maximum on-connect time to wait for all pending negotiation options to
    #: complete before negotiation is considered 'final', signaled by the
    #: completion of waiter :attr:`waiter_connected`.
    CONNECT_MAXWAIT = 4.00

    #: maximum timer length for ``_check_negotiation``
    #: re-scheduling (default: 50ms)
    CONNECT_DEFERRED = 0.05

    #: RFC compliance demands IAC-GA, an out-of-band "go ahead" signal for
    #: clients that fail to negotiate suppress go-ahead (WONT SGA).  This
    #: feature may be useful for synchronizing prompts with automation, but
    #: may otherwise cause harm to non-compliant client streams.
    send_go_ahead = True

    _when_connected = None
    _last_received = None
    _transport = None
    _advanced = False
    _closing = False

    def __init__(self, reader_factory=None, writer_factory=None,
                 shell_factory=None, waiter_connected=None,
                 waiter_closed=None, log=None, loop=None):
        """Class initializer."""
        # XXX
        self._reader_factory = reader_factory or StreamReader
        self._writer_factory = writer_factory or TelnetWriter
        self._shell_factory = shell_factory or TelnetShell
        self._loop = loop or asyncio.get_event_loop()
        self._extra = dict()
        self.waiter_connected = waiter_connected or asyncio.Future()
        self.waiter_closed = waiter_closed or asyncio.Future()
        self.reader = None
        self.writer = None
        self.log = log
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

        # close transport if it is not already, such as user-requested Logout.
        self._transport.close()

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

        self.reader = self._reader_factory(
            protocol=self, log=self.log)

        self.writer = self._writer_factory(
            transport=transport, protocol=self,
            reader=self.reader, server=True,
            loop=self._loop, log=self.log)

        # XXX "self.shell" needs interface finalization
        if self._shell_factory:
            self.shell = self._shell_factory(
                protocol=self, reader=self.reader, writer=self.writer,
                loop=self._loop, log=self.log)
            self.waiter_connected.add_done_callback(
                lambda result: self.shell.display_prompt())
        self._loop.call_soon(self.begin_negotiation)

    def data_received(self, data):
        """
        Process bytes received by transport.

        You should not override this method, but rather the ``shell``
        protocol is a reader receiving ``feed_byte(byte)`` XXX ?
        """
        self._last_received = datetime.datetime.now()

        cmd_received = False
        for byte in data:
            try:
                recv = self.writer.feed_byte(byte)
            except:
                self._log_exception(self.log.debug, *sys.exc_info())
            else:
                if recv and self.shell:
                    # forward to shell
                    self.shell.feed_byte(
                        byte=byte, slc_function=self.writer.slc_received)
                else:
                    # part of IAC command byte phrase
                    cmd_received = True

        # until negotiation is complete, re-check aggressively upon
        # receipt of any command byte.
        if not self.waiter_connected.done() and cmd_received:
            self._check_negotiation_timer()

    # Our protocol methods
    def get_extra_info(self, name, default=None):
        """Get optional server information."""
        return self._extra.get(name, default)

    def begin_negotiation(self):
        """
        Begin on-connect negotiation.

        A Telnet server is expected to assert the preferred session options
        immediately after connection.  Deriving implementations should always
        call ``super().begin_negotiation()``.
        """
        if self._closing:
            return
        self._check_later = self._loop.call_soon(self._check_negotiation_timer)

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
        self.log.debug('any DO or WILL? {0}'.format(server_do or client_will))
        return server_do or client_will

    def check_negotiation(self, final=False):
        """
        Returns whether negotiation is complete.

        :param bool final: Whether this is the final time this callback
            will be requested to answer regarding protocol negotiation.
        :returns: Whether negotiation is final.
        :rtype: bool

        Ensure ``super().check_negotiation()`` is called when derived.
        """
        result = self._check_negotiation()
        if final and not result:
            self.log.debug('negotiation failed after {:1.2f}s.'
                           .format(self.duration))
            self.waiter_connected.set_result(self)
            return False
        return True

    # private methods

    def _check_negotiation_timer(self):
        self._check_later.cancel()

        later = self.CONNECT_MAXWAIT - self.duration

        if self.check_negotiation(final=bool(later < 0)):
            self.log.debug('negotiation complete after {:1.2f}s.'
                           .format(self.duration))
            self.waiter_connected.set_result(self)

        else:
            self._check_later = self._loop.call_later(
                later, self._check_negotiation_timer)

    def _check_negotiation(self):
        """
        Callback check until on-connect negotiation is complete.

        Method is called on each new command byte processed until negotiation
        is considered final, or after :attr:`CONNECT_MAXWAIT` has elapsed,
        setting :attr:`waiter_connected` to value ``self`` when complete.
        """
        if self._closing:
            return
        if self.waiter_connected.done():
            return

        if not self._advanced:
            if self.negotiation_should_advance():
                self._advanced = True
                val = self._loop.call_soon(self.begin_advanced_negotiation)
                self.log.debug('negotiation will advance: {0}'.format(val))

        # negotiation is complete (returns True) when all negotiation options
        # that have been requested have been acknowledged.
        return not any(self.writer.pending_option.values())

    @staticmethod
    def _log_exception(logger, e_type, e_value, e_tb):
        rows_tbk = [line for line in
                    '\n'.join(traceback.format_tb(e_tb)).split('\n')
                    if line]
        rows_exc = [line.rstrip() for line in
                    traceback.format_exception_only(e_type, e_value)]

        for line in rows_tbk + rows_exc:
            logger(line)



