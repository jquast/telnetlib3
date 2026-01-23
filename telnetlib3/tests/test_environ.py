"""Test NEW_ENVIRON, rfc-1572_."""

# std imports
import asyncio

# 3rd party
import pytest

# local
# local imports
import telnetlib3
import telnetlib3.stream_writer
from telnetlib3.tests.accessories import bind_host, unused_tcp_port


async def test_telnet_server_on_environ(bind_host, unused_tcp_port):
    """Test Server's callback method on_environ()."""
    # local
    from telnetlib3.telopt import IS, SB, SE, IAC, WILL, NEW_ENVIRON
    from telnetlib3.tests.accessories import create_server, asyncio_connection

    _waiter = asyncio.Future()

    class ServerTestEnviron(telnetlib3.TelnetServer):
        def on_environ(self, mapping):
            super().on_environ(mapping)
            _waiter.set_result(self)

    async with create_server(
        protocol_factory=ServerTestEnviron, host=bind_host, port=unused_tcp_port
    ):
        async with asyncio_connection(bind_host, unused_tcp_port) as (reader, writer):
            writer.write(IAC + WILL + NEW_ENVIRON)
            writer.write(
                IAC
                + SB
                + NEW_ENVIRON
                + IS
                + telnetlib3.stream_writer._encode_env_buf(
                    {
                        "aLpHa": "oMeGa",
                        "beta": "b",
                        "gamma": "".join(chr(n) for n in range(0, 128)),
                    }
                )
                + IAC
                + SE
            )

            srv_instance = await asyncio.wait_for(_waiter, 0.5)
            assert srv_instance.get_extra_info("ALPHA") == "oMeGa"
            assert srv_instance.get_extra_info("BETA") == "b"
            assert srv_instance.get_extra_info("GAMMA") == ("".join(chr(n) for n in range(0, 128)))


async def test_telnet_client_send_environ(bind_host, unused_tcp_port):
    """Test Client's callback method send_environ() for specific requests."""
    # local
    from telnetlib3.tests.accessories import create_server, open_connection

    _waiter = asyncio.Future()
    given_cols = 19
    given_rows = 84
    given_encoding = "cp437"
    given_term = "vt220"

    class ServerTestEnviron(telnetlib3.TelnetServer):
        def on_environ(self, mapping):
            super().on_environ(mapping)
            _waiter.set_result(mapping)

    async with create_server(
        protocol_factory=ServerTestEnviron, host=bind_host, port=unused_tcp_port
    ):
        async with open_connection(
            host=bind_host,
            port=unused_tcp_port,
            cols=given_cols,
            rows=given_rows,
            encoding=given_encoding,
            term=given_term,
            connect_minwait=0.05,
        ) as (reader, writer):
            mapping = await asyncio.wait_for(_waiter, 0.5)
            assert mapping == {
                "COLUMNS": str(given_cols),
                "LANG": "en_US." + given_encoding,
                "LINES": str(given_rows),
                "TERM": "vt220",
            }


async def test_telnet_client_send_var_uservar_environ(bind_host, unused_tcp_port):
    """Test Client's callback method send_environ() for VAR/USERVAR request."""
    # local
    from telnetlib3.tests.accessories import create_server, open_connection

    _waiter = asyncio.Future()
    given_cols = 19
    given_rows = 84
    given_encoding = "cp437"
    given_term = "vt220"

    class ServerTestEnviron(telnetlib3.TelnetServer):
        def on_environ(self, mapping):
            super().on_environ(mapping)
            _waiter.set_result(mapping)

        def on_request_environ(self):
            # local
            from telnetlib3.telopt import VAR, USERVAR

            return [VAR, USERVAR]

    async with create_server(
        protocol_factory=ServerTestEnviron,
        host=bind_host,
        port=unused_tcp_port,
    ):
        async with open_connection(
            host=bind_host,
            port=unused_tcp_port,
            cols=given_cols,
            rows=given_rows,
            encoding=given_encoding,
            term=given_term,
            connect_minwait=0.05,
            connect_maxwait=0.05,
        ) as (reader, writer):
            mapping = await asyncio.wait_for(_waiter, 0.5)
            assert mapping == {}

            mapping == {
                "COLUMNS": str(given_cols),
                "LANG": "en_US." + given_encoding,
                "LINES": str(given_rows),
                "TERM": "vt220",
            }
            for key, val in mapping.items():
                assert writer.get_extra_info(key) == val


async def test_telnet_server_reject_environ(bind_host, unused_tcp_port):
    """Test Client's callback method send_environ() for specific requests."""
    # local
    from telnetlib3.telopt import SB, NEW_ENVIRON
    from telnetlib3.tests.accessories import create_server, open_connection

    given_cols = 19
    given_rows = 84
    given_encoding = "cp437"
    given_term = "vt220"

    class ServerTestEnviron(telnetlib3.TelnetServer):
        def on_request_environ(self):
            return None

    async with create_server(
        protocol_factory=ServerTestEnviron,
        host=bind_host,
        port=unused_tcp_port,
    ):
        async with open_connection(
            host=bind_host,
            port=unused_tcp_port,
            cols=given_cols,
            rows=given_rows,
            encoding=given_encoding,
            term=given_term,
            connect_minwait=0.05,
            connect_maxwait=0.05,
        ) as (reader, writer):
            _failed = {key: val for key, val in writer.pending_option.items() if val}
            assert _failed == {SB + NEW_ENVIRON: True}
