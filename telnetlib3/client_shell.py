# std imports
import collections
import contextlib
import logging
import asyncio
import sys

# local
from . import accessories

__all__ = ('telnet_client_shell', )


# TODO: needs 'wait_for' implementation (see DESIGN.rst)
# task = telnet_writer.wait_for(lambda: telnet_writer.local_mode[ECHO] == True)

if sys.platform == 'win32':
    @asyncio.coroutine
    def telnet_client_shell(telnet_reader, telnet_writer):
        raise NotImplementedError(
            'win32 not yet supported as telnet client. Please contribute!')

else:
    import termios
    import os

    @contextlib.contextmanager
    def _set_tty(fobj, tty_func):
        """
        return context manager for manipulating stdin tty state.

        if stdin is not attached to a terminal, no action is performed
        before or after yielding.
        """

    class Terminal(object):
        """
        Context manager that yields (sys.stdin, sys.stdout) for POSIX systems.

        When sys.stdin is a attached to a terminal, it is configured for
        the matching telnet modes negotiated for the given telnet_writer.
        """
        ModeDef = collections.namedtuple(
            'mode', ['iflag', 'oflag', 'cflag', 'lflag',
                     'ispeed', 'ospeed', 'cc'])

        def __init__(self, telnet_writer, loop=None):
            self.telnet_writer = telnet_writer
            self.loop = loop or asyncio.get_event_loop()
            self._fileno = sys.stdin.fileno()
            self._istty = os.path.sameopenfile(0, 1)

        def __enter__(self):
            self._save_mode = self.get_mode()
            if self._istty:
                self.set_mode(self.determine_mode(self._save_mode))
            return self

        def __exit__(self, *_):
            if self._istty:
                termios.tcsetattr(
                    self._fileno, termios.TCSAFLUSH, list(self._save_mode))

        def get_mode(self):
            if self._istty:
                return self.ModeDef(*termios.tcgetattr(self._fileno))

        def set_mode(self, mode):
            termios.tcsetattr(
                sys.stdin.fileno(), termios.TCSAFLUSH, list(mode))

        def determine_mode(self, mode):
            """
            Return copy of 'mode' with changes suggested for telnet connection.
            """
            from telnetlib3.telopt import ECHO

            if not self.telnet_writer.will_echo:
                # return mode as-is
                self.telnet_writer.log.debug('local echo, linemode')
                return mode
            self.telnet_writer.log.debug('server echo, kludge mode')

            # "Raw mode", see tty.py function setraw.  This allows sending
            # of ^J, ^C, ^S, ^\, and others, which might otherwise
            # interrupt with signals or map to another character.  We also
            # trust the remote server to manage CR/LF without mapping.
            #
            iflag = mode.iflag & ~(
                termios.BRKINT |  # Do not send INTR signal on break
                termios.ICRNL  |  # Do not map CR to NL on input
                termios.INPCK  |  # Disable input parity checking
                termios.ISTRIP |  # Do not strip input characters to 7 bits
                termios.IXON)     # Disable START/STOP output control

            # Disable parity generation and detection,
            # Select eight bits per byte character size.
            cflag = mode.cflag & ~(termios.CSIZE | termios.PARENB)
            cflag = cflag | termios.CS8

            # Disable canonical input (^H and ^C processing),
            # disable any other special control characters,
            # disable checking for INTR, QUIT, and SUSP input.
            lflag = mode.lflag & ~(
                termios.ICANON | termios.IEXTEN | termios.ISIG | termios.ECHO)

            # Disable post-output processing,
            # such as mapping LF('\n') to CRLF('\r\n') in output.
            oflag = mode.oflag & ~(termios.OPOST | termios.ONLCR)

            # "A pending read is not satisfied until MIN bytes are received
            #  (i.e., the pending read until MIN bytes are received), or a
            #  signal is received.  A program that uses this case to read
            #  record-based terminal I/O may block indefinitely in the read
            #  operation."
            cc = list(mode.cc)
            cc[termios.VMIN] = 1
            cc[termios.VTIME] = 0

            return self.ModeDef(
                iflag=iflag, oflag=oflag, cflag=cflag, lflag=lflag,
                ispeed=mode.ispeed, ospeed=mode.ospeed, cc=cc)

        @asyncio.coroutine
        def make_stdio(self):
            """
            Return (reader, writer) pair for sys.stdin, sys.stdout.

            This method is a coroutine.
            """
            reader = asyncio.StreamReader()
            reader_protocol = asyncio.StreamReaderProtocol(reader)

            # Thanks:
            #
            #   https://gist.github.com/nathan-hoad/8966377
            #
            # After some experimentation, this 'sameopenfile' conditional seems
            # allow us to handle stdin as a pipe or a keyboard.  In the case of
            # a tty, 0 and 1 are the same open file, we use:
            #
            #    https://github.com/orochimarufan/.files/blob/master/bin/mpr
            write_fobj = sys.stdout
            if self._istty:
                write_fobj = sys.stdin

            writer_transport, writer_protocol = yield from (
                self.loop.connect_write_pipe(
                    asyncio.streams.FlowControlMixin, write_fobj))

            writer = asyncio.StreamWriter(
                writer_transport, writer_protocol, None, self.loop)

            yield from self.loop.connect_read_pipe(
                lambda: reader_protocol, sys.stdin)

            return reader, writer

    @asyncio.coroutine
    def telnet_client_shell(telnet_reader, telnet_writer):
        """
        Minimal telnet client shell for POSIX terminals.

        This shell performs minimal tty mode handling when a terminal is
        attached to standard in (keyboard), notably raw mode is often set
        and this shell may exit only by disconnect from server, or the
        escape character, ^].

        stdin or stdout may also be a pipe or file, behaving much like nc(1).

        This function is a :func:`~asyncio.coroutine`.
        """
        loop = asyncio.get_event_loop()
        keyboard_escape = '\x1d'

        with Terminal(telnet_writer=telnet_writer, loop=loop) as term:
            linesep = '\n'
            if term._istty and telnet_writer.will_echo:
                linesep = '\r\n'
            stdin, stdout = yield from term.make_stdio()
            stdout.write("Escape character is '{escape}'.{linesep}".format(
                escape=accessories.name_unicode(keyboard_escape),
                linesep=linesep).encode())

            stdin_task = accessories.make_reader_task(stdin)
            telnet_task = accessories.make_reader_task(telnet_reader)
            wait_for = set([stdin_task, telnet_task])
            while wait_for:
                done, pending = yield from asyncio.wait(
                    wait_for, return_when=asyncio.FIRST_COMPLETED)

                task = done.pop()
                wait_for.remove(task)

                telnet_writer.log.debug('task=%s, wait_for=%s', task, wait_for)

                # client input
                if task == stdin_task:
                    inp = task.result()
                    if inp:
                        if keyboard_escape in inp.decode():
                            # on ^], close connection to remote host
                            telnet_task.cancel()
                            wait_for.remove(telnet_task)
                            stdout.write(
                                "\033[m{linesep}Connection closed.{linesep}"
                                .format(linesep=linesep).encode())
                        else:
                            telnet_writer.write(inp.decode())
                            stdin_task = accessories.make_reader_task(stdin)
                            wait_for.add(stdin_task)
                    else:
                        telnet_writer.log.debug('EOF from client stdin')

                # server output
                if task == telnet_task:
                    out = task.result()

                    # TODO: We should not require to check for '_eof' value,
                    # but for some systems, htc.zapto.org, it is required,
                    # where b'' is received even though connection is on?.
                    if not out and telnet_reader._eof:
                        if stdin_task in wait_for:
                            stdin_task.cancel()
                            wait_for.remove(stdin_task)
                        stdout.write(("\033[m{linesep}Connection closed "
                                      "by foreign host.{linesep}").format(
                                          linesep=linesep).encode())
                    else:
                        stdout.write(out.encode() or b':?!?:')
                        telnet_task = accessories.make_reader_task(
                            telnet_reader)
                        wait_for.add(telnet_task)
