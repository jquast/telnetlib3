# std imports
import asyncio

# 3rd party
import pytest

# local
from telnetlib3.stream_reader import TelnetReader


class PauseNIErrorTransport:
    def __init__(self):
        self.paused = False
        self.resumed = False
        self._closing = False

    def pause_reading(self):
        raise NotImplementedError

    def resume_reading(self):
        self.resumed = True

    def is_closing(self):
        return self._closing

    def get_extra_info(self, name, default=None):
        return default


class ResumeTransport:
    def __init__(self):
        self.paused = False
        self.resumed = False
        self._closing = False

    def pause_reading(self):
        self.paused = True

    def resume_reading(self):
        self.resumed = True

    def is_closing(self):
        return self._closing

    def get_extra_info(self, name, default=None):
        return default


def test_repr_shows_key_fields():
    r = TelnetReader(limit=1234)
    r.feed_data(b"abc")
    r.feed_eof()
    r.set_exception(RuntimeError("boom"))
    r.set_transport(ResumeTransport())
    r._paused = True

    rep = repr(r)
    assert "TelnetReader" in rep
    assert "3 bytes" in rep
    assert "eof" in rep
    assert "limit=1234" in rep
    assert "exception=" in rep
    assert "transport=" in rep
    assert "paused" in rep
    assert "encoding=False" in rep


async def test_set_exception_and_wakeup_waiter():
    r = TelnetReader()
    loop = asyncio.get_running_loop()
    fut = loop.create_future()
    r._waiter = fut
    err = RuntimeError("oops")
    r.set_exception(err)
    assert r.exception() is err
    assert fut.done()
    with pytest.raises(RuntimeError):
        fut.result()

    fut2 = loop.create_future()
    r._waiter = fut2
    r._wakeup_waiter()
    assert fut2.done()
    assert fut2.result() is None


@pytest.mark.asyncio
async def test_wait_for_data_resumes_when_paused_and_data_arrives():
    r = TelnetReader(limit=4)
    t = ResumeTransport()
    r.set_transport(t)
    r.feed_data(b"123456789")
    assert t.paused is True or len(r._buffer) > 2 * r._limit
    r._paused = True

    async def feeder():
        await asyncio.sleep(0.01)
        r.feed_data(b"x")

    feeder_task = asyncio.create_task(feeder())
    await asyncio.wait_for(r._wait_for_data("read"), 0.5)
    await feeder_task
    assert t.resumed is True


@pytest.mark.asyncio
async def test_concurrent_reads_raise_runtimeerror():
    r = TelnetReader()

    async def first():
        return await r.read(1)

    async def second():
        with pytest.raises(RuntimeError, match="already waiting"):
            await r.read(1)

    t1 = asyncio.create_task(first())
    await asyncio.sleep(0)
    t2 = asyncio.create_task(second())
    await asyncio.sleep(0.01)
    r.feed_data(b"A")
    res = await asyncio.wait_for(t1, 0.5)
    assert res == b"A"
    await t2


def test_feed_data_notimplemented_pause_drops_transport():
    r = TelnetReader(limit=1)
    t = PauseNIErrorTransport()
    r.set_transport(t)
    r.feed_data(b"ABCD")
    assert r._transport is None


@pytest.mark.asyncio
async def test_read_zero_returns_empty_bytes():
    r = TelnetReader()
    out = await r.read(0)
    assert out == b""


@pytest.mark.asyncio
async def test_read_until_wait_path_then_data_arrives():
    r = TelnetReader()

    # start waiting
    async def waiter():
        return await r.read(3)

    task = asyncio.create_task(waiter())
    await asyncio.sleep(0.01)
    r.feed_data(b"xyz")
    out = await asyncio.wait_for(task, 0.5)
    assert out == b"xyz"


@pytest.mark.asyncio
async def test_readexactly_exact_and_split_paths():
    r = TelnetReader()
    r.feed_data(b"abcd")
    got = await r.readexactly(4)
    assert got == b"abcd"
    r2 = TelnetReader()
    r2.feed_data(b"abcde")
    got2 = await r2.readexactly(3)
    assert got2 == b"abc"
    assert bytes(r2._buffer) == b"de"


async def test_readuntil_separator_empty_raises():
    r = TelnetReader()
    with pytest.raises(ValueError):
        await r.readuntil(b"")


async def test_readuntil_pattern_invalid_types():
    r = TelnetReader()
    with pytest.raises(ValueError, match="pattern should be a re\\.Pattern"):
        await r.readuntil_pattern(None)
