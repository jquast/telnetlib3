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
    unused_tcp_port,
    make_preexec_coverage,
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


@pytest.fixture
def mock_session():
    """Create a mock PTYSession for unit testing."""
    # std imports
    from unittest.mock import MagicMock

    # local
    from telnetlib3.server_pty_shell import PTYSession

    def _create(extra_info=None, capture_writes=False):
        reader = MagicMock()
        writer = MagicMock()
        written = [] if capture_writes else None
        if capture_writes:
            writer.write = written.append
        if extra_info is None:
            writer.get_extra_info = MagicMock(return_value=None)
        elif callable(extra_info):
            writer.get_extra_info = MagicMock(side_effect=extra_info)
        else:
            writer.get_extra_info = MagicMock(side_effect=lambda k, d=None: extra_info.get(k, d))
        session = PTYSession(reader, writer, "/nonexistent.program", [])
        return session, written

    return _create


@_ignore_forkpty_deprecation
async def test_pty_shell_integration(bind_host, unused_tcp_port, require_no_capture):
    """Test PTY shell with various helper modes: cat, env, stty_size."""
    # local
    from telnetlib3 import make_pty_shell
    from telnetlib3.tests.accessories import create_server, open_connection

    # Test 1: cat mode - echo input back
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
        shell=make_pty_shell(
            sys.executable, [PTY_HELPER, "cat"], preexec_fn=make_preexec_coverage()
        ),
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

    # Test 2: env mode - verify TERM propagation
    _waiter = asyncio.Future()
    _output = asyncio.Future()

    async def client_shell(reader, writer):
        await _waiter
        await asyncio.sleep(0.5)
        output = await asyncio.wait_for(reader.read(100), 2.0)
        _output.set_result(output)

    async with create_server(
        protocol_factory=ServerWithWaiter,
        host=bind_host,
        port=unused_tcp_port,
        shell=make_pty_shell(
            sys.executable, [PTY_HELPER, "env", "TERM"], preexec_fn=make_preexec_coverage()
        ),
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

    # Test 3: stty_size mode - verify NAWS propagation
    _waiter = asyncio.Future()

    async with create_server(
        protocol_factory=ServerWithWaiter,
        host=bind_host,
        port=unused_tcp_port,
        shell=make_pty_shell(
            sys.executable, [PTY_HELPER, "stty_size"], preexec_fn=make_preexec_coverage()
        ),
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


@_ignore_forkpty_deprecation
async def test_pty_shell_lifecycle(bind_host, unused_tcp_port, require_no_capture):
    """Test PTY shell lifecycle: child exit and client disconnect."""
    # local
    from telnetlib3 import make_pty_shell
    from telnetlib3.tests.accessories import create_server, open_connection

    # Test 1: child exit closes connection gracefully
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
        shell=make_pty_shell(
            sys.executable, [PTY_HELPER, "exit_code", "0"], preexec_fn=make_preexec_coverage()
        ),
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

    # Test 2: client disconnect kills child process
    _waiter = asyncio.Future()
    _closed = asyncio.Future()

    class ServerWithCloseWaiter(telnetlib3.TelnetServer):
        def begin_shell(self, result):
            super().begin_shell(result)
            if not _waiter.done():
                _waiter.set_result(self)

        def connection_lost(self, exc):
            super().connection_lost(exc)
            if not _closed.done():
                _closed.set_result(True)

    async with create_server(
        protocol_factory=ServerWithCloseWaiter,
        host=bind_host,
        port=unused_tcp_port,
        shell=make_pty_shell(
            sys.executable, [PTY_HELPER, "cat"], preexec_fn=make_preexec_coverage()
        ),
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


async def test_pty_session_build_environment(mock_session):
    """Test PTYSession environment building with various configurations."""
    # Test with full environment info
    session, _ = mock_session(
        {
            "TERM": "xterm-256color",
            "rows": 30,
            "cols": 100,
            "LANG": "en_US.UTF-8",
            "DISPLAY": ":0",
        }
    )
    env = session._build_environment()
    assert env["TERM"] == "xterm-256color"
    assert env["LINES"] == "30"
    assert env["COLUMNS"] == "100"
    assert env["LANG"] == "en_US.UTF-8"
    assert env["LC_ALL"] == "en_US.UTF-8"
    assert env["DISPLAY"] == ":0"

    # Test charset fallback when no LANG
    session, _ = mock_session(
        {
            "TERM": "vt100",
            "rows": 24,
            "cols": 80,
            "charset": "ISO-8859-1",
        }
    )
    env = session._build_environment()
    assert env["TERM"] == "vt100"
    assert env["LANG"] == "en_US.ISO-8859-1"


async def test_pty_session_naws_behavior(mock_session):
    """Test NAWS debouncing, latest value usage, and cleanup cancellation."""
    # std imports
    import struct
    from unittest.mock import MagicMock, patch

    session, _ = mock_session()
    session.master_fd = 1
    session.child_pid = 12345
    session.writer.protocol = MagicMock()

    signal_calls = []
    ioctl_calls = []

    def mock_killpg(pgid, sig):
        signal_calls.append((pgid, sig))

    def mock_ioctl(fd, cmd, data):
        ioctl_calls.append((fd, cmd, data))

    with patch("os.getpgid", return_value=12345), patch(
        "os.killpg", side_effect=mock_killpg
    ), patch("fcntl.ioctl", side_effect=mock_ioctl):
        # Rapid updates should be debounced - only one signal after delay
        session._on_naws(25, 80)
        session._on_naws(30, 90)
        session._on_naws(50, 150)
        assert len(signal_calls) == 0

        await asyncio.sleep(0.25)
        assert len(signal_calls) == 1
        assert len(ioctl_calls) == 1

        # Should use latest values (50, 150)
        expected_winsize = struct.pack("HHHH", 50, 150, 0, 0)
        assert ioctl_calls[0][2] == expected_winsize

    # Test cleanup cancels pending NAWS timer
    session, _ = mock_session()
    session.master_fd = 1
    session.child_pid = 12345
    session.writer.protocol = MagicMock()
    winch_calls = []

    def mock_killpg_winch(pgid, sig):
        # std imports
        import signal as signal_mod

        if sig == signal_mod.SIGWINCH:
            winch_calls.append((pgid, sig))

    with patch("os.getpgid", return_value=12345), patch(
        "os.killpg", side_effect=mock_killpg_winch
    ), patch("os.kill"), patch("os.waitpid", return_value=(0, 0)), patch("os.close"), patch(
        "fcntl.ioctl"
    ):
        session._on_naws(25, 80)
        session.cleanup()
        await asyncio.sleep(0.25)
        assert len(winch_calls) == 0


async def test_pty_session_write_to_telnet_buffering(mock_session):
    """Test _write_to_telnet line buffering, BSU/ESU handling, and overflow protection."""
    # local
    from telnetlib3.server_pty_shell import _BSU, _ESU

    # Line buffering: buffers until newline
    session, written = mock_session({"charset": "utf-8"}, capture_writes=True)
    session._write_to_telnet(b"hello")
    assert len(written) == 0
    assert session._output_buffer == b"hello"

    session._write_to_telnet(b" world\nmore")
    assert len(written) == 1
    assert "hello world\n" in written[0]
    assert session._output_buffer == b"more"

    # BSU/ESU: complete sequence flushes immediately
    session, written = mock_session({"charset": "utf-8"}, capture_writes=True)
    session._write_to_telnet(_BSU + b"content" + _ESU)
    assert len(written) == 1
    assert session._in_sync_update is False

    # BSU waits for ESU
    session, written = mock_session({"charset": "utf-8"}, capture_writes=True)
    session._write_to_telnet(_BSU + b"partial")
    assert len(written) == 0
    assert session._in_sync_update is True
    session._write_to_telnet(b" content" + _ESU)
    assert len(written) == 1
    assert session._in_sync_update is False

    # Buffer overflow protection (256KB)
    session, written = mock_session({"charset": "utf-8"}, capture_writes=True)
    session._in_sync_update = True
    session._output_buffer = b"x" * 300000

    session._write_to_telnet(b"")
    assert len(written) == 1
    assert session._output_buffer == b""


async def test_pty_session_flush_output_behavior(mock_session):
    """Test flush_output charset handling and incomplete UTF-8 buffering."""
    # std imports
    from unittest.mock import MagicMock

    # local
    from telnetlib3.server_pty_shell import PTYSession

    # Charset change recreates decoder
    reader = MagicMock()
    writer = MagicMock()
    written = []
    writer.write = written.append
    charset_values = ["utf-8"]
    writer.get_extra_info = MagicMock(
        side_effect=lambda k, d=None: charset_values[0] if k == "charset" else d
    )
    session = PTYSession(reader, writer, "/nonexistent.program", [])
    session._flush_output(b"hello")
    original_decoder = session._decoder
    assert session._decoder_charset == "utf-8"
    charset_values[0] = "latin-1"
    session._flush_output(b"world")
    assert session._decoder is not original_decoder
    assert session._decoder_charset == "latin-1"

    # Incomplete UTF-8 sequences are buffered
    session, written = mock_session({"charset": "utf-8"}, capture_writes=True)
    session._flush_output(b"hello\xc3")
    assert len(written) == 1
    assert written[0] == "hello"
    session._flush_output(b"\xa9", final=True)
    assert len(written) == 2
    assert written[1] == "\xe9"


async def test_pty_session_write_to_pty_behavior(mock_session):
    """Test _write_to_pty encoding, error handling, and None fd guard."""
    # std imports
    from unittest.mock import patch

    # String encoding
    session, _ = mock_session({"charset": "utf-8"})
    session.master_fd = 99
    written_data = []

    def mock_write(fd, data):
        written_data.append((fd, data))
        return len(data)

    with patch("os.write", side_effect=mock_write):
        session._write_to_pty("hello")
        assert written_data == [(99, b"hello")]

    # OSError sets _closing flag
    session, _ = mock_session({"charset": "utf-8"})
    session.master_fd = 99
    session._closing = False
    with patch("os.write", side_effect=OSError("broken pipe")):
        session._write_to_pty(b"data")
        assert session._closing is True

    # None fd does nothing
    session, _ = mock_session({"charset": "utf-8"})
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
    writer.write = written.append
    writer.get_extra_info = MagicMock(return_value="utf-8")

    session = PTYSession(reader, writer, "/nonexistent.program", [])
    session._output_buffer = b"remaining data"
    session.master_fd = 99
    session.child_pid = 12345

    with patch("os.close"), patch("os.kill"), patch("os.waitpid", return_value=(0, 0)):
        session.cleanup()

    assert len(written) == 1
    assert written[0] == "remaining data"
    assert session._output_buffer == b""


async def test_wait_for_terminal_info_behavior():
    """Test _wait_for_terminal_info early return, timeout, and polling behavior."""
    # std imports
    import time
    from unittest.mock import MagicMock

    # local
    from telnetlib3.server_pty_shell import _wait_for_terminal_info

    # Returns early when TERM and rows available
    writer = MagicMock()
    writer.get_extra_info = MagicMock(side_effect={"TERM": "xterm", "rows": 25}.get)
    await _wait_for_terminal_info(writer, timeout=2.0)

    # Times out when info not available
    writer = MagicMock()
    writer.get_extra_info = MagicMock(return_value=None)
    start = time.time()
    await _wait_for_terminal_info(writer, timeout=0.3)
    assert time.time() - start >= 0.25

    # Polls until rows become available
    call_count = [0]

    def get_info(key):
        call_count[0] += 1
        if key == "TERM":
            return "xterm"
        if key == "rows" and call_count[0] > 4:
            return 25
        return None

    writer = MagicMock()
    writer.get_extra_info = MagicMock(side_effect=get_info)
    start = time.time()
    await _wait_for_terminal_info(writer, timeout=2.0)
    assert time.time() - start < 1.0
    assert call_count[0] > 2


async def test_pty_session_set_window_size_behavior(mock_session):
    """Test _set_window_size guards and error handling."""
    # std imports
    from unittest.mock import patch

    # No fd does nothing
    session, _ = mock_session()
    session.master_fd = None
    session.child_pid = None
    ioctl_calls = []
    with patch(
        "fcntl.ioctl", side_effect=lambda fd, cmd, data: ioctl_calls.append((fd, cmd, data))
    ):
        session._set_window_size(25, 80)
    assert len(ioctl_calls) == 0

    # Handles ProcessLookupError gracefully
    session, _ = mock_session()
    session.master_fd = 99
    session.child_pid = 12345
    with patch("fcntl.ioctl"), patch("os.getpgid", return_value=12345), patch(
        "os.killpg", side_effect=ProcessLookupError("process gone")
    ):
        session._set_window_size(25, 80)


@pytest.mark.parametrize(
    "close_effect,kill_effect,waitpid_effect,check_attr",
    [
        (None, None, ChildProcessError("already reaped"), "child_pid"),
        (OSError("bad fd"), None, (0, 0), "master_fd"),
        (None, ProcessLookupError("already dead"), (0, 0), "child_pid"),
    ],
)
async def test_pty_session_cleanup_error_recovery(
    close_effect, kill_effect, waitpid_effect, check_attr
):
    """Test cleanup handles various error conditions gracefully."""
    # std imports
    from unittest.mock import MagicMock, patch

    # local
    from telnetlib3.server_pty_shell import PTYSession

    reader = MagicMock()
    writer = MagicMock()
    writer.get_extra_info = MagicMock(return_value="utf-8")

    session = PTYSession(reader, writer, "/nonexistent.program", [])
    session.master_fd = 99
    session.child_pid = 12345

    close_patch = patch("os.close", side_effect=close_effect) if close_effect else patch("os.close")
    kill_patch = patch("os.kill", side_effect=kill_effect) if kill_effect else patch("os.kill")
    waitpid_side = waitpid_effect if isinstance(waitpid_effect, Exception) else None
    waitpid_return = None if isinstance(waitpid_effect, Exception) else waitpid_effect
    waitpid_patch = patch("os.waitpid", side_effect=waitpid_side, return_value=waitpid_return)

    with close_patch, kill_patch, waitpid_patch:
        session.cleanup()

    assert getattr(session, check_attr) is None


@pytest.mark.parametrize(
    "in_sync_update,expected_writes,expected_buffer",
    [
        (False, 1, b""),
        (True, 0, b"partial line"),
    ],
)
async def test_pty_session_flush_remaining_scenarios(
    in_sync_update, expected_writes, expected_buffer
):
    """Test _flush_remaining behavior based on sync update state."""
    # std imports
    from unittest.mock import MagicMock

    # local
    from telnetlib3.server_pty_shell import PTYSession

    reader = MagicMock()
    writer = MagicMock()
    written = []
    writer.write = written.append
    writer.get_extra_info = MagicMock(return_value="utf-8")

    session = PTYSession(reader, writer, "/nonexistent.program", [])
    session._output_buffer = b"partial line"
    session._in_sync_update = in_sync_update

    session._flush_remaining()

    assert len(written) == expected_writes
    if expected_writes > 0:
        assert written[0] == "partial line"
    assert session._output_buffer == expected_buffer


async def test_pty_session_flush_output_empty_data():
    """Test _flush_output does nothing with empty data."""
    # std imports
    from unittest.mock import MagicMock

    # local
    from telnetlib3.server_pty_shell import PTYSession

    reader = MagicMock()
    writer = MagicMock()
    written = []
    writer.write = written.append
    writer.get_extra_info = MagicMock(return_value="utf-8")

    session = PTYSession(reader, writer, "/nonexistent.program", [])

    session._flush_output(b"")
    session._flush_output(b"", final=True)

    assert len(written) == 0


async def test_pty_session_write_to_telnet_pre_bsu_content():
    """Test content before BSU is flushed."""
    # std imports
    from unittest.mock import MagicMock

    # local
    from telnetlib3.server_pty_shell import _BSU, _ESU, PTYSession

    reader = MagicMock()
    writer = MagicMock()
    written = []
    writer.write = written.append
    writer.get_extra_info = MagicMock(return_value="utf-8")

    session = PTYSession(reader, writer, "/nonexistent.program", [])

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


@pytest.mark.parametrize(
    "error_data,expected_substrings",
    [
        (b"FileNotFoundError:2:No such file", ["FileNotFoundError", "No such file"]),
        (b"just some error text", ["Exec failed"]),
    ],
)
async def test_pty_session_exec_error_parsing(error_data, expected_substrings):
    """Test _handle_exec_error parses various error formats."""
    # std imports
    from unittest.mock import MagicMock

    # local
    from telnetlib3.server_pty_shell import PTYSession, PTYSpawnError

    reader = MagicMock()
    writer = MagicMock()
    writer.get_extra_info = MagicMock(return_value=None)

    session = PTYSession(reader, writer, "/nonexistent.program", [])

    with pytest.raises(PTYSpawnError) as exc_info:
        session._handle_exec_error(error_data)

    for substring in expected_substrings:
        assert substring in str(exc_info.value)


@pytest.mark.parametrize(
    "child_pid,waitpid_behavior,expected",
    [
        (None, None, False),
        (99999, ChildProcessError, False),
        (12345, (0, 0), True),
    ],
)
async def test_pty_session_isalive_scenarios(child_pid, waitpid_behavior, expected):
    """Test _isalive returns correct values for various child states."""
    # std imports
    from unittest.mock import MagicMock, patch

    # local
    from telnetlib3.server_pty_shell import PTYSession

    reader = MagicMock()
    writer = MagicMock()
    writer.get_extra_info = MagicMock(return_value=None)

    session = PTYSession(reader, writer, "/nonexistent.program", [])
    session.child_pid = child_pid

    if waitpid_behavior is None:
        assert session._isalive() is expected
    elif isinstance(waitpid_behavior, type) and issubclass(waitpid_behavior, Exception):
        with patch.object(os, "waitpid", side_effect=waitpid_behavior):
            assert session._isalive() is expected
    else:
        with patch.object(os, "waitpid", return_value=waitpid_behavior):
            assert session._isalive() is expected


async def test_pty_session_terminate_scenarios():
    """Test _terminate handles various termination scenarios."""
    # std imports
    import signal
    from unittest.mock import MagicMock, patch

    # local
    from telnetlib3.server_pty_shell import PTYSession

    reader = MagicMock()
    writer = MagicMock()
    writer.get_extra_info = MagicMock(return_value=None)

    # Scenario 1: No child pid - returns True immediately
    session = PTYSession(reader, writer, "/nonexistent.program", [])
    session.child_pid = None
    assert session._terminate() is True

    # Scenario 2: Child alive, sends signals, child dies
    session = PTYSession(reader, writer, "/nonexistent.program", [])
    session.child_pid = 12345
    kill_calls = []
    isalive_calls = [True, True, False]

    def mock_kill(pid, sig):
        kill_calls.append((pid, sig))

    def mock_isalive():
        return isalive_calls.pop(0) if isalive_calls else False

    with patch.object(os, "kill", side_effect=mock_kill), patch.object(
        session, "_isalive", side_effect=mock_isalive
    ), patch("time.sleep"):
        result = session._terminate()

    assert result is True
    assert len(kill_calls) >= 1
    assert kill_calls[0][1] == signal.SIGHUP

    # Scenario 3: ProcessLookupError - child already gone
    session = PTYSession(reader, writer, "/nonexistent.program", [])
    session.child_pid = 12345
    isalive_returns = [True]

    def mock_isalive_2():
        return isalive_returns.pop(0) if isalive_returns else False

    with patch.object(os, "kill", side_effect=ProcessLookupError), patch.object(
        session, "_isalive", side_effect=mock_isalive_2
    ):
        result = session._terminate()

    assert result is True


async def test_pty_session_ga_timer_fires_after_idle(mock_session):
    """GA is sent 500ms after _flush_remaining when SGA not negotiated."""
    from unittest.mock import MagicMock

    session, written = mock_session({"charset": "utf-8"}, capture_writes=True)
    protocol = MagicMock()
    protocol.never_send_ga = False
    session.writer.protocol = protocol
    session.writer.is_closing = MagicMock(return_value=False)
    ga_calls = []
    session.writer.send_ga = lambda: ga_calls.append(True)

    session._output_buffer = b"prompt> "
    session._flush_remaining()
    assert session._ga_timer is not None
    assert len(ga_calls) == 0

    await asyncio.sleep(0.6)
    assert len(ga_calls) == 1
    assert session._ga_timer is None


async def test_pty_session_ga_timer_cancelled_by_new_output(mock_session):
    """GA timer is cancelled when new PTY output arrives."""
    from unittest.mock import MagicMock

    session, written = mock_session({"charset": "utf-8"}, capture_writes=True)
    protocol = MagicMock()
    protocol.never_send_ga = False
    session.writer.protocol = protocol
    session.writer.is_closing = MagicMock(return_value=False)
    ga_calls = []
    session.writer.send_ga = lambda: ga_calls.append(True)

    session._output_buffer = b"prompt> "
    session._flush_remaining()
    assert session._ga_timer is not None

    session._write_to_telnet(b"more output\n")
    assert session._ga_timer is None

    await asyncio.sleep(0.6)
    assert len(ga_calls) == 0


async def test_pty_session_ga_timer_suppressed_by_never_send_ga(mock_session):
    """GA timer is not scheduled when never_send_ga is set."""
    from unittest.mock import MagicMock

    session, written = mock_session({"charset": "utf-8"}, capture_writes=True)
    protocol = MagicMock()
    protocol.never_send_ga = True
    session.writer.protocol = protocol

    session._output_buffer = b"prompt> "
    session._flush_remaining()
    assert session._ga_timer is None


async def test_pty_session_ga_timer_cancelled_on_cleanup(mock_session):
    """GA timer is cancelled during cleanup."""
    from unittest.mock import MagicMock, patch

    session, _ = mock_session({"charset": "utf-8"})
    protocol = MagicMock()
    protocol.never_send_ga = False
    session.writer.protocol = protocol
    session.writer.is_closing = MagicMock(return_value=False)
    session.writer.send_ga = MagicMock()
    session.master_fd = 99
    session.child_pid = 12345

    session._schedule_ga()
    assert session._ga_timer is not None

    with patch("os.close"), patch("os.kill"), patch("os.waitpid", return_value=(0, 0)):
        session.cleanup()

    assert session._ga_timer is None
    await asyncio.sleep(0.6)
    session.writer.send_ga.assert_not_called()
