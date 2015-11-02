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
    parser.add_argument('host', nargs='?', default='localhost')
    parser.add_argument('port', nargs='?', default=6023, type=int)
    parser.add_argument('--loglevel', default='info')
    parser.add_argument('--logfile')
    parser.add_argument('--encoding')
    parser.add_argument('--force-binary', action='store_true')
    parser.add_argument('--timeout', default=300, type=int)
    return parser

def parse_args():
    args = get_argparser().parse_args()
    return {
        'host': args.host,
        'port': args.port,
        'encoding': args.encoding,
        'force_binary': args.force_binary,
        'timeout': args.timeout,
        'loglevel': args.loglevel,
        'logfile': args.logfile,
    }

def disp_kv(keyvalues):
    return ' '.join('='.join(map(str, kv)) for kv in keyvalues)

def main(host, port, **kwds):
    log = get_logger(kwds.pop('loglevel'), kwds.pop('logfile'))
    loop = asyncio.get_event_loop()
    server = loop.run_until_complete(telnetlib3.create_server(
        host=host, port=port, log=log, **kwds))

    log.info('Listening on %s %s', *server.sockets[0].getsockname()[:2])
    log.debug('Config: {0}'.format(disp_kv(kwds.items())))
    loop.run_until_complete(server.wait_closed())
    return 0


if __name__ == '__main__':
    exit(main(**parse_args()))

# vim: set shiftwidth=4 tabstop=4 softtabstop=4 expandtab textwidth=79 :
