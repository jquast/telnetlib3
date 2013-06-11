#!/usr/bin/env python3
#: A simple SLC tab that offers nearly all characters for negotiation,
#  but has no default values of its own, soliciting them from client.
#DEFAULT_SLC_TAB = {
#        SLC_FORW1: SLC(SLC_NOSUPPORT, _POSIX_VDISABLE),
#        SLC_FORW2: SLC(SLC_NOSUPPORT, _POSIX_VDISABLE),
#        SLC_EOF: SLC(), SLC_EC: SLC(),
#        SLC_EL: SLC(), SLC_IP: SLC(),
#        SLC_ABORT: SLC(), SLC_XON: SLC(),
#        SLC_XOFF: SLC(), SLC_EW: SLC(),
#        SLC_RP: SLC(), SLC_LNEXT: SLC(),
#        SLC_AO: SLC(), SLC_SUSP: SLC(),
#        SLC_AYT: SLC(), SLC_BRK: SLC(),
#        SLC_SYNCH: SLC(), SLC_EOR: SLC(), }
import argparse

import telnetlib3
from telnetlib3 import tulip, TelnetServer, TelnetStream, Telsh

class HoneyStream(TelnetStream):
    default_env_request = (
            "USER HOSTNAME UID EUID TERM COLUMNS LINES DISPLAY SYSTEMTYPE "
            "LOGNAME VISUAL EDITOR BASH_VERSION KSH_VERSION PWD OLDPWD "
            "SSH_CONNECTION SSH_CLIENT SSH_TTY SSH_AUTH_SOCK SHELL "
            "PS1 PS2 _ LANG SHELLOPTS BASHOPTS SFUTLNTVER SFUTLNTMODE "
            "MACHTYPE HOSTTYPE LC_ALL HISTFILE TMPDIR SECONDS "
            ).split()

class HoneypotShell(Telsh):
    @property
    def prompt(self):
        """ Returns PS1 or PS2 prompt depending on current multiline context,
            with prompt escape `%' resolved for special values.
        """
        if self.server.state == self.server.BANNER:
            return ''
        if self.server.state == self.server.LOGIN:
            return 'login: '
        elif self.server.state == self.server.AUTH:
            return 'password: '
        return '$ '

    def display_prompt(self, redraw=False, input=None):
        input = ''.join((ucs if (self.stream.can_write(ucs)
            and ucs.isprintable()) else '' for ucs in self.lastline))
        self.stream.write(''.join(('\r\n', self.prompt, input,)))
        if self._send_ga:
            self.stream.send_ga()

class TelnetHoneypotServer(TelnetServer):
    BANNER, LOGIN, AUTH, SHELL = range(4)
    state = BANNER
    attempt = 0

    @property
    def default_banner(self):
        """ .. default_banner() -> string

            Returns first banner string written to stream during negotiation.
        """
        if self.server_fqdn.done():
            return 'Welcome to {} !\r\nlogin: '.format(
                    self.server_fqdn.result())
        return ''

    def begin_negotiation(self):
        TelnetServer.begin_negotiation(self)

    def request_advanced_opts(self, ttype):
        from telnetlib3 import telopt
        for opt in range(240):
            if bytes([opt]) not in (telopt.TM, telopt.LINEMODE):
                self.stream.iac(telopt.DONT, bytes([opt]))

        for opt in range(240):
            if bytes([opt]) not in (telopt.LOGOUT):
                self.stream.iac(telopt.DO, bytes([opt]))

        for opt in range(240):
            if bytes([opt]) not in (telopt.TM, telopt.LINEMODE):
                self.stream.iac(telopt.WONT, bytes([opt]))

        for opt in range(240):
            if bytes([opt]) not in (telopt.LOGOUT, telopt.TM, telopt.LINEMODE):
                self.stream.iac(telopt.WILL, bytes([opt]))

        telnetlib3.server.TelnetServer.request_advanced_opts(self, ttype)

    def line_received(self, input, *args):
        self.log.debug('line_received: {!r}'.format(input))
        if self.strip_eol:
            input = input.rstrip(self.strip_eol)
        if self.state == self.LOGIN or self.state == self.BANNER:
            self.env['_LOGIN{}'.format(self.attempt)] = input
            if input:
                self.state == self.AUTH
        elif self.state == self.AUTH:
            self.env['_PASSWORD{}'.format(self.attempt)] = input
            self.attempt += 1
        self._lastline.clear()
        self.display_prompt()

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
    args = ARGS.parse_args()
    if ':' in args.host:
        args.host, port = args.host.split(':', 1)
        args.port = int(port)
    log = logging.getLogger()
    log_const = args.loglevel.upper()
    assert (log_const in dir(logging)
            and isinstance(getattr(logging, log_const), int)
            ), args.loglevel
    log.setLevel(getattr(logging, log_const))

    loop = tulip.get_event_loop()
    func = loop.start_serving(lambda: TelnetHoneypotServer(
        stream=HoneyStream, encoding='ascii'), args.host, args.port)

    socks = loop.run_until_complete(func)
    logging.info('Listening on %s', socks[0].getsockname())
    loop.run_forever()

if __name__ == '__main__':
    main()

