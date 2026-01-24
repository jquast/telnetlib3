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


@contextlib.asynccontextmanager
async def connection_context(reader, writer):
    """Async context manager for connection cleanup."""
    try:
        yield reader, writer
    finally:
        writer.close()
        await writer.wait_closed()


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


class _TrackingProtocol(asyncio.Protocol):
    """Protocol wrapper that tracks transport for cleanup."""

    _transports = None  # Class-level list, set per-server instance

    def connection_made(self, transport):
        if self._transports is not None:
            self._transports.append(transport)
        super().connection_made(transport)


@contextlib.asynccontextmanager
async def asyncio_server(protocol_factory, host, port):
    """Create an asyncio server with automatic cleanup."""
    # Track transports for accepted connections so we can close them
    transports = []

    # Create a subclass that tracks transports for this server instance
    class TrackingProtocol(_TrackingProtocol, protocol_factory):
        _transports = transports

    server = await asyncio.get_event_loop().create_server(TrackingProtocol, host, port)
    try:
        yield server
    finally:
        # Close all accepted connection transports
        for transport in transports:
            if not transport.is_closing():
                transport.close()
        # Give transports time to close
        if transports:
            await asyncio.sleep(0)
        server.close()
        await server.wait_closed()


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
