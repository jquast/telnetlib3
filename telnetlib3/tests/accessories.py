"""Test accessories for telnetlib3 project."""
# std imports
import logging

# local
import telnetlib3

# 3rd-party
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


@pytest.fixture(scope="module", params=["127.0.0.1", "::1"])
def bind_host(request):
    return request.param


class TestTelnetServer(telnetlib3.Server):
    pass
#    CONNECT_MINWAIT = 0.10
#    CONNECT_MAXWAIT = 0.50
#    CONNECT_DEFERRED = 0.01
#    TTYPE_LOOPMAX = 2
#    default_env = {
#        'PS1': 'test-telsh %# ',
#    }


class TestTelnetClient(telnetlib3.Client):
    CONNECT_MINWAIT = 0.20
    CONNECT_MAXWAIT = 0.75
    CONNECT_DEFERRED = 0.01
    default_env = {
        'COLUMNS': '80', 'LINES': '24',
        'USER': 'test-client',
        'TERM': 'test-terminal',
        'CHARSET': 'ascii',
    }
