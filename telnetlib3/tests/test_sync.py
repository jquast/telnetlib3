"""Tests for the synchronous (blocking) interface."""

# pylint: disable=unused-import

# std imports
import time
import threading

# 3rd party
import pytest

# local
from telnetlib3.sync import ServerConnection, TelnetConnection, BlockingTelnetServer
from telnetlib3.tests.accessories import bind_host, unused_tcp_port  # pytest fixtures


def test_client_connect_and_close(bind_host, unused_tcp_port):
    """TelnetConnection connects and closes properly."""
    server = BlockingTelnetServer(bind_host, unused_tcp_port)
    server.start()

    conn = TelnetConnection(bind_host, unused_tcp_port, timeout=5)
    conn.connect()
    assert conn._connected.is_set()
    conn.close()

    server.shutdown()


def test_client_context_manager(bind_host, unused_tcp_port):
    """TelnetConnection works as context manager."""
    server = BlockingTelnetServer(bind_host, unused_tcp_port)
    server.start()

    with TelnetConnection(bind_host, unused_tcp_port, timeout=5) as conn:
        assert conn._connected.is_set()

    server.shutdown()


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
    """TelnetConnection readline works correctly."""

    def handler(server_conn):
        server_conn.write("Hello, World!\r\n")
        server_conn.flush(timeout=5)

    server = BlockingTelnetServer(bind_host, unused_tcp_port, handler=handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    server._started.wait(timeout=5)

    with TelnetConnection(bind_host, unused_tcp_port, timeout=5) as conn:
        line = conn.readline(timeout=5)
        assert "Hello, World!" in line

    server.shutdown()


def test_client_read_until(bind_host, unused_tcp_port):
    """TelnetConnection read_until works correctly."""

    def handler(server_conn):
        server_conn.write(">>> ")
        server_conn.flush(timeout=5)

    server = BlockingTelnetServer(bind_host, unused_tcp_port, handler=handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    server._started.wait(timeout=5)

    with TelnetConnection(bind_host, unused_tcp_port, timeout=5) as conn:
        data = conn.read_until(">>> ", timeout=5)
        assert data.endswith(b">>> ")

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


def test_client_already_connected_error(bind_host, unused_tcp_port):
    """Connect fails if already connected."""
    server = BlockingTelnetServer(bind_host, unused_tcp_port)
    server.start()

    with TelnetConnection(bind_host, unused_tcp_port, timeout=5) as conn:
        with pytest.raises(RuntimeError, match="Already connected"):
            conn.connect()

    server.shutdown()


def test_server_start_and_shutdown(bind_host, unused_tcp_port):
    """BlockingTelnetServer starts and shuts down properly."""
    server = BlockingTelnetServer(bind_host, unused_tcp_port)
    server.start()
    assert server._started.is_set()
    server.shutdown()


def test_server_accept(bind_host, unused_tcp_port):
    """BlockingTelnetServer accepts connections."""
    server = BlockingTelnetServer(bind_host, unused_tcp_port)
    server.start()

    def client_thread():
        time.sleep(0.05)
        with TelnetConnection(bind_host, unused_tcp_port, timeout=5):
            time.sleep(0.5)

    thread = threading.Thread(target=client_thread, daemon=True)
    thread.start()

    conn = server.accept(timeout=5)
    assert isinstance(conn, ServerConnection)
    conn.close()
    server.shutdown()


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


def test_server_accept_timeout(bind_host, unused_tcp_port):
    """Accept times out when no client connects."""
    server = BlockingTelnetServer(bind_host, unused_tcp_port)
    server.start()
    with pytest.raises(TimeoutError, match="Accept timed out"):
        server.accept(timeout=0.1)
    server.shutdown()


def test_server_connection_read_write(bind_host, unused_tcp_port):
    """ServerConnection read and write work correctly."""
    server = BlockingTelnetServer(bind_host, unused_tcp_port)
    server.start()

    def client_thread():
        time.sleep(0.05)
        with TelnetConnection(bind_host, unused_tcp_port, timeout=5) as conn:
            conn.write("hello")
            conn.flush()
            assert conn.read(5, timeout=5) == "HELLO"

    thread = threading.Thread(target=client_thread)
    thread.start()

    conn = server.accept(timeout=5)
    data = conn.read(5, timeout=5)
    conn.write(data.upper())
    conn.flush(timeout=5)
    conn.close()

    thread.join(timeout=5)
    server.shutdown()


def test_server_connection_closed_error(bind_host, unused_tcp_port):
    """Operations fail on closed ServerConnection."""
    server = BlockingTelnetServer(bind_host, unused_tcp_port)
    server.start()

    def client_thread():
        time.sleep(0.1)
        with TelnetConnection(bind_host, unused_tcp_port, timeout=5):
            time.sleep(0.1)

    thread = threading.Thread(target=client_thread, daemon=True)
    thread.start()

    conn = server.accept(timeout=5)
    conn.close()

    with pytest.raises(RuntimeError, match="Connection closed"):
        conn.read()
    with pytest.raises(RuntimeError, match="Connection closed"):
        conn.write("test")

    server.shutdown()


def test_server_connection_miniboa_properties(bind_host, unused_tcp_port):
    """ServerConnection has miniboa-compatible properties."""
    server = BlockingTelnetServer(bind_host, unused_tcp_port)
    server.start()

    def client_thread():
        time.sleep(0.05)
        with TelnetConnection(bind_host, unused_tcp_port, timeout=5):
            time.sleep(0.5)

    thread = threading.Thread(target=client_thread, daemon=True)
    thread.start()

    conn = server.accept(timeout=5)

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
    server.shutdown()


def test_server_connection_miniboa_methods(bind_host, unused_tcp_port):
    """ServerConnection has miniboa-compatible methods."""
    server = BlockingTelnetServer(bind_host, unused_tcp_port)
    server.start()

    def client_thread():
        time.sleep(0.05)
        with TelnetConnection(bind_host, unused_tcp_port, timeout=5) as conn:
            conn.write("test\r\n")
            conn.flush()
            time.sleep(0.5)

    thread = threading.Thread(target=client_thread, daemon=True)
    thread.start()

    conn = server.accept(timeout=5)

    assert f"{bind_host}:" in conn.addrport()
    assert conn.idle() >= 0
    assert conn.duration() >= 0

    time.sleep(0.05)
    assert conn.idle() >= 0.05

    conn.readline(timeout=5)
    assert conn.idle() < 0.1

    conn.deactivate()
    assert conn.active is False
    server.shutdown()


def test_server_connection_send_converts_newlines(bind_host, unused_tcp_port):
    """ServerConnection send() converts \\n to \\r\\n like miniboa."""
    server = BlockingTelnetServer(bind_host, unused_tcp_port)
    server.start()

    received = []

    def client_thread():
        time.sleep(0.05)
        with TelnetConnection(bind_host, unused_tcp_port, timeout=5) as conn:
            received.append(conn.read(20, timeout=5))

    thread = threading.Thread(target=client_thread)
    thread.start()

    conn = server.accept(timeout=5)
    conn.send("Hello\nWorld\n")
    conn.flush(timeout=5)
    conn.close()

    thread.join(timeout=5)
    server.shutdown()

    assert len(received) == 1
    assert "\r\n" in received[0]


def test_client_writer_property(bind_host, unused_tcp_port):
    """TelnetConnection.writer exposes underlying TelnetWriter."""
    server = BlockingTelnetServer(bind_host, unused_tcp_port)
    server.start()

    with TelnetConnection(bind_host, unused_tcp_port, timeout=5) as conn:
        writer = conn.writer
        assert writer is not None
        assert hasattr(writer, "mode")
        assert hasattr(writer, "remote_option")
        assert hasattr(writer, "local_option")

    server.shutdown()


def test_server_connection_writer_property(bind_host, unused_tcp_port):
    """ServerConnection.writer exposes underlying TelnetWriter."""
    server = BlockingTelnetServer(bind_host, unused_tcp_port)
    server.start()

    def client_thread():
        time.sleep(0.05)
        with TelnetConnection(bind_host, unused_tcp_port, timeout=5):
            time.sleep(0.5)

    thread = threading.Thread(target=client_thread, daemon=True)
    thread.start()

    conn = server.accept(timeout=5)
    writer = conn.writer
    assert writer is not None
    assert hasattr(writer, "mode")
    assert hasattr(writer, "remote_option")
    assert hasattr(writer, "local_option")

    conn.close()
    server.shutdown()


def test_client_get_extra_info(bind_host, unused_tcp_port):
    """TelnetConnection.get_extra_info returns connection metadata."""
    server = BlockingTelnetServer(bind_host, unused_tcp_port)
    server.start()

    with TelnetConnection(bind_host, unused_tcp_port, timeout=5) as conn:
        peername = conn.get_extra_info("peername")
        assert peername is not None
        assert len(peername) == 2
        assert isinstance(peername[1], int)

        # Non-existent key returns default
        assert conn.get_extra_info("nonexistent") is None
        assert conn.get_extra_info("nonexistent", "default") == "default"

    server.shutdown()


def test_client_operations_after_close_raise(bind_host, unused_tcp_port):
    """Operations fail after connection is closed."""
    server = BlockingTelnetServer(bind_host, unused_tcp_port)
    server.start()

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

    server.shutdown()


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
def test_server_connection_timeout(bind_host, unused_tcp_port, method, args, error_match):
    """ServerConnection methods time out when no data available."""
    server = BlockingTelnetServer(bind_host, unused_tcp_port)
    server.start()

    def client_thread():
        time.sleep(0.05)
        with TelnetConnection(bind_host, unused_tcp_port, timeout=5):
            time.sleep(2)

    thread = threading.Thread(target=client_thread, daemon=True)
    thread.start()

    conn = server.accept(timeout=5)
    with pytest.raises(TimeoutError, match=error_match):
        getattr(conn, method)(*args, timeout=0.1)
    conn.close()
    server.shutdown()


def test_server_connection_read_until_timeout(bind_host, unused_tcp_port):
    """ServerConnection.read_until times out when match not found."""
    server = BlockingTelnetServer(bind_host, unused_tcp_port)
    server.start()

    def client_thread():
        time.sleep(0.05)
        with TelnetConnection(bind_host, unused_tcp_port, timeout=5) as conn:
            conn.write("no match here")
            conn.flush()
            time.sleep(2)

    thread = threading.Thread(target=client_thread, daemon=True)
    thread.start()

    conn = server.accept(timeout=5)
    with pytest.raises(TimeoutError, match="Read until timed out"):
        conn.read_until(">>> ", timeout=0.1)
    conn.close()
    server.shutdown()


def test_server_connection_wait_for_timeout(bind_host, unused_tcp_port):
    """ServerConnection.wait_for times out when conditions not met."""
    server = BlockingTelnetServer(bind_host, unused_tcp_port)
    server.start()

    def client_thread():
        time.sleep(0.05)
        with TelnetConnection(bind_host, unused_tcp_port, timeout=5):
            time.sleep(1.0)

    thread = threading.Thread(target=client_thread, daemon=True)
    thread.start()

    conn = server.accept(timeout=5)
    with pytest.raises(TimeoutError, match="Wait for negotiation timed out"):
        conn.wait_for(remote={"LINEMODE": True}, timeout=0.1)
    conn.close()
    server.shutdown()


@pytest.mark.parametrize(
    "method,args,kwargs",
    [
        pytest.param("wait_for", (), {"remote": {"NAWS": True}}, id="wait_for"),
        pytest.param("read_until", (">>> ",), {}, id="read_until"),
        pytest.param("flush", (), {}, id="flush"),
        pytest.param("readline", (), {}, id="readline"),
    ],
)
def test_server_connection_methods_closed_error(bind_host, unused_tcp_port, method, args, kwargs):
    """ServerConnection methods raise RuntimeError when called after close."""
    server = BlockingTelnetServer(bind_host, unused_tcp_port)
    server.start()

    def client_thread():
        time.sleep(0.05)
        with TelnetConnection(bind_host, unused_tcp_port, timeout=5):
            time.sleep(0.2)

    thread = threading.Thread(target=client_thread, daemon=True)
    thread.start()

    conn = server.accept(timeout=5)
    conn.close()
    with pytest.raises(RuntimeError, match="Connection closed"):
        getattr(conn, method)(*args, **kwargs)
    server.shutdown()


def test_server_already_started_error(bind_host, unused_tcp_port):
    """Server start raises if already started."""
    server = BlockingTelnetServer(bind_host, unused_tcp_port)
    server.start()
    with pytest.raises(RuntimeError, match="Server already started"):
        server.start()
    server.shutdown()


def test_client_read_until_eof(bind_host, unused_tcp_port):
    """TelnetConnection.read_until raises EOFError when connection closes before match."""

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


def test_client_connect_timeout(bind_host, unused_tcp_port):
    """TelnetConnection connect_timeout raises ConnectionError on unreachable port."""
    conn = TelnetConnection(bind_host, unused_tcp_port, timeout=5, connect_timeout=0.1)
    with pytest.raises(ConnectionError):
        conn.connect()


def test_client_connect_timeout_success(bind_host, unused_tcp_port):
    """TelnetConnection connect_timeout does not interfere with successful connection."""
    server = BlockingTelnetServer(bind_host, unused_tcp_port)
    server.start()

    with TelnetConnection(bind_host, unused_tcp_port, timeout=5, connect_timeout=5.0) as conn:
        assert conn._connected.is_set()

    server.shutdown()
