# std imports
import asyncio

# 3rd party
import pytest

# local
from telnetlib3.server_base import BaseServer


@pytest.mark.asyncio
async def test_connection_lost_closes_transport_despite_set_protocol_error():
    server = BaseServer.__new__(BaseServer)
    server.log = __import__("logging").getLogger("test_server")
    server._tasks = []
    server._waiter_connected = asyncio.get_event_loop().create_future()
    server._extra = {}
    server.shell = None

    closed = []

    class BadTransport:
        def set_protocol(self, proto):
            raise RuntimeError("set_protocol failed")

        def close(self):
            closed.append(True)

        def get_extra_info(self, name, default=None):
            return default

    class FakeReader:
        def feed_eof(self):
            pass

    server._transport = BadTransport()
    server.reader = FakeReader()
    server.connection_lost(None)
    assert len(closed) == 1
    assert server._transport is None


@pytest.mark.asyncio
async def test_connection_lost_remove_done_callback_raises():
    server = BaseServer.__new__(BaseServer)
    server.log = __import__("logging").getLogger("test_server")
    server._tasks = []
    server._extra = {}
    server.shell = None
    server._closing = False

    waiter = asyncio.get_event_loop().create_future()

    class _BadWaiter:
        done = waiter.done
        cancelled = waiter.cancelled
        cancel = waiter.cancel

        def remove_done_callback(self, cb):
            raise ValueError("already removed")

    server._waiter_connected = _BadWaiter()

    class FakeReader:
        def feed_eof(self):
            pass

    class FakeTransport:
        def close(self):
            pass

        def get_extra_info(self, name, default=None):
            return default

    server._transport = FakeTransport()
    server.reader = FakeReader()
    server.connection_lost(None)
    assert server._transport is None


@pytest.mark.asyncio
async def test_data_received_trace_log():
    import logging
    from telnetlib3.accessories import TRACE

    server = BaseServer(encoding=False)

    class FakeTransport:
        def get_extra_info(self, name, default=None):
            return ("127.0.0.1", 9999) if name == "peername" else default

        def write(self, data):
            pass

        def is_closing(self):
            return False

        def close(self):
            pass

    server.connection_made(FakeTransport())
    from telnetlib3 import server_base

    old_level = server_base.logger.level
    server_base.logger.setLevel(TRACE)
    try:
        server.data_received(b"hello")
    finally:
        server_base.logger.setLevel(old_level)


@pytest.mark.asyncio
async def test_data_received_fast_path_no_iac():
    server = BaseServer(encoding=False)

    class FakeTransport:
        def get_extra_info(self, name, default=None):
            return ("127.0.0.1", 9999) if name == "peername" else default

        def write(self, data):
            pass

        def is_closing(self):
            return False

        def close(self):
            pass

    server.connection_made(FakeTransport())
    server.writer.slc_simulated = False
    server.data_received(b"hello world")
    assert len(server.reader._buffer) >= len(b"hello world")
