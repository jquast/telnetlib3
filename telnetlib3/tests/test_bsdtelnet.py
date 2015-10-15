"""Functionally tests telnetlib3 as a server, using telnet(1)"""
# std imports
import functools
import logging
import asyncio
import io

# local imports
import telnetlib3
from .accessories import (
    # fixtures
    unused_tcp_port,
    event_loop,
    bind_host,
    log,
)

# 3rd party imports
import pexpect
import pytest


@pytest.mark.skipif(pexpect.which('telnet') is None,
                    reason="Requires telnet(1)")
@pytest.mark.asyncio
def test_bsdtelnet(event_loop, bind_host, unused_tcp_port, log):

    event_loop.set_debug(True)

    waiter_server_encoding = asyncio.Future()
    waiter_server_closed = asyncio.Future()

    server = yield from event_loop.create_server(
        protocol_factory=lambda: telnetlib3.TelnetServer(
            waiter_closed=waiter_server_closed,
            waiter_encoding=waiter_server_encoding,
            log=log),
        host=bind_host, port=unused_tcp_port)

    log.info('Listening on {host} {port}'.format(
             host=server.sockets[0].getsockname(),
             port=unused_tcp_port))

    child = pexpect.spawn(
        command='telnet', timeout=1,
        encoding='utf8', echo=False)
    child.delaybeforesend = 0.0

    telnet_options = ('options',)

    for opt in telnet_options:
        yield from child.expect("telnet> ", async=True)
        child.sendline(u"set {0}".format(opt))

    yield from child.expect(u"telnet> ", async=True)

    child.sendline(u"open {0} {1}".format(bind_host, unused_tcp_port))

    yield from child.expect_exact("Escape character is '^]'.", async=True)

    make_waiter_client = functools.partial(
        child.expect_exact, timeout=None, async=True)

    done, pending = yield from asyncio.wait(
        [make_waiter_client('SENT WILL BINARY'),
         waiter_server_encoding],
        loop=event_loop, timeout=1,
        return_when=asyncio.ALL_COMPLETED)

    child.sendline(u"quit")

    done, pending = yield from asyncio.wait(
        [make_waiter_client('Connection closed by foreign host.'),
         waiter_server_closed],
        loop=event_loop, timeout=1,
        return_when=asyncio.ALL_COMPLETED)

    assert not pending
