# std imports
import struct
import asyncio
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
    TM,
    EOR,
    IAC,
    SGA,
    DONT,
    ECHO,
    NAWS,
    SEND,
    WILL,
    WONT,
    LFLOW,
    TTYPE,
    BINARY,
    LOGOUT,
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
    name_command,
)
from telnetlib3.stream_writer import TelnetWriter


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

    # write escapes IAC
    w.write(b"A" + IAC + b"B")
    assert t.writes[-1] == b"A" + IAC + IAC + b"B"

    # send_iac writes verbatim starting with IAC
    w.send_iac(IAC + CMD_EOR)
    assert t.writes[-1] == IAC + CMD_EOR


def test_iac_skip_when_option_already_enabled_remote_and_local():
    w, t, _ = new_writer(server=True)

    # remote option already enabled -> DO should be skipped
    w.remote_option[BINARY] = True
    sent = w.iac(DO, BINARY)
    assert sent is False
    assert not t.writes

    # local option already enabled -> WILL should be skipped
    w.local_option[ECHO] = True
    sent2 = w.iac(WILL, ECHO)
    assert sent2 is False
    assert not t.writes


def test_iac_do_sets_pending_and_writes_when_not_enabled():
    w, t, _ = new_writer(server=True)

    assert w.remote_option.enabled(BINARY) is False
    sent = w.iac(DO, BINARY)
    assert sent is True
    assert (DO + BINARY) in w.pending_option
    assert t.writes[-1] == IAC + DO + BINARY


def test_send_eor_requires_local_option_enabled():
    w, t, _ = new_writer(server=True)

    # not enabled -> returns False, no write
    assert w.send_eor() is False
    assert not t.writes

    # enable and try again
    w.local_option[EOR] = True
    assert w.send_eor() is True
    assert t.writes[-1] == IAC + CMD_EOR


def test_echo_server_only_and_will_echo_controls_write():
    w, t, _ = new_writer(server=True)

    # will_echo depends on local ECHO for server perspective
    w.local_option[ECHO] = True
    w.echo(b"x")
    assert t.writes[-1] == b"x"

    # client perspective: echo should assert
    w2, t2, _ = new_writer(server=False, client=True)
    with pytest.raises(AssertionError):
        w2.echo(b"x")
    assert not t2.writes


def test_mode_property_transitions():
    w, _, _ = new_writer(server=True)

    # default server: local
    assert w.mode == "local"

    # server with ECHO and SGA -> kludge
    w.local_option[ECHO] = True
    w.local_option[SGA] = True
    assert w.mode == "kludge"

    # remote LINEMODE enabled -> remote
    w.remote_option[LINEMODE] = True
    assert w.mode == "remote"


def test_request_status_sends_and_pends():
    w, t, _ = new_writer(server=True)
    w.remote_option[STATUS] = True

    sent = w.request_status()
    assert sent is True
    assert t.writes[-1] == IAC + SB + STATUS + SEND + IAC + SE
    # second request while pending -> False
    sent2 = w.request_status()
    assert sent2 is False


def test_send_status_requires_privilege_then_minimal_frame():
    w, t, _ = new_writer(server=True)

    with pytest.raises(ValueError):
        w._send_status()

    # allow by setting local STATUS True
    w.local_option[STATUS] = True
    w._send_status()
    assert t.writes[-1] == IAC + SB + STATUS + IS + IAC + SE


def test_receive_status_matches_local_and_remote_states():
    w, _, _ = new_writer(server=True)
    # local DO BINARY should match when local_option[BINARY] True
    w.local_option[BINARY] = True
    # remote WILL ECHO should match when remote_option[ECHO] True
    w.remote_option[ECHO] = True
    buf = collections.deque([DO, BINARY, WILL, ECHO])
    # should not raise
    w._receive_status(buf)


def test_request_tspeed_and_handle_send_and_is():
    # request_tspeed from server when remote declared WILL TSPEED
    ws, ts, _ = new_writer(server=True)
    ws.remote_option[TSPEED] = True
    assert ws.request_tspeed() is True
    assert ts.writes[-1] == IAC + SB + TSPEED + SEND + IAC + SE

    # client receives SEND and responds IS rx,tx
    wc, tc, _ = new_writer(server=False, client=True)
    wc.set_ext_send_callback(TSPEED, lambda: (9600, 9600))
    buf = collections.deque([TSPEED, SEND])
    wc._handle_sb_tspeed(buf)
    assert tc.writes[-1] == IAC + SB + TSPEED + IS + b"9600" + b"," + b"9600" + IAC + SE

    # server receives IS values
    seen = {}
    ws2, _, _ = new_writer(server=True)
    ws2.set_ext_callback(TSPEED, lambda rx, tx: seen.setdefault("v", (rx, tx)))
    payload = b"57600,115200"
    # feed payload as individual bytes, matching expected subnegotiation format
    buf2 = collections.deque([TSPEED, IS] + [payload[i : i + 1] for i in range(len(payload))])
    ws2._handle_sb_tspeed(buf2)
    assert seen["v"] == (57600, 115200)


def test_handle_sb_charset_request_accept_reject_and_accepted():
    # REQUEST -> REJECTED
    w, t, _ = new_writer(server=True)
    w.set_ext_send_callback(CHARSET, lambda offers=None: None)
    sep = b" "
    offers = b"UTF-8 ASCII"
    buf = collections.deque([CHARSET, REQUEST, sep, offers])
    w._handle_sb_charset(buf)
    assert t.writes[-1] == IAC + SB + CHARSET + b"\x03" + IAC + SE  # REJECTED = 3

    # REQUEST -> ACCEPTED UTF-8
    w2, t2, _ = new_writer(server=True)
    w2.set_ext_send_callback(CHARSET, lambda offers=None: "UTF-8")
    buf2 = collections.deque([CHARSET, REQUEST, sep, offers])
    w2._handle_sb_charset(buf2)
    assert t2.writes[-1] == IAC + SB + CHARSET + b"\x02" + b"UTF-8" + IAC + SE  # ACCEPTED = 2

    # ACCEPTED -> callback fired
    seen = {}
    w3, _, _ = new_writer(server=True)
    w3.set_ext_callback(CHARSET, lambda cs: seen.setdefault("cs", cs))
    buf3 = collections.deque([CHARSET, b"\x02", b"UTF-8"])  # ACCEPTED
    w3._handle_sb_charset(buf3)
    assert seen["cs"] == "UTF-8"

    # REJECTED path (warning only)
    w4, _, _ = new_writer(server=True)
    buf4 = collections.deque([CHARSET, b"\x03"])  # REJECTED
    w4._handle_sb_charset(buf4)


def test_handle_sb_xdisploc_is_and_send():
    # IS -> server callback
    seen = {}
    ws, _, _ = new_writer(server=True)
    ws.set_ext_callback(XDISPLOC, lambda val: seen.setdefault("x", val))
    buf = collections.deque([XDISPLOC, IS, b"host:0"])
    ws._handle_sb_xdisploc(buf)
    assert seen["x"] == "host:0"

    # SEND -> client response from ext_send_callback
    wc, tc, _ = new_writer(server=False, client=True)
    wc.set_ext_send_callback(XDISPLOC, lambda: "disp:1")
    buf2 = collections.deque([XDISPLOC, SEND])
    wc._handle_sb_xdisploc(buf2)
    assert tc.writes[-1] == IAC + SB + XDISPLOC + IS + b"disp:1" + IAC + SE


def test_handle_sb_ttype_is_and_send():
    # IS -> server callback
    seen = {}
    ws, _, _ = new_writer(server=True)
    ws.set_ext_callback(TTYPE, lambda s: seen.setdefault("t", s))
    buf = collections.deque([TTYPE, IS, b"xterm-256color"])
    ws._handle_sb_ttype(buf)
    assert seen["t"] == "xterm-256color"

    # SEND -> client response
    wc, tc, _ = new_writer(server=False, client=True)
    wc.set_ext_send_callback(TTYPE, lambda: "vt100")
    buf2 = collections.deque([TTYPE, SEND])
    wc._handle_sb_ttype(buf2)
    assert tc.writes[-1] == IAC + SB + TTYPE + IS + b"vt100" + IAC + SE


def _encode_env(env):
    """Helper to encode env dict like _encode_env_buf would, for tests."""
    # local
    from telnetlib3.stream_writer import _encode_env_buf

    return _encode_env_buf(env)


def test_handle_sb_environ_send_and_is():
    # client SEND -> respond with IS encoded from ext_send_callback
    wc, tc, _ = new_writer(server=False, client=True)
    wc.set_ext_send_callback(NEW_ENVIRON, lambda keys: {"USER": "root"})
    # SEND with asking for USER
    send_payload = _encode_env({"USER": ""})
    buf = collections.deque([NEW_ENVIRON, SEND, send_payload])
    wc._handle_sb_environ(buf)
    frame = tc.writes[-1]
    assert frame.startswith(IAC + SB + NEW_ENVIRON + IS)
    assert frame.endswith(IAC + SE)
    assert b"USER" in frame and b"root" in frame

    # server IS -> callback receives dict
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
    # without WILL NEW_ENVIRON -> False
    assert ws.request_environ() is False

    # with WILL NEW_ENVIRON but empty request list from callback -> False
    ws.remote_option[NEW_ENVIRON] = True
    ws.set_ext_send_callback(NEW_ENVIRON, lambda: [])
    assert ws.request_environ() is False

    # non-empty request list -> sends SB NEW_ENVIRON SEND ... SE
    ws.set_ext_send_callback(NEW_ENVIRON, lambda: ["USER", "LANG"])
    assert ws.request_environ() is True
    frame = ts.writes[-1]
    assert frame.startswith(IAC + SB + NEW_ENVIRON + SEND)
    assert frame.endswith(IAC + SE)


def test_request_charset_and_xdisploc_and_ttype():
    ws, ts, _ = new_writer(server=True)
    # charset requires WILL CHARSET
    assert ws.request_charset() is False
    ws.remote_option[CHARSET] = True
    ws.set_ext_send_callback(CHARSET, lambda: ["UTF-8", "ASCII"])
    assert ws.request_charset() is True
    assert ts.writes[-1].startswith(IAC + SB + CHARSET + b"\x01")  # REQUEST = 1

    # xdisploc requires WILL XDISPLOC, then sends and sets pending
    assert ws.request_xdisploc() is False
    ws.remote_option[XDISPLOC] = True
    assert ws.request_xdisploc() is True
    assert ts.writes[-1] == IAC + SB + XDISPLOC + SEND + IAC + SE
    # subsequent call suppressed while pending
    assert ws.request_xdisploc() is False

    # ttype requires WILL TTYPE, then sends and sets pending
    assert ws.request_ttype() is False
    ws.remote_option[TTYPE] = True
    assert ws.request_ttype() is True
    assert ts.writes[-1] == IAC + SB + TTYPE + SEND + IAC + SE
    # subsequent call suppressed while pending
    assert ws.request_ttype() is False


def test_send_lineflow_mode_server_only_and_modes():
    ws, ts, _ = new_writer(server=True)
    # without WILL LFLOW -> error path returns False
    assert ws.send_lineflow_mode() is False

    # client should error-return as well
    wc, _, _ = new_writer(server=False, client=True)
    assert wc.send_lineflow_mode() is False

    # with WILL LFLOW, xon_any False -> RESTART_XON
    ws.remote_option[LFLOW] = True
    ws.xon_any = False
    assert ws.send_lineflow_mode() is True
    assert ts.writes[-1] == IAC + SB + LFLOW + LFLOW_RESTART_XON + IAC + SE

    # xon_any True -> RESTART_ANY
    ws.xon_any = True
    assert ws.send_lineflow_mode() is True
    assert ts.writes[-1] == IAC + SB + LFLOW + LFLOW_RESTART_ANY + IAC + SE


def test_send_ga_respects_sga():
    ws, ts, _ = new_writer(server=True)
    # default: DO SGA not received -> GA allowed
    assert ws.send_ga() is True
    assert ts.writes[-1] == IAC + b"\xf9"  # GA

    # after DO SGA (local_option[SGA] True), GA suppressed
    ws.local_option[SGA] = True
    assert ws.send_ga() is False


def test_send_naws_and_handle_naws():
    # client path for sending NAWS
    wc, tc, _ = new_writer(server=False, client=True)
    wc.set_ext_send_callback(NAWS, lambda: (24, 80))  # rows, cols
    wc._send_naws()
    frame = tc.writes[-1]
    assert frame.startswith(IAC + SB + NAWS)
    assert frame.endswith(IAC + SE)
    # payload is packed (cols, rows)
    payload = frame[3:-2]
    data = payload.replace(IAC + IAC, IAC)
    assert len(data) == 4
    cols, rows = struct.unpack("!HH", data)
    assert (rows, cols) == (24, 80)

    # server receive NAWS -> callback(rows, cols)
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
    # must have DO LFLOW received
    ws.local_option[LFLOW] = True

    # OFF
    buf = collections.deque([LFLOW, LFLOW_OFF])
    ws._handle_sb_lflow(buf)
    assert ws.lflow is False

    # ON
    buf = collections.deque([LFLOW, LFLOW_ON])
    ws._handle_sb_lflow(buf)
    assert ws.lflow is True

    # RESTART_ANY -> xon_any False
    buf = collections.deque([LFLOW, LFLOW_RESTART_ANY])
    ws._handle_sb_lflow(buf)
    assert ws.xon_any is False

    # RESTART_XON -> xon_any True
    buf = collections.deque([LFLOW, LFLOW_RESTART_XON])
    ws._handle_sb_lflow(buf)
    assert ws.xon_any is True


def test_handle_sb_status_send_and_is():
    ws, ts, _ = new_writer(server=True)
    # prepare privilege for _send_status
    ws.local_option[STATUS] = True

    # SEND -> calls _send_status writes minimal frame
    buf = collections.deque([STATUS, SEND])
    ws._handle_sb_status(buf)
    assert ts.writes[-1] == IAC + SB + STATUS + IS + IAC + SE

    # IS -> pass a matching pair DO/WILL to _receive_status
    ws2, _, _ = new_writer(server=True)
    ws2.local_option[BINARY] = True
    ws2.remote_option[SGA] = True
    payload = collections.deque([DO, BINARY, WILL, SGA])
    buf2 = collections.deque([STATUS, IS] + list(payload))
    ws2._handle_sb_status(buf2)


def test_handle_sb_forwardmask_assertions_and_do_raises_notimplemented():
    # client end receiving DO FORWARDMASK must have WILL LINEMODE True
    wc, _, _ = new_writer(server=False, client=True)
    wc.local_option[LINEMODE] = True
    # DO with some bytes must call _handle_do_forwardmask -> NotImplementedError
    with pytest.raises(AssertionError):
        wc._handle_sb_forwardmask(DO, collections.deque([b"x", b"y"]))


def test_handle_sb_linemode_switches():
    ws, ts, _ = new_writer(server=True)

    # LMODE_MODE without ACK -> triggers send_linemode (ACK set)
    ws.local_option[LINEMODE] = True  # allow send_linemode assertion
    ws.remote_option[LINEMODE] = True
    ws._handle_sb_linemode_mode(collections.deque([bytes([0])]))  # suggest 0 mask
    # send_linemode writes two frames (SB LINEMODE LMODE_MODE ... SE)
    assert ts.writes[-1].endswith(IAC + SE)

    # Client: ACK set and mode differs -> ignore change (no write, local unchanged)
    wc, tc, _ = new_writer(server=False, client=True)
    wc._linemode = slc.Linemode(bytes([0]))  # local
    suggest_ack = bytes([ord(bytes([1])) | ord(slc.LMODE_MODE_ACK)])
    wc._handle_sb_linemode_mode(collections.deque([suggest_ack]))
    # nothing written
    assert not tc.writes

    # Client: ACK set and mode matches -> set and no write
    wc2, tc2, _ = new_writer(server=False, client=True)
    same = slc.Linemode(bytes([1]))
    wc2._linemode = same
    suggest_ack2 = bytes([ord(same.mask) | ord(slc.LMODE_MODE_ACK)])
    wc2._handle_sb_linemode_mode(collections.deque([suggest_ack2]))
    assert wc2._linemode == same
    assert not tc2.writes


def test_handle_subnegotiation_dispatch_and_unhandled():
    ws, _, _ = new_writer(server=True)
    # dispatch to NAWS handler (will log unsolicited), ensure no exception
    # must reflect receipt of WILL NAWS prior to NAWS subnegotiation
    ws.remote_option[NAWS] = True
    payload = struct.pack("!HH", 10, 20)
    buf = collections.deque([NAWS, payload[0:1], payload[1:2], payload[2:3], payload[3:4]])
    ws._handle_sb_naws(buf)

    # unhandled command
