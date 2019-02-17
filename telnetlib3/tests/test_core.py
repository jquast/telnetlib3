"""Test instantiation of basic server and client forms."""
# std imports
import asyncio
import os
import sys
import tempfile
import time
import unittest.mock

# local imports
import telnetlib3
from telnetlib3.tests.accessories import (
    unused_tcp_port,
    event_loop,
    bind_host
)

# 3rd party
import pytest
import pexpect


@pytest.mark.asyncio
async def test_create_server(bind_host, unused_tcp_port):
    """Test telnetlib3.create_server basic instantiation."""
    # exercise,
    await telnetlib3.create_server(
        host=bind_host, port=unused_tcp_port)

# disabled by jquast Sun Feb 17 13:44:15 PST 2019,
# we need to await completion of full negotiation, travis-ci
# is failing with additional, 'failed-reply:DO BINARY'
#@pytest.mark.asyncio
#async def test_open_connection(bind_host, unused_tcp_port):
#    """Exercise telnetlib3.open_connection with default options."""
#    _waiter = asyncio.Future()
#    await telnetlib3.create_server(bind_host, unused_tcp_port,
#                                        _waiter_connected=_waiter,
#                                        connect_maxwait=0.05)
#    client_reader, client_writer = await telnetlib3.open_connection(
#        bind_host, unused_tcp_port, connect_minwait=0.05)
#    server = await asyncio.wait_for(_waiter, 0.5)
#    assert repr(server.writer) == (
#        '<TelnetWriter server mode:kludge +lineflow -xon_any +slc_sim '
#        'server-will:BINARY,ECHO,SGA '
#        'client-will:BINARY,CHARSET,NAWS,NEW_ENVIRON,TTYPE>')
#    assert repr(client_writer) == (
#        '<TelnetWriter client mode:kludge +lineflow -xon_any +slc_sim '
#        'client-will:BINARY,CHARSET,NAWS,NEW_ENVIRON,TTYPE '
#        'server-will:BINARY,ECHO,SGA>')


@pytest.mark.asyncio
async def test_create_server_conditionals(
        event_loop, bind_host, unused_tcp_port):
    """Test telnetlib3.create_server conditionals."""
    # exercise,
    await telnetlib3.create_server(
        protocol_factory=lambda: telnetlib3.TelnetServer,
        host=bind_host, port=unused_tcp_port,
        loop=event_loop)


@pytest.mark.asyncio
async def test_create_server_on_connect(event_loop, bind_host, unused_tcp_port):
    """Test on_connect() anonymous function callback of create_server."""
    # given,
    given_pf = unittest.mock.MagicMock()
    await telnetlib3.create_server(
        protocol_factory=given_pf,
        host=bind_host, port=unused_tcp_port,
        loop=event_loop)

    reader, writer = await asyncio.open_connection(
        host=bind_host, port=unused_tcp_port, loop=event_loop)

    # verify
    assert given_pf.called


@pytest.mark.asyncio
async def test_telnet_server_open_close(
        event_loop, bind_host, unused_tcp_port):
    """Test telnetlib3.TelnetServer() instantiation and connection_made()."""
    from telnetlib3.telopt import IAC, WONT, TTYPE
    # given,
    _waiter = asyncio.Future()
    await telnetlib3.create_server(
        _waiter_connected=_waiter,
        host=bind_host, port=unused_tcp_port,
        loop=event_loop)

    # exercise,
    reader, writer = await asyncio.open_connection(
        host=bind_host, port=unused_tcp_port, loop=event_loop)

    writer.write(IAC + WONT + TTYPE + b'bye\r')
    server = await asyncio.wait_for(_waiter, 0.5)
    server.writer.write('Goodbye!')
    server.writer.close()
    result = await reader.read()
    assert result == b'\xff\xfd\x18Goodbye!'


@pytest.mark.asyncio
async def test_telnet_client_open_close_by_write(
        event_loop, bind_host, unused_tcp_port):
    """Exercise BaseClient.connection_lost() on writer closed."""
    await event_loop.create_server(asyncio.Protocol,
                                        bind_host, unused_tcp_port)

    reader, writer = await telnetlib3.open_connection(
        host=bind_host, port=unused_tcp_port,
        connect_minwait=0.05)
    writer.close()
    assert (await reader.read()) == ''


@pytest.mark.asyncio
async def test_telnet_client_open_closed_by_peer(
        event_loop, bind_host, unused_tcp_port):
    """Exercise BaseClient.connection_lost()."""
    class DisconnecterProtocol(asyncio.Protocol):
        def connection_made(self, transport):
            # disconnect on connect
            transport.close()

    await event_loop.create_server(DisconnecterProtocol,
                                        bind_host, unused_tcp_port)

    reader, writer = await telnetlib3.open_connection(
        host=bind_host, port=unused_tcp_port,
        connect_minwait=0.05)

    # read until EOF, no data received.
    data_received = await reader.read()
    assert data_received == ''


@pytest.mark.asyncio
async def test_telnet_server_advanced_negotiation(
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

    await telnetlib3.create_server(
        protocol_factory=ServerTestAdvanced,
        host=bind_host, port=unused_tcp_port,
        loop=event_loop)

    reader, writer = await asyncio.open_connection(
        host=bind_host, port=unused_tcp_port, loop=event_loop)

    # exercise,
    writer.write(IAC + WILL + TTYPE)
    server = await asyncio.wait_for(_waiter, 0.5)

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
async def test_telnet_server_closed_by_client(
        event_loop, bind_host, unused_tcp_port):
    """Exercise TelnetServer.connection_lost."""
    # given
    _waiter = asyncio.Future()

    await telnetlib3.create_server(
        _waiter_closed=_waiter,
        host=bind_host, port=unused_tcp_port,
        loop=event_loop)

    reader, writer = await asyncio.open_connection(
        host=bind_host, port=unused_tcp_port, loop=event_loop)

    # exercise,
    writer.close()

    # verify,
    srv_instance = await asyncio.wait_for(_waiter, 0.5)
    assert srv_instance._closing

    # exercise, a 2nd call to .connection_lost() returns early,
    # allowing callbacks the freedom to call it at any time from
    # the server-end to dump the client.
    srv_instance.connection_lost(exc=None)


@pytest.mark.asyncio
async def test_telnet_server_eof_by_client(
        event_loop, bind_host, unused_tcp_port):
    """Exercise TelnetServer.eof_received()."""
    # given
    _waiter = asyncio.Future()

    await telnetlib3.create_server(
        _waiter_closed=_waiter,
        host=bind_host, port=unused_tcp_port,
        loop=event_loop)

    reader, writer = await asyncio.open_connection(
        host=bind_host, port=unused_tcp_port, loop=event_loop)

    # exercise,
    writer.write_eof()

    # verify,
    srv_instance = await asyncio.wait_for(_waiter, 0.5)
    assert srv_instance._closing


@pytest.mark.asyncio
async def test_telnet_server_closed_by_server(
        event_loop, bind_host, unused_tcp_port):
    """Exercise TelnetServer.connection_lost by close()."""
    from telnetlib3.telopt import IAC, DO, WONT, TTYPE

    # given
    _waiter_connected = asyncio.Future()
    _waiter_closed = asyncio.Future()

    await telnetlib3.create_server(
        _waiter_connected=_waiter_connected,
        _waiter_closed=_waiter_closed,
        host=bind_host, port=unused_tcp_port,
        loop=event_loop)

    reader, writer = await asyncio.open_connection(
        host=bind_host, port=unused_tcp_port, loop=event_loop)

    # data received by client, connection is made
    expect_hello = IAC + DO + TTYPE
    hello_reply = IAC + WONT + TTYPE + b'quit' + b'\r\n'

    # exercise,
    hello = await reader.readexactly(len(expect_hello))

    # verify,
    assert hello == expect_hello

    # exercise,
    writer.write(hello_reply)
    server = await asyncio.wait_for(_waiter_connected, 0.5)

    # exercise, by closing.
    server.writer.close()

    # verify
    await asyncio.wait_for(_waiter_closed, 0.5)


@pytest.mark.asyncio
async def test_telnet_server_idle_duration(
        event_loop, bind_host, unused_tcp_port):
    """Exercise TelnetServer.idle property."""
    from telnetlib3.telopt import IAC, WONT, TTYPE

    # given
    _waiter_connected = asyncio.Future()
    _waiter_closed = asyncio.Future()

    await telnetlib3.create_server(
        _waiter_connected=_waiter_connected,
        _waiter_closed=_waiter_closed,
        host=bind_host, port=unused_tcp_port,
        loop=event_loop)

    reader, writer = await asyncio.open_connection(
        host=bind_host, port=unused_tcp_port, loop=event_loop)

    writer.write(IAC + WONT + TTYPE)
    server = await asyncio.wait_for(_waiter_connected, 0.5)

    # verify
    assert 0 <= server.idle <= 0.5
    assert 0 <= server.duration <= 0.5


@pytest.mark.asyncio
async def test_telnet_client_idle_duration_minwait(
        event_loop, bind_host, unused_tcp_port):
    """Exercise TelnetClient.idle property and minimum connection time."""
    from telnetlib3.telopt import IAC, WONT, TTYPE

    # a server that doesn't care
    await event_loop.create_server(asyncio.Protocol,
                                        bind_host, unused_tcp_port)

    given_minwait = 0.100

    stime = time.time()
    reader, writer = await telnetlib3.open_connection(
        host=bind_host, port=unused_tcp_port, loop=event_loop,
        connect_minwait=given_minwait, connect_maxwait=given_minwait)

    elapsed_ms = int((time.time() - stime) * 1e3)
    expected_ms = int(given_minwait * 1e3)
    assert expected_ms <= elapsed_ms <= expected_ms + 50

    # verify
    assert 0 <= writer.protocol.idle <= 0.5
    assert 0 <= writer.protocol.duration <= 0.5


@pytest.mark.asyncio
async def test_telnet_server_closed_by_error(
        event_loop, bind_host, unused_tcp_port):
    """Exercise TelnetServer.connection_lost by exception."""
    from telnetlib3.telopt import IAC, DO, WONT, TTYPE

    # given
    _waiter_connected = asyncio.Future()
    _waiter_closed = asyncio.Future()

    await telnetlib3.create_server(
        _waiter_connected=_waiter_connected,
        _waiter_closed=_waiter_closed,
        host=bind_host, port=unused_tcp_port,
        loop=event_loop)

    reader, writer = await asyncio.open_connection(
        host=bind_host, port=unused_tcp_port, loop=event_loop)

    writer.write(IAC + WONT + TTYPE)
    server = await asyncio.wait_for(_waiter_connected, 0.5)

    class CustomException(Exception):
        pass

    # exercise, by connection_lost(exc=Exception())..
    server.writer.write('Bye!')
    server.connection_lost(CustomException('blah!'))

    # verify, custom exception is thrown into any yielding reader
    with pytest.raises(CustomException):
        await server.reader.read()


@pytest.mark.asyncio
async def test_telnet_client_open_close_by_error(
        event_loop, bind_host, unused_tcp_port):
    """Exercise BaseClient.connection_lost() on error."""
    await event_loop.create_server(asyncio.Protocol,
                                        bind_host, unused_tcp_port)

    class GivenException(Exception):
        pass

    reader, writer = await telnetlib3.open_connection(
        host=bind_host, port=unused_tcp_port, connect_minwait=0.05)

    writer.protocol.connection_lost(GivenException("candy corn 4 everyone"))
    with pytest.raises(GivenException):
        await reader.read()


@pytest.mark.asyncio
async def test_telnet_server_negotiation_fail(
        event_loop, bind_host, unused_tcp_port):
    """Test telnetlib3.TelnetServer() negotiation failure with client."""
    from telnetlib3.telopt import DO, TTYPE
    # given
    _waiter_connected = asyncio.Future()

    await telnetlib3.create_server(
        _waiter_connected=_waiter_connected,
        host=bind_host, port=unused_tcp_port,
        loop=event_loop, connect_maxwait=0.05)

    reader, writer = await asyncio.open_connection(
        host=bind_host, port=unused_tcp_port, loop=event_loop)

    # exercise,
    await reader.readexactly(3)  # IAC DO TTYPE, we ignore it!

    # negotiation then times out, deferring to waiter_connected.
    server = await asyncio.wait_for(_waiter_connected, 1.0)

    # verify,
    assert server.negotiation_should_advance() is False
    assert server.writer.pending_option[DO + TTYPE] == True

    assert repr(server.writer) == ('<TelnetWriter server '
                                   'mode:local +lineflow -xon_any +slc_sim '
                                   'failed-reply:DO TTYPE>')

@pytest.mark.asyncio
async def test_telnet_client_negotiation_fail(
        event_loop, bind_host, unused_tcp_port):
    """Test telnetlib3.TelnetCLient() negotiation failure with server."""

    class ClientNegotiationFail(telnetlib3.TelnetClient):
        def connection_made(self, transport):
            from telnetlib3.telopt import WILL, TTYPE
            super().connection_made(transport)
            # this creates a pending negotiation demand from the client-side.
            self.writer.iac(WILL, TTYPE)

    # a server that never responds with nothing.
    await event_loop.create_server(asyncio.Protocol,
                                        bind_host, unused_tcp_port)

    given_minwait = 0.05
    given_maxwait = 0.100

    stime = time.time()
    reader, writer = await asyncio.wait_for(telnetlib3.open_connection(
        client_factory=ClientNegotiationFail, host=bind_host,
        port=unused_tcp_port,
        connect_minwait=given_minwait,
        connect_maxwait=given_maxwait), 5)

    elapsed_ms = int((time.time() - stime) * 1e3)
    expected_ms = int(given_maxwait * 1e3)
    assert expected_ms <= elapsed_ms <= expected_ms + 50


@pytest.mark.asyncio
async def test_telnet_server_as_module(event_loop):
    """Test __main__ hook, when executing python -m telnetlib3.server --help"""
    prog = sys.executable
    args = [prog, '-m', 'telnetlib3.server', '--help']
    proc = await asyncio.create_subprocess_exec(
        *args, loop=event_loop, stdout=asyncio.subprocess.PIPE,
                                stderr=asyncio.subprocess.PIPE)

    # we would expect the script to display help output and exit
    help_output, _ = await proc.communicate()
    assert help_output.startswith(b'usage: server.py [-h]')
    await proc.communicate()
    await proc.wait()


@pytest.mark.asyncio
async def test_telnet_server_cmdline(bind_host, unused_tcp_port, event_loop):
    """Test executing telnetlib3/server.py as server"""
    # this code may be reduced when pexpect asyncio is bugfixed ..
    prog = pexpect.which('telnetlib3-server')
    args = [prog, bind_host, str(unused_tcp_port), '--loglevel=info',
            '--connect-maxwait=0.05']
    proc = await asyncio.create_subprocess_exec(
        *args, loop=event_loop, stderr=asyncio.subprocess.PIPE)

    seen = b''
    while True:
        line = await asyncio.wait_for(proc.stderr.readline(), 0.5)
        if b'Server ready' in line:
            break
        seen += line
        assert line, seen.decode()  # EOF reached

    # client connects,
    reader, writer = await asyncio.open_connection(
        host=bind_host, port=unused_tcp_port, loop=event_loop)

    # and closes,
    await reader.readexactly(3)  # IAC DO TTYPE, we ignore it!
    writer.close()

    seen = b''
    while True:
        line = await asyncio.wait_for(proc.stderr.readline(), 0.5)
        if b'Connection closed' in line:
            break
        seen += line
        assert line, seen.decode()  # EOF reached

    # send SIGTERM
    proc.terminate()

    # we would expect the server to gracefully end.
    await proc.communicate()
    await proc.wait()


@pytest.mark.asyncio
async def test_telnet_client_as_module(event_loop):
    """Test __main__ hook, when executing python -m telnetlib3.client --help"""
    prog = sys.executable
    args = [prog, '-m', 'telnetlib3.client', '--help']
    proc = await asyncio.create_subprocess_exec(
        *args, loop=event_loop, stdout=asyncio.subprocess.PIPE,
                                stderr=asyncio.subprocess.PIPE)

    # we would expect the script to display help output and exit
    help_output, _ = await proc.communicate()
    assert help_output.startswith(b'usage: client.py [-h]')
    await proc.communicate()
    await proc.wait()


@pytest.mark.asyncio
async def test_telnet_client_cmdline(bind_host, unused_tcp_port, event_loop):
    """Test executing telnetlib3/client.py as client"""
    # this code may be reduced when pexpect asyncio is bugfixed ..
    # we especially need pexpect to pass sys.stdin.isatty() test.
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
    await event_loop.create_server(HelloServer,
                                        bind_host, unused_tcp_port)

    proc = await asyncio.create_subprocess_exec(
        *args, loop=event_loop,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE)

    line = await asyncio.wait_for(proc.stdout.readline(), 1.5)
    assert line.strip() == b"Escape character is '^]'."

    line = await asyncio.wait_for(proc.stdout.readline(), 1.5)
    assert line.strip() == b'hello, space cadet.'

    # message received, expect the client to gracefully quit.
    out, err = await asyncio.wait_for(proc.communicate(), 1)
    assert out == b'\x1b[m\nConnection closed by foreign host.\n'


@pytest.mark.asyncio
async def test_telnet_client_tty_cmdline(bind_host, unused_tcp_port,
                                   event_loop):
    """Test executing telnetlib3/client.py as client using a tty (pexpect)"""
    # this code may be reduced when pexpect asyncio is bugfixed ..
    # we especially need pexpect to pass sys.stdin.isatty() test.
    prog, args = 'telnetlib3-client', [
        bind_host, str(unused_tcp_port), '--loglevel=warning',
        '--connect-minwait=0.05', '--connect-maxwait=0.05']

    class HelloServer(asyncio.Protocol):
        def connection_made(self, transport):
            super().connection_made(transport)
            transport.write(b'hello, space cadet.\r\n')
            event_loop.call_soon(transport.close)

    # start vanilla tcp server
    await event_loop.create_server(HelloServer,
                                        bind_host, unused_tcp_port)
    proc = pexpect.spawn(prog, args)
    await proc.expect(pexpect.EOF, async_=True, timeout=5)
    # our 'space cadet' has \r\n hardcoded, so \r\r\n happens, ignore it
    assert proc.before == (b"Escape character is '^]'.\r\n"
                           b"hello, space cadet.\r\r\n"
                           b"\x1b[m\r\n"
                           b"Connection closed by foreign host.\r\n")

@pytest.mark.asyncio
async def test_telnet_client_cmdline_stdin_pipe(bind_host, unused_tcp_port,
                                          event_loop):
    """Test sending data through command-line client (by os PIPE)."""
    # this code may be reduced when pexpect asyncio is bugfixed ..
    # we especially need pexpect to pass sys.stdin.isatty() test.
    prog = pexpect.which('telnetlib3-client')
    fd, logfile = tempfile.mkstemp(prefix='telnetlib3', suffix='.log')
    os.close(fd)

    args = [prog, bind_host, str(unused_tcp_port), '--loglevel=info',
            '--connect-minwait=0.15', '--connect-maxwait=0.15',
            '--logfile={0}'.format(logfile)]

    @asyncio.coroutine
    def shell(reader, writer):
        writer.write('Press Return to continue:')
        inp = yield from reader.readline()
        if inp:
            writer.echo(inp)
            writer.write('\ngoodbye.\n')
        yield from writer.drain()
        writer.close()

    # start server
    await telnetlib3.create_server(
        host=bind_host, port=unused_tcp_port,
        shell=shell, loop=event_loop,
        connect_maxwait=0.05)

    # start client by way of pipe
    proc = await asyncio.create_subprocess_exec(
        *args, loop=event_loop,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE)

    #line = await asyncio.wait_for(proc.stdout.readline(), 1.5)
    #assert line.strip() == b"Escape character is '^]'."

    # message received, expect the client to gracefully quit.
    stdout, stderr = await asyncio.wait_for(proc.communicate(b'\r'), 2)

    # And finally, analyze the contents of the logfile,
    # - 2016-03-18 20:19:25,227 INFO client_base.py:113 Connected to <Peer 127.0.0.1 51237>
    # - 2016-03-18 20:19:25,286 INFO client_base.py:67 Connection closed to <Peer 127.0.0.1 51237>
    logfile_output = open(logfile).read().splitlines()
    assert stdout == (b"Escape character is '^]'.\n"
                      b"Press Return to continue:\r\ngoodbye.\n"
                      b"\x1b[m\nConnection closed by foreign host.\n")

    # verify,
    assert len(logfile_output) == 2, logfile
    assert 'Connected to <Peer' in logfile_output[0], logfile
    assert 'Connection closed to <Peer' in logfile_output[1], logfile
    os.unlink(logfile)
