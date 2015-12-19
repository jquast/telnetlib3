"""Test instantiation of basic server and client forms."""
# std imports
import asyncio
import unittest.mock

# local imports
import telnetlib3
from telnetlib3.tests.accessories import (
    # TestTelnetClient,
    unused_tcp_port,
    event_loop,
    bind_host,
    log
)

# 3rd party
import pytest


@pytest.mark.asyncio
def test_create_server(bind_host, unused_tcp_port, log):
    """Test telnetlib3.create_server basic instantiation."""
    # exercise,
    yield from telnetlib3.create_server(
        host=bind_host, port=unused_tcp_port, log=log)


@pytest.mark.asyncio
def test_create_server_conditionals(
        event_loop, bind_host, unused_tcp_port, log):
    """Test telnetlib3.create_server conditionals."""
    # exercise,
    yield from telnetlib3.create_server(
        protocol_factory=lambda: telnetlib3.TelnetServer,
        host=bind_host, port=unused_tcp_port,
        loop=event_loop, log=log)


@pytest.mark.asyncio
def test_create_server_on_connect(event_loop, bind_host, unused_tcp_port, log):
    """Test on_connect() anonymous function callback of create_server."""
    # given,
    given_pf = unittest.mock.MagicMock()
    server = yield from telnetlib3.create_server(
        protocol_factory=given_pf,
        host=bind_host, port=unused_tcp_port,
        loop=event_loop, log=log)

    reader, writer = yield from asyncio.open_connection(
        host=bind_host, port=unused_tcp_port, loop=event_loop)

    # verify
    assert given_pf.called


@pytest.mark.asyncio
def test_telnet_server_instantiation(
        event_loop, bind_host, unused_tcp_port, log):
    """Test telnetlib3.TelnetServer() instantiation and connection_made()."""
    # given,
    yield from telnetlib3.create_server(
        host=bind_host, port=unused_tcp_port,
        loop=event_loop, log=log)

    # exercise,
    yield from asyncio.open_connection(
        host=bind_host, port=unused_tcp_port, loop=event_loop)


@pytest.mark.asyncio
def test_telnet_server_advanced_negotiation(
        event_loop, bind_host, unused_tcp_port, log):
    """Test telnetlib3.TelnetServer() advanced negotiation."""
    # given
    from telnetlib3.telopt import (
        IAC, DO, WILL, SB, TTYPE, NEW_ENVIRON, NAWS, SGA, ECHO, CHARSET, BINARY
    )
    _waiter = asyncio.Future()

    class ServerTestAdvanced(telnetlib3.TelnetServer):
        def begin_advanced_negotiation(self):
            super().begin_advanced_negotiation()
            _waiter.set_result(self)

    yield from telnetlib3.create_server(
        protocol_factory=ServerTestAdvanced,
        host=bind_host, port=unused_tcp_port,
        loop=event_loop, log=log)

    reader, writer = yield from asyncio.open_connection(
        host=bind_host, port=unused_tcp_port, loop=event_loop)

    # exercise,
    writer.write(IAC + WILL + TTYPE)
    srv_writer = (yield from _waiter).writer

    server = yield from asyncio.wait_for(_waiter, 0.5)

    # verify,
    assert server.writer.remote_option[TTYPE] == True
    assert server.writer.pending_option == {
        # server's request to negotiation TTYPE affirmed
        DO + TTYPE: False,
        # server's request for TTYPE value unreplied
        SB + TTYPE: True,
        # remaining unreplied values from begin_advanced_negotiation()
        DO + NEW_ENVIRON: True,
        DO + CHARSET: True,
        DO + NAWS: True,
        WILL + SGA: True,
        WILL + ECHO: True,
        WILL + BINARY: True,
    }


@pytest.mark.asyncio
def test_telnet_server_closed_by_client(
        event_loop, bind_host, unused_tcp_port, log):
    """Exercise TelnetServer.connection_lost."""
    # given
    _waiter = asyncio.Future()

    yield from telnetlib3.create_server(
        waiter_closed=_waiter,
        host=bind_host, port=unused_tcp_port,
        loop=event_loop, log=log)

    reader, writer = yield from asyncio.open_connection(
        host=bind_host, port=unused_tcp_port, loop=event_loop)

    # exercise,
    writer.close()

    # verify,
    srv_instance = yield from asyncio.wait_for(_waiter, 0.5)
    assert srv_instance._closing

    # exercise, a 2nd call to .connection_lost() returns early,
    # allowing callbacks the freedom to call it at any time from
    # the server-end to dump the client.
    srv_instance.connection_lost(exc=None)


@pytest.mark.asyncio
def test_telnet_server_eof_by_client(
        event_loop, bind_host, unused_tcp_port, log):
    """Exercise TelnetServer.eof_received()."""
    # given
    _waiter = asyncio.Future()

    yield from telnetlib3.create_server(
        waiter_closed=_waiter,
        host=bind_host, port=unused_tcp_port,
        loop=event_loop, log=log)

    reader, writer = yield from asyncio.open_connection(
        host=bind_host, port=unused_tcp_port, loop=event_loop)

    # exercise,
    writer.write_eof()

    # verify,
    srv_instance = yield from asyncio.wait_for(_waiter, 0.5)
    assert srv_instance._closing


@pytest.mark.asyncio
def test_telnet_server_closed_by_server(
        event_loop, bind_host, unused_tcp_port, log):
    """Exercise TelnetServer.connection_lost."""
    from telnetlib3.telopt import IAC, DO, WONT, TTYPE

    # given
    _waiter_connected = asyncio.Future()
    _waiter_closed = asyncio.Future()

    yield from telnetlib3.create_server(
        waiter_connected=_waiter_connected,
        waiter_closed=_waiter_closed,
        host=bind_host, port=unused_tcp_port,
        loop=event_loop, log=log)

    reader, writer = yield from asyncio.open_connection(
        host=bind_host, port=unused_tcp_port, loop=event_loop)

    # data received by client, connection is made
    expect_hello = IAC + DO + TTYPE
    hello_reply = IAC + WONT + TTYPE

    # exercise,
    hello = yield from reader.readexactly(len(expect_hello))

    # verify,
    assert hello == expect_hello

    # exercise,
    writer.write(hello_reply)
    server = yield from asyncio.wait_for(_waiter_connected, 0.5)

    # exercise, by closing.
    server.writer.close()

    # verify
    yield from asyncio.wait_for(_waiter_closed, 0.5)


@pytest.mark.asyncio
def test_telnet_server_idle_duration(
        event_loop, bind_host, unused_tcp_port, log):
    """Exercise TelnetServer.idle property."""
    from telnetlib3.telopt import IAC, WONT, TTYPE

    # given
    _waiter_connected = asyncio.Future()
    _waiter_closed = asyncio.Future()

    yield from telnetlib3.create_server(
        waiter_connected=_waiter_connected,
        waiter_closed=_waiter_closed,
        host=bind_host, port=unused_tcp_port,
        loop=event_loop, log=log)

    reader, writer = yield from asyncio.open_connection(
        host=bind_host, port=unused_tcp_port, loop=event_loop)

    writer.write(IAC + WONT + TTYPE)
    server = yield from asyncio.wait_for(_waiter_connected, 0.5)

    # exercise
    assert 0 <= server.idle <= 0.5
    assert 0 <= server.duration <= 0.5


@pytest.mark.asyncio
def test_telnet_server_negotiation_fail(
        event_loop, bind_host, unused_tcp_port, log):
    """Test telnetlib3.TelnetServer() negotiation failure with client."""
    from telnetlib3.telopt import DO, TTYPE
    # given
    _waiter_connected = asyncio.Future()

    class ServerNegotiationFail(telnetlib3.TelnetServer):
        CONNECT_MAXWAIT = 0.05
        CONNECT_DEFERRED = 0.01

    yield from telnetlib3.create_server(
        protocol_factory=ServerNegotiationFail,
        waiter_connected=_waiter_connected,
        host=bind_host, port=unused_tcp_port,
        loop=event_loop, log=log)

    reader, writer = yield from asyncio.open_connection(
        host=bind_host, port=unused_tcp_port, loop=event_loop)

    # exercise,
    reader.readexactly(3)  # IAC DO TTYPE, we ignore it!

    # negotiation then times out, deferring to waiter_connected.
    server = yield from asyncio.wait_for(_waiter_connected, 5.5)

    # verify,
    assert server.negotiation_should_advance() is False
    assert server.writer.pending_option[DO + TTYPE] == True
