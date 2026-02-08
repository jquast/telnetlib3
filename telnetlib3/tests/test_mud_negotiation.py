"""Integration tests for MUD protocol negotiation (GMCP, MSDP, MSSP)."""

# std imports
import collections

# local
from telnetlib3.telopt import DO, SB, SE, IAC, GMCP, MSDP, MSSP, WILL
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


def test_handle_will_gmcp():
    w, t, p = new_writer(server=True)
    w.handle_will(GMCP)
    assert IAC + DO + GMCP in t.writes
    assert w.remote_option.get(GMCP) is True


def test_handle_will_msdp():
    w, t, p = new_writer(server=True)
    w.handle_will(MSDP)
    assert IAC + DO + MSDP in t.writes
    assert w.remote_option.get(MSDP) is True


def test_handle_will_mssp():
    w, t, p = new_writer(server=True)
    w.handle_will(MSSP)
    assert IAC + DO + MSSP in t.writes
    assert w.remote_option.get(MSSP) is True


def test_handle_do_gmcp():
    w, t, p = new_writer(server=True)
    w.handle_do(GMCP)
    assert IAC + WILL + GMCP in t.writes


def test_handle_do_msdp():
    w, t, p = new_writer(server=True)
    w.handle_do(MSDP)
    assert IAC + WILL + MSDP in t.writes


def test_handle_do_mssp():
    w, t, p = new_writer(server=True)
    w.handle_do(MSSP)
    assert IAC + WILL + MSSP in t.writes


def test_set_ext_callback_gmcp():
    w, t, p = new_writer(server=True)
    w.set_ext_callback(GMCP, lambda *a: None)


def test_set_ext_callback_msdp():
    w, t, p = new_writer(server=True)
    w.set_ext_callback(MSDP, lambda *a: None)


def test_set_ext_callback_mssp():
    w, t, p = new_writer(server=True)
    w.set_ext_callback(MSSP, lambda *a: None)


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

    # local
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

    # local
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

    # local
    from telnetlib3.telopt import MSSP_VAL, MSSP_VAR

    payload = MSSP_VAR + b"NAME" + MSSP_VAL + b"TestMUD" + MSSP_VAR + b"PLAYERS" + MSSP_VAL + b"5"
    buf = collections.deque([bytes([MSSP[0]])] + [bytes([b]) for b in payload])
    w.handle_subnegotiation(buf)
    assert w.mssp_data == {"NAME": "TestMUD", "PLAYERS": "5"}


def test_sb_mssp_latin1_fallback():
    """MSSP with non-UTF-8 bytes falls back to latin-1 decoding."""
    w, t, p = new_writer(server=True)
    w.pending_option[SB + MSSP] = True

    # local
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

    # local
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

    # local
    from telnetlib3.telopt import MSDP_VAL, MSDP_VAR

    w.send_msdp({"HEALTH": "100"})
    expected = IAC + SB + MSDP + MSDP_VAR + b"HEALTH" + MSDP_VAL + b"100" + IAC + SE
    assert expected in t.writes


def test_send_mssp():
    w, t, p = new_writer(server=True)
    w.local_option[MSSP] = True

    # local
    from telnetlib3.telopt import MSSP_VAL, MSSP_VAR

    w.send_mssp({"NAME": "TestMUD"})
    expected = IAC + SB + MSSP + MSSP_VAR + b"NAME" + MSSP_VAL + b"TestMUD" + IAC + SE
    assert expected in t.writes
