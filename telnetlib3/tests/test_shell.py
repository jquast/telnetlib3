"""Test the server's shell(reader, writer) callback."""
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
def test_telnet_shell_as_coroutine(event_loop, bind_host,
                                   unused_tcp_port, log):
    """Test callback shell(reader, writer) as coroutine of create_server()."""
    from telnetlib3.telopt import IAC, DO, WONT, TTYPE
    # given,
    _waiter = asyncio.Future()
    send_input = 'Alpha'
    expect_output = 'Beta'
    expect_hello = IAC + DO + TTYPE
    hello_reply = IAC + WONT + TTYPE

    @asyncio.coroutine
    def shell(reader, writer):
        _waiter.set_result(True)
        inp = yield from reader.readexactly(len(send_input))
        assert inp == send_input
        writer.write(expect_output)

    # exercise,
    yield from telnetlib3.create_server(
        host=bind_host, port=unused_tcp_port,
        shell=shell, loop=event_loop, log=log)

    reader, writer = yield from asyncio.open_connection(
        host=bind_host, port=unused_tcp_port, loop=event_loop)

    # given, verify IAC DO TTYPE
    hello = yield from reader.readexactly(len(expect_hello))
    assert hello == expect_hello

    # exercise,
    # respond 'WONT TTYPE' to quickly complete negotiation as failed.
    writer.write(hello_reply)

    # await for the shell callback
    yield from asyncio.wait_for(_waiter, 0.5)

    # client sends input, reads shell output response
    writer.write(send_input.encode('ascii'))
    server_output = yield from reader.readexactly(len(expect_output))

    # verify,
    assert server_output.decode('ascii') == expect_output


@pytest.mark.asyncio
def test_telnet_shell_make_coro_by_function(event_loop, bind_host,
                                            unused_tcp_port, log):
    """Test callback shell(reader, writer) as function, for create_server()."""
    from telnetlib3.telopt import IAC, DO, WONT, TTYPE
    # given,
    _waiter = asyncio.Future()

    def shell(reader, writer):
        _waiter.set_result(True)

    # exercise,
    yield from telnetlib3.create_server(
        host=bind_host, port=unused_tcp_port,
        shell=shell, loop=event_loop, log=log)

    reader, writer = yield from asyncio.open_connection(
        host=bind_host, port=unused_tcp_port, loop=event_loop)

    # exercise, cancel negotiation and await for the shell callback
    writer.write(IAC + WONT + TTYPE)

    # verify,
    yield from asyncio.wait_for(_waiter, 0.5)


@pytest.mark.asyncio
def test_telnet_server_no_shell(
        event_loop, bind_host, unused_tcp_port, log):
    """Test telnetlib3.TelnetServer() instantiation and connection_made()."""
    from telnetlib3.telopt import IAC, DO, WONT, TTYPE
    _waiter = asyncio.Future()
    client_expected = IAC + DO + TTYPE + b'beta'
    server_expected = IAC + WONT + TTYPE + b'alpha'
    # given,
    yield from telnetlib3.create_server(
        waiter_connected=_waiter,
        host=bind_host, port=unused_tcp_port,
        loop=event_loop, log=log)

    # exercise,
    client_reader, client_writer = yield from asyncio.open_connection(
        host=bind_host, port=unused_tcp_port, loop=event_loop)

    client_writer.write(IAC + WONT + TTYPE + b'alpha')

    server = yield from asyncio.wait_for(_waiter, 0.5)
    server.writer.write('beta')
    server.writer.close()
    client_recv = yield from client_reader.read()
    assert client_recv == client_expected


@pytest.mark.asyncio
def test_telnet_given_shell(
        event_loop, bind_host, unused_tcp_port, log):
    """Iterate all state-reading commands of default telnet_shell."""
    from telnetlib3.telopt import IAC, WILL, DO, WONT, ECHO, SGA, BINARY, TTYPE
    from telnetlib3 import telnet_server_shell
    # given
    _waiter = asyncio.Future()
    yield from telnetlib3.create_server(
        host=bind_host, port=unused_tcp_port,
        shell=telnet_server_shell,
        waiter_connected=_waiter,
        timeout=0.25, loop=event_loop, log=log)

    reader, writer = yield from asyncio.open_connection(
        host=bind_host, port=unused_tcp_port, loop=event_loop)

    expected = IAC + DO + TTYPE
    result = yield from reader.readexactly(len(expected))
    assert result == expected

    writer.write(IAC + WONT + TTYPE)

    expected = b'Ready.\r\ntel:sh> '
    result = yield from reader.readexactly(len(expected))
    assert result == expected

    server = yield from _waiter
    server_port = str(server._transport.get_extra_info('peername')[1])

    cmd_output_table = (
        # exercise backspace in input for help command
        ((b'\bhel\blp\r'), (
            b'\r\nquit, writer, slc, toggle [option|all], reader, proto'
            b'\r\ntel:sh> '
        )),
        (b'writer\r\x00', (
            b'\r\n<TelnetWriter server mode:local +lineflow -xon_any +slc_sim>'
            b'\r\ntel:sh> '
        )),
        (b'reader\r\n', (
            b"\r\n<TelnetReader encoding='US-ASCII'>"
            b'\r\ntel:sh> '
        )),
        (b'proto\n', (
            b'\r\n<Peer ' +
            bind_host.encode('ascii') + b' ' +
            server_port.encode('ascii') + b'>' +
            b'\r\ntel:sh> '
        )),
        (b'slc\r\n', (
            b'\r\nSpecial Line Characters:'
            b'\r\n         SLC_AO: (^O, variable|flushout)'
            b'\r\n         SLC_EC: (^?, variable)'
            b'\r\n         SLC_EL: (^U, variable)'
            b'\r\n         SLC_EW: (^W, variable)'
            b'\r\n         SLC_IP: (^C, variable|flushin|flushout)'
            b'\r\n         SLC_RP: (^R, variable)'
            b'\r\n        SLC_AYT: (^T, variable)'
            b'\r\n        SLC_EOF: (^D, variable)'
            b'\r\n        SLC_XON: (^Q, variable)'
            b'\r\n       SLC_SUSP: (^Z, variable|flushin)'
            b'\r\n       SLC_XOFF: (^S, variable)'
            b'\r\n      SLC_ABORT: (^\, variable|flushin|flushout)'
            b'\r\n      SLC_LNEXT: (^V, variable)'
            b'\r\nUnset by client: SLC_BRK, SLC_EOR, SLC_SYNCH'
            b'\r\nNot supported by server: SLC_EBOL, SLC_ECR, SLC_EEOL, '
            b'SLC_EWR, SLC_FORW1, SLC_FORW2, SLC_INSRT, SLC_MCBOL, '
            b'SLC_MCEOL, SLC_MCL, SLC_MCR, SLC_MCWL, SLC_MCWR, SLC_OVER'
            b'\r\ntel:sh> '
        )),
        (b'toggle\n', (
            b'\r\nbinary off'
            b'\r\necho off'
            b'\r\ngoahead ON'
            b'\r\ninbinary off'
            b'\r\nlflow ON'
            b'\r\noutbinary off'
            b'\r\nxon-any off'
            b'\r\ntel:sh> '
        )),
        (b'toggle not-an-option\r', (
            b'\r\ntoggle: not an option.'
            b'\r\ntel:sh> '
        )),
        (b'toggle all\r\n', (
            b'\r\n' +
            # negotiation options received,
            # though ignored by our dumb client.
            IAC + WILL + ECHO +
            IAC + WILL + SGA +
            IAC + WILL + BINARY +
            IAC + DO + BINARY +
            b'will echo.'
            b'\r\nwill suppress go-ahead.'
            b'\r\nwill outbinary.'
            b'\r\ndo inbinary.'
            b'\r\nxon-any enabled.'
            b'\r\nlineflow disabled.'
            b'\r\ntel:sh> '
        )),
        (b'toggle\n', (
            # and therefor the same state values remain unchanged --
            # with exception of lineflow and xon-any, which are
            # states toggled by the shell directly (and presumably
            # knows what to do with it!)
            b'\r\nbinary off'
            b'\r\necho off'
            b'\r\ngoahead ON'
            b'\r\ninbinary off'
            b'\r\nlflow off'  # flipped
            b'\r\noutbinary off'
            b'\r\nxon-any ON'  # flipped
            b'\r\ntel:sh> '
        )),
        (b'\r\n', (
            b'\r\ntel:sh> '
        )),
        (b'not-a-command\n', (
            b'\r\nno such command.'
            b'\r\ntel:sh> '
        )),
        (b'quit\r', b'\r\nGoodbye.\r\n'),
    )

    for (cmd, output_expected) in cmd_output_table:
        writer.write(cmd)
        try:
            result = yield from asyncio.wait_for(
                reader.readexactly(len(output_expected)), 0.5)
        except asyncio.streams.IncompleteReadError as err:
            result = err.partial
        assert result == output_expected, repr(cmd)

    # nothing more to read.
    result = yield from reader.read()
    assert result == b''


@pytest.mark.asyncio
def test_telnet_shell_eof(event_loop, bind_host, unused_tcp_port, log):
    """Test EOFError in telnet_shell()."""
    from telnetlib3.telopt import IAC, WONT, TTYPE
    from telnetlib3 import telnet_server_shell
    # given
    _waiter_connected = asyncio.Future()
    _waiter_closed = asyncio.Future()

    yield from telnetlib3.create_server(
        host=bind_host, port=unused_tcp_port,
        waiter_connected=_waiter_connected,
        waiter_closed=_waiter_closed,
        shell=telnet_server_shell,
        timeout=0.25, loop=event_loop, log=log)

    reader, writer = yield from asyncio.open_connection(
        host=bind_host, port=unused_tcp_port, loop=event_loop)
    writer.write(IAC + WONT + TTYPE)

    yield from asyncio.wait_for(_waiter_connected, 0.5)
    writer.close()
    yield from asyncio.wait_for(_waiter_closed, 0.5)
