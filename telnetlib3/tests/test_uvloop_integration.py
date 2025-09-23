"""Minimal uvloop integration test for telnetlib3."""

import asyncio
import pytest
import uvloop
import telnetlib3
from telnetlib3.tests.accessories import unused_tcp_port, bind_host


async def minimal_shell(reader, writer):
    """Minimal shell that just sends OK and closes."""
    writer.write("OK\r\n")
    await writer.drain()
    writer.close()


@pytest.mark.asyncio
async def test_uvloop_telnet_integration(bind_host, unused_tcp_port):
    """Test basic telnet client-server connection with uvloop."""
    # Verify we're running with uvloop
    loop = asyncio.get_running_loop()
    assert "uvloop" in str(type(loop)).lower(), f"Expected uvloop, got {type(loop)}"

    # Create server
    server = await telnetlib3.create_server(
        host=bind_host, port=unused_tcp_port, shell=minimal_shell
    )

    # Connect client
    reader, writer = await telnetlib3.open_connection(
        host=bind_host, port=unused_tcp_port
    )

    # Read response and verify connection works
    data = await reader.read(1024)
    response = data if isinstance(data, str) else data.decode("utf-8", errors="ignore")
    assert "OK" in response

    # Clean up
    writer.close()
    await writer.wait_closed()
    server.close()
    await server.wait_closed()
