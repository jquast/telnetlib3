"""Test LINEMODE, rfc-1184_."""

# std imports
import asyncio

# local
# local imports
import telnetlib3
import telnetlib3.stream_writer
from telnetlib3.tests.accessories import (  # pylint: disable=unused-import
    bind_host,
    unused_tcp_port,
)


async def test_server_demands_remote_linemode_client_agrees(  # pylint: disable=too-many-locals
    bind_host, unused_tcp_port
):
    # local
    from telnetlib3.slc import LMODE_MODE, LMODE_MODE_ACK
    from telnetlib3.telopt import DO, SB, SE, IAC, WILL, LINEMODE
    from telnetlib3.tests.accessories import create_server, asyncio_connection

    class ServerTestLinemode(telnetlib3.BaseServer):
        def begin_negotiation(self):
            super().begin_negotiation()
            self.writer.iac(DO, LINEMODE)
            asyncio.get_event_loop().call_later(0.1, self.connection_lost, None)

    async with create_server(
        protocol_factory=ServerTestLinemode,
        host=bind_host,
        port=unused_tcp_port,
    ) as server:
        async with asyncio_connection(bind_host, unused_tcp_port) as (
            client_reader,
            client_writer,
        ):
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

            srv_instance = await asyncio.wait_for(server.wait_for_client(), 0.1)
            assert not any(srv_instance.writer.pending_option.values())

            result = await client_reader.read()
            assert result == b""

            assert srv_instance.writer.mode == "remote"
            assert srv_instance.writer.linemode.remote is True
            assert srv_instance.writer.linemode.local is False
            assert srv_instance.writer.linemode.trapsig is False
            assert srv_instance.writer.linemode.ack is True
            assert srv_instance.writer.linemode.soft_tab is False
            assert srv_instance.writer.linemode.lit_echo is True
            assert srv_instance.writer.remote_option.enabled(LINEMODE)


async def test_server_demands_remote_linemode_client_demands_local(  # pylint: disable=too-many-locals
    bind_host, unused_tcp_port
):
    # local
    from telnetlib3.slc import LMODE_MODE, LMODE_MODE_ACK, LMODE_MODE_LOCAL
    from telnetlib3.telopt import DO, SB, SE, IAC, WILL, LINEMODE
    from telnetlib3.tests.accessories import create_server, asyncio_connection

    class ServerTestLinemode(telnetlib3.BaseServer):
        def begin_negotiation(self):
            super().begin_negotiation()
            self.writer.iac(DO, LINEMODE)
            asyncio.get_event_loop().call_later(0.1, self.connection_lost, None)

    async with create_server(
        protocol_factory=ServerTestLinemode,
        host=bind_host,
        port=unused_tcp_port,
    ) as server:
        async with asyncio_connection(bind_host, unused_tcp_port) as (
            client_reader,
            client_writer,
        ):
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

            srv_instance = await asyncio.wait_for(server.wait_for_client(), 0.1)
            assert not any(srv_instance.writer.pending_option.values())

            result = await client_reader.read()
            assert result == b""

            assert srv_instance.writer.mode == "local"
            assert srv_instance.writer.linemode.remote is False
            assert srv_instance.writer.linemode.local is True
            assert srv_instance.writer.linemode.trapsig is False
            assert srv_instance.writer.linemode.ack is True
            assert srv_instance.writer.linemode.soft_tab is False
            assert srv_instance.writer.linemode.lit_echo is False
            assert srv_instance.writer.remote_option.enabled(LINEMODE)
