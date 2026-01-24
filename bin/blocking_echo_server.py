#!/usr/bin/env python
"""
Blocking (synchronous) telnet echo server.

This example demonstrates using BlockingTelnetServer for a traditional
threaded server that doesn't require asyncio knowledge.

Each client connection is handled in a separate thread.

Example session::

    $ telnet localhost 6023
    Welcome! Type messages and I'll echo them back.
    Type 'quit' to disconnect.

    hello
    Echo: hello
    quit
    Goodbye!
"""

# local
from telnetlib3.sync import BlockingTelnetServer


def handle_client(conn):
    """Handle a single client connection (runs in its own thread)."""
    conn.write("Welcome! Type messages and I'll echo them back.\r\n")
    conn.write("Type 'quit' to disconnect.\r\n\r\n")
    conn.flush()

    while True:
        try:
            line = conn.readline(timeout=300)  # 5 minute timeout
            if not line:
                break

            line = line.strip()
            if line.lower() == "quit":
                conn.write("Goodbye!\r\n")
                conn.flush()
                break

            conn.write(f"Echo: {line}\r\n")
            conn.flush()
        except TimeoutError:
            conn.write("\r\nTimeout - disconnecting.\r\n")
            conn.flush()
            break

    conn.close()


def main():
    """Start the blocking echo server."""
    server = BlockingTelnetServer("127.0.0.1", 6023, handler=handle_client)
    print("Blocking echo server running on localhost:6023")
    print("Connect with: telnet localhost 6023")
    print("Press Ctrl+C to stop")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        server.shutdown()


if __name__ == "__main__":
    main()
