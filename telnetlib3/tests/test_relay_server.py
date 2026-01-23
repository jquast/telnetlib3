# std imports
import asyncio

# 3rd party
import pytest

# local
from telnetlib3.relay_server import relay_shell


class FakeWriter:
    def __init__(self):
        self.buffer = []
        self.closed = False
        self._closing = False
        self._extra = {"cols": 80, "rows": 24}

    def write(self, data):
        # Collect text written by the shell
        self.buffer.append(data)

    def echo(self, data):
        # Readline may call echo; we do not need to simulate terminal behavior
        self.buffer.append(data)

    def get_extra_info(self, key, default=None):
        return self._extra.get(key, default)

    def is_closing(self):
        return self._closing

    def close(self):
        self._closing = True
        self.closed = True


class SeqReader:
    """
    Async reader that returns provided sequence 1 byte at a time.

    When the sequence is exhausted, returns '' to indicate EOF.
    """

    def __init__(self, sequence):
        # sequence must be str
        self.data = sequence
        self.pos = 0

    async def read(self, n):
        # Only 1-byte reads are requested by relay_shell
        if self.pos >= len(self.data):
            return ""
        ch = self.data[self.pos]
        self.pos += 1
        return ch


class PayloadReader:
    """Reader that yields a list of payloads on subsequent read() calls, then ''."""

    def __init__(self, payloads):
        self.payloads = list(payloads)

    async def read(self, n):
        if not self.payloads:
            return ""
        return self.payloads.pop(0)


class DummyServerWriter:
    def __init__(self):
        self.writes = []
        self.closed = False

    def write(self, data):
        self.writes.append(data)

    def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_relay_shell_wrong_passcode_closes(monkeypatch):
    """Relay shell should prompt for passcode 3 times and close on failure."""
    # Prepare fake client I/O
    client_reader = SeqReader("bad1\rbad2\rbad3\r")
    client_writer = FakeWriter()

    # Avoid 1-second sleeps in loop
    async def _no_sleep(_):
        return None

    monkeypatch.setattr(asyncio, "sleep", _no_sleep)

    await relay_shell(client_reader, client_writer)

    out = "".join(client_writer.buffer)
    # Greeting and prompts
    assert "Telnet Relay shell ready." in out
    assert out.count("Passcode: ") == 3
    # Connection should not be attempted on wrong pass
    assert "Connecting to" not in out
    # Writer is closed
    assert client_writer.closed is True


@pytest.mark.asyncio
async def test_relay_shell_success_relays_and_closes(monkeypatch):
    """Relay shell should connect on correct passcode and relay server output."""
    # Client enters correct passcode then EOF from client
    client_reader = PayloadReader(
        # readline() is fed 1 char at a time
        list("867-5309\r")
        + [""]  # then EOF from client stdin after connection
    )
    client_writer = FakeWriter()

    # Avoid 1-second sleeps in loop
    async def _no_sleep(_):
        return None

    monkeypatch.setattr(asyncio, "sleep", _no_sleep)

    # Mock open_connection to a dummy server that sends "hello" then EOF
    server_reader = PayloadReader(["hello", ""])
    server_writer = DummyServerWriter()

    async def _fake_open_connection(host, port, cols=None, rows=None):
        # Basic sanity on forwarded cols/rows
        assert cols == 80 and rows == 24
        return server_reader, server_writer

    monkeypatch.setattr("telnetlib3.relay_server.open_connection", _fake_open_connection)

    await relay_shell(client_reader, client_writer)

    out = "".join(client_writer.buffer)
    # Greeting, connect, connected, and relayed output
    assert "Telnet Relay shell ready." in out
    assert "Connecting to 1984.ws:23" in out
    assert "connected!" in out
    assert "hello" in out

    # Both sides closed
    assert client_writer.closed is True
    assert server_writer.closed is True
