"""Test NEW_ENVIRON, rfc-1572_."""

# std imports
import asyncio

# 3rd party
import pytest

# local
import telnetlib3
import telnetlib3.stream_writer
from telnetlib3.telopt import DO, IS, SB, SE, IAC, VAR, WILL, TTYPE, USERVAR, NEW_ENVIRON
from telnetlib3.tests.accessories import (  # pylint: disable=unused-import; pylint: disable=unused-import,
    bind_host,
    create_server,
    open_connection,
    unused_tcp_port,
    asyncio_connection,
)


async def test_telnet_server_on_environ(bind_host, unused_tcp_port):
    """Test Server's callback method on_environ()."""
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
            # Check expected values are present
            assert mapping["COLUMNS"] == str(given_cols)
            assert mapping["LANG"] == "en_US." + given_encoding
            assert mapping["LINES"] == str(given_rows)
            assert mapping["TERM"] == "vt220"
            # Additional env vars may be present (USER, HOME, SHELL, COLORTERM)
            # but their values depend on the test environment


async def test_telnet_client_send_var_uservar_environ(bind_host, unused_tcp_port):
    """Test Client's callback method send_environ() for VAR/USERVAR request."""
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


async def test_telnet_server_reject_environ(bind_host, unused_tcp_port):
    """Test Client's callback method send_environ() for specific requests."""
    given_cols = 19
    given_rows = 84
    given_term = "vt220"

    class ServerTestEnviron(telnetlib3.TelnetServer):
        def on_request_environ(self):
            return None

    async with create_server(
        protocol_factory=ServerTestEnviron,
        host=bind_host,
        port=unused_tcp_port,
        encoding=False,
        connect_maxwait=0.15,
    ):
        async with open_connection(
            host=bind_host,
            port=unused_tcp_port,
            cols=given_cols,
            rows=given_rows,
            encoding=False,
            term=given_term,
            connect_minwait=0.1,
            connect_maxwait=0.15,
        ) as (reader, writer):
            _failed = {key: val for key, val in writer.pending_option.items() if val}
            assert _failed == {SB + NEW_ENVIRON: True}


class _MockTransport:
    def get_extra_info(self, key, default=None):
        return default

    def write(self, data):
        pass

    def is_closing(self):
        return False


def _make_server():
    server = telnetlib3.TelnetServer()
    server.connection_made(_MockTransport())
    return server


@pytest.mark.parametrize(
    "ttype1,ttype2,expect_skip",
    [
        ("ANSI", "VT100", True),
        ("ANSI", "ANSI", False),
        ("ansi", "vt100", False),
        ("xterm", "xterm", False),
        ("xterm", "xterm-256color", False),
    ],
)
async def test_negotiate_environ_ms_telnet(ttype1, ttype2, expect_skip):
    """NEW_ENVIRON is skipped for Microsoft telnet (ANSI + VT100)."""
    server = _make_server()
    server._extra["ttype1"] = ttype1
    server._extra["ttype2"] = ttype2
    server._negotiate_environ()
    if expect_skip:
        assert not server.writer.pending_option.get(DO + NEW_ENVIRON)
    else:
        assert server.writer.pending_option.get(DO + NEW_ENVIRON)


async def test_check_negotiation_ttype_refused_triggers_environ():
    """check_negotiation sends DO NEW_ENVIRON when TTYPE is refused."""
    server = _make_server()
    server._advanced = True
    server.writer.remote_option[TTYPE] = False
    server.check_negotiation(final=False)
    assert server._environ_requested
    assert server.writer.pending_option.get(DO + NEW_ENVIRON)


async def test_check_negotiation_final_triggers_environ():
    """check_negotiation sends DO NEW_ENVIRON on final timeout."""
    server = _make_server()
    server._advanced = True
    server.check_negotiation(final=True)
    assert server._environ_requested
    assert server.writer.pending_option.get(DO + NEW_ENVIRON)


async def test_check_negotiation_no_advanced_skips_environ():
    """check_negotiation does not send DO NEW_ENVIRON without advanced."""
    server = _make_server()
    server.writer.remote_option[TTYPE] = False
    server.check_negotiation(final=True)
    assert not server._environ_requested
    assert not server.writer.pending_option.get(DO + NEW_ENVIRON)


async def test_on_ttype_non_ansi_triggers_environ():
    """on_ttype sends DO NEW_ENVIRON immediately for non-ANSI ttype1."""
    server = _make_server()
    server.on_ttype("xterm")
    assert server._environ_requested
    assert server.writer.pending_option.get(DO + NEW_ENVIRON)


async def test_on_ttype_ansi_defers_environ():
    """on_ttype defers DO NEW_ENVIRON when ttype1 is ANSI."""
    server = _make_server()
    server.on_ttype("ANSI")
    assert not server._environ_requested
    assert not server.writer.pending_option.get(DO + NEW_ENVIRON)
