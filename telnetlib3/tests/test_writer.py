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


def test_writer_instantiation_safety():
    """On instantiation, one of server or client must be specified."""
    telnetlib3.TelnetWriter(transport=None, protocol=None, client=True)
    with pytest.raises(TypeError):
        # must define at least server=True or client=True
        telnetlib3.TelnetWriter(transport=None, protocol=None)
    with pytest.raises(TypeError):
        # but cannot define both!
        telnetlib3.TelnetWriter(transport=None, protocol=None,
                                server=True, client=True)


def test_repr():
    """Test writer.__repr__ for client and server viewpoint."""
    srv = telnetlib3.TelnetWriter(transport=None, protocol=None, server=True)
    clt = telnetlib3.TelnetWriter(transport=None, protocol=None, client=True)
    assert repr(srv) == ('<TelnetWriter server '
                         'mode:local +lineflow -xon_any +slc_sim>')
    assert repr(clt) == ('<TelnetWriter client '
                         'mode:local +lineflow -xon_any +slc_sim>')


def test_illegal_2byte_iac():
    """Given an illegal 2byte IAC command, raise ValueError."""
    writer = telnetlib3.TelnetWriter(transport=None,
                                     protocol=None,
                                     server=True)
    from telnetlib3.telopt import IAC, SGA
    writer.feed_byte(IAC)
    with pytest.raises(ValueError):
        # IAC SGA(b'\x03'): not a legal 2-byte cmd
        writer.feed_byte(SGA)


def test_legal_2byte_iac():
    """Nothing special about a 2-byte IAC, test wiring a callback."""
    from telnetlib3.telopt import IAC, NOP

    import threading
    called = threading.Event()

    def callback(cmd):
        assert cmd == NOP
        called.set()

    writer = telnetlib3.TelnetWriter(transport=None,
                                     protocol=None,
                                     server=True)

    writer.set_iac_callback(cmd=NOP, func=callback)
    writer.feed_byte(IAC)
    writer.feed_byte(NOP)

    assert called.is_set()


def test_sb_interrupted():
    """IAC SB gets interrupted by IAC command, reseting and exiting state."""
    # when within an SB buffer, all SB protocols we know about remark that
    # IAC must be escaped -- for example, the NAWS negotiation of a 65535
    # by 0 window size should be '\xff\xff\xff\xff\x00\x00' -- so if we
    # receive an IAC **not** followed by an IAC while within a sub-negotiation
    # buffer, we are in miscommunication.  The remote end is not RFC complaint,
    # not a telnet server, or is simply fuzzing us.
    #
    # instead of awaiting the unlikely SE, and throwing all intermediary bytes
    # out, we just clear what we have received so far within this so called
    # 'SB', and exit the sb buffering state.
    from telnetlib3.telopt import IAC, SB, TM, SE
    import logging
    log = logging.getLogger(__name__)
    log.setLevel(logging.DEBUG)
    writer = telnetlib3.TelnetWriter(transport=None,
                                     protocol=None,
                                     server=True, log=log)

    given = IAC + SB + b'sbdata-\xff\xff-sbdata'
    sb_expected = b'sbdata-\xff-sbdata'
    for val in given:
        writer.feed_byte(bytes([val]))
    assert b''.join(writer._sb_buffer) == sb_expected

    writer.feed_byte(IAC)
    with pytest.raises(ValueError, match ='SB unhandled'):
        # [SB + b's'] unsolicited,
        writer.feed_byte(SE)

    # the 'IAC TM' interrupts and ends the SB buffer
    given = IAC + SB + b'sbdata-' + IAC + TM + b'-sbdata'
    for val in given:
        writer.feed_byte(bytes([val]))
    assert b''.join(writer._sb_buffer) == b''

    # so, even if you sent an IAC + SE, that is no longer
    # legal for this state.
    writer.feed_byte(b'x')
    writer.feed_byte(IAC)
    with pytest.raises(ValueError, match='not a legal 2-byte cmd'):
        writer.feed_byte(SE)


@pytest.mark.asyncio
async def test_iac_do_twice_replies_once(event_loop, bind_host, unused_tcp_port):
    """WILL/WONT replied only once for repeated DO."""
    from telnetlib3.telopt import IAC, DO, WILL, ECHO

    @asyncio.coroutine
    def shell(reader, writer):
        writer.close()

    # given,
    given_from_client = IAC + DO + ECHO + IAC + DO + ECHO
    expect_from_server = IAC + WILL + ECHO

    await telnetlib3.create_server(
        protocol_factory=telnetlib3.BaseServer, host=bind_host, shell=shell,
        port=unused_tcp_port, loop=event_loop, connect_maxwait=0.05)

    client_reader, client_writer = await asyncio.open_connection(
        host=bind_host, port=unused_tcp_port, loop=event_loop)

    # exercise,
    client_writer.write(given_from_client)

    # verify,
    result_from_server = await asyncio.wait_for(client_reader.read(), 0.5)

    assert result_from_server == expect_from_server


@pytest.mark.asyncio
async def test_iac_dont_dont(event_loop, bind_host, unused_tcp_port):
    """WILL/WONT replied only once for repeated DO."""
    from telnetlib3.telopt import IAC, DONT, ECHO

    @asyncio.coroutine
    def shell(reader, writer):
        writer.close()

    # given,
    given_from_client = IAC + DONT + ECHO + IAC + DONT + ECHO
    expect_from_server = b''

    await telnetlib3.create_server(
        protocol_factory=telnetlib3.BaseServer, host=bind_host, shell=shell,
        port=unused_tcp_port, loop=event_loop, connect_maxwait=0.05)

    client_reader, client_writer = await asyncio.open_connection(
        host=bind_host, port=unused_tcp_port, loop=event_loop)

    # exercise,
    client_writer.write(given_from_client)

    # verify,
    result_from_server = await asyncio.wait_for(client_reader.read(), 0.5)

    assert result_from_server == expect_from_server



@pytest.mark.asyncio
async def test_send_iac_dont_dont(event_loop, bind_host, unused_tcp_port):
    """Try a DONT and ensure it cannot be sent twice."""
    from telnetlib3.telopt import DONT, ECHO
    _waiter = asyncio.Future()

    await telnetlib3.create_server(
        protocol_factory=telnetlib3.BaseServer, host=bind_host,
        port=unused_tcp_port, loop=event_loop, connect_maxwait=0.05,
        _waiter_connected=_waiter)

    _, client_writer = await telnetlib3.open_connection(
        host=bind_host, port=unused_tcp_port, loop=event_loop,
        connect_minwait=0.05)

    # say it once,
    result = client_writer.iac(DONT, ECHO)
    assert result == True

    # say it again, suppressed
    result = client_writer.iac(DONT, ECHO)
    assert result == False

    server_writer = (await asyncio.wait_for(_waiter, 0.5)).writer

    assert client_writer.remote_option[ECHO] is False
    assert server_writer.local_option[ECHO] is False


@pytest.mark.asyncio
async def test_slc_simul(event_loop, bind_host, unused_tcp_port):
    """Test SLC control characters are simulated in kludge mode."""
    # For example, ^C is simulated as IP (Interrupt Process) callback.
    from telnetlib3.telopt import IAC, DO, WILL, ECHO, SGA

    # First, change server state into kludge mode -- Then, send all control
    # characters.  We ensure all of our various callbacks that are simulated
    # by control characters were 'fired', as well as the raw bytes received
    # as-is.
    given_input_outband = IAC + DO + ECHO + IAC + DO + SGA
    given_input_inband = bytes(range(ord(' '))) + b'\x7f'
    expected_from_server = IAC + WILL + ECHO + IAC + WILL + SGA
    _waiter_input = asyncio.Future()

    @asyncio.coroutine
    def shell(reader, writer):
        # read everything from client until they hang up.
        result = yield from reader.read()

        # then report what was received and hangup on client
        _waiter_input.set_result((writer.protocol.waiters, result))

        writer.close()

    class SimulSLCServer(telnetlib3.BaseServer):
        slc_callbacks = [getattr(telnetlib3.slc, 'SLC_' + key)
                         # no default value for break, sync, or end-of-record.
                         for key in ('IP', 'AO', 'AYT',
                                     'ABORT', 'EOF', 'SUSP', 'EC', 'EL',
                                     'EW', 'RP', 'LNEXT', 'XON', 'XOFF')]

        def connection_made(self, transport):
            super().connection_made(transport)
            self.waiters = {slc_cmd: asyncio.Future()
                            for slc_cmd in self.slc_callbacks}

            for slc_cmd in self.slc_callbacks:
                self.writer.set_slc_callback(
                    slc_byte=slc_cmd,
                    func=lambda byte: self.waiters[byte].set_result(byte))

    await telnetlib3.create_server(
        protocol_factory=SimulSLCServer, host=bind_host, shell=shell,
        port=unused_tcp_port, loop=event_loop, connect_maxwait=0.05,
        encoding=False)

    client_reader, client_writer = await asyncio.open_connection(
        host=bind_host, port=unused_tcp_port, loop=event_loop)

    # exercise,
    client_writer.write(given_input_outband)
    client_writer.write(given_input_inband)
    await client_writer.drain()
    result = await client_reader.readexactly(len(expected_from_server))
    assert result == expected_from_server
    client_writer.close()

    # verify,
    callbacks, data_received = await asyncio.wait_for(_waiter_input, 0.5)
    for byte, waiter in callbacks.items():
        assert waiter.done(), telnetlib3.slc.name_slc_command(byte)
    assert data_received == given_input_inband


@pytest.mark.asyncio
async def test_unhandled_do_sends_wont(event_loop, bind_host, unused_tcp_port):
    """An unhandled DO is denied by WONT."""
    from telnetlib3.telopt import IAC, DO, NOP, WONT
    given_input_outband = IAC + DO + NOP
    expected_output = IAC + WONT + NOP

    await telnetlib3.create_server(
        protocol_factory=telnetlib3.BaseServer,
        host=bind_host, port=unused_tcp_port, loop=event_loop,
        connect_maxwait=0.05, encoding=False)

    client_reader, client_writer = await asyncio.open_connection(
        host=bind_host, port=unused_tcp_port, loop=event_loop)

    # exercise,
    client_writer.write(given_input_outband)
    result = await asyncio.wait_for(
        client_reader.readexactly(len(expected_output)), 0.5)
    assert result == expected_output


@pytest.mark.asyncio
async def test_writelines_bytes(event_loop, bind_host, unused_tcp_port):
    """Exercise bytes-only interface of writer.writelines() function."""
    given = (b'a', b'b', b'c', b'd')
    expected = b'abcd'

    @asyncio.coroutine
    def shell(reader, writer):
        writer.writelines(given)
        writer.close()

    await telnetlib3.create_server(
        protocol_factory=telnetlib3.BaseServer, host=bind_host, shell=shell,
        port=unused_tcp_port, loop=event_loop, connect_maxwait=0.05,
        encoding=False)

    client_reader, client_writer = await asyncio.open_connection(
        host=bind_host, port=unused_tcp_port, loop=event_loop)

    # verify,
    result = await asyncio.wait_for(client_reader.read(), 0.5)

    assert result == expected


@pytest.mark.asyncio
async def test_writelines_unicode(event_loop, bind_host, unused_tcp_port):
    """Exercise unicode interface of writer.writelines() function."""
    given = ('a', 'b', 'c', 'd')
    expected = b'abcd'

    @asyncio.coroutine
    def shell(reader, writer):
        writer.writelines(given)
        writer.close()

    await telnetlib3.create_server(
        protocol_factory=telnetlib3.BaseServer, host=bind_host, shell=shell,
        port=unused_tcp_port, loop=event_loop, connect_maxwait=0.05,
        encoding='ascii')

    client_reader, client_writer = await asyncio.open_connection(
        host=bind_host, port=unused_tcp_port, loop=event_loop)

    # verify,
    result = await asyncio.wait_for(client_reader.read(), 0.5)

    assert result == expected


def test_bad_iac():
    """Test using writer.iac for something outside of DO/DONT/WILL/WONT."""
    from telnetlib3.telopt import NOP
    writer = telnetlib3.TelnetWriter(transport=None,
                                     protocol=None,
                                     server=True)
    with pytest.raises(ValueError):
        writer.iac(NOP)


@pytest.mark.asyncio
async def test_send_ga(event_loop, bind_host, unused_tcp_port):
    """Writer sends IAC + GA when SGA is not negotiated."""
    from telnetlib3.telopt import IAC, GA
    expected = IAC + GA

    @asyncio.coroutine
    def shell(reader, writer):
        result = writer.send_ga()
        assert result is True
        writer.close()

    await telnetlib3.create_server(
        protocol_factory=telnetlib3.BaseServer, host=bind_host, shell=shell,
        port=unused_tcp_port, loop=event_loop, connect_maxwait=0.05)

    client_reader, client_writer = await asyncio.open_connection(
        host=bind_host, port=unused_tcp_port, loop=event_loop)

    # verify,
    result = await asyncio.wait_for(client_reader.read(), 0.5)

    assert result == expected


@pytest.mark.asyncio
async def test_not_send_ga(event_loop, bind_host, unused_tcp_port):
    """Writer does not send IAC + GA when SGA is negotiated."""
    from telnetlib3.telopt import IAC, DO, WILL, SGA

    # we requires IAC + DO + SGA, and expect a confirming reply.  We also
    # call writer.send_ga() from the shell, whose result should be False
    # (not sent).  The reader never receives an IAC + GA.
    expected = IAC + WILL + SGA

    @asyncio.coroutine
    def shell(reader, writer):
        result = writer.send_ga()
        assert result is False
        writer.close()

    await telnetlib3.create_server(
        protocol_factory=telnetlib3.BaseServer, host=bind_host, shell=shell,
        port=unused_tcp_port, loop=event_loop, connect_maxwait=0.05)

    client_reader, client_writer = await asyncio.open_connection(
        host=bind_host, port=unused_tcp_port, loop=event_loop)

    client_writer.write(IAC + DO + SGA)

    # verify,
    result = await asyncio.wait_for(client_reader.read(), 0.5)
    assert result == expected


@pytest.mark.asyncio
async def test_not_send_eor(event_loop, bind_host, unused_tcp_port):
    """Writer does not send IAC + EOR when un-negotiated."""
    expected = b''

    @asyncio.coroutine
    def shell(reader, writer):
        result = writer.send_eor()
        assert result is False
        writer.close()

    await telnetlib3.create_server(
        protocol_factory=telnetlib3.BaseServer, host=bind_host, shell=shell,
        port=unused_tcp_port, loop=event_loop, connect_maxwait=0.05)

    client_reader, client_writer = await asyncio.open_connection(
        host=bind_host, port=unused_tcp_port, loop=event_loop)

    # verify,
    result = await asyncio.wait_for(client_reader.read(), 0.5)

    assert result == expected


@pytest.mark.asyncio
async def test_send_eor(event_loop, bind_host, unused_tcp_port):
    """Writer sends IAC + EOR if client requests by DO."""
    from telnetlib3.telopt import IAC, DO, WILL, CMD_EOR, EOR
    given = IAC + DO + EOR
    expected = IAC + WILL + EOR + b'<' + IAC + CMD_EOR + b'>'

    # just verify rfc constants are used appropriately in this context
    assert EOR == bytes([25])
    assert CMD_EOR == bytes([239])

    @asyncio.coroutine
    def shell(reader, writer):
        writer.write('<')
        result = writer.send_eor()
        assert result is True
        writer.write('>')
        writer.close()

    await telnetlib3.create_server(
        protocol_factory=telnetlib3.BaseServer, host=bind_host, shell=shell,
        port=unused_tcp_port, loop=event_loop, connect_maxwait=0.05)

    client_reader, client_writer = await asyncio.open_connection(
        host=bind_host, port=unused_tcp_port, loop=event_loop)

    # verify,
    client_writer.write(given)
    result = await asyncio.wait_for(client_reader.read(), 0.5)

    assert result == expected
