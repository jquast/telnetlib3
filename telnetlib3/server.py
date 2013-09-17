#!/usr/bin/env python3
import collections
import datetime
import argparse
import logging
import socket
import time

from telnetlib3 import tulip
from telnetlib3.telsh import Telsh
from telnetlib3.telopt import TelnetStream


__all__ = ('TelnetServer',)

class TelnetServer(tulip.protocols.Protocol):
    """
        The begin_negotiation() method is called on-connect,
        displaying the login banner, and indicates desired options.

            The default implementations sends only: iac(DO, TTYPE).

        The negotiation DO-TTYPE is twofold: provide at least one option to
        negotiate to test the remote iac interpreter. If the remote end
        replies in the affirmitive, then ``request_advanced_opts()`` is
        called. The default implementation prefers remote line editing,
        kludge mode, and finally default NVT half-duplex local-line mode.
    """
    #: mininum on-connect time to wait for client-initiated negotiation options
    CONNECT_MINWAIT = 2.00
    #: maximum on-connect time to wait for client-initiated negotiation options
    #  before negotiation is considered 'final'. some telnet clients will fail
    #  to acknowledge bi-directionally, appearing as a timeout, while others
    #  are simply on very high-latency links.
    CONNECT_MAXWAIT = 6.00
    #: timer length for check_negotiation re-scheduling
    CONNECT_DEFERED = 0.2
    TTYPE_LOOPMAX = 8
    default_env = {
            'COLUMNS': '80',
            'LINES': '24',
            'USER': 'unknown',
            'TERM': 'unknown',
            'CHARSET': 'ascii',
            'PS1': '%s-%v %# ',
            'PS2': '> ',
            'TIMEOUT': '5',
            }

    readonly_env = ['USER', 'HOSTNAME', 'UID', 'REMOTEIP', 'REMOTEHOST']
    def __init__(self, shell=Telsh, stream=TelnetStream,
                       encoding='utf8', log=logging):
        self.log = log
        self._shell_factory = shell
        self._stream_factory = stream
        self._default_encoding = encoding
        #: session environment as S.env['key'], defaults empty string value
        self._client_env = collections.defaultdict(str, **self.default_env)
        self._client_host = tulip.Future()

        #: default environment is server-preferred encoding if un-negotiated.
        self.env_update({'CHARSET': encoding})

        #: 'ECHO off' set for clients capable of remote line editing (fastest).
        self.fast_edit = True

        #: toggled when transport is shutting down
        self._closing = False

        #: datetime of last byte received
        self._last_received = None

        #: datetime of connection made
        self._connected = None

        #: client performed ttype; probably human
        self._advanced = False

        loop = tulip.get_event_loop()

        #: prompt sequence '%h' is result of socket.gethostname().
        self._server_name = loop.run_in_executor(None, socket.gethostname)
        self._server_name.add_done_callback(self.after_server_gethostname)

        self._server_fqdn = tulip.Future()
        self._timeout = tulip.Future()
        self._negotiation = tulip.Future()
        self._negotiation.add_done_callback(self.after_negotiation)
        self._banner = tulip.Future()

    def connection_made(self, transport):
        """ Receive a new telnet client connection.

            A ``TelnetStream`` instance is created for reading on
            the transport as ``stream``, and various IAC, SLC, and
            extended callbacks are registered to local handlers.

            A ``TelnetShell`` instance is created for writing on
            the transport as ``shell``. It receives in-band data
            from the telnet transport, providing line editing and
            command line processing.

            ``begin_negotiation()`` is fired after connection is
            registered.
        """
        self.transport = transport
        self._client_ip = transport.get_extra_info('addr')[0]
        self.stream = self._stream_factory(
                transport=transport, server=True, log=self.log)
        self.shell = self._shell_factory(server=self, log=self.log)
        self.set_stream_callbacks()
        self._last_received = datetime.datetime.now()
        self._connected = datetime.datetime.now()

        # resolve client fqdn (and later, reverse-dns)
        loop = tulip.get_event_loop()
        self._client_host = loop.run_in_executor(None,
                socket.gethostbyaddr, self._client_ip)
        self._client_host.add_done_callback(self.after_client_lookup)

        # begin connect-time negotiation
        loop.call_soon(self.begin_negotiation)

    def set_stream_callbacks(self):
        """ XXX Set default iac, slc, and ext callbacks for telnet stream
        """
        stream, server = self.stream, self
        # wire AYT and SLC_AYT (^T) to callback ``status()``
        #from telnetlib3 import slc, telopt
        from telnetlib3.slc import SLC_AYT
        from telnetlib3.telopt import AYT, AO, IP, BRK, SUSP, ABORT
        from telnetlib3.telopt import TTYPE, TSPEED, XDISPLOC, NEW_ENVIRON
        from telnetlib3.telopt import LOGOUT, SNDLOC, CHARSET, NAWS
        stream.set_iac_callback(AYT, self.handle_ayt)
        stream.set_slc_callback(SLC_AYT, self.handle_ayt)

        # wire various 'interrupts', such as AO, IP to
        # ``interrupt_received``
        for sir in (AO, IP, BRK, SUSP, ABORT,):
            stream.set_iac_callback(sir, self.interrupt_received)

        # wire extended rfc callbacks for terminal atributes, etc.
        for (opt, ext) in (
                (TTYPE, self.ttype_received),
                (TSPEED, self.tspeed_received),
                (XDISPLOC, self.xdisploc_received),
                (NEW_ENVIRON, self.env_update),
                (NAWS, self.naws_received),
                (LOGOUT, self.logout),
                (SNDLOC, self.sndloc_received),
                (CHARSET, self.charset_received),):
            stream.set_ext_callback(opt, ext)

    def begin_negotiation(self):
        """ XXX begin on-connect negotiation.

            A Telnet Server is expected to assert the preferred session
            options immediately after connection.

            The default implementation sends only (DO, TTYPE): the default
            ``ttype_received()`` handler fires ``request_advanced_opts()``,
            further requesting more advanced negotiations that may otherwise
            confuse or corrupt output of the remote end if it is not equipped
            with an IAC interpreter, such as a network scanner.
        """
        if self._closing:
            self._negotiation.cancel()
            return
        from telnetlib3.telopt import DO, TTYPE
        self.stream.iac(DO, TTYPE)

        tulip.get_event_loop().call_soon(self.check_negotiation)

        self.shell.display_prompt()

    def begin_encoding_negotiation(self):
        """ XXX Request bi-directional binary encoding and CHARSET; called only
            if remote end replies affirmitively to (DO, TTYPE).
        """
        from telnetlib3.telopt import WILL, BINARY, DO, CHARSET
        self.stream.iac(WILL, BINARY)
        self.stream.iac(DO, CHARSET)

        loop = tulip.get_event_loop()
        loop.call_soon(self.check_encoding)

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
        loop = tulip.get_event_loop()
        loop.call_later(self.CONNECT_DEFERED, self.check_negotiation)

    def check_encoding(self):
        """ XXX encoding negotiation check-loop, schedules itself for continual
            callback until both outbinary and inbinary has been answered in
            the affirmitive, firing ``after_encoding_negotiation`` callback
            when complete.
        """
        from telnetlib3.telopt import DO, BINARY
        if self._closing:
            self._encoding_negotiation.cancel()
            return

        loop = tulip.get_event_loop()

        # encoding negotiation is complete
        if self.outbinary and self.inbinary:
            self.log.debug('outbinary and inbinary negotiated.')
            loop.call_soon(self.after_encoding)

        # if (WILL, BINARY) requested by begin_negotiation() is answered in
        # the affirmitive, then request (DO, BINARY) to ensure bi-directional
        # transfer of non-ascii characters.
        elif self.outbinary and not self.inbinary and (
                not (DO, BINARY,) in self.stream.pending_option):
            self.log.debug('outbinary=True, requesting inbinary.')
            self.stream.iac(DO, BINARY)
            loop.call_later(self.CONNECT_DEFERED, self.check_encoding)

        elif self.duration > self.CONNECT_MAXWAIT:
            # Many IAC interpreters do not differentiate 'local' from 'remote'
            # tintin++ for example, cannot answer "DONT BINARY" after already
            # having sent "WONT BINARY"; it wrongly evaluates all telnet
            # options as single direction, client-host viewpoint, thereby
            # "failing" to negotiate a pending option (the code ignores it, as
            # it has "already been sent"). Note, that these kinds of IAC
            # interpreters may be discovered by requesting (DO, ECHO): the
            # client answers (WILL, ECHO), which is proposterous.
            loop.call_soon(self.after_encoding)

        else:
            loop.call_later(self.CONNECT_DEFERED, self.check_encoding)

    def after_negotiation(self, status):
        """ XXX telnet stream option negotiation completed
        """
        from telnetlib3.telopt import WONT, ECHO

        # enable 'fast edit' for remote line editing by sending 'wont echo'
        if self.fast_edit and self.stream.mode == 'remote':
            self.log.debug('fast_edit enabled (wont echo)')
            self.stream.iac(WONT, ECHO)

        loop = tulip.get_event_loop()
        self._client_host = loop.run_in_executor(None,
                socket.gethostbyaddr, self._client_ip)
        self._client_host.add_done_callback(self.after_client_lookup)

        # log about connection
        self.log.info('{}.'.format(self))
        self.log.info('stream status is {}.'.format(self.stream))
        self.log.info('client environment is {}.'.format(describe_env(self)))

    def after_encoding(self):
        """ XXX encoding negotiation has completed
        """
        self.log.info('client encoding is {}.'.format(
            self.encoding(outgoing=True, incoming=True)))

    def request_advanced_opts(self):
        """ XXX Request advanced telnet options; called only if remote
            end replies affirmitively to (DO, TTYPE).
        """
        # Once the remote end has been identified as capable of at least TTYPE,
        # this callback is fired a single time. This is the preferred method
        # of delaying advanced negotiation attempts only for those clients
        # deemed smart enough to attempt them, as some non-compliant clients
        # may crash or close connection on receipt of unsupported options.

        # Request *additional* TTYPE response from clients who have replied
        # already, beginning a 'looping' mechanism of ``ttype_received()``
        # replies, by by which MUD clients may be identified.
        from telnetlib3.telopt import WILL, DO, SGA, ECHO, LINEMODE
        from telnetlib3.telopt import LFLOW, NEW_ENVIRON, NAWS, STATUS

        # 'supress go-ahead' + 'will echo' is kludge mode remote line editing
        self.stream.iac(WILL, SGA)
        self.stream.iac(WILL, ECHO)

        # LINEMODE negotiation solicits advanced remote line editing.
        self.stream.iac(DO, LINEMODE)

        # bsd telnet client uses STATUS to verify option state.
        self.stream.iac(WILL, STATUS)

        # lineflow allows pause/resume of transmission.
        self.stream.iac(WILL, LFLOW)

        # the 'new_environ' variables reveal client exported values.
        self.stream.iac(DO, NEW_ENVIRON)

        # 'negotiate about window size', for effective screen draws.
        self.stream.iac(DO, NAWS)

        if self.env['TTYPE0'] != 'ansi':
            # windows-98 era telnet ('ansi'), or terminals replying as
            # such won't have anything more interesting to say in reply
            # to subsequent requests for TTYPE. Windows socket transport
            # is said to hang if a second TTYPE is requested, others may
            # fail to reply.
            self.stream.request_ttype()

            # Also begin request of CHARSET, and bi-directional BINARY.
            self.begin_encoding_negotiation()

    def ttype_received(self, ttype):
        """ Callback for TTYPE response.

        The first firing of this callback signals an advanced client and
        is awarded with additional opts by ``request_advanced_opts()``.

        Otherwise the session variable TERM is set to the value of ``ttype``.
        """
        loop = tulip.get_event_loop()

        if self._advanced is False:
            self._advanced = 1
            self.log.debug('ttype received: {}'.format(ttype))
            if not self.env['TERM']:
                self.env_update({'TERM': ttype})
            # track TTYPE seperately from the NEW_ENVIRON 'TERM' value to
            # avoid telnet loops in TTYPE cycling
            self.env_update({'TERM': ttype})
            self.env_update({'TTYPE0': ttype})
            loop.call_soon(self.request_advanced_opts)
            return

        self.env_update({'TTYPE{}'.format(self._advanced): ttype})
        lastval = self.env['TTYPE{}'.format(self._advanced -1)].lower()
        if ttype == self.env['TTYPE0']:
            self.env_update({'TERM': ttype})
            self.log.debug('end on TTYPE{}: {}, using {env[TERM]}.'
                    .format(self._advanced, ttype, env=self.env))
            return
        # if ttype is empty or maximum loops reached, stop.
        elif (not ttype or
                self._advanced == self.TTYPE_LOOPMAX or
                ttype.lower() == 'unknown'):
            ttype = self.env['TERM'].lower()
            self.env_update({'TERM': ttype})
            self.log.debug('TTYPE stop on {}, using {env[TERM]}.'.format(
                self._advanced, env=self.env))
            return
        # Mud Terminal type (MTTS), use previous ttype, end negotiation
        elif (self._advanced == 2 and
                ttype.upper().startswith('MTTS ')):
            revert_ttype = self.env['TTYPE1']
            self.env_update({'TERM': revert_ttype})
            self.log.debug('TTYPE{} is {}, using {env[TERM]}.'.format(
                self._advanced, ttype, env=self.env))
            return
        # ttype value has looped, use ttype, end negotiation
        elif (ttype.lower() == lastval):
            self.log.debug('TTYPE repeated at {}, using {}.'.format(
                self._advanced, ttype))
            self.env_update({'TERM': ttype})
            return
        ttype = ttype.lower()
        self.env_update({'TERM': ttype})
        self.stream.request_ttype()
        self._advanced += 1


    def data_received(self, data):
        """ Process each byte as received by transport.

            Derived impl. should instead extend or override the
            ``line_received()`` and ``char_received()`` methods.
        """
        self.log.debug('data_received: {!r}'.format(data))
        self._last_received = datetime.datetime.now()
        self._restart_timeout()
        for byte in (bytes([value]) for value in data):

            try:
                self.stream.feed_byte(byte)
            except (ValueError, AssertionError) as err:
                self.log.warn(err)
                continue

            if self.stream.is_oob:
                continue

            if self.stream.slc_received:
                self.shell.feed_slc(byte, func=self.stream.slc_received)
                continue

            self.shell.feed_byte(byte)

    def interrupt_received(self, cmd):
        """ XXX Callback receives telnet IAC or SLC interrupt byte.

            This is suitable for the receipt of interrupt signals,
            such as iac(AO) and SLC_AO.
        """
        from telnetlib3.telopt import name_command
        self.log.debug('interrupt_received: {}'.format(name_command(cmd)))
        self.shell.display_prompt()

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

    def handle_ayt(self, *args):
        """ XXX Callback when AYT or SLC_AYT is received.

            Outputs status of connection and re-displays prompt.
        """
        self.shell.stream.write('\r\n{}.'.format(self.__str__()))
        self.shell.display_prompt()

    def timeout(self):
        """ XXX Callback received on session timeout.
        """
        self.shell.stream.write(
                '\r\nTimeout after {:1.0f}s.\r\n'.format(self.idle))
        self.log.debug('Timeout after {:1.3f}s.'.format(self.idle))
        self.transport.close()

    def logout(self, opt=None):
        """ XXX Callback received by shell exit or IAC-<opt>-LOGOUT.
        """
        from telnetlib3.telopt import DO
        if opt is not None and opt != DO:
            return self.stream.handle_logout(opt)
        self.log.debug('Logout by client.')
        msgs = ('The black thing inside rejoices at your departure',
                'The very earth groans at your depature',
                'The very trees seem to moan as you leave',
                'Echoing screams fill the wastelands as you close your eyes',
                'Your very soul aches as you wake up from your favorite dream')
        self.shell.stream.write(
                '\r\n{}.\r\n'.format(
            msgs[int(time.time()/84) % len(msgs)]))
        self.transport.close()

    def eof_received(self):
        self._closing = True

    def connection_lost(self, exc):
        self._closing = True
        self.log.info('{}{}'.format(self.__str__(),
            ': {}'.format(exc) if exc is not None else ''))
        for task in (self._server_name, self._server_fqdn,
                self._client_host, self._timeout):
            task.cancel()

    def env_update(self, env):
        " Callback receives no environment variables "
        # if client sends 'HOSTNAME' variable, store as '_HOSTNAME'
#    readonly_env = ['USER', 'HOSTNAME', 'UID', 'REMOTEIP', 'REMOTEHOST']
# 
#        if 'HOSTNAME' in env:
#            env['_HOSTNAME'] = env.pop('HOSTNAME')
#        for key, value in self.env.items():
#            if key in self.default_env and value == self.default_env[key]:
#                continue
#            if key in ('HOSTNAME',):
#                continue
#            env_fingerprint[key] = value

        if 'TERM' in env and env['TERM']:
            ttype = env['TERM'].lower()
            if ttype != self.env['TERM']:
               self.log.debug('{!r} -> {!r}'.format(self.env['TERM'], ttype))
            self.shell.term_received(ttype)
            self._client_env['TERM'] = ttype
            del env['TERM']
        if 'TIMEOUT' in env and env['TIMEOUT'] != self.env['TIMEOUT']:
            try:
                val = int(env['TIMEOUT'])
                self._client_env['TIMEOUT'] = env['TIMEOUT']
                self._restart_timeout(val)
            except ValueError as err:
                self.log.debug('bad TIMEOUT {!r}, {}.'.format(
                    env['TIMEOUT'], err))
        else:
            self._client_env.update(env)
            self.log.debug('env_update: %r', env)

    def after_client_lookup(self, arg):
        """ Callback receives result of client name resolution,
            Logs warning if reverse dns verification failed,
        """
        if self.client_ip != self.client_reverse_ip.result():
            # OpenSSH will log 'POSSIBLE BREAK-IN ATTEMPT!' but we dont care ..
            self.log.warn('reverse mapping failed: {}'.format(
                self.arg.result()))
        self.env_update({
            'REMOTEIP': self.client_ip,
            'REMOTEHOST': self.client_hostname.result(),
            })

    def after_server_gethostname(self, arg):
        """ Callback receives result of server name resolution,
            Begins fqdn resolution, available as '%H' prompt character.
        """
        #: prompt sequence '%H' is result of socket.get_fqdn(self._server_name)
        self._server_fqdn = tulip.get_event_loop().run_in_executor(
                    None, socket.getfqdn, arg.result())
        self._server_fqdn.add_done_callback(self.after_server_getfqdn)
        self.env_update({'HOSTNAME': self.server_name.result()})

    def after_server_getfqdn(self, arg):
        """ Callback receives result of server fqdn resolution,
        """
        if arg.cancelled():
            self.log.debug('getfqdn cancelled')
        else:
            if self.env['HOSTNAME'] != arg.result():
                self.env_update({'HOSTNAME': arg.result()})
                self.log.debug('HOSTNAME is {}'.format(arg.result()))

    def __str__(self):
        """ XXX Returns string suitable for status of server session.
        """
        return describe_connection(self)

    @property
    def default_banner(self):
        """ .. default_banner() -> string

            Returns first banner string written to stream during negotiation.
        """
        if self.server_fqdn.done():
            return 'Welcome to {} !'.format(self.server_fqdn.result())
        return ''

    @property
    def client_ip(self):
        """ .. client_ip() -> string

            Returns Client IP address as string.
        """
        return self._client_ip

    @property
    def client_hostname(self):
        """ .. client_hostname() -> Future()

            Returns DNS name of client as String as Future.
        """
        if self._client_host.done():
            val = self._client_host.result()[0]
            return _wrap_future_result(self._client_host, val)
        return self._client_host

    @property
    def client_fqdn(self):
        """ .. client_fqdn() -> Future()

            Returns FQDN dns name of client as Future.
        """
        if self._client_host.done():
            val = self._client_host.result()[0]
            return _wrap_future_result(self._client_host, val)
        return self._client_host

    @property
    def client_reverse_ip(self):
        """ .. client_fqdn() -> Future()

            Returns reverse DNS lookup IP address of client as Future.
        """
        if self._client_host.done():
            val = self._client_host.result()[2][0]
            return _wrap_future_result(self._client_host, val)
        return self._client_host

    @property
    def server_name(self):
        """ .. server_name() -> Future()

            Returns name of server as string as Future.
        """
        if self._server_name.done():
            return self._server_name

    @property
    def server_fqdn(self):
        """ .. server_fqdn() -> Future()

            Returns fqdn string of server as Future.
        """
        return self._server_fqdn

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
    def duration(self):
        """ Returns seconds elapsed since client connected.
        """
        return (datetime.datetime.now() - self._connected).total_seconds()

    @property
    def idle(self):
        """ Returns seconds elapsed since last received data on transport.
        """
        return (datetime.datetime.now() - self._last_received).total_seconds()

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

    def _restart_timeout(self, val=None):
        self._timeout.cancel()
        loop = tulip.get_event_loop()
        val = val if val is not None else self.env['TIMEOUT']
        if val:
            try:
                val = int(val)
            except ValueError:
                val = ''
            if val:
                self._timeout = loop.call_later(val * 60, self.timeout)

    def charset_received(self, charset):
        " Callback receives CHARSET value, rfc2066. "
        self.env_update({'CHARSET': charset.lower()})

    def naws_received(self, width, height):
        " Callback receives NAWS (negotiate about window size), rfc1073. "
        self.env_update({'COLUMNS': str(width), 'LINES': str(height)})

    def xdisploc_received(self, xdisploc):
        " Callback receives XDISPLOC value, rfc1096. "
        self.env_update({'DISPLAY': xdisploc})

    def tspeed_received(self, rx, tx):
        " Callback receives TSPEED values, rfc1079. "
        self.env_update({'TSPEED': '%s,%s' % (rx, tx)})

    def sndloc_received(self, location):
        " Callback receives SNDLOC values, rfc779. "
        self.env_update({'SNDLOC': location})

def describe_env(server):
    env_fingerprint = dict()
    for key, value in server.env.items():
        # do not display default env values, or our own hostname
        if key in server.default_env and value == server.default_env[key]:
            continue
        if key in ('HOSTNAME',):
            continue
        env_fingerprint[key] = value
    return '{{{}}}'.format(', '.join(['{!r}: {!r}'.format(key, value)
        for key, value in sorted(env_fingerprint.items())]))

def describe_connection(server):
    return '{}{}{}{}'.format(
            # user [' using <terminal> ']
            '{}{} '.format(server.env['USER'],
                ' using' if server.env['TERM'] != 'unknown' else ''),
            '{} '.format(server.env['TERM'])
            if server.env['TERM'] != 'unknown' else '',
            # state,
            '{}connected from '.format(
                'dis' if server._closing else ''),
            # ip, dns
            '{}{}'.format(
                server.client_ip, ' ({}{})'.format(
                    server.client_hostname.result(),
                    ('' if server.client_ip
                        == server.client_reverse_ip.result()
                        else server.standout('!= {}, revdns-fail'.format(
                            server.client_reverse_ip.result()))
                        ) if server.client_reverse_ip.done() else '')
                    if server.client_hostname.done() else ''),
            ' after {:0.3f}s'.format(server.duration))

def _wrap_future_result(future, result):
    future = tulip.Future()
    future.set_result(result)
    return future

ARGS = argparse.ArgumentParser(description="Run simple telnet server.")
ARGS.add_argument(
    '--host', action="store", dest='host',
    default='127.0.0.1', help='Host name')
ARGS.add_argument(
    '--port', action="store", dest='port',
    default=6023, type=int, help='Port number')
ARGS.add_argument(
    '--loglevel', action="store", dest="loglevel",
    default='info', type=str, help='Loglevel (debug,info)')

def main():
    import locale
    args = ARGS.parse_args()
    if ':' in args.host:
        args.host, port = args.host.split(':', 1)
        args.port = int(port)
    locale.setlocale(locale.LC_ALL, '')
    enc = locale.getpreferredencoding()
    log = logging.getLogger()
    log_const = args.loglevel.upper()
    assert (log_const in dir(logging)
            and isinstance(getattr(logging, log_const), int)
            ), args.loglevel
    log.setLevel(getattr(logging, log_const))
    log.debug('default_encoding is {}'.format(enc))

    loop = tulip.get_event_loop()
    func = loop.start_serving(lambda: TelnetServer(encoding=enc, log=log),
            args.host, args.port)

    socks = loop.run_until_complete(func)
    logging.info('Listening on %s', socks[0].getsockname())
    loop.run_forever()

if __name__ == '__main__':
    main()

#self.shell.display_prompt
#self.shell.feed_slc
#self.shell.feed_byte
#self.shell.stream.write
#self.shell.display_prompt
#self.shell.stream.write
#self.shell.stream.write
#self.shell.term_received
#self.shell.display_prompt
#self.shell.stream.write
