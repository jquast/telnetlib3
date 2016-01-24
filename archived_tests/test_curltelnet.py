"""Functionally tests telnetlib3 as a server using curl(1)."""
# std imports
import subprocess
import asyncio

# local imports
from telnetlib3.tests.accessories import (
    server_factory,
    unused_tcp_port,
    event_loop,
    bind_host,
    log
)

# 3rd party imports
import pytest
import pexpect


@pytest.mark.skipif(pexpect.which('curl') is None,
                    reason="Requires curl(1)")
@pytest.mark.asyncio
def test_curltelnet(server_factory, event_loop, bind_host, unused_tcp_port, log):
    """Simple curl(1) as Telnet client (simple capabilities)."""

    event_loop.set_debug(True)
    waiter_connected = asyncio.Future()

    server = yield from event_loop.create_server(
        protocol_factory=lambda: server_factory(
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

    wait_for = [waiter_connected,
                curl.communicate(input=b'quit\r')]

    done, pending = yield from asyncio.wait(wait_for,
                                            loop=event_loop,
                                            timeout=1)
    assert not pending, wait_for

    server.close()
    yield from server.wait_closed()
