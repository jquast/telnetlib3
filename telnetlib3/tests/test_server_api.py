# std imports
import asyncio

# local
from telnetlib3.telopt import IAC, WILL, WONT, TTYPE, BINARY
from telnetlib3.tests.accessories import bind_host  # pytest fixture
from telnetlib3.tests.accessories import unused_tcp_port  # pytest fixture
from telnetlib3.tests.accessories import create_server, asyncio_connection


async def test_server_wait_for_client(bind_host, unused_tcp_port):
    """Test Server.wait_for_client() returns protocol after negotiation."""
    async with create_server(host=bind_host, port=unused_tcp_port, connect_maxwait=0.05) as server:
        async with asyncio_connection(bind_host, unused_tcp_port) as (reader, writer):
            writer.write(IAC + WONT + TTYPE)
            client = await asyncio.wait_for(server.wait_for_client(), 0.5)
            assert client is not None
            assert hasattr(client, "writer")
            assert hasattr(client, "reader")


async def test_server_clients_list(bind_host, unused_tcp_port):
    """Test Server.clients property returns list of connected protocols."""
    async with create_server(host=bind_host, port=unused_tcp_port, connect_maxwait=0.05) as server:
        assert server.clients == []

        async with asyncio_connection(bind_host, unused_tcp_port) as (reader, writer):
            writer.write(IAC + WONT + TTYPE)
            client = await asyncio.wait_for(server.wait_for_client(), 0.5)
            assert len(server.clients) == 1
            # client is a weakref proxy, clients[0] is actual protocol
            assert server.clients[0] == client


async def test_server_client_disconnect_cleanup(bind_host, unused_tcp_port):
    """Test that clients are removed from list on disconnect."""
    async with create_server(host=bind_host, port=unused_tcp_port, connect_maxwait=0.05) as server:
        async with asyncio_connection(bind_host, unused_tcp_port) as (reader, writer):
            writer.write(IAC + WONT + TTYPE)
            client = await asyncio.wait_for(server.wait_for_client(), 0.5)
            assert len(server.clients) == 1

        # Connection closed, wait a moment for cleanup
        await asyncio.sleep(0.05)
        assert len(server.clients) == 0


async def test_server_is_serving(bind_host, unused_tcp_port):
    """Test Server.is_serving() delegates to underlying server."""
    async with create_server(host=bind_host, port=unused_tcp_port) as server:
        assert server.is_serving() is True

    # After context exit, server is closed
    assert server.is_serving() is False


async def test_server_sockets(bind_host, unused_tcp_port):
    """Test Server.sockets property returns socket list."""
    async with create_server(host=bind_host, port=unused_tcp_port) as server:
        assert server.sockets is not None
        assert len(server.sockets) > 0


async def test_server_with_wait_for(bind_host, unused_tcp_port):
    """Test integration of Server.wait_for_client() with writer.wait_for()."""
    async with create_server(host=bind_host, port=unused_tcp_port, connect_maxwait=0.05) as server:
        async with asyncio_connection(bind_host, unused_tcp_port) as (reader, writer):
            # Send WILL BINARY and WONT TTYPE
            writer.write(IAC + WILL + BINARY)
            writer.write(IAC + WONT + TTYPE)

            client = await asyncio.wait_for(server.wait_for_client(), 0.5)

            # Use wait_for to check specific negotiation state
            await asyncio.wait_for(client.writer.wait_for(remote={"BINARY": True}), 0.5)
            assert client.writer.remote_option[BINARY] is True


async def test_server_multiple_sequential_clients(bind_host, unused_tcp_port):
    """Test wait_for_client() works for multiple sequential connections."""
    async with create_server(host=bind_host, port=unused_tcp_port, connect_maxwait=0.05) as server:
        # First client
        async with asyncio_connection(bind_host, unused_tcp_port) as (reader1, writer1):
            writer1.write(IAC + WONT + TTYPE)
            client1 = await asyncio.wait_for(server.wait_for_client(), 0.5)
            assert client1 is not None

        # Wait for first client to disconnect
        await asyncio.sleep(0.05)

        # Second client
        async with asyncio_connection(bind_host, unused_tcp_port) as (reader2, writer2):
            writer2.write(IAC + WONT + TTYPE)
            client2 = await asyncio.wait_for(server.wait_for_client(), 0.5)
            assert client2 is not None
            assert client2 is not client1
