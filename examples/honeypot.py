#!/usr/bin/env python3

import argparse
import logging
import random

import telnetlib3
from telnetlib3 import tulip
from telnetlib3 import TelnetServer, Telsh, TelnetStream, TelnetShellStream

#: A simple SLC tab that offers nearly all characters for negotiation,
#  but has no default values of its own, soliciting them from client.
from telnetlib3.slc import SLC_FORW1, SLC, SLC_NOSUPPORT, _POSIX_VDISABLE
from telnetlib3.slc import SLC_FORW2, SLC_EOF, SLC_EL, SLC_ABORT, SLC_XOFF
from telnetlib3.slc import SLC_RP, SLC_AO, SLC_AYT, SLC_SYNCH, SLC_EC, SLC_IP
from telnetlib3.slc import SLC_XON, SLC_EW, SLC_LNEXT, SLC_SUSP, SLC_BRK
from telnetlib3.slc import SLC_EOR
SLC_TAB = { SLC_FORW1: SLC(SLC_NOSUPPORT, _POSIX_VDISABLE),
            SLC_FORW2: SLC(SLC_NOSUPPORT, _POSIX_VDISABLE),
            SLC_EOF: SLC(), SLC_EC: SLC(),
            SLC_EL: SLC(), SLC_IP: SLC(),
            SLC_ABORT: SLC(), SLC_XON: SLC(),
            SLC_XOFF: SLC(), SLC_EW: SLC(),
            SLC_RP: SLC(), SLC_LNEXT: SLC(),
            SLC_AO: SLC(), SLC_SUSP: SLC(),
            SLC_AYT: SLC(), SLC_BRK: SLC(),
            SLC_SYNCH: SLC(), SLC_EOR: SLC(), }

# honeypot shell,

#class HoneyShellStream(TelnetShellStream):
#    default_env_request = (
#            "USER HOSTNAME UID EUID TERM COLUMNS LINES DISPLAY SYSTEMTYPE "
#            "LOGNAME VISUAL EDITOR BASH_VERSION KSH_VERSION PWD OLDPWD "
#            "SSH_CONNECTION SSH_CLIENT SSH_TTY SSH_AUTH_SOCK SHELL "
#            "PS1 PS2 _ LANG SHELLOPTS BASHOPTS SFUTLNTVER SFUTLNTMODE "
#            "MACHTYPE HOSTTYPE LC_ALL HISTFILE TMPDIR SECONDS "
#            ).split()
#
#    def write(self, string, errors=None):
#        TelnetShellStream.write(self, string, errors)
#
#    def echo(self, string, errors=None):
#        if self.will_echo:
#            TelnetShellStream.write(self, string, errors)

# a modified variant of 'telsh', which acts sh-like enough,
# but aware of the 4-stage context; command processing doesnt
# begin until after AUTH

class HoneyShell(Telsh):
    def __init__(self, server, stream=TelnetShellStream, log=logging):
        Telsh.__init__(self, server, stream, log)
        self.behaviors = [
                NONAME, DDWRT, CWAV1, CAIR1, CAIR2, P661HNUF1, BUSYBOX,
                NOLOGIN, NONENAME, LOCALHOST, LOCALHOSTLOCALDOMAIN, BADNAT, ]
        self.pick_behavior()

    def pick_behavior(self):
        """ Chose a behavior for target end.
        """
        self.honey = random.choice(self.behaviors)()

    @property
    def prompt(self):
        """ Returns context-aware prompt depending on honeypot behavior
        """
        if self.server.state == self.server.BANNER:
            self.server.state = self.server.LOGIN
            return self.honey.display_banner() + self.honey.display_login()
        elif self.server.state == self.server.LOGIN:
            return self.honey.display_login()
        elif self.server.state == self.server.AUTH:
            return self.honey.display_password()
        elif self.server.state == self.server.SHELL:
            return Telsh.prompt(self)

# Honeypot telnet server
# a modified variant of the basic TelnetStream that requests additional
# ENV parameters

class HoneyStream(TelnetStream):
    default_env_request = (
            "USER HOSTNAME UID EUID TERM COLUMNS LINES DISPLAY SYSTEMTYPE "
            "LOGNAME VISUAL EDITOR BASH_VERSION KSH_VERSION PWD OLDPWD "
            "SSH_CONNECTION SSH_CLIENT SSH_TTY SSH_AUTH_SOCK SHELL "
           "PS1 PS2 _ LANG SHELLOPTS BASHOPTS SFUTLNTVER SFUTLNTMODE "
            "MACHTYPE HOSTTYPE LC_ALL HISTFILE TMPDIR SECONDS "
            ).split()

# a modified telnet server, with 4-stage promotion process

class TelnetHoneypotServer(TelnetServer):
    BANNER, LOGIN, AUTH, SHELL = range(4)
    state = BANNER
    attempt = 0
    default_slc_tab = SLC_TAB

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
        self.fast_edit = False
        TelnetServer.begin_negotiation(self)

    def request_advanced_opts(self):
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

        telnetlib3.server.TelnetServer.request_advanced_opts(self)

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
        self.shell.display_prompt()

# various behaviors found in the wild

class SimplePass():
    def display_password(self):
        return '\r\nPassword: '

class NoBanner():
    def display_banner(self):
        return ''

class NONAME(SimplePass, NoBanner):
    def display_login(self):
        return 'login: '

class DDWRT(SimplePass):
    def display_banner(self):
        return ('DD-WRT v24-sp2 std (c) 2011 NewMedia-NET GmbH\r\n'
                'Release: 05/08/11 (SVN revision: 16994)\r\n')
    def display_login(self):
        return '\r\nDD-WRT login: '

class CWAV1(SimplePass, NoBanner):
    def display_login(self):
        return 'CWAV-275v2TT login: '

class CAIR1(SimplePass, NoBanner):
    def display_login(self):
        return 'CAir5452 login: '

class CAIR2(SimplePass, NoBanner):
    def display_login(self):
        return 'CAir5341TT login: '

class P661HNUF1(SimplePass, NoBanner):
    def display_login(self):
        return 'P-661HNU-F1 login: '

class BUSYBOX(SimplePass, NoBanner):
    def display_login(self):
        return 'BusyBox on localhost login: '

class NOLOGIN(SimplePass, NoBanner):
    def display_login(self):
        return self.display_password()

class NONENAME(SimplePass, NoBanner):
    def display_login(self):
        return '(none) login: '

class LOCALHOST(SimplePass, NoBanner):
    def display_login(self):
        return 'localhost login: '

class LOCALHOSTLOCALDOMAIN(SimplePass, NoBanner):
    def display_login(self):
        return 'localhost.localdomain login: '

class BADNAT(SimplePass, NoBanner):
    def display_login(self):
        return '192.168.0.9 login: '

# program arguments

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
    honey_server_factory = lambda: TelnetHoneypotServer(
        stream=HoneyStream, shell=HoneyShell, encoding='ascii')
    func = loop.start_serving(honey_server_factory, args.host, args.port)

    socks = loop.run_until_complete(func)
    logging.info('Listening on %s', socks[0].getsockname())
    loop.run_forever()

if __name__ == '__main__':
    main()
