"""Guard shells for connection limiting and robot detection.

These shells are used when normal shell access is denied due to connection
limits or failed robot checks.
"""

import asyncio
import logging
import re

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

    def __init__(self, limit):
        """
        Initialize connection counter.

        :param limit: Maximum number of concurrent connections.
        """
        self.limit = limit
        self._count = 0

    def try_acquire(self):
        """Try to acquire a connection slot. Returns True if successful."""
        if self._count < self.limit:
            self._count += 1
            return True
        return False

    def release(self):
        """Release a connection slot."""
        if self._count > 0:
            self._count -= 1

    @property
    def count(self):
        """Current connection count."""
        return self._count


async def _read_line_inner(reader, max_len):
    """Inner loop for _read_line, separated for wait_for compatibility."""
    buf = ""
    while len(buf) < max_len:
        char = await reader.read(1)
        if not char:
            break
        if char in ("\r", "\n"):
            break
        buf += char
    return buf


async def _read_line(reader, timeout, max_len=_MAX_INPUT):
    """Read a line with timeout and length limit."""
    try:
        return await asyncio.wait_for(_read_line_inner(reader, max_len), timeout)
    except asyncio.TimeoutError:
        return None


async def _read_cpr_response(reader):
    """Read CPR response bytes until 'R' terminator."""
    buf = b""
    while True:
        data = await reader.read(1)
        if not data:
            return None
        if isinstance(data, str):
            data = data.encode("latin-1")
        buf += data
        if buf.endswith(b"R"):
            match = _CPR_PATTERN.search(buf)
            if match:
                return (int(match.group(1)), int(match.group(2)))


async def _get_cursor_position(reader, writer, timeout=2.0):
    """
    Query cursor position using DSR/CPR.

    :returns: (row, col) tuple or (None, None) on timeout/failure.
    """
    # Send Device Status Report request
    writer.write("\x1b[6n")
    await writer.drain()

    # Read response: ESC [ row ; col R
    try:
        result = await asyncio.wait_for(_read_cpr_response(reader), timeout)
        return result if result else (None, None)
    except asyncio.TimeoutError:
        return (None, None)


async def _measure_width(reader, writer, text, timeout=2.0):
    """
    Measure rendered width of text using cursor position.

    :returns: Width in columns, or None on failure.
    """
    _, x1 = await _get_cursor_position(reader, writer, timeout)
    if x1 is None:
        return None

    writer.write(text)
    await writer.drain()

    _, x2 = await _get_cursor_position(reader, writer, timeout)
    if x2 is None:
        return None

    # Clear the test character
    writer.write(f"\x1b[{x1}G" + " " * (x2 - x1) + f"\x1b[{x1}G")
    await writer.drain()

    return x2 - x1


async def robot_check(reader, writer, timeout=5.0):
    """
    Check if client can render wide characters.

    :returns: True if client passes (renders wide char as width 2).
    """
    width = await _measure_width(reader, writer, _WIDE_TEST_CHAR, timeout)
    return width == 2


async def robot_shell(reader, writer):
    """
    Shell for failed robot checks.

    Asks philosophical questions, logs responses, and disconnects.
    """
    logger.info("robot_shell: connection from %s", writer.get_extra_info("peername"))

    writer.write("Do robots dream of electric sheep? [yn] ")
    await writer.drain()

    line1 = await _read_line(reader, timeout=10.0)
    if line1 is None:
        logger.info("robot_shell: timeout waiting for response")
        return

    logger.info("robot denied, line1=%r", line1)

    writer.write("\r\nHave you ever wondered, who are the windowmakers? ")
    await writer.drain()

    line2 = await _read_line(reader, timeout=10.0)
    if line2 is None:
        logger.info("robot_shell: timeout on second question")
        return

    logger.info("robot denied, line2=%r", line2)

    writer.write("\r\n")
    await writer.drain()


async def busy_shell(reader, writer):
    """
    Shell for when connection limit is reached.

    Displays busy message, logs any input, and disconnects.
    """
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
