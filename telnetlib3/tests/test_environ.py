"""Test NEW_ENVIRON, rfc-1572_."""
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
async def test_telnet_server_on_environ(
        event_loop, bind_host, unused_tcp_port):
    """Test Server's callback method on_environ()."""
    # given
    from telnetlib3.telopt import (
        IAC, WILL, SB, SE, IS, NEW_ENVIRON
    )
    _waiter = asyncio.Future()

    class ServerTestEnviron(telnetlib3.TelnetServer):
        def on_environ(self, mapping):
            super().on_environ(mapping)
            _waiter.set_result(self)

    await telnetlib3.create_server(
        protocol_factory=ServerTestEnviron,
        host=bind_host, port=unused_tcp_port,
        loop=event_loop)

    reader, writer = await asyncio.open_connection(
        host=bind_host, port=unused_tcp_port, loop=event_loop)

    # exercise,
    writer.write(IAC + WILL + NEW_ENVIRON)
    writer.write(IAC + SB + NEW_ENVIRON + IS +
                 telnetlib3.stream_writer._encode_env_buf({
                     # note how the default implementation .upper() cases
                     # all environment keys.
                     'aLpHa': 'oMeGa',
                     'beta': 'b',
                     'gamma': u''.join(chr(n) for n in range(0, 128)),
                 }) + IAC + SE)

    srv_instance = await asyncio.wait_for(_waiter, 0.5)
    assert srv_instance.get_extra_info('ALPHA') == 'oMeGa'
    assert srv_instance.get_extra_info('BETA') == 'b'
    assert srv_instance.get_extra_info('GAMMA') == (
        u''.join(chr(n) for n in range(0, 128)))


@pytest.mark.asyncio
async def test_telnet_client_send_environ(event_loop, bind_host,
                                    unused_tcp_port):
    """Test Client's callback method send_environ() for specific requests."""
    # given
    _waiter = asyncio.Future()
    given_cols = 19
    given_rows = 84
    given_encoding = 'cp437'
    given_term = 'vt220'

    class ServerTestEnviron(telnetlib3.TelnetServer):
        def on_environ(self, mapping):
            super().on_environ(mapping)
            _waiter.set_result(mapping)

    await telnetlib3.create_server(
        protocol_factory=ServerTestEnviron,
        host=bind_host, port=unused_tcp_port,
        loop=event_loop)

    reader, writer = await telnetlib3.open_connection(
        host=bind_host, port=unused_tcp_port, loop=event_loop,
        cols=given_cols, rows=given_rows, encoding=given_encoding,
        term=given_term, connect_minwait=0.05)

    mapping = await asyncio.wait_for(_waiter, 0.5)
    assert mapping == {
        'COLUMNS': str(given_cols),
        'LANG': 'en_US.' + given_encoding,
        'LINES': str(given_rows),
        'TERM': 'vt220'
    }


@pytest.mark.asyncio
async def test_telnet_client_send_var_uservar_environ(event_loop, bind_host,
                                                unused_tcp_port):
    """Test Client's callback method send_environ() for VAR/USERVAR request."""
    # given
    _waiter = asyncio.Future()
    given_cols = 19
    given_rows = 84
    given_encoding = 'cp437'
    given_term = 'vt220'

    class ServerTestEnviron(telnetlib3.TelnetServer):
        def on_environ(self, mapping):
            super().on_environ(mapping)
            _waiter.set_result(mapping)

        def on_request_environ(self):
            from telnetlib3.telopt import VAR, USERVAR
            return [VAR, USERVAR]

    await telnetlib3.create_server(
        protocol_factory=ServerTestEnviron,
        host=bind_host, port=unused_tcp_port,
        loop=event_loop)

    reader, writer = await telnetlib3.open_connection(
        host=bind_host, port=unused_tcp_port, loop=event_loop,
        cols=given_cols, rows=given_rows, encoding=given_encoding,
        term=given_term, connect_minwait=0.05, connect_maxwait=0.05)

    mapping = await asyncio.wait_for(_waiter, 0.5)
    # although nothing was demanded by server,
    assert mapping == {}

    # the client still volunteered these basic variables,
    mapping == {
        'COLUMNS': str(given_cols),
        'LANG': 'en_US.' + given_encoding,
        'LINES': str(given_rows),
        'TERM': 'vt220'
    }
    for key, val in mapping.items():
        assert writer.get_extra_info(key) == val


@pytest.mark.asyncio
async def test_telnet_server_reject_environ(event_loop, bind_host,
                                      unused_tcp_port):
    """Test Client's callback method send_environ() for specific requests."""
    from telnetlib3.telopt import SB, NEW_ENVIRON
    # given
    given_cols = 19
    given_rows = 84
    given_encoding = 'cp437'
    given_term = 'vt220'

    class ServerTestEnviron(telnetlib3.TelnetServer):
        def on_request_environ(self):
            return None

    await telnetlib3.create_server(
        protocol_factory=ServerTestEnviron,
        host=bind_host, port=unused_tcp_port,
        loop=event_loop)

    reader, writer = await telnetlib3.open_connection(
        host=bind_host, port=unused_tcp_port, loop=event_loop,
        cols=given_cols, rows=given_rows, encoding=given_encoding,
        term=given_term, connect_minwait=0.05, connect_maxwait=0.05)

    # this causes the client to expect the server to have demanded environment
    # values, since it did, of course demand DO NEW_ENVIRON! However, our API
    # choice here has chosen not to -- the client then indicates this as a
    # failed sub-negotiation (SB + NEW_ENVIRON).
    _failed = {key: val for key, val in writer.pending_option.items() if val}
    assert _failed == {SB + NEW_ENVIRON: True}
