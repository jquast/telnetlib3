"""Negotiate About Window Size, *NAWS*. rfc-1073_."""
# std imports
import asyncio
import struct

# local imports
import telnetlib3
from telnetlib3.tests.accessories import (
    unused_tcp_port,
    event_loop,
    bind_host,
    log
)

# 3rd party
import pytest


@pytest.mark.asyncio
def test_telnet_server_on_naws(
        event_loop, bind_host, unused_tcp_port, log):
    """Test Server's Negotiate about window size (NAWS)."""
    # given
    from telnetlib3.telopt import IAC, WILL, SB, SE, NAWS
    _waiter = asyncio.Future()
    given_cols, given_rows = 40, 20

    class ServerTestNaws(telnetlib3.TelnetServer):
        def on_naws(self, width, height):
            super().on_naws(width, height)
            _waiter.set_result(self)

    yield from telnetlib3.create_server(
        protocol_factory=ServerTestNaws,
        host=bind_host, port=unused_tcp_port,
        loop=event_loop, log=log)

    reader, writer = yield from asyncio.open_connection(
        host=bind_host, port=unused_tcp_port, loop=event_loop)

    # exercise,
    writer.write(IAC + WILL + NAWS)
    writer.write(IAC + SB + NAWS +
                 struct.pack('!HH', given_cols, given_rows) +
                 IAC + SE)

    srv_instance = yield from _waiter
    assert srv_instance.get_extra_info('cols') == given_cols
    assert srv_instance.get_extra_info('rows') == given_rows
