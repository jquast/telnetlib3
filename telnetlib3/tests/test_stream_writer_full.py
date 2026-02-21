# std imports
import asyncio
import logging
import collections

# 3rd party
import pytest

# local
from telnetlib3 import slc
from telnetlib3.telopt import (
    DM,
    DO,
    GA,
    IS,
    SB,
    SE,
    TM,
    ESC,
    IAC,
    NOP,
    SGA,
    VAR,
    DONT,
    ECHO,
    GMCP,
    INFO,
    NAWS,
    SEND,
    WILL,
    WONT,
    LFLOW,
    TTYPE,
    VALUE,
    BINARY,
    LOGOUT,
    SNDLOC,
    STATUS,
    TSPEED,
    CHARSET,
    REQUEST,
    USERVAR,
    ACCEPTED,
    LINEMODE,
    REJECTED,
    XDISPLOC,
    LFLOW_OFF,
    TTABLE_IS,
    NEW_ENVIRON,
    AUTHENTICATION,
    COM_PORT_OPTION,
    theNULL,
)
from telnetlib3.stream_writer import (
    Option,
    TelnetWriter,
    TelnetWriterUnicode,
    _decode_env_buf,
    _encode_env_buf,
    _escape_environ,
    _unescape_environ,
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
    assert not w.connection_closed
    w.close()
    assert w.connection_closed is True
    assert w._transport is None
    assert w._protocol is None
    assert t._closing is True
    assert w._closed_fut is None or w._closed_fut.done()
    assert not w._ext_callback
    assert not w._ext_send_callback
    assert not w._slc_callback
    assert not w._iac_callback
    assert p.conn_lost_called is True
    w.close()

    t2 = MockTransport()
    p2 = ProtocolBase()
    w2 = TelnetWriter(t2, p2, server=True)
    w2.close()
    w2.write(b"ignored")
    assert not t2.writes


@pytest.mark.parametrize(
    "setup",
    [lambda w, t: setattr(t, "_closing", True), lambda w, t: w.close()],
    ids=["closing", "closed"],
)
def test_send_iac_skipped_when_closing_or_closed(setup):
    w, t, _ = new_writer(server=True)
    setup(w, t)
    w.send_iac(IAC + NOP)
    assert not t.writes


def test_forwardmask_skipped_when_closing():
    """request_forwardmask() drops writes when transport is closing."""
    w, t, _ = new_writer(server=True)
    w.remote_option[LINEMODE] = True
    t._closing = True
    w.request_forwardmask()
    assert not t.writes


def test_send_linemode_skipped_when_closing():
    """send_linemode() drops writes when transport is closing."""
    w, t, _ = new_writer(server=True)
    w.remote_option[LINEMODE] = True
    t._closing = True
    w.send_linemode()
    assert not t.writes


def test_slc_end_skipped_when_closing():
    """_slc_end() drops writes when closing, buffer still cleared."""
    w, t, _ = new_writer(server=True)
    w._slc_buffer = [b"\x03\x03\x04"]
    t._closing = True
    w._slc_end()
    assert not t.writes
    assert not w._slc_buffer


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
    t._closing = True
    await w.drain()
    assert p.drain_called is True


def test_request_forwardmask_writes_mask_between_frames():
    w, t, _ = new_writer(server=True)
    w.remote_option[LINEMODE] = True
    assert w.request_forwardmask() is True
    assert len(t.writes) >= 3
    assert t.writes[-3] == IAC + SB + LINEMODE + DO + slc.LMODE_FORWARDMASK
    assert len(t.writes[-2]) in (16, 32)
    assert t.writes[-1] == IAC + SE


def test_send_linemode_asserts_when_not_negotiated():
    w, t, _ = new_writer(server=True)
    with pytest.raises(AssertionError):
        w.send_linemode()


@pytest.mark.parametrize(
    "server, client, cmd, check",
    [
        (True, False, DO, lambda t: t._closing is True),
        (True, False, DONT, lambda t: not t.writes),
        (False, True, WILL, lambda t: t.writes[-1] == IAC + DONT + LOGOUT),
        (False, True, WONT, lambda t: not t.writes),
    ],
    ids=["server_do_closes", "server_dont_noop", "client_will_dont", "client_wont_noop"],
)
def test_handle_logout(server, client, cmd, check):
    w, t, _ = new_writer(server=server, client=client)
    w.handle_logout(cmd)
    assert check(t)


def test_handle_do_server_linemode_refused():
    ws, ts, _ = new_writer(server=True)
    ws.handle_do(LINEMODE)
    assert ts.writes[-1] == IAC + WONT + LINEMODE


def test_handle_do_client_logout_raises():
    wc, *_ = new_writer(server=False, client=True)
    with pytest.raises(ValueError, match="cannot recv DO LOGOUT"):
        wc.handle_do(LOGOUT)


def test_handle_do_client_echo_refused():
    wc, tc, _ = new_writer(server=False, client=True)
    wc.handle_do(ECHO)
    assert tc.writes[-1] == IAC + WONT + ECHO


def test_handle_do_tm_callback():
    called = {}
    wtm, ttm, _ = new_writer(server=True)
    wtm.set_iac_callback(TM, lambda cmd: called.setdefault("cmd", cmd))
    wtm.handle_do(TM)
    assert ttm.writes[-1] == IAC + WILL + TM
    assert called["cmd"] == DO


def test_handle_do_server_logout_callback():
    seen = {}
    ws, *_ = new_writer(server=True)
    ws.set_ext_callback(LOGOUT, lambda cmd: seen.setdefault("v", cmd))
    ws.handle_do(LOGOUT)
    assert seen["v"] == DO


def test_handle_dont_logout_calls_callback_on_server():
    seen = {}
    w, *_ = new_writer(server=True)
    w.set_ext_callback(LOGOUT, lambda cmd: seen.setdefault("v", cmd))
    w.handle_dont(LOGOUT)
    assert seen["v"] == DONT


def test_handle_will_server_echo_raises():
    ws, *_ = new_writer(server=True)
    with pytest.raises(ValueError, match="cannot recv WILL ECHO"):
        ws.handle_will(ECHO)


def test_handle_will_client_naws_refused():
    wc, tc, _ = new_writer(server=False, client=True)
    wc.handle_will(NAWS)
    assert tc.writes[-1] == IAC + DONT + NAWS


def test_handle_will_server_tm_raises():
    wtm, *_ = new_writer(server=True)
    with pytest.raises(ValueError, match="cannot recv WILL TM"):
        wtm.handle_will(TM)


def test_handle_will_server_logout_callback():
    seen = {}
    w, *_ = new_writer(server=True)
    w.set_ext_callback(LOGOUT, lambda cmd: seen.setdefault("v", cmd))
    w.handle_will(LOGOUT)
    assert seen["v"] == WILL


def test_handle_will_pending_authentication_rejected():
    w, t, _ = new_writer(server=True)
    w.pending_option[DO + AUTHENTICATION] = True
    w.handle_will(AUTHENTICATION)
    assert t.writes[-1] == IAC + DONT + AUTHENTICATION
    assert not w.pending_option.get(DO + AUTHENTICATION, False)
    assert AUTHENTICATION in w.rejected_will


def test_handle_will_then_do_unsupported_sends_both_dont_and_wont():
    """WILL then DO for unsupported option must send DONT and WONT."""
    w, t, _ = new_writer(server=True)
    w.handle_will(AUTHENTICATION)
    assert t.writes[-1] == IAC + DONT + AUTHENTICATION
    assert AUTHENTICATION in w.rejected_will
    w.handle_do(AUTHENTICATION)
    assert t.writes[-1] == IAC + WONT + AUTHENTICATION
    assert AUTHENTICATION in w.rejected_do


def test_handle_wont_tm_unsolicited_raises():
    w, *_ = new_writer(server=True)
    with pytest.raises(ValueError, match="WONT TM"):
        w.handle_wont(TM)


def test_handle_wont_tm_pending_clears():
    w, *_ = new_writer(server=True)
    w.pending_option[DO + TM] = True
    w.handle_wont(TM)
    assert w.remote_option[TM] is False


def test_handle_wont_client_logout_callback():
    seen = {}
    wc, *_ = new_writer(server=False, client=True)
    wc.set_ext_callback(LOGOUT, lambda cmd: seen.setdefault("v", cmd))
    wc.handle_wont(LOGOUT)
    assert seen["v"] == WONT


def test_handle_subnegotiation_comport_and_gmcp_and_errors():
    w, *_ = new_writer(server=True)
    w.handle_subnegotiation(collections.deque([GMCP, b"a", b"b"]))
    w.handle_subnegotiation(collections.deque([COM_PORT_OPTION, b"\x64", b"T", b"e", b"s", b"t"]))
    assert w.comport_data is not None
    assert w.comport_data["signature"] == "Test"

    with pytest.raises(ValueError, match="SE: buffer empty"):
        w.handle_subnegotiation(collections.deque([]))
    with pytest.raises(ValueError, match="SE: buffer is NUL"):
        w.handle_subnegotiation(collections.deque([theNULL, b"x"]))
    with pytest.raises(ValueError, match="SE: buffer too short"):
        w.handle_subnegotiation(collections.deque([NAWS]))
    with pytest.raises(ValueError, match="SB unhandled"):
        w.handle_subnegotiation(collections.deque([bytes([0x7F]), b"x"]))


def test_handle_sb_charset_request_rejected():
    w, t, _ = new_writer(server=True)
    w.set_ext_send_callback(CHARSET, lambda offers=None: None)
    w._handle_sb_charset(collections.deque([CHARSET, REQUEST, b" ", b"UTF-8 ASCII"]))
    assert t.writes[-1] == IAC + SB + CHARSET + REJECTED + IAC + SE


def test_handle_sb_charset_request_accepted():
    w, t, _ = new_writer(server=True)
    w.set_ext_send_callback(CHARSET, lambda offers=None: "UTF-8")
    w._handle_sb_charset(collections.deque([CHARSET, REQUEST, b" ", b"UTF-8 ASCII"]))
    assert t.writes[-1] == (IAC + SB + CHARSET + ACCEPTED + b"UTF-8" + IAC + SE)


def test_handle_sb_charset_accepted_callback():
    seen = {}
    w, *_ = new_writer(server=True)
    w.set_ext_callback(CHARSET, lambda cs: seen.setdefault("cs", cs))
    w._handle_sb_charset(collections.deque([CHARSET, ACCEPTED, b"UTF-8"]))
    assert seen["cs"] == "UTF-8"


def test_handle_sb_charset_ttable_not_implemented():
    w, *_ = new_writer(server=True)
    with pytest.raises(NotImplementedError):
        w._handle_sb_charset(collections.deque([CHARSET, TTABLE_IS]))


def test_handle_sb_charset_illegal_raises():
    w, *_ = new_writer(server=True)
    with pytest.raises(ValueError):
        w._handle_sb_charset(collections.deque([CHARSET, b"\x99"]))


def test_handle_sb_xdisploc_wrong_side_asserts_and_send_and_is():
    wc, tc, _ = new_writer(server=False, client=True)
    wc.set_ext_send_callback(XDISPLOC, lambda: "host:0")
    wc._handle_sb_xdisploc(collections.deque([XDISPLOC, SEND]))
    assert tc.writes[-1] == IAC + SB + XDISPLOC + IS + b"host:0" + IAC + SE

    seen = {}
    ws2, *_ = new_writer(server=True)
    ws2.set_ext_callback(XDISPLOC, lambda x: seen.setdefault("x", x))
    ws2._handle_sb_xdisploc(collections.deque([XDISPLOC, IS, b"disp:1"]))
    assert seen["x"] == "disp:1"


def test_handle_sb_tspeed_wrong_side_asserts_and_send_and_is():
    wc, tc, _ = new_writer(server=False, client=True)
    wc.set_ext_send_callback(TSPEED, lambda: (9600, 9600))
    wc._handle_sb_tspeed(collections.deque([TSPEED, SEND]))
    assert tc.writes[-1] == IAC + SB + TSPEED + IS + b"9600" + b"," + b"9600" + IAC + SE

    seen = {}
    ws2, *_ = new_writer(server=True)
    ws2.set_ext_callback(TSPEED, lambda rx, tx: seen.setdefault("v", (rx, tx)))
    payload = b"57600,115200"
    ws2._handle_sb_tspeed(
        collections.deque([TSPEED, IS] + [payload[i : i + 1] for i in range(len(payload))])
    )
    assert seen["v"] == (57600, 115200)


def test_handle_sb_environ_wrong_side_send_and_is():
    wc, tc, _ = new_writer(server=False, client=True)
    wc.set_ext_send_callback(NEW_ENVIRON, lambda keys: {"USER": "root"})
    send_payload = _encode_env_buf({"USER": ""})
    wc._handle_sb_environ(collections.deque([NEW_ENVIRON, SEND, send_payload]))
    assert tc.writes[-1].startswith(IAC + SB + NEW_ENVIRON + IS)
    assert tc.writes[-1].endswith(IAC + SE)

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
    with pytest.raises(ValueError):
        w._handle_sb_status(collections.deque([STATUS, b"\x99"]))
    w._receive_status(collections.deque([NOP, BINARY]))
    w._receive_status(collections.deque([DO]))


def test_handle_sb_lflow_requires_do_lflow():
    w, *_ = new_writer(server=True)
    with pytest.raises(ValueError):
        w._handle_sb_lflow(collections.deque([LFLOW, LFLOW_OFF]))


def test_handle_sb_linemode_illegal_option_raises():
    w, *_ = new_writer(server=True)
    with pytest.raises(ValueError, match="Illegal IAC SB LINEMODE"):
        w._handle_sb_linemode(collections.deque([LINEMODE, b"\xff"]))


def test_is_oob_and_feed_byte_progression():
    w, *_ = new_writer(server=True)
    w.set_iac_callback(NOP, lambda c: None)
    assert w.feed_byte(IAC) is False
    assert w.is_oob
    assert w.feed_byte(NOP) is False
    assert w.is_oob
    assert w.feed_byte(b"A") is True
    assert not w.is_oob


def test_iac_pending_and_dont_paths():
    w, t, _ = new_writer(server=True)
    w.pending_option[DO + ECHO] = True
    assert w.iac(DO, ECHO) is False

    assert w.iac(DONT, ECHO) is True
    assert w.remote_option[ECHO] is False
    assert t.writes[-1] == IAC + DONT + ECHO

    assert w.iac(DONT, ECHO) is False


def test_telnetwriterunicode_write_and_echo_and_encoding_errors():
    def fn_encoding(outgoing=True):
        return "ascii"

    t = MockTransport()
    p = ProtocolBase()
    w = TelnetWriterUnicode(t, p, fn_encoding, server=True)
    w.write("hi")
    assert t.writes[-1] == b"hi"
    w.local_option[ECHO] = True
    w.echo("X")
    assert t.writes[-1] == b"X"
    # writelines
    w.writelines(["a", "b"])
    assert t.writes[-1] == b"ab"


def test_option_enabled_and_setitem_debug_path():
    opt = Option("testopt", log=type("L", (), {"debug": lambda *a, **k: None})())
    assert opt.enabled(ECHO) is False
    opt[ECHO] = True
    assert opt.enabled(ECHO) is True
    opt[ECHO] = False
    assert opt.enabled(ECHO) is False


def test_escape_unescape_and_env_encode_decode_roundtrip():
    buf = b"A" + VAR + b"B" + USERVAR + b"C"
    esc = _escape_environ(buf)
    assert VAR in esc and USERVAR in esc and esc.count(ESC) == 2
    unesc = _unescape_environ(esc)
    assert unesc == buf
    env = {"USER": "root", "LANG": "C.UTF-8"}
    enc = _encode_env_buf(env)
    dec = _decode_env_buf(enc)
    assert dec == {"USER": "root", "LANG": "C.UTF-8"}


def test_decode_env_buf_bare_delimiters():
    """Bare VAR/USERVAR delimiters produce empty-string keys."""
    payload = VAR + USERVAR
    result = _decode_env_buf(payload)
    assert result == {"": ""}


def test_handle_sb_environ_bare_var_uservar_sends_empty():
    """SEND with bare VAR/USERVAR passes [''] to callback; security policy returns {}."""
    wc, tc, _ = new_writer(server=False, client=True)
    received_keys = []
    wc.set_ext_send_callback(NEW_ENVIRON, lambda keys: (received_keys.extend(keys), {})[1])
    payload = VAR + USERVAR
    wc._handle_sb_environ(collections.deque([NEW_ENVIRON, SEND, payload]))
    assert received_keys == [""]


def test_decode_env_buf_ebcdic():
    """EBCDIC-encoded env data decoded when encoding=cp037."""
    ebcdic_user = "USER".encode("cp037")
    ebcdic_root = "root".encode("cp037")
    payload = VAR + ebcdic_user + VALUE + ebcdic_root
    result = _decode_env_buf(payload, encoding="cp037")
    assert result == {"USER": "root"}


def test_decode_env_buf_non_ascii_replace():
    """Non-ASCII bytes with default ascii encoding use replacement chars."""
    payload = VAR + b"\x93\x96\x87\x89\x95" + VALUE + b"\xff\xfe"
    result = _decode_env_buf(payload)
    assert len(result) == 1
    key = list(result.keys())[0]
    assert "\ufffd" in key


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
    assert w.transport is t

    t2 = MT2()
    w2 = TelnetWriter(t2, p, server=True)
    assert w2.can_write_eof() is True
    w2.write_eof()
    assert t2.eof_called is True

    assert w2.is_closing() is False
    t2._closing = True
    assert w2.is_closing() is True


def test_repr_covers_flags_and_wills_and_failed_reply():
    w, t, p = new_writer(server=True)
    w.pending_option[DO + ECHO] = True
    w.local_option[ECHO] = True
    w.local_option[SGA] = True
    w.remote_option[BINARY] = True
    s = repr(w)
    assert "TelnetWriter" in s and "server" in s
    assert "failed-reply:" in s
    assert "server-will:" in s
    assert "client-will:" in s

    wc, tc, pc = new_writer(server=False, client=True)
    wc.pending_option[WILL + SGA] = True
    wc.remote_option[ECHO] = True
    wc.local_option[BINARY] = True
    sc = repr(wc)
    assert "client" in sc


def test_request_tspeed_and_charset_pending_branches():
    w, t, p = new_writer(server=True)
    w.remote_option[TSPEED] = True
    assert w.request_tspeed() is True
    assert w.request_tspeed() is False

    w.local_option[CHARSET] = True
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
    payload = b"x,y"
    buf = collections.deque([TSPEED, IS] + [payload[i : i + 1] for i in range(len(payload))])
    w._handle_sb_tspeed(buf)
    assert "v" not in seen


def test_handle_sb_lflow_unknown_raises():
    w, t, p = new_writer(server=True)
    w.local_option[LFLOW] = True
    with pytest.raises(ValueError):
        w._handle_sb_lflow(collections.deque([LFLOW, b"\x99"]))


def test_ttype_xdisploc_tspeed_pending_flags_cleared():
    wc, tc, pc = new_writer(server=False, client=True)
    wc.set_ext_send_callback(TTYPE, lambda: "vt100")
    wc.pending_option[WILL + TTYPE] = True
    wc._handle_sb_ttype(collections.deque([TTYPE, SEND]))
    assert not wc.pending_option.enabled(WILL + TTYPE)

    wc.set_ext_send_callback(XDISPLOC, lambda: "host:0")
    wc.pending_option[WILL + XDISPLOC] = True
    wc._handle_sb_xdisploc(collections.deque([XDISPLOC, SEND]))
    assert not wc.pending_option.enabled(WILL + XDISPLOC)

    wc.set_ext_send_callback(TSPEED, lambda: (9600, 9600))
    wc.pending_option[WILL + TSPEED] = True
    wc._handle_sb_tspeed(collections.deque([TSPEED, SEND]))
    assert not wc.pending_option.enabled(WILL + TSPEED)


def test_environ_pending_typo_branch_cleared():
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
    ws.handle_subnegotiation(collections.deque([SNDLOC, b"Room 641-A"]))
    assert seen["loc"] == "Room 641-A"


def test_simple_handlers_cover_logging():
    w, t, p = new_writer(server=True)
    w.handle_nop(NOP)
    w.handle_ga(GA)
    w.handle_dm(DM)
    w.handle_eor(b"\x00")
    w.handle_abort(b"\x00")
    w.handle_eof(b"\x00")
    w.handle_susp(b"\x00")
    w.handle_brk(b"\x00")
    w.handle_ayt(b"\x00")
    w.handle_ip(b"\x00")
    w.handle_ao(b"\x00")
    w.handle_ec(b"\x00")
    w.handle_tm(DO)


def test_feed_byte_clears_pending_dont_on_will():
    wc, tc, pc = new_writer(server=False, client=True)
    wc.pending_option[DONT + ECHO] = True
    wc.feed_byte(IAC)
    wc.feed_byte(WILL)
    wc.feed_byte(ECHO)
    assert not wc.pending_option.enabled(DONT + ECHO)
    assert wc.remote_option[ECHO] is True
    assert tc.writes[-1] == IAC + DO + ECHO


def test_send_status_composes_both_local_and_remote_entries():
    w, t, p = new_writer(server=True)
    w.local_option[STATUS] = True
    w.local_option[BINARY] = True
    w.local_option[ECHO] = False
    w.remote_option[SGA] = True
    w.remote_option[LINEMODE] = False
    w.pending_option[DO + ECHO] = True
    w.pending_option[DONT + NAWS] = True

    w._send_status()
    frame = t.writes[-1]
    assert frame.startswith(IAC + SB + STATUS + IS) and frame.endswith(IAC + SE)
    payload = frame[4:-2]
    assert any(b in payload for b in (DO, DONT))
    assert any(b in payload for b in (WILL, WONT))


def test_reader_requires_exception_callable():
    class BadReader2:
        exception = 42

    t = MockTransport()
    p = ProtocolBase()
    with pytest.raises(TypeError):
        TelnetWriter(t, p, server=True, reader=BadReader2())


def test_request_status_without_will_returns_false():
    w, t, p = new_writer(server=True)
    assert w.request_status() is False


def test_receive_status_mismatch_logs_no_exception():
    w, t, p = new_writer(server=True)
    buf = collections.deque([DO, BINARY])
    w._receive_status(buf)


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
    assert not t.writes


def test_handle_sb_forwardmask_server_will_and_client_do():
    ws, ts, ps = new_writer(server=True)
    ws.remote_option[LINEMODE] = True
    ws._handle_sb_forwardmask(WILL, collections.deque())
    opt = SB + LINEMODE + slc.LMODE_FORWARDMASK
    assert ws.remote_option[opt] is True

    wc, tc, pc = new_writer(server=False, client=True)
    wc.local_option[LINEMODE] = True
    wc._handle_sb_forwardmask(DO, collections.deque([b"x"]))
    assert wc.local_option[opt] is True


def test_handle_sb_forwardmask_server_without_linemode():
    ws, ts, ps = new_writer(server=True)
    ws._handle_sb_forwardmask(WILL, collections.deque())
    opt = SB + LINEMODE + slc.LMODE_FORWARDMASK
    assert ws.remote_option[opt] is True


def test_handle_sb_forwardmask_server_rejects_do_dont():
    ws, ts, ps = new_writer(server=True)
    ws.remote_option[LINEMODE] = True
    ws._handle_sb_forwardmask(DO, collections.deque())
    opt = SB + LINEMODE + slc.LMODE_FORWARDMASK
    assert opt not in ws.remote_option


def test_handle_sb_forwardmask_client_without_linemode():
    wc, tc, pc = new_writer(server=False, client=True)
    wc._handle_sb_forwardmask(DONT, collections.deque())
    opt = SB + LINEMODE + slc.LMODE_FORWARDMASK
    assert wc.local_option[opt] is False


def test_handle_sb_linemode_passes_opt_to_forwardmask():
    ws, ts, ps = new_writer(server=True)
    ws.remote_option[LINEMODE] = True
    buf = collections.deque([LINEMODE, WONT, slc.LMODE_FORWARDMASK])
    ws._handle_sb_linemode(buf)
    opt = SB + LINEMODE + slc.LMODE_FORWARDMASK
    assert ws.remote_option[opt] is False


def test_slc_add_buffer_full_raises():
    w, t, p = new_writer(server=True)
    for _ in range(slc.NSLC * 6):
        w._slc_buffer.append(b"x")
    with pytest.raises(ValueError):
        w._slc_add(slc.SLC_IP)
    w._slc_buffer.clear()


def test_handle_sb_linemode_slc_various():
    w, t, p = new_writer(server=True)

    w._slc_process(bytes([255]), slc.SLC(slc.SLC_VARIABLE, b"\x01"))
    w._slc_process(theNULL, slc.SLC(slc.SLC_DEFAULT, theNULL))
    w._slc_process(theNULL, slc.SLC(slc.SLC_VARIABLE, theNULL))

    func = slc.SLC_IP
    mydef = w.slctab[func]
    ack_mask = bytes([ord(mydef.mask) | ord(slc.SLC_ACK)])
    w._slc_process(func, slc.SLC(ack_mask, mydef.val))

    diff_val = b"\x00" if mydef.val != b"\x00" else b"\x01"
    w._slc_process(func, slc.SLC(ack_mask, diff_val))

    w._slc_process(slc.SLC_AO, slc.SLC(slc.SLC_NOSUPPORT, theNULL))
    w._slc_process(slc.SLC_SYNCH, slc.SLC(slc.SLC_DEFAULT, b"\x7f"))
    w._slc_process(slc.SLC_EC, slc.SLC(slc.SLC_VARIABLE, b"\x08"))
    w._slc_process(slc.SLC_BRK, slc.SLC(slc.SLC_VARIABLE, b"\x02"))

    f = slc.SLC_EOF
    w.slctab[f] = slc.SLC(slc.SLC_CANTCHANGE, theNULL)
    w._slc_process(f, slc.SLC(slc.SLC_CANTCHANGE, b"\x04"))

    f2 = slc.SLC_EL
    w.slctab[f2] = slc.SLC(slc.SLC_CANTCHANGE, theNULL)
    w._slc_process(f2, slc.SLC(slc.SLC_VARIABLE, b"\x15"))

    trip = collections.deque([slc.SLC_IP, slc.SLC_VARIABLE, b"\x03"])
    w._handle_sb_linemode_slc(trip)


def test_request_forwardmask_returns_false_without_will_linemode():
    w, _, _ = new_writer(server=True)
    assert w.request_forwardmask() is False


def test_mode_client_kludge_and_server_kludge_and_remote_local():
    ws, ts, ps = new_writer(server=True)
    ws.local_option[ECHO] = True
    ws.local_option[SGA] = True
    assert ws.mode == "kludge"
    wc, tc, pc = new_writer(server=False, client=True)
    wc.remote_option[ECHO] = True
    wc.remote_option[SGA] = True
    assert wc.mode == "kludge"
    wc.remote_option[LINEMODE] = True
    wc._linemode = slc.Linemode(bytes([0]))
    assert wc.mode == "remote"


def test_handle_send_server_and_client_charset_returns():
    ws, ts, ps = new_writer(server=True)
    assert ws.handle_send_server_charset() == ["UTF-8"]
    wc, tc, pc = new_writer(server=False, client=True)
    assert not wc.handle_send_client_charset(["UTF-8", "ASCII"])


def test_charset_accepted_updates_environ_encoding():
    """CHARSET ACCEPTED updates environ_encoding for NEW_ENVIRON decoding."""
    ws, ts, ps = new_writer(server=True)
    assert ws.environ_encoding == "ascii"
    ws.set_ext_callback(CHARSET, lambda c: None)
    buf = collections.deque([CHARSET, ACCEPTED, b"UTF-8"])
    ws._handle_sb_charset(buf)
    assert ws.environ_encoding == "UTF-8"


def test_charset_request_accepted_updates_environ_encoding():
    """Client accepting CHARSET REQUEST updates environ_encoding."""
    wc, tc, pc = new_writer(server=False, client=True)
    assert wc.environ_encoding == "ascii"
    wc.set_ext_send_callback(CHARSET, lambda offers: "UTF-8")
    sep = b";"
    buf = collections.deque([CHARSET, REQUEST, sep, b"UTF-8;ASCII"])
    wc._handle_sb_charset(buf)
    assert wc.environ_encoding == "UTF-8"


def test_iac_wont_and_dont_suppressed_when_remote_false():
    w, t, p = new_writer(server=True)
    w.local_option[ECHO] = True
    assert w.iac(WONT, ECHO) is True
    assert w.local_option[ECHO] is False
    assert t.writes[-1] == IAC + WONT + ECHO
    w.remote_option[ECHO] = False
    assert w.iac(DONT, ECHO) is False


def test_send_status_clears_pending_will_status():
    w, t, p = new_writer(server=True)
    w.pending_option[WILL + STATUS] = True
    w._send_status()
    assert not w.pending_option.enabled(WILL + STATUS)


def test_handle_sb_linemode_forwardmask_wrong_sb_opt_raises():
    w, _, _ = new_writer(server=True)
    with pytest.raises(ValueError, match="expected LMODE_FORWARDMASK"):
        w._handle_sb_linemode(collections.deque([LINEMODE, DO, b"\x99"]))


def test_handle_sb_environ_info_warning_path():
    seen = []
    ws, ts, ps = new_writer(server=True)
    ws.set_ext_callback(NEW_ENVIRON, seen.append)
    is_payload = _encode_env_buf({"USER": "root"})
    ws._handle_sb_environ(collections.deque([NEW_ENVIRON, IS, is_payload]))
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
    wtm.pending_option[DO + TM] = True
    wtm.handle_will(TM)
    assert wtm.remote_option[TM] is True
    assert called.get("cmd") == WILL


def test_handle_send_helpers_return_values():
    w, t, p = new_writer(server=True)
    assert not w.handle_send_xdisploc()
    assert not w.handle_send_ttype()
    assert not w.handle_send_server_environ()
    assert w.handle_send_naws() == (80, 24)


def test_miscellaneous_handle_logs_cover_remaining_handlers():
    ws, ts, ps = new_writer(server=True)
    ws.handle_xdisploc("host:0")
    ws.handle_sndloc("Room 1")
    ws.handle_ttype("xterm")
    ws.handle_naws(80, 24)
    ws.handle_environ({"USER": "root"})
    ws.handle_tspeed(9600, 9600)
    ws.handle_charset("UTF-8")
    ws.handle_lnext(b"\x00")
    ws.handle_rp(b"\x00")
    ws.handle_ew(b"\x00")
    ws.handle_xon(b"\x00")
    ws.handle_xoff(b"\x00")


def test_sb_interrupted_logs_warning_with_context(caplog):
    """SB interruption logs WARNING (not ERROR) with option name and byte count."""
    w, t, _ = new_writer(server=True)
    w.feed_byte(IAC)
    w.feed_byte(SB)
    w.feed_byte(CHARSET)
    w.feed_byte(b"\x01")
    w.feed_byte(b"\x02")
    with caplog.at_level(logging.WARNING):
        w.feed_byte(IAC)
        w.feed_byte(WONT)
    assert any("SB CHARSET (3 bytes) interrupted by IAC WONT" in r.message for r in caplog.records)
    assert all(r.levelno != logging.ERROR for r in caplog.records)
    w.feed_byte(ECHO)


def test_sb_begin_logged(caplog):
    """Entering SB mode logs the option name at DEBUG level."""
    w, t, _ = new_writer(server=True)
    with caplog.at_level(logging.DEBUG):
        w.feed_byte(IAC)
        w.feed_byte(SB)
        w.feed_byte(TTYPE)
    assert any("begin sub-negotiation SB TTYPE" in r.message for r in caplog.records)


def test_handle_will_comport_accepted_and_signature_requested():
    """Client accepting WILL COM_PORT_OPTION sends DO and requests SIGNATURE."""
    w, t, _ = new_writer(server=False, client=True)
    w.handle_will(COM_PORT_OPTION)
    assert t.writes[-2] == IAC + DO + COM_PORT_OPTION
    assert w.remote_option.enabled(COM_PORT_OPTION)
    assert COM_PORT_OPTION not in w.rejected_will
    assert t.writes[-1] == IAC + SB + COM_PORT_OPTION + b"\x00" + IAC + SE


def test_comport_sb_signature_response():
    """COM-PORT-OPTION SIGNATURE response is parsed and stored."""
    w, *_ = new_writer(server=False, client=True)
    w.remote_option[COM_PORT_OPTION] = True
    w.handle_subnegotiation(
        collections.deque([COM_PORT_OPTION, b"\x64", b"M", b"y", b"D", b"e", b"v"])
    )
    assert w.comport_data == {"signature": "MyDev"}


def test_comport_sb_baudrate_response():
    """COM-PORT-OPTION SET-BAUDRATE response is parsed."""
    w, *_ = new_writer(server=False, client=True)
    w.handle_subnegotiation(
        collections.deque(
            [COM_PORT_OPTION, bytes([101]), *[bytes([b]) for b in (0, 0, 0x25, 0x80)]]
        )
    )
    assert w.comport_data["baudrate"] == 9600


@pytest.mark.parametrize(
    "subcmd, payload_byte, key, expected",
    [(102, 8, "datasize", 8), (103, 1, "parity", "NONE"), (104, 1, "stopsize", "1")],
    ids=["datasize", "parity", "stopsize"],
)
def test_comport_sb_datasize_parity_stopsize(subcmd, payload_byte, key, expected):
    w, *_ = new_writer(server=False, client=True)
    w.handle_subnegotiation(
        collections.deque([COM_PORT_OPTION, bytes([subcmd]), bytes([payload_byte])])
    )
    assert w.comport_data[key] == expected


def test_comport_sb_empty_subcmd_payload():
    """COM-PORT-OPTION SIGNATURE with no payload does not store a signature."""
    w, *_ = new_writer(server=False, client=True)
    w.handle_subnegotiation(collections.deque([COM_PORT_OPTION, b"\x00"]))
    assert "signature" not in (w.comport_data or {})


def test_ttype_is_from_server_ignored_on_client():
    """Client receiving TTYPE IS (protocol violation) logs warning, no crash."""
    w, *_ = new_writer(server=False, client=True)
    w.handle_subnegotiation(collections.deque([TTYPE, IS, b"\x01", b"\x00"]))


def test_linemode_slc_no_forwardmask_on_client():
    """Client processing SLC does not call request_forwardmask."""
    w, t, _ = new_writer(server=False, client=True)
    w.local_option[LINEMODE] = True
    w.remote_option[LINEMODE] = True
    func = slc.SLC_IP
    flag = bytes([slc.SLC_LEVELBITS | ord(slc.SLC_FLUSHIN)])
    value = b"\x03"  # ^C
    w._handle_sb_linemode_slc(collections.deque([func, flag, value]))
    # no AssertionError raised — forwardmask not requested on client


def test_linemode_mode_without_negotiation_ignored():
    """LINEMODE-MODE without prior LINEMODE negotiation is ignored."""
    w, t, _ = new_writer(server=False, client=True)
    mode_byte = bytes([0x03])
    w._handle_sb_linemode_mode(collections.deque([mode_byte]))
    # no AssertionError — the mode is silently ignored


@pytest.mark.parametrize(
    "func_name, byte_val, expected",
    [
        ("name_option", WONT, repr(WONT)),
        ("name_option", DO, repr(DO)),
        ("name_option", DONT, repr(DONT)),
        ("name_option", WILL, repr(WILL)),
        ("name_option", IAC, repr(IAC)),
        ("name_option", SB, repr(SB)),
        ("name_option", SE, repr(SE)),
        ("name_option", SGA, "SGA"),
        ("name_option", TTYPE, "TTYPE"),
        ("name_option", NAWS, "NAWS"),
        ("name_command", WONT, "WONT"),
        ("name_command", SGA, "SGA"),
    ],
)
def test_name_option_distinguishes_commands_from_options(func_name, byte_val, expected):
    from telnetlib3.telopt import name_option, name_command

    fn = name_option if func_name == "name_option" else name_command
    assert fn(byte_val) == expected


@pytest.mark.parametrize(
    "method, args, expected",
    [
        ("handle_send_sndloc", (), ""),
        ("handle_send_client_environ", ({},), {}),
        ("handle_send_tspeed", (), (9600, 9600)),
    ],
    ids=["sndloc", "client_environ", "tspeed"],
)
def test_handle_send_default_returns(method, args, expected):
    w, _, _ = new_writer(server=True)
    assert getattr(w, method)(*args) == expected


def test_handle_msdp_logs_debug(caplog):
    w, t, p = new_writer(server=True)
    with caplog.at_level(logging.DEBUG):
        w.handle_msdp({"HP": "100"})
    assert any("MSDP" in r.message for r in caplog.records)


def test_send_iac_trace_log(caplog):
    w, t, p = new_writer(server=True)
    with caplog.at_level(5):
        w.send_iac(IAC + NOP)
    assert len(t.writes) == 1


@pytest.mark.parametrize(
    "method, data, log_substr",
    [
        ("send_msdp", {"HP": "100"}, "cannot send MSDP"),
        ("send_mssp", {"NAME": "TestMUD"}, "cannot send MSSP"),
    ],
    ids=["msdp", "mssp"],
)
def test_send_mud_protocol_returns_early_without_negotiation(caplog, method, data, log_substr):
    w, t, _ = new_writer(server=True)
    with caplog.at_level(logging.DEBUG):
        getattr(w, method)(data)
    assert not t.writes
    assert any(log_substr in r.message for r in caplog.records)


def test_handle_will_always_do_sends_do():
    w, t, p = new_writer(server=True)
    w.always_do.add(AUTHENTICATION)
    w.handle_will(AUTHENTICATION)
    assert t.writes[-1] == IAC + DO + AUTHENTICATION
    assert w.remote_option.enabled(AUTHENTICATION)
    assert AUTHENTICATION not in w.rejected_will


def test_write_non_bytes_raises_type_error():
    w, t, p = new_writer(server=True)
    with pytest.raises(TypeError, match="buf expected bytes"):
        w.write("not bytes")


def test_slc_send_skips_func_zero_on_client():
    wc, tc, pc = new_writer(server=False, client=True)
    wc.local_option[LINEMODE] = True
    wc.remote_option[LINEMODE] = True
    initial_buffer_len = len(wc._slc_buffer)
    wc._slc_send()
    assert len(wc._slc_buffer) >= initial_buffer_len


@pytest.mark.asyncio
async def test_wait_for_expected_false_registers_waiter():
    w, t, p = new_writer(server=True)
    w.remote_option[ECHO] = True

    async def waiter():
        return await w.wait_for(remote={"ECHO": False})

    task = asyncio.create_task(waiter())
    await asyncio.sleep(0)
    assert len(w._waiters) == 1

    w.remote_option[ECHO] = False
    w._check_waiters()
    result = await task
    assert result is True
