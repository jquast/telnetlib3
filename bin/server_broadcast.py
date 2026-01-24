#!/usr/bin/env python
"""
Telnet server that broadcasts messages to all connected clients.

This example demonstrates using server.clients to access all connected
protocols and broadcast messages. It also shows wait_for() to await
specific negotiation states.

Run this server, then connect multiple telnet clients. Messages typed
in one client will be broadcast to all others.
"""

# std imports
import asyncio

# local
import telnetlib3


async def handle_client(server, client, client_id):
    """Handle a single client, broadcasting their input to all others."""
    client.writer.write(f"\r\nYou are client #{client_id}\r\n")
    client.writer.write("Type messages to broadcast (Ctrl+] to disconnect)\r\n\r\n")

    # Wait for BINARY mode if available
    try:
        await asyncio.wait_for(client.writer.wait_for(remote={"BINARY": True}), timeout=2.0)
    except asyncio.TimeoutError:
        pass  # Continue without BINARY mode

    while True:
        data = await client.reader.read(1024)
        if not data:
            break

        # Broadcast to all other clients
        message = f"[Client #{client_id}]: {data}"
        for other in server.clients:
            if other is not client:
                other.writer.write(message)

    # Notify others of disconnect
    for other in server.clients:
        if other is not client:
            other.writer.write(f"\r\n[Client #{client_id} disconnected]\r\n")


async def main():
    """Start server and handle client connections."""
    server = await telnetlib3.create_server(host="127.0.0.1", port=6023)
    print("Broadcast server running on localhost:6023")
    print("Connect multiple clients with: telnet localhost 6023")

    client_counter = 0
    tasks = []

    try:
        while True:
            client = await server.wait_for_client()
            client_counter += 1
            print(f"Client #{client_counter} connected (total: {len(server.clients)})")

            # Handle each client in a separate task
            task = asyncio.create_task(handle_client(server, client, client_counter))
            tasks.append(task)

    except KeyboardInterrupt:
        print("\nShutting down...")
        server.close()
        await server.wait_closed()
        for task in tasks:
            task.cancel()


if __name__ == "__main__":
    asyncio.run(main())
