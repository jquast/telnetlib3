"""Test Server encoding mixin."""

# std imports
import asyncio

# 3rd party
import pytest

# local
# local imports
import telnetlib3
import telnetlib3.stream_writer
from telnetlib3.tests.accessories import bind_host, unused_tcp_port


async def test_telnet_server_encoding_default(bind_host, unused_tcp_port):
    """Default encoding US-ASCII unless it can be negotiated/confirmed!"""
    # local
    from telnetlib3.telopt import IAC, WONT, TTYPE
    from telnetlib3.tests.accessories import create_server, asyncio_connection

    _waiter = asyncio.Future()

    async with create_server(
        host=bind_host,
        port=unused_tcp_port,
        _waiter_connected=_waiter,
        connect_maxwait=0.05,
    ):
        async with asyncio_connection(bind_host, unused_tcp_port) as (reader, writer):
            writer.write(IAC + WONT + TTYPE)

            srv_instance = await asyncio.wait_for(_waiter, 0.5)
            assert srv_instance.encoding(incoming=True) == "US-ASCII"
            assert srv_instance.encoding(outgoing=True) == "US-ASCII"
            assert srv_instance.encoding(incoming=True, outgoing=True) == "US-ASCII"
            with pytest.raises(TypeError):
                srv_instance.encoding()


async def test_telnet_client_encoding_default(bind_host, unused_tcp_port):
    """Default encoding US-ASCII unless it can be negotiated/confirmed!"""
    # local
    from telnetlib3.tests.accessories import asyncio_server, open_connection

    async with asyncio_server(asyncio.Protocol, bind_host, unused_tcp_port):
        async with open_connection(host=bind_host, port=unused_tcp_port, connect_minwait=0.05) as (
            reader,
            writer,
        ):
            assert writer.protocol.encoding(incoming=True) == "US-ASCII"
            assert writer.protocol.encoding(outgoing=True) == "US-ASCII"
            assert writer.protocol.encoding(incoming=True, outgoing=True) == "US-ASCII"
            with pytest.raises(TypeError):
                writer.protocol.encoding()


async def test_telnet_server_encoding_client_will(bind_host, unused_tcp_port):
    """Server Default encoding (utf8) incoming when client WILL."""
    # local
    from telnetlib3.telopt import IAC, WILL, WONT, TTYPE, BINARY
    from telnetlib3.tests.accessories import create_server, asyncio_connection

    _waiter = asyncio.Future()

    async with create_server(host=bind_host, port=unused_tcp_port, _waiter_connected=_waiter):
        async with asyncio_connection(bind_host, unused_tcp_port) as (reader, writer):
            writer.write(IAC + WILL + BINARY)
            writer.write(IAC + WONT + TTYPE)

            srv_instance = await asyncio.wait_for(_waiter, 0.5)
            assert srv_instance.encoding(incoming=True) == "utf8"
            assert srv_instance.encoding(outgoing=True) == "US-ASCII"
            assert srv_instance.encoding(incoming=True, outgoing=True) == "US-ASCII"


async def test_telnet_server_encoding_server_do(bind_host, unused_tcp_port):
    """Server's default encoding."""
    # local
    from telnetlib3.telopt import DO, IAC, WONT, TTYPE, BINARY
    from telnetlib3.tests.accessories import create_server, asyncio_connection

    _waiter = asyncio.Future()

    async with create_server(host=bind_host, port=unused_tcp_port, _waiter_connected=_waiter):
        async with asyncio_connection(bind_host, unused_tcp_port) as (reader, writer):
            writer.write(IAC + DO + BINARY)
            writer.write(IAC + WONT + TTYPE)

            srv_instance = await asyncio.wait_for(_waiter, 0.5)
            assert srv_instance.encoding(incoming=True) == "US-ASCII"
            assert srv_instance.encoding(outgoing=True) == "utf8"
            assert srv_instance.encoding(incoming=True, outgoing=True) == "US-ASCII"


async def test_telnet_server_encoding_bidirectional(bind_host, unused_tcp_port):
    """Server's default encoding with bi-directional BINARY negotiation."""
    # local
    from telnetlib3.telopt import DO, IAC, WILL, WONT, TTYPE, BINARY
    from telnetlib3.tests.accessories import create_server, asyncio_connection

    _waiter = asyncio.Future()

    async with create_server(
        host=bind_host,
        port=unused_tcp_port,
        _waiter_connected=_waiter,
        connect_maxwait=0.05,
    ):
        async with asyncio_connection(bind_host, unused_tcp_port) as (reader, writer):
            writer.write(IAC + DO + BINARY)
            writer.write(IAC + WILL + BINARY)
            writer.write(IAC + WONT + TTYPE)

            srv_instance = await asyncio.wait_for(_waiter, 0.5)
            assert srv_instance.encoding(incoming=True) == "utf8"
            assert srv_instance.encoding(outgoing=True) == "utf8"
            assert srv_instance.encoding(incoming=True, outgoing=True) == "utf8"


async def test_telnet_client_and_server_encoding_bidirectional(bind_host, unused_tcp_port):
    """Given a default encoding for client and server, client always wins!"""
    # local
    from telnetlib3.tests.accessories import create_server, open_connection

    _waiter = asyncio.Future()

    async with create_server(
        host=bind_host,
        port=unused_tcp_port,
        _waiter_connected=_waiter,
        encoding="latin1",
        connect_maxwait=1.0,
    ):
        async with open_connection(
            host=bind_host, port=unused_tcp_port, encoding="cp437", connect_minwait=1.0
        ) as (reader, writer):
            srv_instance = await asyncio.wait_for(_waiter, 1.5)

            assert srv_instance.encoding(incoming=True) == "cp437"
            assert srv_instance.encoding(outgoing=True) == "cp437"
            assert srv_instance.encoding(incoming=True, outgoing=True) == "cp437"
            assert writer.protocol.encoding(incoming=True) == "cp437"
            assert writer.protocol.encoding(outgoing=True) == "cp437"
            assert writer.protocol.encoding(incoming=True, outgoing=True) == "cp437"


async def test_telnet_server_encoding_by_LANG(bind_host, unused_tcp_port):
    """Server's encoding negotiated by LANG value."""
    # local
    from telnetlib3.telopt import DO, IS, SB, SE, IAC, WILL, WONT, TTYPE, BINARY, NEW_ENVIRON
    from telnetlib3.tests.accessories import create_server, asyncio_connection

    _waiter = asyncio.Future()

    async with create_server(host=bind_host, port=unused_tcp_port, _waiter_connected=_waiter):
        async with asyncio_connection(bind_host, unused_tcp_port) as (reader, writer):
            writer.write(IAC + DO + BINARY)
            writer.write(IAC + WILL + BINARY)
            writer.write(IAC + WILL + NEW_ENVIRON)
            writer.write(
                IAC
                + SB
                + NEW_ENVIRON
                + IS
                + telnetlib3.stream_writer._encode_env_buf(
                    {
                        "LANG": "uk_UA.KOI8-U",
                    }
                )
                + IAC
                + SE
            )
            writer.write(IAC + WONT + TTYPE)

            srv_instance = await asyncio.wait_for(_waiter, 0.5)
            assert srv_instance.encoding(incoming=True) == "KOI8-U"
            assert srv_instance.encoding(outgoing=True) == "KOI8-U"
            assert srv_instance.encoding(incoming=True, outgoing=True) == "KOI8-U"
            assert srv_instance.get_extra_info("LANG") == "uk_UA.KOI8-U"


async def test_telnet_server_binary_mode(bind_host, unused_tcp_port):
    """Server's encoding=False creates a binary reader/writer interface."""
    # local
    from telnetlib3.telopt import DO, IAC, WONT, TTYPE
    from telnetlib3.tests.accessories import create_server, asyncio_connection

    _waiter = asyncio.Future()

    async def binary_shell(reader, writer):
        writer.write(b"server_output")

        val = await reader.readexactly(1)
        assert val == b"c"
        val = await reader.readexactly(len(b"lient "))
        assert val == b"lient "
        writer.close()
        val = await reader.read()
        assert val == b"output"

    async with create_server(
        host=bind_host,
        port=unused_tcp_port,
        shell=binary_shell,
        _waiter_connected=_waiter,
        encoding=False,
    ):
        async with asyncio_connection(bind_host, unused_tcp_port) as (reader, writer):
            val = await reader.readexactly(len(IAC + DO + TTYPE))
            assert val == IAC + DO + TTYPE

            writer.write(IAC + WONT + TTYPE)
            writer.write(b"client output")

            val = await reader.readexactly(len(b"server_output"))
            assert val == b"server_output"

            eof = await reader.read()
            assert eof == b""


async def test_telnet_client_and_server_escape_iac_encoding(bind_host, unused_tcp_port):
    """Ensure that IAC (byte 255) may be sent across the wire by encoding."""
    # local
    from telnetlib3.tests.accessories import create_server, open_connection

    _waiter = asyncio.Future()
    given_string = "".join(chr(val) for val in list(range(256))) * 2

    async with create_server(
        host=bind_host,
        port=unused_tcp_port,
        _waiter_connected=_waiter,
        encoding="iso8859-1",
        connect_maxwait=0.05,
    ):
        async with open_connection(
            host=bind_host, port=unused_tcp_port, encoding="iso8859-1", connect_minwait=0.05
        ) as (client_reader, client_writer):
            server = await asyncio.wait_for(_waiter, 0.5)

            server.writer.write(given_string)
            result = await client_reader.readexactly(len(given_string))
            assert result == given_string
            server.writer.close()
            eof = await asyncio.wait_for(client_reader.read(), 0.5)
            assert eof == ""


async def test_telnet_client_and_server_escape_iac_binary(bind_host, unused_tcp_port):
    """Ensure that IAC (byte 255) may be sent across the wire in binary."""
    # local
    from telnetlib3.tests.accessories import create_server, open_connection

    _waiter = asyncio.Future()
    given_string = bytes(range(256)) * 2

    async with create_server(
        host=bind_host,
        port=unused_tcp_port,
        _waiter_connected=_waiter,
        encoding=False,
        connect_maxwait=0.05,
    ):
        async with open_connection(
            host=bind_host, port=unused_tcp_port, encoding=False, connect_minwait=0.05
        ) as (client_reader, client_writer):
            server = await asyncio.wait_for(_waiter, 0.5)

            server.writer.write(given_string)
            result = await client_reader.readexactly(len(given_string))
            assert result == given_string
            server.writer.close()
            eof = await asyncio.wait_for(client_reader.read(), 0.5)
            assert eof == b""
