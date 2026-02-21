"""Benchmarks for telnetlib3 hot paths."""

# std imports
import asyncio

# 3rd party
import pytest

# local
import telnetlib3
from telnetlib3.slc import snoop, generate_slctab
from telnetlib3.telopt import IAC, NAWS, WILL, TTYPE, theNULL
from telnetlib3.stream_reader import TelnetReader
from telnetlib3.stream_writer import TelnetWriter


class MockTransport:
    """Minimal transport mock for benchmarking."""

    def write(self, data):
        pass

    def get_write_buffer_size(self):
        return 0

    def is_closing(self):
        return False


class MockProtocol:
    """Minimal protocol mock for benchmarking."""


@pytest.fixture
def writer():
    """Create a TelnetWriter for benchmarking."""
    return TelnetWriter(transport=MockTransport(), protocol=MockProtocol(), server=True)


@pytest.fixture
def reader():
    """Create a TelnetReader for benchmarking."""
    return TelnetReader()


# -- feed_byte: the main IAC parser, called for every byte on server --


@pytest.mark.parametrize(
    "byte",
    [
        pytest.param(b"A", id="normal"),
        pytest.param(b"\x00", id="null"),
        pytest.param(b"\xff", id="iac"),
    ],
)
def test_feed_byte(benchmark, writer, byte):
    """Benchmark feed_byte() with different byte types."""
    benchmark(writer.feed_byte, byte)


def test_feed_byte_iac_nop(benchmark, writer):
    """Benchmark feed_byte() for complete IAC NOP sequence."""

    def feed_iac_nop():
        writer.feed_byte(IAC)
        writer.feed_byte(b"\xf1")  # NOP

    benchmark(feed_iac_nop)


def test_feed_byte_iac_will(benchmark, writer):
    """Benchmark feed_byte() for IAC WILL TTYPE negotiation."""

    def feed_iac_will():
        writer.feed_byte(IAC)
        writer.feed_byte(WILL)
        writer.feed_byte(TTYPE)

    benchmark(feed_iac_will)


# -- is_oob: checked after every feed_byte() call --


def test_is_oob_property(benchmark, writer):
    """Benchmark is_oob property access."""
    benchmark(lambda: writer.is_oob)


# -- Option dict lookups: checked during negotiation --


@pytest.mark.parametrize(
    "option_attr",
    [
        pytest.param("local_option", id="local"),
        pytest.param("remote_option", id="remote"),
        pytest.param("pending_option", id="pending"),
    ],
)
def test_option_lookup(benchmark, writer, option_attr):
    """Benchmark option dictionary lookups."""
    option = getattr(writer, option_attr)
    benchmark(option.enabled, TTYPE)


def test_option_setitem(benchmark, writer):
    """Benchmark option dictionary assignment."""
    benchmark(writer.local_option.__setitem__, NAWS, True)


# -- TelnetReader.feed_data: buffers incoming data --


@pytest.mark.parametrize(
    "size",
    [pytest.param(1, id="1byte"), pytest.param(64, id="64bytes"), pytest.param(1024, id="1kb")],
)
def test_reader_feed_data(benchmark, reader, size):
    """Benchmark TelnetReader.feed_data() with different chunk sizes."""
    data = b"x" * size
    benchmark(reader.feed_data, data)


# -- SLC snoop: used in client fast path for SLC character detection --


@pytest.fixture
def slctab():
    """Generate SLC table for benchmarking."""
    return generate_slctab()


@pytest.mark.parametrize(
    "byte", [pytest.param(b"\x03", id="match_ip"), pytest.param(b"A", id="no_match")]
)
def test_snoop(benchmark, slctab, byte):
    """Benchmark snoop() for SLC character matching."""
    benchmark(snoop, byte, slctab, {})


def test_slc_value_set_membership(benchmark, slctab):
    """Benchmark SLC value set membership check (client fast path)."""
    slc_vals = frozenset(defn.val[0] for defn in slctab.values() if defn.val != theNULL)
    benchmark(lambda: 3 in slc_vals)


# -- End-to-end: full connection with bulk data transfer --


DATA_1MB = b"x" * (1024 * 1024)


async def _setup_server_client_pair():
    """Create connected server and client pair."""
    received_data = bytearray()
    server_ready = asyncio.Event()
    srv_writer = None

    async def shell(reader, writer):
        nonlocal srv_writer
        srv_writer = writer
        server_ready.set()
        while True:
            data = await reader.read(65536)
            if not data:
                break
            received_data.extend(data.encode() if isinstance(data, str) else data)

    server = await telnetlib3.create_server(
        host="127.0.0.1", port=0, shell=shell, encoding=False, connect_maxwait=0.1
    )
    port = server.sockets[0].getsockname()[1]

    client_reader, client_writer = await telnetlib3.open_connection(
        host="127.0.0.1",
        port=port,
        encoding=False,
        connect_maxwait=0.1,
        client_factory=telnetlib3.TelnetClient,
    )

    await server_ready.wait()

    return {
        "server": server,
        "srv_writer": srv_writer,
        "client_reader": client_reader,
        "client_writer": client_writer,
        "received_data": received_data,
    }


async def _teardown_server_client_pair(pair):
    """Clean up server and client pair."""
    pair["client_writer"].close()
    await pair["client_writer"].wait_closed()
    pair["server"].close()
    await pair["server"].wait_closed()


def test_bulk_transfer_client_to_server(benchmark):
    """Benchmark 1MB bulk transfer from client to server."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        pair = loop.run_until_complete(_setup_server_client_pair())
        client_writer = pair["client_writer"]
        received = pair["received_data"]

        async def send_1mb():
            received.clear()
            client_writer.write(DATA_1MB)
            await client_writer.drain()
            while len(received) < len(DATA_1MB):
                await asyncio.sleep(0.001)

        benchmark(lambda: loop.run_until_complete(send_1mb()))

        loop.run_until_complete(_teardown_server_client_pair(pair))
    finally:
        loop.close()


def test_bulk_transfer_server_to_client(benchmark):
    """Benchmark 1MB bulk transfer from server to client."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        pair = loop.run_until_complete(_setup_server_client_pair())
        srv_writer = pair["srv_writer"]
        client_reader = pair["client_reader"]

        async def send_1mb():
            srv_writer.write(DATA_1MB)
            await srv_writer.drain()
            received = 0
            while received < len(DATA_1MB):
                chunk = await client_reader.read(65536)
                if not chunk:
                    break
                received += len(chunk)

        benchmark(lambda: loop.run_until_complete(send_1mb()))

        loop.run_until_complete(_teardown_server_client_pair(pair))
    finally:
        loop.close()
