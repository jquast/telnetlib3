#!/usr/bin/env python
"""
Blocking (synchronous) telnet client.

This example demonstrates using TelnetConnection for a traditional
blocking client that doesn't require asyncio knowledge.

Example usage::

    $ python blocking_client.py localhost 6023
    Connected to localhost:6023
    >>> hello
    Echo: hello
    >>> quit
    Goodbye!
    Connection closed.
"""

# std imports
import sys

# local
from telnetlib3.sync import TelnetConnection


def main():
    """Connect to a telnet server and interact."""
    host = sys.argv[1] if len(sys.argv) > 1 else "localhost"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 6023

    print(f"Connecting to {host}:{port}...")

    with TelnetConnection(host, port, timeout=10) as conn:
        print(f"Connected to {host}:{port}")

        # Read initial server greeting
        try:
            greeting = conn.read(timeout=2)
            if greeting:
                print(greeting, end="")
        except TimeoutError:
            pass

        # Interactive loop
        while True:
            try:
                user_input = input(">>> ")
                conn.write(user_input + "\r\n")
                conn.flush()

                # Read response
                response = conn.read(timeout=5)
                if response:
                    print(response, end="")

                if not response or "goodbye" in response.lower():
                    break

            except (EOFError, KeyboardInterrupt):
                print("\nDisconnecting...")
                break
            except TimeoutError:
                print("(no response)")

    print("Connection closed.")


if __name__ == "__main__":
    main()
