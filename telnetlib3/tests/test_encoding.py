"""Test Server encoding mixin."""
# std imports
import asyncio

# local imports
import telnetlib3
import telnetlib3.stream_writer
from telnetlib3.tests.accessories import (
    unused_tcp_port,
    event_loop,
    bind_host
)

# 3rd party
import pytest


@pytest.mark.asyncio
async def test_telnet_server_encoding_default(
        event_loop, bind_host, unused_tcp_port):
    """Default encoding US-ASCII unless it can be negotiated/confirmed!"""
    from telnetlib3.telopt import IAC, WONT, TTYPE
    # given
    _waiter = asyncio.Future()

    await telnetlib3.create_server(
        host=bind_host, port=unused_tcp_port,
        _waiter_connected=_waiter,
        loop=event_loop, connect_maxwait=0.05)

    reader, writer = await asyncio.open_connection(
        host=bind_host, port=unused_tcp_port, loop=event_loop)

    # exercise, quickly failing negotiation/encoding.
    writer.write(IAC + WONT + TTYPE)

    # verify,
    srv_instance = await asyncio.wait_for(_waiter, 0.5)
    assert srv_instance.encoding(incoming=True) == 'US-ASCII'
    assert srv_instance.encoding(outgoing=True) == 'US-ASCII'
    assert srv_instance.encoding(incoming=True, outgoing=True) == 'US-ASCII'
    with pytest.raises(TypeError):
        # at least one direction should be specified
        srv_instance.encoding()


@pytest.mark.asyncio
async def test_telnet_client_encoding_default(
        event_loop, bind_host, unused_tcp_port):
    """Default encoding US-ASCII unless it can be negotiated/confirmed!"""
    from telnetlib3.telopt import IAC, WONT, TTYPE
    # given
    _waiter = asyncio.Future()

    await event_loop.create_server(asyncio.Protocol,
                                        bind_host, unused_tcp_port)

    reader, writer = await telnetlib3.open_connection(
        host=bind_host, port=unused_tcp_port, loop=event_loop,
        connect_minwait=0.05)


    # after MIN_CONNECT elapsed, client is in US-ASCII state.
    assert writer.protocol.encoding(incoming=True) == 'US-ASCII'
    assert writer.protocol.encoding(outgoing=True) == 'US-ASCII'
    assert writer.protocol.encoding(incoming=True, outgoing=True) == 'US-ASCII'
    with pytest.raises(TypeError):
        # at least one direction should be specified
        writer.protocol.encoding()


@pytest.mark.asyncio
async def test_telnet_server_encoding_client_will(
        event_loop, bind_host, unused_tcp_port):
    """Server Default encoding (utf8) incoming when client WILL."""
    from telnetlib3.telopt import IAC, WONT, WILL, TTYPE, BINARY
    # given
    _waiter = asyncio.Future()

    await telnetlib3.create_server(
        host=bind_host, port=unused_tcp_port,
        _waiter_connected=_waiter,
        loop=event_loop)

    reader, writer = await asyncio.open_connection(
        host=bind_host, port=unused_tcp_port, loop=event_loop)

    # exercise, quickly failing negotiation/encoding.
    writer.write(IAC + WILL + BINARY)
    writer.write(IAC + WONT + TTYPE)

    # verify,
    srv_instance = await asyncio.wait_for(_waiter, 0.5)
    assert srv_instance.encoding(incoming=True) == 'utf8'
    assert srv_instance.encoding(outgoing=True) == 'US-ASCII'
    assert srv_instance.encoding(incoming=True, outgoing=True) == 'US-ASCII'


@pytest.mark.asyncio
async def test_telnet_server_encoding_server_do(
        event_loop, bind_host, unused_tcp_port):
    """Server's default encoding."""
    from telnetlib3.telopt import IAC, WONT, DO, TTYPE, BINARY
    # given
    _waiter = asyncio.Future()

    await telnetlib3.create_server(
        host=bind_host, port=unused_tcp_port,
        _waiter_connected=_waiter,
        loop=event_loop)

    reader, writer = await asyncio.open_connection(
        host=bind_host, port=unused_tcp_port, loop=event_loop)

    # exercise, server will binary
    writer.write(IAC + DO + BINARY)
    writer.write(IAC + WONT + TTYPE)

    # verify,
    srv_instance = await asyncio.wait_for(_waiter, 0.5)
    assert srv_instance.encoding(incoming=True) == 'US-ASCII'
    assert srv_instance.encoding(outgoing=True) == 'utf8'
    assert srv_instance.encoding(incoming=True, outgoing=True) == 'US-ASCII'


@pytest.mark.asyncio
async def test_telnet_server_encoding_bidirectional(
        event_loop, bind_host, unused_tcp_port):
    """Server's default encoding with bi-directional BINARY negotiation."""
    from telnetlib3.telopt import IAC, WONT, DO, WILL, TTYPE, BINARY
    # given
    _waiter = asyncio.Future()

    await telnetlib3.create_server(
        host=bind_host, port=unused_tcp_port,
        _waiter_connected=_waiter,
        loop=event_loop, connect_maxwait=0.05)

    reader, writer = await asyncio.open_connection(
        host=bind_host, port=unused_tcp_port, loop=event_loop)

    # exercise, bi-directional BINARY with quickly failing negotiation.
    writer.write(IAC + DO + BINARY)
    writer.write(IAC + WILL + BINARY)
    writer.write(IAC + WONT + TTYPE)

    # verify,
    srv_instance = await asyncio.wait_for(_waiter, 0.5)
    assert srv_instance.encoding(incoming=True) == 'utf8'
    assert srv_instance.encoding(outgoing=True) == 'utf8'
    assert srv_instance.encoding(incoming=True, outgoing=True) == 'utf8'


@pytest.mark.asyncio
async def test_telnet_client_and_server_encoding_bidirectional(
        event_loop, bind_host, unused_tcp_port):
    """Given a default encoding for client and server, client always wins!"""
    # given
    _waiter = asyncio.Future()

    await telnetlib3.create_server(
        host=bind_host, port=unused_tcp_port, _waiter_connected=_waiter,
        loop=event_loop, encoding='latin1', connect_maxwait=1.0)

    reader, writer = await telnetlib3.open_connection(
        host=bind_host, port=unused_tcp_port, loop=event_loop,
        encoding='cp437', connect_minwait=1.0)

    srv_instance = await asyncio.wait_for(_waiter, 1.5)

    assert srv_instance.encoding(incoming=True) == 'cp437'
    assert srv_instance.encoding(outgoing=True) == 'cp437'
    assert srv_instance.encoding(incoming=True, outgoing=True) == 'cp437'
    assert writer.protocol.encoding(incoming=True) == 'cp437'
    assert writer.protocol.encoding(outgoing=True) == 'cp437'
    assert writer.protocol.encoding(incoming=True, outgoing=True) == 'cp437'


@pytest.mark.asyncio
async def test_telnet_server_encoding_by_LANG(
        event_loop, bind_host, unused_tcp_port):
    """Server's encoding negotiated by LANG value."""
    from telnetlib3.telopt import (
        IAC, WONT, DO, WILL, TTYPE, BINARY,
        WILL, SB, SE, IS, NEW_ENVIRON)
    # given
    _waiter = asyncio.Future()

    await telnetlib3.create_server(
        host=bind_host, port=unused_tcp_port,
        _waiter_connected=_waiter,
        loop=event_loop)

    reader, writer = await asyncio.open_connection(
        host=bind_host, port=unused_tcp_port, loop=event_loop)

    # exercise, bi-direction binary with LANG variable.
    writer.write(IAC + DO + BINARY)
    writer.write(IAC + WILL + BINARY)
    writer.write(IAC + WILL + NEW_ENVIRON)
    writer.write(IAC + SB + NEW_ENVIRON + IS +
                 telnetlib3.stream_writer._encode_env_buf({
                     'LANG': 'uk_UA.KOI8-U',
                 }) + IAC + SE)
    writer.write(IAC + WONT + TTYPE)

    # verify,
    srv_instance = await asyncio.wait_for(_waiter, 0.5)
    assert srv_instance.encoding(incoming=True) == 'KOI8-U'
    assert srv_instance.encoding(outgoing=True) == 'KOI8-U'
    assert srv_instance.encoding(incoming=True, outgoing=True) == 'KOI8-U'
    assert srv_instance.get_extra_info('LANG') == 'uk_UA.KOI8-U'


@pytest.mark.asyncio
async def test_telnet_server_binary_mode(
        event_loop, bind_host, unused_tcp_port):
    """Server's encoding=False creates a binary reader/writer interface."""
    from telnetlib3.telopt import IAC, WONT, DO, TTYPE, BINARY
    # given
    _waiter = asyncio.Future()

    @asyncio.coroutine
    def binary_shell(reader, writer):
        # our reader and writer should provide binary output
        writer.write(b'server_output')

        val = yield from reader.readexactly(1)
        assert val == b'c'
        val = yield from reader.readexactly(len(b'lient '))
        assert val == b'lient '
        writer.close()
        val = yield from reader.read()
        assert val == b'output'

    await telnetlib3.create_server(
        host=bind_host, port=unused_tcp_port,
        shell=binary_shell, _waiter_connected=_waiter, encoding=False,
        loop=event_loop)

    reader, writer = await asyncio.open_connection(
        host=bind_host, port=unused_tcp_port, loop=event_loop)

    # exercise, server will binary
    val = await reader.readexactly(len(IAC + DO + TTYPE))
    assert val == IAC + DO + TTYPE

    writer.write(IAC + WONT + TTYPE)
    writer.write(b'client output')

    val = await reader.readexactly(len(b'server_output'))
    assert val == b'server_output'

    eof = await reader.read()
    assert eof == b''


@pytest.mark.asyncio
async def test_telnet_client_and_server_escape_iac_encoding(
        event_loop, bind_host, unused_tcp_port):
    """Ensure that IAC (byte 255) may be sent across the wire by encoding."""
    # given
    _waiter = asyncio.Future()
    given_string = ''.join(chr(val) for val in list(range(256))) * 2

    await telnetlib3.create_server(
        host=bind_host, port=unused_tcp_port, _waiter_connected=_waiter,
        loop=event_loop, encoding='iso8859-1', connect_maxwait=0.05)

    client_reader, client_writer = await telnetlib3.open_connection(
        host=bind_host, port=unused_tcp_port, loop=event_loop,
        encoding='iso8859-1', connect_minwait=0.05)

    server = await asyncio.wait_for(_waiter, 0.5)

    server.writer.write(given_string)
    result = await client_reader.readexactly(len(given_string))
    assert result == given_string
    server.writer.close()
    eof = await asyncio.wait_for(client_reader.read(), 0.5)
    assert eof == ''


@pytest.mark.asyncio
async def test_telnet_client_and_server_escape_iac_binary(
        event_loop, bind_host, unused_tcp_port):
    """Ensure that IAC (byte 255) may be sent across the wire in binary."""
    # given
    _waiter = asyncio.Future()
    given_string = bytes(range(256)) * 2

    await telnetlib3.create_server(
        host=bind_host, port=unused_tcp_port, _waiter_connected=_waiter,
        loop=event_loop, encoding=False, connect_maxwait=0.05)

    client_reader, client_writer = await telnetlib3.open_connection(
        host=bind_host, port=unused_tcp_port, loop=event_loop,
        encoding=False, connect_minwait=0.05)

    server = await asyncio.wait_for(_waiter, 0.5)

    server.writer.write(given_string)
    result = await client_reader.readexactly(len(given_string))
    assert result == given_string
    server.writer.close()
    eof = await asyncio.wait_for(client_reader.read(), 0.5)
    assert eof == b''
