"""Functionally tests telnetlib3 Client against its own Server."""
# std imports
import asyncio

# local imports
from .accessories import (
    TestTelnetServer,
    TestTelnetClient,
    unused_tcp_port,
    event_loop,
    bind_host,
    log
)

# 3rd party
import pytest


@pytest.mark.asyncio
def test_telnet_coupled(event_loop, bind_host, unused_tcp_port, log):
    """Couple TelnetClient to TelnetServer as standard protocol connection."""
    waiter_server_connected = asyncio.Future()
    waiter_client_connected = asyncio.Future()
    waiter_server_closed = asyncio.Future()
    waiter_client_closed = asyncio.Future()

    server = yield from event_loop.create_server(
        protocol_factory=lambda: TestTelnetServer(
            waiter_connected=waiter_server_connected,
            waiter_closed=waiter_server_closed,
            log=log),
        host=bind_host, port=unused_tcp_port)

    log.info('Listening on {0}'.format(server.sockets[0].getsockname()))

    _, client_protocol = yield from event_loop.create_connection(
        protocol_factory=lambda: TestTelnetClient(
            waiter_connected=waiter_client_connected,
            waiter_closed=waiter_client_closed,
            encoding='utf8', log=log),
        host=bind_host, port=unused_tcp_port)

    done, pending = yield from asyncio.wait(
        [waiter_client_connected, waiter_server_connected],
        loop=event_loop, timeout=1)

    assert not pending, (done,
                         pending,
                         waiter_client_connected,
                         waiter_server_connected)

    client_protocol.writer.write(u'quit\r')

    done, pending = yield from asyncio.wait(
        [waiter_client_closed, waiter_server_closed],
        loop=event_loop, timeout=1)

    assert not pending, (done,
                         pending,
                         waiter_client_connected,
                         waiter_server_connected)
