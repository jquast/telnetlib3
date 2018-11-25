"""Test TSPEED, rfc-1079_."""
# std imports
import asyncio

# local imports
import telnetlib3
import telnetlib3.stream_writer
from telnetlib3.tests.accessories import (
    unused_tcp_port,
    event_loop,
    bind_host
)

# 3rd party
import pytest


@pytest.mark.asyncio
async def test_telnet_server_on_tspeed(event_loop, bind_host, unused_tcp_port):
    """Test Server's callback method on_tspeed()."""
    # given
    from telnetlib3.telopt import IAC, WILL, SB, SE, IS, TSPEED
    _waiter = asyncio.Future()

    class ServerTestTspeed(telnetlib3.TelnetServer):
        def on_tspeed(self, rx, tx):
            super().on_tspeed(rx, tx)
            _waiter.set_result(self)

    await telnetlib3.create_server(
        protocol_factory=ServerTestTspeed,
        host=bind_host, port=unused_tcp_port,
        loop=event_loop)

    reader, writer = await asyncio.open_connection(
        host=bind_host, port=unused_tcp_port, loop=event_loop)

    # exercise,
    writer.write(IAC + WILL + TSPEED)
    writer.write(IAC + SB + TSPEED + IS + b'123,456' + IAC + SE)

    # verify,
    srv_instance = await asyncio.wait_for(_waiter, 0.5)
    assert srv_instance.get_extra_info('tspeed') == '123,456'


@pytest.mark.asyncio
async def test_telnet_client_send_tspeed(event_loop, bind_host, unused_tcp_port):
    """Test Client's callback method send_tspeed()."""
    # given
    _waiter = asyncio.Future()
    given_rx, given_tx = 1337, 1919

    class ServerTestTspeed(telnetlib3.TelnetServer):
        def on_tspeed(self, rx, tx):
            super().on_tspeed(rx, tx)
            _waiter.set_result((rx, tx))

        def begin_advanced_negotiation(self):
            from telnetlib3.telopt import DO, TSPEED
            super().begin_advanced_negotiation()
            self.writer.iac(DO, TSPEED)

    await telnetlib3.create_server(
        protocol_factory=ServerTestTspeed,
        host=bind_host, port=unused_tcp_port,
        loop=event_loop)

    reader, writer = await telnetlib3.open_connection(
        host=bind_host, port=unused_tcp_port, loop=event_loop,
        tspeed=(given_rx, given_tx), connect_minwait=0.05)

    recv_rx, recv_tx = await asyncio.wait_for(_waiter, 0.5)
    assert recv_rx == given_rx
    assert recv_tx == given_tx
