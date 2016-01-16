"""Test instantiation of basic server and client forms."""
# std imports
import asyncio
import platform
import unittest.mock

# local imports
import telnetlib3
from telnetlib3.tests.accessories import (
    # TestTelnetClient,
    unused_tcp_port,
    event_loop,
    bind_host
)

# 3rd party
import pytest


@pytest.mark.asyncio
def test_create_server(bind_host, unused_tcp_port):
    """Test telnetlib3.create_server basic instantiation."""
    # exercise,
    yield from telnetlib3.create_server(
        host=bind_host, port=unused_tcp_port)

@pytest.mark.asyncio
def test_open_connection(bind_host, unused_tcp_port):
    """Exercise telnetlib3.open_connection with default options."""
    _waiter = asyncio.Future()
    yield from telnetlib3.start_server(bind_host, unused_tcp_port,
                                       waiter_connected=_waiter,
                                       connect_maxwait=0.05)
    yield from telnetlib3.open_connection(bind_host, unused_tcp_port,
                                          connect_minwait=0.05)
    yield from asyncio.wait_for(_waiter, 0.5)


@pytest.mark.asyncio
def test_create_server_conditionals(
        event_loop, bind_host, unused_tcp_port):
    """Test telnetlib3.create_server conditionals."""
    # exercise,
    yield from telnetlib3.create_server(
        protocol_factory=lambda: telnetlib3.TelnetServer,
        host=bind_host, port=unused_tcp_port,
        loop=event_loop)


@pytest.mark.asyncio
def test_create_server_on_connect(event_loop, bind_host, unused_tcp_port):
    """Test on_connect() anonymous function callback of create_server."""
    # given,
    given_pf = unittest.mock.MagicMock()
    yield from telnetlib3.create_server(
        protocol_factory=given_pf,
        host=bind_host, port=unused_tcp_port,
        loop=event_loop)

    reader, writer = yield from asyncio.open_connection(
        host=bind_host, port=unused_tcp_port, loop=event_loop)

    # verify
    assert given_pf.called


@pytest.mark.asyncio
def test_telnet_server_open_close(
        event_loop, bind_host, unused_tcp_port):
    """Test telnetlib3.TelnetServer() instantiation and connection_made()."""
    from telnetlib3.telopt import IAC, WONT, TTYPE
    # given,
    _waiter = asyncio.Future()
    yield from telnetlib3.create_server(
        waiter_connected=_waiter,
        host=bind_host, port=unused_tcp_port,
        loop=event_loop)

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
def test_telnet_client_open_close_by_write(
        event_loop, bind_host, unused_tcp_port):
    """Exercise BaseClient.connection_lost() on writer closed."""
    yield from event_loop.create_server(asyncio.Protocol,
                                        bind_host, unused_tcp_port)

    reader, writer = yield from telnetlib3.open_connection(
        host=bind_host, port=unused_tcp_port,
        connect_minwait=0.05)
    writer.close()
    assert (yield from reader.read()) == ''


@pytest.mark.asyncio
def test_telnet_client_open_closed_by_peer(
        event_loop, bind_host, unused_tcp_port):
    """Exercise BaseClient.connection_lost()."""
    class DisconnecterProtocol(asyncio.Protocol):
        def connection_made(self, transport):
            # disconnect on connect
            transport.close()

    yield from event_loop.create_server(DisconnecterProtocol,
                                        bind_host, unused_tcp_port)

    reader, writer = yield from telnetlib3.open_connection(
        host=bind_host, port=unused_tcp_port,
        connect_minwait=0.05)

    # read until EOF, no data received.
    data_received = yield from reader.read()
    assert data_received == ''


@pytest.mark.asyncio
def test_telnet_server_advanced_negotiation(
        event_loop, bind_host, unused_tcp_port):
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
        loop=event_loop)

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
        event_loop, bind_host, unused_tcp_port):
    """Exercise TelnetServer.connection_lost."""
    # given
    _waiter = asyncio.Future()

    yield from telnetlib3.create_server(
        waiter_closed=_waiter,
        host=bind_host, port=unused_tcp_port,
        loop=event_loop)

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
        event_loop, bind_host, unused_tcp_port):
    """Exercise TelnetServer.eof_received()."""
    # given
    _waiter = asyncio.Future()

    yield from telnetlib3.create_server(
        waiter_closed=_waiter,
        host=bind_host, port=unused_tcp_port,
        loop=event_loop)

    reader, writer = yield from asyncio.open_connection(
        host=bind_host, port=unused_tcp_port, loop=event_loop)

    # exercise,
    writer.write_eof()

    # verify,
    srv_instance = yield from asyncio.wait_for(_waiter, 0.5)
    assert srv_instance._closing


@pytest.mark.asyncio
def test_telnet_server_closed_by_server(
        event_loop, bind_host, unused_tcp_port):
    """Exercise TelnetServer.connection_lost by close()."""
    from telnetlib3.telopt import IAC, DO, WONT, TTYPE

    # given
    _waiter_connected = asyncio.Future()
    _waiter_closed = asyncio.Future()

    yield from telnetlib3.create_server(
        waiter_connected=_waiter_connected,
        waiter_closed=_waiter_closed,
        host=bind_host, port=unused_tcp_port,
        loop=event_loop)

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
        event_loop, bind_host, unused_tcp_port):
    """Exercise TelnetServer.idle property."""
    from telnetlib3.telopt import IAC, WONT, TTYPE

    # given
    _waiter_connected = asyncio.Future()
    _waiter_closed = asyncio.Future()

    yield from telnetlib3.create_server(
        waiter_connected=_waiter_connected,
        waiter_closed=_waiter_closed,
        host=bind_host, port=unused_tcp_port,
        loop=event_loop)

    reader, writer = yield from asyncio.open_connection(
        host=bind_host, port=unused_tcp_port, loop=event_loop)

    writer.write(IAC + WONT + TTYPE)
    server = yield from asyncio.wait_for(_waiter_connected, 0.5)

    # verify
    assert 0 <= server.idle <= 0.5
    assert 0 <= server.duration <= 0.5


@pytest.mark.asyncio
def test_telnet_client_idle_duration_minwait(
        event_loop, bind_host, unused_tcp_port):
    """Exercise TelnetClient.idle property and minimum connection time."""
    from telnetlib3.telopt import IAC, WONT, TTYPE

    # a server that doesn't care
    yield from event_loop.create_server(asyncio.Protocol,
                                        bind_host, unused_tcp_port)

    given_minwait = 0.100

    import time

    stime = time.time()
    reader, writer = yield from telnetlib3.open_connection(
        host=bind_host, port=unused_tcp_port, loop=event_loop,
        connect_minwait=given_minwait, connect_maxwait=given_minwait)

    elapsed_ms = int((time.time() - stime) * 1e3)
    expected_ms = int(given_minwait * 1e3)
    assert expected_ms <= elapsed_ms <= expected_ms + 50

    # verify
    assert 0 <= writer.protocol.idle <= 0.5
    assert 0 <= writer.protocol.duration <= 0.5


@pytest.mark.asyncio
def test_telnet_server_closed_by_error(
        event_loop, bind_host, unused_tcp_port):
    """Exercise TelnetServer.connection_lost by exception."""
    from telnetlib3.telopt import IAC, DO, WONT, TTYPE

    # given
    _waiter_connected = asyncio.Future()
    _waiter_closed = asyncio.Future()

    yield from telnetlib3.create_server(
        waiter_connected=_waiter_connected,
        waiter_closed=_waiter_closed,
        host=bind_host, port=unused_tcp_port,
        loop=event_loop)

    reader, writer = yield from asyncio.open_connection(
        host=bind_host, port=unused_tcp_port, loop=event_loop)

    writer.write(IAC + WONT + TTYPE)
    server = yield from asyncio.wait_for(_waiter_connected, 0.5)

    class CustomException(Exception):
        pass

    # exercise, by connection_lost(exc=Exception())..
    server.writer.write('Bye!')
    server.connection_lost(CustomException('blah!'))

    # verify, custom exception is thrown into any yielding reader
    with pytest.raises(CustomException):
        yield from server.reader.read()


@pytest.mark.asyncio
def test_telnet_client_open_close_by_error(
        event_loop, bind_host, unused_tcp_port):
    """Exercise BaseClient.connection_lost() on error."""
    yield from event_loop.create_server(asyncio.Protocol,
                                        bind_host, unused_tcp_port)

    class GivenException(Exception):
        pass

    reader, writer = yield from telnetlib3.open_connection(
        host=bind_host, port=unused_tcp_port, connect_minwait=0.05)

    writer.protocol.connection_lost(GivenException("candy corn 4 everyone"))
    with pytest.raises(GivenException):
        yield from reader.read()


@pytest.mark.asyncio
def test_telnet_server_negotiation_fail(
        event_loop, bind_host, unused_tcp_port):
    """Test telnetlib3.TelnetServer() negotiation failure with client."""
    from telnetlib3.telopt import DO, TTYPE
    # given
    _waiter_connected = asyncio.Future()

    yield from telnetlib3.create_server(
        waiter_connected=_waiter_connected,
        host=bind_host, port=unused_tcp_port,
        loop=event_loop, connect_maxwait=0.05)

    reader, writer = yield from asyncio.open_connection(
        host=bind_host, port=unused_tcp_port, loop=event_loop)

    # exercise,
    reader.readexactly(3)  # IAC DO TTYPE, we ignore it!

    # negotiation then times out, deferring to waiter_connected.
    server = yield from asyncio.wait_for(_waiter_connected, 1.0)

    # verify,
    assert server.negotiation_should_advance() is False
    assert server.writer.pending_option[DO + TTYPE] == True


@pytest.mark.asyncio
def test_telnet_client_negotiation_fail(
        event_loop, bind_host, unused_tcp_port):
    """Test telnetlib3.TelnetCLient() negotiation failure with server."""

    class ClientNegotiationFail(telnetlib3.TelnetClient):
        def connection_made(self, transport):
            from telnetlib3.telopt import WILL, TTYPE
            super().connection_made(transport)
            # this creates a pending negotiation demand from the client-side.
            self.writer.iac(WILL, TTYPE)

    # a server that never responds with nothing.
    yield from event_loop.create_server(asyncio.Protocol,
                                        bind_host, unused_tcp_port)

    given_minwait = 0.05
    given_maxwait = 0.100

    import time
    stime = time.time()
    reader, writer = yield from asyncio.wait_for(telnetlib3.open_connection(
        client_factory=ClientNegotiationFail, host=bind_host,
        port=unused_tcp_port,
        connect_minwait=given_minwait,
        connect_maxwait=given_maxwait), 5)

    elapsed_ms = int((time.time() - stime) * 1e3)
    expected_ms = int(given_maxwait * 1e3)
    assert expected_ms <= elapsed_ms <= expected_ms + 50


@pytest.mark.asyncio
def test_telnet_server_cmdline(bind_host, unused_tcp_port, event_loop):
    """Test executing telnetlib3/server.py as server"""
    # this code may be reduced when pexpect asyncio is bugfixed ..
    import os
    import pexpect
    prog = pexpect.which('telnetlib3-server')
    args = [prog, bind_host, str(unused_tcp_port), '--loglevel=info',
            '--connect-maxwait=0.05']
    proc = yield from asyncio.create_subprocess_exec(
        *args, loop=event_loop, stderr=asyncio.subprocess.PIPE)

    while True:
        line = yield from asyncio.wait_for(proc.stderr.readline(), 0.5)
        if b'Server ready' in line:
            break

    # client connects,
    reader, writer = yield from asyncio.open_connection(
        host=bind_host, port=unused_tcp_port, loop=event_loop)

    # and closes,
    yield from reader.readexactly(3)  # IAC DO TTYPE, we ignore it!
    writer.close()

    while True:
        line = yield from asyncio.wait_for(proc.stderr.readline(), 0.5)
        if b'Connection closed' in line:
            break

    # send SIGTERM
    proc.terminate()

    # we would expect the server to gracefully end.
    yield from proc.communicate()
    yield from proc.wait()


@pytest.mark.asyncio
def test_telnet_client_cmdline(bind_host, unused_tcp_port, event_loop):
    """Test executing telnetlib3/client.py as client"""
    # this code may be reduced when pexpect asyncio is bugfixed ..
    # we especially need pexpect to pass sys.stdin.isatty() test.
    import os
    import pexpect
    prog = pexpect.which('telnetlib3-client')
    args = [prog, bind_host, str(unused_tcp_port), '--loglevel=info',
            '--connect-minwait=0.05', '--connect-maxwait=0.05']

    class HelloServer(asyncio.Protocol):
        def connection_made(self, transport):
            super().connection_made(transport)
            transport.write(b'hello, space cadet.\r\n')
            # hangup
            event_loop.call_soon(transport.close)

    # start vanilla tcp server
    yield from event_loop.create_server(HelloServer,
                                        bind_host, unused_tcp_port)

    proc = yield from asyncio.create_subprocess_exec(
        *args, loop=event_loop,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE)

    line = yield from asyncio.wait_for(proc.stdout.readline(), 1.5)
    assert line.strip() == b'hello, space cadet.'

    # message received, expect the client to gracefully quit.
    yield from asyncio.wait_for(proc.communicate(), 1)


@pytest.mark.asyncio
def test_telnet_client_tty_cmdline(bind_host, unused_tcp_port,
                                   event_loop):
    """Test executing telnetlib3/client.py as client using a tty (pexpect)"""
    # this code may be reduced when pexpect asyncio is bugfixed ..
    # we especially need pexpect to pass sys.stdin.isatty() test.
    import os
    import pexpect
    prog, args = 'telnetlib3-client', [
        bind_host, str(unused_tcp_port), '--loglevel=warn',
        '--connect-minwait=0.05', '--connect-maxwait=0.05']

    class HelloServer(asyncio.Protocol):
        def connection_made(self, transport):
            super().connection_made(transport)
            transport.write(b'hello, space cadet.\r\n')
            event_loop.call_soon(transport.close)

    # start vanilla tcp server
    yield from event_loop.create_server(HelloServer,
                                        bind_host, unused_tcp_port)
    import sys
    proc = pexpect.spawn(prog, args)
    yield from proc.expect(pexpect.EOF, async=True, timeout=5)
    assert proc.before.splitlines()[0] == b'hello, space cadet.'


@pytest.mark.asyncio
def test_telnet_client_cmdline_stdin_pipe(bind_host, unused_tcp_port,
                                          event_loop):
    """Test sending data through command-line client (by os PIPE)."""
    # this code may be reduced when pexpect asyncio is bugfixed ..
    # we especially need pexpect to pass sys.stdin.isatty() test.
    import os
    import pexpect

    prog = pexpect.which('telnetlib3-client')
    args = [prog, bind_host, str(unused_tcp_port), '--loglevel=info',
            '--connect-minwait=0.05', '--connect-maxwait=0.05']

    @asyncio.coroutine
    def shell(reader, writer):
        writer.write('hello ')
        inp = yield from reader.read(1)
        if inp:
            writer.echo(inp)
            writer.write('\r\ngoodbye.\r\n')
        yield from writer.drain()
        writer.close()

    # start server
    yield from telnetlib3.create_server(
        host=bind_host, port=unused_tcp_port,
        shell=shell, loop=event_loop,
        connect_maxwait=0.05)

    # start client by way of pipe
    proc = yield from asyncio.create_subprocess_exec(
        *args, loop=event_loop,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE)

    # message received, expect the client to gracefully quit.
    output = yield from asyncio.wait_for(proc.communicate(b'X'), 1)
    assert output[0] == b'hello X\r\ngoodbye.\r\n'
