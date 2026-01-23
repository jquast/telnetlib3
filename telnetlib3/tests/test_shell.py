"""Test the server's shell(reader, writer) callback."""

# std imports
import asyncio
import logging

# local
# local imports
import telnetlib3
from telnetlib3.tests.accessories import bind_host, unused_tcp_port


async def test_telnet_server_shell_as_coroutine(bind_host, unused_tcp_port):
    """Test callback shell(reader, writer) as coroutine of create_server()."""
    # local
    from telnetlib3.telopt import DO, IAC, WONT, TTYPE

    # given,
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

    # exercise,
    await telnetlib3.create_server(host=bind_host, port=unused_tcp_port, shell=shell)

    reader, writer = await asyncio.open_connection(host=bind_host, port=unused_tcp_port)

    # given, verify IAC DO TTYPE
    hello = await asyncio.wait_for(reader.readexactly(len(expect_hello)), 0.5)
    assert hello == expect_hello

    # exercise,
    # respond 'WONT TTYPE' to quickly complete negotiation as failed.
    writer.write(hello_reply)

    # await for the shell callback to be ready,
    await asyncio.wait_for(_waiter, 0.5)

    # client sends input, reads shell output response
    writer.write(send_input.encode("ascii"))
    server_output = await asyncio.wait_for(reader.readexactly(len(expect_output)), 0.5)

    # verify,
    assert server_output.decode("ascii") == expect_output

    # nothing more to read from server; server writer closed in shell.
    result = await reader.read()
    assert result == b""


async def test_telnet_client_shell_as_coroutine(bind_host, unused_tcp_port):
    """Test callback shell(reader, writer) as coroutine of create_server()."""
    _waiter = asyncio.Future()

    async def shell(reader, writer):
        # just hang up
        _waiter.set_result(True)

    # a server that doesn't care
    await asyncio.get_event_loop().create_server(asyncio.Protocol, bind_host, unused_tcp_port)

    reader, writer = await telnetlib3.open_connection(
        host=bind_host,
        port=unused_tcp_port,
        shell=shell,
        connect_minwait=0.05,
    )

    await asyncio.wait_for(_waiter, 0.5)


async def test_telnet_server_shell_make_coro_by_function(bind_host, unused_tcp_port):
    """Test callback shell(reader, writer) as function, for create_server()."""
    # local
    from telnetlib3.telopt import DO, IAC, WONT, TTYPE

    # given,
    _waiter = asyncio.Future()

    def shell(reader, writer):
        _waiter.set_result(True)

    # exercise,
    await telnetlib3.create_server(host=bind_host, port=unused_tcp_port, shell=shell)

    reader, writer = await asyncio.open_connection(host=bind_host, port=unused_tcp_port)

    # exercise, cancel negotiation and await for the shell callback
    writer.write(IAC + WONT + TTYPE)

    # verify,
    await asyncio.wait_for(_waiter, 0.5)


async def test_telnet_server_no_shell(bind_host, unused_tcp_port):
    """Test telnetlib3.TelnetServer() instantiation and connection_made()."""
    # local
    from telnetlib3.telopt import DO, IAC, WONT, TTYPE

    _waiter = asyncio.Future()
    client_expected = IAC + DO + TTYPE + b"beta"
    server_expected = IAC + WONT + TTYPE + b"alpha"
    # given,
    await telnetlib3.create_server(_waiter_connected=_waiter, host=bind_host, port=unused_tcp_port)

    # exercise,
    client_reader, client_writer = await asyncio.open_connection(
        host=bind_host, port=unused_tcp_port
    )

    client_writer.write(IAC + WONT + TTYPE + b"alpha")

    server = await asyncio.wait_for(_waiter, 0.5)
    server.writer.write("beta")
    server.writer.close()
    client_recv = await client_reader.read()
    assert client_recv == client_expected


async def test_telnet_server_given_shell(bind_host, unused_tcp_port):
    """Iterate all state-reading commands of default telnet_server_shell."""
    # local
    from telnetlib3 import telnet_server_shell
    from telnetlib3.telopt import DO, IAC, SGA, ECHO, WILL, WONT, TTYPE, BINARY

    # given
    _waiter = asyncio.Future()
    await telnetlib3.create_server(
        host=bind_host,
        port=unused_tcp_port,
        shell=telnet_server_shell,
        _waiter_connected=_waiter,
        connect_maxwait=0.05,
        timeout=1.25,
        limit=13377,
    )

    reader, writer = await asyncio.open_connection(host=bind_host, port=unused_tcp_port)

    expected = IAC + DO + TTYPE
    result = await asyncio.wait_for(reader.readexactly(len(expected)), 0.5)
    assert result == expected

    writer.write(IAC + WONT + TTYPE)

    expected = b"Ready.\r\ntel:sh> "
    result = await asyncio.wait_for(reader.readexactly(len(expected)), 0.5)
    assert result == expected

    server = await asyncio.wait_for(_waiter, 0.5)
    server_port = str(server._transport.get_extra_info("peername")[1])

    # Command & Response table
    cmd_output_table = (
        # exercise backspace in input for help command
        (
            (b"\bhel\blp\r"),
            (b"\r\nquit, writer, slc, toggle [option|all], reader, proto, dump" b"\r\ntel:sh> "),
        ),
        (
            b"writer\r\x00",
            (b"\r\n<TelnetWriter server mode:local +lineflow -xon_any +slc_sim>" b"\r\ntel:sh> "),
        ),
        (
            b"reader\r\n",
            (
                b"\r\n<TelnetReaderUnicode encoding='US-ASCII' limit=13377 buflen=1 eof=False>"
                b"\r\ntel:sh> "
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
        assert result == output_expected and timed_out == False

    # nothing more to read.
    result = await reader.read()
    assert result == b""


async def test_telnet_server_shell_eof(bind_host, unused_tcp_port):
    """Test EOF in telnet_server_shell()."""
    # local
    from telnetlib3 import telnet_server_shell
    from telnetlib3.telopt import IAC, WONT, TTYPE

    # given
    _waiter_connected = asyncio.Future()
    _waiter_closed = asyncio.Future()

    await telnetlib3.create_server(
        host=bind_host,
        port=unused_tcp_port,
        _waiter_connected=_waiter_connected,
        _waiter_closed=_waiter_closed,
        shell=telnet_server_shell,
        timeout=0.25,
    )

    reader, writer = await asyncio.open_connection(host=bind_host, port=unused_tcp_port)
    writer.write(IAC + WONT + TTYPE)

    await asyncio.wait_for(_waiter_connected, 0.5)
    writer.close()
    await asyncio.wait_for(_waiter_closed, 0.5)
