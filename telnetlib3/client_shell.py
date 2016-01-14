# std imports
import asyncio
import contextlib
import sys


__all__ = ('telnet_client_shell', )


if sys.platform == 'win32':
    @asyncio.coroutine
    def telnet_client_shell(reader, writer):
        print('win32 not yet supported as telnet client. Please contribute!')

else:
    import termios
    import fcntl
    import tty
    import os

    @contextlib.contextmanager
    def _set_tty(fobj, tty_func):
        """
        Return context manager for manipulating stdin tty state.

        If stdin is not attached to a terminal, no action is performed
        before or after yielding.
        """
        save_mode = None
        if fobj.isatty():
            save_mode = termios.tcgetattr(fobj.fileno())
            tty_func(fobj.fileno(), termios.TCSANOW)
        try:
            yield
        finally:
            if not fobj.closed and fobj.isatty():
                termios.tcsetattr(fobj.fileno(), termios.TCSAFLUSH, save_mode)


    @asyncio.coroutine
    def _make_stdio(loop):
        import sys
        import os

        reader = asyncio.StreamReader()
        reader_protocol = asyncio.StreamReaderProtocol(reader)

        # we have a terrible bug here. This pattern was pulled from:
        #
        #   https://gist.github.com/nathan-hoad/8966377
        #
        # if we use "os.fdopen(...)" instead of "sys.stdout", as we do here,
        # our interactive terminal (when stdin is connected to a terminal)
        # is perfectly fine.
        #
        # however, if we use a pipe, like "echo input | telnetlib3-client ..."
        # then such program will lock up indefinitely. If we then switch to use
        # of sys.stdout, the pipe issue is resolved, but the following error
        # occurs in interactive use:
        #
        #   unix_events.py:492 pipe closed by peer or os.write(pipe, data)
        #                      raised exception.
        #
        # After some experimentation, this conditional seems to handle both
        # situations. Please do contribute if you can figure this one out.

        if not sys.stdin.isatty():
            write_fobj = sys.stdout
        else:
            write_fobj = sys.stdin

        writer_transport, writer_protocol = yield from loop.connect_write_pipe(
            asyncio.streams.FlowControlMixin, write_fobj)

        writer = asyncio.StreamWriter(
            writer_transport, writer_protocol, None, loop)

        yield from loop.connect_read_pipe(lambda: reader_protocol, sys.stdin)

        return reader, writer


    @asyncio.coroutine
    def telnet_client_shell(reader, writer):
        """
        Rudimentary telnet client shell.

        An interactive telnet session must naturally communicate with the
        standard in and output file descriptors -- one should be able to
        pipe timed input, just as done by nc(1).

        When standard input is connected to a terminal, the terminal mode is
        set using :func:`tty.setcbreak`, allowing ``Ctrl - C`` and other
        signal-generating characters may be used to abort the connection.
        """

        loop = asyncio.get_event_loop()

        with _set_tty(fobj=sys.stdin, tty_func=tty.setcbreak):
            stdin, stdout = yield from _make_stdio(loop)

            stdin_task = None
            telnet_task = None

            while True:
                stdin_task = stdin_task or asyncio.ensure_future(
                    stdin.read(2**12))
                telnet_task = telnet_task or asyncio.ensure_future(
                    reader.read(2**12))
                done, pending = yield from asyncio.wait(
                    [stdin_task, telnet_task],
                    return_when=asyncio.FIRST_COMPLETED)

                task = done.pop()

                if task == stdin_task:
                    # client input
                    inp = task.result()
                    if not inp:
                        # EOF
                        telnet_task.cancel()
                        break
                    writer.write(inp.decode())
                    if not writer.will_echo:
                        stdout.write(inp)
                    stdin_task = None

                else:
                    # server output
                    out = task.result()
                    if not out:
                        # EOF
                        stdin_task.cancel()
                        break

                    stdout.write(out.encode())
                    telnet_task = None
            writer.close()
