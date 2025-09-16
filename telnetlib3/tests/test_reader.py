# std imports
import asyncio
import re
import string

# 3rd party
import pytest

# local
import telnetlib3
from telnetlib3.tests.accessories import unused_tcp_port, bind_host


def test_reader_instantiation_safety():
    """On instantiation, one of server or client must be specified."""
    # given,
    def fn_encoding(incoming):
        return "def-ENC"

    reader = telnetlib3.TelnetReader(limit=1999)

    # exercise,
    result = repr(reader)

    # verify.
    assert result == "<TelnetReader limit=1999 encoding=False>"


def test_reader_with_encoding_instantiation_safety():
    # given,
    def fn_encoding(incoming):
        return "def-ENC"

    expected_result = (
        "<TelnetReaderUnicode encoding='def-ENC' " "limit=1999 buflen=0 eof=False>"
    )

    reader = telnetlib3.TelnetReaderUnicode(fn_encoding=fn_encoding, limit=1999)

    # exercise,
    result = repr(reader)

    # verify.
    assert result == expected_result


def test_reader_eof_safety():
    """Check side-effects of feed_eof."""
    # given,
    reader = telnetlib3.TelnetReader(limit=1999)
    reader.feed_eof()

    # exercise,
    result = repr(reader)

    # verify.
    assert result == "<TelnetReader eof limit=1999 encoding=False>"


def test_reader_unicode_eof_safety():
    # given,
    def fn_encoding(incoming):
        return "def-ENC"

    expected_result = (
        "<TelnetReaderUnicode encoding='def-ENC' " "limit=65536 buflen=0 eof=True>"
    )

    reader = telnetlib3.TelnetReaderUnicode(fn_encoding=fn_encoding)
    reader.feed_eof()

    # exercise,
    result = repr(reader)

    # verify.
    assert result == expected_result


async def test_telnet_reader_using_readline_unicode(bind_host, unused_tcp_port):
    """Ensure strict RFC interpretation of newlines in readline method."""
    # given
    _waiter = asyncio.Future()
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

    def shell(reader, writer):
        for item in sorted(given_expected):
            writer.write(item)
        writer.close()

    await telnetlib3.create_server(
        host=bind_host, port=unused_tcp_port, connect_maxwait=0.05, shell=shell
    )

    client_reader, client_writer = await telnetlib3.open_connection(
        host=bind_host, port=unused_tcp_port, connect_minwait=0.05
    )

    # exercise,
    for given, expected in sorted(given_expected.items()):
        result = await asyncio.wait_for(client_reader.readline(), 0.5)

        # verify.
        assert result == expected

    # exercise,
    eof = await asyncio.wait_for(client_reader.read(), 0.5)

    # verify.
    assert eof == ""


async def test_telnet_reader_using_readline_bytes(bind_host, unused_tcp_port):
    """Ensure strict RFC interpretation of newlines in readline method."""
    # given
    _waiter = asyncio.Future()
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

    await telnetlib3.create_server(
        host=bind_host,
        port=unused_tcp_port,
        connect_maxwait=0.05,
        shell=shell,
        encoding=False,
    )

    client_reader, client_writer = await telnetlib3.open_connection(
        host=bind_host, port=unused_tcp_port, connect_minwait=0.05, encoding=False
    )

    # exercise,
    for given, expected in sorted(given_expected.items()):
        result = await asyncio.wait_for(client_reader.readline(), 0.5)

        # verify.
        assert result == expected

    # exercise,
    eof = await asyncio.wait_for(client_reader.read(), 0.5)

    # verify.
    assert eof == b""


async def test_telnet_reader_read_exactly_unicode(bind_host, unused_tcp_port):
    """Ensure TelnetReader.readexactly, especially IncompleteReadError."""
    # given
    _waiter = asyncio.Future()
    given = "☭---------"
    given_partial = "💉-"

    def shell(reader, writer):
        writer.write(given)
        writer.write(given_partial)
        writer.close()

    await telnetlib3.create_server(
        host=bind_host, port=unused_tcp_port, connect_maxwait=0.05, shell=shell
    )

    client_reader, client_writer = await telnetlib3.open_connection(
        host=bind_host, port=unused_tcp_port, connect_minwait=0.05
    )

    # exercise, readexactly # bytes of given
    result = await asyncio.wait_for(client_reader.readexactly(len(given)), 0.5)

    # verify,
    assert result == given

    # exercise, read 1 byte beyond given_partial
    given_readsize = len(given_partial) + 1
    with pytest.raises(asyncio.IncompleteReadError) as exc_info:
        result = await asyncio.wait_for(client_reader.readexactly(given_readsize), 0.5)

    assert exc_info.value.partial == given_partial
    assert exc_info.value.expected == given_readsize


async def test_telnet_reader_read_exactly_bytes(bind_host, unused_tcp_port):
    """Ensure TelnetReader.readexactly, especially IncompleteReadError."""
    # given
    _waiter = asyncio.Future()
    given = string.ascii_letters.encode("ascii")
    given_partial = b"zzz"

    def shell(reader, writer):
        writer.write(given + given_partial)
        writer.close()

    await telnetlib3.create_server(
        host=bind_host,
        port=unused_tcp_port,
        connect_maxwait=0.05,
        shell=shell,
        encoding=False,
    )

    client_reader, client_writer = await telnetlib3.open_connection(
        host=bind_host, port=unused_tcp_port, connect_minwait=0.05, encoding=False
    )

    # exercise, readexactly # bytes of given
    result = await asyncio.wait_for(client_reader.readexactly(len(given)), 0.5)

    # verify,
    assert result == given

    # exercise, read 1 byte beyond given_partial
    given_readsize = len(given_partial) + 1
    with pytest.raises(asyncio.IncompleteReadError) as exc_info:
        result = await asyncio.wait_for(client_reader.readexactly(given_readsize), 0.5)

    assert exc_info.value.partial == given_partial
    assert exc_info.value.expected == given_readsize


async def test_telnet_reader_read_0(bind_host, unused_tcp_port):
    """Ensure TelnetReader.read(0) returns nothing."""
    # given
    def fn_encoding(incoming):
        return "def-ENC"

    reader = telnetlib3.TelnetReaderUnicode(fn_encoding=fn_encoding)

    # exercise
    value = await reader.read(0)

    # verify
    assert value == ""


async def test_telnet_reader_read_beyond_limit_unicode(bind_host, unused_tcp_port):
    """Ensure ability to read(-1) beyond segment sizes of reader._limit."""
    # given
    _waiter = asyncio.Future()

    limit = 10

    def shell(reader, writer):
        assert reader._limit == limit
        given = "x" * (limit + 1)
        writer.write(given)
        writer.close()

    await telnetlib3.create_server(
        host=bind_host,
        port=unused_tcp_port,
        connect_maxwait=0.05,
        shell=shell,
        limit=limit,
    )

    client_reader, client_writer = await telnetlib3.open_connection(
        host=bind_host, port=unused_tcp_port, connect_minwait=0.05, limit=limit
    )

    assert client_reader._limit == limit
    value = await asyncio.wait_for(client_reader.read(), 0.5)
    assert value == "x" * (limit + 1)


async def test_telnet_reader_read_beyond_limit_bytes(bind_host, unused_tcp_port):
    """Ensure ability to read(-1) beyond segment sizes of reader._limit."""
    # given
    _waiter = asyncio.Future()

    limit = 10

    def shell(reader, writer):
        assert reader._limit == limit
        given = b"x" * (limit + 1)
        writer.write(given)
        writer.close()

    await telnetlib3.create_server(
        host=bind_host,
        port=unused_tcp_port,
        connect_maxwait=0.05,
        shell=shell,
        encoding=False,
        limit=limit,
    )

    client_reader, client_writer = await telnetlib3.open_connection(
        host=bind_host,
        port=unused_tcp_port,
        connect_minwait=0.05,
        encoding=False,
        limit=limit,
    )

    assert client_reader._limit == limit
    value = await asyncio.wait_for(client_reader.read(), 0.5)
    assert value == b"x" * (limit + 1)


async def test_telnet_reader_readuntil_pattern(bind_host, unused_tcp_port):
    """Ensure TelnetReader.readuntil_pattern,
    especially IncompleteReadError and LimitOverrunError."""

    # given
    text = b"""
Router> enable
Router# configure terminal
Router(config)# hostname Router-Telnetlib
Router-Telnetlib(config)# exit
Router-Telnetlib# exit
Router>
"""
    meaningless_data: bytes = b"meaningless" * 2**16
    data: bytes = text + meaningless_data + b"\n"

    # Byte pattern to match command prompt
    pattern = re.compile(rb"\S+[>#]")
    limit = 30

    def shell(_, writer):
        writer.write(data)
        writer.close()

    await telnetlib3.create_server(
        host=bind_host,
        port=unused_tcp_port,
        connect_maxwait=0.05,
        shell=shell,
        encoding=False,
        limit=limit,
    )

    client_reader, _ = await telnetlib3.open_connection(
        host=bind_host,
        port=unused_tcp_port,
        connect_minwait=0.05,
        encoding=False,  # type: ignore
        limit=limit,
    )
    assert client_reader is not None

    # Test successful read within limit
    result = await client_reader.readuntil_pattern(pattern)
    assert result == b"\nRouter>"

    result = await client_reader.readuntil_pattern(pattern)
    assert result == b" enable\nRouter#"

    # Test LimitOverrunError: pattern found but data chunk exceeds limit
    # Next chunk ' configure terminal\nRouter(config)#' is 35 bytes long, exceeding limit (30)
    with pytest.raises(asyncio.LimitOverrunError) as exc_info:
        await client_reader.readuntil_pattern(pattern)

    assert "Pattern is found, but chunk is longer than limit" in str(exc_info.value)
    # consumed should be the expected length of the oversized chunk
    assert exc_info.value.consumed == 35

    # Test LimitOverrunError: buffer exceeds limit, pattern not found
    # Oversized chunk remains in buffer, buffer length now exceeds limit
    # Searching for a non-existent pattern should trigger another LimitOverrunError
    with pytest.raises(asyncio.LimitOverrunError) as exc_info:
        await client_reader.readuntil_pattern(re.compile(b"non-existent"))

    assert "Pattern not found, and buffer exceed the limit" in str(exc_info.value)
    # consumed should be the current buffer length
    assert exc_info.value.consumed > limit

    # Clean up oversized chunk for further testing by reading it in parts
    # First, read the overflow portion based on the previous exception info
    expected = b" configure terminal\nRouter(config)#"
    oversized_chunk = await client_reader.read(len(expected))
    assert oversized_chunk == expected

    expected = b" hostname Router-Telnetlib\nRouter-Telnetlib(config)#"
    oversized_chunk = await client_reader.read(len(expected))
    assert oversized_chunk == expected

    result = await client_reader.readuntil_pattern(pattern)
    assert result == b" exit\nRouter-Telnetlib#"

    result = await client_reader.readuntil_pattern(pattern)
    assert result == b" exit\nRouter>"

    # Consume meaningless data
    expected = b"\n" + meaningless_data
    result = await client_reader.readexactly(len(expected))
    assert result == expected

    # Test IncompleteReadError: EOF before pattern found
    # Server has closed connection, only a newline remains
    with pytest.raises(asyncio.IncompleteReadError) as exc_info:
        await client_reader.readuntil_pattern(pattern)

    # 'partial' should contain remaining data
    assert exc_info.value.partial == b"\n"
    assert exc_info.value.expected is None

    # After IncompleteReadError, buffer is cleared
    # Subsequent reads should also fail with empty partial buffer
    with pytest.raises(asyncio.IncompleteReadError) as exc_info:
        await client_reader.readuntil_pattern(pattern)
    assert exc_info.value.partial == b""

    # Test ValueError for invalid pattern type
    with pytest.raises(ValueError, match="pattern should be a re.Pattern object"):
        await client_reader.readuntil_pattern(None)  # type: ignore

    with pytest.raises(ValueError, match="Only bytes patterns are supported"):
        await client_reader.readuntil_pattern(re.compile("this is a string pattern"))

    client_reader.set_exception(asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        await client_reader.readuntil_pattern(pattern)
