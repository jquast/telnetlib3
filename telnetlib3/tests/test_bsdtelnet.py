"""Functionally tests telnetlib3 as a server, using telnet(1)."""
# std imports
import asyncio

# local
from .accessories import (
    TestTelnetServer,
    unused_tcp_port,
    event_loop,
    bind_host,
    log,
)

# 3rd-party
import pexpect
import pytest


@pytest.mark.asyncio
def test_bsdtelnet(event_loop, bind_host, unused_tcp_port, log):
    """Simple telnet(1) as client (issues 'quit' command)."""
    # if the event loop is not set in debug mode, pexpect blows up !
    # https://github.com/pexpect/pexpect/issues/294
    event_loop.set_debug(True)

    server_connected = asyncio.Future()
    server_closed = asyncio.Future()

    server = yield from event_loop.create_server(
        protocol_factory=lambda: TestTelnetServer(
            waiter_connected=server_connected,
            waiter_closed=server_closed,
            log=log),
        host=bind_host, port=unused_tcp_port)

    log.info('Listening on {0}'.format(server.sockets[0].getsockname()))

    child = pexpect.spawn(command='telnet', encoding='utf8',
                          echo=False, maxread=65534,
                          searchwindowsize=1024, timeout=1)

    child.delaybeforesend = 0.0
    child.delayafterterminate = 0.0
    child.delayafterclose = 0.0

    # set client-side debugging of telnet negotiation
    yield from child.expect("telnet> ", async=True)
    child.sendline(u"set options")

    # and connect,
    yield from child.expect(u"telnet> ", async=True)
    child.sendline(u"open {0} {1}".format(bind_host, unused_tcp_port))

    # await connection banner,
    yield from child.expect_exact("Trying {0}...\r\n".format(bind_host),
                                  timeout=5,
                                  async=True)

    # find the prompt, '%'
    client_connected = child.expect('.* % ', async=True, timeout=None)

    done, pending = yield from asyncio.wait(
        [client_connected, server_connected],
        loop=event_loop, timeout=1,
        return_when=asyncio.ALL_COMPLETED)

    assert not pending, (client_connected, server_connected)

    cancelled = {future for future in done if future.cancelled()}
    log.debug('done {0}'.format(done))
    log.debug('pending {0}'.format(pending))
    log.debug('cancelled {0}'.format(cancelled))

    assert not cancelled, (done, pending, cancelled, child.buffer)

    child.sendline(u"quit")
    telnet_closed = child.expect(pexpect.EOF, async=True, timeout=None)
    done, pending = yield from asyncio.wait(
        [telnet_closed, server_closed],
        loop=event_loop, timeout=1,
        return_when=asyncio.ALL_COMPLETED)

    assert not any(future.cancelled() for future in done), done

    assert not pending, (telnet_closed, server_closed)
