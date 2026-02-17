"""
Shell callback: telnet client that auto-answers the "war game".

Run ``server_wargame`` first, then this client.

Usage::

    telnetlib3-client --shell=bin.client_wargame.shell localhost 6023

Example output::

    Would you like to play a game? y
    They say the only way to win is to not play at all.
"""


async def shell(reader, writer):
    """Handle client session, auto-answering questions."""
    while True:
        outp = await reader.read(1024)
        if not outp:
            break
        if "?" in outp:
            writer.write("y\r\n")

        print(outp, flush=True, end="")

    print()
