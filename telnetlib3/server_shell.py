"""Telnet server shell implementations."""

from __future__ import annotations

# std imports
import types
import asyncio
from typing import Union, Optional, Generator, cast

# local
from . import slc, telopt, accessories
from .stream_reader import TelnetReader, TelnetReaderUnicode
from .stream_writer import TelnetWriter, TelnetWriterUnicode

CR, LF, NUL = ("\r", "\n", "\x00")
ESC = "\x1b"


async def filter_ansi(
    reader: TelnetReaderUnicode,
    _writer: TelnetWriterUnicode,
) -> str:
    """
    Read and return the next non-ANSI-escape character from reader.

    ANSI escape sequences (ESC [ ... final_byte) are silently consumed.
    """
    while True:
        char = await reader.read(1)
        if not char:
            return ""
        if char != ESC:
            return char
        # Consume escape sequence: ESC [ (params) final_byte
        next_char = await reader.read(1)
        if next_char != "[":
            # Not a CSI sequence, return the second char
            return next_char
        # Read until final byte (0x40-0x7E)
        while True:
            seq_char = await reader.read(1)
            if not seq_char or (0x40 <= ord(seq_char) <= 0x7E):
                break


__all__ = ("telnet_server_shell",)


async def telnet_server_shell(  # pylint: disable=too-complex,too-many-branches,too-many-statements
    reader: Union[TelnetReader, TelnetReaderUnicode],
    writer: Union[TelnetWriter, TelnetWriterUnicode],
) -> None:
    """
    A default telnet shell, appropriate for use with telnetlib3.create_server.

    This shell provides a very simple REPL, allowing introspection and state toggling of the
    connected client session.
    """
    _reader = cast(TelnetReaderUnicode, reader)
    writer = cast(TelnetWriterUnicode, writer)
    linereader = readline(_reader, writer)
    next(linereader)

    writer.write("Ready." + CR + LF)

    command = None
    while not writer.is_closing():
        if command:
            writer.write(CR + LF)
        writer.write("tel:sh> ")
        if not getattr(writer.protocol, "never_send_ga", False):
            writer.send_ga()
        await writer.drain()

        command = None
        while command is None:
            await writer.drain()
            inp = await _reader.read(1)
            if not inp:
                # close/eof by client at prompt
                return
            command = linereader.send(inp)
        writer.write(CR + LF)

        if command == "quit":
            # server hangs up on client
            writer.write("Goodbye." + CR + LF)
            break
        if command == "help":
            writer.write("quit, writer, slc, toggle [option|all], reader, proto, dump")
        elif command == "writer":
            # show 'writer' status
            writer.write(repr(writer))
        elif command == "reader":
            # show 'reader' status
            writer.write(repr(reader))
        elif command == "proto":
            # show 'proto' details of writer
            writer.write(repr(writer.protocol))
        elif command == "version":
            writer.write(accessories.get_version())
        elif command == "slc":
            # show 'slc' support and data tables
            writer.write(get_slcdata(writer))
        elif command.startswith("toggle"):
            # toggle specified options
            option = command[len("toggle ") :] or None
            writer.write(do_toggle(writer, option))
        elif command.startswith("dump"):
            # dump [kb] [ms_delay] [drain|nodrain] [close|noclose]
            #
            # this allows you to experiment with the effects of 'drain', and,
            # some longer-running programs that check for early break through
            # writer.is_closing().
            try:
                kb_limit = int(command.split()[1])
            except (ValueError, IndexError):
                kb_limit = 1000
            try:
                delay = int(float(command.split()[2]) / 1000)
            except (ValueError, IndexError):
                delay = 0
            # experiment with large sizes and 'nodrain', the server pretty much
            # locks up and stops talking to new clients.
            try:
                drain = command.split()[3].lower() == "nodrain"
            except IndexError:
                drain = True
            try:
                do_close = command.split()[4].lower() == "close"
            except IndexError:
                do_close = False
            msg = f"kb_limit={kb_limit}, delay={delay}, drain={drain}, do_close={do_close}:\r\n"
            writer.write(msg)
            for lineout in character_dump(kb_limit):
                if writer.is_closing():
                    break
                writer.write(lineout)
                if drain:
                    await writer.drain()
                if delay:
                    await asyncio.sleep(delay)

            if not writer.is_closing():
                writer.write(f"\r\n{kb_limit} OK")
            if do_close:
                break
        elif command:
            writer.write("no such command.")
    writer.close()


def character_dump(kb_limit: int) -> Generator[str, None, None]:
    """Generate character dump output up to kb_limit kilobytes."""
    num_bytes = 0
    while (num_bytes) < (kb_limit * 1024):
        for char in ("/", "\\"):
            lineout = (char * 80) + "\033[1G"
            yield lineout
            num_bytes += len(lineout)
    yield "\033[1G" + "wrote " + str(num_bytes) + " bytes"


async def get_next_ascii(
    reader: Union[TelnetReader, TelnetReaderUnicode],
    writer: Union[TelnetWriter, TelnetWriterUnicode],
) -> Optional[str]:
    """Accept the next non-ANSI-escape character from reader."""
    _reader = cast(TelnetReaderUnicode, reader)
    escape_sequence = False
    while not writer.is_closing():
        next_char = await _reader.read(1)
        if next_char == "\x1b":
            escape_sequence = True
        elif escape_sequence:
            if 61 <= ord(next_char) <= 90 or 97 <= ord(next_char) <= 122:
                escape_sequence = False
        else:
            return next_char
    return None


@types.coroutine
def readline(
    _reader: Union[TelnetReader, TelnetReaderUnicode],
    writer: Union[TelnetWriter, TelnetWriterUnicode],
) -> Generator[Optional[str], str, None]:
    """
    A very crude readline coroutine interface.

    This is a legacy function
    designed for Python 3.4 and remains here for compatibility, superseded by
    :func:`~.readline2`
    """
    _writer = cast(TelnetWriterUnicode, writer)
    command, inp, last_inp = "", "", ""
    inp = yield None
    while True:
        if inp in (LF, NUL) and last_inp == CR:
            last_inp = inp
            inp = yield None

        elif inp in (CR, LF):
            # first CR or LF yields command
            last_inp = inp
            inp = yield command
            command = ""

        elif inp in ("\b", "\x7f"):
            # backspace over input
            if command:
                command = command[:-1]
                _writer.echo("\b \b")
            last_inp = inp
            inp = yield None

        else:
            # buffer and echo input
            command += inp
            _writer.echo(inp)
            last_inp = inp
            inp = yield None


async def readline2(
    reader: Union[TelnetReader, TelnetReaderUnicode],
    writer: Union[TelnetWriter, TelnetWriterUnicode],
) -> Optional[str]:
    """
    Async readline interface that filters ANSI escape sequences.

    This version attempts to filter away escape sequences, such as when a user
    presses an arrow or function key. Delete key is backspace.

    However, this function does not handle all possible types of carriage
    returns and so it is not used by default shell, :func:`telnet_server_shell`.
    """
    _reader = cast(TelnetReaderUnicode, reader)
    _writer = cast(TelnetWriterUnicode, writer)
    command = ""
    while True:
        next_char = await filter_ansi(_reader, _writer)

        if next_char == CR:
            return command

        if next_char in (LF, NUL) and len(command) == 0:
            continue

        if next_char in ("\b", "\x7f"):
            # backspace over input
            if len(command) > 0:
                command = command[:-1]
                _writer.echo("\b \b")

        elif not next_char:
            return None

        else:
            command += next_char
            _writer.echo(next_char)


def get_slcdata(writer: Union[TelnetWriter, TelnetWriterUnicode]) -> str:
    """Display Special Line Editing (SLC) characters."""
    _slcs = sorted(
        [
            f"{slc.name_slc_command(slc_func):>15}: {slc_def}"
            for (slc_func, slc_def) in sorted(writer.slctab.items())
            if not (slc_def.nosupport or slc_def.val == slc.theNULL)
        ]
    )
    _unset = sorted(
        [
            slc.name_slc_command(slc_func)
            for (slc_func, slc_def) in sorted(writer.slctab.items())
            if slc_def.val == slc.theNULL
        ]
    )
    _nosupport = sorted(
        [
            slc.name_slc_command(slc_func)
            for (slc_func, slc_def) in sorted(writer.slctab.items())
            if slc_def.nosupport
        ]
    )

    return (
        "Special Line Characters:\r\n"
        + "\r\n".join(_slcs)
        + "\r\nUnset by client: "
        + ", ".join(_unset)
        + "\r\nNot supported by server: "
        + ", ".join(_nosupport)
    )


def do_toggle(
    writer: Union[TelnetWriter, TelnetWriterUnicode],
    option: Optional[str],
) -> str:
    """Display or toggle telnet session parameters."""
    tbl_opt = {
        "echo": writer.local_option.enabled(telopt.ECHO),
        "goahead": not writer.local_option.enabled(telopt.SGA),
        "outbinary": writer.outbinary,
        "inbinary": writer.inbinary,
        "binary": writer.outbinary and writer.inbinary,
        "xon-any": writer.xon_any,
        "lflow": writer.lflow,
    }

    if not option:
        return "\r\n".join(
            f"{opt} {'ON' if enabled else 'off'}" for opt, enabled in sorted(tbl_opt.items())
        )

    msgs = []
    if option in ("echo", "all"):
        cmd = telopt.WONT if tbl_opt["echo"] else telopt.WILL
        writer.iac(cmd, telopt.ECHO)
        msgs.append(f"{telopt.name_command(cmd).lower()} echo.")

    if option in ("goahead", "all"):
        cmd = telopt.WILL if tbl_opt["goahead"] else telopt.WONT
        writer.iac(cmd, telopt.SGA)
        msgs.append(f"{telopt.name_command(cmd).lower()} suppress go-ahead.")

    if option in ("outbinary", "binary", "all"):
        cmd = telopt.WONT if tbl_opt["outbinary"] else telopt.WILL
        writer.iac(cmd, telopt.BINARY)
        msgs.append(f"{telopt.name_command(cmd).lower()} outbinary.")

    if option in ("inbinary", "binary", "all"):
        cmd = telopt.DONT if tbl_opt["inbinary"] else telopt.DO
        writer.iac(cmd, telopt.BINARY)
        msgs.append(f"{telopt.name_command(cmd).lower()} inbinary.")

    if option in ("xon-any", "all"):
        writer.xon_any = not tbl_opt["xon-any"]
        writer.send_lineflow_mode()
        msgs.append(f"xon-any {'en' if writer.xon_any else 'dis'}abled.")

    if option in ("lflow", "all"):
        writer.lflow = not tbl_opt["lflow"]
        writer.send_lineflow_mode()
        msgs.append(f"lineflow {'en' if writer.lflow else 'dis'}abled.")

    if option not in tbl_opt and option != "all":
        msgs.append("toggle: not an option.")

    return "\r\n".join(msgs)
