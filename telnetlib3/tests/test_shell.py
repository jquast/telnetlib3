"""Test the server's shell(reader, writer) callback."""
# std imports
import asyncio

# local imports
import telnetlib3
import telnetlib3.stream_writer
from telnetlib3.tests.accessories import (
    server_factory,
    unused_tcp_port,
    event_loop,
    bind_host,
    log
)

# 3rd party
import pytest


@pytest.mark.asyncio
def test_telnet_shell(event_loop, server_factory, bind_host,
                      unused_tcp_port, log):
    """Test callback shell(reader, writer) of create_server()."""
    from telnetlib3.telopt import (
        IAC, DO, WONT, TTYPE,
    )
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

    yield from telnetlib3.create_server(
        host=bind_host, port=unused_tcp_port,
        shell=shell, loop=event_loop, log=log)

    reader, writer = yield from asyncio.open_connection(
        host=bind_host, port=unused_tcp_port, loop=event_loop)

    # respond 'WONT TTYPE' to quickly complete negotiation as failed.
    hello = yield from reader.readexactly(len(expect_hello))
    assert hello == expect_hello
    writer.write(hello_reply)

    # now, await for the shell callback, send input
    yield from asyncio.wait_for(_waiter, 0.5)
    writer.write(send_input.encode('ascii'))
    server_output = yield from reader.readexactly(len(expect_output))
    assert server_output.decode('ascii') == expect_output
