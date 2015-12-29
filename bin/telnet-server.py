#!/usr/bin/env python3
"""Telnet Demonstration Server for the 'telnetlib3' python package.
"""
# std imports
import argparse
import logging
import asyncio

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


def get_argparser():
    parser = argparse.ArgumentParser(
        description="Simple telnet server.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument('host', nargs='?', default='localhost',
                        help='bind address')
    parser.add_argument('port', nargs='?', default=6023, type=int,
                        help='bind port')
    parser.add_argument('--loglevel', default='info',
                        help='level name')
    parser.add_argument('--logfile',
                        help='filepath')
    parser.add_argument('--shell', default='telnetlib3.telnet_server_shell',
                        help='module.function_name')
    parser.add_argument('--encoding', default='utf8',
                        help='encoding name')
    parser.add_argument('--force-binary', action='store_true',
                        help='force binary transmission')
    parser.add_argument('--timeout', default=300, type=int,
                        help='disconnect idle time')
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
        'encoding': args.encoding,
        'force_binary': args.force_binary,
        'timeout': args.timeout,
        'loglevel': args.loglevel,
        'logfile': args.logfile,
        'shell': shell_function
    }


def disp_kv(keyvalues):
    return ' '.join('='.join(map(str, kv)) for kv in keyvalues)


@asyncio.coroutine
def start_server(host, port, log, **kwds):
    server = yield from telnetlib3.create_server(
        host=host, port=port, log=log, **kwds)
    return server


def main(host, port, **kwds):
    loop = asyncio.get_event_loop()
    if kwds.get('loglevel', 'info') == 'debug':
        loop.set_debug(True)
    log = get_logger(kwds.pop('loglevel'), kwds.pop('logfile'))

    log.debug('Config: {0}'.format(disp_kv(kwds.items())))
    server = loop.run_until_complete(start_server(host, port, log, **kwds))

    log.info('Listening on %s %s', *server.sockets[0].getsockname()[:2])
    loop.run_forever()

    return 0


if __name__ == '__main__':
    exit(main(**parse_args()))

# vim: set shiftwidth=4 tabstop=4 softtabstop=4 expandtab textwidth=79 :
