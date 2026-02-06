"""Test the server's shell(reader, writer) callback."""

# std imports
import time
import asyncio

# local
from telnetlib3.telopt import DO, IAC, WONT, TTYPE
from telnetlib3.tests.accessories import (  # pylint: disable=unused-import; pylint: disable=unused-import,
    bind_host,
    create_server,
    unused_tcp_port,
    asyncio_connection,
)


async def test_telnet_server_default_timeout(bind_host, unused_tcp_port):
    """Test callback on_timeout() as coroutine of create_server()."""
    given_timeout = 19.29

    async with create_server(
        host=bind_host,
        port=unused_tcp_port,
        timeout=given_timeout,
    ) as server:
        async with asyncio_connection(bind_host, unused_tcp_port) as (reader, writer):
            writer.write(IAC + WONT + TTYPE)

            srv_instance = await asyncio.wait_for(server.wait_for_client(), 0.5)
            assert srv_instance.get_extra_info("timeout") == given_timeout

            srv_instance.set_timeout()
            assert srv_instance.get_extra_info("timeout") == given_timeout


async def test_telnet_server_set_timeout(bind_host, unused_tcp_port):
    """Test callback on_timeout() as coroutine of create_server()."""
    async with create_server(host=bind_host, port=unused_tcp_port) as server:
        async with asyncio_connection(bind_host, unused_tcp_port) as (reader, writer):
            writer.write(IAC + WONT + TTYPE)

            srv_instance = await asyncio.wait_for(server.wait_for_client(), 0.5)
            for value in (19.29, 0):
                srv_instance.set_timeout(value)
                assert srv_instance.get_extra_info("timeout") == value

            srv_instance.set_timeout()
            assert srv_instance.get_extra_info("timeout") == 0


async def test_telnet_server_waitfor_timeout(bind_host, unused_tcp_port):
    """Test callback on_timeout() as coroutine of create_server()."""
    expected_output = IAC + DO + TTYPE + b"\r\nTimeout.\r\n"

    async with create_server(host=bind_host, port=unused_tcp_port, timeout=0.050):
        async with asyncio_connection(bind_host, unused_tcp_port) as (reader, writer):
            writer.write(IAC + WONT + TTYPE)

            stime = time.time()
            output = await asyncio.wait_for(reader.read(), 0.5)
            elapsed = time.time() - stime
            assert 0.040 <= round(elapsed, 3) <= 0.150
            assert output == expected_output


async def test_telnet_server_binary_mode(bind_host, unused_tcp_port):
    """Test callback on_timeout() in BINARY mode when encoding=False is used."""
    expected_output = IAC + DO + TTYPE + b"\r\nTimeout.\r\n"

    async with create_server(host=bind_host, port=unused_tcp_port, timeout=0.150, encoding=False):
        async with asyncio_connection(bind_host, unused_tcp_port) as (reader, writer):
            writer.write(IAC + WONT + TTYPE)

            stime = time.time()
            output = await asyncio.wait_for(reader.read(), 0.5)
            elapsed = time.time() - stime
            assert 0.050 <= round(elapsed, 3) <= 0.200
            assert output == expected_output
