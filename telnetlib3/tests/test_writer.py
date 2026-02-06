# std imports
import asyncio
import threading
from unittest.mock import MagicMock

# 3rd party
import pytest

# local
import telnetlib3
from telnetlib3.telopt import (
    DO,
    GA,
    SB,
    SE,
    TM,
    EOR,
    IAC,
    NOP,
    SGA,
    DONT,
    ECHO,
    NAWS,
    WILL,
    WONT,
    TTYPE,
    CMD_EOR,
    option_from_name,
)
from telnetlib3.tests.accessories import (  # pylint: disable=unused-import
    bind_host,
    create_server,
    open_connection,
    unused_tcp_port,
    asyncio_connection,
)


class SimulSLCServer(telnetlib3.BaseServer):
    """Test server for SLC simulation in kludge mode."""

    slc_callbacks = [
        getattr(telnetlib3.slc, "SLC_" + key)
        for key in (
            "IP",
            "AO",
            "AYT",
            "ABORT",
            "EOF",
            "SUSP",
            "EC",
            "EL",
            "EW",
            "RP",
            "LNEXT",
            "XON",
            "XOFF",
        )
    ]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.waiters = None

    def connection_made(self, transport):
        super().connection_made(transport)
        self.waiters = {slc_cmd: asyncio.Future() for slc_cmd in self.slc_callbacks}

        for slc_cmd in self.slc_callbacks:
            self.writer.set_slc_callback(
                slc_byte=slc_cmd,
                func=lambda byte: self.waiters[byte].set_result(byte),
            )


def test_writer_instantiation_safety():
    """On instantiation, one of server or client must be specified."""
    telnetlib3.TelnetWriter(transport=None, protocol=None, client=True)
    with pytest.raises(TypeError):
        # must define at least server=True or client=True
        telnetlib3.TelnetWriter(transport=None, protocol=None)
    with pytest.raises(TypeError):
        # but cannot define both!
        telnetlib3.TelnetWriter(transport=None, protocol=None, server=True, client=True)


def test_repr():
    """Test writer.__repr__ for client and server viewpoint."""
    srv = telnetlib3.TelnetWriter(transport=None, protocol=None, server=True)
    clt = telnetlib3.TelnetWriter(transport=None, protocol=None, client=True)
    assert repr(srv) == ("<TelnetWriter server " "mode:local +lineflow -xon_any +slc_sim>")
    assert repr(clt) == ("<TelnetWriter client " "mode:local +lineflow -xon_any +slc_sim>")


def test_illegal_2byte_iac():
    """Given an illegal 2byte IAC command, raise ValueError."""
    writer = telnetlib3.TelnetWriter(transport=None, protocol=None, server=True)
    writer.feed_byte(IAC)
    with pytest.raises(ValueError):
        # IAC SGA(b'\x03'): not a legal 2-byte cmd
        writer.feed_byte(SGA)


def test_legal_2byte_iac():
    """Nothing special about a 2-byte IAC, test wiring a callback."""
    called = threading.Event()

    def callback(cmd):
        assert cmd == NOP
        called.set()

    writer = telnetlib3.TelnetWriter(transport=None, protocol=None, server=True)

    writer.set_iac_callback(cmd=NOP, func=callback)
    writer.feed_byte(IAC)
    writer.feed_byte(NOP)

    assert called.is_set()


def test_sb_interrupted():
    """IAC SB gets interrupted by IAC command, resetting and exiting state."""
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
    writer = telnetlib3.TelnetWriter(
        transport=None,
        protocol=None,
        server=True,
    )

    given = IAC + SB + b"sbdata-\xff\xff-sbdata"
    sb_expected = b"sbdata-\xff-sbdata"
    for val in given:
        writer.feed_byte(bytes([val]))
    assert b"".join(writer._sb_buffer) == sb_expected

    writer.feed_byte(IAC)
    with pytest.raises(ValueError, match="SB unhandled"):
        # [SB + b's'] unsolicited,
        writer.feed_byte(SE)

    # the 'IAC TM' interrupts and ends the SB buffer
    given = IAC + SB + b"sbdata-" + IAC + TM + b"-sbdata"
    for val in given:
        writer.feed_byte(bytes([val]))
    assert b"".join(writer._sb_buffer) == b""

    # so, even if you sent an IAC + SE, that is no longer
    # legal for this state.
    writer.feed_byte(b"x")
    writer.feed_byte(IAC)
    with pytest.raises(ValueError, match="not a legal 2-byte cmd"):
        writer.feed_byte(SE)


async def test_iac_do_twice_replies_once(bind_host, unused_tcp_port):
    """WILL/WONT replied only once for repeated DO."""

    async def shell(reader, writer):
        writer.close()
        await writer.wait_closed()

    given_from_client = IAC + DO + ECHO + IAC + DO + ECHO
    expect_from_server = IAC + WILL + ECHO

    async with create_server(
        protocol_factory=telnetlib3.BaseServer,
        host=bind_host,
        shell=shell,
        port=unused_tcp_port,
        connect_maxwait=0.05,
    ):
        async with asyncio_connection(bind_host, unused_tcp_port) as (
            client_reader,
            client_writer,
        ):
            client_writer.write(given_from_client)
            result_from_server = await asyncio.wait_for(client_reader.read(), 0.5)
            assert result_from_server == expect_from_server


async def test_iac_dont_dont(bind_host, unused_tcp_port):
    """WILL/WONT replied only once for repeated DO."""

    async def shell(reader, writer):
        writer.close()
        await writer.wait_closed()

    given_from_client = IAC + DONT + ECHO + IAC + DONT + ECHO
    expect_from_server = b""

    async with create_server(
        protocol_factory=telnetlib3.BaseServer,
        host=bind_host,
        shell=shell,
        port=unused_tcp_port,
        connect_maxwait=0.05,
    ):
        async with asyncio_connection(bind_host, unused_tcp_port) as (
            client_reader,
            client_writer,
        ):
            client_writer.write(given_from_client)
            result_from_server = await asyncio.wait_for(client_reader.read(), 0.5)
            assert result_from_server == expect_from_server


async def test_send_iac_dont_dont(bind_host, unused_tcp_port):
    """Try a DONT and ensure it cannot be sent twice."""
    async with create_server(
        protocol_factory=telnetlib3.BaseServer,
        host=bind_host,
        port=unused_tcp_port,
        connect_maxwait=0.05,
    ) as server:
        async with open_connection(
            host=bind_host, port=unused_tcp_port, connect_minwait=0.05, connect_maxwait=0.05
        ) as (_, client_writer):
            # say it once,
            result = client_writer.iac(DONT, ECHO)
            assert result

            # say it again (this call is suppressed)
            result = client_writer.iac(DONT, ECHO)
            assert result is False

            srv_instance = await asyncio.wait_for(server.wait_for_client(), 3.0)
            server_writer = srv_instance.writer

        # Wait for server to process client disconnect
        await asyncio.sleep(0.1)

        assert client_writer.remote_option[ECHO] is False, client_writer.remote_option
        assert server_writer.local_option[ECHO] is False, server_writer.local_option


async def test_slc_simul(bind_host, unused_tcp_port):
    """Test SLC control characters are simulated in kludge mode."""
    # For example, ^C is simulated as IP (Interrupt Process) callback.
    #
    # First, change server state into kludge mode -- Then, send all control
    # characters.  We ensure all of our various callbacks that are simulated
    # by control characters were 'fired', as well as the raw bytes received
    # as-is.
    given_input_outband = IAC + DO + ECHO + IAC + DO + SGA
    given_input_inband = bytes(range(ord(" "))) + b"\x7f"
    expected_from_server = IAC + WILL + ECHO + IAC + WILL + SGA
    _waiter_input = asyncio.Future()

    async def shell(reader, writer):
        # read everything from client until they hang up.
        result = await reader.read()

        # then report what was received and hangup on client
        _waiter_input.set_result((writer.protocol.waiters, result))
        writer.close()

    server = await telnetlib3.create_server(
        protocol_factory=SimulSLCServer,
        host=bind_host,
        shell=shell,
        port=unused_tcp_port,
        connect_maxwait=0.05,
        encoding=False,
    )

    try:
        client_reader, client_writer = await asyncio.open_connection(
            host=bind_host,
            port=unused_tcp_port,
        )

        # exercise
        client_writer.write(given_input_outband)
        client_writer.write(given_input_inband)
        await client_writer.drain()
        result = await client_reader.readexactly(len(expected_from_server))
        assert result == expected_from_server
        client_writer.close()

        # verify
        callbacks, data_received = await asyncio.wait_for(_waiter_input, 0.5)
        for byte, waiter in callbacks.items():
            assert waiter.done(), telnetlib3.slc.name_slc_command(byte)
        assert data_received == given_input_inband
    finally:
        server.close()
        await server.wait_closed()


async def test_unhandled_do_sends_wont(bind_host, unused_tcp_port):
    """An unhandled DO is denied by WONT."""
    given_input_outband = IAC + DO + NOP
    expected_output = IAC + WONT + NOP

    async with create_server(
        protocol_factory=telnetlib3.BaseServer,
        host=bind_host,
        port=unused_tcp_port,
        connect_maxwait=0.05,
        encoding=False,
    ):
        async with asyncio_connection(bind_host, unused_tcp_port) as (
            client_reader,
            client_writer,
        ):
            client_writer.write(given_input_outband)
            result = await asyncio.wait_for(client_reader.readexactly(len(expected_output)), 0.5)
            assert result == expected_output


async def test_writelines_bytes(bind_host, unused_tcp_port):
    """Exercise bytes-only interface of writer.writelines() function."""
    given = (b"a", b"b", b"c", b"d")
    expected = b"abcd"

    async def shell(reader, writer):
        writer.writelines(given)
        writer.close()
        await writer.wait_closed()

    async with create_server(
        protocol_factory=telnetlib3.BaseServer,
        host=bind_host,
        shell=shell,
        port=unused_tcp_port,
        connect_maxwait=0.05,
        encoding=False,
    ):
        async with asyncio_connection(bind_host, unused_tcp_port) as (
            client_reader,
            client_writer,
        ):
            result = await asyncio.wait_for(client_reader.read(), 0.5)
            assert result == expected


async def test_writelines_unicode(bind_host, unused_tcp_port):
    """Exercise unicode interface of writer.writelines() function."""
    given = ("a", "b", "c", "d")
    expected = b"abcd"

    async def shell(reader, writer):
        writer.writelines(given)
        writer.close()
        await writer.wait_closed()

    async with create_server(
        protocol_factory=telnetlib3.BaseServer,
        host=bind_host,
        shell=shell,
        port=unused_tcp_port,
        connect_maxwait=0.05,
        encoding="ascii",
    ):
        async with asyncio_connection(bind_host, unused_tcp_port) as (
            client_reader,
            client_writer,
        ):
            result = await asyncio.wait_for(client_reader.read(), 0.5)
            assert result == expected


def test_bad_iac():
    """Test using writer.iac for something outside of DO/DONT/WILL/WONT."""
    writer = telnetlib3.TelnetWriter(transport=None, protocol=None, server=True)
    with pytest.raises(ValueError):
        writer.iac(NOP)


async def test_send_ga(bind_host, unused_tcp_port):
    """Writer sends IAC + GA when SGA is not negotiated."""
    expected = IAC + GA

    async def shell(reader, writer):
        result = writer.send_ga()
        assert result is True
        writer.close()
        await writer.wait_closed()

    async with create_server(
        protocol_factory=telnetlib3.BaseServer,
        host=bind_host,
        shell=shell,
        port=unused_tcp_port,
        connect_maxwait=0.05,
    ):
        async with asyncio_connection(bind_host, unused_tcp_port) as (
            client_reader,
            client_writer,
        ):
            result = await asyncio.wait_for(client_reader.read(), 0.5)
            assert result == expected


async def test_not_send_ga(bind_host, unused_tcp_port):
    """Writer does not send IAC + GA when SGA is negotiated."""
    # we require IAC + DO + SGA, and expect a confirming reply.  We also
    # call writer.send_ga() from the shell, whose result should be False
    # (not sent).  The reader never receives an IAC + GA.
    expected = IAC + WILL + SGA

    async def shell(reader, writer):
        result = writer.send_ga()
        assert result is False
        writer.close()
        await writer.wait_closed()

    async with create_server(
        protocol_factory=telnetlib3.BaseServer,
        host=bind_host,
        shell=shell,
        port=unused_tcp_port,
        connect_maxwait=0.05,
    ):
        async with asyncio_connection(bind_host, unused_tcp_port) as (
            client_reader,
            client_writer,
        ):
            client_writer.write(IAC + DO + SGA)
            result = await asyncio.wait_for(client_reader.read(), 0.5)
            assert result == expected


async def test_not_send_eor(bind_host, unused_tcp_port):
    """Writer does not send IAC + EOR when un-negotiated."""
    expected = b""

    async def shell(reader, writer):
        result = writer.send_eor()
        assert result is False
        writer.close()
        await writer.wait_closed()

    async with create_server(
        protocol_factory=telnetlib3.BaseServer,
        host=bind_host,
        shell=shell,
        port=unused_tcp_port,
        connect_maxwait=0.05,
    ):
        async with asyncio_connection(bind_host, unused_tcp_port) as (
            client_reader,
            client_writer,
        ):
            result = await asyncio.wait_for(client_reader.read(), 0.5)
            assert result == expected


async def test_send_eor(bind_host, unused_tcp_port):
    """Writer sends IAC + EOR if client requests by DO."""
    given = IAC + DO + EOR
    expected = IAC + WILL + EOR + b"<" + IAC + CMD_EOR + b">"

    # just verify rfc constants are used appropriately in this context
    assert EOR == bytes([25])
    assert CMD_EOR == bytes([239])

    async def shell(reader, writer):
        writer.write("<")
        result = writer.send_eor()
        assert result is True
        writer.write(">")
        writer.close()
        await writer.wait_closed()

    async with create_server(
        protocol_factory=telnetlib3.BaseServer,
        host=bind_host,
        shell=shell,
        port=unused_tcp_port,
        connect_maxwait=0.05,
    ):
        async with asyncio_connection(bind_host, unused_tcp_port) as (
            client_reader,
            client_writer,
        ):
            client_writer.write(given)
            result = await asyncio.wait_for(client_reader.read(), 0.5)
            assert result == expected


async def test_wait_closed():
    """Test TelnetWriter.wait_closed() method waits for connection to close."""

    class MockTransport:
        def __init__(self):
            self._closing = False

        def close(self):
            self._closing = True

        def is_closing(self):
            return self._closing

        def write(self, data):
            pass

        def get_extra_info(self, name, default=None):
            return default

    class MockProtocol:
        def get_extra_info(self, name, default=None):
            return default

        async def _drain_helper(self):
            pass

    # Create a TelnetWriter instance with mock transport and protocol
    transport = MockTransport()
    protocol = MockProtocol()
    writer = telnetlib3.TelnetWriter(transport, protocol, server=True)

    # Test that wait_closed() doesn't complete immediately
    wait_task = asyncio.create_task(writer.wait_closed())

    # Give it a moment to start
    await asyncio.sleep(0.01)

    # Should not be done yet
    assert not wait_task.done(), "wait_closed() should not complete before close()"

    # Now close the writer
    writer.close()

    # Give it a moment to complete
    await asyncio.sleep(0.01)

    # Now wait_closed() should complete
    assert wait_task.done(), "wait_closed() should complete after close()"

    # Wait for the task to complete (should not raise)
    await wait_task

    # Test calling wait_closed() after close() - should complete immediately
    await writer.wait_closed()  # Should complete immediately


def test_option_from_name():
    """Test option_from_name returns correct option bytes."""
    assert option_from_name("NAWS") == NAWS
    assert option_from_name("naws") == NAWS
    assert option_from_name("TTYPE") == TTYPE
    assert option_from_name("ECHO") == ECHO

    with pytest.raises(KeyError):
        option_from_name("INVALID_OPTION")


async def test_wait_for_immediate_return():
    """Test wait_for returns immediately when conditions already met."""
    writer = telnetlib3.TelnetWriter(transport=None, protocol=None, server=True)
    writer.remote_option[ECHO] = True

    result = await writer.wait_for(remote={"ECHO": True})
    assert result is True


async def test_wait_for_remote_option():
    """Test wait_for waits for remote option to become true."""
    writer = telnetlib3.TelnetWriter(transport=None, protocol=None, server=True)

    async def set_option_later():
        await asyncio.sleep(0.01)
        writer.remote_option[ECHO] = True

    task = asyncio.create_task(set_option_later())
    result = await asyncio.wait_for(writer.wait_for(remote={"ECHO": True}), 0.5)
    assert result is True
    await task


async def test_wait_for_local_option():
    """Test wait_for waits for local option to become true."""
    writer = telnetlib3.TelnetWriter(transport=None, protocol=None, server=True)

    async def set_option_later():
        await asyncio.sleep(0.01)
        writer.local_option[ECHO] = True

    task = asyncio.create_task(set_option_later())
    result = await asyncio.wait_for(writer.wait_for(local={"ECHO": True}), 0.5)
    assert result is True
    await task


async def test_wait_for_pending_false():
    """Test wait_for waits for pending option to become false."""
    writer = telnetlib3.TelnetWriter(transport=None, protocol=None, server=True)
    writer.pending_option[DO + TTYPE] = True

    async def clear_pending_later():
        await asyncio.sleep(0.01)
        writer.pending_option[DO + TTYPE] = False

    task = asyncio.create_task(clear_pending_later())
    result = await asyncio.wait_for(writer.wait_for(pending={"TTYPE": False}), 0.5)
    assert result is True
    await task


async def test_wait_for_combined_conditions():
    """Test wait_for with multiple conditions."""
    writer = telnetlib3.TelnetWriter(transport=None, protocol=None, server=True)

    async def set_options_later():
        await asyncio.sleep(0.01)
        writer.remote_option[ECHO] = True
        await asyncio.sleep(0.01)
        writer.local_option[NAWS] = True

    task = asyncio.create_task(set_options_later())
    result = await asyncio.wait_for(
        writer.wait_for(remote={"ECHO": True}, local={"NAWS": True}), 0.5
    )
    assert result is True
    await task


async def test_wait_for_invalid_option():
    """Test wait_for raises KeyError for invalid option names."""
    writer = telnetlib3.TelnetWriter(transport=None, protocol=None, server=True)

    with pytest.raises(KeyError):
        await writer.wait_for(remote={"INVALID": True})


async def test_wait_for_cancelled_on_close():
    """Test wait_for is cancelled when connection closes."""
    writer = telnetlib3.TelnetWriter(transport=None, protocol=None, server=True)

    wait_task = asyncio.create_task(writer.wait_for(remote={"ECHO": True}))
    await asyncio.sleep(0.01)

    assert not wait_task.done()
    writer.close()

    with pytest.raises(asyncio.CancelledError):
        await wait_task


async def test_wait_for_condition_immediate():
    """Test wait_for_condition returns immediately when condition met."""
    writer = telnetlib3.TelnetWriter(transport=None, protocol=None, server=True)

    result = await writer.wait_for_condition(lambda w: w.server is True)
    assert result is True


async def test_wait_for_condition_waits():
    """Test wait_for_condition waits for condition to become true."""
    writer = telnetlib3.TelnetWriter(transport=None, protocol=None, server=True)

    async def set_option_later():
        await asyncio.sleep(0.01)
        writer.remote_option[ECHO] = True

    task = asyncio.create_task(set_option_later())
    result = await asyncio.wait_for(
        writer.wait_for_condition(lambda w: w.remote_option.enabled(ECHO)), 0.5
    )
    assert result is True
    await task


async def test_wait_for_cleanup_on_success():
    """Test that waiters are cleaned up after successful completion."""
    writer = telnetlib3.TelnetWriter(transport=None, protocol=None, server=True)

    async def set_option_later():
        await asyncio.sleep(0.01)
        writer.remote_option[ECHO] = True

    task = asyncio.create_task(set_option_later())
    await asyncio.wait_for(writer.wait_for(remote={"ECHO": True}), 0.5)
    await task

    assert len(writer._waiters) == 0
