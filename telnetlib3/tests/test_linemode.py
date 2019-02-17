"""Test LINEMODE, rfc-1184_."""
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
async def test_server_demands_remote_linemode_client_agrees(
        event_loop, bind_host, unused_tcp_port):
    from telnetlib3.telopt import IAC, DO, WILL, LINEMODE, SB, SE
    from telnetlib3.slc import (LMODE_MODE, LMODE_MODE_ACK)

    _waiter = asyncio.Future()

    class ServerTestLinemode(telnetlib3.BaseServer):
        def begin_negotiation(self):
            super().begin_negotiation()
            self.writer.iac(DO, LINEMODE)
            self._loop.call_later(0.1, self.connection_lost, None)

    await telnetlib3.create_server(
        protocol_factory=ServerTestLinemode,
        host=bind_host, port=unused_tcp_port,
        loop=event_loop, _waiter_connected=_waiter)

    client_reader, client_writer = await asyncio.open_connection(
        host=bind_host, port=unused_tcp_port, loop=event_loop)

    expect_mode = telnetlib3.stream_writer.TelnetWriter.default_linemode.mask
    expect_stage1 = IAC + DO + LINEMODE
    expect_stage2 = IAC + SB + LINEMODE + LMODE_MODE + expect_mode + IAC + SE

    reply_mode = bytes([ord(expect_mode) | ord(LMODE_MODE_ACK)])
    reply_stage1 = IAC + WILL + LINEMODE
    reply_stage2 = IAC + SB + LINEMODE + LMODE_MODE + reply_mode + IAC + SE

    result = await client_reader.readexactly(len(expect_stage1))
    assert result == expect_stage1
    client_writer.write(reply_stage1)

    result = await client_reader.readexactly(len(expect_stage2))
    assert result == expect_stage2
    client_writer.write(reply_stage2)

    srv_instance = await asyncio.wait_for(_waiter, 0.1)
    assert not any(srv_instance.writer.pending_option.values())

    result = await client_reader.read()
    assert result == b''

    assert srv_instance.writer.mode == 'remote'
    assert srv_instance.writer.linemode.remote is True
    assert srv_instance.writer.linemode.local is False
    assert srv_instance.writer.linemode.trapsig is False
    assert srv_instance.writer.linemode.ack is True
    assert srv_instance.writer.linemode.soft_tab is False
    assert srv_instance.writer.linemode.lit_echo is True
    assert srv_instance.writer.remote_option.enabled(LINEMODE)


@pytest.mark.asyncio
async def test_server_demands_remote_linemode_client_demands_local(
        event_loop, bind_host, unused_tcp_port):
    from telnetlib3.telopt import IAC, DO, WILL, LINEMODE, SB, SE
    from telnetlib3.slc import (LMODE_MODE, LMODE_MODE_LOCAL, LMODE_MODE_ACK)

    _waiter = asyncio.Future()

    class ServerTestLinemode(telnetlib3.BaseServer):
        def begin_negotiation(self):
            super().begin_negotiation()
            self.writer.iac(DO, LINEMODE)
            self._loop.call_later(0.1, self.connection_lost, None)

    await telnetlib3.create_server(
        protocol_factory=ServerTestLinemode,
        host=bind_host, port=unused_tcp_port,
        loop=event_loop, _waiter_connected=_waiter)

    client_reader, client_writer = await asyncio.open_connection(
        host=bind_host, port=unused_tcp_port, loop=event_loop)

    expect_mode = telnetlib3.stream_writer.TelnetWriter.default_linemode.mask
    expect_stage1 = IAC + DO + LINEMODE
    expect_stage2 = IAC + SB + LINEMODE + LMODE_MODE + expect_mode + IAC + SE

    # No, we demand local mode -- using ACK will finalize such request
    reply_mode = bytes([ord(LMODE_MODE_LOCAL) | ord(LMODE_MODE_ACK)])
    reply_stage1 = IAC + WILL + LINEMODE
    reply_stage2 = IAC + SB + LINEMODE + LMODE_MODE + reply_mode + IAC + SE

    result = await client_reader.readexactly(len(expect_stage1))
    assert result == expect_stage1
    client_writer.write(reply_stage1)

    result = await client_reader.readexactly(len(expect_stage2))
    assert result == expect_stage2
    client_writer.write(reply_stage2)

    srv_instance = await asyncio.wait_for(_waiter, 0.1)
    assert not any(srv_instance.writer.pending_option.values())

    result = await client_reader.read()
    assert result == b''

    assert srv_instance.writer.mode == 'local'
    assert srv_instance.writer.linemode.remote is False
    assert srv_instance.writer.linemode.local is True
    assert srv_instance.writer.linemode.trapsig is False
    assert srv_instance.writer.linemode.ack is True
    assert srv_instance.writer.linemode.soft_tab is False
    assert srv_instance.writer.linemode.lit_echo is False
    assert srv_instance.writer.remote_option.enabled(LINEMODE)
