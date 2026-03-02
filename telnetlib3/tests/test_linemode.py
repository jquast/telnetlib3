"""Test LINEMODE, rfc-1184_."""

# std imports
import sys
import asyncio
import collections

# 3rd party
import pytest

# local
import telnetlib3
import telnetlib3.stream_writer
from telnetlib3 import slc
from telnetlib3.slc import LMODE_SLC, LMODE_MODE, LMODE_MODE_ACK, LMODE_MODE_LOCAL
from telnetlib3.telopt import DO, SB, SE, IAC, WILL, LINEMODE
from telnetlib3.stream_writer import TelnetWriter
from telnetlib3.tests.accessories import (
    MockProtocol,
    MockTransport,
    create_server,
    asyncio_connection,
)


def _make_server_writer():
    t = MockTransport()
    p = MockProtocol()
    w = TelnetWriter(t, p, server=True)
    return w, t


def _make_client_writer():
    t = MockTransport()
    p = MockProtocol()
    w = TelnetWriter(t, p, client=True)
    return w, t


async def test_server_demands_remote_linemode_client_agrees(bind_host, unused_tcp_port):
    class ServerTestLinemode(telnetlib3.BaseServer):
        def begin_negotiation(self):
            super().begin_negotiation()
            self.writer.iac(DO, LINEMODE)
            asyncio.get_event_loop().call_later(0.1, self.connection_lost, None)

    async with create_server(
        protocol_factory=ServerTestLinemode, host=bind_host, port=unused_tcp_port
    ) as server:
        async with asyncio_connection(bind_host, unused_tcp_port) as (client_reader, client_writer):
            expect_mode = telnetlib3.stream_writer.TelnetWriter.default_linemode.mask
            expect_stage1 = IAC + DO + LINEMODE
            expect_stage2 = IAC + SB + LINEMODE + LMODE_MODE + expect_mode + IAC + SE

            reply_mode = bytes([ord(expect_mode) | ord(LMODE_MODE_ACK)])
            reply_stage1 = IAC + WILL + LINEMODE
            reply_stage2 = IAC + SB + LINEMODE + LMODE_MODE + reply_mode + IAC + SE

            result = await client_reader.readexactly(len(expect_stage1))
            assert result == expect_stage1
            client_writer.write(reply_stage1)

            result = await client_reader.readexactly(len(expect_stage2))
            assert result == expect_stage2
            client_writer.write(reply_stage2)

            srv_instance = await asyncio.wait_for(server.wait_for_client(), 0.1)
            await asyncio.wait_for(
                srv_instance.writer.wait_for(
                    remote={"LINEMODE": True}, pending={"LINEMODE": False}
                ),
                0.1,
            )

            # server sends SLC table after MODE ACK; drain remaining bytes to reach EOF
            result = await client_reader.read()
            assert result.startswith(IAC + SB + LINEMODE + LMODE_SLC)

            assert srv_instance.writer.mode == "remote"
            assert srv_instance.writer.linemode.remote is True
            assert srv_instance.writer.linemode.local is False
            assert srv_instance.writer.linemode.trapsig is False
            assert srv_instance.writer.linemode.ack is True
            assert srv_instance.writer.linemode.soft_tab is False
            assert srv_instance.writer.linemode.lit_echo is True


async def test_server_demands_remote_linemode_client_demands_local(bind_host, unused_tcp_port):
    class ServerTestLinemode(telnetlib3.BaseServer):
        def begin_negotiation(self):
            super().begin_negotiation()
            self.writer.iac(DO, LINEMODE)
            asyncio.get_event_loop().call_later(0.1, self.connection_lost, None)

    async with create_server(
        protocol_factory=ServerTestLinemode, host=bind_host, port=unused_tcp_port
    ) as server:
        async with asyncio_connection(bind_host, unused_tcp_port) as (client_reader, client_writer):
            expect_mode = telnetlib3.stream_writer.TelnetWriter.default_linemode.mask
            expect_stage1 = IAC + DO + LINEMODE
            expect_stage2 = IAC + SB + LINEMODE + LMODE_MODE + expect_mode + IAC + SE

            # No, we demand local mode -- using ACK will finalize such request
            reply_mode = bytes([ord(LMODE_MODE_LOCAL) | ord(LMODE_MODE_ACK)])
            reply_stage1 = IAC + WILL + LINEMODE
            reply_stage2 = IAC + SB + LINEMODE + LMODE_MODE + reply_mode + IAC + SE

            result = await client_reader.readexactly(len(expect_stage1))
            assert result == expect_stage1
            client_writer.write(reply_stage1)

            result = await client_reader.readexactly(len(expect_stage2))
            assert result == expect_stage2
            client_writer.write(reply_stage2)

            srv_instance = await asyncio.wait_for(server.wait_for_client(), 0.1)
            await asyncio.wait_for(
                srv_instance.writer.wait_for(
                    remote={"LINEMODE": True}, pending={"LINEMODE": False}
                ),
                0.1,
            )

            # server sends SLC table after MODE ACK; drain remaining bytes to reach EOF
            result = await client_reader.read()
            assert result.startswith(IAC + SB + LINEMODE + LMODE_SLC)

            assert srv_instance.writer.mode == "local"
            assert srv_instance.writer.linemode.remote is False
            assert srv_instance.writer.linemode.local is True
            assert srv_instance.writer.linemode.trapsig is False
            assert srv_instance.writer.linemode.ack is True
            assert srv_instance.writer.linemode.soft_tab is False
            assert srv_instance.writer.linemode.lit_echo is False


def test_slc_validation_rejects_misaligned():
    """_handle_sb_linemode_slc raises ValueError for non-multiple-of-3 buffer."""
    w, _ = _make_server_writer()
    buf = collections.deque([b"\x09", b"\x02", b"\x03", b"\x04"])
    with pytest.raises(ValueError, match="multiple of 3"):
        w._handle_sb_linemode_slc(buf)


def test_slc_change_default_uses_default_table():
    """_slc_change with SLC_DEFAULT restores value from default table, not incoming."""
    w, _ = _make_server_writer()
    default_val = w.default_slc_tab[slc.SLC_EC].val
    slc_def = slc.SLC(slc.SLC_DEFAULT, b"\x03")
    w._slc_change(slc.SLC_EC, slc_def)
    assert w.slctab[slc.SLC_EC].val == default_val
    assert w.slctab[slc.SLC_EC].val != b"\x03"


def test_forwardmask_stored():
    """_handle_do_forwardmask stores a Forwardmask for valid lengths."""
    w, _ = _make_client_writer()
    assert w.forwardmask is None
    buf = collections.deque([bytes([b]) for b in b"\x00" * 16])
    w._handle_do_forwardmask(buf)
    assert w.forwardmask is not None
    assert isinstance(w.forwardmask, slc.Forwardmask)
    assert len(w.forwardmask.value) == 16


@pytest.mark.parametrize("length", [0, 33])
def test_forwardmask_invalid_length(length):
    """_handle_do_forwardmask logs warning and stores nothing for invalid lengths."""
    w, _ = _make_client_writer()
    buf = collections.deque([bytes([0]) for _ in range(length)])
    w._handle_do_forwardmask(buf)
    assert w.forwardmask is None


def test_client_sends_slc_request_on_will_linemode():
    """Client emits SLC (0, SLC_DEFAULT, 0) triplet immediately after WILL LINEMODE."""
    w, t = _make_client_writer()
    w.handle_do(LINEMODE)
    all_writes = b"".join(t.writes)
    assert IAC + WILL + LINEMODE in all_writes
    assert IAC + SB + LINEMODE + LMODE_SLC in all_writes
    from telnetlib3.telopt import theNULL

    assert theNULL + slc.SLC_DEFAULT + theNULL in all_writes


def test_server_sends_slc_after_mode_ack():
    """Server proactively sends SLC table after client acknowledges MODE."""
    w, t = _make_server_writer()
    w.remote_option[LINEMODE] = True
    buf = collections.deque([slc.LMODE_MODE_ACK])
    w._handle_sb_linemode_mode(buf)
    all_writes = b"".join(t.writes)
    assert IAC + SB + LINEMODE + LMODE_SLC in all_writes
    assert w._slc_sent is True
    t.writes.clear()
    w._handle_sb_linemode_mode(collections.deque([slc.LMODE_MODE_ACK]))
    assert IAC + SB + LINEMODE + LMODE_SLC not in b"".join(t.writes)


def test_linemode_buffer_ec_el_ew():
    """LinemodeBuffer.feed() handles EC, EL, EW SLC functions correctly."""
    from telnetlib3.client_shell import LinemodeBuffer

    slctab = slc.generate_slctab(slc.BSD_SLC_TAB)
    buf = LinemodeBuffer(slctab=slctab)
    for c in "hel":
        buf.feed(c)
    # EC (^? = 0x7F in BSD_SLC_TAB)
    echo, data = buf.feed("\x7f")
    assert echo == "\b \b"
    assert data is None
    assert buf._buf == ["h", "e"]
    # EL (^U = 0x15)
    echo, data = buf.feed("\x15")
    assert echo == "\b \b" * 2
    assert data is None
    assert buf._buf == []
    # EW (^W = 0x17) -- add a word first
    for c in "hello world":
        buf.feed(c)
    echo, data = buf.feed("\x17")
    assert echo == "\b \b" * 5
    assert data is None
    assert buf._buf == list("hello ")


def test_linemode_buffer_forwardmask_flush():
    """LinemodeBuffer flushes buffer immediately when a forwardmask character arrives."""
    from telnetlib3.client_shell import LinemodeBuffer

    fm_bytes = bytearray(16)
    # byte 0x01: mask=0, flag=2**(7-1)=64=0x40
    fm_bytes[0] = 0x40
    fm = slc.Forwardmask(bytes(fm_bytes))
    slctab = slc.generate_slctab()
    buf = LinemodeBuffer(slctab=slctab, forwardmask=fm)
    buf.feed("a")
    buf.feed("b")
    echo, data = buf.feed("\x01")
    assert data == b"ab\x01"
    assert buf._buf == []


def test_linemode_buffer_trapsig():
    """LinemodeBuffer returns IAC command bytes for signal chars when TRAPSIG is on."""
    from telnetlib3.telopt import IP, IAC
    from telnetlib3.client_shell import LinemodeBuffer

    slctab = slc.generate_slctab(slc.BSD_SLC_TAB)
    buf = LinemodeBuffer(slctab=slctab, trapsig=True)
    # SLC_IP is ^C = 0x03 in BSD_SLC_TAB
    echo, data = buf.feed("\x03")
    assert echo == ""
    assert data == IAC + IP


def test_linemode_buffer_ec_empty_buf():
    """EC on an empty LinemodeBuffer returns empty echo and no data."""
    from telnetlib3.client_shell import LinemodeBuffer

    slctab = slc.generate_slctab(slc.BSD_SLC_TAB)
    buf = LinemodeBuffer(slctab=slctab)
    # EC = 0x7F in BSD_SLC_TAB; buffer is empty
    echo, data = buf.feed("\x7f")
    assert echo == ""
    assert data is None
    assert buf._buf == []


def test_linemode_buffer_cr_sends_line():
    """CR flushes the buffer and returns the line as bytes for the server."""
    from telnetlib3.client_shell import LinemodeBuffer

    slctab = slc.generate_slctab(slc.BSD_SLC_TAB)
    buf = LinemodeBuffer(slctab=slctab)
    for c in "hello":
        buf.feed(c)
    echo, data = buf.feed("\r")
    assert echo == "\r"
    assert data == b"hello\r"
    assert buf._buf == []


def test_linemode_buffer_lf_sends_line():
    """LF flushes the buffer and returns the line as bytes for the server."""
    from telnetlib3.client_shell import LinemodeBuffer

    slctab = slc.generate_slctab(slc.BSD_SLC_TAB)
    buf = LinemodeBuffer(slctab=slctab)
    for c in "hi":
        buf.feed(c)
    echo, data = buf.feed("\n")
    assert echo == "\n"
    assert data == b"hi\n"
    assert buf._buf == []


def test_linemode_buffer_trapsig_regular_char_buffered():
    """Regular char with trapsig=True is buffered, not sent as IAC."""
    from telnetlib3.client_shell import LinemodeBuffer

    slctab = slc.generate_slctab(slc.BSD_SLC_TAB)
    buf = LinemodeBuffer(slctab=slctab, trapsig=True)
    echo, data = buf.feed("a")
    assert echo == "a"
    assert data is None
    assert buf._buf == ["a"]


def test_linemode_buffer_slc_val_nosupport():
    """_slc_val returns None when the SLC entry is nosupport."""
    from telnetlib3.client_shell import LinemodeBuffer

    slctab = slc.generate_slctab()
    # Force SLC_EC to nosupport
    slctab[slc.SLC_EC] = slc.SLC(slc.SLC_NOSUPPORT, slc.theNULL)
    buf = LinemodeBuffer(slctab=slctab)
    assert buf._slc_val(slc.SLC_EC) is None


def test_linemode_buffer_slc_val_missing():
    """_slc_val returns None when the SLC function is absent from the table."""
    from telnetlib3.client_shell import LinemodeBuffer

    buf = LinemodeBuffer(slctab={})
    assert buf._slc_val(slc.SLC_EC) is None


def test_slc_send_uses_parameter_not_self_slctab():
    """_slc_send(slctab) sends values from the given table, not self.slctab."""
    w, t = _make_server_writer()
    # Modify slctab so SLC_EC has a different value from the default
    modified_val = b"\x42"
    default_val = w.default_slc_tab[slc.SLC_EC].val
    assert default_val != modified_val, "precondition: values must differ"
    w.slctab[slc.SLC_EC] = slc.SLC(slc.SLC_VARIABLE, modified_val)

    # Call _slc_send with the default table; result should contain default_val
    w._slc_start()
    w._slc_send(w.default_slc_tab)
    w._slc_end()

    sent = b"".join(t.writes)
    # Locate the SLC_EC triplet (func, flag, val) in the sent bytes
    ec_byte = slc.SLC_EC
    idx = 0
    found_default = False
    found_modified = False
    while idx < len(sent) - 2:
        if sent[idx : idx + 1] == ec_byte:
            val = sent[idx + 2 : idx + 3]
            if val == default_val:
                found_default = True
            if val == modified_val:
                found_modified = True
        idx += 1
    assert found_default, "expected default value in SLC table"
    assert not found_modified, "must not send modified self.slctab value"


def test_slc_process_default_resets_slctab():
    """(0, SLC_DEFAULT, 0) resets self.slctab to the default table."""
    from telnetlib3.telopt import theNULL

    w, _ = _make_server_writer()
    # Modify an entry so it differs from the default
    w.slctab[slc.SLC_EC] = slc.SLC(slc.SLC_VARIABLE, b"\x42")
    assert w.slctab[slc.SLC_EC].val != w.default_slc_tab[slc.SLC_EC].val

    # Process the special (0, SLC_DEFAULT, 0) request
    w._slc_process(theNULL, slc.SLC(slc.SLC_DEFAULT, theNULL))

    assert w.slctab[slc.SLC_EC].val == w.default_slc_tab[slc.SLC_EC].val


def test_server_sends_slc_table_exactly_once():
    """Server sends SLC table on (0, SLC_DEFAULT, 0) and not again on MODE ACK."""
    from telnetlib3.telopt import theNULL

    w, t = _make_server_writer()
    w.remote_option[LINEMODE] = True

    # Simulate client sending (0, SLC_DEFAULT, 0)
    buf = collections.deque([theNULL, slc.SLC_DEFAULT, theNULL])
    w._handle_sb_linemode_slc(buf)
    slc_table_header = IAC + SB + LINEMODE + LMODE_SLC
    first_send = b"".join(t.writes)
    assert slc_table_header in first_send
    assert w._slc_sent is True

    # Now simulate client sending MODE ACK: server must NOT send SLC table again
    t.writes.clear()
    mode_ack_buf = collections.deque([slc.LMODE_MODE_ACK])
    w._handle_sb_linemode_mode(mode_ack_buf)
    second_send = b"".join(t.writes)
    assert slc_table_header not in second_send


def test_linemode_buffer_ew_skips_trailing_spaces():
    """EW erases trailing whitespace then the preceding word (POSIX VWERASE)."""
    from telnetlib3.client_shell import LinemodeBuffer

    slctab = slc.generate_slctab(slc.BSD_SLC_TAB)
    buf = LinemodeBuffer(slctab=slctab)
    for c in "hello ":
        buf.feed(c)
    # EW (^W = 0x17) on "hello " should erase the trailing space AND "hello"
    echo, data = buf.feed("\x17")
    assert echo == "\b \b" * 6
    assert data is None
    assert buf._buf == []


if sys.platform != "win32":
    import termios

    def test_determine_mode_linemode_edit():
        """determine_mode() keeps cooked mode with kernel echo when LINEMODE EDIT is set."""
        import types

        from telnetlib3.telopt import LINEMODE
        from telnetlib3.client_shell import Terminal
        from telnetlib3._session_context import TelnetSessionContext

        class _Opt:
            def __init__(self, active):
                self._active = active

            def enabled(self, key):
                return key in self._active

        linemode = slc.Linemode(slc.LMODE_MODE_LOCAL)
        ctx = TelnetSessionContext()
        writer = types.SimpleNamespace(
            will_echo=False,
            client=True,
            remote_option=_Opt(set()),
            local_option=_Opt({LINEMODE}),
            linemode=linemode,
            log=types.SimpleNamespace(debug=lambda *a, **kw: None),
            ctx=ctx,
        )
        term = Terminal.__new__(Terminal)
        term.telnet_writer = writer
        term.software_echo = False
        mode = Terminal.ModeDef(
            iflag=termios.BRKINT | termios.ICRNL | termios.IXON,
            oflag=termios.OPOST | termios.ONLCR,
            cflag=termios.CS8 | termios.CREAD,
            lflag=termios.ICANON | termios.ECHO | termios.ISIG | termios.IEXTEN,
            ispeed=termios.B38400,
            ospeed=termios.B38400,
            cc=[b"\x00"] * termios.NCCS,
        )
        result = term.determine_mode(mode)
        assert result.lflag & termios.ICANON  # cooked mode: kernel handles line editing
        assert result.lflag & termios.ECHO  # kernel handles echo
        assert term.software_echo is False
