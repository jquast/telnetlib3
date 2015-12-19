"""Test Server encoding mixin."""
# std imports
import asyncio

# local imports
import telnetlib3
import telnetlib3.stream_writer
from telnetlib3.tests.accessories import (
    unused_tcp_port,
    event_loop,
    bind_host,
    log
)

# 3rd party
import pytest


@pytest.mark.asyncio
def test_telnet_server_encoding_default(
        event_loop, bind_host, unused_tcp_port, log):
    """Test Server's default encoding."""
    from telnetlib3.telopt import IAC, WONT, TTYPE
    # given
    _waiter = asyncio.Future()
    event_loop.set_debug(True)

    yield from telnetlib3.create_server(
        host=bind_host, port=unused_tcp_port,
        waiter_connected=_waiter,
        loop=event_loop, log=log)

    reader, writer = yield from asyncio.open_connection(
        host=bind_host, port=unused_tcp_port, loop=event_loop)

    # exercise, quickly failing negotiation/encoding.
    writer.write(IAC + WONT + TTYPE)

    # verify,
    srv_instance = yield from asyncio.wait_for(_waiter, 0.5)
    assert srv_instance.encoding(incoming=True) == 'US-ASCII'
    assert srv_instance.encoding(outgoing=True) == 'US-ASCII'
    assert srv_instance.encoding(incoming=True, outgoing=True) == 'US-ASCII'
    with pytest.raises(TypeError):
        srv_instance.encoding()  # at least one direction should be specified


@pytest.mark.asyncio
def test_telnet_server_encoding_client_will(
        event_loop, bind_host, unused_tcp_port, log):
    """Test Server's default encoding."""
    from telnetlib3.telopt import IAC, WONT, WILL, TTYPE, BINARY
    # given
    _waiter = asyncio.Future()
    event_loop.set_debug(True)

    yield from telnetlib3.create_server(
        host=bind_host, port=unused_tcp_port,
        waiter_connected=_waiter,
        loop=event_loop, log=log)

    reader, writer = yield from asyncio.open_connection(
        host=bind_host, port=unused_tcp_port, loop=event_loop)

    # exercise, quickly failing negotiation/encoding.
    writer.write(IAC + WILL + BINARY)
    writer.write(IAC + WONT + TTYPE)

    # verify,
    srv_instance = yield from asyncio.wait_for(_waiter, 0.5)
    assert srv_instance.encoding(incoming=True) == 'utf8'
    assert srv_instance.encoding(outgoing=True) == 'US-ASCII'
    assert srv_instance.encoding(incoming=True, outgoing=True) == 'US-ASCII'


@pytest.mark.asyncio
def test_telnet_server_encoding_server_will(
        event_loop, bind_host, unused_tcp_port, log):
    """Test Server's default encoding."""
    from telnetlib3.telopt import IAC, WONT, DO, TTYPE, BINARY
    # given
    _waiter = asyncio.Future()
    event_loop.set_debug(True)

    yield from telnetlib3.create_server(
        host=bind_host, port=unused_tcp_port,
        waiter_connected=_waiter,
        loop=event_loop, log=log)

    reader, writer = yield from asyncio.open_connection(
        host=bind_host, port=unused_tcp_port, loop=event_loop)

    # exercise, quickly failing negotiation/encoding.
    writer.write(IAC + DO + BINARY)
    writer.write(IAC + WONT + TTYPE)

    # verify,
    srv_instance = yield from asyncio.wait_for(_waiter, 0.5)
    assert srv_instance.encoding(incoming=True) == 'US-ASCII'
    assert srv_instance.encoding(outgoing=True) == 'utf8'
    assert srv_instance.encoding(incoming=True, outgoing=True) == 'US-ASCII'


@pytest.mark.asyncio
def test_telnet_server_encoding_bidirectional(
        event_loop, bind_host, unused_tcp_port, log):
    """Test Server's default encoding."""
    from telnetlib3.telopt import IAC, WONT, DO, WILL, TTYPE, BINARY
    # given
    _waiter = asyncio.Future()
    event_loop.set_debug(True)

    yield from telnetlib3.create_server(
        host=bind_host, port=unused_tcp_port,
        waiter_connected=_waiter,
        loop=event_loop, log=log)

    reader, writer = yield from asyncio.open_connection(
        host=bind_host, port=unused_tcp_port, loop=event_loop)

    # exercise, quickly failing negotiation/encoding.
    writer.write(IAC + DO + BINARY)
    writer.write(IAC + WILL + BINARY)
    writer.write(IAC + WONT + TTYPE)

    # verify,
    srv_instance = yield from asyncio.wait_for(_waiter, 0.5)
    assert srv_instance.encoding(incoming=True) == 'utf8'
    assert srv_instance.encoding(outgoing=True) == 'utf8'
    assert srv_instance.encoding(incoming=True, outgoing=True) == 'utf8'
