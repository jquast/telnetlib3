# std imports
import sys
import types
import asyncio

# 3rd party
import pytest

# local
from telnetlib3 import client as cl
from telnetlib3 import accessories
from telnetlib3.client_base import BaseClient
from telnetlib3.tests.accessories import bind_host, create_server  # noqa: F401

_CLIENT_DEFAULTS = {
    "encoding": "utf8",
    "encoding_errors": "strict",
    "force_binary": False,
    "connect_minwait": 0.01,
    "connect_maxwait": 0.02,
}


def _make_client(**kwargs):
    return cl.TelnetClient(**{**_CLIENT_DEFAULTS, **kwargs})


def _make_terminal_client(**kwargs):
    return cl.TelnetTerminalClient(**{**_CLIENT_DEFAULTS, **kwargs})


@pytest.mark.parametrize(
    "offered,encoding,expected",
    [
        pytest.param(["utf-8"], "utf8", "utf-8", id="exact_match"),
        pytest.param(["latin-1"], "utf8", "", id="no_match_reject"),
        pytest.param(["utf-8", "latin-1"], "latin-1", "latin-1", id="latin1_exact_match"),
        pytest.param(["utf-8"], False, "utf-8", id="no_encoding_accepts_viable"),
        pytest.param(["utf-8"], "not-a-real-encoding-xyz", "utf-8", id="unknown_encoding"),
        pytest.param(
            ["iso-8859-1", "utf-8"],
            "not-a-real-encoding-xyz",
            "iso-8859-1",
            id="no_pref_first_viable",
        ),
        pytest.param(
            ["zzz-fake-1", "zzz-fake-2"], "not-a-real-encoding-xyz", "", id="no_viable_encodings"
        ),
        pytest.param(["utf-8"], "latin-1", "utf-8", id="latin1_weak_default"),
    ],
)
@pytest.mark.asyncio
async def test_send_charset(offered, encoding, expected):
    c = _make_client(encoding=encoding)
    assert c.send_charset(offered) == expected


@pytest.mark.asyncio
async def test_send_charset_null_default():
    c = _make_client()
    c.default_encoding = None
    assert not c.send_charset(["zzz-fake-1"])
    assert c.send_charset(["utf-8"]) == "utf-8"


@pytest.mark.parametrize(
    "offered,expected",
    [
        pytest.param(["iso-8859-02"], "iso-8859-02", id="iso_leading_zero"),
        pytest.param(["iso 8859-02"], "iso 8859-02", id="iso_space_leading_zero"),
        pytest.param(["cp-1250"], "cp-1250", id="cp_hyphen"),
    ],
)
@pytest.mark.asyncio
async def test_send_charset_normalization(offered, expected):
    c = _make_client(encoding=False)
    c.default_encoding = None
    assert c.send_charset(offered) == expected


@pytest.mark.parametrize(
    "name,expected",
    [
        pytest.param("iso-8859-02", "iso-8859-2", id="iso_leading_zero"),
        pytest.param("iso 8859-02", "iso-8859-2", id="iso_space_leading_zero"),
        pytest.param("cp-1250", "cp1250", id="cp_hyphen"),
        pytest.param("UTF-8", "UTF-8", id="passthrough"),
        pytest.param("iso-8859-15", "iso-8859-15", id="no_leading_zero"),
        pytest.param("x-penn-def", "x-penn-def", id="unknown_passthrough"),
    ],
)
def test_normalize_charset_name(name, expected):
    assert cl.TelnetClient._normalize_charset_name(name) == expected


@pytest.mark.asyncio
async def test_send_env():
    c = _make_client(term="xterm", cols=132, rows=43)
    env = c.send_env(["TERM", "LANG"])
    assert env["TERM"] == "xterm"
    assert "utf8" in env["LANG"]

    c2 = _make_client()
    env2 = c2.send_env([])
    assert "TERM" in env2 and "LANG" in env2


@pytest.mark.asyncio
async def test_send_naws():
    assert _make_client(rows=24, cols=80).send_naws() == (24, 80)


@pytest.mark.asyncio
async def test_send_ttype():
    assert _make_client(term="vt220").send_ttype() == "vt220"


@pytest.mark.asyncio
async def test_send_tspeed():
    assert _make_client(tspeed=(9600, 9600)).send_tspeed() == (9600, 9600)


@pytest.mark.asyncio
async def test_send_xdisploc():
    assert _make_client(xdisploc="myhost:0.0").send_xdisploc() == "myhost:0.0"


@pytest.mark.skipif(sys.platform == "win32", reason="requires fcntl")
def test_terminal_client_winsize_success(monkeypatch):
    import fcntl
    import struct

    fake_data = struct.pack("hhhh", 42, 120, 0, 0)
    monkeypatch.setattr(fcntl, "ioctl", lambda fd, req, buf: fake_data)
    assert cl.TelnetTerminalClient._winsize() == (42, 120)


@pytest.mark.skipif(sys.platform == "win32", reason="requires fcntl")
def test_terminal_client_winsize_ioerror(monkeypatch):
    import fcntl

    monkeypatch.setenv("LINES", "30")
    monkeypatch.setenv("COLUMNS", "100")

    def _raise(*args, **kwargs):
        raise IOError("not a tty")

    monkeypatch.setattr(fcntl, "ioctl", _raise)
    assert cl.TelnetTerminalClient._winsize() == (30, 100)


@pytest.mark.skipif(sys.platform == "win32", reason="requires fcntl")
@pytest.mark.asyncio
async def test_terminal_client_send_naws(monkeypatch):
    import fcntl

    monkeypatch.setenv("LINES", "48")
    monkeypatch.setenv("COLUMNS", "160")
    monkeypatch.setattr(fcntl, "ioctl", lambda *a, **kw: (_ for _ in ()).throw(IOError))
    assert _make_terminal_client().send_naws() == (48, 160)


@pytest.mark.skipif(sys.platform == "win32", reason="requires fcntl")
@pytest.mark.asyncio
async def test_terminal_client_send_env(monkeypatch):
    import fcntl

    def _raise(*args, **kwargs):
        raise IOError("not a tty")

    monkeypatch.setenv("LINES", "48")
    monkeypatch.setenv("COLUMNS", "160")
    monkeypatch.setattr(fcntl, "ioctl", _raise)
    env = _make_terminal_client().send_env(["LINES", "COLUMNS"])
    assert env["LINES"] == 48 and env["COLUMNS"] == 160


def test_argument_parser():
    parser = cl._get_argument_parser()
    args = parser.parse_args(["example.com", "2323"])
    assert args.host == "example.com" and args.port == 2323 and args.encoding == "utf8"

    defaults = parser.parse_args(["myhost"])
    assert defaults.port == 23 and defaults.force_binary is True and defaults.speed == 38400


def test_transform_args():
    parser = cl._get_argument_parser()
    result = cl._transform_args(
        parser.parse_args(["myhost", "5555", "--encoding", "latin-1", "--speed", "9600"])
    )
    assert result["host"] == "myhost" and result["port"] == 5555
    assert result["encoding"] == "latin-1" and result["tspeed"] == (9600, 9600)
    assert callable(result["shell"]) and "TERM" in result["send_environ"]

    result2 = cl._transform_args(parser.parse_args(["host", "--send-environ", "TERM,LANG"]))
    assert result2["send_environ"] == ("TERM", "LANG")


def test_transform_args_history_file():
    parser = cl._get_argument_parser()
    result = cl._transform_args(parser.parse_args(["myhost"]))
    assert result["history_file"] is not None
    assert "telnetlib3" in result["history_file"]
    assert result["history_file"].endswith("history")

    result_custom = cl._transform_args(
        parser.parse_args(["myhost", "--history-file", "/tmp/my-history"])
    )
    assert result_custom["history_file"] == "/tmp/my-history"

    result_disabled = cl._transform_args(parser.parse_args(["myhost", "--history-file", ""]))
    assert result_disabled["history_file"] is None


@pytest.mark.asyncio
async def test_open_connection_default_factory(bind_host, unused_tcp_port, monkeypatch):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)

    async with create_server(host=bind_host, port=unused_tcp_port, connect_maxwait=0.05):
        reader, writer = await cl.open_connection(
            host=bind_host,
            port=unused_tcp_port,
            connect_minwait=0.05,
            connect_maxwait=0.1,
            encoding=False,
        )
        assert isinstance(writer.protocol, cl.TelnetClient)
        assert not isinstance(writer.protocol, cl.TelnetTerminalClient)
        writer.close()


@pytest.mark.skipif(sys.platform == "win32", reason="TTY factory not used on win32")
@pytest.mark.asyncio
async def test_open_connection_tty_factory(bind_host, unused_tcp_port, monkeypatch):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)

    async with create_server(host=bind_host, port=unused_tcp_port, connect_maxwait=0.05):
        reader, writer = await cl.open_connection(
            host=bind_host,
            port=unused_tcp_port,
            connect_minwait=0.05,
            connect_maxwait=0.1,
            encoding=False,
        )
        assert isinstance(writer.protocol, cl.TelnetTerminalClient)
        writer.close()


def test_detect_syncterm_font_sets_force_binary():
    client = BaseClient.__new__(BaseClient)
    client.log = types.SimpleNamespace(debug=lambda *a, **kw: None, isEnabledFor=lambda _: False)
    client.force_binary = False
    client.writer = types.SimpleNamespace(environ_encoding="utf-8")
    client._detect_syncterm_font(b"\x1b[0;0 D")
    assert client.force_binary is True


@pytest.mark.parametrize("extra_args,expected", [([], "vga"), (["--colormatch", "xterm"], "xterm")])
def test_transform_args_colormatch(extra_args, expected):
    parser = cl._get_argument_parser()
    assert cl._transform_args(parser.parse_args(["myhost"] + extra_args))["colormatch"] == expected


def test_guard_shells_connection_counter():
    from telnetlib3.guard_shells import ConnectionCounter

    counter = ConnectionCounter(2)
    assert counter.try_acquire() is True
    assert counter.try_acquire() is True
    assert counter.try_acquire() is False
    assert counter.count == 2
    counter.release()
    assert counter.count == 1
    assert counter.try_acquire() is True
    counter.release()
    counter.release()
    counter.release()
    assert counter.count == 0


@pytest.mark.asyncio
async def test_guard_shells_busy_shell():
    from telnetlib3.guard_shells import busy_shell

    class MockWriter:
        def __init__(self):
            self.output = []
            self._extra = {"peername": ("127.0.0.1", 12345)}

        def write(self, data):
            self.output.append(data)

        async def drain(self):
            pass

        def get_extra_info(self, key, default=None):
            return self._extra.get(key, default)

    class MockReader:
        async def read(self, n):
            return ""

    reader = MockReader()
    writer = MockWriter()
    await busy_shell(reader, writer)

    output = "".join(writer.output)
    assert "Machine is busy" in output


@pytest.mark.asyncio
async def test_guard_shells_robot_check_timeout():
    from telnetlib3.guard_shells import robot_check

    class MockWriter:
        def __init__(self):
            self.output = []
            self._extra = {"peername": ("127.0.0.1", 12345)}

        def write(self, data):
            self.output.append(data)

        async def drain(self):
            pass

        def get_extra_info(self, key, default=None):
            return self._extra.get(key, default)

    class MockReader:
        def fn_encoding(**kw):
            return "utf-8"
        _decoder = None

        async def read(self, n):
            return ""

    assert await robot_check(MockReader(), MockWriter(), timeout=0.1) is False


async def _noop_shell(reader, writer):
    pass


def _fake_open_connection_factory(loop):
    """Build a mock open_connection that captures the shell callback."""
    captured_kwargs: dict = {}
    writer_obj = types.SimpleNamespace(
        _color_filter=None,
        _raw_mode=None,
        _ascii_eol=False,
        _input_filter=None,
        _repl_enabled=False,
        _history_file=None,
        _session_key="",
        _autoreply_rules=None,
        _autoreplies_file=None,
        _macro_defs=None,
        _macros_file=None,
        protocol=types.SimpleNamespace(waiter_closed=loop.create_future()),
    )
    writer_obj.protocol.waiter_closed.set_result(None)
    reader_obj = types.SimpleNamespace()

    async def _fake_open_connection(*args, **kwargs):
        captured_kwargs.update(kwargs)
        shell = kwargs["shell"]
        await shell(reader_obj, writer_obj)
        return reader_obj, writer_obj

    return _fake_open_connection, captured_kwargs, writer_obj


@pytest.mark.asyncio
async def test_run_client_unknown_palette(monkeypatch):
    """run_client exits with error on unknown palette."""
    monkeypatch.setattr(sys, "argv", ["telnetlib3-client", "localhost", "--colormatch", "bogus"])
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)

    with pytest.raises(SystemExit) as exc_info:
        await cl.run_client()
    assert exc_info.value.code == 1


@pytest.mark.parametrize(
    "argv_extra,filter_cls_name",
    [
        pytest.param(
            ["--encoding", "petscii", "--colormatch", "vga"],
            "PetsciiColorFilter",
            id="petscii_selects_c64",
        ),
        pytest.param(
            ["--encoding", "atascii", "--colormatch", "vga"],
            "AtasciiControlFilter",
            id="atascii_filter",
        ),
        pytest.param(["--colormatch", "vga"], "ColorFilter", id="colormatch_vga"),
        pytest.param(
            ["--colormatch", "petscii"], "PetsciiColorFilter", id="colormatch_petscii_alias"
        ),
    ],
)
@pytest.mark.asyncio
async def test_run_client_color_filter(monkeypatch, argv_extra, filter_cls_name):
    monkeypatch.setattr(
        sys, "argv", ["telnetlib3-client", "localhost"] + argv_extra + ["--no-repl"]
    )
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(accessories, "function_lookup", lambda _: _noop_shell)

    loop = asyncio.get_event_loop()
    fake_oc, captured, writer_obj = _fake_open_connection_factory(loop)
    monkeypatch.setattr(cl, "open_connection", fake_oc)
    await cl.run_client()

    assert type(writer_obj._color_filter).__name__ == filter_cls_name


@pytest.mark.asyncio
async def test_connection_made_reader_set_transport_exception():
    client = _make_client(encoding=False)

    class BadReader:
        def set_transport(self, t):
            raise RuntimeError("no transport support")

        def exception(self):
            return None

    client._reader_factory = lambda **kw: BadReader()
    transport = types.SimpleNamespace(
        get_extra_info=lambda name, default=None: default,
        write=lambda data: None,
        is_closing=lambda: False,
        close=lambda: None,
    )
    client.connection_made(transport)
    assert isinstance(client.reader, BadReader)


def test_detect_syncterm_font_returns_early_when_writer_none():
    client = BaseClient.__new__(BaseClient)
    client.log = types.SimpleNamespace(debug=lambda *a, **kw: None, isEnabledFor=lambda _: False)
    client.writer = None
    client._detect_syncterm_font(b"\x1b[0;0 D")


@pytest.mark.asyncio
async def test_begin_shell_cancelled_future():
    client = BaseClient.__new__(BaseClient)
    client.log = types.SimpleNamespace(debug=lambda *a, **kw: None, isEnabledFor=lambda _: False)
    client.shell = lambda r, w: None
    fut = asyncio.get_event_loop().create_future()
    fut.cancel()
    client.begin_shell(fut)


@pytest.mark.asyncio
async def test_data_received_trace_log(caplog):
    import logging

    client = _make_client(encoding=False)
    transport = types.SimpleNamespace(
        get_extra_info=lambda name, default=None: default,
        write=lambda data: None,
        is_closing=lambda: False,
        close=lambda: None,
        pause_reading=lambda: None,
    )
    client.connection_made(transport)
    with caplog.at_level(5):
        client.data_received(b"\xff\xfb\x01")
    await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_data_received_pauses_at_high_watermark():
    client = _make_client(encoding=False)
    paused = []
    transport = types.SimpleNamespace(
        get_extra_info=lambda name, default=None: default,
        write=lambda data: None,
        is_closing=lambda: False,
        close=lambda: None,
        pause_reading=lambda: paused.append(True),
        resume_reading=lambda: paused.append(False),
    )
    client.connection_made(transport)
    big_data = b"\x00" * (client._read_high + 100)
    client.data_received(big_data)
    assert client._reading_paused is True
    await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_data_received_pause_reading_exception():
    client = _make_client(encoding=False)

    def bad_pause():
        raise RuntimeError("pause not supported")

    transport = types.SimpleNamespace(
        get_extra_info=lambda name, default=None: default,
        write=lambda data: None,
        is_closing=lambda: False,
        close=lambda: None,
        pause_reading=bad_pause,
    )
    client.connection_made(transport)
    big_data = b"\x00" * (client._read_high + 100)
    client.data_received(big_data)
    assert client._reading_paused is False
    await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_process_rx_resumes_reading_on_drain():
    client = _make_client(encoding=False)
    resumed = []
    transport = types.SimpleNamespace(
        get_extra_info=lambda name, default=None: default,
        write=lambda data: None,
        is_closing=lambda: False,
        close=lambda: None,
        pause_reading=lambda: None,
        resume_reading=lambda: resumed.append(True),
    )
    client.connection_made(transport)
    client._reading_paused = True
    client._rx_queue.append(b"\x00" * 10)
    client._rx_bytes = 10
    await client._process_rx()
    assert len(resumed) >= 1


def test_fingerprint_main_oserror(monkeypatch):
    async def _bad_fp():
        raise OSError("connection refused")

    monkeypatch.setattr(cl, "run_fingerprint_client", _bad_fp)
    with pytest.raises(SystemExit) as exc_info:
        cl.fingerprint_main()
    assert exc_info.value.code == 1
