# std imports
import asyncio

# 3rd party
import pytest

# local
import telnetlib3
from telnetlib3.tests.accessories import (
    unused_tcp_port,
    event_loop,
    bind_host
)


def test_reader_instantiation_safety():
    """On instantiation, one of server or client must be specified."""
    telnetlib3.TelnetReader(protocol=None, client=True)
    with pytest.raises(TypeError):
        # must define at least server=True or client=True
        telnetlib3.TelnetReader(protocol=None)
    with pytest.raises(TypeError):
        # but cannot define both!
        telnetlib3.TelnetReader(protocol=None,
                                server=True, client=True)

def test_repr():
    """Test reader.__repr__ for client and server viewpoint."""
    class mock_protocol(object):
        default_encoding = 'def-ENC'
        def encoding(self, **kwds):
            return self.default_encoding

    srv = telnetlib3.TelnetReader(protocol=mock_protocol(), server=True)
    clt = telnetlib3.TelnetReader(protocol=mock_protocol(), client=True)
    assert repr(srv) == "<TelnetReader encoding='def-ENC'>"
    assert repr(clt) == "<TelnetReader encoding='def-ENC'>"
