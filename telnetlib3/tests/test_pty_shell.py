"""Tests for PTY shell functionality."""

# std imports
import sys
import asyncio

# 3rd party
import pytest

# local
import telnetlib3
from telnetlib3.tests.accessories import (  # pylint: disable=unused-import
    bind_host,
    unused_tcp_port,
)

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
    from telnetlib3.tests.accessories import create_server, open_connection

    _waiter = asyncio.Future()

    class ServerWithWaiter(telnetlib3.TelnetServer):
        def begin_shell(self, result):
            super().begin_shell(result)
            if not _waiter.done():
                _waiter.set_result(self)

    async with create_server(
        protocol_factory=ServerWithWaiter,
        host=bind_host,
        port=unused_tcp_port,
        shell=make_pty_shell("/bin/cat"),
        connect_maxwait=0.5,
    ):
        async with open_connection(
            host=bind_host,
            port=unused_tcp_port,
            cols=80,
            rows=25,
            connect_minwait=0.05,
        ) as (reader, writer):
            await asyncio.wait_for(_waiter, 2.0)
            await asyncio.sleep(0.3)

            writer.write("hello world\n")
            await writer.drain()

            result = await asyncio.wait_for(reader.read(50), 2.0)
            assert "hello world" in result


async def test_pty_shell_term_propagation(bind_host, unused_tcp_port, require_no_capture):
    """Test TERM environment propagation."""
    # local
    from telnetlib3 import make_pty_shell
    from telnetlib3.tests.accessories import create_server, open_connection

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

    async with create_server(
        protocol_factory=ServerWithWaiter,
        host=bind_host,
        port=unused_tcp_port,
        shell=make_pty_shell("/bin/sh", ["-c", "echo $TERM"]),
        connect_maxwait=0.5,
    ):
        async with open_connection(
            host=bind_host,
            port=unused_tcp_port,
            cols=80,
            rows=25,
            term="vt220",
            shell=client_shell,
            connect_minwait=0.05,
        ) as (reader, writer):
            output = await asyncio.wait_for(_output, 5.0)
            assert "vt220" in output or "xterm" in output


async def test_pty_shell_child_exit_closes_connection(
    bind_host, unused_tcp_port, require_no_capture
):
    """Test that child exit closes connection gracefully."""
    # local
    from telnetlib3 import make_pty_shell
    from telnetlib3.tests.accessories import create_server, open_connection

    _waiter = asyncio.Future()

    class ServerWithWaiter(telnetlib3.TelnetServer):
        def begin_shell(self, result):
            super().begin_shell(result)
            if not _waiter.done():
                _waiter.set_result(self)

    async with create_server(
        protocol_factory=ServerWithWaiter,
        host=bind_host,
        port=unused_tcp_port,
        shell=make_pty_shell("/bin/sh", ["-c", "echo done; exit 0"]),
        connect_maxwait=0.5,
    ):
        async with open_connection(
            host=bind_host,
            port=unused_tcp_port,
            cols=80,
            rows=25,
            connect_minwait=0.05,
        ) as (reader, writer):
            await asyncio.wait_for(_waiter, 2.0)
            await asyncio.sleep(0.3)

            result = await asyncio.wait_for(reader.read(100), 3.0)
            assert "done" in result

            remaining = await asyncio.wait_for(reader.read(), 3.0)
            assert not remaining


async def test_pty_shell_client_disconnect_kills_child(
    bind_host, unused_tcp_port, require_no_capture
):
    """Test that client disconnect kills child process."""
    # local
    from telnetlib3 import make_pty_shell
    from telnetlib3.tests.accessories import create_server, open_connection

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

    async with create_server(
        protocol_factory=ServerWithWaiter,
        host=bind_host,
        port=unused_tcp_port,
        shell=make_pty_shell("/bin/cat"),
        connect_maxwait=0.5,
    ):
        async with open_connection(
            host=bind_host,
            port=unused_tcp_port,
            cols=80,
            rows=25,
            connect_minwait=0.05,
        ) as (reader, writer):
            await asyncio.wait_for(_waiter, 2.0)
            await asyncio.sleep(0.3)

        await asyncio.wait_for(_closed, 3.0)


async def test_pty_shell_naws_resize(bind_host, unused_tcp_port, require_no_capture):
    """Test NAWS resize forwarding."""
    # local
    from telnetlib3 import make_pty_shell
    from telnetlib3.tests.accessories import create_server, open_connection

    _waiter = asyncio.Future()

    class ServerWithWaiter(telnetlib3.TelnetServer):
        def begin_shell(self, result):
            super().begin_shell(result)
            if not _waiter.done():
                _waiter.set_result(self)

    async with create_server(
        protocol_factory=ServerWithWaiter,
        host=bind_host,
        port=unused_tcp_port,
        shell=make_pty_shell("/bin/sh", ["-c", "stty size"]),
        connect_maxwait=0.5,
    ):
        async with open_connection(
            host=bind_host,
            port=unused_tcp_port,
            cols=80,
            rows=25,
            connect_minwait=0.05,
        ) as (reader, writer):
            await asyncio.wait_for(_waiter, 2.0)
            await asyncio.sleep(0.3)

            output = await asyncio.wait_for(reader.read(50), 2.0)
            assert "25 80" in output


def test_platform_check_not_windows():
    """Test that platform check raises on Windows."""
    # local
    from telnetlib3.server_pty_shell import _platform_check

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
    from telnetlib3.server_pty_shell import PTYSession

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
    from telnetlib3.server_pty_shell import PTYSession

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


async def test_pty_session_naws_debouncing():
    """Test that rapid NAWS updates are debounced."""
    # std imports
    from unittest.mock import MagicMock, patch

    # local
    from telnetlib3.server_pty_shell import PTYSession

    reader = MagicMock()
    writer = MagicMock()
    protocol = MagicMock()
    writer.protocol = protocol
    writer.get_extra_info = MagicMock(return_value=None)

    session = PTYSession(reader, writer, "/bin/sh", [])
    session.master_fd = 1
    session.child_pid = 12345

    signal_calls = []

    def mock_killpg(pgid, sig):
        signal_calls.append((pgid, sig))

    with (
        patch("os.getpgid", return_value=12345),
        patch("os.killpg", side_effect=mock_killpg),
        patch("fcntl.ioctl"),
    ):
        session._on_naws(25, 80)
        session._on_naws(30, 90)
        session._on_naws(35, 100)

        assert len(signal_calls) == 0

        await asyncio.sleep(0.25)

        assert len(signal_calls) == 1

        signal_calls.clear()
        session._on_naws(40, 120)
        session._on_naws(45, 130)

        assert len(signal_calls) == 0

        await asyncio.sleep(0.25)

        assert len(signal_calls) == 1


async def test_pty_session_naws_debounce_uses_latest_values():
    """Test that debounced NAWS uses the latest values."""
    # std imports
    from unittest.mock import MagicMock, patch, call

    # local
    from telnetlib3.server_pty_shell import PTYSession

    reader = MagicMock()
    writer = MagicMock()
    protocol = MagicMock()
    writer.protocol = protocol
    writer.get_extra_info = MagicMock(return_value=None)

    session = PTYSession(reader, writer, "/bin/sh", [])
    session.master_fd = 1
    session.child_pid = 12345

    ioctl_calls = []

    def mock_ioctl(fd, cmd, data):
        ioctl_calls.append((fd, cmd, data))

    with (
        patch("os.getpgid", return_value=12345),
        patch("os.killpg"),
        patch("fcntl.ioctl", side_effect=mock_ioctl),
    ):
        session._on_naws(25, 80)
        session._on_naws(30, 90)
        session._on_naws(50, 150)

        await asyncio.sleep(0.25)

        assert len(ioctl_calls) == 1
        import struct
        import termios
        expected_winsize = struct.pack("HHHH", 50, 150, 0, 0)
        assert ioctl_calls[0][2] == expected_winsize


async def test_pty_session_naws_cleanup_cancels_pending():
    """Test that cleanup cancels pending NAWS timer."""
    # std imports
    from unittest.mock import MagicMock, patch

    # local
    from telnetlib3.server_pty_shell import PTYSession

    reader = MagicMock()
    writer = MagicMock()
    protocol = MagicMock()
    writer.protocol = protocol
    writer.get_extra_info = MagicMock(return_value=None)

    session = PTYSession(reader, writer, "/bin/sh", [])
    session.master_fd = 1
    session.child_pid = 12345

    signal_calls = []

    def mock_killpg(pgid, sig):
        import signal as signal_mod
        if sig == signal_mod.SIGWINCH:
            signal_calls.append((pgid, sig))

    with (
        patch("os.getpgid", return_value=12345),
        patch("os.killpg", side_effect=mock_killpg),
        patch("os.kill"),
        patch("os.waitpid", return_value=(0, 0)),
        patch("os.close"),
        patch("fcntl.ioctl"),
    ):
        session._on_naws(25, 80)

        session.cleanup()

        await asyncio.sleep(0.25)

        assert len(signal_calls) == 0
