"""Tests for the synchronous (blocking) interface."""

# std imports
import time
import socket
import threading

# 3rd party
import pytest

# local
from telnetlib3.sync import ServerConnection, TelnetConnection, BlockingTelnetServer
from telnetlib3.tests.accessories import bind_host, unused_tcp_port  # pytest fixtures


@pytest.fixture
def started_server(bind_host, unused_tcp_port):
    """Yield a started BlockingTelnetServer and shut it down on teardown."""
    server = BlockingTelnetServer(bind_host, unused_tcp_port)
    server.start()
    yield server
    server.shutdown()


def test_client_connect_and_close(bind_host, unused_tcp_port, started_server):
    """TelnetConnection connects and closes properly."""
    conn = TelnetConnection(bind_host, unused_tcp_port, timeout=5)
    conn.connect()
    assert conn._connected.is_set()
    conn.close()


def test_client_context_manager(bind_host, unused_tcp_port, started_server):
    """TelnetConnection works as context manager."""
    with TelnetConnection(bind_host, unused_tcp_port, timeout=5) as conn:
        assert conn._connected.is_set()


def test_client_read_write(bind_host, unused_tcp_port):
    """TelnetConnection and ServerConnection read/write work correctly."""

    def handler(server_conn):
        data = server_conn.read(5, timeout=5)
        server_conn.write(data.upper())
        server_conn.flush(timeout=5)

    server = BlockingTelnetServer(bind_host, unused_tcp_port, handler=handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    server._started.wait(timeout=5)

    with TelnetConnection(bind_host, unused_tcp_port, timeout=5) as conn:
        conn.write("hello")
        conn.flush()
        assert conn.read(5, timeout=5) == "HELLO"

    server.shutdown()


def test_client_readline(bind_host, unused_tcp_port):
    def handler(server_conn):
        server_conn.write("Hello, World!\r\n")
        server_conn.flush(timeout=5)

    server = BlockingTelnetServer(bind_host, unused_tcp_port, handler=handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    server._started.wait(timeout=5)

    with TelnetConnection(bind_host, unused_tcp_port, timeout=5) as conn:
        assert "Hello, World!" in conn.readline(timeout=5)

    server.shutdown()


def test_client_read_until(bind_host, unused_tcp_port):
    def handler(server_conn):
        server_conn.write(">>> ")
        server_conn.flush(timeout=5)

    server = BlockingTelnetServer(bind_host, unused_tcp_port, handler=handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    server._started.wait(timeout=5)

    with TelnetConnection(bind_host, unused_tcp_port, timeout=5) as conn:
        assert conn.read_until(">>> ", timeout=5).endswith(b">>> ")

    server.shutdown()


def test_client_read_some_alias(bind_host, unused_tcp_port):
    """TelnetConnection read_some is alias for read."""

    def handler(server_conn):
        server_conn.write("test")
        server_conn.flush(timeout=5)

    server = BlockingTelnetServer(bind_host, unused_tcp_port, handler=handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    server._started.wait(timeout=5)

    with TelnetConnection(bind_host, unused_tcp_port, timeout=5) as conn:
        assert "test" in conn.read_some(timeout=5)

    server.shutdown()


def test_client_not_connected_error():
    """Operations fail when not connected."""
    conn = TelnetConnection("localhost", 12345)
    with pytest.raises(RuntimeError, match="Not connected"):
        conn.read()
    with pytest.raises(RuntimeError, match="Not connected"):
        conn.write("test")


def test_client_already_connected_error(bind_host, unused_tcp_port, started_server):
    """Connect fails if already connected."""
    with TelnetConnection(bind_host, unused_tcp_port, timeout=5) as conn:
        with pytest.raises(RuntimeError, match="Already connected"):
            conn.connect()


def test_server_start_and_shutdown(bind_host, unused_tcp_port):
    """BlockingTelnetServer starts and shuts down properly."""
    server = BlockingTelnetServer(bind_host, unused_tcp_port)
    server.start()
    assert server._started.is_set()
    server.shutdown()


def test_server_accept(bind_host, unused_tcp_port, started_server):
    """BlockingTelnetServer accepts connections."""

    def client_thread():
        time.sleep(0.05)
        with TelnetConnection(bind_host, unused_tcp_port, timeout=5):
            time.sleep(0.5)

    thread = threading.Thread(target=client_thread, daemon=True)
    thread.start()

    conn = started_server.accept(timeout=5)
    assert isinstance(conn, ServerConnection)
    conn.close()


def test_server_serve_forever(bind_host, unused_tcp_port):
    """BlockingTelnetServer serve_forever with handler."""
    received = []

    def handler(conn):
        received.append(conn.read(4, timeout=5))
        conn.write(received[-1].upper())
        conn.flush(timeout=5)

    server = BlockingTelnetServer(bind_host, unused_tcp_port, handler=handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    server._started.wait(timeout=5)

    with TelnetConnection(bind_host, unused_tcp_port, timeout=5) as conn:
        conn.write("test")
        conn.flush()
        assert conn.read(4, timeout=5) == "TEST"

    server.shutdown()
    assert received == ["test"]


def test_server_serve_forever_no_handler_error(bind_host, unused_tcp_port):
    """serve_forever raises without handler."""
    server = BlockingTelnetServer(bind_host, unused_tcp_port)
    with pytest.raises(RuntimeError, match="No handler provided"):
        server.serve_forever()


def test_server_accept_not_started_error(bind_host, unused_tcp_port):
    """Accept raises if server not started."""
    server = BlockingTelnetServer(bind_host, unused_tcp_port)
    with pytest.raises(RuntimeError, match="Server not started"):
        server.accept()


def test_server_accept_timeout(bind_host, unused_tcp_port, started_server):
    """Accept times out when no client connects."""
    with pytest.raises(TimeoutError, match="Accept timed out"):
        started_server.accept(timeout=0.1)


def test_server_connection_read_write(bind_host, unused_tcp_port, started_server):
    """ServerConnection read and write work correctly."""

    def client_thread():
        time.sleep(0.05)
        with TelnetConnection(bind_host, unused_tcp_port, timeout=5) as conn:
            conn.write("hello")
            conn.flush()
            assert conn.read(5, timeout=5) == "HELLO"

    thread = threading.Thread(target=client_thread)
    thread.start()

    conn = started_server.accept(timeout=5)
    conn.write(conn.read(5, timeout=5).upper())
    conn.flush(timeout=5)
    conn.close()

    thread.join(timeout=5)


def test_server_connection_closed_error(bind_host, unused_tcp_port, started_server):
    """Operations fail on closed ServerConnection."""

    def client_thread():
        time.sleep(0.1)
        with TelnetConnection(bind_host, unused_tcp_port, timeout=5):
            time.sleep(0.1)

    thread = threading.Thread(target=client_thread, daemon=True)
    thread.start()

    conn = started_server.accept(timeout=5)
    conn.close()

    with pytest.raises(RuntimeError, match="Connection closed"):
        conn.read()
    with pytest.raises(RuntimeError, match="Connection closed"):
        conn.write("test")


def test_server_connection_miniboa_properties(bind_host, unused_tcp_port, started_server):
    """ServerConnection has miniboa-compatible properties."""

    def client_thread():
        time.sleep(0.05)
        with TelnetConnection(bind_host, unused_tcp_port, timeout=5):
            time.sleep(0.5)

    thread = threading.Thread(target=client_thread, daemon=True)
    thread.start()

    conn = started_server.accept(timeout=5)

    assert conn.active is True
    assert conn.address == bind_host
    assert isinstance(conn.port, int) and conn.port > 0
    assert conn.terminal_type == "unknown"
    assert conn.columns == 80
    assert conn.rows == 25
    assert isinstance(conn.connect_time, float)
    assert isinstance(conn.last_input_time, float)

    conn.close()
    assert conn.active is False


def test_server_connection_miniboa_methods(bind_host, unused_tcp_port, started_server):
    """ServerConnection has miniboa-compatible methods."""

    def client_thread():
        time.sleep(0.05)
        with TelnetConnection(bind_host, unused_tcp_port, timeout=5) as conn:
            conn.write("test\r\n")
            conn.flush()
            time.sleep(0.5)

    thread = threading.Thread(target=client_thread, daemon=True)
    thread.start()

    conn = started_server.accept(timeout=5)

    assert f"{bind_host}:" in conn.addrport()
    assert conn.idle() >= 0
    assert conn.duration() >= 0

    time.sleep(0.05)
    assert conn.idle() >= 0.05

    conn.readline(timeout=5)
    assert conn.idle() < 0.1

    conn.deactivate()
    assert conn.active is False


@pytest.mark.parametrize(
    "send_text,expected_suffix",
    [
        ("Hello\n", "Hello\r\n"),
        ("Hello\r\n", "Hello\r\n"),
        ("A\nB\r\nC\n", "A\r\nB\r\nC\r\n"),
        ("bare text", "bare text"),
    ],
)
def test_server_connection_send_newline_conversion(
    bind_host, unused_tcp_port, send_text, expected_suffix, started_server
):
    """Send() normalizes all newline styles to \\r\\n without doubling."""
    received = []

    def client_thread():
        time.sleep(0.05)
        with TelnetConnection(bind_host, unused_tcp_port, timeout=5) as conn:
            received.append(conn.read(100, timeout=5))

    thread = threading.Thread(target=client_thread)
    thread.start()

    conn = started_server.accept(timeout=5)
    conn.send(send_text)
    conn.flush(timeout=5)
    conn.close()

    thread.join(timeout=5)

    assert len(received) == 1
    assert received[0].endswith(expected_suffix)
    assert "\r\r\n" not in received[0]


def _assert_writer_attrs(writer):
    assert writer is not None
    assert hasattr(writer, "mode")
    assert hasattr(writer, "remote_option")
    assert hasattr(writer, "local_option")


def test_client_writer_property(bind_host, unused_tcp_port, started_server):
    with TelnetConnection(bind_host, unused_tcp_port, timeout=5) as conn:
        _assert_writer_attrs(conn.writer)


def test_server_connection_writer_property(bind_host, unused_tcp_port, started_server):
    def client_thread():
        time.sleep(0.05)
        with TelnetConnection(bind_host, unused_tcp_port, timeout=5):
            time.sleep(0.5)

    thread = threading.Thread(target=client_thread, daemon=True)
    thread.start()

    conn = started_server.accept(timeout=5)
    _assert_writer_attrs(conn.writer)

    conn.close()


def test_client_get_extra_info(bind_host, unused_tcp_port, started_server):
    with TelnetConnection(bind_host, unused_tcp_port, timeout=5) as conn:
        peername = conn.get_extra_info("peername")
        assert peername is not None
        assert len(peername) == 2
        assert isinstance(peername[1], int)
        assert conn.get_extra_info("nonexistent") is None
        assert conn.get_extra_info("nonexistent", "default") == "default"


def test_client_operations_after_close_raise(bind_host, unused_tcp_port, started_server):
    """Operations fail after connection is closed."""
    conn = TelnetConnection(bind_host, unused_tcp_port, timeout=5)
    conn.connect()
    conn.close()

    with pytest.raises(RuntimeError, match="Connection closed"):
        conn.read()
    with pytest.raises(RuntimeError, match="Connection closed"):
        conn.readline()
    with pytest.raises(RuntimeError, match="Connection closed"):
        conn.write("test")
    with pytest.raises(RuntimeError, match="Connection closed"):
        conn.flush()
    with pytest.raises(RuntimeError, match="Connection closed"):
        conn.get_extra_info("peername")
    with pytest.raises(RuntimeError, match="Connection closed"):
        conn.wait_for(remote={"NAWS": True})


def test_client_read_timeout(bind_host, unused_tcp_port):
    """TelnetConnection.read times out when no data available."""

    def handler(server_conn):
        time.sleep(5)

    server = BlockingTelnetServer(bind_host, unused_tcp_port, handler=handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    server._started.wait(timeout=5)

    with TelnetConnection(bind_host, unused_tcp_port, timeout=5) as conn:
        with pytest.raises(TimeoutError, match="Read timed out"):
            conn.read(1, timeout=0.1)

    server.shutdown()


def test_client_readline_timeout(bind_host, unused_tcp_port):
    """TelnetConnection.readline times out when no line available."""

    def handler(server_conn):
        server_conn.write("no newline here")
        server_conn.flush(timeout=5)
        time.sleep(5)

    server = BlockingTelnetServer(bind_host, unused_tcp_port, handler=handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    server._started.wait(timeout=5)

    with TelnetConnection(bind_host, unused_tcp_port, timeout=5) as conn:
        with pytest.raises(TimeoutError, match="Readline timed out"):
            conn.readline(timeout=0.1)

    server.shutdown()


@pytest.mark.parametrize(
    "method,args,error_match",
    [
        pytest.param("read", (1,), "Read timed out", id="read"),
        pytest.param("readline", (), "Readline timed out", id="readline"),
    ],
)
def test_server_connection_timeout(
    bind_host, unused_tcp_port, method, args, error_match, started_server
):
    """ServerConnection methods time out when no data available."""

    def client_thread():
        time.sleep(0.05)
        with TelnetConnection(bind_host, unused_tcp_port, timeout=5):
            time.sleep(2)

    thread = threading.Thread(target=client_thread, daemon=True)
    thread.start()

    conn = started_server.accept(timeout=5)
    with pytest.raises(TimeoutError, match=error_match):
        getattr(conn, method)(*args, timeout=0.1)
    conn.close()


def test_server_connection_read_until_timeout(bind_host, unused_tcp_port, started_server):
    """ServerConnection.read_until times out when match not found."""

    def client_thread():
        time.sleep(0.05)
        with TelnetConnection(bind_host, unused_tcp_port, timeout=5) as conn:
            conn.write("no match here")
            conn.flush()
            time.sleep(2)

    thread = threading.Thread(target=client_thread, daemon=True)
    thread.start()

    conn = started_server.accept(timeout=5)
    with pytest.raises(TimeoutError, match="Read until timed out"):
        conn.read_until(">>> ", timeout=0.1)
    conn.close()


def test_server_connection_wait_for_timeout(bind_host, unused_tcp_port, started_server):
    """ServerConnection.wait_for times out when conditions not met."""

    def client_thread():
        time.sleep(0.05)
        with TelnetConnection(bind_host, unused_tcp_port, timeout=5):
            time.sleep(1.0)

    thread = threading.Thread(target=client_thread, daemon=True)
    thread.start()

    conn = started_server.accept(timeout=5)
    with pytest.raises(TimeoutError, match="Wait for negotiation timed out"):
        conn.wait_for(remote={"LINEMODE": True}, timeout=0.1)
    conn.close()


@pytest.mark.parametrize(
    "method,args,kwargs",
    [
        pytest.param("wait_for", (), {"remote": {"NAWS": True}}, id="wait_for"),
        pytest.param("read_until", (">>> ",), {}, id="read_until"),
        pytest.param("flush", (), {}, id="flush"),
        pytest.param("readline", (), {}, id="readline"),
    ],
)
def test_server_connection_methods_closed_error(
    bind_host, unused_tcp_port, method, args, kwargs, started_server
):
    """ServerConnection methods raise RuntimeError when called after close."""

    def client_thread():
        time.sleep(0.05)
        with TelnetConnection(bind_host, unused_tcp_port, timeout=5):
            time.sleep(0.2)

    thread = threading.Thread(target=client_thread, daemon=True)
    thread.start()

    conn = started_server.accept(timeout=5)
    conn.close()
    with pytest.raises(RuntimeError, match="Connection closed"):
        getattr(conn, method)(*args, **kwargs)


def test_server_already_started_error(bind_host, unused_tcp_port, started_server):
    """Server start raises if already started."""
    with pytest.raises(RuntimeError, match="Server already started"):
        started_server.start()


def test_client_read_until_eof(bind_host, unused_tcp_port):
    """TelnetConnection.read_until raises EOFError on early close."""

    def handler(server_conn):
        server_conn.write("partial data")
        server_conn.flush(timeout=5)
        server_conn.close()

    server = BlockingTelnetServer(bind_host, unused_tcp_port, handler=handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    server._started.wait(timeout=5)

    with TelnetConnection(bind_host, unused_tcp_port, timeout=5) as conn:
        with pytest.raises(EOFError, match="Connection closed before match found"):
            conn.read_until(">>> ", timeout=2)

    server.shutdown()


def test_client_connect_timeout_unreachable(bind_host, unused_tcp_port):
    """TelnetConnection connect_timeout raises ConnectionError."""
    conn = TelnetConnection(bind_host, unused_tcp_port, timeout=5, connect_timeout=0.1)
    with pytest.raises(ConnectionError):
        conn.connect()


def test_client_connect_timeout_success(bind_host, unused_tcp_port, started_server):
    """TelnetConnection connect_timeout does not interfere with success."""
    with TelnetConnection(bind_host, unused_tcp_port, timeout=5, connect_timeout=5.0) as conn:
        assert conn._connected.is_set()


def test_client_double_close(bind_host, unused_tcp_port, started_server):
    """Closing a TelnetConnection twice is safe (idempotent)."""
    conn = TelnetConnection(bind_host, unused_tcp_port, timeout=5)
    conn.connect()
    conn.close()
    conn.close()
    assert conn._closed is True


def test_client_connect_timeout_fires(bind_host, unused_tcp_port):
    """TelnetConnection.connect raises TimeoutError on very short timeout."""
    conn = TelnetConnection(bind_host, unused_tcp_port, timeout=0.001)
    with pytest.raises((TimeoutError, ConnectionError, OSError)):
        conn.connect()
    assert conn._closed is False or conn._thread is None


def test_client_wait_for_timeout(bind_host, unused_tcp_port, started_server):
    """TelnetConnection.wait_for raises TimeoutError."""
    with TelnetConnection(bind_host, unused_tcp_port, timeout=5) as conn:
        with pytest.raises(TimeoutError, match="Wait for negotiation timed out"):
            conn.wait_for(remote={"LINEMODE": True}, timeout=0.1)


def test_server_connection_double_close(bind_host, unused_tcp_port, started_server):
    """Closing a ServerConnection twice is safe (idempotent)."""

    def client_thread():
        time.sleep(0.05)
        with TelnetConnection(bind_host, unused_tcp_port, timeout=5):
            time.sleep(0.5)

    thread = threading.Thread(target=client_thread, daemon=True)
    thread.start()

    conn = started_server.accept(timeout=5)
    conn.close()
    conn.close()
    assert conn._closed is True


def test_client_read_until_timeout(bind_host, unused_tcp_port):
    """TelnetConnection.read_until times out when match not found."""

    def handler(server_conn):
        server_conn.write("no match here")
        server_conn.flush(timeout=5)
        time.sleep(5)

    server = BlockingTelnetServer(bind_host, unused_tcp_port, handler=handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    server._started.wait(timeout=5)

    with TelnetConnection(bind_host, unused_tcp_port, timeout=5) as conn:
        with pytest.raises(TimeoutError, match="Read until timed out"):
            conn.read_until(">>> ", timeout=0.1)

    server.shutdown()


def test_client_flush_timeout(bind_host, unused_tcp_port):
    """TelnetConnection.flush works after writing data."""

    def handler(server_conn):
        server_conn.read(5, timeout=5)

    server = BlockingTelnetServer(bind_host, unused_tcp_port, handler=handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    server._started.wait(timeout=5)

    with TelnetConnection(bind_host, unused_tcp_port, timeout=5) as conn:
        conn.write("hello")
        conn.flush(timeout=5)

    server.shutdown()


def test_client_connect_timeout_ephemeral(bind_host):
    """TelnetConnection raises TimeoutError on connection timeout."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind((bind_host, 0))
    port = sock.getsockname()[1]
    sock.close()

    conn = TelnetConnection(bind_host, port, timeout=0.1)
    with pytest.raises((TimeoutError, OSError)):
        conn.connect()


@pytest.mark.parametrize(
    "method,args",
    [pytest.param("read", (100,), id="read"), pytest.param("readline", (), id="readline")],
)
def test_client_read_eof(bind_host, unused_tcp_port, method, args):
    """TelnetConnection.read/readline raises EOFError when server closes."""
    received = threading.Event()

    def handler(server_conn):
        received.wait(timeout=5)
        server_conn.close()

    server = BlockingTelnetServer(bind_host, unused_tcp_port, handler=handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    server._started.wait(timeout=5)

    with TelnetConnection(bind_host, unused_tcp_port, timeout=5) as conn:
        received.set()
        time.sleep(0.2)
        with pytest.raises(EOFError):
            getattr(conn, method)(*args, timeout=2)

    server.shutdown()


def test_client_cleanup_exception_handling(bind_host, unused_tcp_port, started_server):
    """TelnetConnection._cleanup swallows exceptions."""
    conn = TelnetConnection(bind_host, unused_tcp_port, timeout=5)
    conn.connect()
    conn.close()
    conn._cleanup()


def test_server_connection_read_some(bind_host, unused_tcp_port):
    """ServerConnection.read_some returns available data."""
    result = []

    def handler(server_conn):
        data = server_conn.read_some(timeout=5)
        result.append(data)

    server = BlockingTelnetServer(bind_host, unused_tcp_port, handler=handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    server._started.wait(timeout=5)

    with TelnetConnection(bind_host, unused_tcp_port, timeout=5, encoding=False) as conn:
        conn.write(b"hello")
        conn.flush(timeout=5)
        time.sleep(0.3)

    server.shutdown()
    assert len(result) == 1


def test_server_connection_read_until(bind_host, unused_tcp_port):
    """ServerConnection.read_until returns data up to match."""
    result = []

    def handler(server_conn):
        data = server_conn.read_until(b">>>", timeout=5)
        result.append(data)

    server = BlockingTelnetServer(bind_host, unused_tcp_port, handler=handler, encoding=False)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    server._started.wait(timeout=5)

    with TelnetConnection(bind_host, unused_tcp_port, timeout=5, encoding=False) as conn:
        conn.write(b"hello>>>")
        conn.flush(timeout=5)
        time.sleep(0.3)

    server.shutdown()
    assert len(result) == 1
    assert b">>>" in result[0]


def test_server_connection_send_newline(bind_host, unused_tcp_port):
    """ServerConnection.send normalizes newlines to CRLF."""
    result = []

    def handler(server_conn):
        server_conn.send("hello\nworld")
        time.sleep(0.2)

    server = BlockingTelnetServer(bind_host, unused_tcp_port, handler=handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    server._started.wait(timeout=5)

    with TelnetConnection(bind_host, unused_tcp_port, timeout=5, encoding=False) as conn:
        time.sleep(0.5)
        data = conn.read(-1, timeout=2)
        result.append(data)

    server.shutdown()
    assert len(result) == 1
    assert b"\r\n" in result[0]


def test_server_shutdown_cancels_tasks(bind_host, unused_tcp_port):
    """BlockingTelnetServer.shutdown cancels pending tasks."""

    def handler(server_conn):
        time.sleep(10)

    server = BlockingTelnetServer(bind_host, unused_tcp_port, handler=handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    server._started.wait(timeout=5)

    with TelnetConnection(bind_host, unused_tcp_port, timeout=5, encoding=False) as conn:
        time.sleep(0.1)

    server.shutdown()
