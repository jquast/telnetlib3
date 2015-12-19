"""Test TSPEED, rfc-1079_."""
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
def test_telnet_server_on_tspeed(event_loop, bind_host, unused_tcp_port, log):
    """Test Server's callback method on_tspeed()."""
    # given
    from telnetlib3.telopt import IAC, WILL, SB, SE, IS, TSPEED
    _waiter = asyncio.Future()
    event_loop.set_debug(True)

    class ServerTestTspeed(telnetlib3.TelnetServer):
        def on_tspeed(self, rx, tx):
            super().on_tspeed(rx, tx)
            _waiter.set_result(self)

    yield from telnetlib3.create_server(
        protocol_factory=ServerTestTspeed,
        host=bind_host, port=unused_tcp_port,
        loop=event_loop, log=log)

    reader, writer = yield from asyncio.open_connection(
        host=bind_host, port=unused_tcp_port, loop=event_loop)

    # exercise,
    writer.write(IAC + WILL + TSPEED)
    writer.write(IAC + SB + TSPEED + IS + b'123,456' + IAC + SE)

    # verify,
    srv_instance = yield from asyncio.wait_for(_waiter, 0.5)
    assert srv_instance.get_extra_info('tspeed') == '123,456'
