# std imports
import sys

# 3rd party
import pytest

# local
from telnetlib3 import client as cl
from telnetlib3.tests.accessories import (  # noqa: F401  # pylint: disable=unused-import
    bind_host,
    create_server,
)

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
    # std imports
    import fcntl
    import struct

    fake_data = struct.pack("hhhh", 42, 120, 0, 0)
    monkeypatch.setattr(fcntl, "ioctl", lambda fd, req, buf: fake_data)
    assert cl.TelnetTerminalClient._winsize() == (42, 120)


@pytest.mark.skipif(sys.platform == "win32", reason="requires fcntl")
def test_terminal_client_winsize_ioerror(monkeypatch):
    # std imports
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
    # std imports
    import fcntl

    monkeypatch.setenv("LINES", "48")
    monkeypatch.setenv("COLUMNS", "160")
    monkeypatch.setattr(fcntl, "ioctl", lambda *a, **kw: (_ for _ in ()).throw(IOError))
    assert _make_terminal_client().send_naws() == (48, 160)


@pytest.mark.skipif(sys.platform == "win32", reason="requires fcntl")
@pytest.mark.asyncio
async def test_terminal_client_send_env(monkeypatch):
    # std imports
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
