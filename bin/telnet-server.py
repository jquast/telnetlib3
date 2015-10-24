#!/usr/bin/env python3
"""
A Demonstrating TelnetServer implementation.

This script simply runs the TelnetServer API in its default configuration.
"""
import argparse
import logging
import asyncio

import telnetlib3


def get_parser():
    argp = argparse.ArgumentParser(description="Run simple telnet server.")
    argp.add_argument(
        '--host', action="store", dest='host',
        default='127.0.0.1', help='Host name')
    argp.add_argument(
        '--port', action="store", dest='port',
        default=6023, type=int, help='Port number')
    argp.add_argument(
        '--log-level', action="store", dest="log_level",
        default='info', type=str, help='Logging level (debug,info)')
    return argp


def parse_args():
    args = get_parser().parse_args()
    return {'log_level': args.log_level.upper(),
            'host': args.host, 'port': args.port}


def configure_logger(log_level):
    fmt = '%(asctime)s %(levelname)s %(filename)s:%(lineno)d %(message)s'
    logging.basicConfig(format=fmt)
    log = logging.getLogger(__name__)

    # use generic 'logging' instance, and set the log-level as specified by
    # command-line argument --loglevel
    logging.getLogger().setLevel(getattr(logging, log_level))
    if log_level == 'debug':
        loop = asyncio.get_event_loop()
        loop.set_debug(True)
    return log


def main(host, port, log_level):
    log = configure_logger(log_level)
    loop = asyncio.get_event_loop()
    coro = loop.create_server(
        lambda: telnetlib3.Server(encoding='utf8', log=log), host, port)
    server = loop.run_until_complete(coro)
    log.info('Listening {0}'.format(server.sockets[0].getsockname()))
    loop.run_forever()

if __name__ == '__main__':
    exit(main(**parse_args()))
