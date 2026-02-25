"""Per-connection session state for MUD client sessions."""

from __future__ import annotations

# std imports
import asyncio
from typing import Any, Union, Callable, Optional, Awaitable

# local
from .stream_writer import TelnetWriter, TelnetWriterUnicode


class _CommandQueue:
    """Mutable state for a running command queue, enabling display and cancellation."""

    __slots__ = ("commands", "current_idx", "cancelled", "cancel_event", "render")

    def __init__(self, commands: list[str], render: Callable[[], None]) -> None:
        self.commands = commands
        self.current_idx = 0
        self.cancelled = False
        self.cancel_event = asyncio.Event()
        self.render = render


class SessionContext:
    """
    Per-connection runtime state for a MUD client session.

    Replaces the dynamic ``_foo`` attributes formerly set via
    :func:`setattr` on :class:`~telnetlib3.stream_writer.TelnetWriter`.
    Created in ``_session_shell`` and attached as ``writer._ctx``.

    :param session_key: Session identifier (``"host:port"``).
    """

    def __init__(self, session_key: str = "") -> None:
        """Initialize session context with default state."""
        # back-reference to the writer (set by _session_shell)
        self.writer: Optional[Union[TelnetWriter, TelnetWriterUnicode]] = None

        # identity
        self.session_key: str = session_key

        # room / navigation
        self.room_graph: Any = None
        self.rooms_file: str = ""
        self.current_room_file: str = ""
        self.current_room_num: str = ""
        self.previous_room_num: str = ""
        self.macro_start_room: str = ""
        self.room_changed: asyncio.Event = asyncio.Event()
        self.room_arrival_timeout: float = 5.0

        # walk automation
        self.wander_active: bool = False
        self.wander_current: int = 0
        self.wander_total: int = 0
        self.wander_task: Optional[asyncio.Task[None]] = None
        self.discover_active: bool = False
        self.discover_current: int = 0
        self.discover_total: int = 0
        self.discover_task: Optional[asyncio.Task[None]] = None
        self.randomwalk_active: bool = False
        self.randomwalk_current: int = 0
        self.randomwalk_total: int = 0
        self.randomwalk_task: Optional[asyncio.Task[None]] = None
        self.active_command: Optional[str] = None

        # command queue
        self.command_queue: Optional[_CommandQueue] = None

        # macros & autoreplies
        self.macro_defs: list[Any] = []
        self.macros_file: str = ""
        self.autoreply_rules: list[Any] = []
        self.autoreplies_file: str = ""
        self.autoreply_engine: Optional[Any] = None

        # prompt / GA pacing
        self.wait_for_prompt: Optional[Callable[..., Awaitable[None]]] = None
        self.echo_command: Optional[Callable[[str], None]] = None
        self.prompt_ready: Optional[asyncio.Event] = None

        # GMCP
        self.gmcp_data: dict[str, Any] = {}
        self.on_gmcp_ready: Optional[Callable[[], None]] = None

        # rendering / input config
        self.color_filter: Optional[Any] = None
        self.raw_mode: Optional[bool] = None
        self.ascii_eol: bool = False
        self.input_filter: Optional[Any] = None
        self.repl_enabled: bool = False
        self.history_file: Optional[str] = None

        # modem activity dots (set by REPL, used by _send_chained et al.)
        self.rx_dot: Optional[Any] = None
        self.tx_dot: Optional[Any] = None
        self.cx_dot: Optional[Any] = None

        # REPL internals
        self.key_dispatch: Optional[Any] = None
        self.cursor_style: str = ""
        self.send_line: Optional[Callable[[str], None]] = None
        self.autoreply_wait_fn: Optional[Callable[..., Awaitable[None]]] = None
        self.send_naws: Optional[Callable[[], None]] = None
