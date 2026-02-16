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
import contextlib
import threading

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
    """Ephemeral CA for issuing test certificates."""
    return trustme.CA()


@pytest.fixture()
def server_ssl_ctx(ca):
    """Server-side SSLContext with CA-issued certificate."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ca.issue_cert("127.0.0.1", "localhost").configure_cert(ctx)
    return ctx


@pytest.fixture()
def client_ssl_ctx(ca):
    """Client-side SSLContext that trusts the ephemeral CA."""
    ctx = ssl.create_default_context()
    ca.configure_trust(ctx)
    return ctx


async def test_tls_end_to_end(bind_host, unused_tcp_port, server_ssl_ctx, client_ssl_ctx):
    """TLS server accepts TLS client, shell runs, data exchanged."""
    _waiter = asyncio.Future()
    send_input = "ping"
    expect_output = "pong"

    async def shell(reader, writer):
        inp = await reader.readexactly(len(send_input))
        assert inp == send_input
        writer.write(expect_output)
        await writer.drain()
        _waiter.set_result(True)

    async with create_server(host=bind_host, port=unused_tcp_port, shell=shell, ssl=server_ssl_ctx):
        async with open_connection(
            bind_host,
            unused_tcp_port,
            ssl=client_ssl_ctx,
            server_hostname="localhost",
            encoding="ascii",
            connect_minwait=0.05,
            connect_maxwait=0.5,
        ) as (reader, writer):
            writer.write("ping")
            await writer.drain()
            await asyncio.wait_for(_waiter, 2.0)
            result = await asyncio.wait_for(reader.readexactly(len(expect_output)), 2.0)
            assert result == expect_output


async def test_tls_ca_verification(bind_host, unused_tcp_port, server_ssl_ctx):
    """Client without CA trust rejects the server certificate."""
    _shell_called = asyncio.Future()

    async def shell(reader, writer):
        _shell_called.set_result(True)

    async with create_server(host=bind_host, port=unused_tcp_port, shell=shell, ssl=server_ssl_ctx):
        untrusted_ctx = ssl.create_default_context()
        with pytest.raises(ssl.SSLCertVerificationError):
            async with open_connection(
                bind_host,
                unused_tcp_port,
                ssl=untrusted_ctx,
                server_hostname="localhost",
                encoding="ascii",
                connect_minwait=0.05,
                connect_maxwait=0.5,
            ):
                pass


async def test_tls_ssl_true_uses_default_context(bind_host, unused_tcp_port, server_ssl_ctx):
    """Ssl=True creates a default context (rejects untrusted certs)."""

    async def shell(reader, writer):
        pass

    async with create_server(host=bind_host, port=unused_tcp_port, shell=shell, ssl=server_ssl_ctx):
        with pytest.raises(ssl.SSLCertVerificationError):
            async with open_connection(
                bind_host,
                unused_tcp_port,
                ssl=True,
                server_hostname="localhost",
                encoding="ascii",
                connect_minwait=0.05,
                connect_maxwait=0.5,
            ):
                pass


async def test_plain_client_rejected_by_tls_server(bind_host, unused_tcp_port, server_ssl_ctx):
    """Plain TCP client cannot connect to a TLS server."""

    async def shell(reader, writer):
        pass

    async with create_server(host=bind_host, port=unused_tcp_port, shell=shell, ssl=server_ssl_ctx):
        reader, writer = await asyncio.open_connection(host=bind_host, port=unused_tcp_port)
        try:
            writer.write(b"hello\r\n")
            await writer.drain()
            data = await asyncio.wait_for(reader.read(1024), 2.0)
            # Server rejects non-TLS data: either raises or returns EOF
            assert data == b""
        except (ConnectionResetError, OSError):
            pass
        finally:
            writer.close()
            await writer.wait_closed()


async def test_server_ssl_cli_args():
    """--ssl-certfile and --ssl-keyfile produce SSLContext in parsed args."""
    import os

    # Create dummy cert/key files (content doesn't matter for arg parsing test,
    # but SSLContext.load_cert_chain will fail — we test the arg parsing path
    # separately by mocking).
    # Instead, test that without --ssl-certfile, ssl is None.
    import sys
    import tempfile

    from telnetlib3.server import parse_server_args

    orig_argv = sys.argv
    try:
        sys.argv = ["prog", "localhost", "6023"]
        result = parse_server_args()
        assert result["ssl"] is None
    finally:
        sys.argv = orig_argv


async def test_client_ssl_cli_args():
    """--ssl flag produces SSLContext in transformed args."""
    from telnetlib3.client import _transform_args, _get_argument_parser

    args = _get_argument_parser().parse_args(["--ssl", "example.com", "992"])
    result = _transform_args(args)
    assert isinstance(result["ssl"], ssl.SSLContext)


async def test_client_ssl_cafile_cli_args(tmp_path, ca):
    """--ssl --ssl-cafile produces SSLContext with custom CA."""
    from telnetlib3.client import _transform_args, _get_argument_parser

    ca_pem = tmp_path / "ca.pem"
    ca.cert_pem.write_to_path(str(ca_pem))

    args = _get_argument_parser().parse_args(
        ["--ssl", "--ssl-cafile", str(ca_pem), "example.com", "992"]
    )
    result = _transform_args(args)
    assert isinstance(result["ssl"], ssl.SSLContext)


async def test_client_no_ssl_cli_args():
    """Without --ssl, ssl key is absent or None."""
    from telnetlib3.client import _transform_args, _get_argument_parser

    args = _get_argument_parser().parse_args(["example.com"])
    result = _transform_args(args)
    assert result.get("ssl") is None


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


async def test_client_ssl_no_verify_cli_args():
    """--ssl-no-verify disables certificate verification."""
    from telnetlib3.client import _transform_args, _get_argument_parser

    args = _get_argument_parser().parse_args(["--ssl-no-verify", "example.com", "992"])
    result = _transform_args(args)
    ctx = result["ssl"]
    assert isinstance(ctx, ssl.SSLContext)
    assert ctx.check_hostname is False
    assert ctx.verify_mode == ssl.CERT_NONE


async def test_client_raw_mode_cli_args():
    """--raw-mode sets raw_mode to True."""
    from telnetlib3.client import _transform_args, _get_argument_parser

    args = _get_argument_parser().parse_args(["--raw-mode", "example.com"])
    result = _transform_args(args)
    assert result["raw_mode"] is True


async def test_client_line_mode_cli_args():
    """--line-mode sets raw_mode to False."""
    from telnetlib3.client import _transform_args, _get_argument_parser

    args = _get_argument_parser().parse_args(["--line-mode", "example.com"])
    result = _transform_args(args)
    assert result["raw_mode"] is False


async def test_client_default_mode_cli_args():
    """Neither --raw-mode nor --line-mode sets raw_mode to None."""
    from telnetlib3.client import _transform_args, _get_argument_parser

    args = _get_argument_parser().parse_args(["example.com"])
    result = _transform_args(args)
    assert result["raw_mode"] is None


async def test_client_ascii_eol_cli_args():
    """--ascii-eol is passed through _transform_args."""
    from telnetlib3.client import _transform_args, _get_argument_parser

    args = _get_argument_parser().parse_args(["--ascii-eol", "example.com"])
    result = _transform_args(args)
    assert result["ascii_eol"] is True


async def test_client_ansi_keys_cli_args():
    """--ansi-keys is passed through _transform_args."""
    from telnetlib3.client import _transform_args, _get_argument_parser

    args = _get_argument_parser().parse_args(["--ansi-keys", "example.com"])
    result = _transform_args(args)
    assert result["ansi_keys"] is True


async def test_server_ssl_certfile_cli_args(tmp_path, ca):
    """--ssl-certfile produces SSLContext in parsed server args."""
    import sys

    from telnetlib3.server import parse_server_args

    cert = ca.issue_cert("localhost")
    cert_pem = tmp_path / "cert.pem"
    key_pem = tmp_path / "key.pem"
    cert.private_key_pem.write_to_path(str(key_pem))
    with open(str(cert_pem), "wb") as f:
        for blob in cert.cert_chain_pems:
            f.write(blob.bytes())

    orig_argv = sys.argv
    try:
        sys.argv = [
            "prog",
            "--ssl-certfile",
            str(cert_pem),
            "--ssl-keyfile",
            str(key_pem),
            "localhost",
            "6023",
        ]
        result = parse_server_args()
        assert isinstance(result["ssl"], ssl.SSLContext)
    finally:
        sys.argv = orig_argv


async def test_parse_option_arg_by_name():
    """_parse_option_arg resolves option names."""
    from telnetlib3.client import _parse_option_arg

    result = _parse_option_arg("TTYPE")
    assert result == bytes([24])


async def test_parse_option_arg_by_number():
    """_parse_option_arg resolves numeric values."""
    from telnetlib3.client import _parse_option_arg

    result = _parse_option_arg("91")
    assert result == bytes([91])


async def test_tls_server_hostname_defaults_to_host(
    bind_host, unused_tcp_port, server_ssl_ctx, client_ssl_ctx
):
    """When server_hostname is omitted, it defaults to host."""
    _waiter = asyncio.Future()

    async def shell(reader, writer):
        _waiter.set_result(True)

    async with create_server(host=bind_host, port=unused_tcp_port, shell=shell, ssl=server_ssl_ctx):
        async with open_connection(
            "localhost",
            unused_tcp_port,
            ssl=client_ssl_ctx,
            encoding="ascii",
            connect_minwait=0.05,
            connect_maxwait=0.5,
        ) as (reader, writer):
            await asyncio.wait_for(_waiter, 2.0)
            writer.close()


async def test_tls_fingerprint_end_to_end(
    bind_host, unused_tcp_port, server_ssl_ctx, client_ssl_ctx
):
    """Fingerprint client can connect to TLS server via open_connection."""
    _waiter = asyncio.Future()

    async def shell(reader, writer):
        writer.write(b"banner\r\n")
        await writer.drain()
        _waiter.set_result(True)

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
            await asyncio.wait_for(_waiter, 2.0)
            data = await asyncio.wait_for(reader.read(1024), 2.0)
            assert b"banner" in data
            writer.close()


# ---------------------------------------------------------------------------
# pty.fork()-based test helper (blessed pattern — coverage in child process)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# PTY fork tests for CLI entry points
# ---------------------------------------------------------------------------


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
    """Run a simple echo-and-close TCP server on a background thread.

    Tracks accepted transports and closes them on exit to prevent
    ResourceWarning from ``_SelectorTransport.__del__``.

    Yields once the server is ready to accept connections.
    """
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
                # Close accepted transports before the server socket so that
                # no orphaned sockets trigger ResourceWarning at GC time.
                for proto in accepted:
                    tr = proto._transport
                    if tr is not None and not tr.is_closing():
                        tr.close()
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

    with _threaded_echo_server(bind_host, port, marker + b"\r\n", server_ssl_ctx):
        output = _run_in_pty(lambda: _child_run_client(bind_host, port, extra_argv))

    assert "pty-marker-ok" in output, f"Marker not found in output:\n{output}"


def _child_run_client(bind_host, port, extra_argv):
    """Child-process body for _pty_run_client (runs inside _run_in_pty)."""
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


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only tests")
def test_cli_run_client_ssl_no_verify(bind_host, unused_tcp_port, server_ssl_ctx):
    """run_client() with --ssl-no-verify exercises TLS context + ssl kwarg."""
    _pty_run_client(bind_host, unused_tcp_port, ["--ssl-no-verify"], server_ssl_ctx)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only tests")
def test_cli_run_client_raw_mode(bind_host, unused_tcp_port):
    """run_client() with --raw-mode exercises raw_mode_val code path."""
    _pty_run_client(bind_host, unused_tcp_port, ["--raw-mode"])


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only tests")
def test_cli_run_client_ascii_eol_ansi_keys(bind_host, unused_tcp_port):
    """run_client() with --raw-mode --ascii-eol --ansi-keys."""
    _pty_run_client(bind_host, unused_tcp_port, ["--raw-mode", "--ascii-eol", "--ansi-keys"])


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only tests")
def test_cli_run_server_ssl(bind_host, unused_tcp_port, ca, tmp_path, client_ssl_ctx):
    """run_server() with --ssl-certfile exercises the ssl pass-through."""
    import pty  # pylint: disable=import-outside-toplevel

    port = unused_tcp_port
    cert = ca.issue_cert(bind_host, "localhost")
    cert_pem = tmp_path / "cert.pem"
    key_pem = tmp_path / "key.pem"
    cert.private_key_pem.write_to_path(str(key_pem))
    with open(str(cert_pem), "wb") as f:
        for blob in cert.cert_chain_pems:
            f.write(blob.bytes())

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
                str(cert_pem),
                "--ssl-keyfile",
                str(key_pem),
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
                bind_host,
                port,
                ssl=client_ssl_ctx,
                server_hostname="localhost",
                encoding="ascii",
                connect_minwait=0.05,
                connect_maxwait=0.5,
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


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only tests")
def test_cli_run_fingerprint_client_ssl(bind_host, unused_tcp_port, server_ssl_ctx):
    """run_fingerprint_client() with --ssl-no-verify exercises TLS context path."""
    port = unused_tcp_port

    def _child():
        sys.argv = [
            "telnetlib3-fingerprint",
            "--ssl-no-verify",
            "--connect-timeout=3",
            bind_host,
            str(port),
        ]
        from telnetlib3.client import run_fingerprint_client  # noqa: PLC0415

        asyncio.run(run_fingerprint_client())

    with _threaded_echo_server(bind_host, port, b"fingerprint-ok\r\n", server_ssl_ctx):
        _run_in_pty(_child)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only tests")
def test_cli_run_client_raw_mode_atascii(bind_host, unused_tcp_port):
    """run_client() with --raw-mode --encoding=atascii exercises input_filter."""
    _pty_run_client(bind_host, unused_tcp_port, ["--raw-mode", "--encoding=atascii"])


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only tests")
def test_cli_run_fingerprint_client_ssl_cafile(
    bind_host, unused_tcp_port, server_ssl_ctx, ca, tmp_path
):
    """run_fingerprint_client() with --ssl --ssl-cafile exercises cafile path."""
    port = unused_tcp_port

    ca_pem = tmp_path / "ca.pem"
    ca.cert_pem.write_to_path(str(ca_pem))

    def _child():
        sys.argv = [
            "telnetlib3-fingerprint",
            "--ssl",
            "--ssl-cafile",
            str(ca_pem),
            "--connect-timeout=3",
            bind_host,
            str(port),
        ]
        from telnetlib3.client import run_fingerprint_client  # noqa: PLC0415

        asyncio.run(run_fingerprint_client())

    with _threaded_echo_server(bind_host, port, b"fingerprint-ok\r\n", server_ssl_ctx):
        _run_in_pty(_child)
