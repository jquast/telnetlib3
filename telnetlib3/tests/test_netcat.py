"""Functionally tests telnetlib3 as a server using nc(1)."""
# std imports
import subprocess
import platform
import asyncio
import math
import time

# local imports
from .accessories import (
    TestTelnetServer,
    unused_tcp_port,
    event_loop,
    bind_host,
    log,
)

# 3rd party imports
import pytest
import pexpect


def get_netcat():
    """ Find and return best-matching IPv6 capable, OpenBSD-derived nc(1)."""
    netcat_paths=('nc',
                  'netcat',
                  '/usr/bin/nc',
                  '/usr/local/bin/nc',
                  # debian,
                  '/bin/nc.openbsd')
    for nc_name in netcat_paths:
        prog = pexpect.which(nc_name)
        if prog is None:
            continue

        stdout, stderr = subprocess.Popen(
            [prog, '-h'],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        ).communicate()

        # only openbsd netcat supports IPv6.
        # So that's the only one we'll use!
        if b'-46' in (stdout + stderr):
            return prog
    return None


@pytest.mark.skipif(get_netcat() is None,
                    reason="Requires IPv6 capable (OpenBSD-borne) nc(1)")
@pytest.mark.asyncio
def test_netcat_z(event_loop, bind_host, unused_tcp_port, log):
    """
    Using nc(1) -z, ensure server behaves well against "port scanning".
    """

    connection_closed = asyncio.Future()

    server = yield from event_loop.create_server(
        protocol_factory=lambda: TestTelnetServer(
            waiter_closed=connection_closed,
            log=log),
        host=bind_host, port=unused_tcp_port)

    log.info('Listening on {0}'.format(server.sockets[0].getsockname()))

    netcat = yield from asyncio.create_subprocess_exec(
        get_netcat(), '-z', bind_host, '{0}'.format(unused_tcp_port),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )

    done, pending = yield from asyncio.wait(
        [connection_closed, netcat.wait()],
        loop=event_loop, timeout=1)

    assert not pending, (netcat, connection_closed)


@pytest.mark.skipif(get_netcat() is None,
                    reason="Requires IPv6 capable (OpenBSD-borne) nc(1)")
@pytest.mark.skipif(platform.system() == "FreeBSD",
                    reason="FreeBSD nc(1) does not exit on socket close.")
@pytest.mark.asyncio
def test_netcat_t_timeout_1s(event_loop, bind_host, unused_tcp_port, log):
    """
    Using nc(1) -t, instruct server to disconnect us by 1s. idle.
    """
    connection_closed = asyncio.Future()

    server = yield from event_loop.create_server(
        protocol_factory=lambda: TestTelnetServer(
            waiter_closed=connection_closed,
            log=log),
        host=bind_host, port=unused_tcp_port)

    log.info('Listening on {0}'.format(server.sockets[0].getsockname()))

    netcat = yield from asyncio.create_subprocess_exec(
        get_netcat(), '-t', bind_host, '{0}'.format(unused_tcp_port),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )

    # expect a prompt or some such.
    yield from netcat.stdout.readline()

    # begin timer,
    log.debug('begin timer')

    stime = time.time()

    netcat.stdin.write(b'set TIMEOUT=1\r')

    # expect disconnection
    done, pending = yield from asyncio.wait(
        [connection_closed, netcat.wait()],
        loop=event_loop, timeout=2.1)

    duration = time.time() - stime

    log.debug('done: {0}'.format(done))
    log.debug('pending: {0}'.format(pending))
    log.debug('duration: {0}'.format(duration))

    assert 0 == len(pending)

    # we were disconnected after idling for >=~1.0000 second.
    assert math.floor(duration) == 1
