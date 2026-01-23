"""
Negotiate About Window Size, *NAWS*.

rfc-1073_.
"""

# std imports
import struct
import asyncio
import platform

# 3rd party
import pytest
import pexpect

# local
# local imports
import telnetlib3
from telnetlib3.tests.accessories import (  # pylint: disable=unused-import
    bind_host,
    unused_tcp_port,
)


async def test_telnet_server_on_naws(bind_host, unused_tcp_port):
    """Test Server's Negotiate about window size (NAWS)."""
    # local
    from telnetlib3.telopt import SB, SE, IAC, NAWS, WILL
    from telnetlib3.tests.accessories import create_server, asyncio_connection

    _waiter = asyncio.Future()
    given_cols, given_rows = 40, 20

    class ServerTestNaws(telnetlib3.TelnetServer):
        def on_naws(self, rows, cols):
            super().on_naws(rows, cols)
            _waiter.set_result(self)

    async with create_server(
        protocol_factory=ServerTestNaws,
        host=bind_host,
        port=unused_tcp_port,
        connect_maxwait=0.05,
    ):
        async with asyncio_connection(bind_host, unused_tcp_port) as (reader, writer):
            writer.write(IAC + WILL + NAWS)
            writer.write(IAC + SB + NAWS + struct.pack("!HH", given_cols, given_rows) + IAC + SE)

            srv_instance = await asyncio.wait_for(_waiter, 0.5)
            assert srv_instance.get_extra_info("cols") == given_cols
            assert srv_instance.get_extra_info("rows") == given_rows


async def test_telnet_client_send_naws(bind_host, unused_tcp_port):
    """Test Client's NAWS of callback method send_naws()."""
    # local
    from telnetlib3.tests.accessories import create_server, open_connection

    _waiter = asyncio.Future()
    given_cols, given_rows = 40, 20

    class ServerTestNaws(telnetlib3.TelnetServer):
        def on_naws(self, rows, cols):
            super().on_naws(rows, cols)
            _waiter.set_result((rows, cols))

    async with create_server(
        protocol_factory=ServerTestNaws,
        host=bind_host,
        port=unused_tcp_port,
        connect_maxwait=0.05,
    ):
        async with open_connection(
            host=bind_host,
            port=unused_tcp_port,
            cols=given_cols,
            rows=given_rows,
            connect_minwait=0.05,
        ) as (reader, writer):
            recv_rows, recv_cols = await asyncio.wait_for(_waiter, 0.5)
            assert recv_cols == given_cols
            assert recv_rows == given_rows


@pytest.mark.skipif(
    tuple(map(int, platform.python_version_tuple())) > (3, 10),
    reason="those shabby pexpect maintainers still use @asyncio.coroutine",
)
async def test_telnet_client_send_tty_naws(bind_host, unused_tcp_port):
    """Test Client's NAWS of callback method send_naws()."""
    # local
    from telnetlib3.tests.accessories import create_server

    _waiter = asyncio.Future()
    given_cols, given_rows = 40, 20
    prog, args = "telnetlib3-client", [
        bind_host,
        str(unused_tcp_port),
        "--loglevel=warning",
        "--connect-minwait=0.005",
        "--connect-maxwait=0.010",
    ]

    class ServerTestNaws(telnetlib3.TelnetServer):
        def on_naws(self, rows, cols):
            super().on_naws(rows, cols)
            _waiter.set_result((cols, rows))
            asyncio.get_event_loop().call_soon(self.connection_lost, None)

    async with create_server(
        protocol_factory=ServerTestNaws,
        host=bind_host,
        port=unused_tcp_port,
        connect_maxwait=0.05,
    ):
        proc = pexpect.spawn(prog, args, dimensions=(given_rows, given_cols))
        await proc.expect(pexpect.EOF, async_=True, timeout=5)
        assert proc.match == pexpect.EOF

        recv_cols, recv_rows = await asyncio.wait_for(_waiter, 0.5)
        assert recv_cols == given_cols
        assert recv_rows == given_rows


async def test_telnet_client_send_naws_65534(bind_host, unused_tcp_port):
    """Test Client's NAWS boundary values."""
    # local
    from telnetlib3.tests.accessories import create_server, open_connection

    _waiter = asyncio.Future()
    given_cols, given_rows = 9999999, -999999
    expect_cols, expect_rows = 65535, 0

    class ServerTestNaws(telnetlib3.TelnetServer):
        def on_naws(self, rows, cols):
            super().on_naws(rows, cols)
            _waiter.set_result((cols, rows))

    async with create_server(
        protocol_factory=ServerTestNaws,
        host=bind_host,
        port=unused_tcp_port,
        connect_maxwait=0.05,
    ):
        async with open_connection(
            host=bind_host,
            port=unused_tcp_port,
            cols=given_cols,
            rows=given_rows,
            connect_minwait=0.05,
        ) as (reader, writer):
            recv_cols, recv_rows = await asyncio.wait_for(_waiter, 0.5)
            assert recv_cols == expect_cols
            assert recv_rows == expect_rows
