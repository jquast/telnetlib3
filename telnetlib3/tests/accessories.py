"""Test accessories for telnetlib3 project."""

# std imports
import os
import asyncio
import contextlib

# 3rd party
import pytest
from pytest_asyncio.plugin import unused_tcp_port


def init_subproc_coverage(run_note=None):
    """
    Initialize coverage tracking in a forked subprocess.

    Derived from blessed library's test accessories.

    :param run_note: Optional note (unused, for compatibility).
    :returns: Coverage instance or None.
    """
    try:
        # 3rd party
        import coverage
    except ImportError:
        return None

    coveragerc = os.path.join(os.path.dirname(__file__), os.pardir, os.pardir, "tox.ini")
    cov = coverage.Coverage(config_file=coveragerc)
    cov.start()
    return cov


def make_preexec_coverage():
    """
    Create a preexec_fn for PTY coverage tracking.

    Derived from blessed library's test accessories.

    :returns: Callable that starts and returns coverage in forked child.
    """

    def preexec():
        return init_subproc_coverage()

    return preexec


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

    # Force deterministic client: TelnetTerminalClient reads the real terminal
    # size via TIOCGWINSZ, ignoring cols/rows parameters. Use TelnetClient so
    # tests get consistent behavior regardless of whether stdin is a TTY.
    if "client_factory" not in kwargs:
        from telnetlib3.client import TelnetClient
        kwargs["client_factory"] = TelnetClient

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
    "init_subproc_coverage",
    "make_preexec_coverage",
    "open_connection",
    "server_context",
    "unused_tcp_port",
)
