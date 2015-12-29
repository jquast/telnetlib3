#!/usr/bin/env python3
"""Telnet Demonstration Client for the 'telnetlib3' python package.
"""
# std imports
import argparse
import logging
import asyncio
import locale
import codecs

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
    locale.setlocale(locale.LC_ALL, '')
    return codecs.lookup(locale.getpreferredencoding()).name


def get_argparser():
    parser = argparse.ArgumentParser(
        description="Simple telnet client.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument('host', action='store',
                        help='Host name')
    parser.add_argument('port', nargs='?', default=23, type=int,
                        help='Port number')
    parser.add_argument('--loglevel', default='info',
                        help='level name')
    parser.add_argument('--logfile',
                        help='filepath')
    parser.add_argument('--shell', default='telnetlib3.telnet_client_shell',
                        help='module.function_name')
    parser.add_argument('--encoding', default='utf8',
                        help='encoding name')
    parser.add_argument('--force-binary', action='store_true',
                        help='force binary transmission')
    return parser


def parse_args():
    args = get_argparser().parse_args()

    # parse --shell='module.function' into function target
    module_name, func_name = args.shell.rsplit('.', 1)
    module = __import__(module_name)
    shell_function = getattr(module, func_name)
    assert callable(shell_function), shell_function

    return {
        'host': args.host,
        'port': args.port,
        'loglevel': args.loglevel,
        'logfile': args.logfile,
        'encoding': args.encoding,
        'shell': shell_function,
        'force_binary': args.force_binary,
    }


def disp_kv(keyvalues):
    return ' '.join('='.join(map(str, kv)) for kv in keyvalues)


@asyncio.coroutine
def start_client(host, port, log, **kwds):
    reader, writer = yield from telnetlib3.connect(
        host=host, port=port, log=log, **kwds)
    return reader, writer


def main(host, port, **kwds):
    loop = asyncio.get_event_loop()
    if kwds.get('loglevel', 'info') == 'debug':
        loop.set_debug(True)
    log = get_logger(kwds.pop('loglevel'), kwds.pop('logfile'))

    log.debug('Config: {0}'.format(disp_kv(kwds.items())))

    reader, writer = loop.run_until_complete(
        start_client(host, port, log, **kwds))

    loop.run_until_complete(writer._protocol.waiter_closed)

    return 0


if __name__ == '__main__':
    exit(main(**parse_args()))
