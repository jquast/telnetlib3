"""Test CHARSET, rfc-2066_."""

# std imports
import asyncio
import collections

# local
import telnetlib3
import telnetlib3.stream_writer
from telnetlib3.telopt import (
    DO,
    SB,
    SE,
    IAC,
    WILL,
    WONT,
    TTYPE,
    CHARSET,
    REQUEST,
    ACCEPTED,
)
from telnetlib3.stream_writer import TelnetWriter
from telnetlib3.tests.accessories import (  # pylint: disable=unused-import
    bind_host,
    unused_tcp_port,
)

# --- Common Mock Classes ---


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


class CustomTelnetClient(telnetlib3.TelnetClient):
    """Test client with controlled send_charset() behavior."""

    def __init__(self, *args, **kwargs):
        self.charset_behavior = kwargs.pop("charset_behavior", None)
        self.charset_response = kwargs.pop("charset_response", None)
        super().__init__(*args, **kwargs)

    def send_charset(self, offered):
        """Override to allow testing specific behavior branches."""
        if self.charset_behavior == "unknown_encoding":
            # Test LookupError handling with explicit encoding
            self.default_encoding = "unknown-encoding-xyz"
            return super().send_charset(offered)
        if self.charset_behavior == "no_viable_offers":
            # Return empty offers list to test no viable offers path
            return super().send_charset([])
        if self.charset_behavior == "explicit_non_latin1":
            # Test rejection when explicit encoding isn't offered
            self.default_encoding = "utf-16"
            return super().send_charset(["utf-8", "ascii"])
        if self.charset_response is not None:
            # Return a predetermined response
            return self.charset_response
        return super().send_charset(offered)


# --- Basic CHARSET Tests ---


async def test_telnet_server_on_charset(bind_host, unused_tcp_port):
    """Test Server's callback method on_charset()."""
    # local
    from telnetlib3.tests.accessories import create_server, asyncio_connection

    _waiter = asyncio.Future()
    given_charset = "KOI8-U"

    class ServerTestCharset(telnetlib3.TelnetServer):
        def on_charset(self, charset):
            super().on_charset(charset)
            _waiter.set_result(self)

    async with create_server(
        protocol_factory=ServerTestCharset, host=bind_host, port=unused_tcp_port
    ):
        async with asyncio_connection(bind_host, unused_tcp_port) as (reader, writer):
            await asyncio.wait_for(reader.readexactly(3), 0.5)
            writer.write(IAC + WILL + CHARSET)
            writer.write(IAC + WONT + TTYPE)
            writer.write(IAC + SB + CHARSET + ACCEPTED + given_charset.encode("ascii") + IAC + SE)

            srv_instance = await asyncio.wait_for(_waiter, 2.0)
            assert srv_instance.get_extra_info("charset") == given_charset

            srv_instance.writer.close()
            await srv_instance.writer.wait_closed()


async def test_telnet_client_send_charset(bind_host, unused_tcp_port):
    """Test Client's callback method send_charset() selection for illegals."""
    # local
    from telnetlib3.tests.accessories import create_server, open_connection

    _waiter = asyncio.Future()
    server_instance = {"protocol": None}

    class ServerTestCharset(telnetlib3.TelnetServer):
        def begin_negotiation(self):
            server_instance["protocol"] = self
            return super().begin_negotiation()

        def on_request_charset(self):
            return ["illegal", "cp437"]

    class ClientTestCharset(telnetlib3.TelnetClient):
        def send_charset(self, offered):
            selected = super().send_charset(offered)
            _waiter.set_result(selected)
            return selected

    async with create_server(
        protocol_factory=ServerTestCharset, host=bind_host, port=unused_tcp_port
    ):
        async with open_connection(
            client_factory=ClientTestCharset,
            host=bind_host,
            port=unused_tcp_port,
            encoding="latin1",
            connect_minwait=0.05,
        ) as (reader, writer):
            val = await asyncio.wait_for(_waiter, 1.5)
            assert val == "cp437"
            assert writer.get_extra_info("charset") == "cp437"

            if server_instance["protocol"]:
                server_instance["protocol"].writer.close()
                await server_instance["protocol"].writer.wait_closed()


async def test_telnet_client_no_charset(bind_host, unused_tcp_port):
    """Test Client's callback method send_charset() does not select."""
    # local
    from telnetlib3.tests.accessories import create_server, open_connection

    _waiter = asyncio.Future()
    server_instance = {"protocol": None}

    class ServerTestCharset(telnetlib3.TelnetServer):
        def begin_negotiation(self):
            server_instance["protocol"] = self
            return super().begin_negotiation()

        def on_request_charset(self):
            return ["illegal", "this-is-no-good-either"]

    class ClientTestCharset(telnetlib3.TelnetClient):
        def send_charset(self, offered):
            selected = super().send_charset(offered)
            _waiter.set_result(selected)
            return selected

    async with create_server(
        protocol_factory=ServerTestCharset,
        host=bind_host,
        port=unused_tcp_port,
    ):
        async with open_connection(
            client_factory=ClientTestCharset,
            host=bind_host,
            port=unused_tcp_port,
            encoding="latin1",
            connect_minwait=0.05,
        ) as (reader, writer):
            val = await asyncio.wait_for(_waiter, 0.5)
            assert not val
            assert writer.get_extra_info("charset") == "latin1"

            if server_instance["protocol"]:
                server_instance["protocol"].writer.close()
                await server_instance["protocol"].writer.wait_closed()


# --- Negotiation Protocol Tests ---


def test_server_sends_do_and_will_charset():
    """Test server can send both DO CHARSET and WILL CHARSET per RFC 2066."""
    ws, ts, _ = new_writer(server=True)

    # Server sends DO CHARSET (requesting client capability)
    assert ws.iac(DO, CHARSET) is True
    assert ts.writes[-1] == IAC + DO + CHARSET

    # Server also sends WILL CHARSET (advertising its own capability)
    assert ws.iac(WILL, CHARSET) is True
    assert ts.writes[-1] == IAC + WILL + CHARSET


def test_client_do_will_then_server_will_allows_client_request():
    """Test scenario from logfile: DO->WILL then server WILL allows client to send SB REQUEST."""
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

    # Client should NOT automatically send SB CHARSET REQUEST,
    # but should be able to send one manually
    tc.writes.clear()

    # Verify client can send a request now that both WILL/DO are established
    assert wc.request_charset() is True
    assert tc.writes[-1].startswith(IAC + SB + CHARSET + REQUEST)


def test_bidirectional_charset_both_sides_can_request():
    """Test that both server and client can initiate CHARSET REQUEST when both have WILL/DO."""
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
    # Server initiates REQUEST
    ws, ts, _ = new_writer(server=True)
    ws.remote_option[CHARSET] = True
    ws.set_ext_send_callback(CHARSET, lambda: ["UTF-8", "ASCII"])

    assert ws.request_charset() is True
    request_frame = ts.writes[-1]
    assert request_frame.startswith(IAC + SB + CHARSET + REQUEST)

    # Client responds with ACCEPTED (server should only invoke callback, not send)
    charset_selected = "UTF-8"
    response_buf = collections.deque([CHARSET, ACCEPTED, charset_selected.encode("ascii")])
    seen = {}
    ws.set_ext_callback(CHARSET, lambda cs: seen.setdefault("cs", cs))
    ws._handle_sb_charset(response_buf)
    assert seen.get("cs") == "UTF-8"


def test_server_sends_will_charset_after_client_will():
    """Test server sends WILL CHARSET after receiving WILL CHARSET from client."""
    ws, ts, _ = new_writer(server=True)

    # Server has not yet sent WILL CHARSET
    assert not ws.local_option.enabled(CHARSET)

    # Simulate client sending WILL CHARSET
    ws.handle_will(CHARSET)

    # Verify server sent WILL CHARSET in response
    assert IAC + WILL + CHARSET in ts.writes

    # Verify server also called request_charset as usual
    # (this is tested by checking if it would send a request,
    # but we need to set up the callback first)
    ws.set_ext_send_callback(CHARSET, lambda: ["UTF-8"])
    # Clear previous writes to test just the request
    ts.writes.clear()

    # The handle_will should have also called request_charset
    # Since remote_option[CHARSET] is now True from handle_will
    assert ws.remote_option.enabled(CHARSET)


def test_server_does_not_send_duplicate_will_charset():
    """Test server doesn't send WILL CHARSET if already sent."""
    ws, ts, _ = new_writer(server=True)

    # Server has already sent WILL CHARSET
    ws.local_option[CHARSET] = True

    # Clear any previous writes
    ts.writes.clear()

    # Simulate client sending WILL CHARSET
    ws.handle_will(CHARSET)

    # Verify server did NOT send WILL CHARSET again
    assert IAC + WILL + CHARSET not in ts.writes

    # But remote option should still be set
    assert ws.remote_option.enabled(CHARSET)


# --- Bug Fix Tests ---


def test_client_responds_with_do_to_will_charset():
    """Test client responds with DO CHARSET when receiving WILL CHARSET from server."""
    # Create client writer instance
    transport = MockTransport()
    protocol = MockProtocol()
    client_writer = TelnetWriter(transport, protocol, client=True, server=False)

    # Simulate server sending WILL CHARSET
    client_writer.handle_will(CHARSET)

    # Verify client sent DO CHARSET in response
    # The fix ensures this happens automatically in handle_will
    sent_do_charset = False
    for write in transport.writes:
        if write == IAC + DO + CHARSET:
            sent_do_charset = True
            break

    assert sent_do_charset, "Client did not send IAC DO CHARSET in response to IAC WILL CHARSET"
    assert client_writer.remote_option.enabled(
        CHARSET
    ), "Client did not enable remote_option[CHARSET]"


def test_unit_charset_negotiation_sequence():
    """Unit test for the CHARSET negotiation sequence with the fixed code."""
    # Create mock server and client
    server_transport = MockTransport()
    server_protocol = MockProtocol()
    server_writer = TelnetWriter(server_transport, server_protocol, server=True)

    client_transport = MockTransport()
    client_protocol = MockProtocol()
    client_writer = TelnetWriter(client_transport, client_protocol, client=True)

    # Simulate the exact bug scenario:
    # 1. Server sends IAC DO CHARSET
    server_writer.iac(DO, CHARSET)
    assert server_transport.writes[-1] == IAC + DO + CHARSET

    # 2. Client receives IAC DO CHARSET and responds with IAC WILL CHARSET
    client_writer.handle_do(CHARSET)
    client_writer.local_option[CHARSET] = True  # Simulating the full code path
    assert client_transport.writes[-1] == IAC + WILL + CHARSET

    # 3. Server receives IAC WILL CHARSET
    server_writer.handle_will(CHARSET)
    assert server_writer.remote_option.enabled(CHARSET)

    # Server should respond with its own IAC WILL CHARSET (bi-directional exchange)
    # Note: The server also immediately sends CHARSET REQUEST after WILL CHARSET
    # when both sides have CHARSET capability, so we need to check if WILL CHARSET is in the writes
    will_charset_sent = False
    for write in server_transport.writes:
        if write == IAC + WILL + CHARSET:
            will_charset_sent = True
            break
    assert will_charset_sent, "Server did not send IAC WILL CHARSET"

    # 4. Client receives IAC WILL CHARSET and should respond with IAC DO CHARSET
    client_transport.writes.clear()  # Clear previous writes
    client_writer.handle_will(CHARSET)

    # Verify that client sent IAC DO CHARSET (this would have failed before the fix)
    assert IAC + DO + CHARSET in client_transport.writes, "Client failed to send IAC DO CHARSET"

    # After this exchange, both sides should have CHARSET capability enabled
    assert server_writer.remote_option.enabled(CHARSET)
    assert server_writer.local_option.enabled(CHARSET)
    assert client_writer.remote_option.enabled(CHARSET)
    assert client_writer.local_option.enabled(CHARSET)


# --- Edge Case Tests ---


async def test_charset_send_unknown_encoding(bind_host, unused_tcp_port):
    """Test client with unknown encoding value."""
    # local
    from telnetlib3.tests.accessories import asyncio_server, open_connection

    async with asyncio_server(asyncio.Protocol, bind_host, unused_tcp_port):
        async with open_connection(
            client_factory=lambda **kwargs: CustomTelnetClient(
                charset_behavior="unknown_encoding", **kwargs
            ),
            host=bind_host,
            port=unused_tcp_port,
            connect_minwait=0.05,
        ) as (reader, writer):
            assert writer.protocol.encoding(incoming=True) == "US-ASCII"


async def test_charset_send_no_viable_offers(bind_host, unused_tcp_port):
    """Test client with no viable encoding offers."""
    # local
    from telnetlib3.tests.accessories import asyncio_server, open_connection

    async with asyncio_server(asyncio.Protocol, bind_host, unused_tcp_port):
        async with open_connection(
            client_factory=lambda **kwargs: CustomTelnetClient(
                charset_behavior="no_viable_offers", **kwargs
            ),
            host=bind_host,
            port=unused_tcp_port,
            connect_minwait=0.05,
            connect_maxwait=0.25,
        ) as (reader, writer):
            assert writer.protocol.encoding(incoming=True) == "US-ASCII"


async def test_charset_explicit_non_latin1_encoding(bind_host, unused_tcp_port):
    """Test client rejecting offered encodings when explicit non-latin1 is set."""
    # local
    from telnetlib3.tests.accessories import asyncio_server, open_connection

    async with asyncio_server(asyncio.Protocol, bind_host, unused_tcp_port):
        async with open_connection(
            client_factory=lambda **kwargs: CustomTelnetClient(
                charset_behavior="explicit_non_latin1", **kwargs
            ),
            host=bind_host,
            port=unused_tcp_port,
            connect_minwait=0.05,
            connect_maxwait=0.25,
        ) as (reader, writer):
            assert writer.protocol.encoding(incoming=True) == "US-ASCII"
