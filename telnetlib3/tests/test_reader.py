# std imports
import asyncio
import string

# 3rd party
import pytest

# local
import telnetlib3
from telnetlib3.tests.accessories import (
    unused_tcp_port,
    event_loop,
    bind_host
)


def test_reader_instantiation_safety():
    """On instantiation, one of server or client must be specified."""
    # exercise
    def fn_encoding(incoming):
        return 'def-ENC'

    reader = telnetlib3.TelnetReader()
    assert repr(reader) == ("<TelnetReader encoding=False "
                            "buflen=0 eof=False>")

    reader = telnetlib3.TelnetReaderUnicode(fn_encoding=fn_encoding)
    assert repr(reader) == ("<TelnetReaderUnicode encoding='def-ENC' "
                            "buflen=0 eof=False>")


@pytest.mark.asyncio
def test_telnet_reader_using_readline_unicode(
        event_loop, bind_host, unused_tcp_port):
    """Ensure strict RFC interpretation of newlines in readline method."""
    # given
    _waiter = asyncio.Future()
    given_expected = {
        'alpha\r\x00': 'alpha\r',
        'bravo\r\n': 'bravo\r\n',
        'charlie\n': 'charlie\n',
        '---\r---\r\n': '---\r---\r\n',
        '\r\x00': '\r',
        '\n': '\n',
        '\r\n': '\r\n',
        'xxxxxxxxxxx': 'xxxxxxxxxxx',
    }

    def shell(reader, writer):
        for item in sorted(given_expected):
            writer.write(item)
        writer.close()

    yield from telnetlib3.create_server(
        host=bind_host, port=unused_tcp_port, loop=event_loop,
        connect_maxwait=0.05, shell=shell)

    client_reader, client_writer = yield from telnetlib3.open_connection(
        host=bind_host, port=unused_tcp_port, loop=event_loop,
        connect_minwait=0.05)

    for given, expected in sorted(given_expected.items()):
        result = yield from asyncio.wait_for(client_reader.readline(), 0.5)
        assert result == expected
    eof = yield from asyncio.wait_for(client_reader.read(), 0.5)
    assert eof == ''


@pytest.mark.asyncio
def test_telnet_reader_using_readline_bytes(
        event_loop, bind_host, unused_tcp_port):
    """Ensure strict RFC interpretation of newlines in readline method."""
    # given
    _waiter = asyncio.Future()
    given_expected = {
        b'alpha\r\x00': b'alpha\r',
        b'bravo\r\n': b'bravo\r\n',
        b'charlie\n': b'charlie\n',
        b'---\r---\r\n': b'---\r---\r\n',
        b'\r\x00': b'\r',
        b'\n': b'\n',
        b'\r\n': b'\r\n',
        b'xxxxxxxxxxx': b'xxxxxxxxxxx',
    }

    def shell(reader, writer):
        for item in sorted(given_expected):
            writer.write(item)
        writer.close()

    yield from telnetlib3.create_server(
        host=bind_host, port=unused_tcp_port, loop=event_loop,
        connect_maxwait=0.05, shell=shell, encoding=False)

    client_reader, client_writer = yield from telnetlib3.open_connection(
        host=bind_host, port=unused_tcp_port, loop=event_loop,
        connect_minwait=0.05, encoding=False)

    for given, expected in sorted(given_expected.items()):
        result = yield from asyncio.wait_for(client_reader.readline(), 0.5)
        assert result == expected
    eof = yield from asyncio.wait_for(client_reader.read(), 0.5)
    assert eof == b''


@pytest.mark.asyncio
def test_telnet_reader_read_exactly_unicode(
        event_loop, bind_host, unused_tcp_port):
    """Ensure TelnetReader.readexactly, especially IncompleteReadError."""
    # given
    _waiter = asyncio.Future()
    # TODO: count utf8!
    given = string.ascii_letters
    given_partial = 'zzz'

    def shell(reader, writer):
        writer.write(given)
        writer.write(given_partial)
        writer.close()

    yield from telnetlib3.create_server(
        host=bind_host, port=unused_tcp_port, loop=event_loop,
        connect_maxwait=0.05, shell=shell)

    client_reader, client_writer = yield from telnetlib3.open_connection(
        host=bind_host, port=unused_tcp_port, loop=event_loop,
        connect_minwait=0.05)

    # exercise, readexactly # bytes of given
    result = yield from asyncio.wait_for(
        client_reader.readexactly(len(given)), 0.5)

    # verify,
    assert result == given

    # exercise, read 1 byte beyond given_partial
    given_readsize = len(given_partial) + 1
    with pytest.raises(asyncio.IncompleteReadError) as exc_info:
        result = yield from asyncio.wait_for(
            client_reader.readexactly(given_readsize), 0.5)

    assert exc_info.value.partial == given_partial
    assert exc_info.value.expected == given_readsize


@pytest.mark.asyncio
def test_telnet_reader_read_exactly_bytes(
        event_loop, bind_host, unused_tcp_port):
    """Ensure TelnetReader.readexactly, especially IncompleteReadError."""
    # given
    _waiter = asyncio.Future()
    # TODO: count utf8!
    given = string.ascii_letters.encode('ascii')
    given_partial = b'zzz'

    def shell(reader, writer):
        writer.write(given + given_partial)
        writer.close()

    yield from telnetlib3.create_server(
        host=bind_host, port=unused_tcp_port, loop=event_loop,
        connect_maxwait=0.05, shell=shell, encoding=False)

    client_reader, client_writer = yield from telnetlib3.open_connection(
        host=bind_host, port=unused_tcp_port, loop=event_loop,
        connect_minwait=0.05, encoding=False)

    # exercise, readexactly # bytes of given
    result = yield from asyncio.wait_for(
        client_reader.readexactly(len(given)), 0.5)

    # verify,
    assert result == given

    # exercise, read 1 byte beyond given_partial
    given_readsize = len(given_partial) + 1
    with pytest.raises(asyncio.IncompleteReadError) as exc_info:
        result = yield from asyncio.wait_for(
            client_reader.readexactly(given_readsize), 0.5)

    assert exc_info.value.partial == given_partial
    assert exc_info.value.expected == given_readsize
