#!/usr/bin/env python3
"""Functionally tests telnetlib3 as a server, driving bsd telnet by pexpect!"""
# std imports
import logging
import asyncio
import io

# local imports
import telnetlib3

# 3rd party imports
import pexpect
import pytest
from pytest_asyncio.plugin import (
    unused_tcp_port,
    event_loop,
)


@pytest.fixture
def log():
    log = logging.getLogger(__name__)
    fmt = '%(levelname)s %(filename)s:%(lineno)d %(message)s'
    logging.basicConfig(format=fmt)
    log.setLevel(logging.DEBUG)
    return log


@pytest.fixture(scope="module", params=["0.0.0.0", "::1"])
def bind_host(request):
    return request.param


@pytest.mark.asyncio
def test_bsdtelnet(event_loop, bind_host, unused_tcp_port, log):
    func = event_loop.create_server(lambda: telnetlib3.TelnetServer(log=log),
                                    bind_host, unused_tcp_port)

    server = event_loop.run_until_complete(func)

    log.info('Listening on %s', server.sockets[0].getsockname())

    pexpect_log = io.StringIO()

    child = pexpect.spawnu(command='telnet', timeout=5,
                           logfile=pexpect_log)

    child.sendline("status")
    yield from child.expect_exact("No connection.", async=True)
    yield from child.expect_exact("Escape character is '^]'.", async=True)
    yield from child.expect_exact('telnet>', async=True)
    child.sendline("open {0} {1}".format(bind_host, unused_tcp_port))

#    val = yield from server.connected
#    assert val
#    assert child.read()
