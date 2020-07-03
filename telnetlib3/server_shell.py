import asyncio

CR, LF, NUL = '\r\n\x00'
from . import slc
from . import telopt
from . import accessories

__all__ = ('telnet_server_shell',)


@asyncio.coroutine
def telnet_server_shell(reader, writer):
    """
    A default telnet shell, appropriate for use with telnetlib3.create_server.

    This shell provides a very simple REPL, allowing introspection and state
    toggling of the connected client session.

    This function is a :func:`~asyncio.coroutine`.
    """
    writer.write("Ready." + CR + LF)

    linereader = readline(reader, writer)
    linereader.send(None)

    command = None
    while True:
        if command:
            writer.write(CR + LF)
        writer.write('tel:sh> ')
        command = None
        while command is None:
            # TODO: use reader.readline()
            inp = yield from reader.read(1)
            if not inp:
                return
            command = linereader.send(inp)
        writer.write(CR + LF)
        if command == 'quit':
            writer.write('Goodbye.' + CR + LF)
            break
        elif command == 'help':
            writer.write('quit, writer, slc, toggle [option|all], '
                         'reader, proto')
        elif command == 'writer':
            writer.write(repr(writer))
        elif command == 'reader':
            writer.write(repr(reader))
        elif command == 'proto':
            writer.write(repr(writer.protocol))
        elif command == 'version':
            writer.write(accessories.get_version())
        elif command == 'slc':
            writer.write(get_slcdata(writer))
        elif command.startswith('toggle'):
            option = command[len('toggle '):] or None
            writer.write(do_toggle(writer, option))
        elif command:
            writer.write('no such command.')
    writer.close()


@asyncio.coroutine
def readline(reader, writer):
    """
    A very crude readline coroutine interface.

    This function is a :func:`~asyncio.coroutine`.
    """
    command, inp, last_inp = '', '', ''
    inp = yield None
    while True:
        if inp in (LF, NUL) and last_inp == CR:
            last_inp = inp
            inp = yield None

        elif inp in (CR, LF):
            # first CR or LF yields command
            last_inp = inp
            inp = yield command
            command = ''

        elif inp in ('\b', '\x7f'):
            # backspace over input
            if command:
                command = command[:-1]
                writer.echo('\b \b')
            last_inp = inp
            inp = yield None

        else:
            # buffer and echo input
            command += inp
            writer.echo(inp)
            last_inp = inp
            inp = yield None


def get_slcdata(writer):
    """Display Special Line Editing (SLC) characters."""
    _slcs = sorted([
        '{:>15}: {}'.format(slc.name_slc_command(slc_func), slc_def)
        for (slc_func, slc_def) in sorted(writer.slctab.items())
        if not (slc_def.nosupport or slc_def.val == slc.theNULL)])
    _unset = sorted([
        slc.name_slc_command(slc_func)
        for (slc_func, slc_def) in sorted(writer.slctab.items())
        if slc_def.val == slc.theNULL])
    _nosupport = sorted([
        slc.name_slc_command(slc_func)
        for (slc_func, slc_def) in sorted(
            writer.slctab.items())
        if slc_def.nosupport])

    return ('Special Line Characters:\r\n' +
            '\r\n'.join(_slcs) +
            '\r\nUnset by client: ' +
            ', '.join(_unset) +
            '\r\nNot supported by server: ' +
            ', '.join(_nosupport))


def do_toggle(writer, option):
    """Display or toggle telnet session parameters."""
    tbl_opt = {
        'echo': writer.local_option.enabled(telopt.ECHO),
        'goahead': not writer.local_option.enabled(telopt.SGA),
        'outbinary': writer.outbinary,
        'inbinary': writer.inbinary,
        'binary': writer.outbinary and writer.inbinary,
        'xon-any': writer.xon_any,
        'lflow': writer.lflow,
    }

    if not option:
        return ('\r\n'.join('{0} {1}'.format(
            opt, 'ON' if enabled else 'off')
            for opt, enabled in sorted(tbl_opt.items())))

    msgs = []
    if option in ('echo', 'all'):
        cmd = (telopt.WONT if tbl_opt['echo'] else telopt.WILL)
        writer.iac(cmd, telopt.ECHO)
        msgs.append('{} echo.'.format(
            telopt.name_command(cmd).lower()))

    if option in ('goahead', 'all'):
        cmd = (telopt.WILL if tbl_opt['goahead'] else telopt.WONT)
        writer.iac(cmd, telopt.SGA)
        msgs.append('{} suppress go-ahead.'.format(
            telopt.name_command(cmd).lower()))

    if option in ('outbinary', 'binary', 'all'):
        cmd = (telopt.WONT if tbl_opt['outbinary'] else telopt.WILL)
        writer.iac(cmd, telopt.BINARY)
        msgs.append('{} outbinary.'.format(
            telopt.name_command(cmd).lower()))

    if option in ('inbinary', 'binary', 'all'):
        cmd = (telopt.DONT if tbl_opt['inbinary'] else telopt.DO)
        writer.iac(cmd, telopt.BINARY)
        msgs.append('{} inbinary.'.format(
            telopt.name_command(cmd).lower()))

    if option in ('xon-any', 'all'):
        writer.xon_any = not tbl_opt['xon-any']
        writer.send_lineflow_mode()
        msgs.append('xon-any {}abled.'.format(
            'en' if writer.xon_any else 'dis'))

    if option in ('lflow', 'all'):
        writer.lflow = not tbl_opt['lflow']
        writer.send_lineflow_mode()
        msgs.append('lineflow {}abled.'.format(
            'en' if writer.lflow else 'dis'))

    if option not in tbl_opt and option != 'all':
        msgs.append('toggle: not an option.')


    return '\r\n'.join(msgs)
