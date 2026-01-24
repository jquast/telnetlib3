#!/usr/bin/env python3
"""Profile telnetlib3 client to identify performance bottlenecks."""

# std imports
import sys
import time
import pstats
import asyncio
import cProfile
from io import StringIO


async def run_profiled():
    """Run client with timing instrumentation."""
    # local
    from telnetlib3.client import run_client

    start = time.perf_counter()
    try:
        await run_client()
    except (KeyboardInterrupt, EOFError):
        pass
    elapsed = time.perf_counter() - start
    print(f"\nTotal time: {elapsed:.2f}s", file=sys.stderr)


def main():
    if "--profile" in sys.argv:
        sys.argv.remove("--profile")
        profiler = cProfile.Profile()
        profiler.enable()
        try:
            asyncio.run(run_profiled())
        finally:
            profiler.disable()
            s = StringIO()
            ps = pstats.Stats(profiler, stream=s).sort_stats("cumulative")
            ps.print_stats(30)
            print(s.getvalue(), file=sys.stderr)
    else:
        asyncio.run(run_profiled())


if __name__ == "__main__":
    main()
