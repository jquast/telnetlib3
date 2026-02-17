"""Tests for TLS (TELNETS) support."""

# std imports
import os
import ssl
import sys
import time as _time
import codecs
import signal
import asyncio
import warnings
import threading
import contextlib

# 3rd party
import pytest
import trustme

# local
from telnetlib3.tests.accessories import (
    bind_host,
    create_server,
    open_connection,
    unused_tcp_port,
    init_subproc_coverage,
)


@pytest.fixture()
def ca():
    return trustme.CA()


@pytest.fixture()
def server_ssl_ctx(ca):
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ca.issue_cert("127.0.0.1", "localhost").configure_cert(ctx)
    return ctx


@pytest.fixture()
def client_ssl_ctx(ca):
    ctx = ssl.create_default_context()
    ca.configure_trust(ctx)
    return ctx


def _echo_shell(send, reply):
    """Return a shell coroutine that expects *send* and writes *reply*."""
    waiter: asyncio.Future[bool] = asyncio.Future()

    async def shell(reader, writer):
        inp = await reader.readexactly(len(send))
        assert inp == send
        writer.write(reply)
        await writer.drain()
        waiter.set_result(True)

    return shell, waiter


_FAST_CLIENT = dict(encoding="ascii", connect_minwait=0.05, connect_maxwait=0.5)


async def _ping_pong(bind_host, port, server_kw, client_kw):
    """Start server with *server_kw*, connect with *client_kw*, exchange ping/pong."""
    shell, waiter = _echo_shell("ping", "pong")
    async with create_server(host=bind_host, port=port, shell=shell, **server_kw):
        async with open_connection(bind_host, port, **_FAST_CLIENT, **client_kw) as (
            reader,
            writer,
        ):
            writer.write("ping")
            await writer.drain()
            await asyncio.wait_for(waiter, 2.0)
            result = await asyncio.wait_for(reader.readexactly(4), 2.0)
            assert result == "pong"


@pytest.mark.parametrize(
    "server_kw, client_kw",
    [
        pytest.param(
            {"ssl": "server_ssl_ctx"},
            {"ssl": "client_ssl_ctx", "server_hostname": "localhost"},
            id="tls-only",
        ),
        pytest.param(
            {"ssl": "server_ssl_ctx", "tls_auto": True},
            {"ssl": "client_ssl_ctx", "server_hostname": "localhost"},
            id="tls-auto-tls-client",
        ),
        pytest.param({"ssl": "server_ssl_ctx", "tls_auto": True}, {}, id="tls-auto-plain-client"),
    ],
)
async def test_ping_pong(
    bind_host, unused_tcp_port, server_ssl_ctx, client_ssl_ctx, server_kw, client_kw
):
    """Server accepts client, shell runs, ping/pong data exchanged."""
    fixtures = {"server_ssl_ctx": server_ssl_ctx, "client_ssl_ctx": client_ssl_ctx}
    resolved_server = {
        k: fixtures.get(v, v) if isinstance(v, str) else v for k, v in server_kw.items()
    }
    resolved_client = {
        k: fixtures.get(v, v) if isinstance(v, str) else v for k, v in client_kw.items()
    }
    await _ping_pong(bind_host, unused_tcp_port, resolved_server, resolved_client)


async def test_tls_auto_both_clients(bind_host, unused_tcp_port, server_ssl_ctx, client_ssl_ctx):
    """Both TLS and plain clients connect sequentially to a tls_auto server."""
    _tls_ok: asyncio.Future[bool] = asyncio.Future()
    _plain_ok: asyncio.Future[bool] = asyncio.Future()

    async def shell(reader, writer):
        inp = await reader.readexactly(3)
        if inp == "tls":
            _tls_ok.set_result(True)
        elif inp == "raw":
            _plain_ok.set_result(True)
        writer.write("ok")
        await writer.drain()

    async with create_server(
        host=bind_host, port=unused_tcp_port, shell=shell, ssl=server_ssl_ctx, tls_auto=True
    ):
        async with open_connection(
            bind_host,
            unused_tcp_port,
            ssl=client_ssl_ctx,
            server_hostname="localhost",
            **_FAST_CLIENT,
        ) as (reader, writer):
            writer.write("tls")
            await writer.drain()
            await asyncio.wait_for(_tls_ok, 2.0)

        async with open_connection(bind_host, unused_tcp_port, **_FAST_CLIENT) as (reader, writer):
            writer.write("raw")
            await writer.drain()
            await asyncio.wait_for(_plain_ok, 2.0)


@pytest.mark.parametrize(
    "client_ssl",
    [
        pytest.param(lambda: ssl.create_default_context(), id="untrusted-ctx"),
        pytest.param(lambda: True, id="ssl-true"),
    ],
)
async def test_tls_cert_rejection(bind_host, unused_tcp_port, server_ssl_ctx, client_ssl):
    """Client without CA trust or with ssl=True rejects the server cert."""

    async def shell(reader, writer):
        pass

    async with create_server(host=bind_host, port=unused_tcp_port, shell=shell, ssl=server_ssl_ctx):
        with pytest.raises(ssl.SSLCertVerificationError):
            async with open_connection(
                bind_host,
                unused_tcp_port,
                ssl=client_ssl(),
                server_hostname="localhost",
                **_FAST_CLIENT,
            ):
                pass


async def test_plain_client_rejected_by_tls_server(bind_host, unused_tcp_port, server_ssl_ctx):
    """Plain TCP client cannot connect to a TLS-only server."""

    async def shell(reader, writer):
        pass

    async with create_server(host=bind_host, port=unused_tcp_port, shell=shell, ssl=server_ssl_ctx):
        reader, writer = await asyncio.open_connection(host=bind_host, port=unused_tcp_port)
        try:
            writer.write(b"hello\r\n")
            await writer.drain()
            data = await asyncio.wait_for(reader.read(1024), 2.0)
            assert data == b""
        except (ConnectionResetError, OSError):
            pass
        finally:
            writer.close()
            await writer.wait_closed()


async def test_tls_server_hostname_defaults_to_host(
    bind_host, unused_tcp_port, server_ssl_ctx, client_ssl_ctx
):
    """When server_hostname is omitted, it defaults to host."""
    waiter: asyncio.Future[bool] = asyncio.Future()

    async def shell(reader, writer):
        waiter.set_result(True)

    async with create_server(host=bind_host, port=unused_tcp_port, shell=shell, ssl=server_ssl_ctx):
        async with open_connection(
            "localhost", unused_tcp_port, ssl=client_ssl_ctx, **_FAST_CLIENT
        ) as (reader, writer):
            await asyncio.wait_for(waiter, 2.0)
            writer.close()


async def test_tls_fingerprint_end_to_end(
    bind_host, unused_tcp_port, server_ssl_ctx, client_ssl_ctx
):
    """Fingerprint client connects to TLS server via open_connection."""
    waiter: asyncio.Future[bool] = asyncio.Future()

    async def shell(reader, writer):
        writer.write(b"banner\r\n")
        await writer.drain()
        waiter.set_result(True)

    async with create_server(
        host=bind_host, port=unused_tcp_port, shell=shell, encoding=False, ssl=server_ssl_ctx
    ):
        async with open_connection(
            bind_host,
            unused_tcp_port,
            ssl=client_ssl_ctx,
            server_hostname="localhost",
            encoding=False,
            connect_minwait=0.05,
            connect_maxwait=0.5,
        ) as (reader, writer):
            await asyncio.wait_for(waiter, 2.0)
            data = await asyncio.wait_for(reader.read(1024), 2.0)
            assert b"banner" in data
            writer.close()


async def test_tls_auto_requires_ssl_context():
    """tls_auto=True without ssl raises ValueError."""
    with pytest.raises(ValueError, match="tls_auto.*requires"):
        async with create_server(host="localhost", port=0, tls_auto=True):
            pass


def _write_cert_files(ca, tmp_path):
    """Write PEM cert+key files, return (cert_path, key_path)."""
    cert = ca.issue_cert("127.0.0.1", "localhost")
    cert_pem = tmp_path / "cert.pem"
    key_pem = tmp_path / "key.pem"
    cert.private_key_pem.write_to_path(str(key_pem))
    with open(str(cert_pem), "wb") as f:
        for blob in cert.cert_chain_pems:
            f.write(blob.bytes())
    return str(cert_pem), str(key_pem)


@contextlib.contextmanager
def _override_argv(argv):
    """Temporarily replace sys.argv."""
    orig = sys.argv
    sys.argv = argv
    try:
        yield
    finally:
        sys.argv = orig


@pytest.mark.parametrize(
    "extra_argv, expect_key, expect_val",
    [
        pytest.param([], "ssl", None, id="no-ssl"),
        pytest.param([], "tls_auto", False, id="no-tls-auto"),
    ],
)
async def test_server_cli_defaults(extra_argv, expect_key, expect_val):
    """Server arg parser defaults for SSL/TLS options."""
    from telnetlib3.server import parse_server_args

    with _override_argv(["prog", "localhost", "6023"] + extra_argv):
        result = parse_server_args()
    assert result[expect_key] == expect_val


async def test_server_ssl_certfile_cli_args(tmp_path, ca):
    """--ssl-certfile produces SSLContext in parsed server args."""
    from telnetlib3.server import parse_server_args

    cert_pem, key_pem = _write_cert_files(ca, tmp_path)
    with _override_argv(
        ["prog", "--ssl-certfile", cert_pem, "--ssl-keyfile", key_pem, "localhost", "6023"]
    ):
        result = parse_server_args()
    assert isinstance(result["ssl"], ssl.SSLContext)


async def test_tls_auto_cli_args(tmp_path, ca):
    """--tls-auto flag is parsed and passed through."""
    from telnetlib3.server import parse_server_args

    cert_pem, key_pem = _write_cert_files(ca, tmp_path)
    with _override_argv(
        [
            "prog",
            "--ssl-certfile",
            cert_pem,
            "--ssl-keyfile",
            key_pem,
            "--tls-auto",
            "localhost",
            "6023",
        ]
    ):
        result = parse_server_args()
    assert result["tls_auto"] is True
    assert isinstance(result["ssl"], ssl.SSLContext)


@pytest.mark.parametrize(
    "argv, key, check",
    [
        pytest.param(
            ["--ssl", "example.com", "992"],
            "ssl",
            lambda v: isinstance(v, ssl.SSLContext),
            id="client-ssl",
        ),
        pytest.param(["example.com"], "ssl", lambda v: v is None, id="client-no-ssl"),
        pytest.param(
            ["--ssl-no-verify", "example.com", "992"],
            "ssl",
            lambda v: isinstance(v, ssl.SSLContext)
            and not v.check_hostname
            and v.verify_mode == ssl.CERT_NONE,  # noqa: E501
            id="client-no-verify",
        ),
        pytest.param(["--raw-mode", "example.com"], "raw_mode", lambda v: v is True, id="raw-mode"),
        pytest.param(
            ["--line-mode", "example.com"], "raw_mode", lambda v: v is False, id="line-mode"
        ),
        pytest.param(["example.com"], "raw_mode", lambda v: v is None, id="default-mode"),
        pytest.param(
            ["--ascii-eol", "example.com"], "ascii_eol", lambda v: v is True, id="ascii-eol"
        ),
        pytest.param(
            ["--ansi-keys", "example.com"], "ansi_keys", lambda v: v is True, id="ansi-keys"
        ),
    ],
)
async def test_client_cli_args(argv, key, check):
    """Client argument parser produces expected transformed values."""
    from telnetlib3.client import _transform_args, _get_argument_parser

    result = _transform_args(_get_argument_parser().parse_args(argv))
    assert check(result.get(key))


async def test_client_ssl_cafile_cli_args(tmp_path, ca):
    """--ssl --ssl-cafile produces SSLContext with custom CA."""
    from telnetlib3.client import _transform_args, _get_argument_parser

    ca_pem = tmp_path / "ca.pem"
    ca.cert_pem.write_to_path(str(ca_pem))
    args = _get_argument_parser().parse_args(
        ["--ssl", "--ssl-cafile", str(ca_pem), "example.com", "992"]
    )
    assert isinstance(_transform_args(args)["ssl"], ssl.SSLContext)


async def test_fingerprint_ssl_cli_args():
    """--ssl flag is accepted by fingerprint argument parser."""
    from telnetlib3.client import _get_fingerprint_argument_parser

    args = _get_fingerprint_argument_parser().parse_args(["--ssl", "example.com", "992"])
    assert args.ssl is True
    assert args.ssl_cafile is None


async def test_fingerprint_ssl_cafile_cli_args(tmp_path, ca):
    """--ssl --ssl-cafile accepted by fingerprint argument parser."""
    from telnetlib3.client import _get_fingerprint_argument_parser

    ca_pem = tmp_path / "ca.pem"
    ca.cert_pem.write_to_path(str(ca_pem))
    args = _get_fingerprint_argument_parser().parse_args(
        ["--ssl", "--ssl-cafile", str(ca_pem), "example.com", "992"]
    )
    assert args.ssl is True
    assert args.ssl_cafile == str(ca_pem)


@pytest.mark.parametrize(
    "input_val, expected",
    [
        pytest.param("TTYPE", bytes([24]), id="by-name"),
        pytest.param("91", bytes([91]), id="by-number"),
    ],
)
async def test_parse_option_arg(input_val, expected):
    """_parse_option_arg resolves option names and numbers."""
    from telnetlib3.client import _parse_option_arg

    assert _parse_option_arg(input_val) == expected


_MAX_SUBPROC_SECONDS = 8


def _read_until_eof(fd: int, encoding: str = "utf8") -> str:
    decoder = codecs.getincrementaldecoder(encoding)()
    outp = ""
    while True:
        try:
            chunk = os.read(fd, 4096)
        except OSError:
            break
        if not chunk:
            break
        outp += decoder.decode(chunk, final=False)
    return outp


def _run_in_pty(child_func, timeout: float = _MAX_SUBPROC_SECONDS) -> str:
    """Fork a PTY, run *child_func* in the child with coverage, return output."""
    if sys.platform == "win32":
        pytest.skip("POSIX-only test")

    import pty  # pylint: disable=import-outside-toplevel

    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=DeprecationWarning)
        pid, master_fd = pty.fork()

    if pid == 0:
        cov = init_subproc_coverage("tls-pty-test")
        exit_code = 0
        try:
            child_func()
        except SystemExit:
            pass
        except BaseException:
            import traceback  # pylint: disable=import-outside-toplevel

            traceback.print_exc()
            exit_code = 1
        finally:
            if cov is not None:
                cov.stop()
                cov.save()
        os._exit(exit_code)

    output = _read_until_eof(master_fd)
    os.close(master_fd)

    start = _time.monotonic()
    while True:
        pid_result, status = os.waitpid(pid, os.WNOHANG)
        if pid_result != 0:
            break
        if _time.monotonic() - start > timeout:
            try:
                os.kill(pid, signal.SIGKILL)
                os.waitpid(pid, 0)
            except OSError:
                pass
            raise AssertionError(f"Child hung after {timeout}s.\nOutput:\n{output}")
        _time.sleep(0.05)

    assert os.WEXITSTATUS(status) == 0, f"Child exited {os.WEXITSTATUS(status)}.\nOutput:\n{output}"
    return output


class _EchoClose(asyncio.Protocol):
    """Write a message then close after a short delay."""

    def __init__(self, msg: bytes):
        self._msg = msg
        self._transport: asyncio.BaseTransport | None = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self._transport = transport
        transport.write(self._msg)
        asyncio.get_event_loop().call_later(0.3, transport.close)


@contextlib.contextmanager
def _threaded_echo_server(host, port, msg=b"ok\r\n", ssl_ctx=None):
    """Run a simple echo-and-close TCP server on a background thread."""
    srv_loop = asyncio.new_event_loop()
    ready = threading.Event()
    stop_holder: list[asyncio.Event] = []
    accepted: list[asyncio.BaseTransport] = []

    def _run():
        asyncio.set_event_loop(srv_loop)

        async def _serve():
            def _factory():
                proto = _EchoClose(msg)
                accepted.append(proto)
                return proto

            kwargs = {"ssl": ssl_ctx} if ssl_ctx else {}
            srv = await srv_loop.create_server(_factory, host, port, **kwargs)
            stop_evt = asyncio.Event()
            stop_holder.append(stop_evt)
            ready.set()
            try:
                await stop_evt.wait()
            finally:
                for proto in accepted:
                    tr = proto._transport
                    if tr is not None and not tr.is_closing():
                        tr.abort()
                srv.close()
                await srv.wait_closed()

        srv_loop.run_until_complete(_serve())

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    ready.wait(timeout=5)
    try:
        yield
    finally:
        if stop_holder:
            srv_loop.call_soon_threadsafe(stop_holder[0].set)
        thread.join(timeout=3)
        srv_loop.close()


def _pty_run_client(bind_host, port, extra_argv, server_ssl_ctx=None):
    """Start a plain/TLS echo server, fork, run run_client() in the child."""
    marker = b"pty-marker-ok"

    def _child():
        sys.argv = [
            "telnetlib3-client",
            bind_host,
            str(port),
            "--connect-minwait=0.05",
            "--connect-maxwait=0.5",
            "--colormatch=none",
        ] + extra_argv
        from telnetlib3.client import run_client  # pylint: disable=import-outside-toplevel

        asyncio.run(run_client())

    with _threaded_echo_server(bind_host, port, marker + b"\r\n", server_ssl_ctx):
        output = _run_in_pty(_child)

    assert "pty-marker-ok" in output, f"Marker not found in output:\n{output}"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only tests")
@pytest.mark.parametrize(
    "extra_argv, use_ssl",
    [
        pytest.param(["--ssl-no-verify"], True, id="ssl-no-verify"),
        pytest.param(["--raw-mode"], False, id="raw-mode"),
        pytest.param(["--raw-mode", "--ascii-eol", "--ansi-keys"], False, id="ascii-eol-ansi-keys"),
        pytest.param(["--raw-mode", "--encoding=atascii"], False, id="atascii"),
    ],
)
def test_cli_run_client(bind_host, unused_tcp_port, server_ssl_ctx, extra_argv, use_ssl):
    """run_client() with various CLI flags exercises expected code paths."""
    ssl_ctx = server_ssl_ctx if use_ssl else None
    _pty_run_client(bind_host, unused_tcp_port, extra_argv, ssl_ctx)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only tests")
def test_cli_run_server_ssl(bind_host, unused_tcp_port, ca, tmp_path, client_ssl_ctx):
    """run_server() with --ssl-certfile exercises the ssl pass-through."""
    import pty  # pylint: disable=import-outside-toplevel

    port = unused_tcp_port
    cert_pem, key_pem = _write_cert_files(ca, tmp_path)

    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=DeprecationWarning)
        pid, master_fd = pty.fork()

    if pid == 0:
        cov = init_subproc_coverage("tls-server-pty")
        exit_code = 0
        try:
            sys.argv = [
                "telnetlib3-server",
                bind_host,
                str(port),
                "--ssl-certfile",
                cert_pem,
                "--ssl-keyfile",
                key_pem,
                "--connect-maxwait=0.05",
                "--loglevel=warning",
            ]
            from telnetlib3.server import main  # pylint: disable=import-outside-toplevel

            main()
        except SystemExit:
            pass
        except BaseException:
            import traceback  # pylint: disable=import-outside-toplevel

            traceback.print_exc()
            exit_code = 1
        finally:
            if cov is not None:
                cov.stop()
                cov.save()
        os._exit(exit_code)

    try:
        _time.sleep(0.5)

        async def _connect_and_close():
            async with open_connection(
                bind_host, port, ssl=client_ssl_ctx, server_hostname="localhost", **_FAST_CLIENT
            ) as (reader, writer):
                pass

        asyncio.run(_connect_and_close())
    finally:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
        try:
            os.waitpid(pid, 0)
        except ChildProcessError:
            pass
        os.close(master_fd)


def _pty_run_fingerprint(bind_host, port, extra_argv, server_ssl_ctx):
    """Run fingerprint client in PTY against echo server."""

    def _child():
        sys.argv = [
            "telnetlib3-fingerprint",
            "--connect-timeout=3",
            bind_host,
            str(port),
        ] + extra_argv
        from telnetlib3.client import run_fingerprint_client  # noqa: PLC0415

        asyncio.run(run_fingerprint_client())

    with _threaded_echo_server(bind_host, port, b"fingerprint-ok\r\n", server_ssl_ctx):
        _run_in_pty(_child)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only tests")
@pytest.mark.parametrize(
    "extra_argv_factory",
    [
        pytest.param(lambda ca_pem: ["--ssl-no-verify"], id="no-verify"),
        pytest.param(lambda ca_pem: ["--ssl", "--ssl-cafile", ca_pem], id="cafile"),
    ],
)
def test_cli_run_fingerprint_client_ssl(
    bind_host, unused_tcp_port, server_ssl_ctx, ca, tmp_path, extra_argv_factory
):
    """run_fingerprint_client() with TLS flags exercises context paths."""
    ca_pem = str(tmp_path / "ca.pem")
    ca.cert_pem.write_to_path(ca_pem)
    extra_argv = extra_argv_factory(ca_pem)
    _pty_run_fingerprint(bind_host, unused_tcp_port, extra_argv, server_ssl_ctx)
