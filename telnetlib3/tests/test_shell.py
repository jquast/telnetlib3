"""Test the server's shell(reader, writer) callback."""

# std imports
import asyncio
import logging

# local
# local imports
from telnetlib3.tests.accessories import (  # pylint: disable=unused-import
    bind_host,
    unused_tcp_port,
)


async def test_telnet_server_shell_as_coroutine(bind_host, unused_tcp_port):
    """Test callback shell(reader, writer) as coroutine of create_server()."""
    # local
    from telnetlib3.telopt import DO, IAC, WONT, TTYPE
    from telnetlib3.tests.accessories import create_server, asyncio_connection

    _waiter = asyncio.Future()
    send_input = "Alpha"
    expect_output = "Beta"
    expect_hello = IAC + DO + TTYPE
    hello_reply = IAC + WONT + TTYPE

    async def shell(reader, writer):
        _waiter.set_result(True)
        inp = await reader.readexactly(len(send_input))
        assert inp == send_input
        writer.write(expect_output)
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    async with create_server(host=bind_host, port=unused_tcp_port, shell=shell):
        async with asyncio_connection(bind_host, unused_tcp_port) as (reader, writer):
            # verify IAC DO TTYPE
            hello = await asyncio.wait_for(reader.readexactly(len(expect_hello)), 0.5)
            assert hello == expect_hello

            # respond 'WONT TTYPE' to quickly complete negotiation as failed.
            writer.write(hello_reply)

            # await for the shell callback to be ready,
            await asyncio.wait_for(_waiter, 0.5)

            # client sends input, reads shell output response
            writer.write(send_input.encode("ascii"))
            server_output = await asyncio.wait_for(reader.readexactly(len(expect_output)), 0.5)

            assert server_output.decode("ascii") == expect_output

            # nothing more to read from server; server writer closed in shell.
            result = await reader.read()
            assert result == b""


async def test_telnet_client_shell_as_coroutine(bind_host, unused_tcp_port):
    """Test callback shell(reader, writer) as coroutine of create_server()."""
    # local
    from telnetlib3.tests.accessories import asyncio_server, open_connection

    _waiter = asyncio.Future()

    async def shell(reader, writer):
        # just hang up
        _waiter.set_result(True)

    # a server that doesn't care
    async with asyncio_server(asyncio.Protocol, bind_host, unused_tcp_port):
        async with open_connection(
            host=bind_host,
            port=unused_tcp_port,
            shell=shell,
            connect_minwait=0.05,
        ) as (reader, writer):
            await asyncio.wait_for(_waiter, 0.5)


async def test_telnet_server_shell_make_coro_by_function(bind_host, unused_tcp_port):
    """Test callback shell(reader, writer) as function, for create_server()."""
    # local
    from telnetlib3.telopt import IAC, WONT, TTYPE
    from telnetlib3.tests.accessories import create_server, asyncio_connection

    _waiter = asyncio.Future()

    def shell(reader, writer):
        _waiter.set_result(True)

    async with create_server(host=bind_host, port=unused_tcp_port, shell=shell):
        async with asyncio_connection(bind_host, unused_tcp_port) as (reader, writer):
            # cancel negotiation and await for the shell callback
            writer.write(IAC + WONT + TTYPE)

            await asyncio.wait_for(_waiter, 0.5)


async def test_telnet_server_no_shell(bind_host, unused_tcp_port):
    """Test telnetlib3.TelnetServer() instantiation and connection_made()."""
    # local
    from telnetlib3.telopt import DO, IAC, WONT, TTYPE
    from telnetlib3.tests.accessories import create_server, asyncio_connection

    client_expected = IAC + DO + TTYPE + b"beta"

    async with create_server(host=bind_host, port=unused_tcp_port) as server:
        async with asyncio_connection(bind_host, unused_tcp_port) as (
            client_reader,
            client_writer,
        ):
            client_writer.write(IAC + WONT + TTYPE + b"alpha")

            srv_instance = await asyncio.wait_for(server.wait_for_client(), 0.5)
            srv_instance.writer.write("beta")
            srv_instance.writer.close()
            await srv_instance.writer.wait_closed()
            client_recv = await client_reader.read()
            assert client_recv == client_expected


async def test_telnet_server_given_shell(
    bind_host, unused_tcp_port
):  # pylint: disable=too-many-locals
    """Iterate all state-reading commands of default telnet_server_shell."""
    # local
    from telnetlib3 import telnet_server_shell
    from telnetlib3.telopt import DO, IAC, SGA, ECHO, WILL, WONT, TTYPE, BINARY
    from telnetlib3.tests.accessories import create_server, asyncio_connection

    async with create_server(
        host=bind_host,
        port=unused_tcp_port,
        shell=telnet_server_shell,
        connect_maxwait=0.05,
        timeout=1.25,
        limit=13377,
    ) as server:
        async with asyncio_connection(bind_host, unused_tcp_port) as (reader, writer):
            expected = IAC + DO + TTYPE
            result = await asyncio.wait_for(reader.readexactly(len(expected)), 0.5)
            assert result == expected

            writer.write(IAC + WONT + TTYPE)

            expected = b"Ready.\r\ntel:sh> "
            result = await asyncio.wait_for(reader.readexactly(len(expected)), 0.5)
            assert result == expected

            srv_instance = await asyncio.wait_for(server.wait_for_client(), 0.5)
            server_port = str(srv_instance._transport.get_extra_info("peername")[1])

            # Command & Response table
            cmd_output_table = (
                # exercise backspace in input for help command
                (
                    (b"\bhel\blp\r"),
                    (
                        b"\r\nquit, writer, slc, toggle [option|all], reader, proto, dump"
                        b"\r\ntel:sh> "
                    ),
                ),
                (
                    b"writer\r\x00",
                    (
                        b"\r\n<TelnetWriter server mode:local +lineflow -xon_any +slc_sim>"
                        b"\r\ntel:sh> "
                    ),
                ),
                (
                    b"reader\r\n",
                    (
                        b"\r\n<TelnetReaderUnicode encoding='US-ASCII' "
                        b"limit=13377 buflen=1 eof=False>\r\ntel:sh> "
                    ),
                ),
                (
                    b"proto\n",
                    (
                        b"\r\n<Peer "
                        + bind_host.encode("ascii")
                        + b" "
                        + server_port.encode("ascii")
                        + b">"
                        + b"\r\ntel:sh> "
                    ),
                ),
                (
                    b"slc\r\n",
                    (
                        b"\r\nSpecial Line Characters:"
                        b"\r\n         SLC_AO: (^O, variable|flushout)"
                        b"\r\n         SLC_EC: (^?, variable)"
                        b"\r\n         SLC_EL: (^U, variable)"
                        b"\r\n         SLC_EW: (^W, variable)"
                        b"\r\n         SLC_IP: (^C, variable|flushin|flushout)"
                        b"\r\n         SLC_RP: (^R, variable)"
                        b"\r\n        SLC_AYT: (^T, variable)"
                        b"\r\n        SLC_EOF: (^D, variable)"
                        b"\r\n        SLC_XON: (^Q, variable)"
                        b"\r\n       SLC_SUSP: (^Z, variable|flushin)"
                        b"\r\n       SLC_XOFF: (^S, variable)"
                        b"\r\n      SLC_ABORT: (^\\, variable|flushin|flushout)"
                        b"\r\n      SLC_LNEXT: (^V, variable)"
                        b"\r\nUnset by client: SLC_BRK, SLC_EOR, SLC_SYNCH"
                        b"\r\nNot supported by server: SLC_EBOL, SLC_ECR, SLC_EEOL, "
                        b"SLC_EWR, SLC_FORW1, SLC_FORW2, SLC_INSRT, SLC_MCBOL, "
                        b"SLC_MCEOL, SLC_MCL, SLC_MCR, SLC_MCWL, SLC_MCWR, SLC_OVER"
                        b"\r\ntel:sh> "
                    ),
                ),
                (
                    b"toggle\n",
                    (
                        b"\r\nbinary off"
                        b"\r\necho off"
                        b"\r\ngoahead ON"
                        b"\r\ninbinary off"
                        b"\r\nlflow ON"
                        b"\r\noutbinary off"
                        b"\r\nxon-any off"
                        b"\r\ntel:sh> "
                    ),
                ),
                (b"toggle not-an-option\r", (b"\r\ntoggle: not an option." b"\r\ntel:sh> ")),
                (
                    b"toggle all\r\n",
                    (
                        b"\r\n" +
                        # negotiation options received,
                        # though ignored by our dumb client.
                        IAC
                        + WILL
                        + ECHO
                        + IAC
                        + WILL
                        + SGA
                        + IAC
                        + WILL
                        + BINARY
                        + IAC
                        + DO
                        + BINARY
                        + b"will echo."
                        b"\r\nwill suppress go-ahead."
                        b"\r\nwill outbinary."
                        b"\r\ndo inbinary."
                        b"\r\nxon-any enabled."
                        b"\r\nlineflow disabled."
                        b"\r\ntel:sh> "
                    ),
                ),
                (
                    b"toggle\n",
                    (
                        # and therefore the same state values remain unchanged --
                        # with exception of lineflow and xon-any, which are
                        # states toggled by the shell directly (and presumably
                        # knows what to do with it!)
                        b"\r\nbinary off"
                        b"\r\necho off"
                        b"\r\ngoahead ON"
                        b"\r\ninbinary off"
                        b"\r\nlflow off"  # flipped
                        b"\r\noutbinary off"
                        b"\r\nxon-any ON"  # flipped
                        b"\r\ntel:sh> "
                    ),
                ),
                (b"\r\n", (b"\r\ntel:sh> ")),
                (b"not-a-command\n", (b"\r\nno such command." b"\r\ntel:sh> ")),
                (b"quit\r", b"\r\nGoodbye.\r\n"),
            )

            for cmd, output_expected in cmd_output_table:
                logging.debug("cmd=%r, output_expected=%r", cmd, output_expected)
                writer.write(cmd)
                await writer.drain()
                timed_out = False
                try:
                    result = await asyncio.wait_for(reader.readexactly(len(output_expected)), 0.5)
                except asyncio.IncompleteReadError as err:
                    result = err.partial
                except TimeoutError:
                    result = await reader.read(1024)
                else:
                    if result != output_expected:
                        # fetch extra output, if any, for better understanding of error
                        result += await reader.read(1024)
                assert result == output_expected and timed_out is False

            # nothing more to read.
            result = await reader.read()
            assert result == b""


async def test_telnet_server_shell_eof(bind_host, unused_tcp_port):
    """Test EOF in telnet_server_shell()."""
    # local
    from telnetlib3 import telnet_server_shell
    from telnetlib3.telopt import IAC, WONT, TTYPE
    from telnetlib3.tests.accessories import create_server, asyncio_connection

    async with create_server(
        host=bind_host,
        port=unused_tcp_port,
        shell=telnet_server_shell,
        timeout=0.25,
    ) as server:
        async with asyncio_connection(bind_host, unused_tcp_port) as (reader, writer):
            writer.write(IAC + WONT + TTYPE)
            srv_instance = await asyncio.wait_for(server.wait_for_client(), 0.5)
        # Wait for server to process client disconnect
        await asyncio.sleep(0.05)
        assert srv_instance._closing


async def test_telnet_server_shell_version_command(bind_host, unused_tcp_port):
    """Test version command in telnet_server_shell."""
    # local
    from telnetlib3 import accessories, telnet_server_shell
    from telnetlib3.telopt import DO, IAC, WONT, TTYPE
    from telnetlib3.tests.accessories import create_server, asyncio_connection

    async with create_server(
        host=bind_host,
        port=unused_tcp_port,
        shell=telnet_server_shell,
        connect_maxwait=0.05,
    ):
        async with asyncio_connection(bind_host, unused_tcp_port) as (reader, writer):
            expected = IAC + DO + TTYPE
            result = await asyncio.wait_for(reader.readexactly(len(expected)), 0.5)
            assert result == expected

            writer.write(IAC + WONT + TTYPE)

            expected = b"Ready.\r\ntel:sh> "
            result = await asyncio.wait_for(reader.readexactly(len(expected)), 0.5)
            assert result == expected

            writer.write(b"version\r")
            await asyncio.sleep(0.05)

            result = b""
            while True:
                try:
                    chunk = await asyncio.wait_for(reader.read(100), 0.2)
                    if not chunk:
                        break
                    result += chunk
                    if b"tel:sh>" in result:
                        break
                except asyncio.TimeoutError:
                    break

            expected_version = accessories.get_version()
            assert expected_version.encode("ascii") in result


async def test_telnet_server_shell_dump_with_kb_limit(bind_host, unused_tcp_port):
    """Test dump command with explicit kb_limit."""
    # local
    from telnetlib3 import telnet_server_shell
    from telnetlib3.telopt import DO, IAC, WONT, TTYPE
    from telnetlib3.tests.accessories import create_server, asyncio_connection

    async with create_server(
        host=bind_host,
        port=unused_tcp_port,
        shell=telnet_server_shell,
        connect_maxwait=0.05,
    ):
        async with asyncio_connection(bind_host, unused_tcp_port) as (reader, writer):
            expected = IAC + DO + TTYPE
            await asyncio.wait_for(reader.readexactly(len(expected)), 0.5)
            writer.write(IAC + WONT + TTYPE)

            expected = b"Ready.\r\ntel:sh> "
            await asyncio.wait_for(reader.readexactly(len(expected)), 0.5)

            writer.write(b"dump 0\r")
            await asyncio.sleep(0.05)

            result = b""
            while True:
                try:
                    chunk = await asyncio.wait_for(reader.read(200), 0.2)
                    if not chunk:
                        break
                    result += chunk
                    if b"wrote 0 bytes" in result:
                        break
                except asyncio.TimeoutError:
                    break

            assert b"kb_limit=0" in result
            assert b"wrote 0 bytes" in result


async def test_telnet_server_shell_dump_with_all_options(bind_host, unused_tcp_port):
    """Test dump command with all options including close."""
    # local
    from telnetlib3 import telnet_server_shell
    from telnetlib3.telopt import DO, IAC, WONT, TTYPE
    from telnetlib3.tests.accessories import create_server, asyncio_connection

    async with create_server(
        host=bind_host,
        port=unused_tcp_port,
        shell=telnet_server_shell,
        connect_maxwait=0.05,
    ):
        async with asyncio_connection(bind_host, unused_tcp_port) as (reader, writer):
            expected = IAC + DO + TTYPE
            await asyncio.wait_for(reader.readexactly(len(expected)), 0.5)
            writer.write(IAC + WONT + TTYPE)

            expected = b"Ready.\r\ntel:sh> "
            await asyncio.wait_for(reader.readexactly(len(expected)), 0.5)

            writer.write(b"dump 0 0 nodrain close\r")
            await asyncio.sleep(0.05)

            result = b""
            while True:
                try:
                    chunk = await asyncio.wait_for(reader.read(300), 0.2)
                    if not chunk:
                        break
                    result += chunk
                except asyncio.TimeoutError:
                    break

            assert b"kb_limit=0" in result
            assert b"do_close=True" in result
            assert b"drain=True" in result


async def test_telnet_server_shell_dump_nodrain(bind_host, unused_tcp_port):
    """Test dump command with nodrain option."""
    # local
    from telnetlib3 import telnet_server_shell
    from telnetlib3.telopt import DO, IAC, WONT, TTYPE
    from telnetlib3.tests.accessories import create_server, asyncio_connection

    async with create_server(
        host=bind_host,
        port=unused_tcp_port,
        shell=telnet_server_shell,
        connect_maxwait=0.05,
    ):
        async with asyncio_connection(bind_host, unused_tcp_port) as (reader, writer):
            expected = IAC + DO + TTYPE
            await asyncio.wait_for(reader.readexactly(len(expected)), 0.5)
            writer.write(IAC + WONT + TTYPE)

            expected = b"Ready.\r\ntel:sh> "
            await asyncio.wait_for(reader.readexactly(len(expected)), 0.5)

            writer.write(b"dump 0 0 drain\r")
            await asyncio.sleep(0.05)

            result = b""
            while True:
                try:
                    chunk = await asyncio.wait_for(reader.read(200), 0.2)
                    if not chunk:
                        break
                    result += chunk
                    if b"drain=False" in result:
                        break
                except asyncio.TimeoutError:
                    break

            assert b"kb_limit=0" in result
            assert b"drain=False" in result


async def test_telnet_server_shell_dump_large_output(bind_host, unused_tcp_port):
    """Test dump command with larger output."""
    # local
    from telnetlib3 import telnet_server_shell
    from telnetlib3.telopt import DO, IAC, WONT, TTYPE
    from telnetlib3.tests.accessories import create_server, asyncio_connection

    async with create_server(
        host=bind_host,
        port=unused_tcp_port,
        shell=telnet_server_shell,
        connect_maxwait=0.05,
    ):
        async with asyncio_connection(bind_host, unused_tcp_port) as (reader, writer):
            expected = IAC + DO + TTYPE
            await asyncio.wait_for(reader.readexactly(len(expected)), 0.5)
            writer.write(IAC + WONT + TTYPE)

            expected = b"Ready.\r\ntel:sh> "
            await asyncio.wait_for(reader.readexactly(len(expected)), 0.5)

            writer.write(b"dump 1\r")
            await asyncio.sleep(0.05)

            result = b""
            while True:
                try:
                    chunk = await asyncio.wait_for(reader.read(4096), 0.5)
                    if not chunk:
                        break
                    result += chunk
                    if b"wrote" in result and b"bytes" in result:
                        break
                except asyncio.TimeoutError:
                    break

            assert b"kb_limit=1" in result
            assert b"/" in result or b"\\" in result
