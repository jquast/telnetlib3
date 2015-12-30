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
    yield from telnetlib3.create_server(
        protocol_factory=given_pf,
        host=bind_host, port=unused_tcp_port,
        loop=event_loop, log=log)

    reader, writer = yield from asyncio.open_connection(
        host=bind_host, port=unused_tcp_port, loop=event_loop)

    # verify
    assert given_pf.called


@pytest.mark.asyncio
def test_telnet_server_open_close(
        event_loop, bind_host, unused_tcp_port, log):
    """Test telnetlib3.TelnetServer() instantiation and connection_made()."""
    from telnetlib3.telopt import IAC, WONT, TTYPE
    # given,
    _waiter = asyncio.Future()
    yield from telnetlib3.create_server(
        waiter_connected=_waiter,
        host=bind_host, port=unused_tcp_port,
        loop=event_loop, log=log)

    # exercise,
    reader, writer = yield from asyncio.open_connection(
        host=bind_host, port=unused_tcp_port, loop=event_loop)

    writer.write(IAC + WONT + TTYPE + b'bye\r')
    server = yield from asyncio.wait_for(_waiter, 0.5)
    server.writer.write('Goodbye!')
    server.writer.close()
    result = yield from reader.read()
    assert result == b'\xff\xfd\x18Goodbye!'


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
    server = yield from asyncio.wait_for(_waiter, 0.5)

    # verify,
    assert server.writer.remote_option[TTYPE] is True
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
    """Exercise TelnetServer.connection_lost by close()."""
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
    hello_reply = IAC + WONT + TTYPE + b'quit' + b'\r\n'

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
def test_telnet_server_closed_by_exception(
        event_loop, bind_host, unused_tcp_port, log):
    """Exercise TelnetServer.connection_lost by exception."""
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

    writer.write(IAC + WONT + TTYPE)
    server = yield from asyncio.wait_for(_waiter_connected, 0.5)

    class CustomException(Exception):
        pass

    # exercise, by connection_lost(exc=Exception())..
    server.writer.write('Bye!')
    server.connection_lost(exc=CustomException('blah!'))

    # verify, custom exceptiohn is thrown into any yielding reader
    with pytest.raises(CustomException):
        yield from server.reader.read()


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


@pytest.mark.asyncio
def test_telnet_server_cmdline(bind_host, unused_tcp_port, event_loop):
    """Test executing telnetlib3/server.py as server"""
    import os
    import pexpect
    prog = pexpect.which('telnetlib3-server')
    _coveragerc = os.path.join(os.path.dirname(__file__),
                               os.pardir, os.pardir,
                               '.coveragerc')

    _env = os.environ.copy()
    # enforce asyncio debug: we rely on 'is serving'
    # notification to await until server port is bound.
    _env['PYTHONASYNCIODEBUG']='1'
    args = [prog, bind_host, str(unused_tcp_port),
            '--loglevel=debug']

    proc = yield from asyncio.create_subprocess_exec(
        *args, loop=event_loop, stderr=asyncio.subprocess.PIPE, env=_env)

    while True:
        line = yield from proc.stderr.readline()
        if b'is serving' in line:
            break

    # client connects,
    reader, writer = yield from asyncio.open_connection(
        host=bind_host, port=unused_tcp_port, loop=event_loop)
    writer.close()

    # and closes,
    while True:
        line = yield from proc.stderr.readline()
        if b'Connection lost' in line:
            break

    # just kill server such as ^C
    proc.terminate()
    yield from proc.communicate()
    yield from proc.wait()
