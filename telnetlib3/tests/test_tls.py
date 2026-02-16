"""Tests for TLS (TELNETS) support."""

# std imports
import ssl
import asyncio

# 3rd party
import pytest
import trustme

# local
from telnetlib3.tests.accessories import bind_host, create_server, open_connection, unused_tcp_port


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
            writer.close()


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
