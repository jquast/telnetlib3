#!/usr/bin/env python3
"""
Telnet Client API for the 'telnetlib3' python package.
"""
# std imports
import asyncio
import argparse

# local imports
from . import accessories
from . import client_base

__all__ = ('TelnetClient', 'connect')


class TelnetClient(client_base.BaseClient):
    # TODO, wire up callbacks for os.environ & etc.
    pass


@asyncio.coroutine
def connect(host=None, port=23, *,
            client_factory=TelnetClient,
            loop=None, family=0, flags=0, local_addr=None,
            log=None, encoding='utf8', encoding_errors='strict',
            force_binary=False,
            # term='unknown', cols=80, rows=25,
            shell=None, waiter_closed=None, waiter_connected=None):

    def connection_factory():
        """Return an SSH client connection handler"""

        return TelnetClient(
            log=log, encoding=encoding, encoding_errors=encoding_errors,
            force_binary=force_binary,
            # term=term, cols=cols, rows=rows,
            shell=shell, waiter_closed=waiter_closed,
            waiter_connected=waiter_connected)

    client_factory = client_factory or TelnetClient
    loop = loop or asyncio.get_event_loop()

    transport, protocol = yield from loop.create_connection(
        connection_factory, host, port,
        family=family, flags=flags, local_addr=local_addr)

    yield from protocol.waiter_connected

    return protocol.reader, protocol.writer


def _get_argument_parser():
    parser = argparse.ArgumentParser(
        description="Telnet protocol client",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument('host', action='store',
                        help='hostname')
    parser.add_argument('port', nargs='?', default=23, type=int,
                        help='port number')
    parser.add_argument('--loglevel', default='info',
                        help='log level')
    parser.add_argument('--logfile',
                        help='filepath')
    parser.add_argument('--shell', default='telnetlib3.telnet_client_shell',
                        help='module.function_name')
    parser.add_argument('--encoding', default='utf8',
                        help='encoding name')
    parser.add_argument('--force-binary', action='store_true',
                        help='force encoding')
    return parser


def _transform_args(args):

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


@asyncio.coroutine
def start_client(host, port, log, **kwds):
    reader, writer = yield from connect(host=host, port=port, log=log, **kwds)
    return reader, writer


def main():
    kwargs = _transform_args(_get_argument_parser().parse_args())
    config_msg = 'Client configuration: ' + accessories.repr_mapping(kwargs)

    loglevel = kwargs.pop('loglevel')
    logfile = kwargs.pop('logfile')
    host = kwargs.pop('host')
    port = kwargs.pop('port')

    log = accessories.make_logger(loglevel=loglevel, logfile=logfile)
    log.debug(config_msg)

    loop = asyncio.get_event_loop()
    if loglevel == 'debug':
        loop.set_debug(True)

    reader, writer = loop.run_until_complete(
        start_client(host, port, log, **kwargs))

    loop.run_until_complete(writer._protocol.waiter_closed)

    return 0

if __name__ == '__main__':
    exit(main())
