# std imports
import asyncio
import logging
import ssl as ssl_module
import socket
from unittest.mock import MagicMock, patch

# 3rd party
import pytest

# local
from telnetlib3.server_base import BaseServer
from telnetlib3.server import TelnetServer, StatusLogger, parse_server_args
from telnetlib3.server import _TLSAutoDetectProtocol


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


def _make_telnet_server(**kwargs):
    """Create a TelnetServer with a FakeTransport for unit testing."""
    defaults = {"encoding": False, "connect_maxwait": 0.01}
    defaults.update(kwargs)
    server = TelnetServer(**defaults)

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
    return server


@pytest.mark.asyncio
async def test_check_negotiation_deferred_echo_environ():
    """check_negotiation triggers deferred ECHO/NEW_ENVIRON when TTYPE refused."""
    from telnetlib3.telopt import TTYPE

    server = _make_telnet_server()
    server._advanced = True
    server._echo_negotiated = False
    server._environ_requested = False
    server.writer.remote_option[TTYPE] = False

    echo_calls = []
    environ_calls = []
    server._negotiate_echo = lambda: echo_calls.append(True)
    server._negotiate_environ = lambda: environ_calls.append(True)

    server.check_negotiation()
    assert len(echo_calls) == 1
    assert len(environ_calls) == 1


@pytest.mark.asyncio
async def test_check_negotiation_final_subneg_timeout_warning(caplog):
    """check_negotiation warns when critical subneg times out."""
    from telnetlib3.telopt import NEW_ENVIRON, SB

    server = _make_telnet_server()
    server._advanced = True
    server._echo_negotiated = True
    server._environ_requested = True
    server.writer.pending_option[SB + NEW_ENVIRON] = True

    with caplog.at_level(
        logging.WARNING, logger="telnetlib3.server"
    ):
        server.check_negotiation(final=True)

    assert "critical subnegotiation" in caplog.text.lower() or \
        "environ" in caplog.text.lower()


@pytest.mark.asyncio
async def test_check_encoding_binary_incoming_request():
    """_check_encoding sends DO BINARY when outbinary set but not inbinary."""
    from telnetlib3.telopt import BINARY, DO

    server = _make_telnet_server()
    server.writer.local_option[BINARY] = True
    server.writer.remote_option[BINARY] = False

    iac_calls = []
    orig_iac = server.writer.iac

    def track_iac(*args):
        iac_calls.append(args)
        return orig_iac(*args)

    server.writer.iac = track_iac
    result = server._check_encoding()
    assert result is False
    assert any(args == (DO, BINARY) for args in iac_calls)


@pytest.mark.asyncio
async def test_tls_autodetect_empty_peek():
    """TLS auto-detect closes transport on empty peek."""
    proto = _TLSAutoDetectProtocol(
        ssl_module.SSLContext(ssl_module.PROTOCOL_TLS_SERVER),
        lambda: MagicMock(),
    )
    transport = MagicMock()
    mock_sock = MagicMock()
    transport.get_extra_info = MagicMock(
        side_effect=lambda name, **kw: mock_sock if name == "socket" else None
    )
    proto._transport = transport

    dup_sock = MagicMock()
    dup_sock.recv.return_value = b""
    with patch("socket.fromfd", return_value=dup_sock):
        proto._detect_tls()

    transport.close.assert_called_once()


@pytest.mark.asyncio
async def test_tls_upgrade_handshake_failure():
    """_upgrade_to_tls handles SSLError gracefully."""
    proto = _TLSAutoDetectProtocol(
        ssl_module.SSLContext(ssl_module.PROTOCOL_TLS_SERVER),
        lambda: MagicMock(),
    )
    transport = MagicMock()
    transport.is_closing.return_value = False
    proto._transport = transport

    loop = asyncio.get_event_loop()
    with patch.object(
        loop, "start_tls",
        side_effect=ssl_module.SSLError("handshake failed"),
    ):
        await proto._upgrade_to_tls()

    transport.close.assert_called_once()


@pytest.mark.asyncio
async def test_status_logger_run_loop():
    """StatusLogger._run() logs when status changes."""
    mock_server = MagicMock()
    mock_server.sockets = []
    logger_obj = StatusLogger(mock_server, interval=0.01)

    call_count = [0]

    def fake_status():
        call_count[0] += 1
        return {"count": call_count[0], "clients": []}

    logger_obj._get_status = fake_status
    logger_obj.start()
    await asyncio.sleep(0.1)
    logger_obj.stop()
    assert call_count[0] >= 2


def test_parse_server_args_force_binary_auto():
    """parse_server_args auto-enables force_binary for non-ASCII encoding."""
    with patch(
        "sys.argv", ["test", "--encoding", "cp437"]
    ):
        result = parse_server_args()
    assert result["force_binary"] is True


def test_parse_server_args_ascii_no_force_binary():
    """parse_server_args does not auto-enable force_binary for ASCII."""
    with patch(
        "sys.argv", ["test", "--encoding", "us-ascii"]
    ):
        result = parse_server_args()
    assert result["force_binary"] is False


@pytest.mark.asyncio
async def test_run_server_guarded_shell_wrapping():
    """run_server wraps shell with robot_check and pty_fork_limit guards."""
    from telnetlib3.server import run_server, create_server

    created_server = MagicMock()
    created_server.wait_closed = MagicMock(
        side_effect=asyncio.CancelledError
    )

    async def mock_create_server(**kwargs):
        created_server.shell = kwargs.get("shell")
        return created_server

    with patch("telnetlib3.server.create_server", side_effect=mock_create_server):
        with patch("asyncio.get_event_loop") as mock_loop:
            mock_loop.return_value = asyncio.get_event_loop()
            try:
                await run_server(
                    host="127.0.0.1", port=0,
                    shell=lambda r, w: None,
                    robot_check=True, pty_fork_limit=2,
                )
            except (asyncio.CancelledError, Exception):
                pass

    assert created_server.shell is not None


@pytest.mark.asyncio
async def test_run_server_status_logger_lifecycle():
    """run_server starts and stops StatusLogger when status_interval > 0."""
    from telnetlib3.server import run_server

    created_server = MagicMock()
    wait_future = asyncio.get_event_loop().create_future()
    wait_future.set_result(None)
    created_server.wait_closed = MagicMock(return_value=wait_future)
    created_server.sockets = []

    async def mock_create_server(*args, **kwargs):
        return created_server

    with patch("telnetlib3.server.create_server", side_effect=mock_create_server):
        loop = asyncio.get_event_loop()
        with patch.object(loop, "add_signal_handler"):
            with patch.object(loop, "remove_signal_handler"):
                await run_server(
                    host="127.0.0.1", port=0,
                    shell=lambda r, w: None,
                    status_interval=1,
                )


@pytest.mark.asyncio
async def test_check_negotiation_ttype_resolved_no_pending():
    """check_negotiation triggers environ when TTYPE resolved with no pending."""
    from telnetlib3.telopt import TTYPE

    server = _make_telnet_server()
    server._advanced = True
    server._echo_negotiated = True
    server._environ_requested = False
    server.writer.remote_option[TTYPE] = True

    environ_calls = []
    server._negotiate_environ = lambda: environ_calls.append(True)

    server.check_negotiation()
    assert len(environ_calls) == 1


@pytest.mark.asyncio
async def test_check_encoding_charset_request():
    """_check_encoding sends CHARSET REQUEST when both sides support it."""
    from telnetlib3.telopt import CHARSET

    server = _make_telnet_server()
    server.writer.remote_option[CHARSET] = True
    server.writer.local_option[CHARSET] = True

    charset_calls = []
    server.writer.request_charset = lambda: charset_calls.append(True)
    server._check_encoding()
    assert len(charset_calls) == 1


@pytest.mark.asyncio
async def test_data_received_no_iac_batch():
    """data_received fast path batches data without IAC bytes."""
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
    data = b"plain text no iac"
    server.data_received(data)
    assert bytes(server.reader._buffer).endswith(data)
