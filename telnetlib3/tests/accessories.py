"""Test accessories for telnetlib3 project."""

# std imports
import asyncio
import contextlib

# 3rd party
import pytest
from pytest_asyncio.plugin import unused_tcp_port


@pytest.fixture(scope="module", params=["127.0.0.1"])
def bind_host(request):
    """Localhost bind address."""
    return request.param


@contextlib.asynccontextmanager
async def server_context(server):
    """Async context manager for server cleanup."""
    try:
        yield server
    finally:
        server.close()
        await server.wait_closed()
        # Windows IOCP needs multiple event loop iterations for socket cleanup
        await asyncio.sleep(0)
        await asyncio.sleep(0)


@contextlib.asynccontextmanager
async def connection_context(reader, writer):
    """Async context manager for connection cleanup."""
    try:
        yield reader, writer
    finally:
        writer.close()
        await writer.wait_closed()
        # Windows IOCP needs multiple event loop iterations for socket cleanup
        await asyncio.sleep(0)
        await asyncio.sleep(0)


@contextlib.asynccontextmanager
async def create_server(*args, **kwargs):
    """Create a telnetlib3 server with automatic cleanup."""
    # local - avoid circular import
    # local
    import telnetlib3

    server = await telnetlib3.create_server(*args, **kwargs)
    try:
        yield server
    finally:
        server.close()
        await server.wait_closed()
        # Windows IOCP needs multiple event loop iterations for socket cleanup
        await asyncio.sleep(0)
        await asyncio.sleep(0)


@contextlib.asynccontextmanager
async def open_connection(*args, **kwargs):
    """Open a telnetlib3 connection with automatic cleanup."""
    # local - avoid circular import
    # local
    import telnetlib3

    reader, writer = await telnetlib3.open_connection(*args, **kwargs)
    try:
        yield reader, writer
    finally:
        writer.close()
        await writer.wait_closed()
        # Windows IOCP needs multiple event loop iterations for socket cleanup
        await asyncio.sleep(0)
        await asyncio.sleep(0)


@contextlib.asynccontextmanager
async def asyncio_connection(host, port):
    """Open an asyncio connection with automatic cleanup."""
    reader, writer = await asyncio.open_connection(host=host, port=port)
    try:
        yield reader, writer
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except (BrokenPipeError, ConnectionResetError):
            pass
        # Windows IOCP needs multiple event loop iterations for socket cleanup
        await asyncio.sleep(0)
        await asyncio.sleep(0)


@contextlib.asynccontextmanager
async def asyncio_server(protocol_factory, host, port):
    """Create an asyncio server with automatic cleanup."""
    server = await asyncio.get_event_loop().create_server(protocol_factory, host, port)
    try:
        yield server
    finally:
        server.close()
        await server.wait_closed()
        # Windows IOCP needs multiple event loop iterations for socket cleanup
        await asyncio.sleep(0)
        await asyncio.sleep(0)


__all__ = (
    "asyncio_connection",
    "asyncio_server",
    "bind_host",
    "connection_context",
    "create_server",
    "open_connection",
    "server_context",
    "unused_tcp_port",
)
