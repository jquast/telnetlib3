#!/usr/bin/env python3
"""Benchmark raw telnetlib3 client throughput."""

# std imports
import sys
import time
import asyncio


async def benchmark_shell(reader, writer):  # pylint: disable=unused-argument
    """Minimal shell that just measures throughput."""
    total_bytes = 0
    start = time.perf_counter()
    last_report = start

    try:
        while True:
            # Read as much as available
            data = await reader.read(65536)
            if not data:
                break
            total_bytes += len(data)

            # Report every second
            now = time.perf_counter()
            if now - last_report >= 1.0:
                elapsed = now - start
                rate = total_bytes / elapsed / 1024
                print(
                    f"\r{total_bytes:,} bytes, {elapsed:.1f}s, {rate:.1f} KB/s",
                    end="",
                    flush=True,
                    file=sys.stderr,
                )
                last_report = now

    except (KeyboardInterrupt, EOFError):
        pass
    finally:
        elapsed = time.perf_counter() - start
        if elapsed > 0:
            rate = total_bytes / elapsed / 1024
            print(
                f"\nFinal: {total_bytes:,} bytes in {elapsed:.2f}s = {rate:.1f} KB/s",
                file=sys.stderr,
            )


async def main():
    """Connect to a telnet server and benchmark throughput."""
    # local
    import telnetlib3  # pylint: disable=import-outside-toplevel

    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} host [port]", file=sys.stderr)
        sys.exit(1)

    host = sys.argv[1]
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 23

    print(f"Connecting to {host}:{port}...", file=sys.stderr)

    _, writer = await telnetlib3.open_connection(
        host,
        port,
        shell=benchmark_shell,
        connect_minwait=0.5,
        connect_maxwait=2.0,
    )

    await writer.protocol.waiter_closed


if __name__ == "__main__":
    asyncio.run(main())
