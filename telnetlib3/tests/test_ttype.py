"""Test TTYPE, rfc-930_."""

# std imports
import asyncio

# local
import telnetlib3
import telnetlib3.stream_writer
from telnetlib3.telopt import IS, SB, SE, IAC, WILL, TTYPE
from telnetlib3.tests.accessories import (  # pylint: disable=unused-import; pylint: disable=unused-import,
    bind_host,
    create_server,
    unused_tcp_port,
    asyncio_connection,
)


async def test_telnet_server_on_ttype(bind_host, unused_tcp_port):
    """Test Server's callback method on_ttype()."""
    _waiter = asyncio.Future()

    class ServerTestTtype(telnetlib3.TelnetServer):
        def on_ttype(self, ttype):
            super().on_ttype(ttype)
            _waiter.set_result(self)

    async with create_server(
        protocol_factory=ServerTestTtype, host=bind_host, port=unused_tcp_port
    ):
        async with asyncio_connection(bind_host, unused_tcp_port) as (reader, writer):
            writer.write(IAC + WILL + TTYPE)
            writer.write(IAC + SB + TTYPE + IS + b"ALPHA" + IAC + SE)
            writer.write(IAC + SB + TTYPE + IS + b"ALPHA" + IAC + SE)

            srv_instance = await asyncio.wait_for(_waiter, 0.5)
            assert "ALPHA" == srv_instance.get_extra_info("ttype1")
            assert "ALPHA" == srv_instance.get_extra_info("ttype2")
            assert "ALPHA" == srv_instance.get_extra_info("TERM")


async def test_telnet_server_on_ttype_beyond_max(bind_host, unused_tcp_port):
    """Test Server's callback method on_ttype() with long list."""
    _waiter = asyncio.Future()
    given_ttypes = (
        "ALPHA",
        "BETA",
        "GAMMA",
        "DETLA",
        "EPSILON",
        "ZETA",
        "ETA",
        "THETA",
        "IOTA",
        "KAPPA",
        "LAMBDA",
        "MU",
    )

    class ServerTestTtype(telnetlib3.TelnetServer):
        def on_ttype(self, ttype):
            super().on_ttype(ttype)
            if ttype == given_ttypes[-1]:
                _waiter.set_result(self)

    async with create_server(
        protocol_factory=ServerTestTtype, host=bind_host, port=unused_tcp_port
    ):
        async with asyncio_connection(bind_host, unused_tcp_port) as (reader, writer):
            writer.write(IAC + WILL + TTYPE)
            for send_ttype in given_ttypes:
                writer.write(IAC + SB + TTYPE + IS + send_ttype.encode("ascii") + IAC + SE)

            srv_instance = await asyncio.wait_for(_waiter, 0.5)
            for idx in range(telnetlib3.TelnetServer.TTYPE_LOOPMAX):
                key = f"ttype{idx + 1}"
                expected = given_ttypes[idx]
                assert srv_instance.get_extra_info(key) == expected, (idx, key)

            key = f"ttype{telnetlib3.TelnetServer.TTYPE_LOOPMAX + 1}"
            expected = given_ttypes[-1]
            assert srv_instance.get_extra_info(key) == expected, (idx, key)
            assert srv_instance.get_extra_info("TERM") == given_ttypes[-1]


async def test_telnet_server_on_ttype_empty(bind_host, unused_tcp_port):
    """Test Server's callback method on_ttype(): empty value is ignored."""
    _waiter = asyncio.Future()
    given_ttypes = ("ALPHA", "", "BETA")

    class ServerTestTtype(telnetlib3.TelnetServer):
        def on_ttype(self, ttype):
            super().on_ttype(ttype)
            if ttype == given_ttypes[-1]:
                _waiter.set_result(self)

    async with create_server(
        protocol_factory=ServerTestTtype, host=bind_host, port=unused_tcp_port
    ):
        async with asyncio_connection(bind_host, unused_tcp_port) as (reader, writer):
            writer.write(IAC + WILL + TTYPE)
            for send_ttype in given_ttypes:
                writer.write(IAC + SB + TTYPE + IS + send_ttype.encode("ascii") + IAC + SE)

            srv_instance = await asyncio.wait_for(_waiter, 0.5)
            assert srv_instance.get_extra_info("ttype1") == "ALPHA"
            assert srv_instance.get_extra_info("ttype2") == "BETA"
            assert srv_instance.get_extra_info("TERM") == "BETA"


async def test_telnet_server_on_ttype_looped(bind_host, unused_tcp_port):
    """Test Server's callback method on_ttype() when value looped."""
    _waiter = asyncio.Future()
    given_ttypes = ("ALPHA", "BETA", "GAMMA", "ALPHA")

    class ServerTestTtype(telnetlib3.TelnetServer):
        count = 1

        def on_ttype(self, ttype):
            super().on_ttype(ttype)
            if self.count == len(given_ttypes):
                _waiter.set_result(self)
            self.count += 1

    async with create_server(
        protocol_factory=ServerTestTtype, host=bind_host, port=unused_tcp_port
    ):
        async with asyncio_connection(bind_host, unused_tcp_port) as (reader, writer):
            writer.write(IAC + WILL + TTYPE)
            for send_ttype in given_ttypes:
                writer.write(IAC + SB + TTYPE + IS + send_ttype.encode("ascii") + IAC + SE)

            srv_instance = await asyncio.wait_for(_waiter, 0.5)
            assert srv_instance.get_extra_info("ttype1") == "ALPHA"
            assert srv_instance.get_extra_info("ttype2") == "BETA"
            assert srv_instance.get_extra_info("ttype3") == "GAMMA"
            assert srv_instance.get_extra_info("ttype4") == "ALPHA"
            assert srv_instance.get_extra_info("TERM") == "ALPHA"


async def test_telnet_server_on_ttype_repeated(bind_host, unused_tcp_port):
    """Test Server's callback method on_ttype() when value repeats."""
    _waiter = asyncio.Future()
    given_ttypes = ("ALPHA", "BETA", "GAMMA", "GAMMA")

    class ServerTestTtype(telnetlib3.TelnetServer):
        count = 1

        def on_ttype(self, ttype):
            super().on_ttype(ttype)
            if self.count == len(given_ttypes):
                _waiter.set_result(self)
            self.count += 1

    async with create_server(
        protocol_factory=ServerTestTtype, host=bind_host, port=unused_tcp_port
    ):
        async with asyncio_connection(bind_host, unused_tcp_port) as (reader, writer):
            writer.write(IAC + WILL + TTYPE)
            for send_ttype in given_ttypes:
                writer.write(IAC + SB + TTYPE + IS + send_ttype.encode("ascii") + IAC + SE)

            srv_instance = await asyncio.wait_for(_waiter, 0.5)
            assert srv_instance.get_extra_info("ttype1") == "ALPHA"
            assert srv_instance.get_extra_info("ttype2") == "BETA"
            assert srv_instance.get_extra_info("ttype3") == "GAMMA"
            assert srv_instance.get_extra_info("ttype4") == "GAMMA"
            assert srv_instance.get_extra_info("TERM") == "GAMMA"


async def test_telnet_server_on_ttype_mud(bind_host, unused_tcp_port):
    """Test Server's callback method on_ttype() for MUD clients (MTTS)."""
    _waiter = asyncio.Future()
    given_ttypes = ("ALPHA", "BETA", "MTTS 137")

    class ServerTestTtype(telnetlib3.TelnetServer):
        count = 1

        def on_ttype(self, ttype):
            super().on_ttype(ttype)
            if self.count == len(given_ttypes):
                _waiter.set_result(self)
            self.count += 1

    async with create_server(
        protocol_factory=ServerTestTtype, host=bind_host, port=unused_tcp_port
    ):
        async with asyncio_connection(bind_host, unused_tcp_port) as (reader, writer):
            writer.write(IAC + WILL + TTYPE)
            for send_ttype in given_ttypes:
                writer.write(IAC + SB + TTYPE + IS + send_ttype.encode("ascii") + IAC + SE)

            srv_instance = await asyncio.wait_for(_waiter, 0.5)
            assert srv_instance.get_extra_info("ttype1") == "ALPHA"
            assert srv_instance.get_extra_info("ttype2") == "BETA"
            assert srv_instance.get_extra_info("ttype3") == "MTTS 137"
            assert srv_instance.get_extra_info("TERM") == "BETA"
