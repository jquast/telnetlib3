"""Test CHARSET, rfc-2066_."""

# std imports
import asyncio
import collections

# local imports
import telnetlib3
import telnetlib3.stream_writer
from telnetlib3.tests.accessories import unused_tcp_port, bind_host
from telnetlib3.stream_writer import TelnetWriter

# 3rd party
import pytest


async def test_telnet_server_on_charset(bind_host, unused_tcp_port):
    """Test Server's callback method on_charset()."""
    # given
    from telnetlib3.telopt import IAC, WILL, WONT, SB, SE, TTYPE, CHARSET, ACCEPTED

    _waiter = asyncio.Future()
    given_charset = "KOI8-U"

    class ServerTestCharset(telnetlib3.TelnetServer):
        def on_charset(self, charset):
            super().on_charset(charset)
            _waiter.set_result(self)

    await telnetlib3.create_server(
        protocol_factory=ServerTestCharset, host=bind_host, port=unused_tcp_port
    )

    reader, writer = await asyncio.open_connection(host=bind_host, port=unused_tcp_port)

    val = await asyncio.wait_for(reader.readexactly(3), 0.5)
    # exercise,
    writer.write(IAC + WILL + CHARSET)
    writer.write(IAC + WONT + TTYPE)
    writer.write(
        IAC + SB + CHARSET + ACCEPTED + given_charset.encode("ascii") + IAC + SE
    )

    # verify,
    srv_instance = await asyncio.wait_for(_waiter, 2.0)
    assert srv_instance.get_extra_info("charset") == given_charset


async def test_telnet_client_send_charset(bind_host, unused_tcp_port):
    """Test Client's callback method send_charset() selection for illegals."""
    # given
    _waiter = asyncio.Future()

    class ServerTestCharset(telnetlib3.TelnetServer):
        def on_request_charset(self):
            return ["illegal", "cp437"]

    class ClientTestCharset(telnetlib3.TelnetClient):
        def send_charset(self, offered):
            selected = super().send_charset(offered)
            _waiter.set_result(selected)
            return selected

    await asyncio.wait_for(
        telnetlib3.create_server(
            protocol_factory=ServerTestCharset, host=bind_host, port=unused_tcp_port
        ),
        0.15,
    )

    reader, writer = await asyncio.wait_for(
        telnetlib3.open_connection(
            client_factory=ClientTestCharset,
            host=bind_host,
            port=unused_tcp_port,
            encoding="latin1",
            connect_minwait=0.05,
        ),
        0.15,
    )

    val = await asyncio.wait_for(_waiter, 1.5)
    assert val == "cp437"
    assert writer.get_extra_info("charset") == "cp437"


async def test_telnet_client_no_charset(bind_host, unused_tcp_port):
    """Test Client's callback method send_charset() does not select."""
    # given
    _waiter = asyncio.Future()

    class ServerTestCharset(telnetlib3.TelnetServer):
        def on_request_charset(self):
            return ["illegal", "this-is-no-good-either"]

    class ClientTestCharset(telnetlib3.TelnetClient):
        def send_charset(self, offered):
            selected = super().send_charset(offered)
            _waiter.set_result(selected)
            return selected

    await telnetlib3.create_server(
        protocol_factory=ServerTestCharset,
        host=bind_host,
        port=unused_tcp_port,
    )

    reader, writer = await telnetlib3.open_connection(
        client_factory=ClientTestCharset,
        host=bind_host,
        port=unused_tcp_port,
        encoding="latin1",
        connect_minwait=0.05,
    )

    # charset remains latin1
    val = await asyncio.wait_for(_waiter, 0.5)
    assert val == ""
    assert writer.get_extra_info("charset") == "latin1"


class MockTransport:
    def __init__(self):
        self.writes = []
        self._closing = False

    def write(self, data):
        self.writes.append(bytes(data))

    def is_closing(self):
        return self._closing

    def close(self):
        self._closing = True

    def get_extra_info(self, name, default=None):
        return default


class MockProtocol:
    def get_extra_info(self, name, default=None):
        return default

    async def _drain_helper(self):
        pass


def new_writer(server=True, client=False):
    t = MockTransport()
    p = MockProtocol()
    w = TelnetWriter(t, p, server=server, client=client)
    return w, t, p


def test_server_sends_do_and_will_charset():
    """Test server can send both DO CHARSET and WILL CHARSET per RFC 2066."""
    from telnetlib3.telopt import (
        IAC,
        DO,
        WILL,
        CHARSET,
    )

    ws, ts, _ = new_writer(server=True)

    # Server sends DO CHARSET (requesting client capability)
    assert ws.iac(DO, CHARSET) is True
    assert ts.writes[-1] == IAC + DO + CHARSET

    # Server also sends WILL CHARSET (advertising its own capability)
    assert ws.iac(WILL, CHARSET) is True
    assert ts.writes[-1] == IAC + WILL + CHARSET


def test_client_do_will_then_server_will_allows_client_request():
    """Test scenario from logfile: DO->WILL then server WILL should allow client SB REQUEST."""
    from telnetlib3.telopt import (
        IAC,
        WILL,
        SB,
        CHARSET,
        REQUEST,
    )

    wc, tc, _ = new_writer(server=False, client=True)
    wc.set_ext_send_callback(CHARSET, lambda: ["UTF-8"])

    # Simulate server DO CHARSET
    # Note: handle_do() returns True but local_option[...] is set by the caller
    # path in feed_byte(); set it explicitly here for the unit test.
    wc.handle_do(CHARSET)
    wc.local_option[CHARSET] = True
    assert tc.writes[-1] == IAC + WILL + CHARSET

    # Simulate server WILL CHARSET (the "unsolicited" one from logfile)
    wc.handle_will(CHARSET)
    assert wc.remote_option[CHARSET] is True

    # Now client should have sent SB CHARSET REQUEST automatically
    # when receiving WILL CHARSET from server (per implementation).
    assert tc.writes[-1].startswith(IAC + SB + CHARSET + REQUEST)


def test_bidirectional_charset_both_sides_can_request():
    """Test that both server and client can initiate CHARSET REQUEST when both have WILL/DO."""
    from telnetlib3.telopt import IAC, SB, CHARSET, REQUEST

    # Server side
    ws, ts, _ = new_writer(server=True)
    ws.set_ext_send_callback(CHARSET, lambda: ["UTF-8", "ASCII"])

    # Client side
    wc, tc, _ = new_writer(server=False, client=True)
    wc.set_ext_send_callback(CHARSET, lambda: ["UTF-8"])

    # Simulate full negotiation: server DO, client WILL, server WILL, client DO
    ws.remote_option[CHARSET] = True  # client sent WILL
    wc.local_option[CHARSET] = True  # client sent WILL/received DO
    wc.remote_option[CHARSET] = True  # server sent WILL
    ws.local_option[CHARSET] = True  # server sent WILL/received DO

    # Both sides should be able to initiate REQUEST
    assert ws.request_charset() is True
    assert wc.request_charset() is True

    # Verify both sent REQUEST frames
    assert ts.writes[-1].startswith(IAC + SB + CHARSET + REQUEST)
    assert tc.writes[-1].startswith(IAC + SB + CHARSET + REQUEST)


def test_charset_request_response_cycle():
    """Test complete CHARSET REQUEST/ACCEPTED cycle."""
    from telnetlib3.telopt import IAC, SB, CHARSET, REQUEST, ACCEPTED

    # Server initiates REQUEST
    ws, ts, _ = new_writer(server=True)
    ws.remote_option[CHARSET] = True
    ws.set_ext_send_callback(CHARSET, lambda: ["UTF-8", "ASCII"])

    assert ws.request_charset() is True
    request_frame = ts.writes[-1]
    assert request_frame.startswith(IAC + SB + CHARSET + REQUEST)

    # Client responds with ACCEPTED (server should only invoke callback, not send)
    charset_selected = "UTF-8"
    response_buf = collections.deque(
        [CHARSET, ACCEPTED, charset_selected.encode("ascii")]
    )
    seen = {}
    ws.set_ext_callback(CHARSET, lambda cs: seen.setdefault("cs", cs))
    ws._handle_sb_charset(response_buf)
    assert seen.get("cs") == "UTF-8"
