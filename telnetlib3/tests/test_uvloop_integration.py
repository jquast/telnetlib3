"""Minimal uvloop integration test for telnetlib3."""

# std imports
import asyncio

# 3rd party
import pytest

try:
    # 3rd party
    import uvloop

    HAS_UVLOOP = True
except ImportError:
    HAS_UVLOOP = False

# local
import telnetlib3
from telnetlib3.tests.accessories import (  # pylint: disable=unused-import
    bind_host,
    unused_tcp_port,
)

pytestmark = pytest.mark.skipif(not HAS_UVLOOP, reason="uvloop not installed")


@pytest.fixture(scope="module")
def event_loop_policy():
    return uvloop.EventLoopPolicy()


async def minimal_shell(reader, writer):
    """Minimal shell that just sends OK and closes."""
    writer.write("OK\r\n")
    await writer.drain()
    writer.close()


@pytest.mark.asyncio
async def test_uvloop_telnet_integration(bind_host, unused_tcp_port):
    """Test basic telnet client-server connection with uvloop."""
    # Skip if uvloop isn't the active event loop (pytest-asyncio configuration issue)
    loop = asyncio.get_running_loop()
    if "uvloop" not in str(type(loop)).lower():
        pytest.skip(f"uvloop not active (got {type(loop).__name__}), check pytest-asyncio config")

    # Create server
    server = await telnetlib3.create_server(
        host=bind_host, port=unused_tcp_port, shell=minimal_shell
    )

    # Connect client
    reader, writer = await telnetlib3.open_connection(
        host=bind_host,
        port=unused_tcp_port,
        client_factory=telnetlib3.TelnetClient,
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
