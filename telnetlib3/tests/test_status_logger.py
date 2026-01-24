# pylint: disable=unused-import
# std imports
import sys
import asyncio

# local
from telnetlib3.server import StatusLogger, parse_server_args
from telnetlib3.telopt import IAC, WONT, TTYPE
from telnetlib3.tests.accessories import bind_host  # pytest fixture
from telnetlib3.tests.accessories import unused_tcp_port  # pytest fixture
from telnetlib3.tests.accessories import (
    create_server,
    asyncio_connection,
)


async def test_rx_bytes_tracking(bind_host, unused_tcp_port):
    """rx_bytes increments when data is received from client."""
    async with create_server(
        host=bind_host,
        port=unused_tcp_port,
        connect_maxwait=0.05,
    ) as server:
        async with asyncio_connection(bind_host, unused_tcp_port) as (reader, writer):
            writer.write(IAC + WONT + TTYPE)
            client = await asyncio.wait_for(server.wait_for_client(), 0.5)

            initial_rx = client.rx_bytes
            assert initial_rx > 0

            writer.write(b"hello")
            await asyncio.sleep(0.05)
            assert client.rx_bytes == initial_rx + 5


async def test_tx_bytes_tracking(bind_host, unused_tcp_port):
    """tx_bytes increments when data is sent to client."""
    async with create_server(
        host=bind_host,
        port=unused_tcp_port,
        connect_maxwait=0.05,
    ) as server:
        async with asyncio_connection(bind_host, unused_tcp_port) as (reader, writer):
            writer.write(IAC + WONT + TTYPE)
            client = await asyncio.wait_for(server.wait_for_client(), 0.5)

            initial_tx = client.tx_bytes
            assert initial_tx > 0

            client.writer.write("hello")
            await client.writer.drain()
            assert client.tx_bytes > initial_tx


async def test_status_logger_get_status(bind_host, unused_tcp_port):
    """StatusLogger._get_status() returns correct client data."""
    async with create_server(
        host=bind_host,
        port=unused_tcp_port,
        connect_maxwait=0.05,
    ) as server:
        status_logger = StatusLogger(server, 60)
        status = status_logger._get_status()
        assert status["count"] == 0
        assert status["clients"] == []

        async with asyncio_connection(bind_host, unused_tcp_port) as (reader, writer):
            writer.write(IAC + WONT + TTYPE)
            await asyncio.wait_for(server.wait_for_client(), 0.5)

            status = status_logger._get_status()
            assert status["count"] == 1
            assert len(status["clients"]) == 1
            assert "ip" in status["clients"][0]
            assert "port" in status["clients"][0]
            assert "rx" in status["clients"][0]
            assert "tx" in status["clients"][0]


async def test_status_logger_status_changed(bind_host, unused_tcp_port):
    """StatusLogger._status_changed() detects changes correctly."""
    async with create_server(
        host=bind_host,
        port=unused_tcp_port,
        connect_maxwait=0.05,
    ) as server:
        status_logger = StatusLogger(server, 60)

        status_empty = status_logger._get_status()
        assert not status_logger._status_changed(status_empty)

        async with asyncio_connection(bind_host, unused_tcp_port) as (reader, writer):
            writer.write(IAC + WONT + TTYPE)
            await asyncio.wait_for(server.wait_for_client(), 0.5)

            status_with_client = status_logger._get_status()
            assert status_logger._status_changed(status_with_client)

            status_logger._last_status = status_with_client
            status_same = status_logger._get_status()
            assert not status_logger._status_changed(status_same)


async def test_status_logger_format_status():
    """StatusLogger._format_status() formats correctly."""

    class MockServer:
        @property
        def clients(self):
            return []

    status_logger = StatusLogger(MockServer(), 60)

    status_empty = {"count": 0, "clients": []}
    assert status_logger._format_status(status_empty) == "0 clients connected"

    status_one = {
        "count": 1,
        "clients": [{"ip": "127.0.0.1", "port": 12345, "rx": 100, "tx": 200}],
    }
    formatted = status_logger._format_status(status_one)
    assert "1 client(s)" in formatted
    assert "127.0.0.1:12345" in formatted
    assert "rx=100" in formatted
    assert "tx=200" in formatted


async def test_status_logger_start_stop(bind_host, unused_tcp_port):
    """StatusLogger.start() and stop() manage task lifecycle."""
    async with create_server(
        host=bind_host,
        port=unused_tcp_port,
        connect_maxwait=0.05,
    ) as server:
        status_logger = StatusLogger(server, 60)
        assert status_logger._task is None

        status_logger.start()
        assert status_logger._task is not None
        assert not status_logger._task.done()

        status_logger.stop()
        await asyncio.sleep(0.01)
        assert status_logger._task.cancelled()


async def test_status_logger_disabled_with_zero_interval(bind_host, unused_tcp_port):
    """StatusLogger with interval=0 does not create task."""
    async with create_server(
        host=bind_host,
        port=unused_tcp_port,
        connect_maxwait=0.05,
    ) as server:
        status_logger = StatusLogger(server, 0)
        status_logger.start()
        assert status_logger._task is None


def test_status_interval_cli_arg_default():
    """--status-interval CLI argument has correct default."""
    old_argv = sys.argv
    try:
        sys.argv = ["test"]
        args = parse_server_args()
        assert args["status_interval"] == 20
    finally:
        sys.argv = old_argv


def test_status_interval_cli_arg_custom():
    """--status-interval CLI argument accepts custom values."""
    old_argv = sys.argv
    try:
        sys.argv = ["test", "--status-interval", "30"]
        args = parse_server_args()
        assert args["status_interval"] == 30
    finally:
        sys.argv = old_argv


def test_status_interval_cli_arg_disabled():
    """--status-interval 0 disables status logging."""
    old_argv = sys.argv
    try:
        sys.argv = ["test", "--status-interval", "0"]
        args = parse_server_args()
        assert args["status_interval"] == 0
    finally:
        sys.argv = old_argv
