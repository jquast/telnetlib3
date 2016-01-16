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
def test_telnet_server_on_naws(
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

    yield from telnetlib3.create_server(
        protocol_factory=ServerTestNaws,
        host=bind_host, port=unused_tcp_port,
        loop=event_loop)

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


@pytest.mark.asyncio
def test_telnet_client_send_naws(event_loop, bind_host, unused_tcp_port):
    """Test Client's NAWS of callback method send_naws()."""
    class ServerTestEnviron(telnetlib3.TelnetServer):
        def on_environ(self, mapping):
            super().on_environ(mapping)
            _waiter.set_result(self)

    # given a server
    from telnetlib3.telopt import IAC, WILL, SB, SE, NAWS
    _waiter = asyncio.Future()
    given_cols, given_rows = 40, 20

    class ServerTestNaws(telnetlib3.TelnetServer):
        def on_naws(self, width, height):
            super().on_naws(width, height)
            _waiter.set_result((height, width))

    yield from telnetlib3.create_server(
        protocol_factory=ServerTestNaws,
        host=bind_host, port=unused_tcp_port,
        loop=event_loop)

    reader, writer = yield from telnetlib3.open_connection(
        host=bind_host, port=unused_tcp_port, loop=event_loop,
        cols=given_cols, rows=given_rows)

    recv_cols, recv_rows = yield from _waiter
    assert recv_cols == given_cols
    assert recv_rows == given_rows


@pytest.mark.asyncio
def test_telnet_client_send_tty_naws(event_loop, bind_host,
                                     unused_tcp_port):
    """Test Client's NAWS of callback method send_naws()."""
    class ServerTestEnviron(telnetlib3.TelnetServer):
        def on_environ(self, mapping):
            super().on_environ(mapping)
            _waiter.set_result(self)

    # given a server
    from telnetlib3.telopt import IAC, WILL, SB, SE, NAWS
    _waiter = asyncio.Future()
    given_cols, given_rows = 40, 20
    prog, args = 'telnetlib3-client', [
        bind_host, str(unused_tcp_port), '--loglevel=warn',]

    class ServerTestNaws(telnetlib3.TelnetServer):
        def on_naws(self, width, height):
            super().on_naws(width, height)
            _waiter.set_result((height, width))
            event_loop.call_soon(self.connection_lost, None)

    yield from telnetlib3.create_server(
        protocol_factory=ServerTestNaws,
        host=bind_host, port=unused_tcp_port,
        loop=event_loop)

    proc = pexpect.spawn(prog, args, dimensions=(given_rows, given_cols))
    yield from proc.expect(pexpect.EOF, async=True, timeout=5)
    assert proc.match == pexpect.EOF

    recv_cols, recv_rows = yield from _waiter
    assert recv_cols == given_cols
    assert recv_rows == given_rows
