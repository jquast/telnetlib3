"""Test Server encoding mixin."""

# std imports
import asyncio

# 3rd party
import pytest

# local
import telnetlib3
import telnetlib3.stream_writer
from telnetlib3.telopt import DO, IS, SB, SE, IAC, WILL, WONT, TTYPE, BINARY, NEW_ENVIRON
from telnetlib3.tests.accessories import (  # pylint: disable=unused-import; pylint: disable=unused-import,
    bind_host,
    create_server,
    asyncio_server,
    open_connection,
    unused_tcp_port,
    asyncio_connection,
)


async def test_telnet_server_encoding_default(bind_host, unused_tcp_port):
    """Default encoding US-ASCII unless it can be negotiated/confirmed!"""
    # local
    async with create_server(
        host=bind_host,
        port=unused_tcp_port,
        connect_maxwait=0.05,
    ) as server:
        async with asyncio_connection(bind_host, unused_tcp_port) as (reader, writer):
            writer.write(IAC + WONT + TTYPE)

            srv_instance = await asyncio.wait_for(server.wait_for_client(), 0.5)
            assert srv_instance.encoding(incoming=True) == "US-ASCII"
            assert srv_instance.encoding(outgoing=True) == "US-ASCII"
            assert srv_instance.encoding(incoming=True, outgoing=True) == "US-ASCII"
            with pytest.raises(TypeError):
                srv_instance.encoding()


async def test_telnet_client_encoding_default(bind_host, unused_tcp_port):
    """Default encoding US-ASCII unless it can be negotiated/confirmed!"""
    # local
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
    async with create_server(host=bind_host, port=unused_tcp_port, connect_maxwait=0.15) as server:
        async with asyncio_connection(bind_host, unused_tcp_port) as (reader, writer):
            writer.write(IAC + WILL + BINARY)
            writer.write(IAC + WONT + TTYPE)

            srv_instance = await asyncio.wait_for(server.wait_for_client(), 0.5)
            assert srv_instance.encoding(incoming=True) == "utf8"
            assert srv_instance.encoding(outgoing=True) == "US-ASCII"
            assert srv_instance.encoding(incoming=True, outgoing=True) == "US-ASCII"


async def test_telnet_server_encoding_server_do(bind_host, unused_tcp_port):
    """Server's default encoding."""
    # local
    async with create_server(host=bind_host, port=unused_tcp_port, connect_maxwait=0.5) as server:
        async with asyncio_connection(bind_host, unused_tcp_port) as (reader, writer):
            writer.write(IAC + DO + BINARY)
            writer.write(IAC + WONT + TTYPE)
            await writer.drain()

            srv_instance = await asyncio.wait_for(server.wait_for_client(), 2.0)
            assert srv_instance.encoding(incoming=True) == "US-ASCII"
            assert srv_instance.encoding(outgoing=True) == "utf8"
            assert srv_instance.encoding(incoming=True, outgoing=True) == "US-ASCII"


async def test_telnet_server_encoding_bidirectional(bind_host, unused_tcp_port):
    """Server's default encoding with bi-directional BINARY negotiation."""
    # local
    async with create_server(
        host=bind_host,
        port=unused_tcp_port,
        connect_maxwait=0.05,
    ) as server:
        async with asyncio_connection(bind_host, unused_tcp_port) as (reader, writer):
            writer.write(IAC + DO + BINARY)
            writer.write(IAC + WILL + BINARY)
            writer.write(IAC + WONT + TTYPE)

            srv_instance = await asyncio.wait_for(server.wait_for_client(), 0.5)
            assert srv_instance.encoding(incoming=True) == "utf8"
            assert srv_instance.encoding(outgoing=True) == "utf8"
            assert srv_instance.encoding(incoming=True, outgoing=True) == "utf8"


async def test_telnet_client_and_server_encoding_bidirectional(bind_host, unused_tcp_port):
    """Given a default encoding for client and server, client always wins!"""
    # local
    async with create_server(
        host=bind_host,
        port=unused_tcp_port,
        encoding="latin1",
        connect_maxwait=1.0,
    ) as server:
        async with open_connection(
            host=bind_host, port=unused_tcp_port, encoding="cp437", connect_minwait=1.0
        ) as (reader, writer):
            srv_instance = await asyncio.wait_for(server.wait_for_client(), 1.5)

            assert srv_instance.encoding(incoming=True) == "cp437"
            assert srv_instance.encoding(outgoing=True) == "cp437"
            assert srv_instance.encoding(incoming=True, outgoing=True) == "cp437"
            assert writer.protocol.encoding(incoming=True) == "cp437"
            assert writer.protocol.encoding(outgoing=True) == "cp437"
            assert writer.protocol.encoding(incoming=True, outgoing=True) == "cp437"


async def test_telnet_server_encoding_by_LANG(bind_host, unused_tcp_port):
    """Server's encoding negotiated by LANG value."""
    # local
    async with create_server(host=bind_host, port=unused_tcp_port) as server:
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

            srv_instance = await asyncio.wait_for(server.wait_for_client(), 0.5)
            assert srv_instance.encoding(incoming=True) == "KOI8-U"
            assert srv_instance.encoding(outgoing=True) == "KOI8-U"
            assert srv_instance.encoding(incoming=True, outgoing=True) == "KOI8-U"
            assert srv_instance.get_extra_info("LANG") == "uk_UA.KOI8-U"


async def test_telnet_server_encoding_LANG_no_encoding_suffix(bind_host, unused_tcp_port):
    """Server falls back to default when LANG has no encoding suffix."""
    # local
    async with create_server(host=bind_host, port=unused_tcp_port) as server:
        async with asyncio_connection(bind_host, unused_tcp_port) as (reader, writer):
            writer.write(IAC + DO + BINARY)
            writer.write(IAC + WILL + BINARY)
            writer.write(IAC + WILL + NEW_ENVIRON)
            writer.write(
                IAC
                + SB
                + NEW_ENVIRON
                + IS
                + telnetlib3.stream_writer._encode_env_buf({"LANG": "en_IL"})
                + IAC
                + SE
            )
            writer.write(IAC + WONT + TTYPE)

            srv_instance = await asyncio.wait_for(server.wait_for_client(), 0.5)
            assert srv_instance.encoding(incoming=True) == "utf8"
            assert srv_instance.get_extra_info("LANG") == "en_IL"


async def test_telnet_server_encoding_LANG_invalid_encoding(bind_host, unused_tcp_port):
    """Server falls back to default when LANG has unknown encoding."""
    # local
    async with create_server(host=bind_host, port=unused_tcp_port) as server:
        async with asyncio_connection(bind_host, unused_tcp_port) as (reader, writer):
            writer.write(IAC + DO + BINARY)
            writer.write(IAC + WILL + BINARY)
            writer.write(IAC + WILL + NEW_ENVIRON)
            writer.write(
                IAC
                + SB
                + NEW_ENVIRON
                + IS
                + telnetlib3.stream_writer._encode_env_buf({"LANG": "en_US.BOGUS-ENCODING"})
                + IAC
                + SE
            )
            writer.write(IAC + WONT + TTYPE)

            srv_instance = await asyncio.wait_for(server.wait_for_client(), 0.5)
            assert srv_instance.encoding(incoming=True) == "utf8"
            assert srv_instance.get_extra_info("LANG") == "en_US.BOGUS-ENCODING"


async def test_telnet_server_binary_mode(bind_host, unused_tcp_port):
    """Server's encoding=False creates a binary reader/writer interface."""

    # local
    async def binary_shell(reader, writer):
        writer.write(b"server_output")

        val = await reader.readexactly(1)
        assert val == b"c"
        val = await reader.readexactly(len(b"lient "))
        assert val == b"lient "
        writer.close()
        await writer.wait_closed()
        val = await reader.read()
        assert val == b"output"

    async with create_server(
        host=bind_host,
        port=unused_tcp_port,
        shell=binary_shell,
        encoding=False,
    ) as server:
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
    given_string = "".join(chr(val) for val in list(range(256))) * 2

    async with create_server(
        host=bind_host,
        port=unused_tcp_port,
        encoding="iso8859-1",
        connect_maxwait=0.05,
    ) as server:
        async with open_connection(
            host=bind_host, port=unused_tcp_port, encoding="iso8859-1", connect_minwait=0.05
        ) as (client_reader, client_writer):
            srv_instance = await asyncio.wait_for(server.wait_for_client(), 0.5)

            srv_instance.writer.write(given_string)
            result = await client_reader.readexactly(len(given_string))
            assert result == given_string
            srv_instance.writer.close()
            await srv_instance.writer.wait_closed()
            eof = await asyncio.wait_for(client_reader.read(), 0.5)
            assert not eof


async def test_telnet_client_and_server_escape_iac_binary(bind_host, unused_tcp_port):
    """Ensure that IAC (byte 255) may be sent across the wire in binary."""
    # local
    given_string = bytes(range(256)) * 2

    async with create_server(
        host=bind_host,
        port=unused_tcp_port,
        encoding=False,
        connect_maxwait=0.05,
    ) as server:
        async with open_connection(
            host=bind_host, port=unused_tcp_port, encoding=False, connect_minwait=0.05
        ) as (client_reader, client_writer):
            srv_instance = await asyncio.wait_for(server.wait_for_client(), 0.5)

            srv_instance.writer.write(given_string)
            result = await client_reader.readexactly(len(given_string))
            assert result == given_string
            srv_instance.writer.close()
            await srv_instance.writer.wait_closed()
            eof = await asyncio.wait_for(client_reader.read(), 0.5)
            assert eof == b""
