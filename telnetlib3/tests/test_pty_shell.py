"""Tests for PTY shell functionality."""

# std imports
import sys
import asyncio

# 3rd party
import pytest

# local
import telnetlib3
from telnetlib3.tests.accessories import bind_host, unused_tcp_port

pytestmark = [
    pytest.mark.skipif(sys.platform == "win32", reason="PTY not supported on Windows"),
]


@pytest.fixture
def require_no_capture(request):
    """Skip PTY tests when pytest capture is enabled (breaks PTY fork)."""
    capture_option = request.config.getoption("capture")
    if capture_option not in ("no", "tee-sys"):
        pytest.skip("PTY tests require --capture=no or -s flag")


async def test_pty_shell_basic_cat(bind_host, unused_tcp_port, require_no_capture):
    """Test basic echo with /bin/cat."""
    # local
    from telnetlib3 import make_pty_shell

    _waiter = asyncio.Future()

    class ServerWithWaiter(telnetlib3.TelnetServer):
        def begin_shell(self, result):
            super().begin_shell(result)
            if not _waiter.done():
                _waiter.set_result(self)

    await telnetlib3.create_server(
        protocol_factory=ServerWithWaiter,
        host=bind_host,
        port=unused_tcp_port,
        shell=make_pty_shell("/bin/cat"),
        connect_maxwait=0.5,
    )

    reader, writer = await telnetlib3.open_connection(
        host=bind_host,
        port=unused_tcp_port,
        cols=80,
        rows=25,
        connect_minwait=0.05,
    )

    await asyncio.wait_for(_waiter, 2.0)
    await asyncio.sleep(0.3)

    writer.write("hello world\n")
    await writer.drain()

    result = await asyncio.wait_for(reader.read(50), 2.0)
    assert "hello world" in result

    writer.close()


async def test_pty_shell_term_propagation(bind_host, unused_tcp_port, require_no_capture):
    """Test TERM environment propagation."""
    # local
    from telnetlib3 import make_pty_shell

    _waiter = asyncio.Future()
    _output = asyncio.Future()

    class ServerWithWaiter(telnetlib3.TelnetServer):
        def begin_shell(self, result):
            super().begin_shell(result)
            if not _waiter.done():
                _waiter.set_result(self)

    async def client_shell(reader, writer):
        await _waiter
        await asyncio.sleep(0.5)
        output = await asyncio.wait_for(reader.read(100), 2.0)
        _output.set_result(output)

    await telnetlib3.create_server(
        protocol_factory=ServerWithWaiter,
        host=bind_host,
        port=unused_tcp_port,
        shell=make_pty_shell("/bin/sh", ["-c", "echo $TERM"]),
        connect_maxwait=0.5,
    )

    reader, writer = await telnetlib3.open_connection(
        host=bind_host,
        port=unused_tcp_port,
        cols=80,
        rows=25,
        term="vt220",
        shell=client_shell,
        connect_minwait=0.05,
    )

    output = await asyncio.wait_for(_output, 5.0)
    assert "vt220" in output or "xterm" in output

    writer.close()


async def test_pty_shell_child_exit_closes_connection(
    bind_host, unused_tcp_port, require_no_capture
):
    """Test that child exit closes connection gracefully."""
    # local
    from telnetlib3 import make_pty_shell

    _waiter = asyncio.Future()

    class ServerWithWaiter(telnetlib3.TelnetServer):
        def begin_shell(self, result):
            super().begin_shell(result)
            if not _waiter.done():
                _waiter.set_result(self)

    await telnetlib3.create_server(
        protocol_factory=ServerWithWaiter,
        host=bind_host,
        port=unused_tcp_port,
        shell=make_pty_shell("/bin/sh", ["-c", "echo done; exit 0"]),
        connect_maxwait=0.5,
    )

    reader, writer = await telnetlib3.open_connection(
        host=bind_host,
        port=unused_tcp_port,
        cols=80,
        rows=25,
        connect_minwait=0.05,
    )

    await asyncio.wait_for(_waiter, 2.0)
    await asyncio.sleep(0.3)

    result = await asyncio.wait_for(reader.read(100), 3.0)
    assert "done" in result

    remaining = await asyncio.wait_for(reader.read(), 3.0)
    assert remaining == ""


async def test_pty_shell_client_disconnect_kills_child(
    bind_host, unused_tcp_port, require_no_capture
):
    """Test that client disconnect kills child process."""
    # local
    from telnetlib3 import make_pty_shell

    _waiter = asyncio.Future()
    _closed = asyncio.Future()

    class ServerWithWaiter(telnetlib3.TelnetServer):
        def begin_shell(self, result):
            super().begin_shell(result)
            if not _waiter.done():
                _waiter.set_result(self)

        def connection_lost(self, exc):
            super().connection_lost(exc)
            if not _closed.done():
                _closed.set_result(True)

    await telnetlib3.create_server(
        protocol_factory=ServerWithWaiter,
        host=bind_host,
        port=unused_tcp_port,
        shell=make_pty_shell("/bin/cat"),
        connect_maxwait=0.5,
    )

    reader, writer = await telnetlib3.open_connection(
        host=bind_host,
        port=unused_tcp_port,
        cols=80,
        rows=25,
        connect_minwait=0.05,
    )

    await asyncio.wait_for(_waiter, 2.0)
    await asyncio.sleep(0.3)

    writer.close()

    await asyncio.wait_for(_closed, 3.0)


async def test_pty_shell_naws_resize(bind_host, unused_tcp_port, require_no_capture):
    """Test NAWS resize forwarding."""
    # local
    from telnetlib3 import make_pty_shell

    _waiter = asyncio.Future()

    class ServerWithWaiter(telnetlib3.TelnetServer):
        def begin_shell(self, result):
            super().begin_shell(result)
            if not _waiter.done():
                _waiter.set_result(self)

    await telnetlib3.create_server(
        protocol_factory=ServerWithWaiter,
        host=bind_host,
        port=unused_tcp_port,
        shell=make_pty_shell("/bin/sh", ["-c", "stty size"]),
        connect_maxwait=0.5,
    )

    reader, writer = await telnetlib3.open_connection(
        host=bind_host,
        port=unused_tcp_port,
        cols=80,
        rows=25,
        connect_minwait=0.05,
    )

    await asyncio.wait_for(_waiter, 2.0)
    await asyncio.sleep(0.3)

    output = await asyncio.wait_for(reader.read(50), 2.0)
    assert "25 80" in output

    writer.close()


def test_platform_check_not_windows():
    """Test that platform check raises on Windows."""
    # local
    from telnetlib3.pty_shell import _platform_check

    original_platform = sys.platform
    try:
        sys.platform = "win32"
        with pytest.raises(NotImplementedError, match="Windows"):
            _platform_check()
    finally:
        sys.platform = original_platform


def test_make_pty_shell_returns_callable():
    """Test that make_pty_shell returns a callable."""
    # local
    from telnetlib3 import make_pty_shell

    shell = make_pty_shell("/bin/sh")
    assert callable(shell)

    shell_with_args = make_pty_shell("/bin/sh", ["-c", "echo hello"])
    assert callable(shell_with_args)


async def test_pty_session_build_environment():
    """Test PTYSession environment building."""
    # std imports
    from unittest.mock import MagicMock

    # local
    from telnetlib3.pty_shell import PTYSession

    reader = MagicMock()
    writer = MagicMock()
    writer.get_extra_info = MagicMock(
        side_effect=lambda k, d=None: {
            "TERM": "xterm-256color",
            "rows": 30,
            "cols": 100,
            "LANG": "en_US.UTF-8",
            "DISPLAY": ":0",
        }.get(k, d)
    )

    session = PTYSession(reader, writer, "/bin/sh", [])
    env = session._build_environment()

    assert env["TERM"] == "xterm-256color"
    assert env["LINES"] == "30"
    assert env["COLUMNS"] == "100"
    assert env["LANG"] == "en_US.UTF-8"
    assert env["LC_ALL"] == "en_US.UTF-8"
    assert env["DISPLAY"] == ":0"


async def test_pty_session_build_environment_charset_fallback():
    """Test PTYSession environment building with charset fallback."""
    # std imports
    from unittest.mock import MagicMock

    # local
    from telnetlib3.pty_shell import PTYSession

    reader = MagicMock()
    writer = MagicMock()
    writer.get_extra_info = MagicMock(
        side_effect=lambda k, d=None: {
            "TERM": "vt100",
            "rows": 24,
            "cols": 80,
            "charset": "ISO-8859-1",
        }.get(k, d)
    )

    session = PTYSession(reader, writer, "/bin/sh", [])
    env = session._build_environment()

    assert env["TERM"] == "vt100"
    assert env["LANG"] == "en_US.ISO-8859-1"
