#!/usr/bin/env python
"""
Telnet server demonstrating wait_for_client() API.

This example shows how to use the Server.wait_for_client() method
to get access to connected client protocols without using a shell callback.

Example session::

    $ python server_wait_for_client.py
    Server running on localhost:6023
    Waiting for client...
    Client connected!
    Terminal: xterm-256color
    Window size: 80x24
"""

# std imports
import asyncio

# local
import telnetlib3


async def main():
    """Start server and wait for clients."""
    server = await telnetlib3.create_server(host="127.0.0.1", port=6023)
    print("Server running on localhost:6023")
    print("Connect with: telnet localhost 6023")

    while True:
        print("Waiting for client...")
        client = await server.wait_for_client()
        print("Client connected!")

        # Access negotiated terminal information
        term = client.get_extra_info("TERM") or "unknown"
        cols = client.get_extra_info("cols") or 80
        rows = client.get_extra_info("rows") or 24
        print(f"Terminal: {term}")
        print(f"Window size: {cols}x{rows}")

        # Send welcome message
        client.writer.write(f"\r\nWelcome! Your terminal is {term} ({cols}x{rows})\r\n")
        client.writer.write("Goodbye!\r\n")
        await client.writer.drain()
        client.writer.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nServer stopped")
