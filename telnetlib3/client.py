import collections
import datetime
import logging
import socket

import asyncio

from telnetlib3.telopt import TelnetStream
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
    CONNECT_DEFERED = 0.2

    #: default client environment variables,
    default_env = {
        'COLUMNS': '80',
        'LINES': '24',
        'USER': 'unknown',
        'TERM': 'unknown',
        'CHARSET': 'ascii',
    }

    def __init__(self, shell=ConsoleShell, stream=TelnetStream,
                 encoding='utf8', log=logging):
        self.log = log
        self._shell_factory = shell
        self._stream_factory = stream
        self._default_encoding = encoding
        self._loop = asyncio.get_event_loop()

        #: session environment as S.env['key'], defaults empty string value
        self._client_env = collections.defaultdict(str, **self.default_env)

        #: toggled when transport is shutting down
        self._closing = False

        #: datetime of last byte received
        self._last_received = None

        #: datetime of connection made
        self._connected = None

        self._negotiation = asyncio.Future()
        self._negotiation.add_done_callback(self.after_negotiation)
        #: future result stores value of gethostbyaddr(sever_ip)
        self._server_host = asyncio.Future()

        #: server_fqdn is result of socket.getfqdn() of server_host
        self._server_fqdn = asyncio.Future()


    def __str__(self):
        """ XXX Returns string suitable for status of server session.
        """
        return describe_connection(self)

    def connection_made(self, transport):
        """ Begin a new telnet client connection.

            A ``TelnetStream`` instance is created for reading on
            the transport as ``stream``, and various IAC, SLC.

            ``begin_negotiation()`` is fired after connection
            is registered.
        """
        self.log.debug('connection made')
        self.transport = transport
        self._server_ip, self._server_port = (
            transport.get_extra_info('peername'))
        self.stream = self._stream_factory(
            transport=transport, client=True, log=self.log)
        self.shell = self._shell_factory(client=self, log=self.log)
        self.init_environment()
        self.set_stream_callbacks()
        self._last_received = datetime.datetime.now()
        self._connected = datetime.datetime.now()

        loop = asyncio.get_event_loop()
        # resolve server fqdn (and later, reverse-dns)
        self._server_host = self._loop.run_in_executor(
            None, socket.gethostbyaddr, self._server_ip)
        self._server_host.add_done_callback(self.after_server_lookup)

        # begin connect-time negotiation
        loop.call_soon(self.begin_negotiation)
        desc_port = (
            '' if self.server_port == 23 else
            ' (port {})'.format(self.server_port))
        self.log.info('Connected to {}{}.'.format(self.server_ip, desc_port))

    def init_environment(self):
        """ XXX This method must initialize the class attribute of type
            dict, ``env``, with any values wished to be exported by telnet
            environment sub-negotiation.  Namely: TERM, COLUMNS, LINES,
            CHARSET, or any other values wished to be explicitly exported
            from the client's environment by negotiation.

            Otherwise, the values of ``default_env`` are used.
        """
        self.env['TERM'] = self.shell.terminal_type
        self.env['COLUMNS'] = self.shell.terminal_width
        self.env['LINES'] = self.shell.terminal_height
        self.env['CHARSET'] = self._default_encoding

    def after_server_lookup(self, arg):
        """ Callback receives result of server name resolution,
            Logs warning if reverse dns verification failed,
        """
        if arg.cancelled():
            self.log.debug('server dns lookup cancelled')
            return
        if self.host_ip != self.host_reverse_ip.result():
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
        return self._client_env

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

    def set_stream_callbacks(self):
        """ XXX Set callbacks for returning negotiation responses
        """
#        stream, server = self.stream, self
#        # wire AYT and SLC_AYT (^T) to callback ``status()``
#        #from telnetlib3 import slc, telopt
#        from telnetlib3.slc import SLC_AYT
#        from telnetlib3.telopt import AYT, AO, IP, BRK, SUSP, ABORT
        from telnetlib3.telopt import TTYPE, TSPEED, XDISPLOC, NEW_ENVIRON
        from telnetlib3.telopt import CHARSET, NAWS
#        from telnetlib3.telopt import LOGOUT, SNDLOC, CHARSET, NAWS
#        stream.set_iac_callback(AYT, self.handle_ayt)
#        stream.set_slc_callback(SLC_AYT, self.handle_ayt)

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

    def send_ttype(self):
        """ Callback for responding to TTYPE requests.
        """
        return (self.shell.terminal_type).encode('ascii')

    def send_tspeed(self):
        """ Callback for responding to TSPEED requests.
        """
        return self.shell.terminal_speed

    def send_xdisploc(self):
        """ Callback for responding to XDISPLOC requests.
        """
        return (self.shell.xdisploc).encode('ascii')

    def send_env(self, keys):
        """ Callback for responding to NEW_ENVIRON requests.
        """
        if keys is None:
            return self.env
        return dict([(key, self.env.get(key, '')) for key in keys])

    def send_charset(self):
        """ Callback for responding to CHARSET requests.
        """
        return self._default_encoding

    def send_naws(self):
        """ Callback for responding to NAWS requests.
        """
        return self.shell.terminal_width, self.shell.terminal_height

    def begin_negotiation(self):
        """ XXX begin on-connect negotiation.

            A Telnet Server is expected to assert the preferred session
            options immediately after connection.
        """
        if self._closing:
            self._negotiation.cancel()
            return

        asyncio.get_event_loop().call_soon(self.check_negotiation)

    def check_negotiation(self):
        """ XXX negotiation check-loop, schedules itself for continual callback
            until negotiation is considered final, firing ``after_negotiation``
            callback when complete.
        """
        if self._closing:
            self._negotiation.cancel()
            return
        pots = self.stream.pending_option
        if not any(pots.values()):
            if self.duration > self.CONNECT_MINWAIT:
                self._negotiation.set_result(self.stream.__repr__())
                return
        elif self.duration > self.CONNECT_MAXWAIT:
            self._negotiation.set_result(self.stream.__repr__())
            return
        loop = asyncio.get_event_loop()
        loop.call_later(self.CONNECT_DEFERED, self.check_negotiation)

    def after_negotiation(self, status):
        """ XXX telnet stream option negotiation completed
        """
        self.log.info('{}.'.format(self))
        self.log.info('stream status is {}.'.format(self.stream))

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
        self._closing = True
        self.log.info('{}: {}'.format(self.__str__(),
                                      exc if exc is not None else ''))


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
