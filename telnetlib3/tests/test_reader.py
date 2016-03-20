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

@pytest.mark.asyncio
def test_telnet_reader_using_readline(
        event_loop, bind_host, unused_tcp_port):
    """Ensure TelnetReader.readline() interpretation of telnet's newlines."""
    # given
    _waiter = asyncio.Future()
    given_expected = {
        'alpha\r\x00': 'alpha\r',
        'bravo\r\n': 'bravo\r\n',
        'charlie\n': 'charlie\n',
        '---\r---\r\n': '---\r---\r\n',
        '\r\x00': '\r',
        '\n': '\n',
        '\r\n': '\r\n',
        'xxxxxxxxxxx': 'xxxxxxxxxxx',
    }

    def shell(reader, writer):
        for item in sorted(given_expected):
            writer.write(item)
        writer.close()

    yield from telnetlib3.create_server(
        host=bind_host, port=unused_tcp_port, loop=event_loop,
        connect_maxwait=0.05, shell=shell)

    client_reader, client_writer = yield from telnetlib3.open_connection(
        host=bind_host, port=unused_tcp_port, loop=event_loop,
        connect_minwait=0.05)

    for given, expected in sorted(given_expected.items()):
        result = yield from asyncio.wait_for(client_reader.readline(), 0.5)
        assert result == expected
    eof = yield from asyncio.wait_for(client_reader.read(), 0.5)
    assert eof == ''
