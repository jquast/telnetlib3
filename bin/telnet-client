#!/usr/bin/env python3
"""
A Demonstrating TelnetClient implementation.

This script provides a keyboard-interactive shell for connecting to a Server.

"""
import argparse
import logging
import asyncio
import termios
import locale
import codecs
import fcntl
import tty
import sys
import os

import telnetlib3

from cp437 import convert as convert_cp437


ARGS = argparse.ArgumentParser(
    description="Connect to telnet host",
    formatter_class=argparse.ArgumentDefaultsHelpFormatter)
ARGS.add_argument('host', nargs=1, help='Host name', action='store')
ARGS.add_argument('port', nargs='?', help='Port number',
                  default=23, type=int)
ARGS.add_argument('--loglevel', help='Logging level',
                  action="store", dest="loglevel",
                  default='info', type=str)
ARGS.add_argument('--cp437', help='enable utf-8 translation of PC-DOS art',
                  action="store_true", dest="cp437",
                  default=False)


class CP437ConsoleShell(telnetlib3.conio.ConsoleShell):
    def write(self, string=u''):
        """ Write string to output using cp437->utf-8 encoding
        """
        super().write(convert_cp437(string))


@asyncio.coroutine
def start_client(loop, log, Client, host, port, cp437=False):
    transport, protocol = yield from loop.create_connection(Client, host, port)

    def keyboard_input():
        ucs = sys.stdin.read(1000)
        protocol.stream.write(protocol.shell.encode(ucs))
        if not protocol.shell.will_echo:
            protocol.shell.write(ucs)

    loop.add_reader(sys.stdin.buffer.fileno(), keyboard_input)
    yield from protocol.waiter


def main():
    args = ARGS.parse_args()
    args.host = args.host[0]
    if ':' in args.host:
        args.host, port = args.host.split(':', 1)
        args.port = int(port)

    fmt = '%(asctime)s %(levelname)s %(filename)s:%(lineno)d %(message)s'
    logging.basicConfig(format=fmt)
    log = logging.getLogger('telnet_server')
    log.setLevel(getattr(logging, args.loglevel.upper()))

    locale.setlocale(locale.LC_ALL, '')
    enc = codecs.lookup(locale.getpreferredencoding()).name
    if args.cp437:
        assert enc == 'utf-8', ('cp437 is only for utf-8 clients', enc)
        Client = lambda: telnetlib3.TelnetClient(
            encoding='latin1', log=log, shell=CP437ConsoleShell)
    else:
        Client = lambda: telnetlib3.TelnetClient(
            encoding=enc, log=log)

    loop = asyncio.get_event_loop()
    loop.run_until_complete(start_client(
        loop, log, Client, args.host, args.port))


if __name__ == '__main__':
    mode = termios.tcgetattr(sys.stdin.fileno())
    tty.setcbreak(sys.stdin.fileno())
    fl = fcntl.fcntl(sys.stdin.fileno(), fcntl.F_GETFL)
    fcntl.fcntl(sys.stdin.fileno(), fcntl.F_SETFL, fl | os.O_NONBLOCK)
    try:
        main()
    finally:
        termios.tcsetattr(sys.stdin.fileno(), termios.TCSAFLUSH, mode)
        fcntl.fcntl(sys.stdin.fileno(), fcntl.F_SETFL, fl)
