import types
import asyncio

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
    linereader = readline(reader, writer)
    linereader.send(None)

    writer.write("Ready." + CR + LF)

    command = None
    while not writer.is_closing():
        if command:
            writer.write(CR + LF)
        writer.write("tel:sh> ")
        await writer.drain()

        command = None
        while command is None:
            await writer.drain()
            inp = await reader.read(1)
            if not inp:
                # close/eof by client at prompt
                return
            command = linereader.send(inp)
        writer.write(CR + LF)

        if command == "quit":
            # server hangs up on client
            writer.write("Goodbye." + CR + LF)
            break
        elif command == "help":
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
            writer.write(
                "kb_limit={}, delay={}, drain={}, do_close={}:\r\n".format(
                    kb_limit, delay, drain, do_close
                )
            )
            for lineout in character_dump(kb_limit):
                if writer.is_closing():
                    break
                writer.write(lineout)
                if drain:
                    await writer.drain()
                if delay:
                    await asyncio.sleep(delay)

            if not writer.is_closing():
                writer.write("\r\n{} OK".format(kb_limit))
            if do_close:
                break
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


async def get_next_ascii(reader, writer):
    """
    A coroutine that accepts the next character from `reader` that is not a
    part of an ANSI escape sequence.
    """
    escape_sequence = False
    while not writer.is_closing():
        next_char = await reader.read(1)
        if next_char == "\x1b":
            escape_sequence = True
        elif escape_sequence:
            if 61 <= ord(next_char) <= 90 or 97 <= ord(next_char) <= 122:
                escape_sequence = False
        else:
            return next_char
    return None


@types.coroutine
def readline(reader, writer):
    """
    A very crude readline coroutine interface. This is a legacy function
    designed for Python 3.4 and remains here for compatibility, superseded by
    :func:`~.readline2`
    """
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
                writer.echo("\b \b")
            last_inp = inp
            inp = yield None

        else:
            # buffer and echo input
            command += inp
            writer.echo(inp)
            last_inp = inp
            inp = yield None


async def readline2(reader, writer):
    """
    Another crude readline interface as a more amiable asynchronous function
    than :func:`readline` supplied with the earliest version of this library.

    This version attempts to filter away escape sequences, such as when a user
    presses an arrow or function key. Delete key is backspace.

    However, this function does not handle all possible types of carriage
    returns and so it is not used by default shell, :func:`telnet_server_shell`.
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
