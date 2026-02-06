"""
Guard shells for connection limiting and robot detection.

When running a telnet server on a public IPv4 address, or even on large private networks,
various network scanners, scrapers, worms, bots, and other automatons will connect.

The ``robot_check`` function can reliably detect whether the remote end is a real terminal
emulator by measuring the rendered width of a wide Unicode character. Real terminals
render it as width 2, while bots typically see width 1 or timeout.

These shells are used when normal shell access is denied due to connection limits or
failed robot checks.
"""

from __future__ import annotations

# std imports
import re
import asyncio
import logging
from typing import Optional, Tuple, Union, cast

# local
from .server_shell import readline2
from .stream_reader import TelnetReader, TelnetReaderUnicode
from .stream_writer import TelnetWriter, TelnetWriterUnicode

__all__ = ("robot_check", "robot_shell", "busy_shell", "ConnectionCounter")

logger = logging.getLogger("telnetlib3.guard")

# Wide character test - U+231A WATCH should render as width 2
_WIDE_TEST_CHAR = "\u231a"

# Input limit for guard shells
_MAX_INPUT = 2048

# CPR response pattern: ESC [ row ; col R
_CPR_PATTERN = re.compile(rb"\x1b\[(\d+);(\d+)R")


class ConnectionCounter:
    """Simple shared counter for limiting concurrent connections."""

    def __init__(self, limit: int) -> None:
        """
        Initialize connection counter.

        :param limit: Maximum number of concurrent connections.
        """
        self.limit = limit
        self._count = 0

    def try_acquire(self) -> bool:
        """
        Try to acquire a connection slot.

        Returns True if successful.
        """
        if self._count < self.limit:
            self._count += 1
            return True
        return False

    def release(self) -> None:
        """Release a connection slot."""
        if self._count > 0:
            self._count -= 1

    @property
    def count(self) -> int:
        """Current connection count."""
        return self._count


async def _read_line_inner(
    reader: Union[TelnetReader, TelnetReaderUnicode],
    max_len: int,
) -> str:
    """Inner loop for _read_line, separated for wait_for compatibility."""
    _reader = cast(TelnetReaderUnicode, reader)
    buf = ""
    while len(buf) < max_len:
        char = await _reader.read(1)
        if not char:
            break
        if char in ("\r", "\n"):
            break
        buf += char
    return buf


async def _read_line(
    reader: Union[TelnetReader, TelnetReaderUnicode],
    timeout: float,
    max_len: int = _MAX_INPUT,
) -> Optional[str]:
    """Read a line with timeout and length limit."""
    try:
        return await asyncio.wait_for(_read_line_inner(reader, max_len), timeout)
    except asyncio.TimeoutError:
        return None


async def _readline_with_echo(
    reader: Union[TelnetReader, TelnetReaderUnicode],
    writer: Union[TelnetWriter, TelnetWriterUnicode],
    timeout: float,
) -> Optional[str]:
    """Read a line with echo and timeout, using readline2 from server_shell."""
    try:
        return await asyncio.wait_for(readline2(reader, writer), timeout)
    except asyncio.TimeoutError:
        return None


async def _read_cpr_response(
    reader: Union[TelnetReader, TelnetReaderUnicode],
) -> Optional[Tuple[int, int]]:
    """Read CPR response bytes until 'R' terminator."""
    buf = b""
    while True:
        try:
            data = await reader.read(1)
        except UnicodeDecodeError:
            # Bot sent garbage bytes that can't be decoded
            return None
        if not data:
            return None
        if isinstance(data, str):
            data = data.encode("latin-1")
        buf += data
        if buf.endswith(b"R"):
            match = _CPR_PATTERN.search(buf)
            if match:
                return (int(match.group(1)), int(match.group(2)))


async def _get_cursor_position(
    reader: Union[TelnetReader, TelnetReaderUnicode],
    writer: Union[TelnetWriter, TelnetWriterUnicode],
    timeout: float = 2.0,
) -> Tuple[Optional[int], Optional[int]]:
    """
    Query cursor position using DSR/CPR.

    :returns: (row, col) tuple or (None, None) on timeout/failure.
    """
    # Send Device Status Report request
    _writer = cast(TelnetWriterUnicode, writer)
    _writer.write("\x1b[6n")
    await writer.drain()

    # Read response: ESC [ row ; col R
    try:
        result = await asyncio.wait_for(_read_cpr_response(reader), timeout)
        return result if result else (None, None)
    except asyncio.TimeoutError:
        return (None, None)


async def _measure_width(
    reader: Union[TelnetReader, TelnetReaderUnicode],
    writer: Union[TelnetWriter, TelnetWriterUnicode],
    text: str,
    timeout: float = 2.0,
) -> Optional[int]:
    """
    Measure rendered width of text using cursor position.

    :returns: Width in columns, or None on failure.
    """
    _writer = cast(TelnetWriterUnicode, writer)
    _, x1 = await _get_cursor_position(reader, writer, timeout)
    if x1 is None:
        return None

    _writer.write(text)
    await _writer.drain()

    _, x2 = await _get_cursor_position(reader, writer, timeout)
    if x2 is None:
        return None

    # Clear the test character
    _writer.write(f"\x1b[{x1}G" + " " * (x2 - x1) + f"\x1b[{x1}G")
    await _writer.drain()

    return x2 - x1


async def robot_check(
    reader: Union[TelnetReader, TelnetReaderUnicode],
    writer: Union[TelnetWriter, TelnetWriterUnicode],
    timeout: float = 5.0,
) -> bool:
    """
    Check if client can render wide characters.

    :returns: True if client passes (renders wide char as width 2).
    """
    width = await _measure_width(reader, writer, _WIDE_TEST_CHAR, timeout)
    return bool(width == 2)


async def _ask_question(
    reader: Union[TelnetReader, TelnetReaderUnicode],
    writer: Union[TelnetWriter, TelnetWriterUnicode],
    prompt: str,
    timeout: float = 10.0,
) -> Optional[str]:
    """Ask a question, echoing input and repeating prompt on blank input."""
    _writer = cast(TelnetWriterUnicode, writer)
    while True:
        _writer.write(prompt)
        await _writer.drain()

        line = await _readline_with_echo(reader, writer, timeout)
        if line is None:
            return None

        if line.strip():
            return line
        # Blank input - repeat prompt
        _writer.write("\r\n")


async def robot_shell(
    reader: Union[TelnetReader, TelnetReaderUnicode],
    writer: Union[TelnetWriter, TelnetWriterUnicode],
) -> None:
    """
    Shell for failed robot checks.

    Asks philosophical questions, logs responses, and disconnects.
    """
    writer = cast(TelnetWriterUnicode, writer)
    peername = writer.get_extra_info("peername")
    logger.info("robot_shell: connection from %s", peername)

    answers = []
    try:
        line1 = await _ask_question(reader, writer, "Do robots dream of electric sheep? [yn] ")
        if line1 is None:
            logger.info("robot_shell: timeout waiting for response")
            return
        answers.append(line1)

        line2 = await _ask_question(
            reader, writer, "\r\nHave you ever wondered, who are the windowmakers? "
        )
        if line2 is None:
            logger.info("robot_shell: timeout on second question")
            return
        answers.append(line2)

        writer.write("\r\n")
        await writer.drain()
    finally:
        if answers:
            logger.info("robot denied, answers=%r", answers)


async def busy_shell(
    reader: Union[TelnetReader, TelnetReaderUnicode],
    writer: Union[TelnetWriter, TelnetWriterUnicode],
) -> None:
    """
    Shell for when connection limit is reached.

    Displays busy message, logs any input, and disconnects.
    """
    writer = cast(TelnetWriterUnicode, writer)
    logger.info(
        "busy_shell: connection from %s (limit reached)",
        writer.get_extra_info("peername"),
    )

    writer.write("Machine is busy, do not touch! ")
    await writer.drain()

    line1 = await _read_line(reader, timeout=30.0)
    if line1 is not None:
        logger.info("busy_shell: input1=%r", line1)

    writer.write("\r\nYou hear a distant explosion... ")
    await writer.drain()

    line2 = await _read_line(reader, timeout=30.0)
    if line2 is not None:
        logger.info("busy_shell: input2=%r", line2)

    writer.write("\r\n")
    await writer.drain()
