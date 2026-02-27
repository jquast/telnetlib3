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
        self.discover_active: bool = False
        self.discover_current: int = 0
        self.discover_total: int = 0
        self.discover_task: Optional[asyncio.Task[None]] = None
        self.randomwalk_active: bool = False
        self.randomwalk_current: int = 0
        self.randomwalk_total: int = 0
        self.randomwalk_task: Optional[asyncio.Task[None]] = None
        self.active_command: Optional[str] = None
        self.active_command_time: float = 0.0
        self.blocked_exits: set[tuple[str, str]] = set()  # (room_num, direction)

        # walk resume state
        self.last_walk_mode: str = ""
        self.last_walk_room: str = ""
        self.last_walk_visited: set[str] = set()
        self.last_walk_tried: set[tuple[str, str]] = set()

        # command queue
        self.command_queue: Optional[_CommandQueue] = None

        # macros & autoreplies
        self.macro_defs: list[Any] = []
        self.macros_file: str = ""
        self.autoreply_rules: list[Any] = []
        self.autoreplies_file: str = ""
        self.autoreply_engine: Optional[Any] = None

        # highlighters
        self.highlight_rules: list[Any] = []
        self.highlights_file: str = ""
        self.highlight_engine: Optional[Any] = None

        # prompt / GA pacing
        self.wait_for_prompt: Optional[Callable[..., Awaitable[None]]] = None
        self.echo_command: Optional[Callable[[str], None]] = None
        self.prompt_ready: Optional[asyncio.Event] = None

        # GMCP
        self.gmcp_data: dict[str, Any] = {}
        self.on_gmcp_ready: Optional[Callable[[], None]] = None

        # chat (GMCP Comm.Channel)
        self.chat_messages: list[dict[str, Any]] = []
        self.chat_unread: int = 0
        self.chat_channels: list[dict[str, Any]] = []
        self.chat_file: str = ""

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

        # debounced timestamp persistence
        self._macros_dirty: bool = False
        self._autoreplies_dirty: bool = False
        self._save_timer: Optional[asyncio.TimerHandle] = None

    def mark_macros_dirty(self) -> None:
        """Mark macros as needing a save and schedule a debounced flush."""
        self._macros_dirty = True
        self._schedule_flush()

    def mark_autoreplies_dirty(self) -> None:
        """Mark autoreplies as needing a save and schedule a debounced flush."""
        self._autoreplies_dirty = True
        self._schedule_flush()

    def _schedule_flush(self) -> None:
        """Schedule :meth:`flush_timestamps` after 30 seconds if not already pending."""
        if self._save_timer is not None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._save_timer = loop.call_later(30, self._flush_timestamps_sync)

    def _flush_timestamps_sync(self) -> None:
        """Synchronous wrapper called by the event loop timer."""
        self._save_timer = None
        self.flush_timestamps()

    def flush_timestamps(self) -> None:
        """Persist macro/autoreply timestamps if dirty."""
        if self._macros_dirty and self.macros_file and self.macro_defs:
            from .macros import save_macros

            save_macros(self.macros_file, self.macro_defs, self.session_key)
            self._macros_dirty = False
        if self._autoreplies_dirty and self.autoreplies_file and self.autoreply_rules:
            from .autoreply import save_autoreplies

            save_autoreplies(
                self.autoreplies_file, self.autoreply_rules, self.session_key
            )
            self._autoreplies_dirty = False
