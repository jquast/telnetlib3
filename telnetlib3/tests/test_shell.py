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
def test_telnet_shell_coroutine(event_loop, bind_host, unused_tcp_port, log):
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
def test_telnet_shell(event_loop, bind_host, unused_tcp_port, log):
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
