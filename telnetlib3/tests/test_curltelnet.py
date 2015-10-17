"""Functionally tests telnetlib3 as a server using curl(1)."""
# std imports
import subprocess
import asyncio
import locale
import codecs

# local imports
from .accessories import (
    TestTelnetServer,
    unused_tcp_port,
    event_loop,
    bind_host,
    log
)

# local
import telnetlib3

# 3rd party imports
import pytest
import pexpect


@pytest.mark.skipif(pexpect.which('curl') is None,
                    reason="Requires curl(1)")
@pytest.mark.asyncio
def test_curltelnet(event_loop, bind_host, unused_tcp_port, log):

    waiter_closed = asyncio.Future()
    waiter_connected = asyncio.Future()

    server = yield from event_loop.create_server(
        protocol_factory=lambda: TestTelnetServer(
            waiter_closed=waiter_closed,
            waiter_connected=waiter_connected,
            log=log),
        host=bind_host, port=unused_tcp_port)

    log.info('Listening on {0}'.format(server.sockets[0].getsockname()))

    curl = yield from asyncio.create_subprocess_exec(
        'curl', '--verbose', '--progress-bar',
        'telnet://{0}:{1}'.format(bind_host, unused_tcp_port),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )

    done, pending = yield from asyncio.wait(
        [waiter_connected, curl.communicate(input=b'quit\r'), waiter_closed],
        loop=event_loop, timeout=1)

    assert not pending, (waiter_connected, curl, waiter_closed)
