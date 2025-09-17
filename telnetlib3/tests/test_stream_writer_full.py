# std imports
import asyncio
import collections
import struct

# 3rd party
import pytest

# local
from telnetlib3.stream_writer import (
    TelnetWriter,
    TelnetWriterUnicode,
    Option,
    _escape_environ,
    _unescape_environ,
    _encode_env_buf,
    _decode_env_buf,
)
from telnetlib3 import slc
from telnetlib3.telopt import (
    IAC,
    SB,
    SE,
    IS,
    SEND,
    REQUEST,
    ACCEPTED,
    REJECTED,
    DO,
    DONT,
    WILL,
    WONT,
    GA,
    NOP,
    DM,
    ECHO,
    SGA,
    BINARY,
    LINEMODE,
    LFLOW,
    LFLOW_OFF,
    LFLOW_ON,
    STATUS,
    TTYPE,
    TSPEED,
    XDISPLOC,
    SNDLOC,
    NEW_ENVIRON,
    INFO,
    CHARSET,
    NAWS,
    GMCP,
    COM_PORT_OPTION,
    LOGOUT,
    TM,
    TTABLE_IS,
    theNULL,
)


class MockTransport:
    def __init__(self):
        self._closing = False
        self.writes = []
        self.extra = {}

    def write(self, data):
        # store a copy
        self.writes.append(bytes(data))

    def is_closing(self):
        return self._closing

    def get_extra_info(self, name, default=None):
        return self.extra.get(name, default)

    def close(self):
        self._closing = True


class ProtocolBase:
    def __init__(self, info=None):
        self.info = info or {}
        self.drain_called = False
        self.conn_lost_called = False

    def get_extra_info(self, name, default=None):
        return self.info.get(name, default)

    async def _drain_helper(self):
        self.drain_called = True

    # optional
    def connection_lost(self, exc):
        self.conn_lost_called = True


def new_writer(server=True, client=False, reader=None):
    t = MockTransport()
    p = ProtocolBase()
    w = TelnetWriter(t, p, server=server, client=client, reader=reader)
    return w, t, p


def test_close_idempotent_and_cleanup():
    w, t, p = new_writer(server=True)
    # before
    assert not w.connection_closed
    w.close()
    # transport closed and refs cleared
    assert w.connection_closed is True
    assert w._transport is None
    assert w._protocol is None
    assert t._closing is True
    assert w._closed_fut.done()
    # callbacks cleared
    assert w._ext_callback == {}
    assert w._ext_send_callback == {}
    assert w._slc_callback == {}
    assert w._iac_callback == {}
    # connection_lost was invoked
    assert p.conn_lost_called is True
    # idempotent
    w.close()  # should not raise
    # write after close is ignored
    t2 = MockTransport()
    p2 = ProtocolBase()
    w2 = TelnetWriter(t2, p2, server=True)
    w2.close()
    w2.write(b"ignored")
    assert t2.writes == []


def test_get_extra_info_merges_protocol_and_transport():
    w, t, p = new_writer(server=True)
    p.info["proto_key"] = "P"
    t.extra["trans_key"] = "T"
    assert w.get_extra_info("proto_key") == "P"
    assert w.get_extra_info("trans_key") == "T"
    assert w.get_extra_info("missing", 3) == 3


@pytest.mark.asyncio
async def test_drain_raises_reader_exception():
    class BadReader:
        def exception(self):
            return RuntimeError("boom")

    w, t, p = new_writer(server=True, reader=BadReader())
    with pytest.raises(RuntimeError, match="boom"):
        await w.drain()


@pytest.mark.asyncio
async def test_drain_waits_on_transport_closing_and_calls_drain_helper():
    w, t, p = new_writer(server=True)
    # simulate closing transport
    t._closing = True
    await w.drain()
    assert p.drain_called is True


def test_request_forwardmask_writes_mask_between_frames():
    # server with remote WILL LINEMODE
    w, t, _ = new_writer(server=True)
    w.remote_option[LINEMODE] = True
    sent = w.request_forwardmask()
    assert sent is True
    # should have 3 writes: header, mask, footer
    assert len(t.writes) >= 3
    assert t.writes[-3] == IAC + SB + LINEMODE + DO + slc.LMODE_FORWARDMASK
    # outbinary defaults False -> 16-byte mask
    assert len(t.writes[-2]) in (16, 32)
    assert t.writes[-1] == IAC + SE


def test_send_linemode_asserts_when_not_negotiated():
    w, t, _ = new_writer(server=True)
    with pytest.raises(AssertionError):
        w.send_linemode()


def test_set_callback_validations():
    w, *_ = new_writer(server=True)
    # invalid IAC cmd
    with pytest.raises(AssertionError):
        w.set_iac_callback(cmd=b"\x00", func=lambda c: None)
    # invalid SLC byte
    with pytest.raises(AssertionError):
        w.set_slc_callback(slc_byte=b"\x00", func=lambda c: None)
    # invalid ext send callback
    with pytest.raises(AssertionError):
        w.set_ext_send_callback(cmd=GA, func=lambda: None)
    # invalid ext callback
    with pytest.raises(AssertionError):
        w.set_ext_callback(cmd=GA, func=lambda: None)


def test_handle_logout_paths():
    # server DO -> close
    ws, ts, _ = new_writer(server=True)
    ws.handle_logout(DO)
    assert ts._closing is True
    # server DONT -> no write, no crash
    ws2, ts2, _ = new_writer(server=True)
    ws2.handle_logout(DONT)
    assert ts2.writes == []
    # client WILL -> send DONT LOGOUT
    wc, tc, _ = new_writer(server=False, client=True)
    wc.handle_logout(WILL)
    assert tc.writes[-1] == IAC + DONT + LOGOUT
    # client WONT -> just logs
    wc2, tc2, _ = new_writer(server=False, client=True)
    wc2.handle_logout(WONT)
    assert tc2.writes == []


def test_handle_do_variants_and_tm_and_logout():
    # server receiving forbidden DO -> ValueError
    ws, *_ = new_writer(server=True)
    with pytest.raises(ValueError, match="cannot recv DO LINEMODE"):
        ws.handle_do(LINEMODE)
    # client receiving DO LOGOUT -> ValueError
    wc, *_ = new_writer(server=False, client=True)
    with pytest.raises(ValueError, match="cannot recv DO LOGOUT"):
        wc.handle_do(LOGOUT)
    # client DO ECHO triggers WONT ECHO
    wc2, tc2, _ = new_writer(server=False, client=True)
    wc2.handle_do(ECHO)
    assert tc2.writes[-1] == IAC + WONT + ECHO
    # TM special: sends WILL TM and calls TM callback with DO
    called = {}
    wtm, ttm, _ = new_writer(server=True)
    wtm.set_iac_callback(TM, lambda cmd: called.setdefault("cmd", cmd))
    wtm.handle_do(TM)
    assert ttm.writes[-1] == IAC + WILL + TM
    assert called["cmd"] == DO
    # DO LOGOUT -> ext callback invoked
    seen = {}
    ws2, *_ = new_writer(server=True)
    ws2.set_ext_callback(LOGOUT, lambda cmd: seen.setdefault("v", cmd))
    ws2.handle_do(LOGOUT)
    assert seen["v"] == DO


def test_handle_dont_logout_calls_callback_on_server():
    seen = {}
    w, *_ = new_writer(server=True)
    w.set_ext_callback(LOGOUT, lambda cmd: seen.setdefault("v", cmd))
    w.handle_dont(LOGOUT)
    assert seen["v"] == DONT


def test_handle_will_invalid_cases_and_else_unhandled():
    # server WILL ECHO invalid
    ws, *_ = new_writer(server=True)
    with pytest.raises(ValueError, match="cannot recv WILL ECHO"):
        ws.handle_will(ECHO)
    # client WILL NAWS invalid
    wc, *_ = new_writer(server=False, client=True)
    with pytest.raises(ValueError, match="cannot recv WILL NAWS on client end"):
        wc.handle_will(NAWS)
    # WILL TM requires pending DO TM
    wtm, *_ = new_writer(server=True)
    with pytest.raises(ValueError, match="cannot recv WILL TM"):
        wtm.handle_will(TM)
    # server receiving WILL LOGOUT -> ext callback
    seen = {}
    w3, *_ = new_writer(server=True)
    w3.set_ext_callback(LOGOUT, lambda cmd: seen.setdefault("v", cmd))
    w3.handle_will(LOGOUT)
    assert seen["v"] == WILL
    # ELSE branch (unhandled) -> DONT sent, options set -1, pending cleared
    w4, t4, _ = new_writer(server=True)
    w4.pending_option[DO + GMCP] = True
    w4.handle_will(GMCP)
    assert t4.writes[-1] == IAC + DONT + GMCP
    assert w4.remote_option[GMCP] == -1
    assert w4.local_option[GMCP] == -1
    assert not w4.pending_option.get(DO + GMCP, False)


def test_handle_wont_tm_and_logout_paths():
    # WONT TM w/o pending DO TM -> error
    w, *_ = new_writer(server=True)
    with pytest.raises(ValueError, match="WONT TM"):
        w.handle_wont(TM)
    # with pending DO TM -> toggles False
    w2, *_ = new_writer(server=True)
    w2.pending_option[DO + TM] = True
    w2.handle_wont(TM)
    assert w2.remote_option[TM] is False
    # client WONT LOGOUT -> ext callback
    seen = {}
    wc, *_ = new_writer(server=False, client=True)
    wc.set_ext_callback(LOGOUT, lambda cmd: seen.setdefault("v", cmd))
    wc.handle_wont(LOGOUT)
    assert seen["v"] == WONT


def test_handle_subnegotiation_comport_and_gmcp_and_errors():
    w, *_ = new_writer(server=True)
    # GMCP
    w.handle_subnegotiation(collections.deque([GMCP, b"a", b"b"]))
    # COM PORT OPTION
    w.handle_subnegotiation(collections.deque([COM_PORT_OPTION, b"x", b"y"]))
    # errors
    with pytest.raises(ValueError, match="SE: buffer empty"):
        w.handle_subnegotiation(collections.deque([]))
    with pytest.raises(ValueError, match="SE: buffer is NUL"):
        w.handle_subnegotiation(collections.deque([theNULL, b"x"]))
    with pytest.raises(ValueError, match="SE: buffer too short"):
        w.handle_subnegotiation(collections.deque([NAWS]))
    # unknown command raises
    unknown = bytes([0x7F])
    with pytest.raises(ValueError, match="SB unhandled"):
        w.handle_subnegotiation(collections.deque([unknown, b"x"]))


def test_handle_sb_charset_paths_and_notimpl_and_illegal():
    # REQUEST -> REJECTED
    w, t, _ = new_writer(server=True)
    w.set_ext_send_callback(CHARSET, lambda offers=None: None)
    sep = b" "
    offers = b"UTF-8 ASCII"
    w._handle_sb_charset(collections.deque([CHARSET, REQUEST, sep, offers]))
    assert t.writes[-1] == IAC + SB + CHARSET + REJECTED + IAC + SE
    # REQUEST -> ACCEPTED
    w2, t2, _ = new_writer(server=True)
    w2.set_ext_send_callback(CHARSET, lambda offers=None: "UTF-8")
    w2._handle_sb_charset(collections.deque([CHARSET, REQUEST, sep, offers]))
    assert t2.writes[-1] == IAC + SB + CHARSET + ACCEPTED + b"UTF-8" + IAC + SE
    # ACCEPTED -> callback
    seen = {}
    w3, *_ = new_writer(server=True)
    w3.set_ext_callback(CHARSET, lambda cs: seen.setdefault("cs", cs))
    w3._handle_sb_charset(collections.deque([CHARSET, ACCEPTED, b"UTF-8"]))
    assert seen["cs"] == "UTF-8"
    # TTABLE_* -> NotImplementedError
    w4, *_ = new_writer(server=True)
    with pytest.raises(NotImplementedError):
        w4._handle_sb_charset(collections.deque([CHARSET, TTABLE_IS]))
    # illegal option
    w5, *_ = new_writer(server=True)
    with pytest.raises(ValueError):
        w5._handle_sb_charset(collections.deque([CHARSET, b"\x99"]))


def test_handle_sb_xdisploc_wrong_side_asserts_and_send_and_is():
    # SEND must be client side
    ws, *_ = new_writer(server=True)
    with pytest.raises(AssertionError):
        ws._handle_sb_xdisploc(collections.deque([XDISPLOC, SEND]))
    # client SEND -> IS response
    wc, tc, _ = new_writer(server=False, client=True)
    wc.set_ext_send_callback(XDISPLOC, lambda: "host:0")
    wc._handle_sb_xdisploc(collections.deque([XDISPLOC, SEND]))
    assert tc.writes[-1] == IAC + SB + XDISPLOC + IS + b"host:0" + IAC + SE
    # server IS -> callback
    seen = {}
    ws2, *_ = new_writer(server=True)
    ws2.set_ext_callback(XDISPLOC, lambda x: seen.setdefault("x", x))
    ws2._handle_sb_xdisploc(collections.deque([XDISPLOC, IS, b"disp:1"]))
    assert seen["x"] == "disp:1"


def test_handle_sb_tspeed_wrong_side_asserts_and_send_and_is():
    # SEND must be client side
    ws, *_ = new_writer(server=True)
    with pytest.raises(AssertionError):
        ws._handle_sb_tspeed(collections.deque([TSPEED, SEND]))
    # client SEND -> IS response
    wc, tc, _ = new_writer(server=False, client=True)
    wc.set_ext_send_callback(TSPEED, lambda: (9600, 9600))
    wc._handle_sb_tspeed(collections.deque([TSPEED, SEND]))
    assert tc.writes[-1] == IAC + SB + TSPEED + IS + b"9600" + b"," + b"9600" + IAC + SE
    # server IS -> parse and callback
    seen = {}
    ws2, *_ = new_writer(server=True)
    ws2.set_ext_callback(TSPEED, lambda rx, tx: seen.setdefault("v", (rx, tx)))
    payload = b"57600,115200"
    ws2._handle_sb_tspeed(
        collections.deque(
            [TSPEED, IS] + [payload[i : i + 1] for i in range(len(payload))]
        )
    )
    assert seen["v"] == (57600, 115200)


def test_handle_sb_environ_wrong_side_send_and_is():
    # SEND must be client side
    ws, *_ = new_writer(server=True)
    with pytest.raises(AssertionError):
        ws._handle_sb_environ(collections.deque([NEW_ENVIRON, SEND]))
    # client SEND -> respond IS using ext_send_callback
    wc, tc, _ = new_writer(server=False, client=True)
    wc.set_ext_send_callback(NEW_ENVIRON, lambda keys: {"USER": "root"})
    send_payload = _encode_env_buf({"USER": ""})
    wc._handle_sb_environ(collections.deque([NEW_ENVIRON, SEND, send_payload]))
    assert tc.writes[-1].startswith(IAC + SB + NEW_ENVIRON + IS)
    assert tc.writes[-1].endswith(IAC + SE)
    # server IS -> decoded dict
    seen = {}
    ws2, *_ = new_writer(server=True)
    ws2.set_ext_callback(NEW_ENVIRON, lambda env: seen.setdefault("env", env))
    is_payload = _encode_env_buf({"TERM": "xterm", "LANG": "C"})
    ws2._handle_sb_environ(collections.deque([NEW_ENVIRON, IS, is_payload]))
    assert seen["env"]["TERM"] == "xterm"
    assert seen["env"]["LANG"] == "C"


def test_handle_sb_status_invalid_opt_and_receive_status_errors():
    w, t, _ = new_writer(server=True)
    w.local_option[STATUS] = True
    # invalid option after STATUS
    with pytest.raises(ValueError):
        w._handle_sb_status(collections.deque([STATUS, b"\x99"]))
    # _receive_status invalid cmd
    with pytest.raises(ValueError, match="invalid cmd"):
        w._receive_status(collections.deque([NOP, BINARY]))
    # odd-length payload leaves remainder; implementation ignores trailing byte
    w._receive_status(collections.deque([DO]))


def test_handle_sb_lflow_requires_do_lflow():
    w, *_ = new_writer(server=True)
    # must have DO LFLOW received
    with pytest.raises(ValueError):
        w._handle_sb_lflow(collections.deque([LFLOW, LFLOW_OFF]))


def test_handle_sb_linemode_illegal_option_raises():
    w, *_ = new_writer(server=True)
    with pytest.raises(ValueError, match="Illegal IAC SB LINEMODE"):
        w._handle_sb_linemode(collections.deque([LINEMODE, b"\xff"]))


def test_is_oob_and_feed_byte_progression():
    w, *_ = new_writer(server=True)
    # register NOP to avoid ValueError
    w.set_iac_callback(NOP, lambda c: None)
    # feed IAC
    r1 = w.feed_byte(IAC)
    assert r1 is False
    assert w.is_oob
    # feed 2nd byte NOP
    r2 = w.feed_byte(NOP)
    assert r2 is False
    assert w.is_oob  # cmd_received still truthy during this call
    # now a normal byte resumes in-band
    r3 = w.feed_byte(b"A")
    assert r3 is True
    assert not w.is_oob


def test_iac_pending_and_dont_paths():
    w, t, _ = new_writer(server=True)
    # pending DO suppresses send
    w.pending_option[DO + ECHO] = True
    assert w.iac(DO, ECHO) is False
    # DONT path when no prior key -> set remote False and send
    sent = w.iac(DONT, ECHO)
    assert sent is True
    assert w.remote_option[ECHO] is False
    assert t.writes[-1] == IAC + DONT + ECHO
    # DONT path when already remote False -> suppressed
    sent2 = w.iac(DONT, ECHO)
    assert sent2 is False


def test_telnetwriterunicode_write_and_echo_and_encoding_errors():
    def fn_encoding(outgoing=True):
        return "ascii"

    t = MockTransport()
    p = ProtocolBase()
    w = TelnetWriterUnicode(t, p, fn_encoding, server=True)
    # write unicode
    w.write("hi")
    assert t.writes[-1] == b"hi"
    # echo only if server will_echo -> needs local ECHO
    w.local_option[ECHO] = True
    w.echo("X")
    assert t.writes[-1] == b"X"
    # writelines
    w.writelines(["a", "b"])
    assert t.writes[-1] == b"ab"


def test_option_enabled_and_setitem_debug_path():
    opt = Option("testopt", log=type("L", (), {"debug": lambda *a, **k: None})())
    # not set -> enabled False
    assert opt.enabled(ECHO) is False
    # set True
    opt[ECHO] = True
    assert opt.enabled(ECHO) is True
    # set False
    opt[ECHO] = False
    assert opt.enabled(ECHO) is False


def test_escape_unescape_and_env_encode_decode_roundtrip():
    # escaping VAR/USERVAR
    from telnetlib3.telopt import VAR, USERVAR, ESC, VALUE

    buf = b"A" + VAR + b"B" + USERVAR + b"C"
    esc = _escape_environ(buf)
    assert VAR in esc and USERVAR in esc and esc.count(ESC) == 2
    unesc = _unescape_environ(esc)
    assert unesc == buf
    # encode/decode env
    env = {"USER": "root", "LANG": "C.UTF-8"}
    enc = _encode_env_buf(env)
    dec = _decode_env_buf(enc)
    assert dec == {"USER": "root", "LANG": "C.UTF-8"}


def test_transport_property_write_eof_can_write_eof_and_is_closing():
    class MT2(MockTransport):
        def __init__(self):
            super().__init__()
            self.eof_called = False

        def write_eof(self):
            self.eof_called = True

        def can_write_eof(self):
            return True

    w, t, p = new_writer(server=True)
    # transport property
    assert w.transport is t

    # substitute transport with eof support
    t2 = MT2()
    w2 = TelnetWriter(t2, p, server=True)
    assert w2.can_write_eof() is True
    w2.write_eof()
    assert t2.eof_called is True

    # is_closing: early True via transport.is_closing()
    assert w2.is_closing() is False
    t2._closing = True
    assert w2.is_closing() is True


def test_repr_covers_flags_and_wills_and_failed_reply():
    w, t, p = new_writer(server=True)
    # pending failed-reply
    w.pending_option[DO + ECHO] = True
    # local and remote enabled
    w.local_option[ECHO] = True
    w.local_option[SGA] = True
    w.remote_option[BINARY] = True
    s = repr(w)
    assert "TelnetWriter" in s and "server" in s
    assert "failed-reply:" in s
    assert "server-will:" in s
    assert "client-will:" in s

    # client perspective too
    wc, tc, pc = new_writer(server=False, client=True)
    wc.pending_option[WILL + SGA] = True
    wc.remote_option[ECHO] = True
    wc.local_option[BINARY] = True
    sc = repr(wc)
    assert "client" in sc


def test_request_tspeed_and_charset_pending_branches():
    w, t, p = new_writer(server=True)
    # TSPEED: request pending suppresses second send
    w.remote_option[TSPEED] = True
    assert w.request_tspeed() is True
    assert w.request_tspeed() is False

    # CHARSET: pending suppresses second send
    w.remote_option[CHARSET] = True
    w.set_ext_send_callback(CHARSET, lambda: ["UTF-8"])
    assert w.request_charset() is True
    assert w.request_charset() is False


def test_request_environ_pending_branch():
    w, t, p = new_writer(server=True)
    w.remote_option[NEW_ENVIRON] = True
    w.set_ext_send_callback(NEW_ENVIRON, lambda: ["USER"])
    # mark request as pending
    w.pending_option[SB + NEW_ENVIRON] = True
    assert w.request_environ() is False


def test_tspeed_is_malformed_values_logged_and_ignored():
    seen = {}
    w, t, p = new_writer(server=True)
    w.set_ext_callback(TSPEED, lambda rx, tx: seen.setdefault("v", (rx, tx)))
    payload = b"x,y"  # not integers, triggers ValueError path
    buf = collections.deque(
        [TSPEED, IS] + [payload[i : i + 1] for i in range(len(payload))]
    )
    w._handle_sb_tspeed(buf)
    assert "v" not in seen


def test_handle_sb_lflow_unknown_raises():
    w, t, p = new_writer(server=True)
    w.local_option[LFLOW] = True
    with pytest.raises(ValueError):
        w._handle_sb_lflow(collections.deque([LFLOW, b"\x99"]))


def test_ttype_xdisploc_tspeed_pending_flags_cleared():
    # TTYPE pending cleared on SEND
    wc, tc, pc = new_writer(server=False, client=True)
    wc.set_ext_send_callback(TTYPE, lambda: "vt100")
    wc.pending_option[WILL + TTYPE] = True
    wc._handle_sb_ttype(collections.deque([TTYPE, SEND]))
    assert not wc.pending_option.enabled(WILL + TTYPE)

    # XDISPLOC pending cleared on SEND
    wc.set_ext_send_callback(XDISPLOC, lambda: "host:0")
    wc.pending_option[WILL + XDISPLOC] = True
    wc._handle_sb_xdisploc(collections.deque([XDISPLOC, SEND]))
    assert not wc.pending_option.enabled(WILL + XDISPLOC)

    # TSPEED pending cleared on SEND
    wc.set_ext_send_callback(TSPEED, lambda: (9600, 9600))
    wc.pending_option[WILL + TSPEED] = True
    wc._handle_sb_tspeed(collections.deque([TSPEED, SEND]))
    assert not wc.pending_option.enabled(WILL + TSPEED)


def test_environ_pending_typo_branch_cleared():
    # The implementation clears WILL+TTYPE in environ SEND path; ensure executed
    wc, tc, pc = new_writer(server=False, client=True)
    wc.set_ext_send_callback(NEW_ENVIRON, lambda keys: {"USER": "root"})
    wc.pending_option[WILL + TTYPE] = True
    send_payload = _encode_env_buf({"USER": ""})
    wc._handle_sb_environ(collections.deque([NEW_ENVIRON, SEND, send_payload]))
    assert not wc.pending_option.enabled(WILL + TTYPE)


def test_sndloc_callback():
    seen = {}
    ws, ts, ps = new_writer(server=True)
    ws.set_ext_callback(SNDLOC, lambda s: seen.setdefault("loc", s))
    # Dispatch via handle_subnegotiation to cover that path too
    ws.handle_subnegotiation(collections.deque([SNDLOC, b"Room 641-A"]))
    assert seen["loc"] == "Room 641-A"


def test_simple_handlers_cover_logging():
    w, t, p = new_writer(server=True)
    # IAC-level handlers
    w.handle_nop(NOP)
    w.handle_ga(GA)
    w.handle_dm(DM)
    # mixed-mode byte handlers (accept any byte)
    w.handle_eor(b"\x00")
    w.handle_abort(b"\x00")
    w.handle_eof(b"\x00")
    w.handle_susp(b"\x00")
    w.handle_brk(b"\x00")
    w.handle_ayt(b"\x00")
    w.handle_ip(b"\x00")
    w.handle_ao(b"\x00")
    w.handle_ec(b"\x00")
    w.handle_tm(DO)  # use DO for logging


def test_feed_byte_clears_pending_dont_on_will():
    # Client receiving WILL ECHO with pending DONT+ECHO clears pending
    wc, tc, pc = new_writer(server=False, client=True)
    wc.pending_option[DONT + ECHO] = True
    wc.feed_byte(IAC)
    wc.feed_byte(WILL)
    wc.feed_byte(ECHO)
    assert not wc.pending_option.enabled(DONT + ECHO)
    # should have replied DO ECHO and enabled remote option
    assert wc.remote_option[ECHO] is True
    assert tc.writes[-1] == IAC + DO + ECHO


def test_send_status_composes_both_local_and_remote_entries():
    w, t, p = new_writer(server=True)
    # grant privilege to send status
    w.local_option[STATUS] = True
    # local: one True (BINARY), one False (ECHO)
    w.local_option[BINARY] = True
    w.local_option[ECHO] = False
    # remote: one True (SGA), one False (LINEMODE)
    w.remote_option[SGA] = True
    w.remote_option[LINEMODE] = False
    # include pending DO and DONT flags to exercise branches
    w.pending_option[DO + ECHO] = True
    w.pending_option[DONT + NAWS] = True

    w._send_status()
    frame = t.writes[-1]
    assert frame.startswith(IAC + SB + STATUS + IS) and frame.endswith(IAC + SE)
    # ensure there is at least one WILL/WONT and DO/DONT in payload
    payload = frame[4:-2]
    assert any(b in payload for b in (DO, DONT))
    assert any(b in payload for b in (WILL, WONT))


def test_reader_requires_exception_callable():
    class BadReader2:
        exception = 42  # not callable

    t = MockTransport()
    p = ProtocolBase()
    with pytest.raises(TypeError):
        TelnetWriter(t, p, server=True, reader=BadReader2())


def test_request_status_without_will_returns_false():
    w, t, p = new_writer(server=True)
    assert w.request_status() is False


def test_receive_status_mismatch_logs_no_exception():
    w, t, p = new_writer(server=True)
    # local DO BINARY but local_option[BINARY] False causes mismatch logging
    buf = collections.deque([DO, BINARY])
    w._receive_status(buf)  # should not raise


def test_inbinary_outbinary_properties():
    w, t, p = new_writer(server=True)
    assert w.outbinary is False
    assert w.inbinary is False
    w.local_option[BINARY] = True
    w.remote_option[BINARY] = True
    assert w.outbinary is True
    assert w.inbinary is True


def test_unicode_writer_write_after_close_noop():
    def fn(outgoing=True):
        return "ascii"

    t = MockTransport()
    p = ProtocolBase()
    wu = TelnetWriterUnicode(t, p, fn, server=True)
    wu.close()
    wu.write("ignored")
    # no writes performed after close
    assert t.writes == []


def test_handle_sb_forwardmask_server_will_and_client_do():
    # server WILL path sets remote_option[SB+LINEMODE+FORWARDMASK]
    ws, ts, ps = new_writer(server=True)
    ws.remote_option[LINEMODE] = True
    ws._handle_sb_forwardmask(WILL, collections.deque())
    opt = SB + LINEMODE + slc.LMODE_FORWARDMASK
    assert ws.remote_option[opt] is True

    # client DO path currently asserts that bytes must follow DO (pre-check)
    wc, tc, pc = new_writer(server=False, client=True)
    wc.local_option[LINEMODE] = True
    with pytest.raises(AssertionError):
        wc._handle_sb_forwardmask(DO, collections.deque([b"x"]))


def test_slc_add_buffer_full_raises():
    w, t, p = new_writer(server=True)
    # fill buffer to maximum
    for _ in range(slc.NSLC * 6):
        w._slc_buffer.append(b"x")
    with pytest.raises(ValueError):
        w._slc_add(slc.SLC_IP)
    # clear to avoid side effects
    w._slc_buffer.clear()


def test_handle_sb_linemode_slc_various():
    w, t, p = new_writer(server=True)

    # out-of-range func triggers nosupport add
    w._slc_process(bytes([255]), slc.SLC(slc.SLC_VARIABLE, b"\x01"))

    # func == theNULL with SLC_DEFAULT -> send default tab
    w._slc_process(theNULL, slc.SLC(slc.SLC_DEFAULT, theNULL))
    # func == theNULL with SLC_VARIABLE -> send current tab
    w._slc_process(theNULL, slc.SLC(slc.SLC_VARIABLE, theNULL))

    # equal level and ack set -> return
    func = slc.SLC_IP
    mydef = w.slctab[func]
    ack_mask = bytes([ord(mydef.mask) | ord(slc.SLC_ACK)])
    w._slc_process(func, slc.SLC(ack_mask, mydef.val))

    # ack set with mismatched value -> debug and return
    diff_val = b"\x00" if mydef.val != b"\x00" else b"\x01"
    w._slc_process(func, slc.SLC(ack_mask, diff_val))

    # hislevel NOSUPPORT -> set nosupport + ack
    w._slc_process(slc.SLC_AO, slc.SLC(slc.SLC_NOSUPPORT, theNULL))

    # hislevel DEFAULT with mylevel DEFAULT -> mask to NOSUPPORT
    w._slc_process(slc.SLC_SYNCH, slc.SLC(slc.SLC_DEFAULT, b"\x7f"))

    # self.slctab[func].val != theNULL -> accept change and ack
    w._slc_process(slc.SLC_EC, slc.SLC(slc.SLC_VARIABLE, b"\x08"))

    # mylevel DEFAULT and our val theNULL -> store & ack whatever was sent
    w._slc_process(slc.SLC_BRK, slc.SLC(slc.SLC_VARIABLE, b"\x02"))

    # degenerate to NOSUPPORT when both CANTCHANGE
    f = slc.SLC_EOF
    w.slctab[f] = slc.SLC(slc.SLC_CANTCHANGE, theNULL)
    w._slc_process(f, slc.SLC(slc.SLC_CANTCHANGE, b"\x04"))

    # else: mask current level to levelbits, with mylevel CANTCHANGE
    f2 = slc.SLC_EL
    w.slctab[f2] = slc.SLC(slc.SLC_CANTCHANGE, theNULL)
    w._slc_process(f2, slc.SLC(slc.SLC_VARIABLE, b"\x15"))

    # Full SLC handler path with a proper triplet
    trip = collections.deque([slc.SLC_IP, slc.SLC_VARIABLE, b"\x03"])
    w._handle_sb_linemode_slc(trip)


def test_request_forwardmask_returns_false_without_will_linemode():
    w, t, p = new_writer(server=True)
    # no WILL LINEMODE
    assert w.request_forwardmask() is False


def test_mode_client_kludge_and_server_kludge_and_remote_local():
    # server kludge when local ECHO and SGA
    ws, ts, ps = new_writer(server=True)
    ws.local_option[ECHO] = True
    ws.local_option[SGA] = True
    assert ws.mode == "kludge"
    # client kludge when remote ECHO and SGA
    wc, tc, pc = new_writer(server=False, client=True)
    wc.remote_option[ECHO] = True
    wc.remote_option[SGA] = True
    assert wc.mode == "kludge"
    # remote mode when remote LINEMODE enabled and not local
    wc.remote_option[LINEMODE] = True
    wc._linemode = slc.Linemode(bytes([0]))  # remote
    assert wc.mode == "remote"


def test_handle_send_server_and_client_charset_returns():
    ws, ts, ps = new_writer(server=True)
    assert ws.handle_send_server_charset(["UTF-8"]) == ["UTF-8"]
    wc, tc, pc = new_writer(server=False, client=True)
    assert wc.handle_send_client_charset(["UTF-8", "ASCII"]) == ""


def test_iac_wont_and_dont_suppressed_when_remote_false():
    w, t, p = new_writer(server=True)
    # WONT sets local option False and writes frame
    w.local_option[ECHO] = True
    assert w.iac(WONT, ECHO) is True
    assert w.local_option[ECHO] is False
    assert t.writes[-1] == IAC + WONT + ECHO
    # DONT suppressed when remote has key and is False
    w.remote_option[ECHO] = False
    assert w.iac(DONT, ECHO) is False


def test_send_status_clears_pending_will_status():
    w, t, p = new_writer(server=True)
    w.pending_option[WILL + STATUS] = True
    w._send_status()
    assert not w.pending_option.enabled(WILL + STATUS)


def test_handle_sb_linemode_forwardmask_wrong_sb_opt_raises():
    w, t, p = new_writer(server=True)
    with pytest.raises(ValueError, match="expected LMODE_FORWARDMASK"):
        # DO followed by wrong sb_opt value -> ValueError
        w._handle_sb_linemode(collections.deque([LINEMODE, DO, b"\x99"]))


def test_handle_sb_environ_info_warning_path():
    seen = []
    ws, ts, ps = new_writer(server=True)
    ws.set_ext_callback(NEW_ENVIRON, lambda env: seen.append(env))
    # First IS sets pending_option[SB + NEW_ENVIRON] = False
    is_payload = _encode_env_buf({"USER": "root"})
    ws._handle_sb_environ(collections.deque([NEW_ENVIRON, IS, is_payload]))
    # Then INFO path with pending False triggers warning path and callback
    info_payload = _encode_env_buf({"LANG": "C"})
    ws._handle_sb_environ(collections.deque([NEW_ENVIRON, INFO, info_payload]))
    assert any("USER" in d for d in seen)
    assert any("LANG" in d for d in seen)


def test_handle_will_logout_raises_on_client():
    wc, tc, pc = new_writer(server=False, client=True)
    with pytest.raises(ValueError, match="cannot recv WILL LOGOUT"):
        wc.handle_will(LOGOUT)


def test_handle_will_tm_success_sets_remote_option_and_calls_cb():
    called = {}
    wtm, tt, pp = new_writer(server=True)
    wtm.set_iac_callback(TM, lambda cmd: called.setdefault("cmd", cmd))
    # mark DO+TM pending so WILL TM is accepted
    wtm.pending_option[DO + TM] = True
    wtm.handle_will(TM)
    assert wtm.remote_option[TM] is True
    assert called.get("cmd") == WILL


def test_handle_send_helpers_return_values():
    w, t, p = new_writer(server=True)
    assert w.handle_send_xdisploc() == ""
    assert w.handle_send_ttype() == ""
    assert w.handle_send_server_environ() == []
    assert w.handle_send_naws() == (80, 24)


def test_miscellaneous_handle_logs_cover_remaining_handlers():
    # server writer for server-side handlers
    ws, ts, ps = new_writer(server=True)
    # simple extension/info handlers
    ws.handle_xdisploc("host:0")
    ws.handle_sndloc("Room 1")
    ws.handle_ttype("xterm")
    ws.handle_naws(80, 24)
    ws.handle_environ({"USER": "root"})
    ws.handle_tspeed(9600, 9600)
    ws.handle_charset("UTF-8")
    # SLC related debug handlers
    ws.handle_lnext(b"\x00")
    ws.handle_rp(b"\x00")
    ws.handle_ew(b"\x00")
    ws.handle_xon(b"\x00")
    ws.handle_xoff(b"\x00")
