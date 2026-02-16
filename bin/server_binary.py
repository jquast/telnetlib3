"""
Shell callback: binary (raw bytes) echo server.

Usage::

    telnetlib3-server --encoding=false --shell=bin.server_binary.shell

When ``encoding=False``, the shell receives ``TelnetReader`` and
``TelnetWriter``, which read and write ``bytes`` instead of ``str``.

Example session::

    $ telnet localhost 6023
    Escape character is '^]'.
    [binary echo server] type something:
    hello
    hex: 68 65 6c 6c 6f 0d 0a
    Connection closed by foreign host.
"""


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
