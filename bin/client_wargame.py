#!/usr/bin/env python
"""
Telnet client that plays the "war game" against a server.

This example connects to a telnet server and automatically answers
any question with 'y'. Run server_wargame.py first, then this client.

Example output::

    $ python client_wargame.py

    Would you like to play a game? y
    They say the only way to win is to not play at all.
"""

# std imports
import asyncio

# local
import telnetlib3


async def shell(reader, writer):
    """Handle client session, auto-answering questions."""
    while True:
        # Read stream until '?' mark is found
        outp = await reader.read(1024)
        if not outp:
            # End of File
            break
        if "?" in outp:
            # Reply to all questions with 'y'
            writer.write("y\r\n")

        # Display all server output
        print(outp, flush=True, end="")

    # EOF
    print()


async def main():
    """Connect to the telnet server."""
    _reader, writer = await telnetlib3.open_connection(host="localhost", port=6023, shell=shell)
    await writer.protocol.waiter_closed


if __name__ == "__main__":
    asyncio.run(main())
