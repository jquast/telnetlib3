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
    :param input_filter: Optional :class:`~telnetlib3.client_shell.InputFilter` for
        translating raw keyboard bytes (e.g. arrow keys for ATASCII/PETSCII).
    :param autoreply_engine: Optional autoreply engine (e.g. a MUD macro engine)
        that receives server output via ``engine.feed(text)`` and can send replies.
    :param autoreply_wait_fn: Async callable installed by the shell to gate autoreply
        sends on GA/EOR prompt signals; set automatically during shell startup.
    :param typescript_file: When set, all server output is appended to this file
        (like the POSIX ``typescript`` command).
    :param gmcp_data: Initial GMCP module data mapping; defaults to an empty dict.
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
