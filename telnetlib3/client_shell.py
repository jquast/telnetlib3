# std imports
import asyncio
import contextlib
import sys


__all__ = ('telnet_client_shell', )


@asyncio.coroutine
def telnet_client_shell(reader, writer):

    def on_input():
        from telnetlib3 import ECHO
        ucs = sys.stdin.read(2**12)
        writer.write(ucs)
        if not writer.remote_option.enabled(ECHO):
            print(ucs, end='', flush=True)

    loop = asyncio.get_event_loop()
    stdin_fd = sys.stdin.buffer.fileno()

    with _cbreak(sys.stdin):
        loop.add_reader(stdin_fd, on_input)

        while True:
            ucs = yield from reader.read(2**12)
            if not ucs:
                loop.remove_reader(stdin_fd)
                break
            print(ucs, end='', flush=True)


@contextlib.contextmanager
def _cbreak(fobj):
    """Return context manager for calling tty.setcbreak on ``fobj``."""
    import fcntl
    import tty
    import termios
    import os
    if not os.isatty(fobj.fileno()):
        yield
        return

    mode = termios.tcgetattr(fobj.fileno())
    tty.setcbreak(fobj.fileno())
    fl = fcntl.fcntl(fobj.fileno(), fcntl.F_GETFL)
    fcntl.fcntl(fobj.fileno(), fcntl.F_SETFL, fl | os.O_NONBLOCK)
    try:
        yield
    finally:
        termios.tcsetattr(fobj.fileno(), termios.TCSAFLUSH, mode)
        fcntl.fcntl(fobj.fileno(), fcntl.F_SETFL, fl)
