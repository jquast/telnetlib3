#!/usr/bin/env python3
import collections
import datetime
import argparse
import logging
import socket
import time

from telnetlib3 import tulip
from telnetlib3 import telsh, telopt, slc


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
    CONNECT_MINWAIT = 1.50
    #: maximum on-connect time to wait for client-initiated negotiation options
    #  before negotiation is considered 'final'. some telnet clients will fail
    #  to acknowledge bi-directionally, appearing as a timeout, while others
    #  are simply on very high-latency links.
    CONNECT_MAXWAIT = 6.00
    #: timer length for check_negotiation re-scheduling
    CONNECT_DEFERED = 0.10
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
    def __init__(self, shell=telsh.Telsh, encoding='utf8', log=logging):
        self.log = log
        self._shell_factory = shell
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

        #: datetime
        self._last_received = None

        #: datetime
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

            A ``TelnetStreamReader`` instance is created for reading on
            the transport as ``stream``, and various IAC, SLC, and
            extended callbacks are registered to local handlers.

            A ``TelnetShell`` instance is created for writing on
            the transport as ``shell``. It receives in-band data
            from the telnet transport, providing line editing and
            command line processing.

            ``begin_negotiation()`` is fired after connection is registered.
        """
        self.transport = transport
        self._client_ip = transport.get_extra_info('addr')[0]
        self.stream = telopt.TelnetStreamReader(transport, server=True)
        self.shell = self._shell_factory(server=self)
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
        stream.set_iac_callback(telopt.AYT, self.handle_ayt)
        stream.set_slc_callback(slc.SLC_AYT, self.handle_ayt)

        # wire various 'interrupts', such as AO, IP to
        # ``interrupt_received``
        for sir in (telopt.AO, telopt.IP, telopt.BRK,
                telopt.SUSP,telopt.ABORT,):
            stream.set_iac_callback(sir, self.interrupt_received)

        # wire extended rfc callbacks for terminal atributes, etc.
        for (opt, ext) in (
                (telopt.NEW_ENVIRON, self.env_update),
                (telopt.TTYPE, self.ttype_received),
                (telopt.NAWS, self._naws_update),
                (telopt.CHARSET, self._charset_received),):
            stream.set_ext_callback(opt, ext)


    def begin_negotiation(self):
        """ XXX begin on-connect negotiation. A Telnet Server is expected to
            assert the preferred session options immediately after connection.

            The default implementation sends only (DO, TTYPE), the default
            ``ttype_received()`` fires ``request_advanced_opts()``, further
            requesting more advanced negotiations that may otherwise confuse
            or corrupt output of the remote end if it is not equipped with an
            IAC interpreter (such as a net scanner).
        """

        if self._closing:
            self._negotiation.cancel()
            return

        self.stream.iac(telopt.DO, telopt.TTYPE)
        tulip.get_event_loop().call_soon(self.check_negotiation)
        self.shell.display_prompt()

    def check_negotiation(self):
        """ XXX negotiation check-loop, schedules itself for continual callback
            until negotiation is considered final, firing ``after_negotiation``
            callback.
        """
        print('x')
        def _build_status(stream):
            """ Build simple dict of negotiation status """
            local = stream.local_option
            remote = stream.remote_option
            pending = stream.pending_option
            status = dict()
            if any(pending.values()):
                status.update({'failed_pending':
                    [telopt.name_commands(opt)
                        for (opt, val) in pending.items() if val]})
            if len(local):
                status.update({'local_options':
                    [telopt.name_commands(opt)
                        for (opt, val) in local.items() if val]})
            if len(remote):
                status.update({'remote_options':
                    [telopt.name_commands(opt)
                        for (opt, val) in remote.items() if val]})
            return status

        if self._closing:
            self._negotiation.cancel()
            return

        # negotiation completed when pending options have been acknowledged
        if not any(self.stream.pending_option.values()):
            if self.duration > self.CONNECT_MINWAIT:
                self._negotiation.set_result(_build_status(self.stream))
                return
        elif self.duration > self.CONNECT_MAXWAIT:
            self._negotiation.set_result(_build_status())
            return
        loop = tulip.get_event_loop()
        loop.call_later(self.CONNECT_DEFERED, self.check_negotiation)

    def after_negotiation(self, status):
        """ XXX negotiation completed
        """
        self.log.debug('after_negotiation: {}'.format(status))

        # enable 'fast edit' for remote line editing by sending 'wont echo'
        if self.fast_edit and self.stream.mode == 'remote':
            self.log.debug('fast_edit enabled (remote editing, wont echo)')
            self.stream.iac(telopt.WONT, telopt.ECHO)

        # log about connection
        self.log.info(self.__str__())
        self.log.info('{}'.format(status.__repr__()))

            # 
#        pending = [for opt in self.stream.pending_option.items() if val]
#        pending = [telopt.name_commands(opt)
#                for (opt, val) in self.stream.pending_option.items() if val]
#        debug_pending = ', '.join(pending)
        # disable echo for advanced clients w/remote editing (bsd)

#        self._negotiation = tulip.Future()
#
#
#        if self.duration < self.CONNECT_MINWAIT or (
#                pending and self.duration < self.CONNECT_MAXWAIT):
#            loop.call_later(self.CONNECT_DEFERED, self.check_negotiation)
#        elif pending:
#            self.log.warn('failed to negotiate {<0.60s}{}.'.format(
#                debug_pending, '..' if len(debug_pending) > 60 else ''))
#            loop.call_soon(self.after_negotiation)


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
        self.log.debug('interrupt_received: {}'.format(
            telopt.name_command(cmd)))
        self.shell.display_prompt()


    def __str__(self):
        """ XXX Returns string suitable for status of server session.
        """
        return _describe_connection(self)

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
        # character values above 127 should not be expected to be read
        # inband from the transport unless inbinary is set True.
        return self.stream.remote_option.enabled(telopt.BINARY)

    @property
    def outbinary(self):
        """ Returns True if server status ``outbinary`` is True.
        """
        # character values above 127 should not be written to the transport
        # unless outbinary is set True.
        return self.stream.local_option.enabled(telopt.BINARY)

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
                if (outgoing and not incoming and self.outbinary or
                    not outgoing and incoming and self.inbinary or
                    outgoing and incoming and self.outbinary
                                          and self.inbinary)
                else 'ascii')

    def request_advanced_opts(self, ttype=True):
        """ XXX Request advanced telnet options when remote end replies TTYPE.
        """

        # Once the remote end has been identified as capable of at least TTYPE,
        # this callback is fired a single time. This is the preferred method
        # of delaying advanced negotiation attempts only for those clients
        # deemed smart enough to attempt (as some non-compliant clients may
        # crash or close connection on receipt of unsupported options).

        # Request *additional* TTYPE response from clients who have replied
        # already, beginning a 'looping' mechanism of ``ttype_received()``
        # replies, by by which MUD clients may be identified.
        self.stream.iac(telopt.WILL, telopt.SGA)
        self.stream.iac(telopt.WILL, telopt.ECHO)
        self.stream.iac(telopt.WILL, telopt.BINARY)
        self.stream.iac(telopt.DO, telopt.LINEMODE)
        self.stream.iac(telopt.WILL, telopt.STATUS)
        self.stream.iac(telopt.WILL, telopt.LFLOW)
        self.stream.iac(telopt.DO, telopt.NEW_ENVIRON)
        self.stream.iac(telopt.DO, telopt.NAWS)
        self.stream.iac(telopt.DO, telopt.CHARSET)
        self.stream.iac(telopt.DO, telopt.TTYPE)
        # FIFO guarentees WILL, BINARY has been answered at this point; only
        # request (DO, BINARY) if it was answered affirmative --
        #   tintin++, for instance, cannot answer "DONT BINARY" after already
        #   sending "WONT BINARY". It wrongly evaluates all telnet options as
        #   a single-direction, client-host viewpoint (answers: WILL ECHO! lol)
        if self.stream.local_option.enabled(telopt.BINARY):
            self.stream.iac(telopt.DO, telopt.BINARY)
        if ttype and self.stream.remote_option.enabled(telopt.TTYPE):
            # we've already accepted their ttype, but see what else they have!
            self.stream.request_ttype()

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

    def logout(self, opt=telopt.DO):
        """ XXX Callback received by shell exit or IAC-<opt>-LOGOUT.
        """
        if opt != telopt.DO:
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

    def ttype_received(self, ttype):
        """ Callback for TTYPE response.

        The first firing of this callback signals an advanced client and
        is awarded with additional opts by ``request_advanced_opts()``.

        Otherwise the session variable TERM is set to the value of ``ttype``.
        """
        # there is no sort of acknowledgement protocol ..
        if self._advanced is False:
            self.log.debug('first ttype: {}'.format(ttype))
            if not len(self.env['TERM']):
                self.env_update({'TERM': ttype})
            # track TTYPE seperately from the NEW_ENVIRON 'TERM' value to
            # avoid telnet loops in TTYPE cycling
            self.env_update({'TTYPE0': ttype})
            self.env_update({'TERM': ttype})
            # windows-98 era telnet ('ansi'), or terminals replying as
            # such won't have anything more interesting to say. windows
            # socket transport locks up if a second TTYPE is requested.
            self.request_advanced_opts(ttype=(ttype != 'ansi'))
            self._advanced = 1
            return

        self.env_update({'TTYPE{}'.format(self._advanced): ttype})
        lastval = self.env['TTYPE{}'.format(self._advanced -1)].lower()
        if ttype == self.env['TTYPE0']:
            self.env_update({'TERM': ttype})
            self.log.debug('end on TTYPE{}: {}, using {env[TERM]}.'
                    .format(self._advanced, ttype, env=self.env))
            return
        elif (not ttype or self._advanced == self.TTYPE_LOOPMAX or ttype.lower() == 'unknown'):
            ttype = self.env['TERM'].lower()
            self.env_update({'TERM': ttype})
            self.log.debug('TTYPE stop on {}, using {env[TERM]}.'.format(
                self._advanced, env=self.env))
            return
        elif (self._advanced == 2 and ttype.upper().startswith('MTTS ')):
            # Mud Terminal type started, previous value is most termcap-like
            revert_ttype = self.env['TTYPE1']
            self.env_update({'TERM': revert_ttype})
            self.log.debug('TTYPE{} is mud client; {}, using {env[TERM]}.'.format(
                self._advanced, ttype, env=self.env))
            return
        elif (ttype.lower() == lastval):
            # End of list (looping). Chose this value
            self.log.debug('TTYPE repeated at {}, using {}.'.format(
                self._advanced, ttype))
            self.env_update({'TERM': ttype})
            return
        ttype = ttype.lower()
        self.env_update({'TERM': ttype})
        self.stream.request_ttype()
        self._advanced += 1

    def env_update(self, env):
        " Callback receives no environment variables "
        if 'HOSTNAME' in env:
            env['REMOTEHOST'] = env.pop('HOSTNAME')
        if 'TERM' in env and env['TERM']:
            ttype = env['TERM'].lower()
            if ttype != self.env['TERM']:
               self.log.debug('{!r} -> {!r}'.format(self.env['TERM'], ttype))
            self.shell.term_received(ttype)
            self._client_env['TERM'] = ttype
            del env['TERM']
        if 'TIMEOUT' in env and env['TIMEOUT'] != self.env['TIMEOUT']:
            self._client_env['TIMEOUT'] = env['TIMEOUT']
            self._restart_timeout()
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
        #self.env_update({'HOSTNAME': self.server_name.result()})

    def after_server_getfqdn(self, arg):
        """ Callback receives result of server fqdn resolution,
        """
        if self.env['HOSTNAME'] != arg.result():
            #self.env_update({'HOSTNAME': arg.result()})
            self.log.debug('HOSTNAME fully resolved to {}'.format(arg.result()))
        else:
            self.log.debug('HOSTNAME is {}'.format(arg.result()))


    def _restart_timeout(self, val=None):
        self._timeout.cancel()
        val = val if val is not None else self.env['TIMEOUT']
        if val and int(val):
            self._timeout = tulip.get_event_loop().call_later(
                    int(val) * 60, self.timeout)

    def _charset_received(self, charset):
        " Callback receives CHARSET value, rfc2066 "
        self.env_update({'CHARSET': charset.lower()})

    def _naws_update(self, width, height):
        " Callback receives NAWS values, rfc1073 "
        self.env_update({'COLUMNS': str(width), 'LINES': str(height)})

    def _xdisploc_received(self, xdisploc):
        " Callback receives XDISPLOC value, rfc1096 "
        self.env_update({'DISPLAY': xdisploc})

    def _tspeed_received(self, rx, tx):
        " Callback receives TSPEED values, rfc1079 "
        self.env_update({'TSPEED': '%s,%s' % (rx, tx)})

def _describe_connection(server):
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
                    (', dns-ok' if server.client_ip
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
    import logging
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
