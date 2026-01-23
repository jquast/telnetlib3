"""Test instantiation of basic server and client forms."""

# std imports
import os
import sys
import time
import asyncio
import platform
import tempfile

# 3rd party
import pytest
import pexpect

# local
# local imports
import telnetlib3
from telnetlib3.tests.accessories import (  # pylint: disable=unused-import
    bind_host,
    unused_tcp_port,
)


async def test_create_server(bind_host, unused_tcp_port):
    """Test telnetlib3.create_server basic instantiation."""
    # local
    from telnetlib3.tests.accessories import create_server

    async with create_server(host=bind_host, port=unused_tcp_port):
        pass


# disabled by jquast Sun Feb 17 13:44:15 PST 2019,
# we need to await completion of full negotiation, travis-ci
# is failing with additional, 'failed-reply:DO BINARY'
# async def test_open_connection(bind_host, unused_tcp_port):
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


async def test_create_server_conditionals(bind_host, unused_tcp_port):
    """Test telnetlib3.create_server conditionals."""
    # local
    from telnetlib3.tests.accessories import create_server

    async with create_server(
        protocol_factory=lambda: telnetlib3.TelnetServer,
        host=bind_host,
        port=unused_tcp_port,
    ):
        pass


async def test_create_server_on_connect(bind_host, unused_tcp_port):
    """Test on_connect() anonymous function callback of create_server."""
    # local
    from telnetlib3.tests.accessories import create_server, asyncio_connection

    call_tracker = {"called": False}

    class TrackingProtocol(asyncio.Protocol):
        def __init__(self):
            call_tracker["called"] = True

    async with create_server(
        protocol_factory=TrackingProtocol, host=bind_host, port=unused_tcp_port
    ):
        async with asyncio_connection(bind_host, unused_tcp_port) as (reader, writer):
            await asyncio.sleep(0.01)
            assert call_tracker["called"]


async def test_telnet_server_open_close(bind_host, unused_tcp_port):
    """Test telnetlib3.TelnetServer() instantiation and connection_made()."""
    # local
    from telnetlib3.telopt import IAC, WONT, TTYPE
    from telnetlib3.tests.accessories import create_server, asyncio_connection

    _waiter = asyncio.Future()
    async with create_server(_waiter_connected=_waiter, host=bind_host, port=unused_tcp_port):
        async with asyncio_connection(bind_host, unused_tcp_port) as (
            stream_reader,
            stream_writer,
        ):
            stream_writer.write(IAC + WONT + TTYPE + b"bye\r")
            server = await asyncio.wait_for(_waiter, 0.5)
            server.writer.write("Goodbye!")
            server.writer.close()
            await server.writer.wait_closed()
            assert server.writer.is_closing()
            result = await stream_reader.read()
            assert result == b"\xff\xfd\x18Goodbye!"


async def test_telnet_client_open_close_by_write(bind_host, unused_tcp_port):
    """Exercise BaseClient.connection_lost() on writer closed."""
    # local
    from telnetlib3.tests.accessories import asyncio_server, open_connection

    async with asyncio_server(asyncio.Protocol, bind_host, unused_tcp_port):
        async with open_connection(host=bind_host, port=unused_tcp_port, connect_minwait=0.05) as (
            reader,
            writer,
        ):
            writer.close()
            await writer.wait_closed()
            assert not await reader.read()
            assert writer.is_closing()


async def test_telnet_client_open_closed_by_peer(bind_host, unused_tcp_port):
    """Exercise BaseClient.connection_lost()."""
    # local
    from telnetlib3.tests.accessories import asyncio_server, open_connection

    class DisconnecterProtocol(asyncio.Protocol):
        def connection_made(self, transport):
            # disconnect on connect
            transport.close()

    async with asyncio_server(DisconnecterProtocol, bind_host, unused_tcp_port):
        async with open_connection(host=bind_host, port=unused_tcp_port, connect_minwait=0.05) as (
            reader,
            writer,
        ):
            # read until EOF, no data received.
            data_received = await reader.read()
            assert not data_received


async def test_telnet_server_advanced_negotiation(bind_host, unused_tcp_port):
    """Test telnetlib3.TelnetServer() advanced negotiation."""
    # local
    from telnetlib3.telopt import (
        DO,
        SB,
        IAC,
        SGA,
        ECHO,
        NAWS,
        WILL,
        TTYPE,
        BINARY,
        CHARSET,
        NEW_ENVIRON,
    )
    from telnetlib3.tests.accessories import create_server, asyncio_connection

    _waiter = asyncio.Future()

    class ServerTestAdvanced(telnetlib3.TelnetServer):
        def begin_advanced_negotiation(self):
            super().begin_advanced_negotiation()
            _waiter.set_result(self)

    async with create_server(
        protocol_factory=ServerTestAdvanced, host=bind_host, port=unused_tcp_port
    ):
        async with asyncio_connection(bind_host, unused_tcp_port) as (reader, writer):
            writer.write(IAC + WILL + TTYPE)
            server = await asyncio.wait_for(_waiter, 0.5)

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


async def test_telnet_server_closed_by_client(bind_host, unused_tcp_port):
    """Exercise TelnetServer.connection_lost."""
    # local
    from telnetlib3.tests.accessories import create_server, asyncio_connection

    _waiter = asyncio.Future()

    async with create_server(_waiter_closed=_waiter, host=bind_host, port=unused_tcp_port):
        async with asyncio_connection(bind_host, unused_tcp_port) as (reader, writer):
            writer.close()
            await writer.wait_closed()

            srv_instance = await asyncio.wait_for(_waiter, 0.5)
            assert srv_instance._closing

            srv_instance.connection_lost(exc=None)


async def test_telnet_server_eof_by_client(bind_host, unused_tcp_port):
    """Exercise TelnetServer.eof_received()."""
    # local
    from telnetlib3.tests.accessories import create_server, asyncio_connection

    _waiter = asyncio.Future()

    async with create_server(_waiter_closed=_waiter, host=bind_host, port=unused_tcp_port):
        async with asyncio_connection(bind_host, unused_tcp_port) as (reader, writer):
            writer.write_eof()

            srv_instance = await asyncio.wait_for(_waiter, 0.5)
            assert srv_instance._closing


async def test_telnet_server_closed_by_server(bind_host, unused_tcp_port):
    """Exercise TelnetServer.connection_lost by close()."""
    # local
    from telnetlib3.telopt import DO, IAC, WONT, TTYPE
    from telnetlib3.tests.accessories import create_server, asyncio_connection

    _waiter_connected = asyncio.Future()
    _waiter_closed = asyncio.Future()

    async with create_server(
        _waiter_connected=_waiter_connected,
        _waiter_closed=_waiter_closed,
        host=bind_host,
        port=unused_tcp_port,
    ):
        async with asyncio_connection(bind_host, unused_tcp_port) as (reader, writer):
            expect_hello = IAC + DO + TTYPE
            hello_reply = IAC + WONT + TTYPE + b"quit" + b"\r\n"

            hello = await reader.readexactly(len(expect_hello))
            assert hello == expect_hello

            writer.write(hello_reply)
            server = await asyncio.wait_for(_waiter_connected, 0.5)

            server.writer.close()
            await server.writer.wait_closed()

            await asyncio.wait_for(_waiter_closed, 0.5)


async def test_telnet_server_idle_duration(bind_host, unused_tcp_port):
    """Exercise TelnetServer.idle property."""
    # local
    from telnetlib3.telopt import IAC, WONT, TTYPE
    from telnetlib3.tests.accessories import create_server, asyncio_connection

    _waiter_connected = asyncio.Future()
    _waiter_closed = asyncio.Future()

    async with create_server(
        _waiter_connected=_waiter_connected,
        _waiter_closed=_waiter_closed,
        host=bind_host,
        port=unused_tcp_port,
    ):
        async with asyncio_connection(bind_host, unused_tcp_port) as (reader, writer):
            writer.write(IAC + WONT + TTYPE)
            server = await asyncio.wait_for(_waiter_connected, 0.5)

            assert 0 <= server.idle <= 0.5
            assert 0 <= server.duration <= 0.5


async def test_telnet_client_idle_duration_minwait(bind_host, unused_tcp_port):
    """Exercise TelnetClient.idle property and minimum connection time."""
    # local
    from telnetlib3.tests.accessories import asyncio_server, open_connection

    async with asyncio_server(asyncio.Protocol, bind_host, unused_tcp_port):
        given_minwait = 0.100

        stime = time.time()
        async with open_connection(
            host=bind_host,
            port=unused_tcp_port,
            connect_minwait=given_minwait,
            connect_maxwait=given_minwait,
        ) as (reader, writer):
            elapsed_ms = int((time.time() - stime) * 1e3)
            expected_ms = int(given_minwait * 1e3)
            assert expected_ms <= elapsed_ms <= expected_ms + 50

            assert 0 <= writer.protocol.idle <= 0.5
            assert 0 <= writer.protocol.duration <= 0.5


async def test_telnet_server_closed_by_error(bind_host, unused_tcp_port):
    """Exercise TelnetServer.connection_lost by exception."""
    # local
    from telnetlib3.telopt import IAC, WONT, TTYPE
    from telnetlib3.tests.accessories import create_server, asyncio_connection

    _waiter_connected = asyncio.Future()
    _waiter_closed = asyncio.Future()

    async with create_server(
        _waiter_connected=_waiter_connected,
        _waiter_closed=_waiter_closed,
        host=bind_host,
        port=unused_tcp_port,
    ):
        async with asyncio_connection(bind_host, unused_tcp_port) as (reader, writer):
            writer.write(IAC + WONT + TTYPE)
            server = await asyncio.wait_for(_waiter_connected, 0.5)

            class CustomException(Exception):
                pass

            server.writer.write("Bye!")
            server.connection_lost(CustomException("blah!"))

            with pytest.raises(CustomException):
                await server.reader.read()


async def test_telnet_client_open_close_by_error(bind_host, unused_tcp_port):
    """Exercise BaseClient.connection_lost() on error."""
    # local
    from telnetlib3.tests.accessories import asyncio_server, open_connection

    class GivenException(Exception):
        pass

    async with asyncio_server(asyncio.Protocol, bind_host, unused_tcp_port):
        async with open_connection(host=bind_host, port=unused_tcp_port, connect_minwait=0.05) as (
            reader,
            writer,
        ):
            writer.protocol.connection_lost(GivenException("candy corn 4 everyone"))
            with pytest.raises(GivenException):
                await reader.read()


async def test_telnet_server_negotiation_fail(bind_host, unused_tcp_port):
    """Test telnetlib3.TelnetServer() negotiation failure with client."""
    # local
    from telnetlib3.telopt import DO, TTYPE
    from telnetlib3.tests.accessories import create_server, asyncio_connection

    _waiter_connected = asyncio.Future()

    async with create_server(
        _waiter_connected=_waiter_connected,
        host=bind_host,
        port=unused_tcp_port,
        connect_maxwait=0.05,
    ):
        async with asyncio_connection(bind_host, unused_tcp_port) as (reader, writer):
            await reader.readexactly(3)  # IAC DO TTYPE, we ignore it!

            server = await asyncio.wait_for(_waiter_connected, 1.0)

            assert server.negotiation_should_advance() is False
            assert server.writer.pending_option[DO + TTYPE]

            assert repr(server.writer) == (
                "<TelnetWriter server "
                "mode:local +lineflow -xon_any +slc_sim "
                "failed-reply:DO TTYPE>"
            )


async def test_telnet_client_negotiation_fail(bind_host, unused_tcp_port):
    """Test telnetlib3.TelnetCLient() negotiation failure with server."""
    # local
    from telnetlib3.tests.accessories import asyncio_server, open_connection

    class ClientNegotiationFail(telnetlib3.TelnetClient):
        def connection_made(self, transport):
            # local
            from telnetlib3.telopt import WILL, TTYPE

            super().connection_made(transport)
            self.writer.iac(WILL, TTYPE)

    async with asyncio_server(asyncio.Protocol, bind_host, unused_tcp_port):
        given_minwait = 0.05
        given_maxwait = 0.100

        stime = time.time()
        async with open_connection(
            client_factory=ClientNegotiationFail,
            host=bind_host,
            port=unused_tcp_port,
            connect_minwait=given_minwait,
            connect_maxwait=given_maxwait,
        ) as (reader, writer):
            elapsed_ms = int((time.time() - stime) * 1e3)
            expected_ms = int(given_maxwait * 1e3)
            assert expected_ms <= elapsed_ms <= expected_ms + 50


async def test_telnet_server_as_module():
    """Test __main__ hook, when executing python -m telnetlib3.server --help."""
    prog = sys.executable
    args = [prog, "-m", "telnetlib3.server", "--help"]
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )

    # we would expect the script to display help output and exit
    help_output, _ = await proc.communicate()
    assert b"usage:" in help_output and b"server" in help_output
    await proc.wait()


@pytest.mark.skipif(sys.platform == "win32", reason="Signal handlers not supported on Windows")
async def test_telnet_server_cmdline(bind_host, unused_tcp_port):
    """Test executing telnetlib3/server.py as server."""
    # local
    from telnetlib3.tests.accessories import asyncio_connection

    prog = pexpect.which("telnetlib3-server")
    args = [
        prog,
        bind_host,
        str(unused_tcp_port),
        "--loglevel=info",
        "--connect-maxwait=0.05",
    ]
    proc = await asyncio.create_subprocess_exec(*args, stderr=asyncio.subprocess.PIPE)

    seen = b""
    while True:
        line = await asyncio.wait_for(proc.stderr.readline(), 1.5)
        if b"Server ready" in line:
            break
        seen += line
        assert line, seen.decode()

    async with asyncio_connection(bind_host, unused_tcp_port) as (reader, writer):
        await reader.readexactly(3)  # IAC DO TTYPE, we ignore it!

    seen = b""
    while True:
        line = await asyncio.wait_for(proc.stderr.readline(), 1.5)
        if b"Connection closed" in line:
            break
        seen += line
        assert line, seen.decode()

    proc.terminate()

    await proc.communicate()
    await proc.wait()


async def test_telnet_client_as_module():
    """Test __main__ hook, when executing python -m telnetlib3.client --help."""
    prog = sys.executable
    args = [prog, "-m", "telnetlib3.client", "--help"]
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )

    # we would expect the script to display help output and exit
    help_output, _ = await proc.communicate()
    assert b"usage:" in help_output and b"client" in help_output
    await proc.wait()


@pytest.mark.skipif(sys.platform == "win32", reason="Client shell not implemented on Windows")
async def test_telnet_client_cmdline(bind_host, unused_tcp_port):
    """Test executing telnetlib3/client.py as client."""
    # local
    from telnetlib3.tests.accessories import asyncio_server

    prog = pexpect.which("telnetlib3-client")
    args = [
        prog,
        bind_host,
        str(unused_tcp_port),
        "--loglevel=info",
        "--connect-minwait=0.05",
        "--connect-maxwait=0.05",
    ]

    class HelloServer(asyncio.Protocol):
        def connection_made(self, transport):
            super().connection_made(transport)
            transport.write(b"hello, space cadet.\r\n")
            asyncio.get_event_loop().call_soon(transport.close)

    async with asyncio_server(HelloServer, bind_host, unused_tcp_port):
        proc = await asyncio.create_subprocess_exec(
            *args, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE
        )

        line = await asyncio.wait_for(proc.stdout.readline(), 1.5)
        assert line.strip() == b"Escape character is '^]'."

        line = await asyncio.wait_for(proc.stdout.readline(), 1.5)
        assert line.strip() == b"hello, space cadet."

        out, err = await asyncio.wait_for(proc.communicate(), 1)
        assert out == b"\x1b[m\nConnection closed by foreign host.\n"


@pytest.mark.skipif(
    tuple(map(int, platform.python_version_tuple())) > (3, 10),
    reason="those shabby pexpect maintainers still use @asyncio.coroutine",
)
async def test_telnet_client_tty_cmdline(bind_host, unused_tcp_port):
    """Test executing telnetlib3/client.py as client using a tty (pexpect)"""
    # local
    from telnetlib3.tests.accessories import asyncio_server

    prog, args = "telnetlib3-client", [
        bind_host,
        str(unused_tcp_port),
        "--loglevel=warning",
        "--connect-minwait=0.05",
        "--connect-maxwait=0.05",
    ]

    class HelloServer(asyncio.Protocol):
        def connection_made(self, transport):
            super().connection_made(transport)
            transport.write(b"hello, space cadet.\r\n")
            asyncio.get_event_loop().call_soon(transport.close)

    async with asyncio_server(HelloServer, bind_host, unused_tcp_port):
        proc = pexpect.spawn(prog, args)
        await proc.expect(pexpect.EOF, async_=True, timeout=5)
        assert proc.before == (
            b"Escape character is '^]'.\r\n"
            b"hello, space cadet.\r\r\n"
            b"\x1b[m\r\n"
            b"Connection closed by foreign host.\r\n"
        )


@pytest.mark.skipif(sys.platform == "win32", reason="Client shell not implemented on Windows")
async def test_telnet_client_cmdline_stdin_pipe(bind_host, unused_tcp_port):
    """Test sending data through command-line client (by os PIPE)."""
    # local
    from telnetlib3.tests.accessories import create_server

    prog = pexpect.which("telnetlib3-client")
    fd, logfile = tempfile.mkstemp(prefix="telnetlib3", suffix=".log")
    os.close(fd)

    args = [
        prog,
        bind_host,
        str(unused_tcp_port),
        "--loglevel=info",
        "--connect-minwait=0.15",
        "--connect-maxwait=0.15",
        f"--logfile={logfile}",
    ]

    async def shell(reader, writer):
        writer.write("Press Return to continue:")
        inp = await reader.readline()
        if inp:
            writer.echo(inp)
            writer.write("\ngoodbye.\n")
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    async with create_server(
        host=bind_host, port=unused_tcp_port, shell=shell, connect_maxwait=0.05
    ):
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout, stderr = await asyncio.wait_for(proc.communicate(b"\r"), 2)

        with open(logfile, encoding="utf-8") as f:
            logfile_output = f.read().splitlines()
        assert stdout == (
            b"Escape character is '^]'.\n"
            b"Press Return to continue:\r\ngoodbye.\n"
            b"\x1b[m\nConnection closed by foreign host.\n"
        )

        assert len(logfile_output) == 2, logfile
        assert "Connected to <Peer" in logfile_output[0], logfile
        assert "Connection closed to <Peer" in logfile_output[1], logfile
        os.unlink(logfile)
