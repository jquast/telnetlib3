#!/usr/bin/env python3
"""
"""
# std imports
import argparse
import logging
import asyncio

# local
import telnetlib3

@asyncio.coroutine
def shell(reader, writer):
    writer.write('Would you like to play a game? ')
    resp = yield from reader.read(1)
    writer.write('{0}\r\nThey say the only way to win is '
                 'to not play at all.\r\n'.format(resp))
    writer.close()


def get_logger(loglevel='info'):
    fmt = '%(asctime)s %(levelname)s %(filename)s:%(lineno)d %(message)s'
    lvl = getattr(logging, loglevel.upper())
    logging.getLogger().setLevel(lvl)
    logging.basicConfig(format=fmt)

    log = logging.getLogger('server')
    return log


def get_argparser():
    parser = argparse.ArgumentParser(
        description="Simple telnet server.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument('--host', default='localhost', required=False)
    parser.add_argument('--port', default=6023, type=int, required=False)
    parser.add_argument('--encoding', dest='encoding', type=str)
    parser.add_argument('--force-binary', action='store_true',
                        dest='force_binary')
    parser.add_argument('--timeout', dest='timeout', default=300, type=int)
    parser.add_argument('--loglevel', dest="loglevel", default='info')
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
    }


def disp_kv(keyvalues):
    return ' '.join('='.join(map(str, kv)) for kv in keyvalues)


def main(host, port, **kwds):
    log = get_logger(loglevel=kwds.pop('loglevel'))
    loop = asyncio.get_event_loop()
    kwds['shell'] = shell
    server = loop.run_until_complete(telnetlib3.create_server(
        host=host, port=port, log=log, **kwds))

    log.info('Listening on %s %s', *server.sockets[0].getsockname()[:2])
    log.debug('Config: {0}'.format(disp_kv(kwds.items())))
    loop.run_until_complete(server.wait_closed())
    return 0

if __name__ == '__main__':
    exit(main(**parse_args()))
