"""Test TSPEED, rfc-1079_."""

# std imports
import asyncio

# 3rd party
import pytest

# local
# local imports
import telnetlib3
import telnetlib3.stream_writer
from telnetlib3.tests.accessories import bind_host, unused_tcp_port


async def test_telnet_server_on_tspeed(bind_host, unused_tcp_port):
    """Test Server's callback method on_tspeed()."""
    from telnetlib3.telopt import IS, SB, SE, IAC, WILL, TSPEED
    from telnetlib3.tests.accessories import create_server, asyncio_connection

    _waiter = asyncio.Future()

    class ServerTestTspeed(telnetlib3.TelnetServer):
        def on_tspeed(self, rx, tx):
            super().on_tspeed(rx, tx)
            _waiter.set_result(self)

    async with create_server(
        protocol_factory=ServerTestTspeed, host=bind_host, port=unused_tcp_port
    ):
        async with asyncio_connection(bind_host, unused_tcp_port) as (reader, writer):
            writer.write(IAC + WILL + TSPEED)
            writer.write(IAC + SB + TSPEED + IS + b"123,456" + IAC + SE)

            srv_instance = await asyncio.wait_for(_waiter, 0.5)
            assert srv_instance.get_extra_info("tspeed") == "123,456"


async def test_telnet_client_send_tspeed(bind_host, unused_tcp_port):
    """Test Client's callback method send_tspeed()."""
    from telnetlib3.tests.accessories import create_server, open_connection

    _waiter = asyncio.Future()
    given_rx, given_tx = 1337, 1919

    class ServerTestTspeed(telnetlib3.TelnetServer):
        def on_tspeed(self, rx, tx):
            super().on_tspeed(rx, tx)
            _waiter.set_result((rx, tx))

        def begin_advanced_negotiation(self):
            from telnetlib3.telopt import DO, TSPEED

            super().begin_advanced_negotiation()
            self.writer.iac(DO, TSPEED)

    async with create_server(
        protocol_factory=ServerTestTspeed, host=bind_host, port=unused_tcp_port
    ):
        async with open_connection(
            host=bind_host,
            port=unused_tcp_port,
            tspeed=(given_rx, given_tx),
            connect_minwait=0.05,
        ) as (reader, writer):
            recv_rx, recv_tx = await asyncio.wait_for(_waiter, 0.5)
            assert recv_rx == given_rx
            assert recv_tx == given_tx
