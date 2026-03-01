"""Tests for MCCP (MUD Client Compression Protocol) v2 and v3."""

# std imports
import zlib
import asyncio
import collections

# 3rd party
import pytest

# local
from telnetlib3.telopt import DO, SB, SE, IAC, DONT, WILL, WONT, MCCP2_COMPRESS, MCCP3_COMPRESS
from telnetlib3.stream_writer import TelnetWriter
from telnetlib3.tests.accessories import MockProtocol, MockTransport


def new_writer(server=True, client=False, reader=None):
    t = MockTransport()
    p = MockProtocol()
    w = TelnetWriter(t, p, server=server, client=client, reader=reader)
    return w, t, p


class TestMCCP2Negotiation:
    def test_handle_will_mccp2_client(self):
        w, t, _p = new_writer(server=False, client=True)
        w.handle_will(MCCP2_COMPRESS)
        assert IAC + DO + MCCP2_COMPRESS in t.writes
        assert w.remote_option.get(MCCP2_COMPRESS) is True

    def test_handle_will_mccp2_server(self):
        w, t, _p = new_writer(server=True)
        w.handle_will(MCCP2_COMPRESS)
        assert IAC + DO + MCCP2_COMPRESS in t.writes

    def test_handle_do_mccp2_server(self):
        w, t, _p = new_writer(server=True)
        result = w.handle_do(MCCP2_COMPRESS)
        assert result is True
        assert IAC + WILL + MCCP2_COMPRESS in t.writes

    def test_handle_do_mccp2_client(self):
        w, t, _p = new_writer(server=False, client=True)
        result = w.handle_do(MCCP2_COMPRESS)
        assert result is True
        assert IAC + WILL + MCCP2_COMPRESS in t.writes


class TestMCCP3Negotiation:
    def test_handle_will_mccp3_client(self):
        w, t, _p = new_writer(server=False, client=True)
        w.handle_will(MCCP3_COMPRESS)
        assert IAC + DO + MCCP3_COMPRESS in t.writes
        assert IAC + SB + MCCP3_COMPRESS + IAC + SE in t.writes
        assert w.mccp3_active is True

    def test_handle_will_mccp3_server(self):
        w, t, _p = new_writer(server=True)
        w.handle_will(MCCP3_COMPRESS)
        assert IAC + DO + MCCP3_COMPRESS in t.writes
        assert w.remote_option.get(MCCP3_COMPRESS) is True


class TestMCCPCompressionRejection:
    @pytest.mark.parametrize("opt", [MCCP2_COMPRESS, MCCP3_COMPRESS], ids=["MCCP2", "MCCP3"])
    def test_handle_will_rejected_when_disabled(self, opt):
        w, t, _p = new_writer(server=False, client=True)
        w.compression = False
        w.handle_will(opt)
        assert IAC + DONT + opt in t.writes
        assert w.remote_option.get(opt) is not True

    @pytest.mark.parametrize("opt", [MCCP2_COMPRESS, MCCP3_COMPRESS], ids=["MCCP2", "MCCP3"])
    def test_handle_do_rejected_when_disabled(self, opt):
        w, t, _p = new_writer(server=True)
        w.compression = False
        result = w.handle_do(opt)
        assert result is False
        assert IAC + WONT + opt in t.writes


@pytest.mark.asyncio
class TestMCCPRejectedOverTLS:
    @pytest.mark.parametrize("opt", [MCCP2_COMPRESS, MCCP3_COMPRESS], ids=["MCCP2", "MCCP3"])
    async def test_server_does_not_offer_mccp_when_tls_active(self, opt):
        """Server skips WILL MCCP2/MCCP3 when TLS is active."""
        from telnetlib3.server import TelnetServer

        server = TelnetServer(encoding=False, connect_maxwait=0.1, compression=True)
        transport = MockTransport()
        transport.extra["ssl_object"] = object()
        server.connection_made(transport)

        server.begin_advanced_negotiation()

        written = b"".join(transport.writes)
        assert IAC + WILL + MCCP2_COMPRESS not in written
        assert IAC + WILL + MCCP3_COMPRESS not in written

    async def test_server_offers_mccp_when_no_tls(self):
        """Server sends WILL MCCP2/MCCP3 when no TLS."""
        from telnetlib3.server import TelnetServer

        server = TelnetServer(encoding=False, connect_maxwait=0.1, compression=True)
        transport = MockTransport()
        server.connection_made(transport)

        server.begin_advanced_negotiation()

        written = b"".join(transport.writes)
        assert IAC + WILL + MCCP2_COMPRESS in written
        assert IAC + WILL + MCCP3_COMPRESS in written

    @pytest.mark.parametrize("opt", [MCCP2_COMPRESS, MCCP3_COMPRESS], ids=["MCCP2", "MCCP3"])
    def test_client_rejects_will_mccp_over_tls(self, opt):
        """Client sends DONT when server offers WILL MCCP over TLS."""
        w, t, _p = new_writer(server=False, client=True)
        t.extra["ssl_object"] = object()
        w.handle_will(opt)
        assert IAC + DONT + opt in t.writes
        assert w.remote_option.get(opt) is not True

    @pytest.mark.parametrize("opt", [MCCP2_COMPRESS, MCCP3_COMPRESS], ids=["MCCP2", "MCCP3"])
    def test_client_rejects_do_mccp_over_tls(self, opt):
        """Client sends WONT when server sends DO MCCP over TLS."""
        w, t, _p = new_writer(server=True)
        t.extra["ssl_object"] = object()
        result = w.handle_do(opt)
        assert result is False
        assert IAC + WONT + opt in t.writes


class TestMCCP2SBHandler:
    def test_sb_mccp2_sets_activated_flag(self):
        w, _t, _p = new_writer(server=False, client=True)
        w.pending_option[SB + MCCP2_COMPRESS] = True
        buf = collections.deque([MCCP2_COMPRESS])
        w.handle_subnegotiation(buf)
        assert w._mccp2_activated is True

    def test_sb_mccp2_calls_ext_callback(self):
        w, _t, _p = new_writer(server=False, client=True)
        received = []
        w.set_ext_callback(MCCP2_COMPRESS, lambda val: received.append(val))
        w.pending_option[SB + MCCP2_COMPRESS] = True
        buf = collections.deque([MCCP2_COMPRESS])
        w.handle_subnegotiation(buf)
        assert received == [True]


class TestMCCP3SBHandler:
    def test_sb_mccp3_activates_on_server(self):
        w, _t, _p = new_writer(server=True)
        w.pending_option[SB + MCCP3_COMPRESS] = True
        buf = collections.deque([MCCP3_COMPRESS])
        w.handle_subnegotiation(buf)
        assert w.mccp3_active is True


class TestMCCP2MidChunk:
    def test_mid_chunk_split(self):
        """SB MCCP2 SE followed by compressed bytes in a single chunk."""
        from telnetlib3._base import _process_data_chunk
        from telnetlib3.stream_reader import TelnetReader

        reader = TelnetReader()
        w, _t, _p = new_writer(server=False, client=True, reader=reader)

        plaintext = b"Hello, compressed world!"
        compressor = zlib.compressobj(
            zlib.Z_BEST_COMPRESSION, zlib.DEFLATED, 12, 5, zlib.Z_DEFAULT_STRATEGY
        )
        compressed = compressor.compress(plaintext)
        compressed += compressor.flush(zlib.Z_SYNC_FLUSH)

        chunk = IAC + SB + MCCP2_COMPRESS + IAC + SE + compressed
        cmd_received = _process_data_chunk(chunk, w, reader, None, lambda *a: None)
        assert cmd_received is True
        assert w.mccp2_active is True
        assert w._compressed_remainder == compressed
        w._compressed_remainder = None

    def test_no_remainder_without_mccp2(self):
        from telnetlib3._base import _process_data_chunk
        from telnetlib3.stream_reader import TelnetReader

        reader = TelnetReader()
        w, _t, _p = new_writer(server=False, client=True, reader=reader)

        chunk = b"plain text data"
        cmd_received = _process_data_chunk(chunk, w, reader, None, lambda *a: None)
        assert cmd_received is False
        assert w._compressed_remainder is None


class TestMCCP2Decompression:
    def test_stream_end_detection(self):
        """Z_STREAM_END triggers decompressor cleanup."""
        compressor = zlib.compressobj(
            zlib.Z_BEST_COMPRESSION, zlib.DEFLATED, 12, 5, zlib.Z_DEFAULT_STRATEGY
        )
        data = b"test data for z_stream_end"
        compressed = compressor.compress(data)
        compressed += compressor.flush(zlib.Z_FINISH)

        trailing_plaintext = b"after-compression"
        full = compressed + trailing_plaintext

        decompressor = zlib.decompressobj()
        decompressed = decompressor.decompress(full)
        assert decompressed == data
        assert decompressor.eof is True
        assert decompressor.unused_data == trailing_plaintext


class TestMCCP2CompressionRoundTrip:
    def test_iac_survives_roundtrip(self):
        """IAC sequences survive compression and decompression."""
        compressor = zlib.compressobj(
            zlib.Z_BEST_COMPRESSION, zlib.DEFLATED, 12, 5, zlib.Z_DEFAULT_STRATEGY
        )
        data_with_iac = IAC + WILL + MCCP2_COMPRESS + b"normal text"
        compressed = compressor.compress(data_with_iac)
        compressed += compressor.flush(zlib.Z_SYNC_FLUSH)

        decompressor = zlib.decompressobj()
        decompressed = decompressor.decompress(compressed)
        assert decompressed == data_with_iac


class TestMCCPAttributes:
    def test_initial_attributes(self):
        w, _t, _p = new_writer(server=True)
        assert w._mccp2_activated is False
        assert w.mccp2_active is False
        assert w.mccp3_active is False

    @pytest.mark.parametrize("opt", [MCCP2_COMPRESS, MCCP3_COMPRESS], ids=["MCCP2", "MCCP3"])
    def test_empty_sb_allowed(self, opt):
        w, _t, _p = new_writer(server=False, client=True)
        w.pending_option[SB + opt] = True
        buf = collections.deque([opt])
        w.handle_subnegotiation(buf)


def _make_compressed(plaintext: bytes, finish: bool = False) -> bytes:
    compressor = zlib.compressobj(
        zlib.Z_BEST_COMPRESSION, zlib.DEFLATED, 12, 5, zlib.Z_DEFAULT_STRATEGY
    )
    data = compressor.compress(plaintext)
    data += compressor.flush(zlib.Z_FINISH if finish else zlib.Z_SYNC_FLUSH)
    return data


_BOUNDARY_PLAINTEXT = b"The quick brown fox jumps over the lazy dog. " * 3
_BOUNDARY_COMPRESSED = _make_compressed(_BOUNDARY_PLAINTEXT)
_BOUNDARY_SB = IAC + SB + MCCP2_COMPRESS + IAC + SE
_BOUNDARY_FULL = _BOUNDARY_SB + _BOUNDARY_COMPRESSED
_BOUNDARY_SPLITS = [
    1,
    2,
    3,
    4,
    len(_BOUNDARY_SB) - 1,
    len(_BOUNDARY_SB),
    len(_BOUNDARY_SB) + 1,
    len(_BOUNDARY_SB) + len(_BOUNDARY_COMPRESSED) // 2,
    len(_BOUNDARY_FULL) - 1,
]
_BOUNDARY_IDS = [f"split_at_{s}" for s in _BOUNDARY_SPLITS]


def _make_client_with_capture():
    """Create a BaseClient with captured reader output, for packet boundary tests."""
    from telnetlib3.client_base import BaseClient

    received: list[bytes] = []
    client = BaseClient(encoding=False, connect_minwait=0, connect_maxwait=0.1)
    transport = MockTransport()
    client.connection_made(transport)

    orig_feed = client.reader.feed_data

    def capture_feed(data: bytes) -> None:
        received.append(data)
        orig_feed(data)

    client.reader.feed_data = capture_feed
    return client, received


@pytest.mark.asyncio
class TestMCCP2PacketBoundary:
    @pytest.mark.parametrize("split_at", _BOUNDARY_SPLITS, ids=_BOUNDARY_IDS)
    async def test_two_chunk_delivery(self, split_at):
        """Compressed data split across two TCP chunks at various offsets."""
        client, received = _make_client_with_capture()

        client._process_chunk(_BOUNDARY_FULL[:split_at])
        client._process_chunk(_BOUNDARY_FULL[split_at:])

        joined = b"".join(received)
        assert joined == _BOUNDARY_PLAINTEXT

    @pytest.mark.parametrize("n_chunks", [3, 5, 10], ids=["3_chunks", "5_chunks", "10_chunks"])
    async def test_multi_chunk_delivery(self, n_chunks):
        """Compressed data delivered in many small chunks."""
        client, received = _make_client_with_capture()

        chunk_size = max(1, len(_BOUNDARY_FULL) // n_chunks)
        for i in range(0, len(_BOUNDARY_FULL), chunk_size):
            client._process_chunk(_BOUNDARY_FULL[i : i + chunk_size])

        joined = b"".join(received)
        assert joined == _BOUNDARY_PLAINTEXT

    async def test_z_finish_with_trailing_plaintext(self):
        """Z_FINISH boundary: compressed stream ends, plaintext follows."""
        client, received = _make_client_with_capture()

        plaintext = b"compressed content here"
        trailing = b"plaintext after compression ends"
        compressed = _make_compressed(plaintext, finish=True)
        full = _BOUNDARY_SB + compressed + trailing

        client._process_chunk(full)
        joined = b"".join(received)
        assert joined == plaintext + trailing

    @pytest.mark.parametrize(
        "split_at", [1, 4, 8, 16], ids=["byte_1", "byte_4", "byte_8", "byte_16"]
    )
    async def test_compressed_only_boundary(self, split_at):
        """Split within compressed data only (SB already processed)."""
        client, received = _make_client_with_capture()

        client._process_chunk(_BOUNDARY_SB)

        actual_split = min(split_at, len(_BOUNDARY_COMPRESSED) - 1)
        client._process_chunk(_BOUNDARY_COMPRESSED[:actual_split])
        client._process_chunk(_BOUNDARY_COMPRESSED[actual_split:])

        joined = b"".join(received)
        assert joined == _BOUNDARY_PLAINTEXT


@pytest.mark.asyncio
class TestMCCPDecompressionError:
    async def test_client_corrupt_mccp2_drops_data(self):
        """Corrupt compressed data is discarded, not fed to IAC parser."""
        client, received = _make_client_with_capture()

        # Activate MCCP2 via SB
        client._process_chunk(_BOUNDARY_SB)

        # Send garbage that is not valid zlib
        client._process_chunk(b"\x00\x01\x02\x03\xff\xfe\xfd")

        # Decompressor should be disabled, corrupt data not fed to reader
        assert client._mccp2_decompressor is None
        assert received == []

    async def test_server_corrupt_mccp3_drops_data(self):
        """Corrupt MCCP3 data is discarded, not fed to IAC parser."""
        from telnetlib3.server_base import BaseServer

        server = BaseServer(encoding=False, connect_maxwait=0.1)
        transport = MockTransport()
        server.connection_made(transport)

        received: list[bytes] = []
        orig_feed = server.reader.feed_data
        server.reader.feed_data = lambda d: (received.append(d), orig_feed(d))

        # Manually activate MCCP3 decompression
        server._mccp3_decompressor = zlib.decompressobj()

        # Send garbage
        server.data_received(b"\x00\x01\x02\x03\xff\xfe\xfd")

        assert server._mccp3_decompressor is None
        assert received == []


@pytest.mark.asyncio
class TestMCCP2ServerEnd:
    async def test_mccp2_end_flushes_and_restores(self):
        """_mccp2_end flushes Z_FINISH and restores transport.write."""
        from telnetlib3.server import TelnetServer

        server = TelnetServer(encoding=False, connect_maxwait=0.1, compression=True)
        transport = MockTransport()
        server.connection_made(transport)

        # Manually start MCCP2 compression
        server._mccp2_compressor = zlib.compressobj(
            zlib.Z_BEST_COMPRESSION, zlib.DEFLATED, 12, 5, zlib.Z_DEFAULT_STRATEGY
        )
        orig_write = transport.write
        server._mccp2_orig_write = orig_write
        server._mccp2_pending = True
        server.writer.mccp2_active = True

        # Wrap transport like _mccp2_start does
        def compressed_write(data: bytes) -> None:
            if server._mccp2_compressor is not None:
                c = server._mccp2_compressor.compress(data)
                c += server._mccp2_compressor.flush(zlib.Z_SYNC_FLUSH)
                orig_write(c)
            else:
                orig_write(data)

        transport.write = compressed_write

        server._mccp2_end()

        assert server._mccp2_compressor is None
        assert server._mccp2_pending is False
        assert server.writer.mccp2_active is False
        # transport.write should be restored to original
        assert transport.write is orig_write

    async def test_mccp2_end_handles_zlib_error(self):
        """_mccp2_end catches zlib.error from double-flush."""
        from telnetlib3.server import TelnetServer

        server = TelnetServer(encoding=False, connect_maxwait=0.1, compression=True)
        transport = MockTransport()
        server.connection_made(transport)

        compressor = zlib.compressobj(
            zlib.Z_BEST_COMPRESSION, zlib.DEFLATED, 12, 5, zlib.Z_DEFAULT_STRATEGY
        )
        # Exhaust the compressor so flush(Z_FINISH) raises
        compressor.flush(zlib.Z_FINISH)

        server._mccp2_compressor = compressor
        server._mccp2_orig_write = transport.write
        server._mccp2_pending = True
        server.writer.mccp2_active = True

        server._mccp2_end()

        assert server._mccp2_compressor is None
        assert server.writer.mccp2_active is False

    async def test_compressed_write_fallback_after_end(self):
        """compressed_write uses orig_write when compressor is None."""
        from telnetlib3.server import TelnetServer

        server = TelnetServer(encoding=False, connect_maxwait=0.1, compression=True)
        transport = MockTransport()
        server.connection_made(transport)

        server._mccp2_start()
        compressed_write = transport.write

        # End compression — restores transport.write
        server._mccp2_end()

        # The closure should fallback to orig_write when compressor is None
        transport.writes.clear()
        compressed_write(b"plaintext after end")
        assert b"plaintext after end" in transport.writes


@pytest.mark.asyncio
class TestMCCP3ClientEnd:
    async def test_mccp3_end_flushes_and_restores(self):
        """_mccp3_end flushes Z_FINISH and restores transport.write."""
        from telnetlib3.client_base import BaseClient

        client = BaseClient(encoding=False, connect_minwait=0, connect_maxwait=0.1)
        transport = MockTransport()
        client.connection_made(transport)

        client._mccp3_start()
        assert client._mccp3_compressor is not None

        client._mccp3_end()

        assert client._mccp3_compressor is None
        assert client.writer.mccp3_active is False
        # Final flush bytes should have been written
        assert len(transport.writes) > 0

    async def test_mccp3_end_skips_write_when_closing(self):
        """_mccp3_end skips final write when transport is closing."""
        from telnetlib3.client_base import BaseClient

        client = BaseClient(encoding=False, connect_minwait=0, connect_maxwait=0.1)
        transport = MockTransport()
        client.connection_made(transport)

        client._mccp3_start()
        transport.writes.clear()
        transport._closing = True

        client._mccp3_end()

        assert client._mccp3_compressor is None
        # No final flush written because transport is closing
        assert transport.writes == []

    async def test_mccp3_end_noop_when_inactive(self):
        """_mccp3_end is safe to call when compression is not active."""
        from telnetlib3.client_base import BaseClient

        client = BaseClient(encoding=False, connect_minwait=0, connect_maxwait=0.1)
        transport = MockTransport()
        client.connection_made(transport)

        client._mccp3_end()
        assert client._mccp3_compressor is None
        assert client.writer.mccp3_active is False

    async def test_compressed_write_fallback_after_end(self):
        """Client compressed_write uses orig_write when compressor is None."""
        from telnetlib3.client_base import BaseClient

        client = BaseClient(encoding=False, connect_minwait=0, connect_maxwait=0.1)
        transport = MockTransport()
        client.connection_made(transport)

        client._mccp3_start()
        compressed_write = transport.write

        client._mccp3_end()

        transport.writes.clear()
        compressed_write(b"plain after mccp3 end")
        assert b"plain after mccp3 end" in transport.writes


@pytest.mark.asyncio
class TestMCCP2ServerEndNoop:
    async def test_mccp2_end_noop_when_inactive(self):
        """_mccp2_end is safe to call when compression is not active."""
        from telnetlib3.server import TelnetServer

        server = TelnetServer(encoding=False, connect_maxwait=0.1, compression=True)
        transport = MockTransport()
        server.connection_made(transport)

        server._mccp2_end()
        assert server._mccp2_compressor is None
        assert server.writer.mccp2_active is False


@pytest.mark.asyncio
class TestMCCP2Integration:
    async def test_server_client_mccp2(self):
        """Full MCCP2 round-trip: server compresses, client decompresses."""
        from telnetlib3.client import open_connection
        from telnetlib3.server import create_server

        received_data: list[str] = []
        test_text = "Hello from MCCP2 compressed server!"

        async def server_shell(reader, writer):
            writer.write(test_text)
            writer.close()

        async def client_shell(reader, writer):
            data = await asyncio.wait_for(reader.read(4096), timeout=5)
            received_data.append(data)

        server = await create_server(
            host="127.0.0.1",
            port=0,
            compression=True,
            shell=server_shell,
            encoding="utf-8",
            connect_maxwait=1.0,
        )
        port = server._server.sockets[0].getsockname()[1]

        try:
            reader, writer = await open_connection(
                host="127.0.0.1",
                port=port,
                shell=client_shell,
                encoding="utf-8",
                connect_minwait=0,
                connect_maxwait=2.0,
            )
            await asyncio.wait_for(writer.protocol.waiter_closed, timeout=10)
        finally:
            server.close()

        combined = "".join(received_data)
        assert test_text in combined

    async def test_server_client_mccp2_bidirectional(self):
        """MCCP2 server→client: server writes compressed, client reads plaintext."""
        from telnetlib3.client import open_connection
        from telnetlib3.server import create_server

        server_received: list[str] = []
        client_received: list[str] = []
        client_msg = "hello from client"
        server_msg = "hello from server"

        async def server_shell(reader, writer):
            data = await asyncio.wait_for(reader.read(4096), timeout=5)
            server_received.append(data)
            writer.write(server_msg)
            writer.close()

        async def client_shell(reader, writer):
            writer.write(client_msg)
            data = await asyncio.wait_for(reader.read(4096), timeout=5)
            client_received.append(data)

        server = await create_server(
            host="127.0.0.1",
            port=0,
            compression=True,
            shell=server_shell,
            encoding="utf-8",
            connect_maxwait=1.0,
        )
        port = server._server.sockets[0].getsockname()[1]

        try:
            _reader, writer = await open_connection(
                host="127.0.0.1",
                port=port,
                shell=client_shell,
                encoding="utf-8",
                connect_minwait=0,
                connect_maxwait=2.0,
            )
            await asyncio.wait_for(writer.protocol.waiter_closed, timeout=10)
        finally:
            server.close()

        assert client_msg in "".join(server_received)
        assert server_msg in "".join(client_received)

    async def test_server_client_mccp3(self):
        """MCCP3 client→server: client compresses, server reads plaintext."""
        from telnetlib3.client import open_connection
        from telnetlib3.server import create_server

        server_received: list[str] = []
        client_msg = "hello compressed from client via MCCP3"

        async def server_shell(reader, writer):
            data = await asyncio.wait_for(reader.read(4096), timeout=5)
            server_received.append(data)
            writer.write("ack")
            writer.close()

        async def client_shell(reader, writer):
            await asyncio.sleep(0.2)
            writer.write(client_msg)
            await asyncio.wait_for(reader.read(4096), timeout=5)

        server = await create_server(
            host="127.0.0.1",
            port=0,
            compression=True,
            shell=server_shell,
            encoding="utf-8",
            connect_maxwait=1.0,
        )
        port = server._server.sockets[0].getsockname()[1]

        try:
            _reader, writer = await open_connection(
                host="127.0.0.1",
                port=port,
                shell=client_shell,
                encoding="utf-8",
                connect_minwait=0,
                connect_maxwait=2.0,
            )
            await asyncio.wait_for(writer.protocol.waiter_closed, timeout=10)
        finally:
            server.close()

        assert client_msg in "".join(server_received)
