import collections
import datetime
import logging
import socket
import codecs

import asyncio

from .telopt import TelnetStream
from .conio import ConsoleShell
from . import dns

__all__ = ('TelnetClient',)


class TelnetClient(asyncio.protocols.Protocol):
    #: mininum on-connect time to wait for server-initiated negotiation options
    CONNECT_MINWAIT = 2.00
    #: maximum on-connect time to wait for server-initiated negotiation options
    #  before negotiation is considered 'final'.
    CONNECT_MAXWAIT = 6.00
    #: timer length for check_negotiation re-scheduling
    CONNECT_DEFERED = 0.1

    #: default client environment variables,
    default_env = {
        'COLUMNS': '80',
        'LINES': '24',
        'USER': 'unknown',
        'TERM': 'unknown',
        'CHARSET': 'ascii',
    }

    def __init__(self, shell=ConsoleShell, stream=TelnetStream,
                 encoding='utf-8', log=logging):
        self.log = log
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

        self._telopt_negotiation = asyncio.Future()
        self._telopt_negotiation.add_done_callback(
            self.after_telopt_negotiation)

        self._encoding_negotiation = asyncio.Future()
        self._encoding_negotiation.add_done_callback(
            self.after_encoding_negotiation)

    def __str__(self):
        """ Returns string reporting the status of this client session.
        """
        return describe_connection(self)

    def connection_made(self, transport):
        """ Begin a new telnet client connection.

            A ``TelnetStream`` instance is created for reading on
            the transport as ``stream``, and various IAC, SLC.

            ``begin_negotiation()`` is fired after connection
            is registered.
        """
        self.transport = transport
        self._server_ip, self._server_port = (
            transport.get_extra_info('peername'))
        self.stream = self._stream_factory(
            transport=transport, client=True, log=self.log)
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
        """ XXX This method must initialize the class attribute of type
            dict, ``env``, with any values wished to be exported by telnet
            environment sub-negotiation.

            Namely: TERM, COLUMNS, LINES, CHARSET (encoding),
            or any other values wished to be explicitly exported
            from the client's environment by negotiation.

            Otherwise, the values of ``default_env`` are used.
        """
        self.env['TERM'] = self.shell.terminal_type
        self.env['COLUMNS'] = '{}'.format(self.shell.terminal_width)
        self.env['LINES'] = '{}'.format(self.shell.terminal_height)
        self.env['CHARSET'] = self._default_encoding

    def set_stream_callbacks(self):
        """ XXX Set callbacks for returning negotiation responses
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
        """ Callback receives result of server name resolution,
            Logs warning if reverse dns verification failed,
        """
        if arg.cancelled():
            self.log.debug('server dns lookup cancelled')
            return
        if self.server_ip != self.server_reverse_ip.result():
            # OpenSSH will log 'POSSIBLE BREAK-IN ATTEMPT!'
            # but we dont care .. just demonstrating these values,
            self.log.warn('reverse lookup: {sip} != {rsip} ({arg})'.format(
                cip=self.server_ip, rcip=self.server_reverse_ip,
                arg=arg.result()))

    @property
    def server_ip(self):
        """ .. server_ip() -> string

            Returns Server IP address as string.
        """
        return self._server_ip

    @property
    def server_port(self):
        """ .. server_port() -> string

            Returns Server Port address as integer.
        """
        return self._server_port

    @property
    def server_hostname(self):
        """ .. server_hostname() -> Future()

            Returns DNS name of server as Future.
        """
        return dns.future_hostname(
            future_gethostbyaddr=self._server_host,
            fallback_ip=self.server_ip)

    @property
    def server_fqdn(self):
        """ .. server_fqdn() -> Future()

            Returns FQDN dns name of server as Future.
        """
        return dns.future_fqdn(
            future_gethostbyaddr=self._server_host,
            fallback_ip=self.server_ip)

    @property
    def server_reverse_ip(self):
        """ .. server_reverse_ip() -> Future()

            Returns reverse DNS lookup IP address of server as Future.
        """
        return dns.future_reverse_ip(
            future_gethostbyaddr=self._server_host,
            fallback_ip=self.server_ip)

    @property
    def env(self):
        """ Returns hash of session environment values
        """
        return self._env

    @property
    def connected(self):
        """ Returns datetime connection was made.
        """
        return self._connected

    @property
    def inbinary(self):
        """ Returns True if server status ``inbinary`` is True.
        """
        from telnetlib3.telopt import BINARY
        # character values above 127 should not be expected to be read
        # inband from the transport unless inbinary is set True.
        return self.stream.remote_option.enabled(BINARY)

    @property
    def outbinary(self):
        """ Returns True if server status ``outbinary`` is True.
        """
        from telnetlib3.telopt import BINARY
        # character values above 127 should not be written to the transport
        # unless outbinary is set True.
        return self.stream.local_option.enabled(BINARY)

    def encoding(self, outgoing=False, incoming=False):
        """ Returns the session's preferred input or output encoding.

            Always 'ascii' for the direction(s) indicated unless ``inbinary``
            or ``outbinary`` has been negotiated. Then, the session value
            CHARSET is used, or the constructor kwarg ``encoding`` if CHARSET
            is not negotiated.
        """
        # of note: UTF-8 input with ascii output or vice-versa is possible.
        assert outgoing or incoming
        return (self.env.get('CHARSET', self._default_encoding)
                if (outgoing and not incoming and self.outbinary) or (
                    not outgoing and incoming and self.inbinary) or (
                    outgoing and incoming and self.outbinary and self.inbinary
                    ) else 'ascii')

    def send_ttype(self):
        """ Callback for responding to TTYPE requests.
        """
        return self.shell.terminal_type

    def send_tspeed(self):
        """ Callback for responding to TSPEED requests.
        """
        return self.shell.terminal_speed

    def send_xdisploc(self):
        """ Callback for responding to XDISPLOC requests.
        """
        return self.shell.xdisploc

    def send_env(self, keys):
        """ Callback for responding to NEW_ENVIRON requests, from rfc1572:

               The "type"/VALUE pairs must be returned in the same order as
               the SEND request specified them, and there must be a response
               for each "type ..." explicitly requested.

            Returns an ordered iterable of (key, val) pairs, where both key
            and val are ascii-encodable unicode strings.
        """
        if keys is None:
            return self.env
        return dict([(key, self.env.get(key, '')) for key in keys])

    def send_charset(self, offered):
        """ Callback for responding to CHARSET requests, receiving a list of
            character encodings offered by the server, such as 'LATIN-1'.

            Return the character set agreed to use. The default implementation
            selects any matching encoding that python is capable of using, or
            the same as self.encoding if matched in the offered list.
        """
        selected = None
        for offer in offered:
                try:
                    codec = codecs.lookup(offer)
                except LookupError as err:
                    self.log.debug('{}'.format(err))
                else:
                    if (codec.name == self.env['CHARSET'] or not selected):
                        self.env['CHARSET'] = codec.name
                        selected = offer
        if selected:
            self.log.info('Encoding negotiated: {env[CHARSET]}.'
                          .format(env=self.env))
            return selected
        self.log.info('No suitable encoding offered by server: {!r}.'
                      .format(offered))
        return None

    def send_naws(self):
        """ Callback for responding to NAWS requests.
        """
        return self.shell.terminal_width, self.shell.terminal_height

    def begin_negotiation(self):
        """ XXX begin on-connect negotiation.

            A Telnet Server is expected to assert the preferred session
            options immediately after connection, we provide some time
            to receive any of those before giving up.
        """
        if self._closing:
            self._telopt_negotiation.cancel()
            return

        self._loop.call_soon(self.check_negotiation)

    def check_negotiation(self):
        """ XXX negotiation check-loop, schedules itself for continual callback
            until negotiation is considered final, firing
            ``after_telopt_negotiation`` callback when complete.
        """
        if self._closing:
            self._telopt_negotiation.cancel()
            return
        pots = self.stream.pending_option
        if not any(pots.values()):
            if self.duration > self.CONNECT_MINWAIT:
                self._telopt_negotiation.set_result(self.stream.__str__())
                return
        elif self.duration > self.CONNECT_MAXWAIT:
            self._telopt_negotiation.set_result(self.stream.__str__())
            return
        self._loop.call_later(self.CONNECT_DEFERED, self.check_negotiation)

    def after_telopt_negotiation(self, status):
        """ XXX telnet stream option negotiation completed, ``status``
            is an asyncio.Future instance, where method ``.cancelled()``
            returns True if telnet negotiation was not completed to
            satisfation.  Otherwise, containing a string representation
            of the protocol stream status.
        """
        if status.cancelled():
            self.log.debug('telopt negotiation cancelled')
            return
        self.log.debug('stream status: {}.'.format(status.result()))

    def check_encoding_negotiation(self):
        """ XXX encoding negotiation check-loop, schedules itself for continual
            callback until both outbinary and inbinary has been answered in
            the affirmitive, firing ``after_encoding_negotiation`` callback
            when complete.
        """
        from .telopt import DO, BINARY
        if self._closing:
            self._encoding_negotiation.cancel()
            return

        # encoding negotiation is complete
        if self.outbinary and self.inbinary:
            self.log.debug('outbinary and inbinary negotiated.')
            self._encoding_negotiation.set_result(True)

        # if (WILL, BINARY) requested by begin_negotiation() is answered in
        # the affirmitive, then request (DO, BINARY) to ensure bi-directional
        # transfer of non-ascii characters.
        elif self.outbinary and not self.inbinary and (
                not (DO, BINARY,) in self.stream.pending_option):
            self.log.debug('outbinary=True, requesting inbinary.')
            self.stream.iac(DO, BINARY)
            self._loop.call_later(self.CONNECT_DEFERED,
                                  self.check_encoding_negotiation)

        elif self.duration > self.CONNECT_MAXWAIT:
            # Perhaps some IAC interpretering servers do not differentiate
            # 'local' from 'remote' options -- they are treated equivalently.
            self._encoding_negotiation.set_result(False)

        else:
            self._loop.call_later(self.CONNECT_DEFERED,
                                  self.check_encoding_negotiation)

    def after_encoding_negotiation(self, status):
        """ XXX this callback fires after encoding negotiation has completed.
        """
        if status.cancelled():
            self.log.debug('encoding negotiation cancelled')
            return
        self.log.debug('client encoding is {}.'.format(
            self.encoding(outgoing=True, incoming=True)))

    @property
    def duration(self):
        """ Returns seconds elapsed since connected to server.
        """
        return (datetime.datetime.now() - self._connected).total_seconds()

    def data_received(self, data):
        """ Process each byte as received by transport.
        """
        self.log.debug('data_received: {!r}'.format(data))
        self._last_received = datetime.datetime.now()
        for byte in (bytes([value]) for value in data):

            try:
                self.stream.feed_byte(byte)
            except (ValueError, AssertionError) as err:
                self.log.warn(err)
                continue

            if self.stream.is_oob:
                continue

            self.shell.feed_byte(byte)

    def eof_received(self):
        self.connection_lost('EOF')
        return False

    def connection_lost(self, exc):
        if not self._closing:
            self.log.info('{about}{reason}'.format(
                about=self.__str__(),
                reason='{}: '.format(exc) if exc is not None else ''))
        self._closing = True


def describe_connection(client):
    if client._closing:
        direction = 'from'
        state = 'Disconnected'
    else:
        direction = 'to'
        state = 'Connected'
    if (client.server_hostname.done() and
            client.server_hostname.result() != client.server_ip):
        hostname = ' ({})'.format(client.server_hostname.result())
    else:
        hostname = ''
    if client.server_port != 23:
        port = ' port 23'
    else:
        port = ''

    duration = '{:0.1f}s'.format(client.duration)
    return ('{state} {direction} {serverip}{port}{hostname} after {duration}'
            .format(
                state=state,
                direction=direction,
                serverip=client.server_ip,
                port=port,
                hostname=hostname,
                duration=duration)
            )
