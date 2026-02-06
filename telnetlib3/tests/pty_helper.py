#!/usr/bin/env python
"""
Simple PTY test programs for telnetlib3 tests.

These programs are designed to be run via PTY for testing purposes.
Usage: python -m telnetlib3.tests.pty_helper <mode> [args...]

Modes:
    cat         - Echo stdin to stdout (like /bin/cat)
    echo        - Print arguments and exit
    stty_size   - Print terminal size as "rows cols"
    exit_code   - Exit with given code (default 0)
    env         - Print specified environment variable
    sleep       - Sleep for N seconds (default 60)
    env_all     - Print all environment variables
    sync_output - Output with BSU/ESU synchronized update sequences
    partial_utf8 - Output incomplete UTF-8 then complete it
"""

# std imports
import os
import sys
import time


def cat_mode():
    """Echo stdin to stdout until EOF."""
    try:
        while True:
            data = sys.stdin.read(1)
            if not data:
                break
            sys.stdout.write(data)
            sys.stdout.flush()
    except (EOFError, KeyboardInterrupt):
        pass


def echo_mode(args):
    """Print arguments to stdout."""
    print(" ".join(args))
    sys.stdout.flush()


def stty_size_mode():
    """Print terminal size."""
    # imported locally to avoid error on import with windows systems
    # std imports
    import fcntl
    import struct
    import termios

    try:
        winsize = fcntl.ioctl(sys.stdin.fileno(), termios.TIOCGWINSZ, b"\x00" * 8)
        rows, cols = struct.unpack("HHHH", winsize)[:2]
        print(f"{rows} {cols}")
    except OSError:
        print("unknown")
    sys.stdout.flush()


def exit_code_mode(args):
    """Exit with specified code."""
    code = int(args[0]) if args else 0
    print("done")
    sys.stdout.flush()
    sys.exit(code)


def env_mode(args):
    """Print environment variable."""
    var_name = args[0] if args else "TERM"
    value = os.environ.get(var_name, "")
    print(value)
    sys.stdout.flush()


def sleep_mode(args):
    """Sleep for specified seconds."""
    seconds = float(args[0]) if args else 60
    time.sleep(seconds)


def env_all_mode():
    """Print all environment variables."""
    for key in sorted(os.environ.keys()):
        print(f"{key}={os.environ[key]}")
    sys.stdout.flush()


def sync_output_mode():
    """Output with BSU/ESU synchronized update sequences."""
    bsu = b"\x1b[?2026h"
    esu = b"\x1b[?2026l"
    sys.stdout.buffer.write(b"before\n")
    sys.stdout.buffer.write(bsu + b"synchronized content" + esu)
    sys.stdout.buffer.write(b"\nafter\n")
    sys.stdout.buffer.flush()


def partial_utf8_mode():
    """Output incomplete UTF-8 then complete it."""
    sys.stdout.buffer.write(b"hello\xc3")
    sys.stdout.buffer.flush()
    time.sleep(0.1)
    sys.stdout.buffer.write(b"\xa9world\n")
    sys.stdout.buffer.flush()


def main():
    """Entry point for PTY test helper."""
    if len(sys.argv) < 2:
        print("Usage: python -m telnetlib3.tests.pty_helper <mode> [args...]", file=sys.stderr)
        sys.exit(1)

    mode = sys.argv[1]
    args = sys.argv[2:]

    modes = {
        "cat": cat_mode,
        "echo": lambda: echo_mode(args),
        "stty_size": stty_size_mode,
        "exit_code": lambda: exit_code_mode(args),
        "env": lambda: env_mode(args),
        "sleep": lambda: sleep_mode(args),
        "env_all": env_all_mode,
        "sync_output": sync_output_mode,
        "partial_utf8": partial_utf8_mode,
    }

    if mode not in modes:
        print(f"Unknown mode: {mode}", file=sys.stderr)
        print(f"Available modes: {', '.join(modes.keys())}", file=sys.stderr)
        sys.exit(1)

    modes[mode]()


if __name__ == "__main__":  # pragma: no cover
    main()
