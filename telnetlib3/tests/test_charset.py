"""Test XDISPLOC, rfc-1096_."""
# std imports
import asyncio

# local imports
import telnetlib3
import telnetlib3.stream_writer
from telnetlib3.tests.accessories import (
    unused_tcp_port,
    event_loop,
    bind_host
)

# 3rd party
import pytest


@pytest.mark.asyncio
async def test_telnet_server_on_charset(
        event_loop, bind_host, unused_tcp_port):
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

    await telnetlib3.create_server(
        protocol_factory=ServerTestCharset,
        host=bind_host, port=unused_tcp_port,
        loop=event_loop)

    reader, writer = await asyncio.open_connection(
        host=bind_host, port=unused_tcp_port, loop=event_loop)

    val = await asyncio.wait_for(reader.readexactly(3), 0.5)
    # exercise,
    writer.write(IAC + WILL + CHARSET)
    writer.write(IAC + WONT + TTYPE)
    writer.write(IAC + SB + CHARSET + ACCEPTED +
                 given_charset.encode('ascii') +
                 IAC + SE)

    # verify,
    srv_instance = await asyncio.wait_for(_waiter, 2.0)
    assert srv_instance.get_extra_info('charset') == given_charset


@pytest.mark.asyncio
async def test_telnet_client_send_charset(event_loop, bind_host, unused_tcp_port):
    """Test Client's callback method send_charset() selection for illegals."""
    # given
    _waiter = asyncio.Future()

    class ServerTestCharset(telnetlib3.TelnetServer):
        def on_request_charset(self):
            return ['illegal', 'cp437']

    class ClientTestCharset(telnetlib3.TelnetClient):
        def send_charset(self, offered):
            selected = super().send_charset(offered)
            _waiter.set_result(selected)
            return selected

    await asyncio.wait_for(
        telnetlib3.create_server(
            protocol_factory=ServerTestCharset,
            host=bind_host, port=unused_tcp_port,
            loop=event_loop),
        0.15)

    reader, writer = await asyncio.wait_for(
        telnetlib3.open_connection(
            client_factory=ClientTestCharset,
            host=bind_host, port=unused_tcp_port, loop=event_loop,
            encoding='latin1', connect_minwait=0.05),
        0.15)

    val = await asyncio.wait_for(_waiter, 1.5)
    assert val == 'cp437'
    assert writer.get_extra_info('charset') == 'cp437'


@pytest.mark.asyncio
async def test_telnet_client_no_charset(event_loop, bind_host, unused_tcp_port):
    """Test Client's callback method send_charset() does not select."""
    # given
    _waiter = asyncio.Future()

    class ServerTestCharset(telnetlib3.TelnetServer):
        def on_request_charset(self):
            return ['illegal', 'this-is-no-good-either']

    class ClientTestCharset(telnetlib3.TelnetClient):
        def send_charset(self, offered):
            selected = super().send_charset(offered)
            _waiter.set_result(selected)
            return selected

    await telnetlib3.create_server(
        protocol_factory=ServerTestCharset,
        host=bind_host, port=unused_tcp_port,
        loop=event_loop)

    reader, writer = await telnetlib3.open_connection(
        client_factory=ClientTestCharset,
        host=bind_host, port=unused_tcp_port, loop=event_loop,
        encoding='latin1', connect_minwait=0.05)

    # charset remains latin1
    val = await asyncio.wait_for(_waiter, 0.5)
    assert val == ''
    assert writer.get_extra_info('charset') == 'latin1'
