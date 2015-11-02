#!/usr/bin/env python3
"""Telnet Demonstration Client for the 'telnetlib3' python package.
"""
# std imports
import contextlib
import argparse
import logging
import asyncio
import sys

# local
import telnetlib3


def get_logger(loglevel='info', logfile=None):
    fmt = '%(asctime)s %(levelname)s %(filename)s:%(lineno)d %(message)s'
    lvl = getattr(logging, loglevel.upper())
    logging.getLogger().setLevel(lvl)

    _cfg = {'format': fmt}
    if logfile:
        _cfg['filename'] = logfile
    logging.basicConfig(**_cfg)

    return logging.getLogger(__name__)

def get_encoding():
    import locale, codecs
    locale.setlocale(locale.LC_ALL, '')
    enc = codecs.lookup(locale.getpreferredencoding()).name

def get_argparser():
    parser = argparse.ArgumentParser(
        description="Simple telnet client.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument('host', action='store',
                        help='Host name or url')
    parser.add_argument('port', nargs='?', default=23, type=int,
                        help='Port number')
    parser.add_argument('--loglevel', dest="loglevel", default='info',
                        help='Logging level')
    parser.add_argument('--logfile', dest='logfile', type=str,
                        help='Logfile path')
    parser.add_argument('--encoding', dest='encoding', type=str,
                        help='Encoding of remote end.')
    parser.add_argument('--force-binary', action='store_true',
                        dest='force_binary')
    return parser

def parse_args():
    args = get_argparser().parse_args()
    if '://' in args.host:
        url = urllib.parse.urlparse(args.host)
        assert url.scheme == 'telnet', url
        args.host = url.hostname
        args.port = url.port or 23

    return {
        'host': args.host,
        'port': args.port,
        'loglevel': args.loglevel,
        'logfile': args.logfile,
        'encoding': args.encoding,
        'force_binary': args.force_binary,
    }

def disp_kv(keyvalues):
    return ' '.join('='.join(map(str, kv)) for kv in keyvalues)

@contextlib.contextmanager
def cbreak(fobj):
    import fcntl, tty, termios, os
    mode = termios.tcgetattr(fobj.fileno())
    tty.setcbreak(fobj.fileno())
    fl = fcntl.fcntl(fobj.fileno(), fcntl.F_GETFL)
    fcntl.fcntl(fobj.fileno(), fcntl.F_SETFL, fl | os.O_NONBLOCK)
    try:
        yield
    finally:
        termios.tcsetattr(fobj.fileno(), termios.TCSAFLUSH, mode)
        fcntl.fcntl(fobj.fileno(), fcntl.F_SETFL, fl)

@asyncio.coroutine
def start_client(loop, log, Client, host, port):
    transport, protocol = yield from loop.create_connection(Client, host, port)
    log.info('Connected.')

    with cbreak(sys.stdin):
        def keyboard_input():
            ucs = sys.stdin.read(1000)

            # transmit
            protocol.writer.write(ucs)

            if not protocol.writer.remote_option.enabled(telnetlib3.ECHO):
                # local echo
                protocol.reader.write(ucs)

        loop.add_reader(sys.stdin.buffer.fileno(), keyboard_input)
        yield from protocol.waiter_closed

def main(host, port, **kwds):
    log = get_logger(kwds.pop('loglevel'), kwds.pop('logfile'))
    loop = asyncio.get_event_loop()
    Client = lambda: telnetlib3.TelnetClient(log=log)
    log.info('Connecting %s %s', host, port)
    loop = asyncio.get_event_loop()
    loop.run_until_complete(start_client(loop, log, Client, host, port))

if __name__ == '__main__':
    exit(main(**parse_args()))
