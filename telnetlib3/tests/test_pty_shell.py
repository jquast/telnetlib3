"""Tests for PTY shell functionality."""

# std imports
import os
import sys
import asyncio

# 3rd party
import pytest

# local
import telnetlib3
from telnetlib3.tests.accessories import (  # pylint: disable=unused-import
    bind_host,
    make_preexec_coverage,
    unused_tcp_port,
)

pytestmark = [
    pytest.mark.skipif(sys.platform == "win32", reason="PTY not supported on Windows"),
]

PTY_HELPER = os.path.join(os.path.dirname(__file__), "pty_helper.py")

# Python 3.15+ emits DeprecationWarning when forkpty() is called in a multi-threaded
# process. The warning is valid (forking in threaded processes can deadlock), but
# pytest itself uses threads, so we can't avoid it. The PTY code still works fine -
# we just suppress the warning in tests rather than skipping them entirely.
_ignore_forkpty_deprecation = pytest.mark.filterwarnings(
    "ignore:This process.*is multi-threaded, use of forkpty:DeprecationWarning"
)


@pytest.fixture
def require_no_capture(request):
    """Skip PTY tests when pytest capture is enabled (breaks PTY fork)."""
    capture_option = request.config.getoption("capture")
    if capture_option not in ("no", "tee-sys"):
        pytest.skip("PTY tests require --capture=no or -s flag")


@_ignore_forkpty_deprecation
async def test_pty_shell_basic_cat(bind_host, unused_tcp_port, require_no_capture):
    """Test basic echo with cat mode."""
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
        shell=make_pty_shell(sys.executable, [PTY_HELPER, "cat"],
                            preexec_fn=make_preexec_coverage()),
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


@_ignore_forkpty_deprecation
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
        shell=make_pty_shell(sys.executable, [PTY_HELPER, "env", "TERM"],
                            preexec_fn=make_preexec_coverage()),
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


@_ignore_forkpty_deprecation
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
        shell=make_pty_shell(sys.executable, [PTY_HELPER, "exit_code", "0"],
                            preexec_fn=make_preexec_coverage()),
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


@_ignore_forkpty_deprecation
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
        shell=make_pty_shell(sys.executable, [PTY_HELPER, "cat"],
                            preexec_fn=make_preexec_coverage()),
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


@_ignore_forkpty_deprecation
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
        shell=make_pty_shell(sys.executable, [PTY_HELPER, "stty_size"],
                            preexec_fn=make_preexec_coverage()),
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

    shell = make_pty_shell(sys.executable)
    assert callable(shell)

    shell_with_args = make_pty_shell(sys.executable, [PTY_HELPER, "echo", "hello"])
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

    with patch("os.getpgid", return_value=12345), \
         patch("os.killpg", side_effect=mock_killpg), \
         patch("fcntl.ioctl"):
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
    from unittest.mock import MagicMock, call, patch

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

    with patch("os.getpgid", return_value=12345), \
         patch("os.killpg"), \
         patch("fcntl.ioctl", side_effect=mock_ioctl):
        session._on_naws(25, 80)
        session._on_naws(30, 90)
        session._on_naws(50, 150)

        await asyncio.sleep(0.25)

        assert len(ioctl_calls) == 1
        # std imports
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
        # std imports
        import signal as signal_mod

        if sig == signal_mod.SIGWINCH:
            signal_calls.append((pgid, sig))

    with patch("os.getpgid", return_value=12345), \
         patch("os.killpg", side_effect=mock_killpg), \
         patch("os.kill"), \
         patch("os.waitpid", return_value=(0, 0)), \
         patch("os.close"), \
         patch("fcntl.ioctl"):
        session._on_naws(25, 80)

        session.cleanup()

        await asyncio.sleep(0.25)

        assert len(signal_calls) == 0


async def test_pty_session_write_to_telnet_line_buffering():
    """Test that _write_to_telnet buffers until newline."""
    # std imports
    from unittest.mock import MagicMock

    # local
    from telnetlib3.server_pty_shell import PTYSession

    reader = MagicMock()
    writer = MagicMock()
    written = []
    writer.write = lambda data: written.append(data)
    writer.get_extra_info = MagicMock(return_value="utf-8")

    session = PTYSession(reader, writer, "/bin/sh", [])

    session._write_to_telnet(b"hello")
    assert len(written) == 0
    assert session._output_buffer == b"hello"

    session._write_to_telnet(b" world\nmore")
    assert len(written) == 1
    assert "hello world\n" in written[0]
    assert session._output_buffer == b"more"


async def test_pty_session_write_to_telnet_bsu_esu_sequences():
    """Test synchronized update handling with BSU/ESU sequences."""
    # std imports
    from unittest.mock import MagicMock

    # local
    from telnetlib3.server_pty_shell import PTYSession, _BSU, _ESU

    reader = MagicMock()
    writer = MagicMock()
    written = []
    writer.write = lambda data: written.append(data)
    writer.get_extra_info = MagicMock(return_value="utf-8")

    session = PTYSession(reader, writer, "/bin/sh", [])

    session._write_to_telnet(_BSU + b"content" + _ESU)
    assert len(written) == 1
    assert session._in_sync_update is False
    assert session._output_buffer == b""


async def test_pty_session_write_to_telnet_bsu_waits_for_esu():
    """Test that BSU waits for ESU before flushing."""
    # std imports
    from unittest.mock import MagicMock

    # local
    from telnetlib3.server_pty_shell import PTYSession, _BSU, _ESU

    reader = MagicMock()
    writer = MagicMock()
    written = []
    writer.write = lambda data: written.append(data)
    writer.get_extra_info = MagicMock(return_value="utf-8")

    session = PTYSession(reader, writer, "/bin/sh", [])

    session._write_to_telnet(_BSU + b"partial")
    assert len(written) == 0
    assert session._in_sync_update is True

    session._write_to_telnet(b" content" + _ESU)
    assert len(written) == 1
    assert session._in_sync_update is False


async def test_pty_session_write_to_telnet_buffer_overflow():
    """Test 256KB buffer overflow protection."""
    # std imports
    from unittest.mock import MagicMock

    # local
    from telnetlib3.server_pty_shell import PTYSession, _BSU

    reader = MagicMock()
    writer = MagicMock()
    written = []
    writer.write = lambda data: written.append(data)
    writer.get_extra_info = MagicMock(return_value="utf-8")

    session = PTYSession(reader, writer, "/bin/sh", [])
    session._in_sync_update = True
    large_data = b"x" * 300000
    session._output_buffer = large_data

    session._write_to_telnet(b"")
    assert len(written) == 1
    assert session._output_buffer == b""


async def test_pty_session_flush_output_charset_change():
    """Test that charset change recreates decoder."""
    # std imports
    from unittest.mock import MagicMock

    # local
    from telnetlib3.server_pty_shell import PTYSession

    reader = MagicMock()
    writer = MagicMock()
    written = []
    writer.write = lambda data: written.append(data)
    charset_values = ["utf-8"]
    writer.get_extra_info = MagicMock(side_effect=lambda k, d=None: charset_values[0] if k == "charset" else d)

    session = PTYSession(reader, writer, "/bin/sh", [])

    session._flush_output(b"hello")
    original_decoder = session._decoder
    assert session._decoder_charset == "utf-8"

    charset_values[0] = "latin-1"
    session._flush_output(b"world")
    assert session._decoder is not original_decoder
    assert session._decoder_charset == "latin-1"


async def test_pty_session_flush_output_incomplete_utf8():
    """Test that incomplete UTF-8 sequences are buffered."""
    # std imports
    from unittest.mock import MagicMock

    # local
    from telnetlib3.server_pty_shell import PTYSession

    reader = MagicMock()
    writer = MagicMock()
    written = []
    writer.write = lambda data: written.append(data)
    writer.get_extra_info = MagicMock(return_value="utf-8")

    session = PTYSession(reader, writer, "/bin/sh", [])

    session._flush_output(b"hello\xc3")
    assert len(written) == 1
    assert written[0] == "hello"

    session._flush_output(b"\xa9", final=True)
    assert len(written) == 2
    assert written[1] == "\xe9"


async def test_pty_session_write_to_pty_string_encoding():
    """Test that string data is encoded using charset."""
    # std imports
    import os
    from unittest.mock import MagicMock, patch

    # local
    from telnetlib3.server_pty_shell import PTYSession

    reader = MagicMock()
    writer = MagicMock()
    writer.get_extra_info = MagicMock(return_value="utf-8")

    session = PTYSession(reader, writer, "/bin/sh", [])
    session.master_fd = 99

    written_data = []

    def mock_write(fd, data):
        written_data.append((fd, data))
        return len(data)

    with patch("os.write", side_effect=mock_write):
        session._write_to_pty("hello")
        assert len(written_data) == 1
        assert written_data[0][0] == 99
        assert written_data[0][1] == b"hello"


async def test_pty_session_write_to_pty_oserror_sets_closing():
    """Test that OSError sets _closing flag."""
    # std imports
    import os
    from unittest.mock import MagicMock, patch

    # local
    from telnetlib3.server_pty_shell import PTYSession

    reader = MagicMock()
    writer = MagicMock()
    writer.get_extra_info = MagicMock(return_value="utf-8")

    session = PTYSession(reader, writer, "/bin/sh", [])
    session.master_fd = 99
    session._closing = False

    with patch("os.write", side_effect=OSError("broken pipe")):
        session._write_to_pty(b"data")
        assert session._closing is True


async def test_pty_session_write_to_pty_none_fd():
    """Test that _write_to_pty does nothing with None master_fd."""
    # std imports
    from unittest.mock import MagicMock, patch

    # local
    from telnetlib3.server_pty_shell import PTYSession

    reader = MagicMock()
    writer = MagicMock()
    writer.get_extra_info = MagicMock(return_value="utf-8")

    session = PTYSession(reader, writer, "/bin/sh", [])
    session.master_fd = None

    write_calls = []
    with patch("os.write", side_effect=lambda fd, data: write_calls.append((fd, data))):
        session._write_to_pty(b"data")
        assert len(write_calls) == 0


async def test_pty_session_cleanup_flushes_remaining_buffer():
    """Test that cleanup flushes remaining buffer with final=True."""
    # std imports
    from unittest.mock import MagicMock, patch

    # local
    from telnetlib3.server_pty_shell import PTYSession

    reader = MagicMock()
    writer = MagicMock()
    written = []
    writer.write = lambda data: written.append(data)
    writer.get_extra_info = MagicMock(return_value="utf-8")

    session = PTYSession(reader, writer, "/bin/sh", [])
    session._output_buffer = b"remaining data"
    session.master_fd = 99
    session.child_pid = 12345

    with patch("os.close"), \
         patch("os.kill"), \
         patch("os.waitpid", return_value=(0, 0)):
        session.cleanup()

    assert len(written) == 1
    assert written[0] == "remaining data"
    assert session._output_buffer == b""


async def test_wait_for_terminal_info_returns_early():
    """Test _wait_for_terminal_info returns early when TERM and rows available."""
    # std imports
    from unittest.mock import MagicMock

    # local
    from telnetlib3.server_pty_shell import _wait_for_terminal_info

    writer = MagicMock()
    writer.get_extra_info = MagicMock(side_effect=lambda k: {"TERM": "xterm", "rows": 25}.get(k))

    await _wait_for_terminal_info(writer, timeout=2.0)


async def test_wait_for_terminal_info_timeout():
    """Test _wait_for_terminal_info returns after timeout when TERM/rows not available."""
    # std imports
    import time
    from unittest.mock import MagicMock

    # local
    from telnetlib3.server_pty_shell import _wait_for_terminal_info

    writer = MagicMock()
    writer.get_extra_info = MagicMock(return_value=None)

    start = time.time()
    await _wait_for_terminal_info(writer, timeout=0.3)
    elapsed = time.time() - start

    assert elapsed >= 0.25


async def test_wait_for_terminal_info_waits_for_rows():
    """Test _wait_for_terminal_info waits until rows become available."""
    # std imports
    import time
    from unittest.mock import MagicMock

    # local
    from telnetlib3.server_pty_shell import _wait_for_terminal_info

    call_count = [0]

    def get_info(key):
        call_count[0] += 1
        if key == "TERM":
            return "xterm"
        if key == "rows":
            if call_count[0] > 4:
                return 25
            return None
        return None

    writer = MagicMock()
    writer.get_extra_info = MagicMock(side_effect=get_info)

    start = time.time()
    await _wait_for_terminal_info(writer, timeout=2.0)
    elapsed = time.time() - start

    assert elapsed < 1.0
    assert call_count[0] > 2


async def test_pty_session_set_window_size_no_fd():
    """Test _set_window_size does nothing when master_fd is None."""
    # std imports
    from unittest.mock import MagicMock, patch

    # local
    from telnetlib3.server_pty_shell import PTYSession

    reader = MagicMock()
    writer = MagicMock()
    writer.get_extra_info = MagicMock(return_value=None)

    session = PTYSession(reader, writer, "/bin/sh", [])
    session.master_fd = None
    session.child_pid = None

    ioctl_calls = []
    with patch("fcntl.ioctl", side_effect=lambda fd, cmd, data: ioctl_calls.append((fd, cmd, data))):
        session._set_window_size(25, 80)

    assert len(ioctl_calls) == 0


async def test_pty_session_set_window_size_process_gone():
    """Test _set_window_size handles ProcessLookupError."""
    # std imports
    from unittest.mock import MagicMock, patch

    # local
    from telnetlib3.server_pty_shell import PTYSession

    reader = MagicMock()
    writer = MagicMock()
    writer.get_extra_info = MagicMock(return_value=None)

    session = PTYSession(reader, writer, "/bin/sh", [])
    session.master_fd = 99
    session.child_pid = 12345

    with patch("fcntl.ioctl"), \
         patch("os.getpgid", return_value=12345), \
         patch("os.killpg", side_effect=ProcessLookupError("process gone")):
        session._set_window_size(25, 80)


async def test_pty_session_cleanup_handles_child_process_error():
    """Test cleanup handles ChildProcessError from waitpid."""
    # std imports
    from unittest.mock import MagicMock, patch

    # local
    from telnetlib3.server_pty_shell import PTYSession

    reader = MagicMock()
    writer = MagicMock()
    writer.get_extra_info = MagicMock(return_value="utf-8")

    session = PTYSession(reader, writer, "/bin/sh", [])
    session.master_fd = 99
    session.child_pid = 12345

    with patch("os.close"), \
         patch("os.kill"), \
         patch("os.waitpid", side_effect=ChildProcessError("already reaped")):
        session.cleanup()

    assert session.child_pid is None


async def test_pty_session_cleanup_handles_oserror_on_close():
    """Test cleanup handles OSError when closing master_fd."""
    # std imports
    from unittest.mock import MagicMock, patch

    # local
    from telnetlib3.server_pty_shell import PTYSession

    reader = MagicMock()
    writer = MagicMock()
    writer.get_extra_info = MagicMock(return_value="utf-8")

    session = PTYSession(reader, writer, "/bin/sh", [])
    session.master_fd = 99
    session.child_pid = 12345

    with patch("os.close", side_effect=OSError("bad fd")), \
         patch("os.kill"), \
         patch("os.waitpid", return_value=(0, 0)):
        session.cleanup()

    assert session.master_fd is None


async def test_pty_session_cleanup_handles_process_lookup_error_on_kill():
    """Test cleanup handles ProcessLookupError when killing child."""
    # std imports
    from unittest.mock import MagicMock, patch

    # local
    from telnetlib3.server_pty_shell import PTYSession

    reader = MagicMock()
    writer = MagicMock()
    writer.get_extra_info = MagicMock(return_value="utf-8")

    session = PTYSession(reader, writer, "/bin/sh", [])
    session.master_fd = 99
    session.child_pid = 12345

    with patch("os.close"), \
         patch("os.kill", side_effect=ProcessLookupError("already dead")), \
         patch("os.waitpid", return_value=(0, 0)):
        session.cleanup()

    assert session.child_pid is None


async def test_pty_session_flush_remaining():
    """Test _flush_remaining flushes buffer when not in sync update."""
    # std imports
    from unittest.mock import MagicMock

    # local
    from telnetlib3.server_pty_shell import PTYSession

    reader = MagicMock()
    writer = MagicMock()
    written = []
    writer.write = lambda data: written.append(data)
    writer.get_extra_info = MagicMock(return_value="utf-8")

    session = PTYSession(reader, writer, "/bin/sh", [])
    session._output_buffer = b"partial line"
    session._in_sync_update = False

    session._flush_remaining()

    assert len(written) == 1
    assert written[0] == "partial line"
    assert session._output_buffer == b""


async def test_pty_session_flush_remaining_during_sync():
    """Test _flush_remaining does not flush during synchronized update."""
    # std imports
    from unittest.mock import MagicMock

    # local
    from telnetlib3.server_pty_shell import PTYSession

    reader = MagicMock()
    writer = MagicMock()
    written = []
    writer.write = lambda data: written.append(data)
    writer.get_extra_info = MagicMock(return_value="utf-8")

    session = PTYSession(reader, writer, "/bin/sh", [])
    session._output_buffer = b"partial line"
    session._in_sync_update = True

    session._flush_remaining()

    assert len(written) == 0
    assert session._output_buffer == b"partial line"


async def test_pty_session_flush_output_empty_data():
    """Test _flush_output does nothing with empty data."""
    # std imports
    from unittest.mock import MagicMock

    # local
    from telnetlib3.server_pty_shell import PTYSession

    reader = MagicMock()
    writer = MagicMock()
    written = []
    writer.write = lambda data: written.append(data)
    writer.get_extra_info = MagicMock(return_value="utf-8")

    session = PTYSession(reader, writer, "/bin/sh", [])

    session._flush_output(b"")
    session._flush_output(b"", final=True)

    assert len(written) == 0


async def test_pty_session_write_to_telnet_pre_bsu_content():
    """Test content before BSU is flushed."""
    # std imports
    from unittest.mock import MagicMock

    # local
    from telnetlib3.server_pty_shell import PTYSession, _BSU, _ESU

    reader = MagicMock()
    writer = MagicMock()
    written = []
    writer.write = lambda data: written.append(data)
    writer.get_extra_info = MagicMock(return_value="utf-8")

    session = PTYSession(reader, writer, "/bin/sh", [])

    session._write_to_telnet(b"before\n" + _BSU + b"during" + _ESU)
    assert len(written) == 2
    assert "before\n" in written[0]
    assert session._in_sync_update is False


async def test_pty_spawn_error():
    """Test PTYSpawnError exception class."""
    # local
    from telnetlib3.server_pty_shell import PTYSpawnError

    err = PTYSpawnError("test error")
    assert str(err) == "test error"
    assert isinstance(err, Exception)


async def test_pty_session_handle_exec_error_parse():
    """Test _handle_exec_error parses error format correctly."""
    # std imports
    from unittest.mock import MagicMock

    # local
    from telnetlib3.server_pty_shell import PTYSession, PTYSpawnError

    reader = MagicMock()
    writer = MagicMock()
    writer.get_extra_info = MagicMock(return_value=None)

    session = PTYSession(reader, writer, "/bin/sh", [])

    import pytest

    with pytest.raises(PTYSpawnError) as exc_info:
        session._handle_exec_error(b"FileNotFoundError:2:No such file")
    assert "FileNotFoundError" in str(exc_info.value)
    assert "No such file" in str(exc_info.value)


async def test_pty_session_handle_exec_error_malformed():
    """Test _handle_exec_error handles malformed error data."""
    # std imports
    from unittest.mock import MagicMock

    # local
    from telnetlib3.server_pty_shell import PTYSession, PTYSpawnError

    reader = MagicMock()
    writer = MagicMock()
    writer.get_extra_info = MagicMock(return_value=None)

    session = PTYSession(reader, writer, "/bin/sh", [])

    import pytest

    with pytest.raises(PTYSpawnError) as exc_info:
        session._handle_exec_error(b"just some error text")
    assert "Exec failed" in str(exc_info.value)


async def test_pty_session_isalive_no_child():
    """Test _isalive returns False when no child pid."""
    # std imports
    from unittest.mock import MagicMock

    # local
    from telnetlib3.server_pty_shell import PTYSession

    reader = MagicMock()
    writer = MagicMock()
    writer.get_extra_info = MagicMock(return_value=None)

    session = PTYSession(reader, writer, "/bin/sh", [])
    session.child_pid = None

    assert session._isalive() is False


async def test_pty_session_isalive_child_gone():
    """Test _isalive returns False when child process exited."""
    # std imports
    import os
    from unittest.mock import MagicMock, patch

    # local
    from telnetlib3.server_pty_shell import PTYSession

    reader = MagicMock()
    writer = MagicMock()
    writer.get_extra_info = MagicMock(return_value=None)

    session = PTYSession(reader, writer, "/bin/sh", [])
    session.child_pid = 99999

    with patch.object(os, "waitpid", side_effect=ChildProcessError):
        assert session._isalive() is False


async def test_pty_session_isalive_child_running():
    """Test _isalive returns True when child still running."""
    # std imports
    import os
    from unittest.mock import MagicMock, patch

    # local
    from telnetlib3.server_pty_shell import PTYSession

    reader = MagicMock()
    writer = MagicMock()
    writer.get_extra_info = MagicMock(return_value=None)

    session = PTYSession(reader, writer, "/bin/sh", [])
    session.child_pid = 12345

    with patch.object(os, "waitpid", return_value=(0, 0)):
        assert session._isalive() is True


async def test_pty_session_terminate_not_alive():
    """Test _terminate returns True when child already dead."""
    # std imports
    from unittest.mock import MagicMock, patch

    # local
    from telnetlib3.server_pty_shell import PTYSession

    reader = MagicMock()
    writer = MagicMock()
    writer.get_extra_info = MagicMock(return_value=None)

    session = PTYSession(reader, writer, "/bin/sh", [])
    session.child_pid = None

    assert session._terminate() is True


async def test_pty_session_terminate_sends_signals():
    """Test _terminate sends signal sequence."""
    # std imports
    import os
    import signal
    from unittest.mock import MagicMock, patch, call

    # local
    from telnetlib3.server_pty_shell import PTYSession

    reader = MagicMock()
    writer = MagicMock()
    writer.get_extra_info = MagicMock(return_value=None)

    session = PTYSession(reader, writer, "/bin/sh", [])
    session.child_pid = 12345

    kill_calls = []
    isalive_calls = [True, True, False]  # alive, alive, then dead

    def mock_kill(pid, sig):
        kill_calls.append((pid, sig))

    def mock_isalive():
        return isalive_calls.pop(0) if isalive_calls else False

    with patch.object(os, "kill", side_effect=mock_kill):
        with patch.object(session, "_isalive", side_effect=mock_isalive):
            with patch("time.sleep"):
                result = session._terminate()

    assert result is True
    assert len(kill_calls) >= 1
    assert kill_calls[0][1] == signal.SIGHUP


async def test_pty_session_terminate_process_lookup_error():
    """Test _terminate handles ProcessLookupError."""
    # std imports
    import os
    from unittest.mock import MagicMock, patch

    # local
    from telnetlib3.server_pty_shell import PTYSession

    reader = MagicMock()
    writer = MagicMock()
    writer.get_extra_info = MagicMock(return_value=None)

    session = PTYSession(reader, writer, "/bin/sh", [])
    session.child_pid = 12345

    isalive_returns = [True]

    def mock_isalive():
        return isalive_returns.pop(0) if isalive_returns else False

    with patch.object(os, "kill", side_effect=ProcessLookupError):
        with patch.object(session, "_isalive", side_effect=mock_isalive):
            result = session._terminate()

    assert result is True
