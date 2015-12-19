"""Test NEW_ENVIRON, rfc-1572_."""
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
def test_telnet_server_on_environ(
        event_loop, bind_host, unused_tcp_port, log):
    """Test Server's callback method on_environ()."""
    # given
    from telnetlib3.telopt import (
        IAC, WILL, SB, SE, IS, NEW_ENVIRON
    )
    _waiter = asyncio.Future()
    event_loop.set_debug(True)

    class ServerTestEnviron(telnetlib3.TelnetServer):
        def on_environ(self, mapping):
            super().on_environ(mapping)
            _waiter.set_result(self)

    yield from telnetlib3.create_server(
        protocol_factory=ServerTestEnviron,
        host=bind_host, port=unused_tcp_port,
        loop=event_loop, log=log)

    reader, writer = yield from asyncio.open_connection(
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

    srv_instance = yield from asyncio.wait_for(_waiter, 0.5)
    assert srv_instance.get_extra_info('ALPHA') == 'oMeGa'
    assert srv_instance.get_extra_info('BETA') == 'b'
    assert srv_instance.get_extra_info('GAMMA') == (
        u''.join(chr(n) for n in range(0, 128)))
