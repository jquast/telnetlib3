"""Test XDISPLOC, rfc-1096_."""
# std imports
import asyncio

# local imports
import telnetlib3
import telnetlib3.stream_writer
from telnetlib3.tests.accessories import (
    unused_tcp_port,
    event_loop,
    bind_host,
    log
)

# 3rd party
import pytest


@pytest.mark.asyncio
def test_telnet_server_on_xdisploc(
        event_loop, bind_host, unused_tcp_port, log):
    """Test Server's callback method on_xdisploc()."""
    # given
    from telnetlib3.telopt import (
        IAC, WILL, SB, SE, IS, XDISPLOC
    )
    _waiter = asyncio.Future()
    given_xdisploc = 'alpha:0'

    class ServerTestXdisploc(telnetlib3.TelnetServer):
        def on_xdisploc(self, xdisploc):
            super().on_xdisploc(xdisploc)
            _waiter.set_result(self)

    yield from telnetlib3.create_server(
        protocol_factory=ServerTestXdisploc,
        host=bind_host, port=unused_tcp_port,
        loop=event_loop, log=log)

    reader, writer = yield from asyncio.open_connection(
        host=bind_host, port=unused_tcp_port, loop=event_loop)

    # exercise,
    writer.write(IAC + WILL + XDISPLOC)
    writer.write(IAC + SB + XDISPLOC + IS +
                 given_xdisploc.encode('ascii') +
                 IAC + SE)

    # verify,
    srv_instance = yield from asyncio.wait_for(_waiter, 0.5)
    assert srv_instance.get_extra_info('xdisploc') == 'alpha:0'


@pytest.mark.asyncio
def test_telnet_client_send_xdisploc(event_loop, bind_host, unused_tcp_port, log):
    """Test Client's callback method send_xdisploc()."""
    # given
    _waiter = asyncio.Future()
    given_xdisploc = 'alpha'

    class ServerTestXdisploc(telnetlib3.TelnetServer):
        def on_xdisploc(self, xdisploc):
            super().on_xdisploc(xdisploc)
            _waiter.set_result(xdisploc)

        def begin_advanced_negotiation(self):
            from telnetlib3.telopt import DO, XDISPLOC
            super().begin_advanced_negotiation()
            self.writer.iac(DO, XDISPLOC)

    yield from telnetlib3.create_server(
        protocol_factory=ServerTestXdisploc,
        host=bind_host, port=unused_tcp_port,
        loop=event_loop, log=log)

    reader, writer = yield from telnetlib3.open_connection(
        host=bind_host, port=unused_tcp_port, loop=event_loop,
        xdisploc=given_xdisploc)

    recv_xdisploc = yield from asyncio.wait_for(_waiter, 0.5)
    assert recv_xdisploc == given_xdisploc
