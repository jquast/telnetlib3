"""Module provides class BaseClient."""
import logging
import datetime
import traceback
import asyncio
import weakref
import sys

from .stream_writer import TelnetWriter, TelnetWriterUnicode
from .stream_reader import TelnetReader, TelnetReaderUnicode
from .telopt import name_commands

__all__ = ('BaseClient',)


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

    def __init__(self, shell=None, log=None, loop=None,
                 encoding='utf8', encoding_errors='strict',
                 force_binary=False, connect_minwait=1.0,
                 connect_maxwait=4.0, limit=None,
                 waiter_closed=None, _waiter_connected=None):
        """Class initializer."""
        super().__init__(loop=loop)
        self.log = log or logging.getLogger('telnetlib3.client')
        self._loop = loop or asyncio.get_event_loop()
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

    # Base protocol methods

    def eof_received(self):
        """Called when the other end calls write_eof() or equivalent."""
        self.log.debug('EOF from server, closing.')
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
            self.log.info('Connection closed to %s', self)
            self.reader.feed_eof()
        else:
            self.log.info('Connection lost to %s: %s', self, exc)
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

        reader_kwds = {'loop': self._loop}
        writer_kwds = {'loop': self._loop}

        if self.default_encoding:
            reader_kwds['fn_encoding'] = self.encoding
            writer_kwds['fn_encoding'] = self.encoding
            reader_kwds['encoding_errors'] = self._encoding_errors
            writer_kwds['encoding_errors'] = self._encoding_errors
            reader_factory = self._reader_factory_encoding
            writer_factory = self._writer_factory_encoding

        if self._limit:
            reader_kwds['limit'] = self._limit

        self.reader = reader_factory(**reader_kwds)

        self.writer = writer_factory(
            transport=transport, protocol=self,
            reader=self.reader, client=True,
            log=self.log, **writer_kwds)

        self.log.info('Connected to %s', self)

        self._waiter_connected.add_done_callback(self.begin_shell)
        self._loop.call_soon(self.begin_negotiation)

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
                fut = self._loop.create_task(coro)
                fut.add_done_callback(
                    lambda fut_obj: self.waiter_closed.set_result(weakref.proxy(self)))

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
                self._log_exception(self.log.warning, *sys.exc_info())
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
        hostport = self.get_extra_info('peername')[:2]
        return '<Peer {0} {1}>'.format(*hostport)

    def get_extra_info(self, name, default=None):
        """Get optional client protocol or transport information."""
        return self._extra.get(name, self._transport._extra.get(name, default))

    def begin_negotiation(self):
        """
        Begin on-connect negotiation.

        A Telnet client is expected to send only a minimal amount of client
        session options immediately after connection, it is generally the
        server which dictates server option support.

        Deriving implementations should always call
        ``super().begin_negotiation()``.
        """
        self._check_later = self._loop.call_soon(self._check_negotiation_timer)
        self._tasks.append(self._check_later)

    def encoding(self, outgoing=False, incoming=False):
        """
        Encoding that should be used for the direction indicated.

        The base implementation **always** returns ``encoding`` argument
        given to class initializer or, when unset (``None``), ``US-ASCII``.
        """
        # pylint: disable=unused-argument
        return self.default_encoding or 'US-ASCII'  # pragma: no cover

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

        This method returns False until :attr:`connect_minwait` has elapsed,
        ensuring the server may batch telnet negotiation demands without
        prematurely entering the callback shell.

        Ensure ``super().check_negotiation()`` is called and conditionally
        combined when derived.
        """
        return (not any(self.writer.pending_option.values()) and
                # This particular state check is interesting; what we're trying
                # to allow is a period of time where the server may chose to
                # make demands in batches.  Let us allow our protocol
                # negotiation enough time for such demands to be received.
                #
                # A better measurement of would be to use something like TM
                # (timing-mark) to measure the round-trip time, and double it
                # for this value.
                self.duration > self.connect_minwait)

    # private methods

    def _check_negotiation_timer(self):
        self._check_later.cancel()
        self._tasks.remove(self._check_later)

        later = self.connect_maxwait - self.duration
        final = bool(later < 0)

        if self.check_negotiation(final=final):
            self.log.debug('negotiation complete after {:1.2f}s.'
                           .format(self.duration))
            self._waiter_connected.set_result(weakref.proxy(self))
        elif final:
            self.log.debug('negotiation failed after {:1.2f}s.'
                           .format(self.duration))
            _failed = [name_commands(cmd_option)
                       for (cmd_option, pending) in
                       self.writer.pending_option.items()
                       if pending]
            self.log.debug('failed-reply: {0!r}'.format(', '.join(_failed)))
            self._waiter_connected.set_result(weakref.proxy(self))
        else:
            # keep re-queuing until complete.  Aggressively re-queue until
            # connect_minwait, or connect_maxwait, whichever occurs next
            # in our time-series.
            sooner = self.connect_minwait - self.duration
            if sooner > 0:
                later = sooner
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
