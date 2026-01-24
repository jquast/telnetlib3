#!/usr/bin/env python
"""
Telnet server demonstrating wait_for() negotiation states.

This example shows how to use writer.wait_for() to await specific
telnet option negotiation states before proceeding.

The server waits for:
- NAWS (window size) to be negotiated
- TTYPE (terminal type) negotiation to complete
"""

# std imports
import asyncio

# local
import telnetlib3


async def shell(_reader, writer):
    """Handle client with explicit negotiation waits."""
    writer.write("\r\nWaiting for terminal negotiation...\r\n")

    # Wait for NAWS (Negotiate About Window Size) to be enabled
    try:
        await asyncio.wait_for(writer.wait_for(local={"NAWS": True}), timeout=5.0)
        cols = writer.get_extra_info("cols")
        rows = writer.get_extra_info("rows")
        writer.write(f"Window size: {cols}x{rows}\r\n")
    except asyncio.TimeoutError:
        writer.write("NAWS not negotiated (timeout)\r\n")

    # Wait for TTYPE (Terminal Type) negotiation to complete
    try:
        await asyncio.wait_for(writer.wait_for(pending={"TTYPE": False}), timeout=5.0)
        term = writer.get_extra_info("TERM")
        writer.write(f"Terminal type: {term}\r\n")
    except asyncio.TimeoutError:
        writer.write("TTYPE not negotiated (timeout)\r\n")

    # Wait for both BINARY options if client supports them
    try:
        await asyncio.wait_for(
            writer.wait_for(remote={"BINARY": True}, local={"BINARY": True}),
            timeout=2.0,
        )
        writer.write("Binary mode enabled (bidirectional)\r\n")
    except asyncio.TimeoutError:
        writer.write("Binary mode not fully negotiated\r\n")

    writer.write("\r\nNegotiation complete. Goodbye!\r\n")
    await writer.drain()
    writer.close()


async def main():
    """Start the telnet server."""
    server = await telnetlib3.create_server(host="127.0.0.1", port=6023, shell=shell)
    print("Negotiation demo server running on localhost:6023")
    await server.wait_closed()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nServer stopped")
