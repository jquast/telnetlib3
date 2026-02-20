# std imports
import re
import string
import asyncio

# 3rd party
import pytest

# local
import telnetlib3
from telnetlib3.tests.accessories import bind_host, create_server, open_connection, unused_tcp_port


def _fn_encoding(incoming):
    return "def-ENC"


def test_reader_instantiation_safety():
    assert repr(telnetlib3.TelnetReader(limit=1999)) == (
        "<TelnetReader limit=1999 encoding=False>"
    )


def test_reader_with_encoding_instantiation_safety():
    reader = telnetlib3.TelnetReaderUnicode(fn_encoding=_fn_encoding, limit=1999)
    assert repr(reader) == (
        "<TelnetReaderUnicode encoding='def-ENC' limit=1999 buflen=0 eof=False>"
    )


def test_reader_eof_safety():
    reader = telnetlib3.TelnetReader(limit=1999)
    reader.feed_eof()
    assert repr(reader) == "<TelnetReader eof limit=1999 encoding=False>"


def test_reader_unicode_eof_safety():
    reader = telnetlib3.TelnetReaderUnicode(fn_encoding=_fn_encoding)
    reader.feed_eof()
    assert repr(reader) == (
        "<TelnetReaderUnicode encoding='def-ENC' limit=65536 buflen=0 eof=True>"
    )


async def test_telnet_reader_using_readline_unicode(bind_host, unused_tcp_port):
    """Ensure strict RFC interpretation of newlines in readline method."""
    given_expected = {
        "alpha\r\x00": "alpha\r",
        "bravo\r\n": "bravo\r\n",
        "charlie\n": "charlie\n",
        "---\r": "---\r",
        "---\r\n": "---\r\n",
        "\r\x00": "\r",
        "\n": "\n",
        "\r\n": "\r\n",
        "xxxxxxxxxxx": "xxxxxxxxxxx",
    }

    async def shell(reader, writer):
        for item in sorted(given_expected):
            writer.write(item)
        await writer.drain()
        writer.close()

    async with create_server(
        host=bind_host, port=unused_tcp_port, connect_maxwait=0.05, shell=shell
    ):
        async with open_connection(host=bind_host, port=unused_tcp_port, connect_minwait=0.05) as (
            client_reader,
            client_writer,
        ):
            for given, expected in sorted(given_expected.items()):
                assert await asyncio.wait_for(client_reader.readline(), 0.5) == expected

            assert not await asyncio.wait_for(client_reader.read(), 0.5)


async def test_telnet_reader_using_readline_bytes(bind_host, unused_tcp_port):
    given_expected = {
        b"alpha\r\x00": b"alpha\r",
        b"bravo\r\n": b"bravo\r\n",
        b"charlie\n": b"charlie\n",
        b"---\r": b"---\r",
        b"---\r\n": b"---\r\n",
        b"\r\x00": b"\r",
        b"\n": b"\n",
        b"\r\n": b"\r\n",
        b"xxxxxxxxxxx": b"xxxxxxxxxxx",
    }

    def shell(reader, writer):
        for item in sorted(given_expected):
            writer.write(item)
        writer.close()

    async with create_server(
        host=bind_host, port=unused_tcp_port, connect_maxwait=0.05, shell=shell, encoding=False
    ):
        async with open_connection(
            host=bind_host, port=unused_tcp_port, connect_minwait=0.05, encoding=False
        ) as (client_reader, client_writer):
            for given, expected in sorted(given_expected.items()):
                assert await asyncio.wait_for(client_reader.readline(), 0.5) == expected

            assert await asyncio.wait_for(client_reader.read(), 0.5) == b""


async def test_telnet_reader_read_exactly_unicode(bind_host, unused_tcp_port):
    """Ensure TelnetReader.readexactly, especially IncompleteReadError."""
    given = "☭---------"
    given_partial = "💉-"

    def shell(reader, writer):
        writer.write(given)
        writer.write(given_partial)
        writer.close()

    async with create_server(
        host=bind_host, port=unused_tcp_port, connect_maxwait=0.05, shell=shell
    ):
        async with open_connection(host=bind_host, port=unused_tcp_port, connect_minwait=0.05) as (
            client_reader,
            client_writer,
        ):
            assert await asyncio.wait_for(
                client_reader.readexactly(len(given)), 0.5
            ) == given

            with pytest.raises(asyncio.IncompleteReadError) as exc_info:
                await asyncio.wait_for(
                    client_reader.readexactly(len(given_partial) + 1), 0.5
                )

            assert exc_info.value.partial == given_partial
            assert exc_info.value.expected == len(given_partial) + 1


async def test_telnet_reader_read_exactly_bytes(bind_host, unused_tcp_port):
    given = string.ascii_letters.encode("ascii")
    given_partial = b"zzz"

    def shell(reader, writer):
        writer.write(given + given_partial)
        writer.close()

    async with create_server(
        host=bind_host, port=unused_tcp_port, connect_maxwait=0.05, shell=shell, encoding=False
    ):
        async with open_connection(
            host=bind_host, port=unused_tcp_port, connect_minwait=0.05, encoding=False
        ) as (client_reader, client_writer):
            assert await asyncio.wait_for(
                client_reader.readexactly(len(given)), 0.5
            ) == given

            with pytest.raises(asyncio.IncompleteReadError) as exc_info:
                await asyncio.wait_for(
                    client_reader.readexactly(len(given_partial) + 1), 0.5
                )

            assert exc_info.value.partial == given_partial
            assert exc_info.value.expected == len(given_partial) + 1


async def test_telnet_reader_read_0(bind_host, unused_tcp_port):
    reader = telnetlib3.TelnetReaderUnicode(fn_encoding=_fn_encoding)
    assert not await reader.read(0)


async def test_telnet_reader_read_beyond_limit_unicode(bind_host, unused_tcp_port):
    """Ensure ability to read(-1) beyond segment sizes of reader._limit."""
    limit = 10

    def shell(reader, writer):
        assert reader._limit == limit
        given = "x" * (limit + 1)
        writer.write(given)
        writer.close()

    async with create_server(
        host=bind_host, port=unused_tcp_port, connect_maxwait=0.05, shell=shell, limit=limit
    ):
        async with open_connection(
            host=bind_host, port=unused_tcp_port, connect_minwait=0.05, limit=limit
        ) as (client_reader, client_writer):
            assert client_reader._limit == limit
            value = await asyncio.wait_for(client_reader.read(), 0.5)
            assert value == "x" * (limit + 1)


async def test_telnet_reader_read_beyond_limit_bytes(bind_host, unused_tcp_port):
    """Ensure ability to read(-1) beyond segment sizes of reader._limit."""
    limit = 10

    def shell(reader, writer):
        assert reader._limit == limit
        given = b"x" * (limit + 1)
        writer.write(given)
        writer.close()

    async with create_server(
        host=bind_host,
        port=unused_tcp_port,
        connect_maxwait=0.05,
        shell=shell,
        encoding=False,
        limit=limit,
    ):
        async with open_connection(
            host=bind_host, port=unused_tcp_port, connect_minwait=0.05, encoding=False, limit=limit
        ) as (client_reader, client_writer):
            assert client_reader._limit == limit
            value = await asyncio.wait_for(client_reader.read(), 0.5)
            assert value == b"x" * (limit + 1)


async def test_telnet_reader_readuntil_pattern_success(bind_host, unused_tcp_port):
    """Test successful pattern matching with readuntil_pattern."""
    given_shell_banner = b"""
Router> enable
Router# configure terminal
Router(config)# exit
Router>
"""

    # Byte pattern to match command prompt
    pattern = re.compile(rb"\S+[>#]")
    limit = 50

    async def shell(_, writer):
        writer.write(given_shell_banner)
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    async with create_server(
        host=bind_host,
        port=unused_tcp_port,
        connect_maxwait=0.05,
        shell=shell,
        encoding=False,
        limit=limit,
    ):
        async with open_connection(
            host=bind_host, port=unused_tcp_port, connect_minwait=0.05, encoding=False, limit=limit
        ) as (client_reader, _):
            # Test successful reads within limit
            result = await client_reader.readuntil_pattern(pattern)
            assert result == b"\nRouter>"

            result = await client_reader.readuntil_pattern(pattern)
            assert result == b" enable\nRouter#"

            result = await client_reader.readuntil_pattern(pattern)
            assert result == b" configure terminal\nRouter(config)#"

            result = await client_reader.readuntil_pattern(pattern)
            assert result == b" exit\nRouter>"


async def test_telnet_reader_readuntil_pattern_limit_overrun_chunk_too_large(
    bind_host, unused_tcp_port
):
    """Test LimitOverrunError when pattern is found but chunk exceeds limit."""
    given_shell_banner = b"""
Router> enable
Router# configure terminal which is a very long command line that exceeds our limit
Router(config)# exit
Router>
"""

    # Byte pattern to match command prompt
    pattern = re.compile(rb"\S+[>#]")
    limit = 30

    async def shell(_, writer):
        writer.write(given_shell_banner)
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    async with create_server(
        host=bind_host,
        port=unused_tcp_port,
        connect_maxwait=0.05,
        shell=shell,
        encoding=False,
        limit=limit,
    ):
        async with open_connection(
            host=bind_host, port=unused_tcp_port, connect_minwait=0.05, encoding=False, limit=limit
        ) as (client_reader, _):
            # First successful read
            result = await client_reader.readuntil_pattern(pattern)
            assert result == b"\nRouter>"

            result = await client_reader.readuntil_pattern(pattern)
            assert result == b" enable\nRouter#"

            # Test LimitOverrunError: pattern found but data chunk exceeds limit
            with pytest.raises(asyncio.LimitOverrunError) as exc_info:
                await client_reader.readuntil_pattern(pattern)

            assert "Pattern is found, but chunk is longer than limit" in str(exc_info.value)
            # consumed should be the expected length of the oversized chunk
            expected_chunk_size = len(
                b" configure terminal which is a very long command line"
                b" that exceeds our limit\nRouter(config)#"
            )
            assert exc_info.value.consumed == expected_chunk_size


async def test_telnet_reader_readuntil_pattern_limit_overrun_buffer_full(
    bind_host, unused_tcp_port
):
    """Test LimitOverrunError when buffer exceeds limit and pattern not found."""
    # Create data that will exceed the limit when searching for non-existent pattern
    long_data = b"x" * 50  # exceeds limit of 30
    given_shell_banner = b"Router> " + long_data

    pattern = re.compile(rb"\S+[>#]")
    limit = 30

    async def shell(_, writer):
        writer.write(given_shell_banner)
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    async with create_server(
        host=bind_host,
        port=unused_tcp_port,
        connect_maxwait=0.05,
        shell=shell,
        encoding=False,
        limit=limit,
    ):
        async with open_connection(
            host=bind_host, port=unused_tcp_port, connect_minwait=0.05, encoding=False, limit=limit
        ) as (client_reader, _):
            # First read the Router> prompt
            result = await client_reader.readuntil_pattern(pattern)
            assert result == b"Router>"

            # Test LimitOverrunError: buffer exceeds limit, pattern not found
            with pytest.raises(asyncio.LimitOverrunError) as exc_info:
                await client_reader.readuntil_pattern(re.compile(b"non-existent"))

            assert "Pattern not found, and buffer exceed the limit" in str(exc_info.value)
            assert exc_info.value.consumed > limit


async def test_telnet_reader_readuntil_pattern_incomplete_read_eof(bind_host, unused_tcp_port):
    """Test IncompleteReadError when EOF occurs before pattern is found."""
    given_shell_banner = b"Router> some incomplete data\n"

    pattern = re.compile(rb"\S+[>#]")
    limit = 50

    async def shell(_, writer):
        writer.write(given_shell_banner)
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    async with create_server(
        host=bind_host,
        port=unused_tcp_port,
        connect_maxwait=0.05,
        shell=shell,
        encoding=False,
        limit=limit,
    ):
        async with open_connection(
            host=bind_host, port=unused_tcp_port, connect_minwait=0.05, encoding=False, limit=limit
        ) as (client_reader, _):
            # First successful read
            result = await client_reader.readuntil_pattern(pattern)
            assert result == b"Router>"

            # Test IncompleteReadError: EOF before pattern found
            with pytest.raises(asyncio.IncompleteReadError) as exc_info:
                await client_reader.readuntil_pattern(pattern)

            # 'partial' should contain remaining data
            assert exc_info.value.partial == b" some incomplete data\n"
            assert exc_info.value.expected is None

            # After IncompleteReadError, subsequent reads should also fail with empty buffer
            with pytest.raises(asyncio.IncompleteReadError) as exc_info:
                await client_reader.readuntil_pattern(pattern)
            assert exc_info.value.partial == b""


async def test_telnet_reader_readuntil_pattern_invalid_arguments():
    """Test ValueError for invalid pattern types."""
    reader = telnetlib3.TelnetReader(limit=100)

    # Test ValueError for invalid pattern type
    with pytest.raises(ValueError, match="pattern should be a re.Pattern object"):
        await reader.readuntil_pattern(None)

    with pytest.raises(ValueError, match="Only bytes patterns are supported"):
        await reader.readuntil_pattern(re.compile("this is a string pattern"))


async def test_telnet_reader_readuntil_pattern_cancelled_error(bind_host, unused_tcp_port):
    """Test CancelledError handling in readuntil_pattern."""
    given_shell_banner = b"Router> "

    pattern = re.compile(rb"\S+[>#]")
    limit = 50

    async def shell(_, writer):
        writer.write(given_shell_banner)
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    async with create_server(
        host=bind_host,
        port=unused_tcp_port,
        connect_maxwait=0.05,
        shell=shell,
        encoding=False,
        limit=limit,
    ):
        async with open_connection(
            host=bind_host, port=unused_tcp_port, connect_minwait=0.05, encoding=False, limit=limit
        ) as (client_reader, _):
            # Set exception and test it's properly raised
            client_reader.set_exception(asyncio.CancelledError())
            with pytest.raises(asyncio.CancelledError):
                await client_reader.readuntil_pattern(pattern)
