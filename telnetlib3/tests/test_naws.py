"""Negotiate About Window Size, *NAWS*. rfc-1073_."""
# std imports
import asyncio
import pexpect
import struct

# local imports
import telnetlib3
from telnetlib3.tests.accessories import (
    unused_tcp_port,
    event_loop,
    bind_host
)

# 3rd party
import pytest


@pytest.mark.asyncio
async def test_telnet_server_on_naws(
        event_loop, bind_host, unused_tcp_port):
    """Test Server's Negotiate about window size (NAWS)."""
    # given
    from telnetlib3.telopt import IAC, WILL, SB, SE, NAWS
    _waiter = asyncio.Future()
    given_cols, given_rows = 40, 20

    class ServerTestNaws(telnetlib3.TelnetServer):
        def on_naws(self, width, height):
            super().on_naws(width, height)
            _waiter.set_result(self)

    await telnetlib3.create_server(
        protocol_factory=ServerTestNaws,
        host=bind_host, port=unused_tcp_port,
        loop=event_loop, connect_maxwait=0.05)

    reader, writer = await asyncio.open_connection(
        host=bind_host, port=unused_tcp_port, loop=event_loop)

    # exercise,
    writer.write(IAC + WILL + NAWS)
    writer.write(IAC + SB + NAWS +
                 struct.pack('!HH', given_cols, given_rows) +
                 IAC + SE)

    srv_instance = await asyncio.wait_for(_waiter, 0.5)
    assert srv_instance.get_extra_info('cols') == given_cols
    assert srv_instance.get_extra_info('rows') == given_rows


@pytest.mark.asyncio
async def test_telnet_client_send_naws(event_loop, bind_host, unused_tcp_port):
    """Test Client's NAWS of callback method send_naws()."""
    # given a server
    _waiter = asyncio.Future()
    given_cols, given_rows = 40, 20

    class ServerTestNaws(telnetlib3.TelnetServer):
        def on_naws(self, width, height):
            super().on_naws(width, height)
            _waiter.set_result((height, width))

    await telnetlib3.create_server(
        protocol_factory=ServerTestNaws,
        host=bind_host, port=unused_tcp_port,
        loop=event_loop, connect_maxwait=0.05)

    reader, writer = await telnetlib3.open_connection(
        host=bind_host, port=unused_tcp_port, loop=event_loop,
        cols=given_cols, rows=given_rows, connect_minwait=0.05)

    recv_cols, recv_rows = await asyncio.wait_for(_waiter, 0.5)
    assert recv_cols == given_cols
    assert recv_rows == given_rows


@pytest.mark.asyncio
async def test_telnet_client_send_tty_naws(event_loop, bind_host,
                                     unused_tcp_port):
    """Test Client's NAWS of callback method send_naws()."""
    # given a client,
    _waiter = asyncio.Future()
    given_cols, given_rows = 40, 20
    prog, args = 'telnetlib3-client', [
        bind_host, str(unused_tcp_port), '--loglevel=warning',
        '--connect-minwait=0.005', '--connect-maxwait=0.010']

    # a server,
    class ServerTestNaws(telnetlib3.TelnetServer):
        def on_naws(self, width, height):
            super().on_naws(width, height)
            _waiter.set_result((height, width))
            event_loop.call_soon(self.connection_lost, None)

    await telnetlib3.create_server(
        protocol_factory=ServerTestNaws,
        host=bind_host, port=unused_tcp_port,
        loop=event_loop, connect_maxwait=0.05)

    proc = pexpect.spawn(prog, args, dimensions=(given_rows, given_cols))
    await proc.expect(pexpect.EOF, async_=True, timeout=5)
    assert proc.match == pexpect.EOF

    recv_cols, recv_rows = await asyncio.wait_for(_waiter, 0.5)
    assert recv_cols == given_cols
    assert recv_rows == given_rows


@pytest.mark.asyncio
async def test_telnet_client_send_naws_65534(event_loop, bind_host, unused_tcp_port):
    """Test Client's NAWS boundary values."""
    # given a server
    _waiter = asyncio.Future()
    given_cols, given_rows = 9999999, -999999
    expect_cols, expect_rows = 65535, 0

    class ServerTestNaws(telnetlib3.TelnetServer):
        def on_naws(self, width, height):
            super().on_naws(width, height)
            _waiter.set_result((height, width))

    await telnetlib3.create_server(
        protocol_factory=ServerTestNaws,
        host=bind_host, port=unused_tcp_port,
        loop=event_loop, connect_maxwait=0.05)

    reader, writer = await telnetlib3.open_connection(
        host=bind_host, port=unused_tcp_port, loop=event_loop,
        cols=given_cols, rows=given_rows, connect_minwait=0.05)

    recv_cols, recv_rows = await asyncio.wait_for(_waiter, 0.5)
    assert recv_cols == expect_cols
    assert recv_rows == expect_rows
