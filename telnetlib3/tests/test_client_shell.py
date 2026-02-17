"""Tests for telnetlib3.client_shell — Terminal mode handling."""

# std imports
import sys
import types
import asyncio
from unittest import mock

# 3rd party
import pytest
import pexpect

if sys.platform == "win32":
    pytest.skip("POSIX-only tests", allow_module_level=True)

# std imports
import termios  # noqa: E402

# local
from telnetlib3.client_shell import (  # noqa: E402
    _INPUT_XLAT,
    _INPUT_SEQ_XLAT,
    Terminal,
    InputFilter,
    _send_stdin,
    _transform_output,
)


class _MockOption:
    """Minimal mock for stream_writer.Option."""

    def __init__(self, opts: "dict[bytes, bool]") -> None:
        self._opts = opts

    def enabled(self, key: bytes) -> bool:
        return self._opts.get(key, False)


def _make_writer(
    will_echo: bool = False, raw_mode: "bool | None" = False, will_sga: bool = False
) -> object:
    """Build a minimal mock writer with the attributes Terminal needs."""
    from telnetlib3.telopt import SGA  # pylint: disable=import-outside-toplevel

    writer = types.SimpleNamespace(
        will_echo=will_echo,
        client=True,
        remote_option=_MockOption({SGA: will_sga}),
        log=types.SimpleNamespace(debug=lambda *a, **kw: None),
    )
    if raw_mode is not False:
        writer._raw_mode = raw_mode
    return writer


def _cooked_mode() -> "Terminal.ModeDef":
    """Return a typical cooked-mode ModeDef with canonical input enabled."""
    return Terminal.ModeDef(
        iflag=termios.BRKINT | termios.ICRNL | termios.IXON,
        oflag=termios.OPOST | termios.ONLCR,
        cflag=termios.CS8 | termios.CREAD,
        lflag=termios.ICANON | termios.ECHO | termios.ISIG | termios.IEXTEN,
        ispeed=termios.B38400,
        ospeed=termios.B38400,
        cc=[b"\x00"] * termios.NCCS,
    )


def _make_term(writer: object) -> Terminal:
    """Build a Terminal instance without __init__ side effects."""
    term = Terminal.__new__(Terminal)
    term.telnet_writer = writer
    return term


def _make_atascii_filter() -> InputFilter:
    return InputFilter(_INPUT_SEQ_XLAT["atascii"], _INPUT_XLAT["atascii"])


def _make_petscii_filter() -> InputFilter:
    return InputFilter(_INPUT_SEQ_XLAT["petscii"], _INPUT_XLAT["petscii"])


@pytest.mark.parametrize(
    "will_echo,raw_mode,will_sga",
    [(False, None, False), (False, False, False), (True, False, False), (False, False, True)],
)
def test_determine_mode_unchanged(will_echo: bool, raw_mode: "bool | None", will_sga: bool) -> None:
    term = _make_term(_make_writer(will_echo=will_echo, raw_mode=raw_mode, will_sga=will_sga))
    mode = _cooked_mode()
    assert term.determine_mode(mode) is mode


@pytest.mark.parametrize(
    "will_echo,raw_mode,will_sga",
    [
        (True, None, False),
        (False, None, True),
        (True, None, True),
        (False, True, False),
        (True, True, False),
    ],
)
def test_determine_mode_goes_raw(will_echo: bool, raw_mode: "bool | None", will_sga: bool) -> None:
    term = _make_term(_make_writer(will_echo=will_echo, raw_mode=raw_mode, will_sga=will_sga))
    mode = _cooked_mode()
    result = term.determine_mode(mode)
    assert result is not mode
    assert not result.lflag & termios.ICANON
    assert not result.lflag & termios.ECHO


def test_determine_mode_sga_sets_software_echo() -> None:
    term = _make_term(_make_writer(will_sga=True, raw_mode=None))
    term.determine_mode(_cooked_mode())
    assert term.software_echo is True


def test_make_raw_suppress_echo() -> None:
    term = _make_term(_make_writer(raw_mode=None))
    result = term._make_raw(_cooked_mode(), suppress_echo=True)
    assert not result.lflag & termios.ICANON
    assert not result.lflag & termios.ECHO
    assert not result.oflag & termios.OPOST
    assert result.cc[termios.VMIN] == 1
    assert result.cc[termios.VTIME] == 0


def test_make_raw_keep_echo() -> None:
    term = _make_term(_make_writer(raw_mode=None))
    result = term._make_raw(_cooked_mode(), suppress_echo=False)
    assert not result.lflag & termios.ICANON
    assert result.lflag & termios.ECHO
    assert not result.oflag & termios.OPOST


def test_echo_toggle_password_flow() -> None:
    writer = _make_writer(raw_mode=None)
    term = _make_term(writer)
    mode = _cooked_mode()

    r1 = term.determine_mode(mode)
    assert r1.lflag & termios.ECHO

    writer.will_echo = True
    r2 = term.determine_mode(mode)
    assert not r2.lflag & termios.ECHO
    assert not r2.lflag & termios.ICANON

    writer.will_echo = False
    r3 = term.determine_mode(mode)
    assert r3.lflag & termios.ECHO
    assert r3.lflag & termios.ICANON


def test_echo_toggle_sga_keeps_raw() -> None:
    writer = _make_writer(will_sga=True, raw_mode=None)
    term = _make_term(writer)
    mode = _cooked_mode()

    writer.will_echo = True
    r1 = term.determine_mode(mode)
    assert not r1.lflag & termios.ECHO
    assert not r1.lflag & termios.ICANON

    writer.will_echo = False
    r2 = term.determine_mode(mode)
    assert not r2.lflag & termios.ECHO
    assert not r2.lflag & termios.ICANON


def test_make_raw_toggle_echo_flag() -> None:
    term = _make_term(_make_writer(raw_mode=None))
    mode = _cooked_mode()
    suppressed = term._make_raw(mode, suppress_echo=True)
    assert not suppressed.lflag & termios.ECHO
    restored = term._make_raw(mode, suppress_echo=False)
    assert restored.lflag & termios.ECHO
    assert not restored.lflag & termios.ICANON


@pytest.mark.parametrize(
    "encoding,key,expected",
    [
        ("atascii", 0x7F, 0x7E),
        ("atascii", 0x08, 0x7E),
        ("atascii", 0x0D, 0x9B),
        ("atascii", 0x0A, 0x9B),
        ("petscii", 0x7F, 0x14),
        ("petscii", 0x08, 0x14),
    ],
)
def test_input_xlat(encoding: str, key: int, expected: int) -> None:
    assert _INPUT_XLAT[encoding][key] == expected


def test_input_xlat_normal_bytes_absent() -> None:
    xlat = _INPUT_XLAT["atascii"]
    for b in (ord("a"), ord("A"), ord("1"), ord(" ")):
        assert b not in xlat


@pytest.mark.parametrize("seq,expected", list(_INPUT_SEQ_XLAT["atascii"].items()))
def test_atascii_sequence_translated(seq: bytes, expected: bytes) -> None:
    assert _make_atascii_filter().feed(seq) == expected


@pytest.mark.parametrize("seq,expected", list(_INPUT_SEQ_XLAT["petscii"].items()))
def test_petscii_sequence_translated(seq: bytes, expected: bytes) -> None:
    assert _make_petscii_filter().feed(seq) == expected


@pytest.mark.parametrize(
    "data,expected",
    [
        (b"hello", b"hello"),
        (b"hi\x1b[Alo", b"hi\x1clo"),
        (b"\x1b[A\x1b[B\x1b[C\x1b[D", b"\x1c\x1d\x1f\x1e"),
        (b"\x7f", b"\x7e"),
        (b"\x08", b"\x7e"),
        (b"\r", b"\x9b"),
        (b"\n", b"\x9b"),
        (b"hello\r", b"hello\x9b"),
    ],
)
def test_atascii_filter_feed(data: bytes, expected: bytes) -> None:
    assert _make_atascii_filter().feed(data) == expected


def test_petscii_filter_byte_xlat() -> None:
    assert _make_petscii_filter().feed(b"\x7f") == b"\x14"


@pytest.mark.parametrize(
    "chunks,expected_chunks",
    [
        ((b"\x1b", b"[A"), (b"", b"\x1c")),
        ((b"\x1b[", b"A"), (b"", b"\x1c")),
        ((b"\x1b", b"x"), (b"", b"\x1bx")),
    ],
)
def test_atascii_filter_split_feed(
    chunks: "tuple[bytes, ...]", expected_chunks: "tuple[bytes, ...]"
) -> None:
    f = _make_atascii_filter()
    for chunk, expected in zip(chunks, expected_chunks):
        assert f.feed(chunk) == expected


def test_filter_no_xlat_passthrough() -> None:
    assert InputFilter({}, {}).feed(b"hello\x1b[Aworld") == b"hello\x1b[Aworld"


def test_filter_empty_feed() -> None:
    assert InputFilter({}, {}).feed(b"") == b""


@pytest.mark.parametrize("data,pending", [(b"\x1b", True), (b"\x1b[A", False), (b"x", False)])
def test_filter_has_pending(data: bytes, pending: bool) -> None:
    f = _make_atascii_filter()
    f.feed(data)
    assert f.has_pending == pending


@pytest.mark.parametrize("data,expected", [(b"\x1b", b"\x1b"), (b"\x1b[", b"\x1b[")])
def test_filter_flush(data: bytes, expected: bytes) -> None:
    f = _make_atascii_filter()
    f.feed(data)
    assert f.flush() == expected
    assert not f.has_pending


def test_filter_flush_empty() -> None:
    assert _make_atascii_filter().flush() == b""


def test_filter_flush_applies_byte_xlat() -> None:
    f = InputFilter({b"\x1b[A": b"\x1c"}, {0x1B: 0xFF})
    f.feed(b"\x1b")
    assert f.flush() == b"\xff"


def test_filter_default_esc_delay() -> None:
    assert _make_atascii_filter().esc_delay == 0.35


def test_filter_custom_esc_delay() -> None:
    assert InputFilter({}, {}, esc_delay=0.1).esc_delay == 0.1


def test_filter_ansi_passthrough_with_empty_seq_xlat() -> None:
    f = InputFilter({}, _INPUT_XLAT["atascii"])
    assert f.feed(b"\x1b[A") == b"\x1b[A"


def test_filter_byte_xlat_without_seq_xlat() -> None:
    f = InputFilter({}, _INPUT_XLAT["atascii"])
    assert f.feed(b"\x7f") == b"\x7e"


def test_filter_esc_not_buffered_without_seq_xlat() -> None:
    f = InputFilter({}, _INPUT_XLAT["atascii"])
    assert f.feed(b"\x1b") == b"\x1b"
    assert not f.has_pending


@pytest.mark.parametrize("data,expected", [(b"\r", b"\r"), (b"\n", b"\n"), (b"\x7f", b"\x7e")])
def test_filter_without_eol_xlat(data: bytes, expected: bytes) -> None:
    byte_xlat = dict(_INPUT_XLAT["atascii"])
    byte_xlat.pop(0x0D, None)
    byte_xlat.pop(0x0A, None)
    f = InputFilter(_INPUT_SEQ_XLAT["atascii"], byte_xlat)
    assert f.feed(data) == expected


# std imports
import os  # noqa: E402
import time as _time  # noqa: E402
import select  # noqa: E402
import tempfile  # noqa: E402
import contextlib  # noqa: E402
import subprocess  # noqa: E402

# local
from telnetlib3.tests.accessories import bind_host, asyncio_server, unused_tcp_port  # noqa: E402

_IAC = b"\xff"
_WILL = b"\xfb"
_WONT = b"\xfc"
_ECHO = b"\x01"
_SGA = b"\x03"


def _strip_iac(data: bytes) -> bytes:
    """Remove IAC command sequences from raw data for protocol servers."""
    result = bytearray()
    i = 0
    while i < len(data):
        if data[i] == 0xFF and i + 2 < len(data):
            i += 3
        else:
            result.append(data[i])
            i += 1
    return bytes(result)


def _client_cmd(host: str, port: int, extra: "list[str] | None" = None) -> "list[str]":
    prog = pexpect.which("telnetlib3-client")
    assert prog is not None
    args = [
        prog,
        host,
        str(port),
        "--connect-minwait=0.05",
        "--connect-maxwait=0.5",
        "--colormatch=none",
    ]
    if extra:
        args.extend(extra)
    return args


def _pty_read(
    master_fd: int,
    proc: "subprocess.Popen[bytes] | None" = None,
    marker: "bytes | None" = None,
    timeout: float = 8.0,
) -> bytes:
    """Read from PTY master until *marker* appears, process exits, or timeout."""
    buf = b""
    deadline = _time.monotonic() + timeout
    while _time.monotonic() < deadline:
        remaining = deadline - _time.monotonic()
        if remaining <= 0:
            break
        ready, _, _ = select.select([master_fd], [], [], min(remaining, 0.1))
        if ready:
            try:
                chunk = os.read(master_fd, 4096)
            except OSError:
                break
            if not chunk:
                break
            buf += chunk
            if marker is not None and marker in buf:
                return buf
        elif proc is not None and proc.poll() is not None:
            while True:
                r, _, _ = select.select([master_fd], [], [], 0)
                if not r:
                    break
                try:
                    chunk = os.read(master_fd, 4096)
                except OSError:
                    break
                if not chunk:
                    break
                buf += chunk
            break
    return buf


def _coverage_env() -> "dict[str, str]":
    """Build env dict that enables coverage tracking in subprocess."""
    env = os.environ.copy()
    project_root = os.path.join(os.path.dirname(__file__), os.pardir, os.pardir)
    coveragerc = os.path.join(project_root, "tox.ini")
    if os.path.exists(coveragerc):
        env["COVERAGE_PROCESS_START"] = os.path.abspath(coveragerc)
    return env


@contextlib.contextmanager
def _pty_client(cmd: "list[str]"):
    """
    Spawn client with stdin/stdout on a PTY; yields (proc, master_fd).

    When coverage.py is available, sets COVERAGE_PROCESS_START so the
    subprocess records coverage data (requires ``coverage_subprocess.pth``
    or equivalent installed in site-packages).
    """
    master_fd, slave_fd = os.openpty()
    tmpdir = tempfile.mkdtemp(prefix="telnetlib3_cov_")
    sitecust = os.path.join(tmpdir, "sitecustomize.py")
    with open(sitecust, "w", encoding="utf-8") as f:
        f.write("import coverage\ncoverage.process_startup()\n")
    env = _coverage_env()
    pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = tmpdir + (os.pathsep + pythonpath if pythonpath else "")
    proc = subprocess.Popen(
        cmd, stdin=slave_fd, stdout=slave_fd, stderr=subprocess.DEVNULL, close_fds=True, env=env
    )
    os.close(slave_fd)
    try:
        yield proc, master_fd
    finally:
        os.close(master_fd)
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2)
        try:
            os.unlink(sitecust)
            os.rmdir(tmpdir)
        except OSError:
            pass


@pytest.mark.parametrize(
    "server_msg,delay,extra_args,expected",
    [
        (
            b"hello from server\r\n",
            0.5,
            None,
            [b"Escape character", b"hello from server", b"Connection closed by foreign host."],
        ),
        (b"raw server\r\n", 0.1, ["--raw-mode"], [b"raw server"]),
    ],
)
async def test_simple_server_output(
    bind_host: str,
    unused_tcp_port: int,
    server_msg: bytes,
    delay: float,
    extra_args: "list[str] | None",
    expected: "list[bytes]",
) -> None:
    class Proto(asyncio.Protocol):
        def connection_made(self, transport):
            super().connection_made(transport)
            transport.write(server_msg)
            asyncio.get_event_loop().call_later(delay, transport.close)

    async with asyncio_server(Proto, bind_host, unused_tcp_port):
        cmd = _client_cmd(bind_host, unused_tcp_port, extra_args)
        with _pty_client(cmd) as (proc, master_fd):
            output = await asyncio.to_thread(_pty_read, master_fd, proc=proc)
            for marker in expected:
                assert marker in output


@pytest.mark.parametrize(
    "prompt,response,extra_args,send,expected",
    [
        (b"login: ", b"\r\nwelcome!\r\n", None, b"user\r", [b"login:", b"welcome!"]),
        (b"prompt> ", b"\r\ngot it\r\n", ["--line-mode"], b"hello\r", [b"got it", b"hello"]),
    ],
)
async def test_echo_sga_interaction(
    bind_host: str,
    unused_tcp_port: int,
    prompt: bytes,
    response: bytes,
    extra_args: "list[str] | None",
    send: bytes,
    expected: "list[bytes]",
) -> None:
    class Proto(asyncio.Protocol):
        def connection_made(self, transport):
            super().connection_made(transport)
            transport.write(_IAC + _WILL + _ECHO + _IAC + _WILL + _SGA + prompt)
            self._transport = transport

        def data_received(self, data):
            self._transport.write(response)
            asyncio.get_event_loop().call_later(0.1, self._transport.close)

    async with asyncio_server(Proto, bind_host, unused_tcp_port):
        cmd = _client_cmd(bind_host, unused_tcp_port, extra_args)

        def _interact(master_fd, proc):
            buf = _pty_read(master_fd, marker=prompt.rstrip())
            os.write(master_fd, send)
            buf += _pty_read(master_fd, proc=proc)
            return buf

        with _pty_client(cmd) as (proc, master_fd):
            output = await asyncio.to_thread(_interact, master_fd, proc)
            for marker in expected:
                assert marker in output


async def test_password_hidden_then_echo_restored(bind_host: str, unused_tcp_port: int) -> None:
    class Proto(asyncio.Protocol):
        def __init__(self):
            self._state = "name"
            self._buf = b""

        def connection_made(self, transport):
            super().connection_made(transport)
            self._transport = transport
            transport.write(b"Name: ")

        def data_received(self, data):
            clean = _strip_iac(data)
            if not clean:
                return
            self._buf += clean
            if b"\r" not in self._buf and b"\n" not in self._buf:
                return
            self._buf = b""
            if self._state == "name":
                self._state = "pass"
                self._transport.write(_IAC + _WILL + _ECHO + b"\r\nPassword: ")
            elif self._state == "pass":
                self._state = "done"
                self._transport.write(_IAC + _WONT + _ECHO + b"\r\nLogged in.\r\n")
                asyncio.get_event_loop().call_later(0.2, self._transport.close)

    async with asyncio_server(Proto, bind_host, unused_tcp_port):
        cmd = _client_cmd(bind_host, unused_tcp_port)

        def _interact(master_fd, proc):
            buf = _pty_read(master_fd, marker=b"Name:", timeout=10.0)
            os.write(master_fd, b"admin\r")
            buf += _pty_read(master_fd, marker=b"Password:", timeout=10.0)
            os.write(master_fd, b"secret\r")
            buf += _pty_read(master_fd, proc=proc)
            return buf

        with _pty_client(cmd) as (proc, master_fd):
            output = await asyncio.to_thread(_interact, master_fd, proc)
            assert b"Logged in." in output
            after_prompt = output.split(b"Password:")[-1]
            assert b"secret" not in after_prompt


async def test_backspace_visual_erase(bind_host: str, unused_tcp_port: int) -> None:
    class Proto(asyncio.Protocol):
        def __init__(self):
            self._state = "login"
            self._buf = b""

        def connection_made(self, transport):
            super().connection_made(transport)
            self._transport = transport
            transport.write(_IAC + _WILL + _ECHO + _IAC + _WILL + _SGA + b"login: ")

        def data_received(self, data):
            clean = _strip_iac(data)
            if not clean:
                return
            self._buf += clean
            if b"\r" not in self._buf and b"\n" not in self._buf:
                return
            self._buf = b""
            if self._state == "login":
                self._state = "cmd"
                self._transport.write(_IAC + _WONT + _ECHO + b"\r\nType here> ")
            elif self._state == "cmd":
                self._state = "done"
                self._transport.write(b"\r\ndone\r\n")
                asyncio.get_event_loop().call_later(0.2, self._transport.close)

    async with asyncio_server(Proto, bind_host, unused_tcp_port):
        cmd = _client_cmd(bind_host, unused_tcp_port)

        def _interact(master_fd, proc):
            buf = _pty_read(master_fd, marker=b"login:", timeout=10.0)
            os.write(master_fd, b"user\r")
            buf += _pty_read(master_fd, marker=b"Type here>", timeout=10.0)
            os.write(master_fd, b"ab\x7fc\r")
            buf += _pty_read(master_fd, proc=proc)
            return buf

        with _pty_client(cmd) as (proc, master_fd):
            output = await asyncio.to_thread(_interact, master_fd, proc)
            after_prompt = output.split(b"Type here>")[-1]
            assert b"\x08 \x08" in after_prompt
            assert b"^?" not in after_prompt


def test_check_auto_mode_not_istty() -> None:
    """check_auto_mode returns None when not attached to a TTY."""
    writer = _make_writer(will_echo=True, will_sga=True)
    term = _make_term(writer)
    term._istty = False
    assert term.check_auto_mode(switched_to_raw=False, last_will_echo=False) is None


async def test_setup_winch_registers_handler() -> None:
    """setup_winch registers SIGWINCH handler when istty is True."""
    writer = _make_writer()
    writer.local_option = _MockOption({})
    writer.is_closing = lambda: False
    term = _make_term(writer)
    term._istty = True
    term._winch_handle = None
    term.setup_winch()
    assert term._remove_winch is True
    term.cleanup_winch()
    assert term._remove_winch is False


async def test_send_stdin_with_input_filter() -> None:
    """_send_stdin feeds bytes through input filter and writes translated."""
    inf = InputFilter(_INPUT_SEQ_XLAT["atascii"], _INPUT_XLAT["atascii"])

    writer = _make_writer()
    writer._input_filter = inf
    writer._write = mock.Mock()
    stdout = mock.Mock()

    new_timer, pending = _send_stdin(b"\x1b[A", writer, stdout, local_echo=False)
    assert not pending
    assert new_timer is None
    writer._write.assert_called_once_with(b"\x1c")


async def test_send_stdin_with_pending_sequence() -> None:
    """_send_stdin returns pending=True when partial sequence is buffered."""
    inf = InputFilter(_INPUT_SEQ_XLAT["atascii"], _INPUT_XLAT["atascii"])

    writer = _make_writer()
    writer._input_filter = inf
    writer._write = mock.Mock()
    stdout = mock.Mock()

    new_timer, pending = _send_stdin(b"\x1b", writer, stdout, local_echo=False)
    assert pending is True
    assert new_timer is not None
    new_timer.cancel()


async def test_send_stdin_no_filter() -> None:
    """_send_stdin without input filter calls writer._write directly."""
    writer = _make_writer()
    writer._write = mock.Mock()
    stdout = mock.Mock()

    new_timer, pending = _send_stdin(b"hello", writer, stdout, local_echo=False)
    assert not pending
    assert new_timer is None
    writer._write.assert_called_once_with(b"hello")


def _make_transform_writer(**kwargs: object) -> object:
    """Build a minimal writer for _transform_output tests."""
    return types.SimpleNamespace(**kwargs)


@pytest.mark.parametrize(
    "inp,in_raw,expected",
    [
        ("hello", True, "hello"),
        ("hello\r\n", True, "hello\r\n"),
        ("hello\n", True, "hello\r\n"),
        ("\r", True, "\r"),
        ("A\rB", True, "A\rB"),
        ("\x1b[K\r\x1b[38m", True, "\x1b[K\r\x1b[38m"),
        ("\r\n", True, "\r\n"),
        ("\r\r\n", True, "\r\r\n"),
        ("hello\r\n", False, "hello\n"),
        ("hello\n", False, "hello\n"),
        ("\r", False, "\r"),
    ],
)
def test_transform_output_line_endings(inp: str, in_raw: bool, expected: str) -> None:
    writer = _make_transform_writer()
    assert _transform_output(inp, writer, in_raw) == expected


def test_transform_output_bare_cr_preserved_raw() -> None:
    """Bare CR (cursor return) must not become CRLF in raw mode."""
    writer = _make_transform_writer()
    out = _transform_output("\x1b[34;1H\x1b[K\r\x1b[38;2;17;17;17mX\x1b[6n", writer, True)
    assert "\r\n" not in out
    assert "\r" in out
