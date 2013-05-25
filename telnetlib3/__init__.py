#!/usr/bin/env python3
"""
Telnet Protocol using the 'tulip' project of PEP 3156.

Requires Python 3.3.

For convenience, the 'tulip' module is included.

See the ``README`` file for details and license.
"""
__author__ = "Jeffrey Quast"
__url__ = u'https://github.com/jquast/telnetlib3/'
__copyright__ = "Copyright 2013"
__credits__ = ["Jim Storch",]
__license__ = 'ISC'


__all__ = ['TelnetServer', 'TelnetStreamReader']
import argparse

import tulip
from server import TelnetServer
from telopt import TelnetStreamReader

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

    for sock in loop.run_until_complete(func):
        # XXX --- we could set our socket OOBINLINE, and recieve IAC+DM as
        # a normally interpreted signal, and find some way to go about
        # ignoring it; but the only impl. I've found is BSD Client, which
        # appears to lock up after 'send synch' --- XXX
        #sock.setsockopt(socket.SOL_SOCKET, socket.SO_OOBINLINE, 1)
        logging.info('Listening on %s', sock.getsockname())
    loop.run_forever()

if __name__ == '__main__':
    main()

