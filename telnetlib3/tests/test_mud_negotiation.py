"""Integration tests for MUD protocol negotiation (GMCP, MSDP, MSSP, MXP, etc.)."""

# std imports
import collections

# 3rd party
import pytest

# local
from telnetlib3.telopt import (
    DO,
    SB,
    SE,
    IAC,
    MSP,
    MXP,
    ZMP,
    ATCP,
    DONT,
    GMCP,
    MSDP,
    MSSP,
    WILL,
    WONT,
    AARDWOLF,
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


class ProtocolBase:
    def __init__(self, info=None):
        self.info = info or {}
        self.drain_called = False
        self.conn_lost_called = False

    def get_extra_info(self, name, default=None):
        return self.info.get(name, default)

    async def _drain_helper(self):
        self.drain_called = True

    def connection_lost(self, exc):
        self.conn_lost_called = True


def new_writer(server=True, client=False, reader=None):
    t = MockTransport()
    p = ProtocolBase()
    w = TelnetWriter(t, p, server=server, client=client, reader=reader)
    return w, t, p


_MUD_CORE = [GMCP, MSDP, MSSP]
_MUD_CORE_IDS = ["GMCP", "MSDP", "MSSP"]


@pytest.mark.parametrize("opt", _MUD_CORE, ids=_MUD_CORE_IDS)
def test_handle_will_core(opt):
    w, t, _p = new_writer(server=True)
    w.handle_will(opt)
    assert IAC + DO + opt in t.writes
    assert w.remote_option.get(opt) is True


@pytest.mark.parametrize("opt", _MUD_CORE, ids=_MUD_CORE_IDS)
def test_handle_do_core(opt):
    w, t, _p = new_writer(server=True)
    w.handle_do(opt)
    assert IAC + WILL + opt in t.writes


@pytest.mark.parametrize("opt", _MUD_CORE, ids=_MUD_CORE_IDS)
def test_set_ext_callback_core(opt):
    w, _t, _p = new_writer(server=True)
    w.set_ext_callback(opt, lambda *a: None)


def test_sb_gmcp_dispatch():
    w, t, p = new_writer(server=True)
    received_args = []

    def callback(package, data):
        received_args.append((package, data))

    w.set_ext_callback(GMCP, callback)
    w.pending_option[SB + GMCP] = True

    payload = b'Char.Vitals {"hp": 100}'
    buf = collections.deque([bytes([GMCP[0]])] + [bytes([b]) for b in payload])
    w.handle_subnegotiation(buf)

    assert len(received_args) == 1
    assert received_args[0][0] == "Char.Vitals"
    assert received_args[0][1] == {"hp": 100}


def test_sb_msdp_dispatch():
    w, t, p = new_writer(server=True)
    received_args = []

    def callback(variables):
        received_args.append(variables)

    w.set_ext_callback(MSDP, callback)
    w.pending_option[SB + MSDP] = True

    from telnetlib3.telopt import MSDP_VAL, MSDP_VAR

    payload = MSDP_VAR + b"HEALTH" + MSDP_VAL + b"100"
    buf = collections.deque([bytes([MSDP[0]])] + [bytes([b]) for b in payload])
    w.handle_subnegotiation(buf)

    assert len(received_args) == 1
    assert received_args[0] == {"HEALTH": "100"}


def test_sb_mssp_dispatch():
    w, t, p = new_writer(server=True)
    received_args = []

    def callback(variables):
        received_args.append(variables)

    w.set_ext_callback(MSSP, callback)
    w.pending_option[SB + MSSP] = True

    from telnetlib3.telopt import MSSP_VAL, MSSP_VAR

    payload = MSSP_VAR + b"NAME" + MSSP_VAL + b"TestMUD"
    buf = collections.deque([bytes([MSSP[0]])] + [bytes([b]) for b in payload])
    w.handle_subnegotiation(buf)

    assert len(received_args) == 1
    assert received_args[0] == {"NAME": "TestMUD"}


def test_handle_mssp_stores_data():
    w, t, p = new_writer(server=True)
    assert w.mssp_data is None
    w.handle_mssp({"NAME": "TestMUD", "PLAYERS": "42"})
    assert w.mssp_data == {"NAME": "TestMUD", "PLAYERS": "42"}


def test_sb_mssp_dispatch_stores_data():
    w, t, p = new_writer(server=True)
    w.pending_option[SB + MSSP] = True

    from telnetlib3.telopt import MSSP_VAL, MSSP_VAR

    payload = MSSP_VAR + b"NAME" + MSSP_VAL + b"TestMUD" + MSSP_VAR + b"PLAYERS" + MSSP_VAL + b"5"
    buf = collections.deque([bytes([MSSP[0]])] + [bytes([b]) for b in payload])
    w.handle_subnegotiation(buf)
    assert w.mssp_data == {"NAME": "TestMUD", "PLAYERS": "5"}


def test_sb_mssp_latin1_fallback():
    """MSSP with non-UTF-8 bytes falls back to latin-1 decoding."""
    w, t, p = new_writer(server=True)
    w.pending_option[SB + MSSP] = True

    from telnetlib3.telopt import MSSP_VAL, MSSP_VAR

    # 0xC9 is 'É' in latin-1 but invalid as a lone UTF-8 lead byte
    payload = MSSP_VAR + b"NAME" + MSSP_VAL + b"\xc9toile"
    buf = collections.deque([bytes([MSSP[0]])] + [bytes([b]) for b in payload])
    w.handle_subnegotiation(buf)
    assert w.mssp_data == {"NAME": "\xc9toile"}


def test_sb_gmcp_latin1_fallback():
    """GMCP with non-UTF-8 bytes falls back to latin-1 decoding."""
    w, t, p = new_writer(server=True)
    w.pending_option[SB + GMCP] = True
    received_args: list[tuple[object, ...]] = []
    w.set_ext_callback(GMCP, lambda pkg, data: received_args.append((pkg, data)))
    payload = b"Caf\xe9"
    buf = collections.deque([bytes([GMCP[0]])] + [bytes([b]) for b in payload])
    w.handle_subnegotiation(buf)
    assert received_args[0] == ("Caf\xe9", None)


def test_sb_msdp_latin1_fallback():
    """MSDP with non-UTF-8 bytes falls back to latin-1 decoding."""
    w, t, p = new_writer(server=True)
    w.pending_option[SB + MSDP] = True

    from telnetlib3.telopt import MSDP_VAL, MSDP_VAR

    received_args: list[object] = []
    w.set_ext_callback(MSDP, received_args.append)
    payload = MSDP_VAR + b"KEY" + MSDP_VAL + b"Caf\xe9"
    buf = collections.deque([bytes([MSDP[0]])] + [bytes([b]) for b in payload])
    w.handle_subnegotiation(buf)
    assert received_args[0] == {"KEY": "Caf\xe9"}


def test_send_gmcp():
    w, t, p = new_writer(server=True)
    w.local_option[GMCP] = True
    w.send_gmcp("Char.Vitals", {"hp": 100})
    expected = IAC + SB + GMCP + b'Char.Vitals {"hp":100}' + IAC + SE
    assert expected in t.writes


def test_send_gmcp_not_negotiated():
    w, t, p = new_writer(server=True)
    w.send_gmcp("Char.Vitals", {"hp": 100})
    assert len(t.writes) == 0


def test_send_msdp():
    w, t, p = new_writer(server=True)
    w.local_option[MSDP] = True

    from telnetlib3.telopt import MSDP_VAL, MSDP_VAR

    w.send_msdp({"HEALTH": "100"})
    expected = IAC + SB + MSDP + MSDP_VAR + b"HEALTH" + MSDP_VAL + b"100" + IAC + SE
    assert expected in t.writes


def test_send_mssp():
    w, t, p = new_writer(server=True)
    w.local_option[MSSP] = True

    from telnetlib3.telopt import MSSP_VAL, MSSP_VAR

    w.send_mssp({"NAME": "TestMUD"})
    expected = IAC + SB + MSSP + MSSP_VAR + b"NAME" + MSSP_VAL + b"TestMUD" + IAC + SE
    assert expected in t.writes


_MUD_EXTENDED = [MSP, MXP, ZMP, AARDWOLF, ATCP]
_MUD_EXT_IDS = ["MSP", "MXP", "ZMP", "AARDWOLF", "ATCP"]


@pytest.mark.parametrize("opt", _MUD_EXTENDED, ids=_MUD_EXT_IDS)
def test_handle_will_mud_extended(opt):
    w, t, _p = new_writer(server=True)
    w.handle_will(opt)
    assert IAC + DO + opt in t.writes
    assert w.remote_option.get(opt) is True


@pytest.mark.parametrize("opt", _MUD_EXTENDED, ids=_MUD_EXT_IDS)
def test_handle_do_mud_extended(opt):
    w, t, _p = new_writer(server=True)
    w.handle_do(opt)
    assert IAC + WILL + opt in t.writes


@pytest.mark.parametrize("opt", _MUD_EXTENDED, ids=_MUD_EXT_IDS)
def test_set_ext_callback_mud_extended(opt):
    w, _t, _p = new_writer(server=True)
    w.set_ext_callback(opt, lambda *a: None)


@pytest.mark.parametrize("opt", [MSP, MXP], ids=["MSP", "MXP"])
def test_sb_raw_mud_empty_payload(opt):
    """Empty SB payload (e.g. IAC SB MXP IAC SE) must not raise."""
    w, _t, _p = new_writer(server=True)
    received: list[bytes] = []
    w.set_ext_callback(opt, received.append)
    w.pending_option[SB + opt] = True
    buf = collections.deque([bytes([opt[0]])])
    w.handle_subnegotiation(buf)
    assert received == [b""]


@pytest.mark.parametrize("opt", [MSP, MXP], ids=["MSP", "MXP"])
def test_sb_raw_mud_with_payload(opt):
    w, _t, _p = new_writer(server=True)
    received: list[bytes] = []
    w.set_ext_callback(opt, received.append)
    w.pending_option[SB + opt] = True
    payload = b"\x01\x02\x03"
    buf = collections.deque([bytes([opt[0]])] + [bytes([b]) for b in payload])
    w.handle_subnegotiation(buf)
    assert received == [payload]


def test_mxp_data_stored_on_empty_sb():
    w, _t, _p = new_writer(server=True)
    w.pending_option[SB + MXP] = True
    buf = collections.deque([bytes([MXP[0]])])
    w.handle_subnegotiation(buf)
    assert w.mxp_data == [b""]


def test_mxp_data_stored_with_payload():
    w, _t, _p = new_writer(server=True)
    w.pending_option[SB + MXP] = True
    payload = b"\x01\x02\x03"
    buf = collections.deque([bytes([MXP[0]])] + [bytes([b]) for b in payload])
    w.handle_subnegotiation(buf)
    assert w.mxp_data == [payload]


def test_mxp_data_accumulates():
    w, _t, _p = new_writer(server=True)
    w.pending_option[SB + MXP] = True
    buf1 = collections.deque([bytes([MXP[0]])])
    w.handle_subnegotiation(buf1)
    w.pending_option[SB + MXP] = True
    payload = b"\x01\x02"
    buf2 = collections.deque([bytes([MXP[0]])] + [bytes([b]) for b in payload])
    w.handle_subnegotiation(buf2)
    assert w.mxp_data == [b"", payload]


def test_handle_will_mxp_sets_pending_sb():
    w, t, _p = new_writer(server=True)
    w.handle_will(MXP)
    assert IAC + DO + MXP in t.writes
    assert w.pending_option.get(SB + MXP) is True


def test_handle_do_mxp_sets_pending_sb():
    w, t, _p = new_writer(server=True)
    w.handle_do(MXP)
    assert IAC + WILL + MXP in t.writes
    assert w.pending_option.get(SB + MXP) is True


def test_handle_will_mxp_client_declines():
    w, t, _p = new_writer(server=False, client=True)
    w.handle_will(MXP)
    assert IAC + DONT + MXP in t.writes
    assert w.remote_option.get(MXP) is not True


def test_handle_will_mxp_client_always_do():
    w, t, _p = new_writer(server=False, client=True)
    w.always_do.add(MXP)
    w.handle_will(MXP)
    assert IAC + DO + MXP in t.writes
    assert w.remote_option.get(MXP) is True


_MUD_ALL = [GMCP, MSDP, MSSP, MSP, MXP, ZMP, AARDWOLF, ATCP]
_MUD_ALL_IDS = ["GMCP", "MSDP", "MSSP", "MSP", "MXP", "ZMP", "AARDWOLF", "ATCP"]


@pytest.mark.parametrize("opt", _MUD_ALL, ids=_MUD_ALL_IDS)
def test_handle_will_mud_client_declines(opt):
    w, t, _p = new_writer(server=False, client=True)
    w.handle_will(opt)
    assert IAC + DONT + opt in t.writes


@pytest.mark.parametrize("opt", _MUD_ALL, ids=_MUD_ALL_IDS)
def test_handle_do_mud_client_declines(opt):
    w, t, _p = new_writer(server=False, client=True)
    result = w.handle_do(opt)
    assert result is False
    assert IAC + WONT + opt in t.writes


@pytest.mark.parametrize("opt", _MUD_ALL, ids=_MUD_ALL_IDS)
def test_handle_do_mud_client_always_will(opt):
    w, t, _p = new_writer(server=False, client=True)
    w.always_will.add(opt)
    result = w.handle_do(opt)
    assert result is True
    assert IAC + WILL + opt in t.writes


def test_sb_zmp_dispatch():
    w, _t, _p = new_writer(server=True)
    w.pending_option[SB + ZMP] = True
    payload = b"zmp.ident\x00MudName\x001.0\x00A test MUD\x00"
    buf = collections.deque([bytes([ZMP[0]])] + [bytes([b]) for b in payload])
    w.handle_subnegotiation(buf)
    assert w.zmp_data == [["zmp.ident", "MudName", "1.0", "A test MUD"]]


def test_sb_zmp_empty_payload():
    w, _t, _p = new_writer(server=True)
    w.pending_option[SB + ZMP] = True
    buf = collections.deque([bytes([ZMP[0]])])
    w.handle_subnegotiation(buf)
    assert w.zmp_data == [[]]


def test_sb_zmp_accumulates():
    w, _t, _p = new_writer(server=True)
    w.pending_option[SB + ZMP] = True
    buf1 = collections.deque([bytes([ZMP[0]])] + [bytes([b]) for b in b"zmp.ping\x00"])
    w.handle_subnegotiation(buf1)
    w.pending_option[SB + ZMP] = True
    buf2 = collections.deque([bytes([ZMP[0]])] + [bytes([b]) for b in b"zmp.check\x00zmp.ping\x00"])
    w.handle_subnegotiation(buf2)
    assert len(w.zmp_data) == 2
    assert w.zmp_data[0] == ["zmp.ping"]
    assert w.zmp_data[1] == ["zmp.check", "zmp.ping"]


def test_sb_atcp_dispatch():
    w, _t, _p = new_writer(server=True)
    w.pending_option[SB + ATCP] = True
    payload = b"Room.Exits ne,sw,nw"
    buf = collections.deque([bytes([ATCP[0]])] + [bytes([b]) for b in payload])
    w.handle_subnegotiation(buf)
    assert w.atcp_data == [("Room.Exits", "ne,sw,nw")]


def test_sb_atcp_no_value():
    w, _t, _p = new_writer(server=True)
    w.pending_option[SB + ATCP] = True
    payload = b"Conn.MXP"
    buf = collections.deque([bytes([ATCP[0]])] + [bytes([b]) for b in payload])
    w.handle_subnegotiation(buf)
    assert w.atcp_data == [("Conn.MXP", "")]


def test_sb_atcp_empty_payload():
    w, _t, _p = new_writer(server=True)
    w.pending_option[SB + ATCP] = True
    buf = collections.deque([bytes([ATCP[0]])])
    w.handle_subnegotiation(buf)
    assert w.atcp_data == [("", "")]


def test_sb_aardwolf_dispatch():
    w, _t, _p = new_writer(server=True)
    w.pending_option[SB + AARDWOLF] = True
    payload = bytes([100, 3])
    buf = collections.deque([bytes([AARDWOLF[0]])] + [bytes([b]) for b in payload])
    w.handle_subnegotiation(buf)
    assert len(w.aardwolf_data) == 1
    assert w.aardwolf_data[0]["channel"] == "status"
    assert w.aardwolf_data[0]["data_byte"] == 3


def test_sb_aardwolf_tick():
    w, _t, _p = new_writer(server=True)
    w.pending_option[SB + AARDWOLF] = True
    payload = bytes([101, 1])
    buf = collections.deque([bytes([AARDWOLF[0]])] + [bytes([b]) for b in payload])
    w.handle_subnegotiation(buf)
    assert w.aardwolf_data[0]["channel"] == "tick"
    assert w.aardwolf_data[0]["data_byte"] == 1


def test_sb_aardwolf_empty_payload():
    w, _t, _p = new_writer(server=True)
    w.pending_option[SB + AARDWOLF] = True
    buf = collections.deque([bytes([AARDWOLF[0]])])
    w.handle_subnegotiation(buf)
    assert w.aardwolf_data[0]["channel"] == "unknown"
