#!/usr/bin/env python3
"""
A Demonstrating TelnetClient implementation.

This script provides a keyboard-interactive shell for connecting to a Server.
"""

import argparse
import logging
import asyncio
import sys

import telnetlib3

ARGS = argparse.ArgumentParser(description="Connect to telnet server.")
ARGS.add_argument(
    '--host', action="store", dest='host',
    default='127.0.0.1', help='Host name')
ARGS.add_argument(
    '--port', action="store", dest='port',
    default=6023, type=int, help='Port number')
ARGS.add_argument(
    '--loglevel', action="store", dest="loglevel",
    default='info', type=str, help='Loglevel (debug,info)')


def start_client(loop, log, host, port):
    import locale
#    import os
    locale.setlocale(locale.LC_ALL, '')
    enc = locale.getpreferredencoding()
    client_task = asyncio.Task(loop.create_connection(
        lambda: telnetlib3.TelnetClient(encoding=enc, log=log), host, port))
    loop.run_until_complete(client_task)
#        loop.run_until_complete(t)
#    transport, protocol = yield from loop.create_connection(
#        lambda: TelnetClient(encoding=enc, log=log), host, port)
#
#    # keyboard input reader; catch stdin bytes and pass through transport
#    # line-oriented for now,
#    def stdin_callback():
#        inp = sys.stdin.buffer.readline()
#        inp = os.read(sys.stdin.fileno(), 1)
#        transport.write(inp)
#        log.debug('stdin_callback: {!r}'.format(inp))
#        if not inp:
#            loop.stop()
#        else:
#
#    loop.add_reader(sys.stdin.fileno(), stdin_callback)


def main():
    args = ARGS.parse_args()
    if ':' in args.host:
        args.host, port = args.host.split(':', 1)
        args.port = int(port)

    # use generic 'logging' instance, and set the log-level as specified by
    # command-line argument --loglevel
    fmt = '%(asctime)s %(filename)s:%(lineno)d %(message)s'
    logging.basicConfig(format=fmt)
    log = logging.getLogger('telnet_server')
    log.setLevel(getattr(logging, args.loglevel.upper()))

    loop = asyncio.get_event_loop()
    start_client(loop, log, args.host, args.port)
    loop.run_forever()


if __name__ == '__main__':
    import tty
    import termios
    mode = termios.tcgetattr(sys.stdin.fileno())
    tty.setcbreak(sys.stdin.fileno(), termios.TCSANOW)
    try:
        main()
    finally:
        termios.tcsetattr(sys.stdin.fileno(), termios.TCSAFLUSH, mode)
