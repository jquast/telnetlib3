"""Test TTYPE, rfc-930_."""
# std imports
import asyncio

# local imports
import telnetlib3
import telnetlib3.stream_writer
from telnetlib3.tests.accessories import (
    unused_tcp_port,
    event_loop,
    bind_host
)

# 3rd party
import pytest


@pytest.mark.asyncio
async def test_telnet_server_on_ttype(event_loop, bind_host, unused_tcp_port):
    """Test Server's callback method on_ttype()."""
    # given
    from telnetlib3.telopt import IAC, WILL, SB, SE, IS, TTYPE
    _waiter = asyncio.Future()

    class ServerTestTtype(telnetlib3.TelnetServer):
        def on_ttype(self, ttype):
            super().on_ttype(ttype)
            _waiter.set_result(self)

    await telnetlib3.create_server(
        protocol_factory=ServerTestTtype,
        host=bind_host, port=unused_tcp_port,
        loop=event_loop)

    reader, writer = await asyncio.open_connection(
        host=bind_host, port=unused_tcp_port, loop=event_loop)

    # exercise,
    writer.write(IAC + WILL + TTYPE)
    writer.write(IAC + SB + TTYPE + IS + b'ALPHA' + IAC + SE)
    writer.write(IAC + SB + TTYPE + IS + b'ALPHA' + IAC + SE)

    # verify,
    srv_instance = await asyncio.wait_for(_waiter, 0.5)
    assert 'ALPHA' == srv_instance.get_extra_info('ttype1')
    assert 'ALPHA' == srv_instance.get_extra_info('ttype2')
    assert 'ALPHA' == srv_instance.get_extra_info('TERM')


@pytest.mark.asyncio
async def test_telnet_server_on_ttype_beyond_max(
        event_loop, bind_host, unused_tcp_port):
    """
    Test Server's callback method on_ttype() with long list.

    After TTYPE_LOOPMAX, we stop requesting and tracking further
    terminal types; something of an error (a warning is emitted),
    and assume the use of the first we've seen.  This is to prevent
    an infinite loop with a distant end that is not conforming.
    """
    # given
    from telnetlib3.telopt import IAC, WILL, SB, SE, IS, TTYPE
    _waiter = asyncio.Future()
    given_ttypes = ('ALPHA', 'BETA', 'GAMMA', 'DETLA',
                    'EPSILON', 'ZETA', 'ETA', 'THETA',
                    'IOTA', 'KAPPA', 'LAMBDA', 'MU')

    class ServerTestTtype(telnetlib3.TelnetServer):
        def on_ttype(self, ttype):
            super().on_ttype(ttype)
            if ttype == given_ttypes[-1]:
                _waiter.set_result(self)

    await telnetlib3.create_server(
        protocol_factory=ServerTestTtype,
        host=bind_host, port=unused_tcp_port,
        loop=event_loop)

    reader, writer = await asyncio.open_connection(
        host=bind_host, port=unused_tcp_port, loop=event_loop)

    # exercise,
    writer.write(IAC + WILL + TTYPE)
    for send_ttype in given_ttypes:
        writer.write(IAC + SB + TTYPE + IS +
                     send_ttype.encode('ascii') +
                     IAC + SE)

    # verify,
    srv_instance = await asyncio.wait_for(_waiter, 0.5)
    for idx in range(telnetlib3.TelnetServer.TTYPE_LOOPMAX):
        key = 'ttype{0}'.format(idx + 1)
        expected = given_ttypes[idx]
        assert srv_instance.get_extra_info(key) == expected, (idx, key)

    # ttype{max} gets overwritten continiously, so the last given
    # ttype is the last value.
    key = 'ttype{0}'.format(telnetlib3.TelnetServer.TTYPE_LOOPMAX + 1)
    expected = given_ttypes[-1]
    assert srv_instance.get_extra_info(key) == expected, (idx, key)
    assert srv_instance.get_extra_info('TERM') == given_ttypes[-1]


@pytest.mark.asyncio
async def test_telnet_server_on_ttype_empty(
        event_loop, bind_host, unused_tcp_port):
    """Test Server's callback method on_ttype(): empty value is ignored. """
    # given
    from telnetlib3.telopt import IAC, WILL, SB, SE, IS, TTYPE
    _waiter = asyncio.Future()
    given_ttypes = ('ALPHA', '', 'BETA')

    class ServerTestTtype(telnetlib3.TelnetServer):
        def on_ttype(self, ttype):
            super().on_ttype(ttype)
            if ttype == given_ttypes[-1]:
                _waiter.set_result(self)

    await telnetlib3.create_server(
        protocol_factory=ServerTestTtype,
        host=bind_host, port=unused_tcp_port,
        loop=event_loop)

    reader, writer = await asyncio.open_connection(
        host=bind_host, port=unused_tcp_port, loop=event_loop)

    # exercise,
    writer.write(IAC + WILL + TTYPE)
    for send_ttype in given_ttypes:
        writer.write(IAC + SB + TTYPE + IS +
                     send_ttype.encode('ascii') +
                     IAC + SE)

    # verify,
    srv_instance = await asyncio.wait_for(_waiter, 0.5)
    assert srv_instance.get_extra_info('ttype1') == 'ALPHA'
    assert srv_instance.get_extra_info('ttype2') == 'BETA'
    assert srv_instance.get_extra_info('TERM') == 'BETA'


@pytest.mark.asyncio
async def test_telnet_server_on_ttype_looped(
        event_loop, bind_host, unused_tcp_port):
    """Test Server's callback method on_ttype() when value looped. """
    # given
    from telnetlib3.telopt import IAC, WILL, SB, SE, IS, TTYPE
    _waiter = asyncio.Future()
    given_ttypes = ('ALPHA', 'BETA', 'GAMMA', 'ALPHA')

    class ServerTestTtype(telnetlib3.TelnetServer):
        count = 1

        def on_ttype(self, ttype):
            super().on_ttype(ttype)
            if self.count == len(given_ttypes):
                _waiter.set_result(self)
            self.count += 1

    await telnetlib3.create_server(
        protocol_factory=ServerTestTtype,
        host=bind_host, port=unused_tcp_port,
        loop=event_loop)

    reader, writer = await asyncio.open_connection(
        host=bind_host, port=unused_tcp_port, loop=event_loop)

    # exercise,
    writer.write(IAC + WILL + TTYPE)
    for send_ttype in given_ttypes:
        writer.write(IAC + SB + TTYPE + IS +
                     send_ttype.encode('ascii') +
                     IAC + SE)

    # verify,
    srv_instance = await asyncio.wait_for(_waiter, 0.5)
    assert srv_instance.get_extra_info('ttype1') == 'ALPHA'
    assert srv_instance.get_extra_info('ttype2') == 'BETA'
    assert srv_instance.get_extra_info('ttype3') == 'GAMMA'
    assert srv_instance.get_extra_info('ttype4') == 'ALPHA'
    assert srv_instance.get_extra_info('TERM') == 'ALPHA'


@pytest.mark.asyncio
async def test_telnet_server_on_ttype_repeated(
        event_loop, bind_host, unused_tcp_port):
    """Test Server's callback method on_ttype() when value repeats. """
    # given
    from telnetlib3.telopt import IAC, WILL, SB, SE, IS, TTYPE
    _waiter = asyncio.Future()
    given_ttypes = ('ALPHA', 'BETA', 'GAMMA', 'GAMMA')

    class ServerTestTtype(telnetlib3.TelnetServer):
        count = 1

        def on_ttype(self, ttype):
            super().on_ttype(ttype)
            if self.count == len(given_ttypes):
                _waiter.set_result(self)
            self.count += 1

    await telnetlib3.create_server(
        protocol_factory=ServerTestTtype,
        host=bind_host, port=unused_tcp_port,
        loop=event_loop)

    reader, writer = await asyncio.open_connection(
        host=bind_host, port=unused_tcp_port, loop=event_loop)

    # exercise,
    writer.write(IAC + WILL + TTYPE)
    for send_ttype in given_ttypes:
        writer.write(IAC + SB + TTYPE + IS +
                     send_ttype.encode('ascii') +
                     IAC + SE)

    # verify,
    srv_instance = await asyncio.wait_for(_waiter, 0.5)
    assert srv_instance.get_extra_info('ttype1') == 'ALPHA'
    assert srv_instance.get_extra_info('ttype2') == 'BETA'
    assert srv_instance.get_extra_info('ttype3') == 'GAMMA'
    assert srv_instance.get_extra_info('ttype4') == 'GAMMA'
    assert srv_instance.get_extra_info('TERM') == 'GAMMA'


@pytest.mark.asyncio
async def test_telnet_server_on_ttype_mud(
        event_loop, bind_host, unused_tcp_port):
    """Test Server's callback method on_ttype() for MUD clients (MTTS). """
    # given
    from telnetlib3.telopt import IAC, WILL, SB, SE, IS, TTYPE
    _waiter = asyncio.Future()
    given_ttypes = ('ALPHA', 'BETA', 'MTTS 137')

    class ServerTestTtype(telnetlib3.TelnetServer):
        count = 1

        def on_ttype(self, ttype):
            super().on_ttype(ttype)
            if self.count == len(given_ttypes):
                _waiter.set_result(self)
            self.count += 1

    await telnetlib3.create_server(
        protocol_factory=ServerTestTtype,
        host=bind_host, port=unused_tcp_port,
        loop=event_loop)

    reader, writer = await asyncio.open_connection(
        host=bind_host, port=unused_tcp_port, loop=event_loop)

    # exercise,
    writer.write(IAC + WILL + TTYPE)
    for send_ttype in given_ttypes:
        writer.write(IAC + SB + TTYPE + IS +
                     send_ttype.encode('ascii') +
                     IAC + SE)

    # verify,
    srv_instance = await asyncio.wait_for(_waiter, 0.5)
    assert srv_instance.get_extra_info('ttype1') == 'ALPHA'
    assert srv_instance.get_extra_info('ttype2') == 'BETA'
    assert srv_instance.get_extra_info('ttype3') == 'MTTS 137'
    assert srv_instance.get_extra_info('TERM') == 'BETA'
