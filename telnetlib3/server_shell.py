import types

CR, LF, NUL = "\r\n\x00"
from . import slc
from . import telopt
from . import accessories

__all__ = ("telnet_server_shell",)


async def telnet_server_shell(reader, writer):
    """
    A default telnet shell, appropriate for use with telnetlib3.create_server.

    This shell provides a very simple REPL, allowing introspection and state
    toggling of the connected client session.
    """
    writer.write("Ready." + CR + LF)

    command = None
    while True:
        if command:
            writer.write(CR + LF)
        writer.write("tel:sh> ")

        command = await readline(reader, writer)
        if command is None:
            writer.write("Read stream EOF")
            break

        writer.write(CR + LF)

        if command == "quit":
            writer.write("Goodbye." + CR + LF)
            break
        elif command == "help":
            writer.write("quit, writer, slc, toggle [option|all], reader, proto, dump")
        elif command == "writer":
            writer.write(repr(writer))
        elif command == "reader":
            writer.write(repr(reader))
        elif command == "proto":
            writer.write(repr(writer.protocol))
        elif command == "version":
            writer.write(accessories.get_version())
        elif command == "slc":
            writer.write(get_slcdata(writer))
        elif command.startswith("toggle"):
            option = command[len("toggle ") :] or None
            writer.write(do_toggle(writer, option))
        elif command.startswith("dump"):
            # dump [kb] [ms_delay] [drain|nodrain]
            try:
                kb_limit = int(command.split()[1])
            except (ValueError, IndexError):
                kb_limit = 1000
            try:
                ms_delay = int(command.split()[2]) * 1000
            except (ValueError, IndexError):
                ms_delay = 0
            try:
                drain = command.split()[3] == "drain"
            except IndexError:
                drain = False
            for lineout in character_dump(kb_limit):
                writer.write(lineout)
                if ms_delay:
                    await asyncio.sleep(ms_delay)
                if drain:
                    await writer.drain()
        elif command:
            writer.write("no such command.")
    writer.close()


def character_dump(kb_limit):
    num_bytes = 0
    while (num_bytes) < (kb_limit * 1024):
        for char in ("/", "\\"):
            lineout = (char * 80) + "\033[1G"
            yield lineout
            num_bytes += len(lineout)
    yield ("\033[1G" + "wrote " + str(num_bytes) + " bytes")


async def filter_ansi(reader, writer):
    """
    A coroutine that accepts the next character from `reader` that is not a 
    part of an ANSI escape sequence.
    """
    escape_sequence = False
    while True:
        next_char = await reader.read(1)
        if next_char == "\x1b":
            escape_sequence = True
        elif escape_sequence:
            if 61 <= ord(next_char) <= 90 or 97 <= ord(next_char) <= 122:
                escape_sequence = False
        else:
            return next_char


async def readline(reader, writer):
    """
    A very crude readline coroutine interface.
    Returns None on EOF.
    """
    command = ""
    while True:
        next_char = await filter_ansi(reader, writer)
        
        if next_char == CR:
            return command
        
        elif next_char in (LF, NUL) and len(command) == 0:
            continue

        elif next_char in ("\b", "\x7f"):
            # backspace over input
            if len(command) > 0:
                command = command[:-1]
                writer.echo("\b \b")

        elif next_char == "":
            return None

        else:
            print(f"got character {ord(next_char)}")
            command += next_char
            writer.echo(next_char)


def get_slcdata(writer):
    """Display Special Line Editing (SLC) characters."""
    _slcs = sorted(
        [
            "{:>15}: {}".format(slc.name_slc_command(slc_func), slc_def)
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


def do_toggle(writer, option):
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
            "{0} {1}".format(opt, "ON" if enabled else "off")
            for opt, enabled in sorted(tbl_opt.items())
        )

    msgs = []
    if option in ("echo", "all"):
        cmd = telopt.WONT if tbl_opt["echo"] else telopt.WILL
        writer.iac(cmd, telopt.ECHO)
        msgs.append("{} echo.".format(telopt.name_command(cmd).lower()))

    if option in ("goahead", "all"):
        cmd = telopt.WILL if tbl_opt["goahead"] else telopt.WONT
        writer.iac(cmd, telopt.SGA)
        msgs.append("{} suppress go-ahead.".format(telopt.name_command(cmd).lower()))

    if option in ("outbinary", "binary", "all"):
        cmd = telopt.WONT if tbl_opt["outbinary"] else telopt.WILL
        writer.iac(cmd, telopt.BINARY)
        msgs.append("{} outbinary.".format(telopt.name_command(cmd).lower()))

    if option in ("inbinary", "binary", "all"):
        cmd = telopt.DONT if tbl_opt["inbinary"] else telopt.DO
        writer.iac(cmd, telopt.BINARY)
        msgs.append("{} inbinary.".format(telopt.name_command(cmd).lower()))

    if option in ("xon-any", "all"):
        writer.xon_any = not tbl_opt["xon-any"]
        writer.send_lineflow_mode()
        msgs.append("xon-any {}abled.".format("en" if writer.xon_any else "dis"))

    if option in ("lflow", "all"):
        writer.lflow = not tbl_opt["lflow"]
        writer.send_lineflow_mode()
        msgs.append("lineflow {}abled.".format("en" if writer.lflow else "dis"))

    if option not in tbl_opt and option != "all":
        msgs.append("toggle: not an option.")

    return "\r\n".join(msgs)
