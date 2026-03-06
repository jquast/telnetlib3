"""Base session context for telnet client connections."""

from __future__ import annotations

# std imports
from typing import IO, Any, Callable, Optional, Awaitable

__all__ = ("TelnetSessionContext",)


class TelnetSessionContext:
    r"""
    Base session context for telnet client connections.

    Holds per-connection state that the shell layer needs.  Subclass this to
    add application-specific attributes (e.g. MUD client state, macros, room
    graphs).

    A default instance is created for every :class:`~telnetlib3.stream_writer.TelnetWriter`;
    applications may replace it with a subclass via ``writer.ctx = MyCtx()``.

    :param raw_mode: Terminal raw mode override.  ``None`` = auto-detect
        from server negotiation, ``True`` = force raw, ``False`` = force
        line mode.
    :param ascii_eol: When ``True``, translate ATASCII CR/LF glyphs to
        ASCII ``\r`` / ``\n``.
    """

    def __init__(
        self,
        raw_mode: Optional[bool] = None,
        ascii_eol: bool = False,
        input_filter: Optional[Any] = None,
        autoreply_engine: Optional[Any] = None,
        autoreply_wait_fn: Optional[Callable[..., Awaitable[None]]] = None,
        typescript_file: Optional[IO[str]] = None,
        gmcp_data: Optional[dict[str, Any]] = None,
    ) -> None:
        """Initialize session context with default attribute values."""
        self.raw_mode = raw_mode
        self.ascii_eol = ascii_eol
        self.input_filter = input_filter
        self.autoreply_engine = autoreply_engine
        self.autoreply_wait_fn = autoreply_wait_fn
        self.typescript_file = typescript_file
        self.gmcp_data: dict[str, Any] = gmcp_data if gmcp_data is not None else {}
