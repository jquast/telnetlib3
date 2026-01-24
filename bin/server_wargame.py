#!/usr/bin/env python
"""
Telnet server that offers a basic "war game" question.

This example demonstrates a simple telnet server using asyncio.
Run this server, then connect with: telnet localhost 6023

Example session::

    $ telnet localhost 6023
    Escape character is '^]'.

    Would you like to play a game? y
    They say the only way to win is to not play at all.
    Connection closed by foreign host.
"""

# std imports
import asyncio

# local
import telnetlib3  # pylint: disable=cyclic-import


async def shell(reader, writer):
    """Handle a single client connection."""
    writer.write("\r\nWould you like to play a game? ")
    inp = await reader.read(1)
    if inp:
        writer.echo(inp)
        writer.write("\r\nThey say the only way to win is to not play at all.\r\n")
        await writer.drain()
    writer.close()


async def main():
    """Start the telnet server."""
    server = await telnetlib3.create_server(host="127.0.0.1", port=6023, shell=shell)
    print("Telnet server running on localhost:6023")
    print("Connect with: telnet localhost 6023")
    print("Press Ctrl+C to stop")
    await server.wait_closed()


if __name__ == "__main__":
    asyncio.run(main())
