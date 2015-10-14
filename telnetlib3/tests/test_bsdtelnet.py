"""Functionally tests telnetlib3 as a server, using telnet(1)"""
# std imports
import functools
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
    _log = logging.getLogger(__name__)
    _log.setLevel(logging.DEBUG)
    return _log


@pytest.fixture(scope="module", params=["0.0.0.0", "::1"])
def bind_host(request):
    return request.param


@pytest.mark.asyncio
def test_bsdtelnet(caplog, event_loop, bind_host, unused_tcp_port, log):

    event_loop.set_debug(True)
    caplog.setLevel(logging.DEBUG)

    waiter_server_connected = asyncio.Future()
    waiter_server_telopt = asyncio.Future()
    waiter_server_encoding = asyncio.Future()
    waiter_server_closed = asyncio.Future()

    server = yield from event_loop.create_server(
        protocol_factory=lambda: telnetlib3.TelnetServer(
            waiter_connected=waiter_server_connected,
            waiter_closed=waiter_server_closed,
            waiter_telopt=waiter_server_telopt,
            waiter_encoding=waiter_server_encoding,
            log=log),
        host=bind_host, port=unused_tcp_port)

    log.info('Listening on {host} {port}'.format(
             host=server.sockets[0].getsockname(),
             port=unused_tcp_port))

    child = pexpect.spawn(
        command='telnet', timeout=1,
        encoding='utf8', echo=False)

    # patch pexpect to debug-copy i/o to separate streams
    child.delaybeforesend = 0.0
    child.logfile_read = io.StringIO()
    child.logfile_send = io.StringIO()

    telnet_options = ('options',)
    # telnet_options = ('options', 'debug', 'netdata', 'prettydump',)
    # and to enable all telnet client options
    for opt in telnet_options:
        yield from child.expect("telnet> ", async=True)
        child.sendline(u"set {0}".format(opt))

    # before connecting,
    yield from child.expect(u"telnet> ", async=True)
    child.sendline(u"open {0} {1}".format(bind_host, unused_tcp_port))
    yield from child.expect_exact("Escape character is '^]'.", async=True)

    # now await all completed negotiations
    child_expect = functools.partial(child.expect_exact,
                                     timeout=None,
                                     async=True)

    done, pending = yield from asyncio.wait(
        [child_expect('SENT WILL BINARY'), waiter_server_encoding],
        loop=event_loop, timeout=2,
        return_when=asyncio.ALL_COMPLETED)

    # inquire server of our known status and exit.
    child.sendline(u"quit")

    done, pending = yield from asyncio.wait(
        [child_expect('Connection closed by foreign host.'),
         waiter_server_closed],
        loop=event_loop, timeout=1,
        return_when=asyncio.ALL_COMPLETED)

    assert not pending
