""" Telnet Client asyncio Protocol, https://github.com/jquast/telnetlib3. """
import collections
import traceback
import datetime
import logging
import socket
import codecs
import sys

import asyncio

from .telopt import TelnetStream
from .conio import TerminalShell
from . import dns

__all__ = ('TelnetClient',)


class TelnetClient(asyncio.protocols.Protocol):

    """ Telnet Client Protocol. """

    #: mininum on-connect time to wait for server-initiated negotiation options
    CONNECT_MINWAIT = 2.00
    #: maximum on-connect time to wait for server-initiated negotiation options
    #  before negotiation is considered 'final'.
    CONNECT_MAXWAIT = 6.00
    #: timer length for check_negotiation re-scheduling deferred.
    CONNECT_DEFERRED = 0.05

    #: default client environment variables,
    default_env = {
        'COLUMNS': '80',
        'LINES': '24',
        'USER': 'unknown',
        'TERM': 'unknown',
        'CHARSET': 'ascii',
    }

    def __init__(self, shell=TerminalShell, stream=TelnetStream,
                 encoding='utf-8', log=logging, force_binary=False,
                 waiter_connected=None, waiter_closed=None):
        """ Constructor method for TelnetClient.

        :param shell: Terminal Client shell factory class.
#        :param stream: Telnet IAC Stream interpreter factory class.
        :param encoding: encoding used when BINARY is negotiated.
        :type encoding: str
        :param log: logger instance.
        :type log: logging.Logger
        :param force_binary: Use BINARY even if server will not negotiate.
        :type foce_binary: bool
        """
        self.log = log
        self.force_binary = force_binary
        self._shell_factory = shell
        self._stream_factory = stream
        self._default_encoding = encoding
        self._loop = asyncio.get_event_loop()

        #: session environment as S.env['key'], defaults empty string value
        self._env = collections.defaultdict(str, **self.default_env)

        #: toggled when transport is shutting down
        self._closing = False

        #: datetime of last byte received
        self._last_received = None

        #: datetime of connection made
        self._connected = None

        #: future result stores value of gethostbyaddr(sever_ip)
        self._server_host = asyncio.Future()

        #: server_fqdn is result of socket.getfqdn() of server_host
        self._server_fqdn = asyncio.Future()

        #: values for properties ``server_ip`` and ``server_port``
        self._server_ip = None
        self._server_port = None

        #: waiter is a Future that completes when connection is closed.
        if waiter_closed is None:
            waiter_closed = asyncio.Future()
        self.waiter_closed = waiter_closed

        if waiter_connected is None:
            waiter_connected = asyncio.Future()
        self.waiter_connected = waiter_connected

    def __str__(self):
        """ Return string reporting status of client session. """
        return describe_connection(self)

    def connection_made(self, transport):
        """
        Callback begins new telnet client connection on ``transport``.

        A ``self.stream`` instance is created for reading on the
        ``transport``, environment variables are prepared, and
        various IAC and SLC callbacks are registered.

        ``begin_negotiation()`` is fired after connection is complete.
        """
        #self._transport = transport

        self._server_ip, self._server_port = (
            transport.get_extra_info('peername')[:2])

        self.stream = self._stream_factory(
            transport=transport, client=True, log=self.log)

#        self.reader = self._factory_reader()
#        self.reader.set_transport(transport)
        self.shell = self._shell_factory(client=self, log=self.log)

        self.init_environment_values()
        self.set_stream_callbacks()
        self._last_received = datetime.datetime.now()
        self._connected = datetime.datetime.now()

        # begin connect-time negotiation
        self._loop.call_soon(self.begin_negotiation)

        # resolve server fqdn (and later, reverse-dns)
        self._server_host = self._loop.run_in_executor(
            None, socket.gethostbyaddr, self._server_ip)
        self._server_host.add_done_callback(self.after_server_lookup)

        self.log.info(self)

    def init_environment_values(self):
        """
        Initialize :py:attr:`self.env`, called by :py:meth:`connection_made`.

        This is meant to simulate OS Environment variables.
        :py:attr:`self.env` keys *TERM*, *COLUMNS*, and *LINES* are set by
        the return values of :py:attr:`self.shell` attributes
        *terminal_type*, *terminal_width*, and *terminal_height*.

        All other values remain those set in by :py:attr:`self.default_env`.
        """
        self.env['TERM'] = self.shell.terminal_type
        self.env['COLUMNS'] = '{}'.format(self.shell.terminal_width)
        self.env['LINES'] = '{}'.format(self.shell.terminal_height)
        self.env['CHARSET'] = self._default_encoding

    def set_stream_callbacks(self):
        """
        Initialize callbacks for Telnet negotiation responses.

        Sets callbacks for methods class :py:method:`self.send_ttype`,
        :py:method:`self.send_ttype`, :py:method:`self.send_tspeed`,
        :py:method:`self.send_xdisploc`, :py:method:`self.send_env`,
        :py:method:`self.send_naws`, and :py:method:`self.send_charset`,
        to the appropriate Telnet Option Negotiation byte values.
        """
        from telnetlib3.telopt import TTYPE, TSPEED, XDISPLOC, NEW_ENVIRON
        from telnetlib3.telopt import CHARSET, NAWS

        # wire extended rfc callbacks for terminal atributes, etc.
        for (opt, func) in (
                (TTYPE, self.send_ttype),
                (TSPEED, self.send_tspeed),
                (XDISPLOC, self.send_xdisploc),
                (NEW_ENVIRON, self.send_env),
                (NAWS, self.send_naws),
                (CHARSET, self.send_charset),
                ):
            self.stream.set_ext_send_callback(opt, func)

    def after_server_lookup(self, arg):
        """
        Callback receives result of server ip name resolution.

        :param arg: result of gethostbyaddr of server ip.
        :type arg: asyncio.Future.
        """
        if arg.cancelled():
            self.log.debug('server dns lookup cancelled')
            return
        if self.server_ip != self.server_reverse_ip.result():
            self.log.warn('reverse lookup: {sip} != {rsip} ({arg})'.format(
                cip=self.server_ip, rcip=self.server_reverse_ip,
                arg=arg.result()))

    @property
    def server_ip(self):
        """ IP address of connected server.

        :rtype: str
        """
        return self._server_ip

    @property
    def server_port(self):
        """ Port number of connected server.

        :rtype: int
        """
        return self._server_port

    @property
    def server_hostname(self):
        """ DNS name of server as Future.

        :rtype: asyncio.Future
        """
        return dns.future_hostname(
            future_gethostbyaddr=self._server_host,
            fallback_ip=self.server_ip)

    @property
    def server_fqdn(self):
        """ Fully Qualified Domain Name (FQDN) of server as Future.

        :rtype: asyncio.Future
        """
        return dns.future_fqdn(
            future_gethostbyaddr=self._server_host,
            fallback_ip=self.server_ip)

    @property
    def server_reverse_ip(self):
        """ Reverse DNS (rDNS) of server IP as Future.

        :rtype: asyncio.Future
        """
        return dns.future_reverse_ip(
            future_gethostbyaddr=self._server_host,
            fallback_ip=self.server_ip)

    @property
    def env(self):
        """ Client Environment dictionary.

        :rtype: dict
        """
        return self._env

    @property
    def connected(self):
        """ datetime connection started.

        :rtype: datetime.datetime
        """
        return self._connected

    @property
    def inbinary(self):
        """ Whether client may receive BINARY data from server.

        Character ordinal values above 127 may be transmitted by
        server if IAC WILL BINARY was received by client and agreed
        by IAC DO BINARY.  Always returns True when class attribute
        :py:attr:`self.force_binary` is set.

        :rtype: bool
        """
        from telnetlib3.telopt import BINARY
        return self.force_binary or self.stream.remote_option.enabled(BINARY)

    @property
    def outbinary(self):
        """ Whether client may send BINARY data to server.

        Character ordinal values above 127 should only be transmitted by
        client if *IAC DO BINARY* was sent and agreed by *IAC DO BINARY*.

        Always returns True when class attribute :py:attr:`self.force_binary`
        is set.

        :rtype: bool
        """
        from telnetlib3.telopt import BINARY
        return self.force_binary or self.stream.local_option.enabled(BINARY)

    def encoding(self, outgoing=False, incoming=False):
        """ Client-preferred input or output encoding of BINARY data.

        Always returns 'ascii' for the direction(s) indicated unless
        :py:attr:`self.inbinary` or :py:attr:`self.outbinary` is True,
        Returnning the session-negotiated value of CHARSET(rfc2066)
        or encoding indicated by :py:attr:`self.encoding`.

        As BINARY(rfc856) must be negotiated bi-directionally, both or
        at least one direction should always be indicated, which may
        return different values -- it is entirely possible to receive
        only 'ascii'-encoded data but negotiate the allowance to transmit
        'utf8'.
        """
        assert outgoing or incoming
        return (self.env.get('CHARSET', self._default_encoding)
                if (outgoing and not incoming and self.outbinary) or (
                    not outgoing and incoming and self.inbinary) or (
                    outgoing and incoming and self.outbinary and self.inbinary
                    ) else 'ascii')

    def send_ttype(self):
        """ Callback for responding to TTYPE requests.

        Default implementation returns the value of
        :py:attr:`self.shell.terminal_type`.
        """
        return self.shell.terminal_type

    def send_tspeed(self):
        """ Callback for responding to TSPEED requests.

        Default implementation returns the value of
        :py:attr:`self.shell.terminal_speed`.
        """
        return self.shell.terminal_speed

    def send_xdisploc(self):
        """ Callback for responding to XDISPLOC requests.

        Default implementation returns the value of
        :py:attr:`self.shell.xdisploc`.
        """
        return self.shell.xdisploc

    def send_env(self, keys):
        """ Callback for responding to NEW_ENVIRON requests.

        :param keys: Values are requested for the keys specified. When
           ``None``, all environment values be returned.
        :returns: dictionary of environment values requested, or an
            empty string for keys not available. A return value must be
            given for each key requested.
        :rtype: dict[(key, value), ..]
        """
        if keys is None:
            return self.env
        return dict((key, self.env.get(key, '')) for key in keys)

    def send_charset(self, offered):
        """ Callback for responding to CHARSET requests.

        Receives a list of character encodings offered by the server
        as ``offered`` such as ``('LATIN-1', 'UTF-8')``, for which the
        client may return a value agreed to use, or None to disagree to
        any available offers.  Server offerings may be encodings or
        codepages.

        The default implementation selects any matching encoding that
        python is capable of using, preferring any that matches
        :py:attr:`self.encoding` if matched in the offered list.

        :param offered: list of CHARSET options offered by server.
        :returns: character encoding agreed to be used.
        :rtype: str or None.
        """
        selected = None
        for offer in offered:
                try:
                    codec = codecs.lookup(offer)
                except LookupError as err:
                    self.log.info('LookupError: {}'.format(err))
                else:
                    if (codec.name == self.env['CHARSET'] or not selected):
                        self.env['CHARSET'] = codec.name
                        selected = offer
        if selected:
            self.log.debug('Encoding negotiated: {env[CHARSET]}.'
                           .format(env=self.env))
            return selected
        self.log.info('No suitable encoding offered by server: {!r}.'
                      .format(offered))
        return None

    def send_naws(self):
        """ Callback for responding to NAWS requests.

        :rtype: (int, int)
        :returns: client window size as (columns, rows).
        """
        return (self.shell.terminal_height, self.shell.terminal_width)

    def begin_negotiation(self):
        """ Callback to begin on-connect negotiation.

        A Server is expected to assert the preferred negotiation options
        immediately after connection -- the client should hear about these
        options before asserting its own wishes.

        This implementation schedules :py:meth:`self.check_negotation`
        to be called soon by the event loop, which re-schedules itself
        for callback until at least :py:meth:`self.CONNECT_MINWAIT` has
        elapsed.
        """
        if self._closing:
            self.waiter_connected.cancel()
            return

        self._loop.call_soon(self.check_negotiation)

    def check_negotiation(self):
        """ Callback to check negotiation state on-connect.

        Schedules itself for continual callback until negotiation with
        server is considered final, firing :py:meth:`after_negotiation`
        when complete.
        """
        if self._closing:
            self.waiter_connected.cancel()
            return
        pots = self.stream.pending_option
        if not any(pots.values()):
            if self.duration > self.CONNECT_MINWAIT:
                # the number of seconds since connection has reached
                # CONNECT_MINWAIT and no pending telnet options are
                # awaiting negotiation.
                self.waiter_connected.set_result(self)
                return

        elif self.duration > self.CONNECT_MAXWAIT:
            # with telnet options pending, we set waiter_connected anyway -- it
            # is unlikely after such time elapsed that the server will complete
            # negotiation after this time.
            self.waiter_connected.set_result(self)
            return

        self._loop.call_later(self.CONNECT_DEFERRED, self.check_negotiation)

    def after_negotiation(self, status):
        """ XXX Default public callback does nothing.
        """
        if status.cancelled():
            self.log.debug('telopt negotiation cancelled')
            return
        self.log.debug('stream status: {}.'.format(self.stream))
        self.log.debug('client encoding is {}.'.format(
            self.encoding(outgoing=True, incoming=True)))

    def after_encoding_negotiation(self, status):
        """ Callback when on-connect encoding negotiation is complete.

        :type status: asyncio.Future
        :param status: possibly cancelled Future if connection was closed.
            Otherwise, result value is a boolean indicating whether BINARY
            was negotiated bi-directionally.
        """
        if status.cancelled():
            self.log.debug('encoding negotiation cancelled')
            return


    def check_encoding_negotiation(self):
        """ Callback to check on-connect option negotiation for encoding.

        Schedules itself for continual callback until encoding negotiation
        with server is considered final, firing
        :py:meth:`after_encoding_negotiation` when complete.  Encoding
        negotiation is considered final when BINARY mode has been negotiated
        bi-directionally.
        """
        from .telopt import DO, BINARY
        if self._closing:
            return

        # encoding negotiation is complete
        if self.outbinary and self.inbinary:
            self.log.debug('negotiated outbinary and inbinary with client.')

        # if (WILL, BINARY) requested by begin_negotiation() is answered in
        # the affirmitive, then request (DO, BINARY) to ensure bi-directional
        # transfer of non-ascii characters.
        elif self.outbinary and not self.inbinary and (
                not (DO, BINARY,) in self.stream.pending_option):
            self.log.debug('outbinary=True, requesting inbinary.')
            self.stream.iac(DO, BINARY)
            self._loop.call_later(self.CONNECT_DEFERRED,
                                  self.check_encoding_negotiation)

        elif self.duration > self.CONNECT_MAXWAIT:
            # Perhaps some IAC interpreting servers do not differentiate
            # 'local' from 'remote' options -- they are treated equivalently.
            self.log.debug('failed to negotiate both outbinary and inbinary.')

        else:
            self._loop.call_later(self.CONNECT_DEFERRED,
                                  self.check_encoding_negotiation)

    @property
    def duration(self):
        """ Time elapsed since connected to server as seconds.

        :rtype: float
        """
        if self._connected:
            return (datetime.datetime.now() - self._connected).total_seconds()
        return float('inf')

    def data_received(self, data):
        """ Process each byte as received by transport.

        All bytes are sent to :py:meth:`TelnetStream.feed_byte` to
        check for Telnet Is-A-Command (IAC) or continuation bytes.
        When bytes are in-band, they are then sent to
        :py:meth:`self.shell.feed_byte`
        """
        self.log.debug('data_received: {!r}'.format(data))
        self._last_received = datetime.datetime.now()
        for byte in (bytes([value]) for value in data):

            try:
                self.stream.feed_byte(byte)
            except (ValueError, AssertionError):
                e_type, e_value, _ = sys.exc_info()
                map(self.log.warn,
                    traceback.format_exception_only(e_type, e_value))
                continue

            if self.stream.is_oob:
                continue

            # self.reader.feed_byte()
            self.shell.feed_byte(byte)

    def eof_received(self):
        """ Callback when EOF was received by server. """
        self.connection_lost('EOF')
        return False

    def connection_lost(self, exc):
        """ Callback when connection to server was lost.

        :param exc: exception
        """
        if not self._closing:
            self._closing = True
            self.log.info('{about}{reason}'.format(
                about=self.__str__(),
                reason=': {}'.format(exc) if exc is not None else ''))
            self.waiter_connected.cancel()
            self.waiter_closed.set_result(self)


def describe_connection(client):
    if client._closing:
        state, direction = 'Disconnected', 'from'
    else:
        state, direction = 'Connected', 'to'
    if (client.server_hostname.done() and
            client.server_hostname.result() != client.server_ip):
        hostname = ' ({})'.format(client.server_hostname.result())
    else:
        hostname = ''
    if client.server_port != 23:
        port = ' port 23'
    else:
        port = ''

    duration = '{:0.2f}s'.format(client.duration)
    return ('{state} {direction} {serverip}{port}{hostname} after {duration}'
            .format(
                state=state,
                direction=direction,
                serverip=client.server_ip,
                port=port,
                hostname=hostname,
                duration=duration)
            )
