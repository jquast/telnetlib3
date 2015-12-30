"""Test XDISPLOC, rfc-1096_."""
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
def test_telnet_server_on_charset(
        event_loop, bind_host, unused_tcp_port, log):
    """Test Server's callback method on_charset()."""
    # given
    from telnetlib3.telopt import (
        IAC, WILL, WONT, SB, SE, TTYPE, CHARSET, ACCEPTED
    )
    _waiter = asyncio.Future()
    given_charset = 'KOI8-U'

    class ServerTestCharset(telnetlib3.TelnetServer):
        def on_charset(self, charset):
            super().on_charset(charset)
            _waiter.set_result(self)

    yield from telnetlib3.create_server(
        protocol_factory=ServerTestCharset,
        host=bind_host, port=unused_tcp_port,
        loop=event_loop, log=log)

    reader, writer = yield from asyncio.open_connection(
        host=bind_host, port=unused_tcp_port, loop=event_loop)

    val = yield from reader.readexactly(3)
    # exercise,
    writer.write(IAC + WILL + CHARSET)
    writer.write(IAC + WONT + TTYPE)
    writer.write(IAC + SB + CHARSET + ACCEPTED +
                 given_charset.encode('ascii') +
                 IAC + SE)

    # verify,
    srv_instance = yield from asyncio.wait_for(_waiter, 2.0)
    assert srv_instance.get_extra_info('charset') == given_charset
