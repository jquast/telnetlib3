"""Test the server's shell(reader, writer) callback."""

# std imports
import time
import asyncio

# 3rd party
import pytest

# local
# local imports
import telnetlib3
import telnetlib3.stream_writer
from telnetlib3.tests.accessories import bind_host, unused_tcp_port


async def test_telnet_server_default_timeout(bind_host, unused_tcp_port):
    """Test callback on_timeout() as coroutine of create_server()."""
    from telnetlib3.telopt import IAC, WONT, TTYPE
    from telnetlib3.tests.accessories import create_server, asyncio_connection

    _waiter = asyncio.Future()
    given_timeout = 19.29

    async with create_server(
        _waiter_connected=_waiter,
        host=bind_host,
        port=unused_tcp_port,
        timeout=given_timeout,
    ):
        async with asyncio_connection(bind_host, unused_tcp_port) as (reader, writer):
            writer.write(IAC + WONT + TTYPE)

            server = await asyncio.wait_for(_waiter, 0.5)
            assert server.get_extra_info("timeout") == given_timeout

            server.set_timeout()
            assert server.get_extra_info("timeout") == given_timeout


async def test_telnet_server_set_timeout(bind_host, unused_tcp_port):
    """Test callback on_timeout() as coroutine of create_server()."""
    from telnetlib3.telopt import IAC, WONT, TTYPE
    from telnetlib3.tests.accessories import create_server, asyncio_connection

    _waiter = asyncio.Future()

    async with create_server(
        _waiter_connected=_waiter, host=bind_host, port=unused_tcp_port
    ):
        async with asyncio_connection(bind_host, unused_tcp_port) as (reader, writer):
            writer.write(IAC + WONT + TTYPE)

            server = await asyncio.wait_for(_waiter, 0.5)
            for value in (19.29, 0):
                server.set_timeout(value)
                assert server.get_extra_info("timeout") == value

            server.set_timeout()
            assert server.get_extra_info("timeout") == 0


async def test_telnet_server_waitfor_timeout(bind_host, unused_tcp_port):
    """Test callback on_timeout() as coroutine of create_server()."""
    from telnetlib3.telopt import DO, IAC, WONT, TTYPE
    from telnetlib3.tests.accessories import create_server, asyncio_connection

    expected_output = IAC + DO + TTYPE + b"\r\nTimeout.\r\n"

    async with create_server(
        host=bind_host, port=unused_tcp_port, timeout=0.050
    ):
        async with asyncio_connection(bind_host, unused_tcp_port) as (reader, writer):
            writer.write(IAC + WONT + TTYPE)

            stime = time.time()
            output = await asyncio.wait_for(reader.read(), 0.5)
            elapsed = time.time() - stime
            assert 0.050 <= round(elapsed, 3) <= 0.100
            assert output == expected_output


async def test_telnet_server_binary_mode(bind_host, unused_tcp_port):
    """Test callback on_timeout() in BINARY mode when encoding=False is used."""
    from telnetlib3.telopt import DO, IAC, WONT, TTYPE
    from telnetlib3.tests.accessories import create_server, asyncio_connection

    expected_output = IAC + DO + TTYPE + b"\r\nTimeout.\r\n"

    async with create_server(
        host=bind_host, port=unused_tcp_port, timeout=0.150, encoding=False
    ):
        async with asyncio_connection(bind_host, unused_tcp_port) as (reader, writer):
            writer.write(IAC + WONT + TTYPE)

            stime = time.time()
            output = await asyncio.wait_for(reader.read(), 0.5)
            elapsed = time.time() - stime
            assert 0.050 <= round(elapsed, 3) <= 0.200
            assert output == expected_output
