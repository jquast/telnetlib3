"""Test accessories for telnetlib3 project."""
# std imports
import logging

# 3rd-party
import pytest
from pytest_asyncio.plugin import unused_tcp_port, event_loop


@pytest.fixture
def log():
    _log = logging.getLogger(__name__)
    _log.setLevel(logging.DEBUG)
    return _log


@pytest.fixture(scope="module", params=["127.0.0.1", "::1"])
def bind_host(request):
    return request.param
