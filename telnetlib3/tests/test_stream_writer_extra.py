# std imports
import struct
import logging
import collections

# 3rd party
import pytest

# local
from telnetlib3 import slc
from telnetlib3.telopt import (
    DO,
    IS,
    SB,
    SE,
    EOR,
    IAC,
    SGA,
    ECHO,
    NAWS,
    SEND,
    WILL,
    WONT,
    LFLOW,
    TTYPE,
    BINARY,
    SNDLOC,
    STATUS,
    TSPEED,
    CHARSET,
    CMD_EOR,
    REQUEST,
    LFLOW_ON,
    LINEMODE,
    XDISPLOC,
    LFLOW_OFF,
    NEW_ENVIRON,
    LFLOW_RESTART_ANY,
    LFLOW_RESTART_XON,
)
from telnetlib3.client_base import BaseClient
from telnetlib3.server_base import BaseServer
from telnetlib3.stream_writer import TelnetWriter, _encode_env_buf, _format_sb_status


class MockTransport:
    def __init__(self):
        self._closing = False
        self.writes = []
        self.extra = {}

    def write(self, data):
        self.writes.append(bytes(data))

    def is_closing(self):
        return self._closing

    def get_extra_info(self, name, default=None):
        return self.extra.get(name, default)

    def pause_reading(self):
        pass

    def resume_reading(self):
        pass

    def close(self):
        self._closing = True


class MockProtocol:
    def __init__(self, info=None):
        self.info = info or {}

    def get_extra_info(self, name, default=None):
        return self.info.get(name, default)

    async def _drain_helper(self):
        pass


def new_writer(server=True, client=False):
    t = MockTransport()
    p = MockProtocol()
    w = TelnetWriter(t, p, server=server, client=client)
    return w, t, p


def test_write_escapes_iac_and_send_iac_verbatim():
    w, t, _ = new_writer(server=True)
    w.write(b"A" + IAC + b"B")
    assert t.writes[-1] == b"A" + IAC + IAC + b"B"
    w.send_iac(IAC + CMD_EOR)
    assert t.writes[-1] == IAC + CMD_EOR


def test_iac_skip_when_option_already_enabled_remote_and_local():
    w, t, _ = new_writer(server=True)
    w.remote_option[BINARY] = True
    assert w.iac(DO, BINARY) is False
    assert not t.writes

    w.local_option[ECHO] = True
    assert w.iac(WILL, ECHO) is False
    assert not t.writes


def test_iac_do_sets_pending_and_writes_when_not_enabled():
    w, t, _ = new_writer(server=True)
    assert w.remote_option.enabled(BINARY) is False
    assert w.iac(DO, BINARY) is True
    assert DO + BINARY in w.pending_option
    assert t.writes[-1] == IAC + DO + BINARY


def test_send_eor_requires_local_option_enabled():
    w, t, _ = new_writer(server=True)
    assert w.send_eor() is False
    assert not t.writes

    w.local_option[EOR] = True
    assert w.send_eor() is True
    assert t.writes[-1] == IAC + CMD_EOR


def test_echo_server_only_and_will_echo_controls_write():
    w, t, _ = new_writer(server=True)
    w.local_option[ECHO] = True
    w.echo(b"x")
    assert t.writes[-1] == b"x"

    w2, t2, _ = new_writer(server=False, client=True)
    w2.echo(b"x")
    assert not t2.writes


def test_mode_property_transitions():
    w, _, _ = new_writer(server=True)
    assert w.mode == "local"

    w.local_option[ECHO] = True
    w.local_option[SGA] = True
    assert w.mode == "kludge"

    w.remote_option[LINEMODE] = True
    assert w.mode == "remote"


def test_request_status_sends_and_pends():
    w, t, _ = new_writer(server=True)
    w.remote_option[STATUS] = True
    assert w.request_status() is True
    assert t.writes[-1] == IAC + SB + STATUS + SEND + IAC + SE
    assert w.request_status() is False


def test_send_status_requires_privilege_then_minimal_frame():
    w, t, _ = new_writer(server=True)
    with pytest.raises(ValueError):
        w._send_status()

    w.local_option[STATUS] = True
    w._send_status()
    assert t.writes[-1] == IAC + SB + STATUS + IS + IAC + SE


def test_receive_status_matches_local_and_remote_states():
    w, _, _ = new_writer(server=True)
    w.local_option[BINARY] = True
    w.remote_option[ECHO] = True
    buf = collections.deque([DO, BINARY, WILL, ECHO])
    w._receive_status(buf)


def test_request_tspeed_and_handle_send_and_is():
    ws, ts, _ = new_writer(server=True)
    ws.remote_option[TSPEED] = True
    assert ws.request_tspeed() is True
    assert ts.writes[-1] == IAC + SB + TSPEED + SEND + IAC + SE

    wc, tc, _ = new_writer(server=False, client=True)
    wc.set_ext_send_callback(TSPEED, lambda: (9600, 9600))
    buf = collections.deque([TSPEED, SEND])
    wc._handle_sb_tspeed(buf)
    assert tc.writes[-1] == IAC + SB + TSPEED + IS + b"9600" + b"," + b"9600" + IAC + SE

    seen = {}
    ws2, _, _ = new_writer(server=True)
    ws2.set_ext_callback(TSPEED, lambda rx, tx: seen.setdefault("v", (rx, tx)))
    payload = b"57600,115200"
    buf2 = collections.deque([TSPEED, IS] + [payload[i : i + 1] for i in range(len(payload))])
    ws2._handle_sb_tspeed(buf2)
    assert seen["v"] == (57600, 115200)


def test_handle_sb_charset_request_accept_reject_and_accepted():
    w, t, _ = new_writer(server=True)
    w.set_ext_send_callback(CHARSET, lambda offers=None: None)
    sep = b" "
    offers = b"UTF-8 ASCII"
    buf = collections.deque([CHARSET, REQUEST, sep, offers])
    w._handle_sb_charset(buf)
    assert t.writes[-1] == IAC + SB + CHARSET + b"\x03" + IAC + SE

    w2, t2, _ = new_writer(server=True)
    w2.set_ext_send_callback(CHARSET, lambda offers=None: "UTF-8")
    buf2 = collections.deque([CHARSET, REQUEST, sep, offers])
    w2._handle_sb_charset(buf2)
    assert t2.writes[-1] == IAC + SB + CHARSET + b"\x02" + b"UTF-8" + IAC + SE

    seen = {}
    w3, _, _ = new_writer(server=True)
    w3.set_ext_callback(CHARSET, lambda cs: seen.setdefault("cs", cs))
    buf3 = collections.deque([CHARSET, b"\x02", b"UTF-8"])
    w3._handle_sb_charset(buf3)
    assert seen["cs"] == "UTF-8"

    w4, _, _ = new_writer(server=True)
    buf4 = collections.deque([CHARSET, b"\x03"])
    w4._handle_sb_charset(buf4)


def test_handle_sb_xdisploc_is_and_send():
    seen = {}
    ws, _, _ = new_writer(server=True)
    ws.set_ext_callback(XDISPLOC, lambda val: seen.setdefault("x", val))
    buf = collections.deque([XDISPLOC, IS, b"host:0"])
    ws._handle_sb_xdisploc(buf)
    assert seen["x"] == "host:0"

    wc, tc, _ = new_writer(server=False, client=True)
    wc.set_ext_send_callback(XDISPLOC, lambda: "disp:1")
    buf2 = collections.deque([XDISPLOC, SEND])
    wc._handle_sb_xdisploc(buf2)
    assert tc.writes[-1] == IAC + SB + XDISPLOC + IS + b"disp:1" + IAC + SE


def test_handle_sb_ttype_is_and_send():
    seen = {}
    ws, _, _ = new_writer(server=True)
    ws.set_ext_callback(TTYPE, lambda s: seen.setdefault("t", s))
    buf = collections.deque([TTYPE, IS, b"xterm-256color"])
    ws._handle_sb_ttype(buf)
    assert seen["t"] == "xterm-256color"

    wc, tc, _ = new_writer(server=False, client=True)
    wc.set_ext_send_callback(TTYPE, lambda: "vt100")
    buf2 = collections.deque([TTYPE, SEND])
    wc._handle_sb_ttype(buf2)
    assert tc.writes[-1] == IAC + SB + TTYPE + IS + b"vt100" + IAC + SE


def _encode_env(env):
    return _encode_env_buf(env)


def test_handle_sb_environ_send_and_is():
    wc, tc, _ = new_writer(server=False, client=True)
    wc.set_ext_send_callback(NEW_ENVIRON, lambda keys: {"USER": "root"})
    send_payload = _encode_env({"USER": ""})
    buf = collections.deque([NEW_ENVIRON, SEND, send_payload])
    wc._handle_sb_environ(buf)
    frame = tc.writes[-1]
    assert frame.startswith(IAC + SB + NEW_ENVIRON + IS)
    assert frame.endswith(IAC + SE)
    assert b"USER" in frame and b"root" in frame

    seen = {}
    ws, _, _ = new_writer(server=True)
    ws.set_ext_callback(NEW_ENVIRON, lambda env: seen.setdefault("env", env))
    is_payload = _encode_env({"TERM": "xterm", "LANG": "C"})
    buf2 = collections.deque([NEW_ENVIRON, IS, is_payload])
    ws._handle_sb_environ(buf2)
    assert seen["env"]["TERM"] == "xterm"
    assert seen["env"]["LANG"] == "C"


def test_request_environ_server_side_conditions():
    ws, ts, _ = new_writer(server=True)
    assert ws.request_environ() is False

    ws.remote_option[NEW_ENVIRON] = True
    ws.set_ext_send_callback(NEW_ENVIRON, lambda: [])
    assert ws.request_environ() is False

    ws.set_ext_send_callback(NEW_ENVIRON, lambda: ["USER", "LANG"])
    assert ws.request_environ() is True
    frame = ts.writes[-1]
    assert frame.startswith(IAC + SB + NEW_ENVIRON + SEND)
    assert frame.endswith(IAC + SE)


def test_request_charset_and_xdisploc_and_ttype():
    ws, ts, _ = new_writer(server=True)
    assert ws.request_charset() is False
    ws.remote_option[CHARSET] = True
    ws.set_ext_send_callback(CHARSET, lambda: ["UTF-8", "ASCII"])
    assert ws.request_charset() is True
    assert ts.writes[-1].startswith(IAC + SB + CHARSET + b"\x01")

    assert ws.request_xdisploc() is False
    ws.remote_option[XDISPLOC] = True
    assert ws.request_xdisploc() is True
    assert ts.writes[-1] == IAC + SB + XDISPLOC + SEND + IAC + SE
    assert ws.request_xdisploc() is False

    assert ws.request_ttype() is False
    ws.remote_option[TTYPE] = True
    assert ws.request_ttype() is True
    assert ts.writes[-1] == IAC + SB + TTYPE + SEND + IAC + SE
    assert ws.request_ttype() is False


def test_send_lineflow_mode_server_only_and_modes():
    ws, ts, _ = new_writer(server=True)
    assert ws.send_lineflow_mode() is False

    wc, _, _ = new_writer(server=False, client=True)
    assert wc.send_lineflow_mode() is False

    ws.remote_option[LFLOW] = True
    ws.xon_any = False
    assert ws.send_lineflow_mode() is True
    assert ts.writes[-1] == IAC + SB + LFLOW + LFLOW_RESTART_XON + IAC + SE

    ws.xon_any = True
    assert ws.send_lineflow_mode() is True
    assert ts.writes[-1] == IAC + SB + LFLOW + LFLOW_RESTART_ANY + IAC + SE


def test_send_ga_respects_sga():
    ws, ts, _ = new_writer(server=True)
    assert ws.send_ga() is True
    assert ts.writes[-1] == IAC + b"\xf9"

    ws.local_option[SGA] = True
    assert ws.send_ga() is False


def test_send_naws_and_handle_naws():
    wc, tc, _ = new_writer(server=False, client=True)
    wc.set_ext_send_callback(NAWS, lambda: (24, 80))
    wc._send_naws()
    frame = tc.writes[-1]
    assert frame.startswith(IAC + SB + NAWS)
    assert frame.endswith(IAC + SE)
    payload = frame[3:-2]
    data = payload.replace(IAC + IAC, IAC)
    assert len(data) == 4
    cols, rows = struct.unpack("!HH", data)
    assert (rows, cols) == (24, 80)

    seen = {}
    ws, _, _ = new_writer(server=True)
    ws.remote_option[NAWS] = True
    ws.set_ext_callback(NAWS, lambda r, c: seen.setdefault("sz", (r, c)))
    payload2 = struct.pack("!HH", 100, 200)
    buf2 = collections.deque([NAWS, payload2[0:1], payload2[1:2], payload2[2:3], payload2[3:4]])
    ws._handle_sb_naws(buf2)
    assert seen["sz"] == (200, 100)


def test_handle_sb_lflow_toggles():
    ws, _, _ = new_writer(server=True)
    ws.local_option[LFLOW] = True

    buf = collections.deque([LFLOW, LFLOW_OFF])
    ws._handle_sb_lflow(buf)
    assert ws.lflow is False

    buf = collections.deque([LFLOW, LFLOW_ON])
    ws._handle_sb_lflow(buf)
    assert ws.lflow is True

    buf = collections.deque([LFLOW, LFLOW_RESTART_ANY])
    ws._handle_sb_lflow(buf)
    assert ws.xon_any is False

    buf = collections.deque([LFLOW, LFLOW_RESTART_XON])
    ws._handle_sb_lflow(buf)
    assert ws.xon_any is True


def test_handle_sb_status_send_and_is():
    ws, ts, _ = new_writer(server=True)
    ws.local_option[STATUS] = True

    buf = collections.deque([STATUS, SEND])
    ws._handle_sb_status(buf)
    assert ts.writes[-1] == IAC + SB + STATUS + IS + IAC + SE

    ws2, _, _ = new_writer(server=True)
    ws2.local_option[BINARY] = True
    ws2.remote_option[SGA] = True
    payload = collections.deque([DO, BINARY, WILL, SGA])
    buf2 = collections.deque([STATUS, IS] + list(payload))
    ws2._handle_sb_status(buf2)


def test_handle_sb_forwardmask_do_accepted():
    wc, _, _ = new_writer(server=False, client=True)
    wc.local_option[LINEMODE] = True
    wc._handle_sb_forwardmask(DO, collections.deque([b"x", b"y"]))
    opt = SB + LINEMODE + slc.LMODE_FORWARDMASK
    assert wc.local_option[opt] is True


def test_handle_sb_linemode_mode_empty_buffer():
    ws, _, _ = new_writer(server=True)
    ws.local_option[LINEMODE] = True
    ws.remote_option[LINEMODE] = True
    with pytest.raises(ValueError, match="missing mode byte"):
        ws._handle_sb_linemode_mode(collections.deque())


def test_handle_sb_linemode_switches():
    ws, ts, _ = new_writer(server=True)
    ws.local_option[LINEMODE] = True
    ws.remote_option[LINEMODE] = True
    ws._handle_sb_linemode_mode(collections.deque([bytes([3])]))
    assert ts.writes[-1].endswith(IAC + SE)

    wc, tc, _ = new_writer(server=False, client=True)
    wc._linemode = slc.Linemode(bytes([0]))
    suggest_ack = bytes([ord(bytes([1])) | ord(slc.LMODE_MODE_ACK)])
    wc._handle_sb_linemode_mode(collections.deque([suggest_ack]))
    assert not tc.writes

    wc2, tc2, _ = new_writer(server=False, client=True)
    same = slc.Linemode(bytes([1]))
    wc2._linemode = same
    suggest_ack2 = bytes([ord(same.mask) | ord(slc.LMODE_MODE_ACK)])
    wc2._handle_sb_linemode_mode(collections.deque([suggest_ack2]))
    assert wc2._linemode == same
    assert not tc2.writes


def test_handle_sb_linemode_suppresses_duplicate_mode():
    """Redundant MODE without ACK matching current mode is not re-ACKed."""
    ws, ts, _ = new_writer(server=True)
    ws.local_option[LINEMODE] = True
    ws.remote_option[LINEMODE] = True

    mode_val = bytes([3])
    mode_with_ack = bytes([3 | 4])

    ws._handle_sb_linemode_mode(collections.deque([mode_val]))
    assert len(ts.writes) > 0
    first_write_count = len(ts.writes)
    assert ws._linemode.mask == mode_with_ack

    ws._handle_sb_linemode_mode(collections.deque([mode_val]))
    assert len(ts.writes) == first_write_count

    ws._handle_sb_linemode_mode(collections.deque([bytes([1])]))
    assert len(ts.writes) > first_write_count


def test_handle_sb_linemode_suppresses_duplicate_mode_client():
    """Client also suppresses redundant MODE proposals."""
    wc, tc, _ = new_writer(server=False, client=True)
    wc.local_option[LINEMODE] = True
    wc.remote_option[LINEMODE] = True

    mode_val = bytes([3])
    mode_with_ack = bytes([3 | 4])

    wc._handle_sb_linemode_mode(collections.deque([mode_val]))
    first_write_count = len(tc.writes)
    assert first_write_count > 0
    assert wc._linemode.mask == mode_with_ack

    for _ in range(3):
        wc._handle_sb_linemode_mode(collections.deque([mode_val]))
    assert len(tc.writes) == first_write_count


def test_handle_subnegotiation_dispatch_and_unhandled():
    ws, _, _ = new_writer(server=True)
    ws.remote_option[NAWS] = True
    payload = struct.pack("!HH", 10, 20)
    buf = collections.deque([NAWS, payload[0:1], payload[1:2], payload[2:3], payload[3:4]])
    ws._handle_sb_naws(buf)

    with pytest.raises(ValueError, match="SB unhandled"):
        ws.handle_subnegotiation(collections.deque([b"\x99", b"\x00"]))


async def test_server_data_received_split_sb_linemode():
    class NoNegServer(BaseServer):
        def begin_negotiation(self):
            pass

        def _check_negotiation_timer(self):
            pass

    transport = MockTransport()
    server = NoNegServer(encoding=False)
    server.connection_made(transport)

    server.writer.remote_option[LINEMODE] = True
    server.writer.local_option[LINEMODE] = True

    transport.writes.clear()

    chunk1 = IAC + SB + LINEMODE + slc.LMODE_MODE
    server.data_received(chunk1)
    assert server.writer.is_oob

    mask_byte = b"\x10"
    chunk2 = mask_byte + IAC + SE
    server.data_received(chunk2)

    response = b"".join(transport.writes)
    assert IAC + SB + LINEMODE + slc.LMODE_MODE in response


async def test_client_process_chunk_split_sb_linemode():
    transport = MockTransport()
    client = BaseClient(encoding=False)
    client.connection_made(transport)

    client.writer.remote_option[LINEMODE] = True
    client.writer.local_option[LINEMODE] = True

    transport.writes.clear()

    chunk1 = IAC + SB + LINEMODE + slc.LMODE_MODE
    client._process_chunk(chunk1)
    assert client.writer.is_oob

    mask_byte = b"\x10"
    chunk2 = mask_byte + IAC + SE
    client._process_chunk(chunk2)

    response = b"".join(transport.writes)
    assert IAC + SB + LINEMODE + slc.LMODE_MODE in response


@pytest.mark.parametrize(
    "opt, data, expected",
    [
        (NAWS, b"\x00\x50\x00\x19", "NAWS 80x25"),
        (NAWS, b"\x01\x00\x00\xc8", "NAWS 256x200"),
        (TTYPE, IS + b"VT100", "TTYPE IS VT100"),
        (TTYPE, SEND + b"xterm", "TTYPE SEND xterm"),
        (XDISPLOC, IS + b"host:0.0", "XDISPLOC IS host:0.0"),
        (SNDLOC, IS + b"Building4", "SNDLOC IS Building4"),
        (TTYPE, b"\x99" + b"data", "TTYPE 99 data"),
        (STATUS, b"\xab\xcd", "STATUS abcd"),
        (NAWS, b"\x00\x50\x00", "NAWS 005000"),
        (STATUS, b"", "STATUS"),
        (BINARY, b"", "BINARY"),
    ],
)
def test_format_sb_status(opt, data, expected):
    """Test _format_sb_status output for each branch."""
    assert _format_sb_status(opt, data) == expected


def _make_status_is_buf(*parts):
    """Build a deque for _handle_sb_status from raw byte sequences."""
    buf = collections.deque()
    buf.append(STATUS)
    buf.append(IS)
    for part in parts:
        for byte_val in part:
            buf.append(bytes([byte_val]))
    return buf


def test_receive_status_sb_naws(caplog):
    """STATUS IS with embedded SB NAWS data SE."""
    ws, _, _ = new_writer(server=True)
    ws.local_option[NAWS] = True
    naws_payload = struct.pack("!HH", 80, 25)
    buf = _make_status_is_buf(SB + NAWS + naws_payload + SE)
    with caplog.at_level(logging.DEBUG):
        ws._handle_sb_status(buf)
    assert any("NAWS 80x25" in msg for msg in caplog.messages)


def test_receive_status_sb_missing_se(caplog):
    """STATUS IS with SB block missing SE consumes rest of buffer."""
    ws, _, _ = new_writer(server=True)
    naws_payload = struct.pack("!HH", 80, 25)
    buf = _make_status_is_buf(SB + NAWS + naws_payload)
    with caplog.at_level(logging.DEBUG):
        ws._handle_sb_status(buf)
    assert any("subneg" in msg for msg in caplog.messages)


def test_receive_status_mixed_do_will_and_sb(caplog):
    """STATUS IS with DO/WILL pairs intermixed with SB blocks."""
    ws, _, _ = new_writer(server=True)
    ws.local_option[BINARY] = True
    ws.remote_option[SGA] = True
    ws.remote_option[ECHO] = True
    ws.local_option[NAWS] = True
    naws_payload = struct.pack("!HH", 132, 43)
    buf = _make_status_is_buf(
        DO + BINARY + WILL + SGA + SB + NAWS + naws_payload + SE + WONT + ECHO
    )
    with caplog.at_level(logging.DEBUG):
        ws._handle_sb_status(buf)
    assert any("agreed" in msg.lower() for msg in caplog.messages)
    assert any("NAWS 132x43" in msg for msg in caplog.messages)
    assert any("disagree" in msg.lower() for msg in caplog.messages)
