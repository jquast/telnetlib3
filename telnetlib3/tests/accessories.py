"""Test accessories for telnetlib3 project."""
# std imports
import logging

# 3rd-party
import pytest
from pytest_asyncio.plugin import unused_tcp_port, event_loop 

@pytest.fixture(scope="module", params=['127.0.0.1'])
def bind_host(request):
    """ Localhost bind address. """
    return request.param

__all__ = ('bind_host', 'unused_tcp_port', 'event_loop',)
