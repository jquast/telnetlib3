#!/usr/bin/env python3
import collections
import datetime
import argparse
import logging
import socket
import time

import tulip
import telsh
import telopt
import slc

__all__ = ['TelnetServer']

class TelnetServer(tulip.protocols.Protocol):
    """
        The banner() method is called on-connect, displaying the login banner,
        and indicates the desired telnet options.

            The default implementations sends only: iac(WILL, SGA),
            iac(WILL, ECHO), and iac(DO, TTYPE).

        The negotiation DO-TTYPE is twofold: provide at least one option to
        negotiate to test the remote iac interpreter, (if any!). If the remote
        end replies in the affirmitive, then ``request_advanced_opts()`` is
        called.
    """
    # TODO: Future, callback_after_complete(first_prompt)
    CONNECT_MINWAIT = 0.50
    CONNECT_MAXWAIT = 4.00
    CONNECT_DEFERED = 0.15
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

    readonly_env = ['USER', 'HOSTNAME', 'UID']
    def __init__(self, log=logging, default_encoding='utf8'):
        self.log = log
        #: cient_env holds client session variables
        self._client_env = collections.defaultdict(str, **self.default_env)
        self._default_encoding = self._client_env['CHARSET'] = default_encoding
        self._closing = False
        self._last_received = None
        self._connected = None
        self._advanced = False

        #: prompt sequence '%h' is result of socket.gethostname()
        self._server_name = tulip.get_event_loop().run_in_executor(None,
                socket.gethostname)

    def connection_made(self, transport):
        """ Receive a new telnet client connection.

            A ``TelnetStreamReader`` instance is created for reading on
            the transport as ``i_stream``, and various IAC, SLC, and
            extended callbacks are registered.

            A ``TelnetShell`` instance is created for writing on
            the transport as ``shell``. It receives in-band data
            from the telnet transport, providing line editing and
            command line processing.

            Then, ``banner()`` is fired.
        """
        self.transport = transport
        self.stream = telopt.TelnetStreamReader(transport, server=True)
        _set_default_callbacks(server=self, stream=self.stream)
        self.shell = telsh.Telsh(server=self)
        self._last_received = datetime.datetime.now()
        self._connected = datetime.datetime.now()
        self._server_fqdn = tulip.Future()
        self._timeout = tulip.Future()
        self._client_ip = transport.get_extra_info('addr')[0]
        self._client_hostname = tulip.get_event_loop().run_in_executor(None,
                socket.gethostbyaddr, self._client_ip)  # client fqdn,
        self._client_hostname.add_done_callback(  # check reverse-dns
                self.completed_client_lookup)
        self._banner_displayed = False
        loop = tulip.get_event_loop()
        loop.call_soon(self.banner)
        loop.call_soon(self._negotiate, self.first_prompt)

    def banner(self):
        """ XXX Display login banner and solicit initial telnet options.
        """
        #   The default initially sets 'kludge' mode, which does not warrant
        #   any reply and is always compatible with any client NVT.
        #
        #   Notably, a request to negotiate TTYPE is made. If sucessful,
        #   the callback ``request_advanced_opts()`` is fired.
        #
        #   The reason all capabilities are not immediately announced is that
        #   the remote end may be too dumb to advance any further, and these
        #   additional negotiations can only serve to confuse the remote end
        #   or erroneously display garbage output if remote end is not equipped
        #   with an iac interpreter.

        self.stream.iac(telopt.WILL, telopt.SGA)
        self.stream.iac(telopt.WILL, telopt.ECHO)
        self.stream.iac(telopt.DO, telopt.TTYPE)

    def first_prompt(self, call_after=None):
        """ XXX First time prompt fire
        """
        call_after = (self.shell.display_prompt
                if call_after is None else call_after)
        assert callable(call_after), call_after

        self.log.info(self.__str__())

        # conceivably, you could use various callback mechanisms to
        # relate to authenticating or other multi-state login process.
        loop = tulip.get_event_loop()
        loop.call_soon(call_after)

    def data_received(self, data):
        """ Process each byte as received by transport.

            Derived impl. should instead extend or override the
            ``line_received()`` and ``char_received()`` methods.
        """
        self.log.debug('data_received: {!r}'.format(data))
        self._last_received = datetime.datetime.now()
        self._restart_timeout()
        for byte in (bytes([value]) for value in data):

            self.stream.feed_byte(byte)

            if self.stream.is_oob:
                continue

            if self.stream.slc_received:
                self.shell.feed_slc(byte, slc=self.stream.slc_received)
                continue

            self.shell.feed_byte(byte)

    def __str__(self):
        """ XXX Returns string suitable for status of server session.
        """
        return _describe_connection(self)

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
        if self._client_hostname.done():
            val = self._client_hostname.result()[0]
            return _wrap_future_result(self._client_hostname, val)
        return self._client_hostname

    @property
    def client_fqdn(self):
        """ .. client_fqdn() -> Future()

            Returns FQDN dns name of client as Future.
        """
        if self._client_hostname.done():
            val = self._client_hostname.result()[0]
            return _wrap_future_result(self._client_hostname, val)
        return self._client_hostname

    @property
    def client_reverse_ip(self):
        """ .. client_fqdn() -> Future()

            Returns reverse DNS lookup IP address of client as Future.
        """
        if self._client_hostname.done():
            val = self._client_hostname.result()[2][0]
            return _wrap_future_result(self._client_hostname, val)
        return self._client_hostname

    @property
    def server_name(self):
        """ .. server_name() -> Future()

            Returns name of server as string as Future.
        """
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
            CHARSET is used, or ``default_encoding``, if CHARSET is not
            negotiated.
        """
        # of note: UTF-8 input with ascii output or vice-versa is possible.
        assert outgoing or incoming
        return (self.env.get('CHARSET', self._default_encoding)
                if (outgoing and not incoming and self.outbinary or
                    not outgoing and incoming and self.inbinary or
                    outgoing and incoming and self.outbinary
                                          and self.inbinary)
                else 'ascii')

    def completed_client_lookup(self, arg):
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

    def completed_server_lookup(self, arg):
        """ Callback receives result of server name resolution,
            Begins fqdn resolution, available as '%H' prompt character.
        """
        #: prompt sequence '%H' is result of socket.get_fqdn(self._server_name)
        self._server_fqdn = tulip.get_event_loop().run_in_executor(
                    None, socket.getfqdn, arg.result())


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
        self.stream.iac(telopt.DO, telopt.LINEMODE)
        self.stream.iac(telopt.WILL, telopt.STATUS)
        self.stream.iac(telopt.WILL, telopt.LFLOW)
        self.stream.iac(telopt.DO, telopt.NEW_ENVIRON)
        self.stream.iac(telopt.DO, telopt.NAWS)
        self.stream.iac(telopt.DO, telopt.CHARSET)
        self.stream.iac(telopt.DO, telopt.TTYPE)
        self.stream.iac(telopt.DO, telopt.BINARY)
        self.stream.iac(telopt.WILL, telopt.BINARY)
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
                self._client_hostname, self._timeout):
            task.cancel()

    def ttype_received(self, ttype):
        """ Callback for TTYPE response.

        The first firing of this callback signals an advanced client and
        is awarded with additional opts by ``request_advanced_opts()``.

        Otherwise the session variable TERM is set to the value of ``ttype``.
        """
        # there is no sort of acknowledgement protocol ..
        if self._advanced is False:
            if not len(self.env['TERM']):
                self.env_update({'TERM': ttype})
            # track TTYPE seperately from the NEW_ENVIRON 'TERM' value to
            # avoid telnet loops in TTYPE cycling
            self.env_update({'TTYPE0': ttype})
            # windows-98 era telnet ('ansi'), or terminals replying as
            # such won't have anything more interesting to say. windows
            # socket transport locks up if a second TTYPE is requested.
            self.request_advanced_opts(ttype=(ttype != 'ansi'))
            self._advanced = 1
            return

        self.env_update({'TTYPE{}'.format(self._advanced): ttype})
        lastval = self.env['TTYPE{}'.format(self._advanced)]
        if ttype == self.env['TTYPE0']:
            self.env_update({'TERM': ttype})
            self.log.debug('end on TTYPE{}: {}, using {env[TERM]}.'
                    .format(self._advanced, ttype, env=self.env))
            return
        elif (self._advanced == self.TTYPE_LOOPMAX
                or not ttype or ttype.lower() == 'unknown'):
            ttype = self.env['TERM'].lower()
            self.env_update({'TERM': ttype})
            self.log.debug('TTYPE stop on {}, using {env[TERM]}.'.format(
                self._advanced, env=self.env))
            return
        elif (self._advanced == 2 and ttype.upper().startswith('MTTS ')):
            # Mud Terminal type started, previous value is most termcap-like
            ttype = self.env['TTYPE{}'.format(self._advanced)]
            self.env_update({'TERM': ttype})
            self.log.debug('TTYPE is {}, using {env[TERM]}.'.format(
                self._advanced, env=self.env))
        elif (ttype.lower() == lastval):
            # End of list (looping). Chose this value
            self.log.debug('TTYPE repeated at {}, using {}.'.format(
                self._advanced, ttype))
            self.env_update({'TERM': ttype})
            return
        ttype = ttype.lower()
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
            self.shell.set_term(ttype)
            self._client_env['TERM'] = ttype
            del env['TERM']
        if 'TIMEOUT' in env and env['TIMEOUT'] != self.env['TIMEOUT']:
            self._client_env['TIMEOUT'] = env['TIMEOUT']
            self._restart_timeout()
        else:
            self._client_env.update(env)
            self.log.debug('env_update: %r', env)

    def interrupt_received(self, cmd):
        """ XXX Callback receives telnet IAC or SLC interrupt byte.

            This is suitable for the receipt of interrupt signals,
            such as iac(AO) and SLC_AO.
        """
        self.log.debug('interrupt_received: {}'.format(
            telopt.name_command(cmd)))
        self.shell.display_prompt()

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

    def _negotiate(self, call_after=None):
        """
        Negotiate options before prompting for input, this method calls itself
        every CONNECT_DEFERED up to the greater of the value CONNECT_MAXWAIT.

        Negotiation completes when all ``pending_options`` of the
        TelnetStreamReader have completed. Any options not negotiated
        are displayed to the client as a warning, and
        ``shell.display_prompt()`` is called for the first time,
        unless ``call_after`` specifies another callback.
        """
        if call_after is None:
            call_after = self.first_prompt
        assert callable(call_after), call_after
        if self._closing:
            return
        if not self._banner_displayed and self.server_fqdn.done():
            self.shell.stream.write('Welcome to {}! '.format(
                    self.server_fqdn.result()))
            self._banner_displayed = True
        loop = tulip.get_event_loop()
        pending = [telopt.name_commands(opt)
                for (opt, val) in self.stream.pending_option.items()
                if val]
        if self.duration < self.CONNECT_MINWAIT or (
                pending and self.duration < self.CONNECT_MAXWAIT):
            loop.call_later(self.CONNECT_DEFERED, self._negotiate, call_after)
            return
        elif pending:
            self.log.warn('negotiate failed for {}.'.format(pending))
            self.shell.write('\r\nnegotiate failed for {}.'.format(pending))
        loop.call_soon(call_after)

def _set_default_callbacks(server, stream):
    """ Register callbacks of TelnetStreamReader for default TelnetServer
    """
    # wire AYT and SLC_AYT (^T) to callback ``status()``
    stream.set_iac_callback(telopt.AYT, server.handle_ayt)
    stream.set_slc_callback(slc.SLC_AYT, server.handle_ayt)

    # wire various 'interrupts', such as AO, IP to ``interrupt_received``
    stream.set_iac_callback(telopt.AO, server.interrupt_received)
    stream.set_iac_callback(telopt.IP, server.interrupt_received)
    stream.set_iac_callback(telopt.BRK, server.interrupt_received)
    stream.set_iac_callback(telopt.SUSP, server.interrupt_received)
    stream.set_iac_callback(telopt.ABORT, server.interrupt_received)

    # wire extended rfc callbacks for terminal type, dimensions
    stream.set_ext_callback(telopt.NEW_ENVIRON, server.env_update)
    stream.set_ext_callback(telopt.TTYPE, server.ttype_received)
    stream.set_ext_callback(telopt.NAWS, server._naws_update)
    stream.set_ext_callback(telopt.CHARSET, server._charset_received)

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
    func = loop.start_serving(lambda: TelnetServer(default_encoding=enc),
            args.host, args.port)

    socks = loop.run_until_complete(func)
    logging.info('Listening on %s', socks[0].getsockname())
    loop.run_forever()

if __name__ == '__main__':
    main()

