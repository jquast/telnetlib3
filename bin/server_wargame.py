"""
Shell callback: simple "war game" question.

Usage::

    telnetlib3-server --shell=bin.server_wargame.shell

Then connect with::

    telnet localhost 6023

Example session::

    $ telnet localhost 6023
    Escape character is '^]'.

    Would you like to play a game? y
    They say the only way to win is to not play at all.
    Connection closed by foreign host.
"""


async def shell(reader, writer):
    """Handle a single client connection."""
    writer.write("\r\nWould you like to play a game? ")
    inp = await reader.read(1)
    if inp:
        writer.echo(inp)
        writer.write("\r\nThey say the only way to win is to not play at all.\r\n")
        await writer.drain()
    writer.close()
