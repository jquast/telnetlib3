"""Functionally tests telnetlib3 Client against its own Server."""
# std imports
import asyncio

# local imports
import telnetlib3
from telnetlib3.tests.accessories import (
    server_factory,
    TestTelnetClient,
    unused_tcp_port,
    event_loop,
    bind_host,
    log
)

# 3rd party
import pytest

#
@pytest.mark.asyncio
def test_telnet_coupled(server_factory, event_loop, bind_host, unused_tcp_port, log):
    """Couple TelnetClient to TelnetServer as standard protocol connection."""
    event_loop.set_debug(True)
    waiter_server_connected = asyncio.Future()
    waiter_client_connected = asyncio.Future()
    waiter_client_closed = asyncio.Future()

    server = yield from telnetlib3.create_server(
        host=bind_host, port=unused_tcp_port,
        waiter_connected=waiter_server_connected,
        log=log)

    log.info('Listening on {0}'.format(server.sockets[0].getsockname()))

    _, client = yield from event_loop.create_connection(
        protocol_factory=lambda: TestTelnetClient(
            waiter_connected=waiter_client_connected,
            waiter_closed=waiter_client_closed,
            encoding='utf8', log=log),
        host=bind_host, port=unused_tcp_port)

    wait_for = [waiter_client_connected, waiter_server_connected]
    done, pending = yield from asyncio.wait(wait_for,
                                            loop=event_loop,
                                            timeout=1)

    assert not pending, wait_for

    client.writer.write(u'quit\r')
    server.close()
    wait_for = [waiter_client_closed, server.wait_closed()]
    done, pending = yield from asyncio.wait(wait_for,
                                            loop=event_loop,
                                            timeout=1)

    assert not pending, (done, pending, wait_for)


@pytest.mark.asyncio
def test_extended(server_factory, event_loop, bind_host, unused_tcp_port, log):
    """Using coupled connections, exercise extended telnet options."""
    event_loop.set_debug(True)
    waiter_server_connected = asyncio.Future()
    waiter_client_connected = asyncio.Future()
    waiter_client_closed = asyncio.Future()

    server = yield from telnetlib3.create_server(
        host=bind_host, port=unused_tcp_port,
        waiter_connected=waiter_server_connected,
        log=log)

    log.info('Listening on {0}'.format(server.sockets[0].getsockname()))

    _, client = yield from event_loop.create_connection(
        protocol_factory=lambda: TestTelnetClient(
            waiter_connected=waiter_client_connected,
            waiter_closed=waiter_client_closed,
            encoding='utf8', log=log),
        host=bind_host, port=unused_tcp_port)

    wait_for = [waiter_client_connected, waiter_server_connected]
    done, pending = yield from asyncio.wait(wait_for,
                                            loop=event_loop,
                                            timeout=1)

    assert not pending, (done, pending, wait_for)

    from telnetlib3.telopt import WILL, TSPEED
    client.writer.iac(WILL, TSPEED)
    client.writer.write(u'quit\r')
    wait_for = [waiter_client_closed]
    done, pending = yield from asyncio.wait(wait_for,
                                            loop=event_loop,
                                            timeout=1)
    assert not pending, (done, pending, wait_for)

    server.close()
    yield from server.wait_closed()
