#!/usr/bin/env python
"""
Telnet server using binary (raw bytes) mode.

This example demonstrates using ``encoding=False`` for a server that works
with raw bytes instead of Unicode strings. This is useful for protocol
bridging, binary data transfer, or custom protocols over telnet.

When encoding is set (the default), the shell callback receives
``TelnetReaderUnicode`` and ``TelnetWriterUnicode``, which read and write
``str``. When ``encoding=False``, the shell receives ``TelnetReader`` and
``TelnetWriter``, which read and write ``bytes``.

Run this server, then connect with: telnet localhost 6023

Example session::

    $ telnet localhost 6023
    Escape character is '^]'.
    [binary echo server] type something:
    hello
    hex: 68 65 6c 6c 6f 0d 0a
    Connection closed by foreign host.
"""

# std imports
import asyncio

# local
import telnetlib3  # pylint: disable=cyclic-import


async def shell(reader, writer):
    """Echo client input back as hex bytes."""
    writer.write(b"[binary echo server] type something:\r\n")
    await writer.drain()

    data = await reader.read(128)
    if data:
        hex_str = " ".join(f"{b:02x}" for b in data)
        writer.write(f"hex: {hex_str}\r\n".encode("ascii"))
        await writer.drain()
    writer.close()


async def main():
    """Start the telnet server in binary mode."""
    server = await telnetlib3.create_server(
        host="127.0.0.1", port=6023, shell=shell, encoding=False
    )
    print("Binary telnet server running on localhost:6023")
    print("Connect with: telnet localhost 6023")
    print("Press Ctrl+C to stop")
    await server.wait_closed()


if __name__ == "__main__":
    asyncio.run(main())
