"""Test instantiation of basic server and client forms."""
# std imports
import asyncio
import unittest.mock

# local imports
import telnetlib3
from telnetlib3.tests.accessories import (
    server_factory,
    # TestTelnetClient,
    unused_tcp_port,
    event_loop,
    bind_host,
    log
)

# 3rd party
import pytest


@pytest.mark.asyncio
def test_create_server(
        server_factory, bind_host, unused_tcp_port, log):
    """Test telnetlib3.create_server basic instantiation."""
    # exercise,
    yield from telnetlib3.create_server(
        host=bind_host, port=unused_tcp_port, log=log)


@pytest.mark.asyncio
def test_create_server_conditionals(
        server_factory, event_loop, bind_host, unused_tcp_port, log):
    """Test telnetlib3.create_server conditionals."""
    # exercise,
    yield from telnetlib3.create_server(
        protocol_factory=lambda: telnetlib3.TelnetServer,
        host=bind_host, port=unused_tcp_port,
        loop=event_loop, log=log)


@pytest.mark.asyncio
def test_create_server_on_connect(
        server_factory, event_loop, bind_host, unused_tcp_port, log):
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
        server_factory, event_loop, bind_host, unused_tcp_port, log):
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
        server_factory, event_loop, bind_host, unused_tcp_port, log):
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

    # verify,
    assert srv_writer.remote_option[TTYPE] == True
    assert srv_writer.pending_option == {
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
