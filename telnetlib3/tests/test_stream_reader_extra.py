# std imports
import re
import asyncio

# 3rd party
import pytest

# local
from telnetlib3.stream_reader import TelnetReader, TelnetReaderUnicode


class MockTransport:
    def __init__(self):
        self.paused = False
        self.resumed = False
        self._closing = False
        self.writes = []

    def pause_reading(self):
        self.paused = True

    def resume_reading(self):
        self.resumed = True

    def is_closing(self):
        return self._closing

    def get_extra_info(self, name, default=None):
        return default

    def write(self, data):
        self.writes.append(bytes(data))


@pytest.mark.asyncio
async def test_readuntil_success_consumes_and_returns():
    r = TelnetReader(limit=64)
    r.feed_data(b"abc\nrest")
    out = await r.readuntil(b"\n")
    assert out == b"abc\n"
    assert bytes(r._buffer) == b"rest"


@pytest.mark.asyncio
async def test_readuntil_eof_incomplete_raises_and_clears():
    r = TelnetReader(limit=64)
    r.feed_data(b"partial")
    r.feed_eof()
    with pytest.raises(asyncio.IncompleteReadError) as exc:
        await r.readuntil(b"\n")
    assert exc.value.partial == b"partial"
    assert r._buffer == bytearray()


@pytest.mark.asyncio
async def test_readuntil_limit_overrun_leaves_buffer():
    r = TelnetReader(limit=5)
    r.feed_data(b"abcdefg")
    with pytest.raises(asyncio.LimitOverrunError):
        await r.readuntil(b"\n")
    assert bytes(r._buffer) == b"abcdefg"


@pytest.mark.asyncio
async def test_readuntil_pattern_success_and_eof_incomplete():
    r = TelnetReader(limit=64)
    pat = re.compile(b"XYZ")
    r.feed_data(b"aaXYZbb")
    out = await r.readuntil_pattern(pat)
    assert out == b"aaXYZ"
    assert bytes(r._buffer) == b"bb"

    r2 = TelnetReader(limit=64)
    r2.feed_data(b"aaaa")
    r2.feed_eof()
    with pytest.raises(asyncio.IncompleteReadError) as exc:
        await r2.readuntil_pattern(pat)
    assert exc.value.partial == b"aaaa"
    assert r2._buffer == bytearray()


@pytest.mark.asyncio
async def test_read_negative_reads_until_eof_in_blocks():
    r = TelnetReader(limit=4)
    r.feed_data(b"12345678")
    r.feed_eof()
    out = await r.read(-1)
    assert out == b"12345678"
    assert r.at_eof() is True


@pytest.mark.asyncio
async def test_pause_and_resume_transport_based_on_buffer_limit():
    r = TelnetReader(limit=4)
    t = MockTransport()
    r.set_transport(t)
    r.feed_data(b"123456789")
    assert t.paused is True
    got = await r.read(5)
    assert got == b"12345"
    assert t.resumed is True


@pytest.mark.asyncio
async def test_anext_iterates_lines_and_stops_on_eof():
    r = TelnetReader()
    r.feed_data(b"Line1\nLine2\n")
    one = await r.__anext__()
    assert one == b"Line1\n"
    two = await r.__anext__()
    assert two == b"Line2\n"
    r.feed_eof()
    with pytest.raises(StopAsyncIteration):
        await r.__anext__()


@pytest.mark.asyncio
async def test_exception_propagates_to_read_calls():
    r = TelnetReader()
    r.set_exception(RuntimeError("boom"))
    with pytest.raises(RuntimeError, match="boom"):
        await r.read(1)


def test_deprecated_close_and_connection_closed_warns():
    r = TelnetReader()
    with pytest.warns(DeprecationWarning):
        _ = r.connection_closed
    with pytest.warns(DeprecationWarning):
        r.close()
    assert r._eof is True


@pytest.mark.asyncio
async def test_readexactly_negative_and_eof_partial():
    r = TelnetReader()
    with pytest.raises(ValueError):
        await r.readexactly(-5)

    r2 = TelnetReader()
    r2.feed_data(b"abc")
    r2.feed_eof()
    with pytest.raises(asyncio.IncompleteReadError) as exc:
        await r2.readexactly(5)
    assert exc.value.partial == b"abc"


@pytest.mark.asyncio
async def test_unicode_reader_read_zero_and_read_consumes():
    def enc(incoming):
        return "ascii"

    ur = TelnetReaderUnicode(fn_encoding=enc)
    assert await ur.read(0) == ""
    ur.feed_data(b"abc")
    out2 = await ur.read(2)
    assert out2 == "ab"
    out1 = await ur.read(10)
    assert out1 == "c"


@pytest.mark.asyncio
async def test_unicode_readexactly_reads_characters_not_bytes():
    def enc(incoming):
        return "utf-8"

    ur = TelnetReaderUnicode(fn_encoding=enc)
    ur.feed_data("☭ab".encode("utf-8"))
    out = await ur.readexactly(2)
    assert out == "☭a"
    out2 = await ur.readexactly(1)
    assert out2 == "b"


@pytest.mark.asyncio
async def test_feed_data_empty_returns_early():
    r = TelnetReader(limit=64)
    r.feed_data(b"existing")
    r.feed_data(b"")
    assert bytes(r._buffer) == b"existing"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "make_reader, method, args",
    [
        (lambda: TelnetReader(), "readuntil", (b"\n",)),
        (lambda: TelnetReader(), "readexactly", (5,)),
        (lambda: TelnetReaderUnicode(fn_encoding=lambda incoming=True: "ascii"), "readline", ()),
        (
            lambda: TelnetReaderUnicode(fn_encoding=lambda incoming=True: "ascii"),
            "readexactly",
            (3,),
        ),
    ],
    ids=["readuntil", "readexactly", "unicode-readline", "unicode-readexactly"],
)
async def test_read_method_raises_stored_exception(make_reader, method, args):
    reader = make_reader()
    reader.set_exception(RuntimeError("boom"))
    with pytest.raises(RuntimeError, match="boom"):
        await getattr(reader, method)(*args)


def test_aiter_returns_self():
    r = TelnetReader()
    assert r.__aiter__() is r
