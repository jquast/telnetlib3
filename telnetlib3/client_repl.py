"""REPL and TUI components for linemode telnet client sessions."""

# pylint: disable=too-complex

# std imports
import os
import sys
import time
import asyncio
import logging
import collections
from typing import Any, List, Tuple, Union, Callable, Optional

# local
from ._ansi import (
    HOME,
    SGR_CYAN,
    SGR_RESET,
    CLEAR_HOME,
    CLEAR_LINE,
    SGR_YELLOW,
    SAVE_CURSOR,
    SCROLL_RESET,
    CLEAR_SCREEN,
    CURSOR_DEFAULT,
    RESTORE_CURSOR,
    EXIT_ALT_SCREEN,
    ENTER_ALT_SCREEN,
    TERMINAL_CLEANUP,
    CURSOR_BLINKING_BAR,
    cup,
    decstbm,
)
from .stream_reader import TelnetReader, TelnetReaderUnicode
from .stream_writer import TelnetWriter, TelnetWriterUnicode

try:
    import prompt_toolkit
    import prompt_toolkit.styles
    import prompt_toolkit.filters
    import prompt_toolkit.history
    import prompt_toolkit.application
    import prompt_toolkit.key_binding
    import prompt_toolkit.auto_suggest

    HAS_PROMPT_TOOLKIT = True
except ImportError:
    HAS_PROMPT_TOOLKIT = False

PASSWORD_CHAR = "\u25cf"

# Number of bottom rows reserved for prompt_toolkit (input + toolbar).
# prompt_toolkit allocates a toolbar row in its layout even when the
# bottom_toolbar callback returns None, so we must reserve 2 from the start.
_PT_RESERVE_INITIAL = 2
_PT_RESERVE_WITH_TOOLBAR = 2

# Maximum bytes retained in the output replay ring buffer for Ctrl-L repaint.
_REPLAY_BUFFER_MAX = 65536

# Buffer for MUD data received while a TUI editor subprocess is running.
# The asyncio _read_server loop continues receiving MUD data during editor
# sessions; writing that data to the terminal fills the PTY buffer and
# deadlocks the editor's Textual WriterThread.  Data is queued here and
# replayed when the editor exits.
_editor_active = False  # pylint: disable=invalid-name
_editor_buffer: list[bytes] = []

__all__ = (
    "HAS_PROMPT_TOOLKIT",
    "PromptToolkitRepl",
    "BasicLineRepl",
    "ScrollRegion",
    "repl_event_loop",
    "_split_incomplete_esc",
)


def _split_incomplete_esc(data: bytes) -> tuple[bytes, bytes]:
    """
    Split *data* into (complete, holdback) at a trailing incomplete escape.

    If *data* ends mid-escape-sequence the incomplete tail is returned as
    *holdback* so the caller can buffer it until more bytes arrive.
    Handles CSI (``ESC [``) with arbitrarily long parameter/intermediate
    bytes, OSC (``ESC ]``), DCS (``ESC P``), and plain two-byte ``ESC X``
    sequences.

    :returns: ``(flush_now, hold_back)`` -- concatenation equals *data*.
    """
    n = len(data)
    if n == 0:
        return data, b""

    idx = data.rfind(0x1B)
    if idx == -1:
        return data, b""

    pos = idx + 1

    if pos >= n:
        # Lone ESC at the very end.
        return data[:idx], data[idx:]

    nxt = data[pos]

    if nxt == 0x5B:  # '[' -- CSI
        pos += 1
        # Parameter bytes 0x30-0x3F, intermediate bytes 0x20-0x2F.
        while pos < n and 0x20 <= data[pos] <= 0x3F:
            pos += 1
        # Final byte 0x40-0x7E completes the sequence.
        if pos < n and 0x40 <= data[pos] <= 0x7E:
            return data, b""
        return data[:idx], data[idx:]

    if nxt in (0x5D, 0x50):  # ']' OSC  /  'P' DCS
        # Terminated by BEL (0x07) or ST (ESC \).
        while pos < n:
            if data[pos] == 0x07:
                return data, b""
            if data[pos] == 0x1B and pos + 1 < n and data[pos + 1] == 0x5C:
                return data, b""
            pos += 1
        return data[:idx], data[idx:]

    if 0x40 <= nxt <= 0x5F:
        # Two-byte escape -- already complete (Fe sequence).
        return data, b""

    # Unknown sequence type; assume complete.
    return data, b""


class OutputRingBuffer:
    """Rolling buffer of raw display output for Ctrl-L screen repaint."""

    def __init__(self, max_bytes: int = _REPLAY_BUFFER_MAX) -> None:
        self._chunks: collections.deque[bytes] = collections.deque()
        self._total = 0
        self._max = max_bytes

    def append(self, data: bytes) -> None:
        """Append a chunk, discarding oldest data when over capacity."""
        self._chunks.append(data)
        self._total += len(data)
        while self._total > self._max and self._chunks:
            removed = self._chunks.popleft()
            self._total -= len(removed)

    def replay(self) -> bytes:
        """Return all buffered output concatenated."""
        return b"".join(self._chunks)


def _restore_after_subprocess(
    replay_buf: Optional["OutputRingBuffer"], reserve: int = _PT_RESERVE_WITH_TOOLBAR
) -> None:
    """
    Restore terminal state after a TUI subprocess exits.

    Restores stdin blocking mode, resets SGR/mouse/alt-screen via
    :data:`TERMINAL_CLEANUP`, clears the screen, re-establishes the
    DECSTBM scroll region, replays the output ring buffer, and clears
    the reserved input rows.

    :param replay_buf: Ring buffer to replay, or ``None`` to skip replay.
    :param reserve: Number of bottom rows reserved for the input area.
    """
    try:
        os.set_blocking(sys.stdin.fileno(), True)
    except OSError:
        pass
    sys.stdout.write(TERMINAL_CLEANUP)
    try:
        _tsize = os.get_terminal_size()
    except OSError:
        _tsize = os.terminal_size((80, 24))
    scroll_bottom = max(1, _tsize.lines - reserve - 1)
    sys.stdout.write(CLEAR_HOME)
    sys.stdout.write(decstbm(1, scroll_bottom))
    sys.stdout.write(cup(1, 1))
    if replay_buf is not None:
        data = replay_buf.replay()
        if data:
            sys.stdout.write(data.decode("utf-8", errors="replace"))
    sys.stdout.write(SAVE_CURSOR)
    dmz = scroll_bottom + 1
    _input_row = _tsize.lines - reserve + 1
    if dmz < _input_row:
        sys.stdout.write(cup(dmz, 1) + CLEAR_LINE + "\u2500" * _tsize.columns)
    for _r in range(_input_row, _tsize.lines + 1):
        sys.stdout.write(cup(_r, 1) + CLEAR_LINE)
    sys.stdout.flush()


def _hsv_to_rgb(h: float, s: float, v: float) -> Tuple[int, int, int]:
    """Convert HSV (h in [0,360), s/v in [0,1]) to (r, g, b) in [0,255]."""
    import colorsys  # pylint: disable=import-outside-toplevel

    r, g, b = colorsys.hsv_to_rgb(h / 360.0, s, v)
    return (int(r * 255), int(g * 255), int(b * 255))


def _fmt_value(n: int) -> str:
    """
    Format a numeric value with k/m suffixes for compact display.

    :param n: Integer value.
    :returns: Formatted string, e.g. ``1.2k``, ``3.5m``.
    """
    if n >= 1_000_000:
        v = n / 1_000_000
        return f"{v:.1f}m"
    if n >= 1_000:
        v = n / 1_000
        return f"{v:.1f}k"
    return str(n)


def _vital_color(fraction: float, kind: str) -> str:
    """
    Return an RGB hex color for a vitals bar.

    :param fraction: 0.0 (empty) to 1.0 (full).
    :param kind: ``"hp"`` for red-to-green, ``"mp"`` for golden-yellow-to-blue,
        ``"xp"`` for purple-to-violet.
    """
    fraction = max(0.0, min(1.0, fraction))
    if kind == "hp":
        # Stay red below 33%, then red->green over 33%-100%.
        hue = max(0.0, (fraction - 0.33) / 0.67) * 120.0
    elif kind == "xp":
        # Purple (270) -> cyan (180) as XP fills.
        hue = 270.0 - fraction * 90.0
    elif kind == "wander":
        # Cyan (180) -> yellow (60) as autowander progresses.
        hue = 180.0 - fraction * 120.0
    elif kind == "discover":
        # Green (120) -> magenta (300) as autodiscover progresses.
        hue = 120.0 + fraction * 180.0
    else:
        # Stay golden yellow below 33%, then golden-yellow->blue over 33%-100%.
        # hue 45=golden yellow, hue 240=blue.
        t = max(0.0, (fraction - 0.33) / 0.67)
        hue = 45.0 + t * 195.0
    r, g, b = _hsv_to_rgb(hue, 0.7, 0.8)
    return f"#{r:02x}{g:02x}{b:02x}"


def _wcswidth(text: str) -> int:
    """Return display width of *text*, handling wide chars."""
    from wcwidth import wcswidth  # pylint: disable=import-outside-toplevel

    w = wcswidth(text)
    return w if w >= 0 else len(text)


# Width of the inner progress bar (between the brackets).
_BAR_WIDTH = 16


def _vital_bar(
    current: Any, maximum: Any, width: int, kind: str, flash: bool = False
) -> "List[Tuple[str, str]]":
    """
    Build a labelled progress-bar with overlaid text.

    Returns ``HP:`` or ``MP:`` prefix followed by a colored bar.
    The label (e.g. ``60/65 92%``) is rendered *on top of* the bar.
    The filled portion uses dark text on the vitals color, the empty
    portion uses dim text on a dark background.

    :param flash: When ``True``, render the bar in white for attention.
    """
    try:
        cur = int(current)
    except (TypeError, ValueError):
        cur = 0
    if maximum is not None:
        try:
            mx = int(maximum)
        except (TypeError, ValueError):
            mx = 0
    else:
        mx = 0

    if mx > 0:
        frac = max(0.0, min(1.0, cur / mx))
    else:
        frac = 1.0

    filled = int(round(frac * width))
    pct = int(round(frac * 100))

    bar_color = _vital_color(frac, kind)
    if flash:
        filled_style = "fg:#101010 bg:#ffffff"
        empty_style = "fg:#aaaaaa bg:#888888"
    else:
        filled_style = f"fg:#101010 bg:{bar_color}"
        empty_style = "fg:#666666 bg:#2a2a2a"

    if mx > 0:
        label = f"{_fmt_value(cur)}/{_fmt_value(mx)} {pct}%"
    else:
        label = _fmt_value(cur)

    # Center the label inside the bar width.
    # Both filled and empty portions use spaces as background.
    lpad = max(0, (width - len(label)) // 2)

    bg = list(" " * filled + " " * (width - filled))
    for i, ch in enumerate(label, start=lpad):
        if i < width:
            bg[i] = ch
    bar_text = "".join(bg[:width])

    filled_text = bar_text[:filled]
    empty_text = bar_text[filled:]

    prefix = {"hp": "HP", "mp": "MP", "xp": "XP", "wander": "AW"}.get(kind, kind.upper())
    return [
        ("fg:#dddddd", prefix),
        ("fg:#dddddd", ":"),
        (filled_style, filled_text),
        (empty_style, empty_text),
    ]


def _center_truncate(text: str, avail: int) -> str:
    """Truncate *text* to fit *avail* display columns."""
    if avail <= 0:
        return ""
    w = _wcswidth(text)
    if w <= avail:
        return text
    # Truncate character by character.
    result = []
    total = 0
    for ch in text:
        cw = _wcswidth(ch)
        if total + cw + 1 > avail:
            break
        result.append(ch)
        total += cw
    return "".join(result) + "\u2026"


if HAS_PROMPT_TOOLKIT:

    class _FilteredFileHistory(prompt_toolkit.history.FileHistory):
        """
        FileHistory subclass that skips storing password inputs.

        :attr:`is_password` is a callable checked at store time -- when
        it returns ``True`` the entry is silently discarded.
        """

        def __init__(
            self, filename: str, is_password: "Optional[Callable[[], bool]]" = None
        ) -> None:
            self._is_password = is_password
            super().__init__(filename)

        def store_string(self, string: str) -> None:
            """Skip storing the string when in password mode."""
            if self._is_password is not None and self._is_password():
                return
            super().store_string(string)

    def _make_history(
        history_file: Optional[str], is_password: "Optional[Callable[[], bool]]" = None
    ) -> "Union[_FilteredFileHistory, prompt_toolkit.history.InMemoryHistory]":
        """Create a history instance, ensuring parent directories exist."""
        if history_file:
            import pathlib  # pylint: disable=import-outside-toplevel

            pathlib.Path(history_file).parent.mkdir(parents=True, exist_ok=True)
            return _FilteredFileHistory(history_file, is_password=is_password)
        return prompt_toolkit.history.InMemoryHistory()

    class PromptToolkitRepl:
        """
        REPL using prompt_toolkit's PromptSession for linemode input.

        Provides persistent or in-memory history, history-based autosuggestion,
        and password masking when the server negotiates WILL ECHO (password mode).
        Password inputs are never written to the history file.

        :param telnet_writer: Writer for sending input to the server.
        :param log: Logger instance.
        :param history_file: Path to a persistent history file, or ``None``
            to use in-memory history only.
        """

        def __init__(
            self,
            telnet_writer: Union[TelnetWriter, TelnetWriterUnicode],
            log: logging.Logger,
            history_file: Optional[str] = None,
            connection_info: str = "",
        ) -> None:
            """Initialize REPL with writer, logger, and optional history file."""
            self._writer = telnet_writer
            self._log = log
            self._history = _make_history(history_file, is_password=self._is_password_mode)
            self._replay_buf: Optional[OutputRingBuffer] = None
            kb = prompt_toolkit.key_binding.KeyBindings()

            @kb.add("c-]")  # type: ignore[untyped-decorator]
            def _escape_quit(event: Any) -> None:
                """Ctrl+] closes the connection, matching classic telnet."""
                event.app.exit(exception=EOFError)

            @kb.add("c-l")  # type: ignore[untyped-decorator]
            def _clear_repaint(event: Any) -> None:
                """Ctrl+L clears screen and replays recent output."""
                _repaint_screen(event, self._replay_buf, scroll=self._scroll)

            from prompt_toolkit.keys import (  # pylint: disable=import-outside-toplevel
                Keys as _PTKeys,
            )

            @kb.add(_PTKeys.BracketedPaste)  # type: ignore[untyped-decorator]
            def _paste_lines(event: Any) -> None:
                """Split pasted text on newlines, send all but last as commands."""
                data = event.data.replace("\r\n", "\n").replace("\r", "\n")
                lines = data.split("\n")
                buf = event.current_buffer
                for line in lines[:-1]:
                    buf.text = line
                    buf.validate_and_handle()
                if lines[-1]:
                    buf.insert_text(lines[-1])

            self._kb = kb
            telnet_writer._pt_kb = kb
            self._macro_defs = getattr(telnet_writer, "_macro_defs", None)
            if self._macro_defs is not None:
                from .macros import bind_macros  # pylint: disable=import-outside-toplevel

                bind_macros(kb, self._macro_defs, telnet_writer, log)

            @kb.add("f1")  # type: ignore[untyped-decorator]
            def _help_screen(event: Any) -> None:
                """F1 shows keybinding help on the alternate screen."""
                _show_help(event, self._macro_defs)

            @kb.add("f8")  # type: ignore[untyped-decorator]
            def _edit_macros(event: Any) -> None:
                """F8 opens macro editor TUI in subprocess."""
                _launch_tui_editor(event, "macros", telnet_writer, self._replay_buf)

            @kb.add("f7")  # type: ignore[untyped-decorator]
            def _browse_rooms(event: Any) -> None:
                """F7 opens room browser TUI in subprocess."""
                _launch_room_browser(event, telnet_writer, self._replay_buf)

            @kb.add("f9")  # type: ignore[untyped-decorator]
            def _edit_autoreplies(event: Any) -> None:
                """F9 opens autoreply editor TUI in subprocess."""
                _launch_tui_editor(event, "autoreplies", telnet_writer, self._replay_buf)

            @kb.add("f21")  # type: ignore[untyped-decorator]  # Shift+F9
            def _toggle_autoreplies(_event: Any) -> None:
                """Shift+F9 (F21) toggles the autoreply engine on/off."""
                engine = self._autoreply_engine
                if engine is None:
                    return
                engine.enabled = not engine.enabled
                echo_fn = getattr(telnet_writer, "_echo_command", None)
                if echo_fn is not None:
                    state = "ON" if engine.enabled else "OFF"
                    echo_fn(f"AUTOREPLIES {state}")

            @kb.add("f4")  # type: ignore[untyped-decorator]
            def _discover_mode(_event: Any) -> None:
                """F4 toggles autodiscover (explore unvisited exits)."""
                if getattr(telnet_writer, "_discover_active", False):
                    task = getattr(telnet_writer, "_discover_task", None)
                    if task is not None:
                        task.cancel()
                    return

                def _confirm_and_start() -> None:
                    from .rooms import (  # pylint: disable=import-outside-toplevel
                        load_prefs,
                        save_prefs,
                    )

                    skey = getattr(telnet_writer, "_session_key", "")
                    prefs = load_prefs(skey) if skey else {}
                    if not prefs.get("skip_autodiscover_confirm"):
                        ok, dont_ask = _confirm_dialog(
                            "Autodiscover",
                            "Autodiscover explores exits from nearby rooms "
                            "that lead to unvisited places. It will travel "
                            "to each frontier exit, check the room, then "
                            "return before trying the next branch.",
                            warning=(
                                "WARNING: This can lead to dangerous areas, "
                                "death traps, or aggressive monsters! Your "
                                "character may die. Use with caution."
                            ),
                            replay_buf=self._replay_buf,
                        )
                        if not ok:
                            return
                        if dont_ask and skey:
                            prefs["skip_autodiscover_confirm"] = True
                            save_prefs(skey, prefs)

                    async def _go() -> None:
                        t = asyncio.ensure_future(
                            _autodiscover(telnet_writer, log)
                        )
                        telnet_writer._discover_task = t

                    asyncio.get_event_loop().call_soon_threadsafe(
                        asyncio.ensure_future, _go()
                    )

                from prompt_toolkit.application import (  # pylint: disable=import-outside-toplevel
                    get_app,
                    run_in_terminal,
                )

                run_in_terminal(_confirm_and_start, render_cli_done=False)
                app = get_app()
                app.renderer.reset()
                app.invalidate()

            @kb.add("f5")  # type: ignore[untyped-decorator]
            def _wander_mode(_event: Any) -> None:
                """F5 toggles autowander through same-named rooms."""
                if getattr(telnet_writer, "_wander_active", False):
                    task = getattr(telnet_writer, "_wander_task", None)
                    if task is not None:
                        task.cancel()
                    return

                def _confirm_and_start() -> None:
                    from .rooms import (  # pylint: disable=import-outside-toplevel
                        load_prefs,
                        save_prefs,
                    )

                    skey = getattr(telnet_writer, "_session_key", "")
                    prefs = load_prefs(skey) if skey else {}
                    if not prefs.get("skip_autowander_confirm"):
                        ok, dont_ask = _confirm_dialog(
                            "Autowander",
                            "Autowander visits all rooms with the same "
                            "name as the current room using slow travel. "
                            "Autoreplies fire in each room visited. The "
                            "route is optimised to minimise backtracking.",
                            replay_buf=self._replay_buf,
                        )
                        if not ok:
                            return
                        if dont_ask and skey:
                            prefs["skip_autowander_confirm"] = True
                            save_prefs(skey, prefs)

                    async def _go() -> None:
                        task = asyncio.ensure_future(
                            _autowander(telnet_writer, log)
                        )
                        telnet_writer._wander_task = task

                    asyncio.get_event_loop().call_soon_threadsafe(
                        asyncio.ensure_future, _go()
                    )

                from prompt_toolkit.application import (  # pylint: disable=import-outside-toplevel
                    get_app,
                    run_in_terminal,
                )

                run_in_terminal(_confirm_and_start, render_cli_done=False)
                app = get_app()
                app.renderer.reset()
                app.invalidate()

            self._rprompt_text = connection_info or ""
            self._autoreply_engine: Any = None
            self._last_hp: Optional[int] = None
            self._last_mp: Optional[int] = None
            self._hp_flash: float = 0.0
            self._mp_flash: float = 0.0
            self._last_xp: Optional[int] = None
            self._xp_flash: float = 0.0
            self._xp_delta: int = 0
            self._xp_history: collections.deque[tuple[float, int]] = collections.deque()
            self._has_gmcp: bool = False
            self._scroll: Any = None

            self._style_normal = prompt_toolkit.styles.Style.from_dict(
                {
                    "": "fg:#cccccc bg:#1a0000",
                    "bottom-toolbar": "noreverse",
                    "bottom-toolbar.text": "",
                    "rprompt-info": "fg:#dddddd",
                    "rprompt": "fg:#dddddd bg:#1a0000",
                    "xp-label": "fg:#c8a8ff",
                    "xp-delta-pos": "fg:#44ff44 bold",
                    "xp-delta-neg": "fg:#ff4444 bold",
                    "xp-rate": "fg:#888888",
                    "stat-label": "fg:#aaaaaa",
                    "auto-suggest": "fg:#666666",
                }
            )
            self._style_autoreply = prompt_toolkit.styles.Style.from_dict(
                {
                    "": "fg:#000000 bg:#b8860b",
                    "bottom-toolbar": "noreverse bg:#b8860b",
                    "bottom-toolbar.text": "fg:#000000 bg:#b8860b",
                    "rprompt-info": "fg:#000000 bg:#b8860b",
                    "rprompt": "fg:#000000 bg:#b8860b",
                    "rprompt-autoreply": "fg:#000000 bg:#b8860b bold",
                    "xp-label": "fg:#000000 bg:#b8860b",
                    "xp-delta-pos": "fg:#000000 bg:#b8860b bold",
                    "xp-delta-neg": "fg:#000000 bg:#b8860b bold",
                    "xp-rate": "fg:#000000 bg:#b8860b",
                    "stat-label": "fg:#000000 bg:#b8860b",
                    "auto-suggest": "fg:#666666",
                }
            )
            self._style = self._style_normal
            _color_depth = None
            if os.environ.get("COLORTERM") in ("truecolor", "24bit"):
                # pylint: disable-next=import-outside-toplevel
                from prompt_toolkit.output.color_depth import ColorDepth

                _color_depth = ColorDepth.TRUE_COLOR
            self._session: "prompt_toolkit.PromptSession[str]" = prompt_toolkit.PromptSession(
                history=self._history,
                auto_suggest=prompt_toolkit.auto_suggest.AutoSuggestFromHistory(),
                enable_history_search=True,
                key_bindings=kb,
                style=self._style,
                bottom_toolbar=self._get_toolbar,
                color_depth=_color_depth,
                erase_when_done=True,
                wrap_lines=False,
            )

        def _get_toolbar(  # pylint: disable=too-many-locals,too-many-branches,too-many-statements
            self,
        ) -> "Optional[List[Tuple[str, str]]]":
            """Return toolbar as formatted text tuples with GMCP and rprompt."""
            gmcp_data: "Optional[dict[str, Any]]" = getattr(self._writer, "_gmcp_data", None)
            if not self._has_gmcp:
                if not gmcp_data:
                    return None
                self._has_gmcp = True
                if self._scroll is not None:
                    self._scroll.grow_reserve(_PT_RESERVE_WITH_TOOLBAR)

            engine = self._autoreply_engine
            ar_active = engine is not None and (engine.exclusive_active or engine.reply_pending)
            wander_active = getattr(self._writer, "_wander_active", False)
            discover_active = getattr(self._writer, "_discover_active", False)
            if wander_active or discover_active or ar_active:
                self._session.style = self._style_autoreply
            else:
                self._session.style = self._style_normal

            bars: "List[Tuple[str, str]]" = []
            room_name = ""
            now = time.monotonic()
            if gmcp_data:  # pylint: disable=too-many-nested-blocks
                vitals = gmcp_data.get("Char.Vitals")
                if isinstance(vitals, dict):
                    hp = vitals.get("hp", vitals.get("HP"))
                    maxhp = vitals.get("maxhp", vitals.get("maxHP", vitals.get("max_hp")))
                    if hp is not None:
                        try:
                            hp_int = int(hp)
                        except (TypeError, ValueError):
                            hp_int = 0
                        if self._last_hp is not None and hp_int != self._last_hp:
                            self._hp_flash = now
                            self._schedule_invalidate()
                        self._last_hp = hp_int
                        hp_flashing = (now - self._hp_flash) < 0.1
                        bars.extend(_vital_bar(hp, maxhp, _BAR_WIDTH, "hp", flash=hp_flashing))
                    mp = vitals.get(
                        "mp",
                        vitals.get("MP", vitals.get("mana", vitals.get("sp", vitals.get("SP")))),
                    )
                    maxmp = vitals.get(
                        "maxmp",
                        vitals.get(
                            "maxMP", vitals.get("max_mp", vitals.get("maxsp", vitals.get("maxSP")))
                        ),
                    )
                    if mp is not None:
                        try:
                            mp_int = int(mp)
                        except (TypeError, ValueError):
                            mp_int = 0
                        if self._last_mp is not None and mp_int != self._last_mp:
                            self._mp_flash = now
                            self._schedule_invalidate()
                        self._last_mp = mp_int
                        mp_flashing = (now - self._mp_flash) < 0.1
                        if bars:
                            bars.append(("", "   "))
                        bars.extend(_vital_bar(mp, maxmp, _BAR_WIDTH, "mp", flash=mp_flashing))

                status = gmcp_data.get("Char.Status")
                if isinstance(status, dict):
                    xp_raw = status.get("xp", status.get("XP", status.get("experience")))
                    maxxp = status.get(
                        "maxxp", status.get("maxXP", status.get("max_xp", status.get("maxexp")))
                    )
                    if xp_raw is not None:
                        try:
                            xp_int = int(xp_raw)
                        except (TypeError, ValueError):
                            xp_int = 0
                        if self._last_xp is not None and xp_int != self._last_xp:
                            self._xp_delta = xp_int - self._last_xp
                            self._xp_flash = now
                            self._xp_history.append((now, xp_int))
                            self._schedule_invalidate()
                        elif self._last_xp is None:
                            self._xp_history.append((now, xp_int))
                        self._last_xp = xp_int

                        cutoff = now - 300.0
                        while self._xp_history and self._xp_history[0][0] < cutoff:
                            self._xp_history.popleft()

                        xp_flashing = (now - self._xp_flash) < 2.0
                        if bars:
                            bars.append(("", "   "))
                        bars.extend(_vital_bar(xp_raw, maxxp, _BAR_WIDTH, "xp", flash=xp_flashing))

                        if len(self._xp_history) >= 2 and maxxp is not None:
                            oldest_t, oldest_xp = self._xp_history[0]
                            span = now - oldest_t
                            if span > 0:
                                rate_per_sec = (xp_int - oldest_xp) / span
                                try:
                                    remaining = int(maxxp) - xp_int
                                except (TypeError, ValueError):
                                    remaining = 0
                                if rate_per_sec > 0 and remaining > 0:
                                    eta_sec = remaining / rate_per_sec
                                    eta_hr = eta_sec / 3600.0
                                    if eta_hr >= 1.0:
                                        eta_text = f" ETA {eta_hr:.1f}h"
                                    else:
                                        eta_min = int(eta_sec / 60.0)
                                        eta_text = f" ETA {eta_min}m"
                                    bars.append(("class:xp-rate", eta_text))

                if isinstance(status, dict):
                    level = status.get("level")
                    if level is not None:
                        if bars:
                            bars.append(("", "  "))
                        bars.append(("class:stat-label", f"Lv:{level}"))

                    money = status.get("money")
                    if money is not None:
                        try:
                            money_int = int(money)
                            money_str = f"${money_int:,}"
                        except (TypeError, ValueError):
                            money_str = f"${money}"
                        if bars:
                            bars.append(("", "  "))
                        bars.append(("class:stat-label", money_str))

                room_info = gmcp_data.get("Room.Info", gmcp_data.get("Room.Name"))
                if isinstance(room_info, dict):
                    room_name = str(room_info.get("name", room_info.get("Name", "")))
                elif isinstance(room_info, str):
                    room_name = room_info

            if room_name:
                self._rprompt_text = room_name

            if wander_active:
                wcur = getattr(self._writer, "_wander_current", 0)
                wtot = getattr(self._writer, "_wander_total", 0)
                wander_bar = _vital_bar(wcur, wtot, 12, "wander")
                bars = bars + [("fg:#dddddd", " ")] + wander_bar
                right_text = ""
                rprompt_class = ""
            elif discover_active:
                dcur = getattr(self._writer, "_discover_current", 0)
                dtot = getattr(self._writer, "_discover_total", 0)
                disc_bar = _vital_bar(dcur, dtot, 12, "discover")
                bars = bars + [("fg:#dddddd", " ")] + disc_bar
                right_text = ""
                rprompt_class = ""
            elif ar_active:
                idx = getattr(engine, "exclusive_rule_index", None)
                if idx is not None:
                    ar_label = f"Autoreply #{idx}"
                else:
                    ar_label = "Autoreply"
                right_text = " " + ar_label
                rprompt_class = "class:rprompt-autoreply"
            else:
                right_text = " " + self._rprompt_text
                rprompt_class = "class:rprompt-info"
            right_width = len(right_text)

            cols = prompt_toolkit.application.get_app().output.get_size().columns

            bars_width = sum(_wcswidth(t) if t else 0 for _, t in bars)

            if bars:
                pad = max(1, cols - bars_width - right_width)
                result: "List[Tuple[str, str]]" = list(bars)
                result.append(("", " " * pad))
                result.append((rprompt_class, right_text))
                return result
            pad = max(1, cols - right_width)
            return [("", " " * pad), (rprompt_class, right_text)]

        def _schedule_invalidate(self) -> None:
            """Schedule a toolbar redraw after the flash duration expires."""
            try:
                app = self._session.app
                if app is not None:
                    app.invalidate()
                    loop = asyncio.get_event_loop()
                    loop.call_later(0.12, app.invalidate)
            except RuntimeError:
                pass

        def _is_password_mode(self) -> bool:
            """Return True when server has negotiated WILL ECHO."""
            return bool(self._writer.will_echo)

        async def prompt(self) -> Optional[str]:
            """
            Read one line of input from the user.

            ``is_password`` is passed as a callable so prompt_toolkit
            re-evaluates it on every render -- if the server toggles
            ``WILL ECHO`` while the prompt is already waiting, the
            display switches to masked input immediately.

            :returns: Input string, or ``None`` on EOF.
            """
            try:
                result: str = await self._session.prompt_async(
                    "",
                    is_password=prompt_toolkit.filters.Condition(
                        self._is_password_mode
                    ),
                    rprompt=[("class:rprompt", " F1 Help ")],
                )
                return result
            except EOFError:
                return None
            except KeyboardInterrupt:
                return None


def _repaint_screen(
    _event: Any, replay_buf: Optional[OutputRingBuffer], scroll: Any = None
) -> None:
    """
    Clear screen and replay recent output from the ring buffer.

    Re-establishes the DECSTBM scroll region and replays buffered output so recent MUD text
    reappears with colors intact.
    """
    # pylint: disable-next=protected-access
    reserve = scroll._reserve if scroll is not None else _PT_RESERVE_WITH_TOOLBAR

    def _run_repaint() -> None:
        try:
            _tsize = os.get_terminal_size()
        except OSError:
            return
        if scroll is not None:
            scroll.update_size(_tsize.lines, _tsize.columns)
        fd = sys.stdout.fileno()
        was_blocking = os.get_blocking(fd)
        os.set_blocking(fd, True)
        try:
            scroll_bottom = max(1, _tsize.lines - reserve - 1)
            sys.stdout.write(CLEAR_HOME)
            sys.stdout.write(decstbm(1, scroll_bottom))
            sys.stdout.write(cup(1, 1))
            if replay_buf is not None:
                data = replay_buf.replay()
                if data:
                    sys.stdout.write(data.decode("utf-8", errors="replace"))
            sys.stdout.write(SAVE_CURSOR)
            # Draw DMZ separator (between scroll region and input area).
            dmz = scroll_bottom + 1
            _input_row = _tsize.lines - reserve + 1
            if dmz < _input_row:
                sys.stdout.write(cup(dmz, 1) + CLEAR_LINE + "\u2500" * _tsize.columns)
            for _r in range(_input_row, _tsize.lines + 1):
                sys.stdout.write(cup(_r, 1) + CLEAR_LINE)
            sys.stdout.write(cup(_input_row, 1))
            sys.stdout.flush()
        finally:
            os.set_blocking(fd, was_blocking)

    from prompt_toolkit.application import (  # pylint: disable=import-outside-toplevel
        get_app,
        run_in_terminal,
    )

    run_in_terminal(_run_repaint)
    app = get_app()
    app.renderer.reset()
    app.invalidate()


def _confirm_dialog(
    title: str,
    body: str,
    warning: str = "",
    replay_buf: Optional["OutputRingBuffer"] = None,
) -> tuple[bool, bool]:
    """
    Show a Textual confirmation dialog in a subprocess.

    Launches :func:`telnetlib3.client_tui.confirm_dialog_main` as a
    subprocess, reads the result from a temporary file, and restores
    terminal state on return.

    :param title: Dialog title.
    :param body: Body text.
    :param warning: Optional warning text displayed in red.
    :param replay_buf: Optional replay buffer for screen repaint.
    :returns: ``(confirmed, dont_ask_again)`` tuple.
    """
    import json as _json  # pylint: disable=import-outside-toplevel
    import subprocess  # pylint: disable=import-outside-toplevel
    import tempfile  # pylint: disable=import-outside-toplevel

    fd, result_path = tempfile.mkstemp(suffix=".json", prefix="confirm-")
    os.close(fd)

    cmd = [
        sys.executable, "-c",
        "from telnetlib3.client_tui import confirm_dialog_main; "
        f"confirm_dialog_main({title!r}, {body!r},"
        f" warning={warning!r}, result_file={result_path!r})",
    ]

    global _editor_active  # noqa: PLW0603  # pylint: disable=global-statement
    sys.stdout.write(SCROLL_RESET)
    sys.stdout.flush()
    sys.stderr.flush()
    sys.__stderr__.flush()
    _editor_active = True
    try:
        subprocess.run(cmd, check=False)
    except FileNotFoundError:
        pass
    finally:
        _editor_active = False
        _restore_after_subprocess(replay_buf)

    confirmed = False
    dont_ask = False
    try:
        with open(result_path, "r", encoding="utf-8") as f:
            data = _json.load(f)
        confirmed = bool(data.get("confirmed", False))
        dont_ask = bool(data.get("dont_ask", False))
    except (OSError, ValueError):
        pass
    finally:
        try:
            os.unlink(result_path)
        except OSError:
            pass

    return confirmed, dont_ask


def _show_help(_event: Any, macro_defs: "Any" = None) -> None:
    """
    Display keybinding help on the alternate screen buffer.

    :param _event: prompt_toolkit key event (unused).
    :param macro_defs: Optional list of macro definitions to display.
    """
    def _run_help() -> None:
        sys.stdout.write(ENTER_ALT_SCREEN)
        sys.stdout.write(HOME + CLEAR_SCREEN)
        lines = [
            "",
            "  telnetlib3 \u2014 Keybindings",
            "",
            "  F1          This help screen",
            "  F4          Autodiscover (explore unvisited exits)",
            "  F5          Wander mode (visit same-named rooms)",
            "  F7          Browse rooms / fast travel",
            "  F8          Edit macros (TUI editor)",
            "  F9          Edit autoreplies (TUI editor)",
            "  Shift+F9    Toggle autoreplies on/off",
            "  Ctrl+]      Disconnect",
            "",
            "  Command processing:",
            "  ;            Separator (e.g. get all;drop sword)",
            "  3n;2e        Repeat prefix (expands to n;n;n;e;e)",
            "",
        ]
        if macro_defs:
            lines.append("  User macros:")
            for m in macro_defs:
                key = " ".join(m.keys) if hasattr(m, "keys") else str(getattr(m, "key", "?"))
                text = getattr(m, "text", "")
                display = text.replace("\r\n", "<CR>").replace("\r", "<CR>")
                if len(display) > 40:
                    display = display[:37] + "..."
                lines.append(f"  {key:<12}{display}")
            lines.append("")
        lines.append("  Press any key to return.")
        lines.append("")
        sys.stdout.write("\r\n".join(lines))
        sys.stdout.flush()

        import tty  # pylint: disable=import-outside-toplevel
        import select  # pylint: disable=import-outside-toplevel
        import termios  # pylint: disable=import-outside-toplevel,redefined-outer-name

        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        was_blocking = os.get_blocking(fd)
        try:
            tty.setraw(fd)
            os.set_blocking(fd, True)
            select.select([fd], [], [])
            os.read(fd, 1)
        finally:
            os.set_blocking(fd, was_blocking)
            termios.tcsetattr(fd, termios.TCSADRAIN, old)

        sys.stdout.write(EXIT_ALT_SCREEN)
        sys.stdout.flush()

        try:
            _tsize = os.get_terminal_size()
            scroll_bottom = max(1, _tsize.lines - _PT_RESERVE_WITH_TOOLBAR)
            sys.stdout.write(decstbm(1, scroll_bottom))
            sys.stdout.write(cup(scroll_bottom, 1))
            sys.stdout.write(SAVE_CURSOR)
            sys.stdout.flush()
        except OSError:
            pass

    from prompt_toolkit.application import (  # pylint: disable=import-outside-toplevel
        get_app,
        run_in_terminal,
    )

    run_in_terminal(_run_help)
    app = get_app()
    app.renderer.reset()
    app.invalidate()


def _launch_tui_editor(
    _event: Any,
    editor_type: str,
    writer: Union[TelnetWriter, TelnetWriterUnicode],
    replay_buf: Optional[OutputRingBuffer] = None,
) -> None:
    """
    Launch a TUI editor for macros or autoreplies in a subprocess.

    Suspends the prompt_toolkit app, runs the editor, then reloads
    definitions on return.

    :param _event: prompt_toolkit key event (unused).
    :param editor_type: ``"macros"`` or ``"autoreplies"``.
    :param writer: Telnet writer with file path and definition attributes.
    :param replay_buf: Optional replay buffer for screen repaint on return.
    """
    import subprocess  # pylint: disable=import-outside-toplevel

    from ._paths import CONFIG_DIR as _config_dir  # pylint: disable=import-outside-toplevel

    _session_key = getattr(writer, "_session_key", "")

    if editor_type == "macros":
        path = getattr(writer, "_macros_file", None) or os.path.join(_config_dir, "macros.json")
        entry = "edit_macros_main"
        # pylint: disable=import-outside-toplevel
        from .rooms import rooms_path as _rooms_path_fn
        from .rooms import current_room_path as _current_room_path_fn
        # pylint: enable=import-outside-toplevel

        _rp = getattr(writer, "_rooms_file", None) or _rooms_path_fn(_session_key)
        _crp = getattr(writer, "_current_room_file", None) or _current_room_path_fn(_session_key)
        cmd_args = f"{path!r}, {_session_key!r}," f" rooms_file={_rp!r}, current_room_file={_crp!r}"
    else:
        path = getattr(writer, "_autoreplies_file", None) or os.path.join(
            _config_dir, "autoreplies.json"
        )
        entry = "edit_autoreplies_main"
        engine = getattr(writer, "_autoreply_engine", None)
        _select = getattr(engine, "last_matched_pattern", "") if engine else ""
        cmd_args = f"{path!r}, {_session_key!r}, select_pattern={_select!r}"

    cmd = [sys.executable, "-c", f"from telnetlib3.client_tui import {entry}; {entry}({cmd_args})"]

    log = logging.getLogger(__name__)

    def _run_editor() -> None:
        global _editor_active  # noqa: PLW0603  # pylint: disable=global-statement
        # Reset DECSTBM scroll region before launching TUI subprocess --
        # the Textual app uses the alternate screen buffer, but some
        # terminals share scroll region state across buffers, causing a
        # "doubling" effect where widgets render at the wrong row.
        sys.stdout.write(SCROLL_RESET)
        sys.stdout.flush()
        # Flush and drain stderr -- Textual writes all output to
        # sys.__stderr__, and leftover buffered data can fill the PTY
        # buffer, blocking the editor's WriterThread.
        sys.stderr.flush()
        sys.__stderr__.flush()
        _editor_active = True
        try:
            subprocess.run(cmd, check=False)
        except FileNotFoundError:
            log.warning("could not launch TUI editor subprocess")
        finally:
            _editor_active = False
            _restore_after_subprocess(replay_buf)

        if editor_type == "macros":
            _reload_macros(writer, path, _session_key, log)
        else:
            _reload_autoreplies(writer, path, _session_key, log)

    from prompt_toolkit.application import (  # pylint: disable=import-outside-toplevel
        get_app,
        run_in_terminal,
    )

    run_in_terminal(_run_editor)
    app = get_app()
    app.renderer.reset()
    app.invalidate()


def _reload_macros(
    writer: Union[TelnetWriter, TelnetWriterUnicode],
    path: str,
    session_key: str,
    log: logging.Logger,
) -> None:
    """Reload macro definitions from disk and rebind keys."""
    if not os.path.exists(path):
        return
    from .macros import bind_macros, load_macros  # pylint: disable=import-outside-toplevel

    try:
        # pylint: disable=protected-access
        new_defs = load_macros(path, session_key)
        writer._macro_defs = new_defs
        writer._macros_file = path
        # Rebind keys -- prompt_toolkit uses last-registered-wins,
        # so new bindings for the same keys override old ones.
        kb = getattr(writer, "_pt_kb", None)
        if kb is not None:
            bind_macros(kb, new_defs, writer, log)
        n_macros = len(new_defs)
        # pylint: enable=protected-access
        log.info("reloaded %d macros from %s", n_macros, path)
    except ValueError as exc:
        log.warning("failed to reload macros: %s", exc)


def _reload_autoreplies(
    writer: Union[TelnetWriter, TelnetWriterUnicode],
    path: str,
    session_key: str,
    log: logging.Logger,
) -> None:
    """Reload autoreply rules from disk after editing."""
    if not os.path.exists(path):
        return
    from .autoreply import load_autoreplies  # pylint: disable=import-outside-toplevel

    try:
        # pylint: disable=protected-access
        writer._autoreply_rules = load_autoreplies(path, session_key)
        writer._autoreplies_file = path
        n_rules = len(writer._autoreply_rules)
        # pylint: enable=protected-access
        log.info("reloaded %d autoreplies from %s", n_rules, path)
    except ValueError as exc:
        log.warning("failed to reload autoreplies: %s", exc)


def _launch_room_browser(
    _event: Any,
    writer: Union[TelnetWriter, TelnetWriterUnicode],
    replay_buf: Optional["OutputRingBuffer"] = None,
) -> None:
    """
    Launch the room browser TUI in a subprocess.

    On return, check for a fast travel file and queue movement commands.

    :param _event: prompt_toolkit key event (unused).
    :param writer: Telnet writer with session attributes.
    :param replay_buf: Optional replay buffer for screen repaint on return.
    """
    import subprocess  # pylint: disable=import-outside-toplevel

    _session_key = getattr(writer, "_session_key", "")
    if not _session_key:
        return

    # pylint: disable=import-outside-toplevel
    from .rooms import rooms_path as _rooms_path_fn
    from .rooms import fasttravel_path as _fasttravel_path_fn
    from .rooms import read_fasttravel
    from .rooms import current_room_path as _current_room_path_fn
    # pylint: enable=import-outside-toplevel

    _rp = getattr(writer, "_rooms_file", None) or _rooms_path_fn(_session_key)
    _crp = getattr(writer, "_current_room_file", None) or _current_room_path_fn(_session_key)
    _ftp = _fasttravel_path_fn(_session_key)

    cmd = [
        sys.executable,
        "-c",
        f"from telnetlib3.client_tui import edit_rooms_main; "
        f"edit_rooms_main({_rp!r}, {_session_key!r}, {_crp!r}, {_ftp!r})",
    ]

    log = logging.getLogger(__name__)

    def _run_browser() -> None:
        global _editor_active  # noqa: PLW0603  # pylint: disable=global-statement
        sys.stdout.write(SCROLL_RESET)
        sys.stdout.flush()
        sys.stderr.flush()
        sys.__stderr__.flush()
        _editor_active = True
        try:
            subprocess.run(cmd, check=False)
        except FileNotFoundError:
            log.warning("could not launch room browser subprocess")
        finally:
            _editor_active = False
            _restore_after_subprocess(replay_buf)

        # Reload room graph from disk to pick up bookmark changes made in TUI.
        from .rooms import load_rooms as _load_rooms  # pylint: disable=import-outside-toplevel

        if os.path.exists(_rp):
            _reloaded = _load_rooms(_rp)
            room_graph = getattr(writer, "_room_graph", None)
            if room_graph is not None:
                room_graph.rooms = _reloaded.rooms

        steps, slow = read_fasttravel(_ftp)
        if steps:
            log.debug("fast travel: scheduling %d steps (slow=%s)", len(steps), slow)
            loop = asyncio.get_event_loop()
            loop.call_soon_threadsafe(
                asyncio.ensure_future, _fast_travel(steps, writer, log, slow=slow)
            )

    from prompt_toolkit.application import (  # pylint: disable=import-outside-toplevel
        get_app,
        run_in_terminal,
    )

    run_in_terminal(_run_browser)
    app = get_app()
    app.renderer.reset()
    app.invalidate()


async def _fast_travel(
    steps: list[tuple[str, str]],
    writer: Union[TelnetWriter, TelnetWriterUnicode],
    log: logging.Logger,
    slow: bool = False,
    destination: str = "",
    correct_names: bool = True,
) -> None:
    """
    Execute fast travel by sending movement commands with GA/EOR pacing.

    Uses the same ``_wait_for_prompt`` / ``_echo_command`` functions that
    the autoreply engine and manual input use, so commands are paced by
    the server's GA/EOR prompt signal and echoed visibly.

    In fast mode (default), exclusive autoreplies are suppressed.
    Non-exclusive autoreplies still fire; travel pauses until they
    complete and then waits for a clean EOR with no match before
    sending the next direction.

    In slow mode, all autoreplies fire including exclusive ones.

    When the player arrives at an unexpected room, instead of aborting
    the function re-pathfinds from the actual position to *destination*
    and continues with the new route (up to 3 re-routes).

    :param steps: List of (direction, expected_room_num) pairs.
    :param writer: Telnet writer for sending commands.
    :param log: Logger.
    :param slow: If ``True``, allow exclusive autoreplies.
    :param destination: Final target room ID for re-pathfinding on detour.
    :param correct_names: If ``True`` (default), rewrite graph edges when
        arriving at a same-name room with a different ID.  Set to ``False``
        for autowander where distinct room IDs must be preserved.
    """
    # pylint: disable=too-many-locals,too-many-branches,too-many-statements,too-many-nested-blocks
    wait_fn = getattr(writer, "_wait_for_prompt", None)
    echo_fn = getattr(writer, "_echo_command", None)

    from .autoreply import AutoreplyEngine  # pylint: disable=import-outside-toplevel

    def _get_engine() -> Optional["AutoreplyEngine"]:
        """Find the active autoreply engine, if any."""
        # Stashed by _refresh_autoreply_engine via repl._autoreply_engine
        # which is the same object as the one feeding on server output.
        return getattr(writer, "_autoreply_engine", None)

    engine = _get_engine()
    if engine is not None and not slow:
        engine.suppress_exclusive = True

    mode = "slow travel" if slow else "fast travel"

    from .rooms import RoomGraph  # pylint: disable=import-outside-toplevel

    def _get_graph() -> Optional[RoomGraph]:
        return getattr(writer, "_room_graph", None)

    def _room_name(num: str) -> str:
        """Look up a human-readable room name from the writer's graph."""
        graph = _get_graph()
        if graph is not None:
            room = graph.rooms.get(num)
            if room is not None:
                return f"{room.name} ({num[:8]}...)"
        return num

    def _names_match(expected_num: str, actual_num: str) -> bool:
        """Check whether two room IDs refer to rooms with the same name."""
        graph = _get_graph()
        if graph is None:
            return False
        expected = graph.rooms.get(expected_num)
        actual = graph.rooms.get(actual_num)
        if expected is None or actual is None:
            return False
        return expected.name == actual.name and bool(expected.name)

    def _correct_edge(
        prev_num: str,
        direction: str,
        old_target: str,
        new_target: str,
        remaining_steps: list[tuple[str, str]],
    ) -> None:
        """Update the graph edge and rewrite remaining steps in-place."""
        graph = _get_graph()
        if graph is not None:
            prev = graph.rooms.get(prev_num)
            if prev is not None and prev.exits.get(direction) == old_target:
                prev.exits[direction] = new_target
                log.info(
                    "%s: corrected exit %s of %s: %s -> %s",
                    mode,
                    direction,
                    prev_num[:8],
                    old_target[:8],
                    new_target[:8],
                )
        for j, (d, r) in enumerate(remaining_steps):
            if r == old_target:
                remaining_steps[j] = (d, new_target)

    room_changed = getattr(writer, "_room_changed", None)
    _max_retries = 3
    _max_reroutes = 3

    if not destination and steps:
        destination = steps[-1][1]

    try:
        step_idx = 0
        reroute_count = 0
        while step_idx < len(steps):
            direction, expected_room = steps[step_idx]
            prev_room = getattr(writer, "_current_room_num", "")

            for attempt in range(_max_retries + 1):
                # Delay between steps (and retries) for server rate limits.
                if step_idx > 0 or attempt > 0:
                    await asyncio.sleep(_MOVE_STEP_DELAY)

                if room_changed is not None:
                    room_changed.clear()

                tag = f" [{step_idx + 1}/{len(steps)}]"
                if attempt == 0:
                    log.info("%s [%d/%d] %s", mode, step_idx + 1, len(steps), direction)
                    if echo_fn is not None:
                        echo_fn(direction + tag)
                else:
                    log.info(
                        "%s [%d/%d] %s (retry %d)",
                        mode,
                        step_idx + 1,
                        len(steps),
                        direction,
                        attempt,
                    )
                # Clear prompt_ready before sending so wait_fn waits
                # for a FRESH GA/EOR from this step's response.  The
                # server sends multiple GA/EORs per response (room
                # prompt + GMCP vitals updates), and stale signals
                # from the previous step cause wait_fn to return
                # before the current room output has been received.
                _prompt_ready = getattr(writer, "_prompt_ready", None)
                if _prompt_ready is not None:
                    _prompt_ready.clear()

                writer.write(direction + "\r\n")  # type: ignore[arg-type]

                if wait_fn is not None:
                    await wait_fn()

                # Yield to let _read_server feed the room output to the
                # autoreply engine before we check reply_pending.
                await asyncio.sleep(0)

                engine = _get_engine()
                _cond_cancelled = False
                if engine is not None:
                    while engine.reply_pending:
                        await asyncio.sleep(0.05)
                    if slow:
                        failed = engine.condition_failed
                        if failed is not None:
                            rule_idx, desc = failed
                            msg = (
                                f"Travel mode cancelled - failed "
                                f"conditional in AUTOREPLY "
                                f"#{rule_idx} [{desc}]"
                            )
                            log.warning("%s", msg)
                            if echo_fn is not None:
                                echo_fn(msg)
                            _cond_cancelled = True
                    # In slow mode, exclusive rules enter exclusive mode
                    # (e.g. "kill" sent, waiting for "died\.").  Wait for
                    # combat to finish before moving to the next room.
                    # After exclusive/reply_pending clear, wait for a
                    # fresh prompt so the server response to the last
                    # autoreply command is processed -- it may trigger
                    # new matches (cascading always-rules).
                    if slow and (engine.exclusive_active or engine.reply_pending):
                        _settle_passes = 0
                        _max_settle = 20  # safety cap
                        while _settle_passes < _max_settle:
                            if engine.exclusive_active:
                                while engine.exclusive_active:
                                    engine.check_timeout()
                                    await asyncio.sleep(0.05)
                            while engine.reply_pending:
                                await asyncio.sleep(0.05)
                            # Wait for server to respond to whatever the
                            # autoreply just sent.  The prompt signal
                            # drives on_prompt() which may queue new
                            # replies.
                            if wait_fn is not None:
                                await wait_fn()
                            await asyncio.sleep(0)
                            # If neither exclusive nor reply_pending
                            # after the prompt, we've converged.
                            if not engine.exclusive_active and not engine.reply_pending:
                                break
                            _settle_passes += 1
                if _cond_cancelled:
                    break

                # GMCP Room.Info may arrive after the EOR.  Wait for it.
                actual = getattr(writer, "_current_room_num", "")
                if expected_room and actual != expected_room and room_changed is not None:
                    try:
                        await asyncio.wait_for(room_changed.wait(), timeout=2.0)
                    except asyncio.TimeoutError:
                        pass
                    actual = getattr(writer, "_current_room_num", "")

                if actual == expected_room:
                    break
                # Same-name room with different ID -- correct the edge
                # and continue as if we arrived at the expected room.
                # Skipped when correct_names=False (autowander) to preserve
                # distinct room IDs in grids of same-named rooms.
                if (
                    correct_names
                    and expected_room
                    and actual
                    and actual != expected_room
                    and _names_match(expected_room, actual)
                ):
                    log.info(
                        "%s: room ID changed for %s (%s -> %s), correcting",
                        mode,
                        _room_name(actual),
                        expected_room[:8],
                        actual[:8],
                    )
                    _correct_edge(prev_room, direction, expected_room, actual, steps)
                    expected_room = actual
                    break
                # Room didn't change -- server likely rejected move (rate limit).
                # Retry unless we've exhausted attempts.
                if actual == prev_room and attempt < _max_retries:
                    continue
                # Arrived at wrong room -- try to re-route.
                break

            if _cond_cancelled:
                break
            if expected_room and actual and actual != expected_room:
                # Update graph edge to reflect actual connection.
                graph = _get_graph()
                if graph is not None:
                    prev = graph.rooms.get(prev_room)
                    if prev is not None:
                        prev.exits[direction] = actual
                        log.info(
                            "%s: updated edge %s of %s: -> %s",
                            mode,
                            direction,
                            prev_room[:8],
                            actual[:8],
                        )

                # Try re-pathfinding from actual position.
                if (
                    destination
                    and actual
                    and actual != destination
                    and reroute_count < _max_reroutes
                    and graph is not None
                ):
                    new_steps = graph.find_path_with_rooms(actual, destination)
                    if new_steps is not None:
                        reroute_count += 1
                        msg = (
                            f"{mode}: re-routing from "
                            f"{_room_name(actual)}"
                            f" ({reroute_count}/{_max_reroutes})"
                        )
                        log.info("%s", msg)
                        if echo_fn is not None:
                            echo_fn(msg)
                        steps = new_steps
                        step_idx = 0
                        continue

                expected_name = _room_name(expected_room)
                actual_name = _room_name(actual)
                msg = (
                    f"{mode} stopped: expected {expected_name} after "
                    f"'{direction}', got {actual_name}"
                )
                log.warning("%s", msg)
                if echo_fn is not None:
                    echo_fn(msg)
                break
            step_idx += 1
    finally:
        engine = _get_engine()
        if engine is not None:
            engine.suppress_exclusive = False


async def _autowander(
    writer: Union[TelnetWriter, TelnetWriterUnicode], log: logging.Logger
) -> None:
    """
    Visit up to 99 same-named rooms using slow travel.

    Computes a list of rooms with the same name as the current room, sorted by least-recently-
    visited, then walks through them one leg at a time with slow travel (autoreplies fire in each
    room).

    :param writer: Telnet writer with room graph and session attributes.
    :param log: Logger.
    """
    if getattr(writer, "_wander_active", False):
        return

    current = getattr(writer, "_current_room_num", "")
    graph = getattr(writer, "_room_graph", None)
    echo_fn = getattr(writer, "_echo_command", None)
    if not current or graph is None:
        if echo_fn is not None:
            echo_fn("AUTOWANDER: no room data")
        return

    targets = graph.find_same_name(current)
    if not targets:
        if echo_fn is not None:
            echo_fn("AUTOWANDER: no matching rooms")
        return

    # Nearest-neighbor ordering: greedily pick the closest unvisited
    # target from the current position to minimise backtracking.
    ordered: list[type(targets[0])] = []
    remaining = list(targets)
    pos = current
    while remaining:
        best_idx = 0
        best_dist = float("inf")
        for idx, candidate in enumerate(remaining):
            path = graph.find_path_with_rooms(pos, candidate.num)
            dist = len(path) if path is not None else float("inf")
            if dist < best_dist:
                best_dist = dist
                best_idx = idx
        chosen = remaining.pop(best_idx)
        ordered.append(chosen)
        pos = chosen.num
    targets = ordered

    _leg_retries = 3
    visited: set[str] = {current}
    # pylint: disable=protected-access
    writer._wander_active = True
    writer._wander_total = len(targets)
    try:
        for i, target_room in enumerate(targets):
            writer._wander_current = i + 1
            pos = getattr(writer, "_current_room_num", "")
            if pos == target_room.num or target_room.num in visited:
                visited.add(target_room.num)
                continue

            arrived = False
            for leg_attempt in range(_leg_retries + 1):
                pos = getattr(writer, "_current_room_num", "")
                steps = graph.find_path_with_rooms(pos, target_room.num)
                if steps is None:
                    if echo_fn is not None:
                        echo_fn(
                            f"AUTOWANDER [{i + 1}/{len(targets)}]: "
                            f"no path to {target_room.name} "
                            f"({target_room.num[:8]})"
                        )
                    break
                if echo_fn is not None:
                    tag = (
                        "" if leg_attempt == 0
                        else f" (retry {leg_attempt})"
                    )
                    echo_fn(
                        f"AUTOWANDER [{i + 1}/{len(targets)}]: "
                        f"heading to {target_room.name} "
                        f"({target_room.num[:8]}){tag}"
                    )
                await _fast_travel(
                    steps, writer, log,
                    slow=True,
                    destination=target_room.num,
                    correct_names=False,
                )
                # Wait for any autoreply combat triggered by the
                # arrival glance to finish before moving on.  The
                # post_command "glance" from the previous kill can
                # trigger a new "kill X" that enters exclusive mode
                # AFTER _fast_travel's settle loop has returned.
                _ar = getattr(writer, "_autoreply_engine", None)
                if _ar is not None:
                    _settle = 0
                    while _settle < 60:
                        if _ar.exclusive_active:
                            while _ar.exclusive_active:
                                _ar.check_timeout()
                                await asyncio.sleep(0.1)
                        while _ar.reply_pending:
                            await asyncio.sleep(0.05)
                        await asyncio.sleep(0.1)
                        if (
                            not _ar.exclusive_active
                            and not _ar.reply_pending
                        ):
                            break
                        _settle += 1
                actual = getattr(writer, "_current_room_num", "")
                if actual == target_room.num:
                    arrived = True
                    visited.add(actual)
                    break
                # Mark any intermediate room we passed through.
                if actual:
                    visited.add(actual)
                if leg_attempt < _leg_retries:
                    log.info(
                        "AUTOWANDER: leg to %s failed, retrying (%d/%d)",
                        target_room.num[:8],
                        leg_attempt + 1,
                        _leg_retries,
                    )
                    await asyncio.sleep(1.0)
            actual = getattr(writer, "_current_room_num", "")
            if not arrived and actual != target_room.num:
                if echo_fn is not None:
                    echo_fn(
                        f"AUTOWANDER [{i + 1}/{len(targets)}]: "
                        f"could not reach {target_room.name} "
                        f"({target_room.num[:8]})"
                    )
                break
    finally:
        writer._wander_active = False
        writer._wander_current = 0
        writer._wander_total = 0
        writer._wander_task = None


async def _autodiscover(
    writer: Union[TelnetWriter, TelnetWriterUnicode], log: logging.Logger
) -> None:
    """
    Explore unvisited exits reachable from the current room.

    BFS-discovers frontier exits (leading to unvisited or unknown rooms),
    travels to each, then returns to the starting room before trying the
    next.  Maintains an in-memory ``tried`` set to avoid retrying exits
    that failed or led to unexpected rooms.

    :param writer: Telnet writer with room graph and session attributes.
    :param log: Logger.
    """
    if getattr(writer, "_discover_active", False):
        return

    current = getattr(writer, "_current_room_num", "")
    graph = getattr(writer, "_room_graph", None)
    echo_fn = getattr(writer, "_echo_command", None)
    if not current or graph is None:
        if echo_fn is not None:
            echo_fn("AUTODISCOVER: no room data")
        return

    tried: set[tuple[str, str]] = set()

    branches = graph.find_branches(current)
    if not branches:
        if echo_fn is not None:
            echo_fn("AUTODISCOVER: no unvisited exits nearby")
        return

    # pylint: disable=protected-access
    writer._discover_active = True
    writer._discover_total = len(branches)
    writer._discover_current = 0
    step_count = 0
    try:
        while True:
            pos = getattr(writer, "_current_room_num", "")
            # Re-discover from current position each iteration — picks up
            # newly revealed exits from rooms we just visited, nearest-first.
            branches = [
                (gw, d, t) for gw, d, t in graph.find_branches(pos)
                if (gw, d) not in tried
            ]
            if not branches:
                break

            writer._discover_total = step_count + len(branches)
            gw_room, direction, target_num = branches[0]
            step_count += 1
            writer._discover_current = step_count

            # Travel to the gateway room (nearest-first, so usually short).
            if pos != gw_room:
                steps = graph.find_path_with_rooms(pos, gw_room)
                if steps is None:
                    tried.add((gw_room, direction))
                    if echo_fn is not None:
                        echo_fn(
                            f"AUTODISCOVER [{step_count}]: "
                            f"no path to gateway {gw_room[:8]}"
                        )
                    continue
                if echo_fn is not None:
                    echo_fn(
                        f"AUTODISCOVER [{step_count}]: "
                        f"heading to gateway {gw_room[:8]}"
                    )
                await _fast_travel(
                    steps, writer, log, slow=False, destination=gw_room,
                )
                actual = getattr(writer, "_current_room_num", "")
                if actual != gw_room:
                    tried.add((gw_room, direction))
                    log.info(
                        "AUTODISCOVER: failed to reach gateway %s",
                        gw_room[:8],
                    )
                    continue

            # Step through the frontier exit.
            if echo_fn is not None:
                echo_fn(
                    f"AUTODISCOVER [{step_count}]: "
                    f"exploring {direction} from {gw_room[:8]}"
                )
            _send = getattr(writer, "_send_line", None)
            if _send is not None:
                _send(direction)
            elif isinstance(writer, TelnetWriterUnicode):
                writer.write(direction + "\r\n")
            else:
                writer.write((direction + "\r\n").encode("utf-8"))
            # Wait for room arrival.
            for _wait in range(30):
                await asyncio.sleep(0.3)
                new_room = getattr(writer, "_current_room_num", "")
                if new_room != gw_room:
                    break
            else:
                tried.add((gw_room, direction))
                if echo_fn is not None:
                    echo_fn(
                        f"AUTODISCOVER [{step_count}]: "
                        f"no room change after {direction}"
                    )
                continue

            tried.add((gw_room, direction))
            actual = getattr(writer, "_current_room_num", "")
            if target_num and actual != target_num and target_num in graph.rooms:
                if echo_fn is not None:
                    echo_fn(
                        f"AUTODISCOVER [{step_count}]: "
                        f"unexpected room {actual[:8]} "
                        f"(expected {target_num[:8]})"
                    )

            # Wait for any autoreply to settle.
            _ar = getattr(writer, "_autoreply_engine", None)
            if _ar is not None:
                _settle = 0
                while _settle < 60:
                    if _ar.exclusive_active:
                        while _ar.exclusive_active:
                            _ar.check_timeout()
                            await asyncio.sleep(0.1)
                    while _ar.reply_pending:
                        await asyncio.sleep(0.05)
                    await asyncio.sleep(0.1)
                    if not _ar.exclusive_active and not _ar.reply_pending:
                        break
                    _settle += 1

            # Stay where we are — next iteration re-discovers branches
            # from current position, so nearby clusters get swept without
            # backtracking.
    except asyncio.CancelledError:
        pass
    finally:
        writer._discover_active = False
        writer._discover_current = 0
        writer._discover_total = 0
        writer._discover_task = None


# std imports
import re as _re  # noqa: E402  # pylint: disable=wrong-import-position

_REPEAT_RE = _re.compile(r"^(\d+)([A-Za-z].*)$")
_BACKTICK_RE = _re.compile(r"`[^`]*`")


def expand_commands(line: str) -> list[str]:
    """
    Split *line* on ``;`` (outside backticks) and expand repeat prefixes.

    Backtick-enclosed tokens (e.g. ```fast travel 123```, ```delay 1s```)
    are preserved verbatim -- they are not split on ``;`` and repeat
    expansion is not applied.

    A segment like ``5e`` becomes ``['e', 'e', 'e', 'e', 'e']``.
    Only a leading integer followed immediately by an alphabetic
    character triggers expansion (e.g. ``5east`` -> 5 × ``east``).
    Segments without a leading digit are passed through unchanged.

    :param line: Raw user input line.
    :returns: Flat list of individual commands.
    """
    # Replace backtick tokens with placeholders to protect from ; splitting.
    placeholders: list[str] = []

    def _replace_bt(m: _re.Match[str]) -> str:
        placeholders.append(m.group(0))
        return f"\x00BT{len(placeholders) - 1}\x00"

    protected = _BACKTICK_RE.sub(_replace_bt, line)
    parts = protected.split(";") if ";" in protected else [protected]
    result: list[str] = []
    for part in parts:
        stripped = part.strip()
        if not stripped:
            continue
        # Restore backtick placeholders.
        while "\x00BT" in stripped:
            for i, orig in enumerate(placeholders):
                stripped = stripped.replace(f"\x00BT{i}\x00", orig)
        if stripped.startswith("`") and stripped.endswith("`"):
            result.append(stripped)
            continue
        m = _REPEAT_RE.match(stripped)
        if m:
            count = min(int(m.group(1)), 200)
            cmd = m.group(2)
            result.extend([cmd] * count)
        else:
            result.append(stripped)
    return result


_TRAVEL_RE = _re.compile(
    r"^`(fast travel|slow travel|return fast|return slow|autowander|autodiscover)\s*(.*?)`$",
    _re.IGNORECASE,
)


async def _handle_travel_commands(
    parts: list[str], writer: Union[TelnetWriter, TelnetWriterUnicode], log: logging.Logger
) -> list[str]:
    """
    Scan *parts* for travel commands, execute them, and return remaining parts.

    Recognised commands (case-insensitive, enclosed in backticks):

    - ```fast travel <id>``` -- fast travel to room *id*
    - ```slow travel <id>``` -- slow travel to room *id*
    - ```return fast``` -- fast travel to the current room (snapshot)
    - ```return slow``` -- slow travel to the current room (snapshot)
    - ```autowander``` -- visit all same-named rooms via slow travel
    - ```autodiscover``` -- explore unvisited exits from nearby rooms

    Only the **first** travel command in the list is handled; everything
    before it is returned as-is (already sent by the caller), and everything
    after it is returned for the caller to send as chained commands once
    travel finishes.

    :param parts: Expanded command list from :func:`expand_commands`.
    :param writer: Telnet writer with room graph attributes.
    :param log: Logger.
    :returns: Commands that still need to be sent to the server.
    """
    from .rooms import load_rooms  # pylint: disable=import-outside-toplevel

    for idx, cmd in enumerate(parts):
        m = _TRAVEL_RE.match(cmd)
        if not m:
            continue
        verb = m.group(1).lower()
        arg = m.group(2).strip()

        if verb == "autowander":
            await _autowander(writer, log)
            return parts[idx + 1 :]

        if verb == "autodiscover":
            await _autodiscover(writer, log)
            return parts[idx + 1 :]

        slow = "slow" in verb
        is_return = verb.startswith("return")

        if is_return:
            room_id = getattr(writer, "_current_room_num", "")
        else:
            room_id = arg

        if not room_id:
            log.warning("travel command with no room id: %r", cmd)
            break

        current = getattr(writer, "_current_room_num", "")
        if not current:
            log.warning("no current room -- cannot travel")
            break

        rooms_file = getattr(writer, "_rooms_file", None)
        if not rooms_file:
            session_key = getattr(writer, "_session_key", "")
            if session_key:
                from .rooms import rooms_path as _rp_fn  # pylint: disable=import-outside-toplevel

                rooms_file = _rp_fn(session_key)
        if not rooms_file:
            log.warning("no rooms file -- cannot travel")
            break

        graph = load_rooms(rooms_file)
        path = graph.find_path_with_rooms(current, room_id)
        if path is None:
            log.warning("no path from %s to %s", current, room_id)
            break

        await _fast_travel(path, writer, log, slow=slow, destination=room_id)
        return parts[idx + 1 :]

    return parts


_MOVE_STEP_DELAY = 0.15
_MOVE_MAX_RETRIES = 3


async def _send_chained(
    commands: list[str], writer: Union[TelnetWriter, TelnetWriterUnicode], log: logging.Logger
) -> None:
    """
    Send multiple commands with GA/EOR pacing between each.

    The first command is assumed to have already been sent by the caller.
    This coroutine sends commands 2..N, waiting for the server prompt
    signal before each one.

    When all commands in the list are identical (e.g. ``9e`` expanded to
    nine ``e`` commands), movement retry logic is applied: if the room
    does not change after a command, the same command is retried up to
    :data:`_MOVE_MAX_RETRIES` times with a delay between attempts.

    :param commands: List of commands (index 1+ will be sent).
    :param writer: Telnet writer.
    :param log: Logger.
    """
    wait_fn = getattr(writer, "_wait_for_prompt", None)
    echo_fn = getattr(writer, "_echo_command", None)
    prompt_ready = getattr(writer, "_prompt_ready", None)
    room_changed = getattr(writer, "_room_changed", None)

    is_repeated = len(commands) > 1 and len(set(commands)) == 1

    for _idx, cmd in enumerate(commands[1:], 1):
        prev_room = getattr(writer, "_current_room_num", "") if is_repeated else ""

        if not is_repeated:
            # Mixed commands: GA/EOR pacing only.
            if prompt_ready is not None:
                prompt_ready.clear()
            if wait_fn is not None:
                await wait_fn()
            log.debug("chained command: %r", cmd)
            if echo_fn is not None:
                echo_fn(cmd)
            writer.write(cmd + "\r\n")  # type: ignore[arg-type]
            continue

        # Repeated commands: delay + room-change pacing with retry.
        for attempt in range(_MOVE_MAX_RETRIES + 1):
            # Always delay -- the first repeated command needs spacing
            # from the caller's initial send, and retries need a longer
            # back-off to respect the server's rate limit.
            if attempt == 0:
                await asyncio.sleep(_MOVE_STEP_DELAY)
            else:
                await asyncio.sleep(1.0)
            if room_changed is not None:
                room_changed.clear()
            if prompt_ready is not None:
                prompt_ready.clear()
            if attempt == 0:
                log.debug("chained command: %r", cmd)
                if echo_fn is not None:
                    echo_fn(cmd)
            else:
                log.info("chained retry %d: %r", attempt, cmd)
            writer.write(cmd + "\r\n")  # type: ignore[arg-type]

            if not prev_room:
                break

            # Wait briefly for room change -- GMCP typically arrives
            # within 100-200ms.  A short timeout keeps movement brisk
            # while still detecting rate-limit rejections.
            actual = getattr(writer, "_current_room_num", "")
            if actual != prev_room:
                break
            if room_changed is not None:
                try:
                    await asyncio.wait_for(room_changed.wait(), timeout=0.5)
                except asyncio.TimeoutError:
                    pass
                actual = getattr(writer, "_current_room_num", "")
            if actual != prev_room:
                break
            if attempt < _MOVE_MAX_RETRIES:
                log.info(
                    "room unchanged after %r, retrying (%d/%d)", cmd, attempt + 1, _MOVE_MAX_RETRIES
                )
            else:
                log.warning(
                    "room unchanged after %r, giving up after %d retries", cmd, _MOVE_MAX_RETRIES
                )
                return


async def execute_macro_commands(
    text: str, writer: Union[TelnetWriter, TelnetWriterUnicode], log: logging.Logger
) -> None:
    """
    Execute a macro text string, handling travel and delay commands.

    Expands the text with :func:`expand_commands`, then processes each
    part -- backtick-enclosed travel commands are routed through
    :func:`_handle_travel_commands`, delay commands pause execution,
    and plain commands are sent to the server with GA/EOR pacing.

    :param text: Raw macro text with ``;`` separators.
    :param writer: Telnet writer.
    :param log: Logger.
    """
    from .autoreply import _DELAY_RE  # pylint: disable=import-outside-toplevel

    parts = expand_commands(text)
    if not parts:
        return

    wait_fn = getattr(writer, "_wait_for_prompt", None)
    echo_fn = getattr(writer, "_echo_command", None)
    prompt_ready = getattr(writer, "_prompt_ready", None)

    idx = 0
    while idx < len(parts):
        cmd = parts[idx]

        # Travel command -- hand off the rest to _handle_travel_commands.
        if _TRAVEL_RE.match(cmd):
            remainder = await _handle_travel_commands(parts[idx:], writer, log)
            # remainder contains post-travel commands; continue processing.
            parts = remainder
            idx = 0
            continue

        # Delay command.
        dm = _DELAY_RE.match(cmd)
        if dm:
            value = float(dm.group(1))
            unit = dm.group(2)
            delay = value / 1000.0 if unit == "ms" else value
            if delay > 0:
                await asyncio.sleep(delay)
            idx += 1
            continue

        # Plain command -- send with pacing.
        if idx > 0:
            if prompt_ready is not None:
                prompt_ready.clear()
            if wait_fn is not None:
                await wait_fn()
        log.info("macro: sending %r", cmd)
        if echo_fn is not None:
            echo_fn(cmd)
        writer.write(cmd + "\r\n")  # type: ignore[arg-type]
        idx += 1


class BasicLineRepl:
    """
    Fallback REPL using asyncio stdin for linemode input.

    Terminal cooked mode (ICANON) provides basic line editing (backspace, ^U kill-line).  No history
    or autocomplete.

    :param telnet_writer: Writer for sending input to the server.
    :param stdin_reader: asyncio StreamReader connected to stdin.
    :param log: Logger instance.
    """

    def __init__(
        self,
        telnet_writer: Union[TelnetWriter, TelnetWriterUnicode],
        stdin_reader: asyncio.StreamReader,
        log: logging.Logger,
    ) -> None:
        """Initialize raw-mode REPL with writer, stdin reader, and logger."""
        self._writer = telnet_writer
        self._stdin = stdin_reader
        self._log = log

    async def prompt(self) -> Optional[str]:
        """
        Read one line from stdin.

        :returns: Input string, or ``None`` on EOF.
        """
        data = await self._stdin.readline()
        if not data:
            return None
        return data.decode("utf-8", errors="replace").rstrip("\n")


if sys.platform != "win32":
    import fcntl
    import struct
    import termios

    def _get_terminal_size() -> Tuple[int, int]:
        """Return ``(rows, cols)`` of the controlling terminal."""
        try:
            fmt = "hhhh"
            buf = b"\x00" * struct.calcsize(fmt)
            val = fcntl.ioctl(sys.stdin.fileno(), termios.TIOCGWINSZ, buf)
            rows, cols, _, _ = struct.unpack(fmt, val)
            return rows, cols
        except OSError:
            return (int(os.environ.get("LINES", "25")), int(os.environ.get("COLUMNS", "80")))

    class ScrollRegion:
        """
        Context manager that sets a VT100 scroll region (DECSTBM).

        Confines terminal output to the top portion, reserving
        the bottom line(s) for the REPL input.  Follows the same
        pattern as ``blessed.Terminal.scroll_region``.

        :param stdout: asyncio StreamWriter for local terminal output.
        :param rows: Total terminal height.
        :param cols: Total terminal width.
        :param reserve_bottom: Number of bottom lines to reserve.
        """

        def __init__(
            self, stdout: asyncio.StreamWriter, rows: int, cols: int, reserve_bottom: int = 1
        ) -> None:
            """Initialize scroll region with output stream and dimensions."""
            self._stdout = stdout
            self._rows = rows
            self._cols = cols
            self._reserve = reserve_bottom
            self._active = False
            self._dirty = False

        @property
        def scroll_rows(self) -> int:
            """Number of rows in the scroll region (1 row DMZ above input)."""
            return max(1, self._rows - self._reserve - 1)

        @property
        def input_row(self) -> int:
            """1-indexed row number for the first reserved (input) line."""
            return self._rows - self._reserve + 1

        @property
        def resize_pending(self) -> bool:
            """Check and clear the resize-pending flag."""
            if self._dirty:
                self._dirty = False
                return True
            return False

        def grow_reserve(self, new_reserve: int) -> None:
            """
            Increase the reserved bottom area and reapply scroll region.

            Emits newlines inside the current scroll region first so that any server text on the
            rows about to be claimed is scrolled up rather than silently overwritten (e.g. a
            password prompt arriving just as the GMCP status bar appears).
            """
            if new_reserve <= self._reserve:
                return
            extra = new_reserve - self._reserve
            old_input_row = self.input_row
            if self._active:
                # Push content up: position at bottom of scroll region
                # and emit newlines so the terminal scrolls.
                old_bottom = self.scroll_rows
                self._stdout.write(cup(old_bottom, 1).encode())
                self._stdout.write(b"\n" * extra)
            self._reserve = new_reserve
            if self._active:
                for _r in range(old_input_row, old_input_row + new_reserve):
                    self._stdout.write((cup(_r, 1) + CLEAR_LINE).encode())
                self._set_scroll_region()
                # The \n scroll shifted content up by `extra` rows but
                # the DECSC saved position (from _read_server) stayed
                # at its old absolute row — which may now be inside the
                # reserved area.  Restore it, move up to follow the
                # shifted content, then re-save.
                self._stdout.write(RESTORE_CURSOR.encode())
                if extra > 0:
                    self._stdout.write(f"\x1b[{extra}A".encode())
                self._stdout.write(SAVE_CURSOR.encode())
                for _r in range(self.input_row, self.input_row + new_reserve):
                    self._stdout.write((cup(_r, 1) + CLEAR_LINE).encode())
                self._dirty = True

        def update_size(self, rows: int, cols: int) -> None:
            """
            Update dimensions and reapply scroll region.

            No content scrolling occurs here — ``_on_resize_repaint``
            replays the buffer and saves the cursor at the correct
            position afterward.
            """
            old_input_row = self.input_row
            self._rows = rows
            self._cols = cols
            if self._active:
                for _r in range(old_input_row, old_input_row + self._reserve):
                    self._stdout.write((cup(_r, 1) + CLEAR_LINE).encode())
                self._set_scroll_region()
                self._stdout.write(SAVE_CURSOR.encode())
                for _r in range(self.input_row, self.input_row + self._reserve):
                    self._stdout.write((cup(_r, 1) + CLEAR_LINE).encode())
                self._dirty = True

        def _set_scroll_region(self) -> None:
            """Write DECSTBM escape sequence to set scroll region."""
            top = 1
            bottom = self.scroll_rows
            self._stdout.write(decstbm(top, bottom).encode())
            # Draw DMZ separator (one row below scroll region, above input).
            dmz = bottom + 1
            if dmz < self.input_row:
                self._stdout.write(
                    (cup(dmz, 1) + CLEAR_LINE + "\u2500" * self._cols).encode()
                )
            self._stdout.write(cup(bottom, 1).encode())

        def _reset_scroll_region(self) -> None:
            """Reset scroll region to full terminal height."""
            self._stdout.write(decstbm(1, self._rows).encode())

        def save_and_goto_input(self) -> None:
            """Save cursor, move to input line, clear it."""
            self._stdout.write(SAVE_CURSOR.encode())
            self._stdout.write(cup(self.input_row, 1).encode())
            self._stdout.write(CLEAR_LINE.encode())

        def restore_cursor(self) -> None:
            """Restore cursor to saved position in scroll region."""
            self._stdout.write(RESTORE_CURSOR.encode())

        def __enter__(self) -> "ScrollRegion":
            self._set_scroll_region()
            self._active = True
            return self

        def __exit__(self, *_: Any) -> None:
            self._active = False
            self._reset_scroll_region()
            self._stdout.write(cup(self._rows, 1).encode())

    import contextlib

    @contextlib.asynccontextmanager
    async def _repl_scaffold(
        telnet_writer: Union[TelnetWriter, TelnetWriterUnicode],
        term: Any,
        stdout: asyncio.StreamWriter,
        reserve_bottom: int = 1,
        on_resize: "Optional[Callable[[int, int], None]]" = None,
    ) -> "Any":
        """
        Set up NAWS patch, scroll region, and resize handler.

        Yields ``(scroll, rows_cols)`` where *rows_cols* is a mutable
        ``[rows, cols]`` list kept up-to-date by the resize handler.
        Restores the original ``handle_send_naws`` in a ``finally`` block.

        :param on_resize: Optional extra callback invoked after scroll
            region update, receiving ``(new_rows, new_cols)``.
        """
        from .telopt import NAWS  # pylint: disable=import-outside-toplevel

        rows, cols = _get_terminal_size()
        rows_cols = [rows, cols]
        scroll_region: Optional[ScrollRegion] = None

        orig_send_naws = getattr(telnet_writer, "handle_send_naws", None)

        def _adjusted_send_naws() -> Tuple[int, int]:
            # pylint: disable-next=protected-access
            if scroll_region is not None and scroll_region._active:
                _, cur_cols = _get_terminal_size()
                return (scroll_region.scroll_rows, cur_cols)
            return _get_terminal_size()

        telnet_writer.handle_send_naws = _adjusted_send_naws  # type: ignore[method-assign]

        try:
            if telnet_writer.local_option.enabled(NAWS) and not telnet_writer.is_closing():
                telnet_writer._send_naws()  # pylint: disable=protected-access

            with ScrollRegion(stdout, rows, cols, reserve_bottom=reserve_bottom) as scroll:
                scroll_region = scroll

                def _handle_resize(new_rows: int, new_cols: int) -> None:
                    rows_cols[0] = new_rows
                    rows_cols[1] = new_cols
                    scroll.update_size(new_rows, new_cols)
                    if on_resize is not None:
                        on_resize(new_rows, new_cols)

                term.on_resize = _handle_resize
                try:
                    yield scroll, rows_cols
                finally:
                    term.on_resize = None
        finally:
            if orig_send_naws is not None:
                telnet_writer.handle_send_naws = orig_send_naws  # type: ignore[method-assign]

    async def _run_repl_tasks(server_coro: "Any", input_coro: "Any") -> None:
        """Run server and input coroutines; cancel the other when one finishes."""
        server_task = asyncio.ensure_future(server_coro)
        input_task = asyncio.ensure_future(input_coro)
        _, pending = await asyncio.wait(
            [server_task, input_task], return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def repl_event_loop(
        telnet_reader: Union[TelnetReader, TelnetReaderUnicode],
        telnet_writer: Union[TelnetWriter, TelnetWriterUnicode],
        term: Any,
        stdout: asyncio.StreamWriter,
        history_file: Optional[str] = None,
    ) -> bool:
        """
        Event loop with REPL input at the bottom of the screen.

        When ``prompt_toolkit`` is available, :func:`patch_stdout` handles
        all cursor management -- server output is printed above the prompt
        automatically.  The fallback ``BasicLineRepl`` path uses a manual
        ``ScrollRegion`` instead.

        :param term: ``Terminal`` instance from ``client_shell``.
        :returns: ``True`` if the server switched to kludge mode
            (caller should fall through to the standard event loop),
            ``False`` if the connection closed normally.
        """
        if HAS_PROMPT_TOOLKIT:
            return await _repl_event_loop_pt(
                telnet_reader, telnet_writer, term, stdout, history_file=history_file
            )
        return await _repl_event_loop_basic(telnet_reader, telnet_writer, term, stdout)

    async def _repl_event_loop_pt(
        telnet_reader: Union[TelnetReader, TelnetReaderUnicode],
        telnet_writer: Union[TelnetWriter, TelnetWriterUnicode],
        term: Any,
        stdout: asyncio.StreamWriter,
        history_file: Optional[str] = None,
    ) -> bool:
        """
        REPL event loop using prompt_toolkit.

        Server output is written directly to the raw terminal (asyncio
        ``stdout``) using DECSC/DECRC to save and restore cursor position
        between the scroll region and prompt_toolkit's input line.
        prompt_toolkit redraws its input line on every keystroke, so
        brief cursor disturbance is invisible.
        """
        # pylint: disable=too-many-locals,too-many-statements,protected-access
        from .client_shell import (  # pylint: disable=import-outside-toplevel,cyclic-import
            _transform_output,
            _flush_color_filter,
        )

        mode_switched = False

        _session_key = getattr(telnet_writer, "_session_key", "")
        _is_ssl = telnet_writer.get_extra_info("ssl_object") is not None
        _conn_info = _session_key + (" SSL" if _is_ssl else "")

        replay_buf = OutputRingBuffer()
        _scroll_ref: list[Any] = [None]

        def _on_resize_repaint(_rows: int, _cols: int) -> None:
            # Clear the scroll area, replay buffered output, then
            # re-save cursor so _read_server picks up at the right spot.
            # Erase full screen and reposition to top-left.
            _reserve = (
                _scroll_ref[0]._reserve if _scroll_ref[0] is not None else _PT_RESERVE_WITH_TOOLBAR
            )
            stdout.write(CLEAR_HOME.encode() + cup(1, 1).encode())
            data = replay_buf.replay()
            if data:
                stdout.write(data)
            # Save cursor at end of replayed content.
            stdout.write(SAVE_CURSOR.encode())
            # Clear reserved input rows.
            _input_row = _rows - _reserve + 1
            for _r in range(_input_row, _rows + 1):
                stdout.write((cup(_r, 1) + CLEAR_LINE).encode())
            # Reset and redraw prompt_toolkit's input line from scratch.
            try:
                _app = prompt_toolkit.application.get_app()
                _app.renderer.reset()
                _app.invalidate()
            except RuntimeError:
                pass

        async with _repl_scaffold(
            telnet_writer,
            term,
            stdout,
            reserve_bottom=_PT_RESERVE_INITIAL,
            on_resize=_on_resize_repaint,
        ) as (scroll, _):
            _scroll_ref[0] = scroll
            # Save initial scroll-region cursor position (DECSC).
            # _read_server restores this before writing, then re-saves.
            stdout.write(SAVE_CURSOR.encode())
            stdout.write((cup(scroll.input_row, 1) + CLEAR_LINE).encode())
            # Set cursor to blinking bar for text input (DECSCUSR).
            stdout.write(CURSOR_BLINKING_BAR.encode())

            repl = PromptToolkitRepl(  # pylint: disable=possibly-used-before-assignment
                telnet_writer,
                telnet_writer.log,
                history_file=history_file,
                connection_info=_conn_info,
            )
            repl._replay_buf = replay_buf  # pylint: disable=protected-access
            repl._scroll = scroll  # pylint: disable=protected-access

            # Patch prompt_toolkit's Application._on_resize so that
            # SIGWINCH (which pt handles during prompt_async) also
            # updates our DECSTBM scroll region and replays the buffer.
            # Without this, pt overrides our SIGWINCH handler but never
            # updates the scroll region, causing screen corruption.
            _pt_app = repl._session.app  # pylint: disable=protected-access
            _orig_pt_on_resize = _pt_app._on_resize

            def _combined_on_resize() -> None:
                new_rows, new_cols = _get_terminal_size()
                scroll.update_size(new_rows, new_cols)
                _on_resize_repaint(new_rows, new_cols)
                # Send updated window size to server.
                from .telopt import NAWS  # pylint: disable=import-outside-toplevel

                try:
                    if telnet_writer.local_option.enabled(NAWS) and not telnet_writer.is_closing():
                        # pylint: disable-next=protected-access
                        telnet_writer._send_naws()
                except Exception:  # pylint: disable=broad-exception-caught
                    pass
                _orig_pt_on_resize()

            _pt_app._on_resize = _combined_on_resize

            def _insert_into_prompt(text: str) -> None:
                app = repl._session.app  # pylint: disable=protected-access
                if app and app.current_buffer:
                    app.current_buffer.insert_text(text)

            def _echo_autoreply(cmd: str) -> None:
                stdout.write(RESTORE_CURSOR.encode())
                _colored = f"{SGR_CYAN}{cmd}{SGR_RESET}\r\n"
                stdout.write(_colored.encode())
                replay_buf.append(_colored.encode())
                stdout.write(SAVE_CURSOR.encode())
                try:
                    _app = prompt_toolkit.application.get_app()
                    _cp = _app.renderer._cursor_pos
                    _row = scroll.input_row + _cp.y
                    _col = _cp.x + 1
                    stdout.write(cup(_row, _col).encode())
                    _app.invalidate()
                except RuntimeError:
                    stdout.write(cup(scroll.input_row, 1).encode())

            # EOR/GA-based command pacing: wait for server prompt signal
            # before sending each autoreply command.
            prompt_ready = asyncio.Event()
            prompt_ready.set()
            _ga_detected = False
            # Deferred on_prompt: the GA/EOR callback fires from the
            # IAC handler *before* _read_server calls feed(), so the
            # autoreply buffer is empty when on_prompt() would run.
            # Instead, the callback sets this flag and _read_server
            # calls on_prompt() after feed() so matches see the text.
            _prompt_pending = False

            def _on_prompt_signal(_cmd: bytes) -> None:
                nonlocal _ga_detected, _prompt_pending
                _ga_detected = True
                prompt_ready.set()
                _prompt_pending = True
                # Wake the reader so _read_server's read() returns even
                # when the GA/EOR arrives in a TCP segment with no visible
                # text.  Without this, _prompt_pending sits unprocessed
                # and deferred autoreply matches never fire until the next
                # user command forces fresh server output.
                telnet_reader._wakeup_waiter()  # type: ignore[union-attr]

            from .telopt import GA, CMD_EOR  # pylint: disable=import-outside-toplevel

            telnet_writer.set_iac_callback(GA, _on_prompt_signal)
            telnet_writer.set_iac_callback(CMD_EOR, _on_prompt_signal)

            async def _wait_for_prompt() -> None:
                if not _ga_detected:
                    return
                try:
                    await asyncio.wait_for(prompt_ready.wait(), timeout=2.0)
                except asyncio.TimeoutError:
                    pass
                prompt_ready.clear()

            # Stash pacing/echo functions for fast travel to use.
            telnet_writer._wait_for_prompt = _wait_for_prompt
            telnet_writer._echo_command = _echo_autoreply
            telnet_writer._prompt_ready = prompt_ready

            autoreply_engine = None
            _ar_rules_ref: object = None

            def _refresh_autoreply_engine() -> None:
                nonlocal autoreply_engine, _ar_rules_ref
                cur_rules = getattr(telnet_writer, "_autoreply_rules", None)
                if cur_rules is _ar_rules_ref:
                    return
                _ar_rules_ref = cur_rules
                prev_suppress = (
                    autoreply_engine.suppress_exclusive if autoreply_engine is not None else False
                )
                if autoreply_engine is not None:
                    autoreply_engine.cancel()
                    autoreply_engine = None
                if cur_rules:
                    from .autoreply import (  # pylint: disable=import-outside-toplevel
                        AutoreplyEngine,
                    )

                    autoreply_engine = AutoreplyEngine(
                        cur_rules,
                        telnet_writer,
                        telnet_writer.log,
                        insert_fn=_insert_into_prompt,
                        echo_fn=_echo_autoreply,
                        wait_fn=_wait_for_prompt,
                    )
                    autoreply_engine.suppress_exclusive = prev_suppress
                repl._autoreply_engine = autoreply_engine
                telnet_writer._autoreply_engine = autoreply_engine

            _refresh_autoreply_engine()

            server_done = False

            async def _read_server() -> None:
                nonlocal server_done, mode_switched, _prompt_pending
                _esc_hold = b""
                while not server_done:
                    out = await telnet_reader.read(2**24)
                    if not out:
                        if telnet_reader.at_eof():
                            server_done = True
                            if _esc_hold:
                                stdout.write(RESTORE_CURSOR.encode())
                                stdout.write(_esc_hold)
                                replay_buf.append(_esc_hold)
                                stdout.write(SAVE_CURSOR.encode())
                            _flush_color_filter(telnet_writer, stdout)
                            stdout.write(RESTORE_CURSOR.encode())
                            stdout.write(b"\r\nConnection closed by foreign host.\r\n")
                            return
                        # GA/EOR without visible text (e.g. bare GMCP
                        # update).  Fire deferred on_prompt if pending.
                        if _prompt_pending and autoreply_engine is not None:
                            _prompt_pending = False
                            autoreply_engine.on_prompt()
                        continue
                    if isinstance(out, bytes):
                        out = out.decode("utf-8", errors="replace")
                    out = _transform_output(out, telnet_writer, True)
                    _refresh_autoreply_engine()
                    if autoreply_engine is not None:
                        autoreply_engine.feed(out)
                        # Fire deferred on_prompt *after* feed() so the
                        # autoreply buffer contains the text that arrived
                        # alongside GA/EOR.  Without this, on_prompt()
                        # fires from the IAC callback before feed() and
                        # matches an empty buffer — room content like
                        # "Snake" would not trigger until the *next*
                        # GA/EOR, by which time autowander has moved on.
                        if _prompt_pending:
                            _prompt_pending = False
                            autoreply_engine.on_prompt()
                    if _editor_active:
                        _editor_buffer.append(out.encode())
                        continue
                    stdout.write(RESTORE_CURSOR.encode())
                    if _editor_buffer:
                        for chunk in _editor_buffer:
                            stdout.write(chunk)
                            replay_buf.append(chunk)
                        _editor_buffer.clear()
                    encoded = _esc_hold + out.encode()
                    encoded, _esc_hold = _split_incomplete_esc(encoded)
                    if encoded:
                        stdout.write(encoded)
                        replay_buf.append(encoded)
                    stdout.write(SAVE_CURSOR.encode())
                    # Reposition cursor where prompt_toolkit expects it,
                    # so delta rendering and user keystrokes land at the
                    # correct column.  Fall back to column 1 if no app.
                    try:
                        _app = prompt_toolkit.application.get_app()
                        _cp = _app.renderer._cursor_pos
                        _row = scroll.input_row + _cp.y
                        _col = _cp.x + 1
                        stdout.write(cup(_row, _col).encode())
                        _app.invalidate()
                    except RuntimeError:
                        stdout.write(cup(scroll.input_row, 1).encode())
                    if telnet_writer.mode == "kludge":
                        mode_switched = True
                        server_done = True
                        return

            async def _read_input() -> None:
                nonlocal server_done
                while not server_done:
                    stdout.write((cup(scroll.input_row, 1) + CLEAR_LINE).encode())
                    # Reset PT renderer before each new prompt so it does
                    # a full redraw of the input line and toolbar after we
                    # cleared the row above.
                    try:
                        prompt_toolkit.application.get_app().renderer.reset()
                    except RuntimeError:
                        pass
                    line = await repl.prompt()
                    if line is None:
                        server_done = True
                        telnet_writer.close()
                        return
                    # Re-evaluate terminal size after prompt returns --
                    # prompt_toolkit handles SIGWINCH during prompt_async
                    # but our scroll region may not have been updated if
                    # the combined handler wasn't active yet.
                    _cur_rows, _cur_cols = _get_terminal_size()
                    if (
                        _cur_rows != scroll._rows  # pylint: disable=protected-access
                        or _cur_cols != scroll._cols
                    ):
                        scroll.update_size(_cur_rows, _cur_cols)
                        _on_resize_repaint(_cur_rows, _cur_cols)
                    # EOR/GA pacing: wait for server prompt before sending.
                    if _ga_detected:
                        try:
                            await asyncio.wait_for(prompt_ready.wait(), timeout=2.0)
                        except asyncio.TimeoutError:
                            pass
                    # pylint: disable-next=protected-access
                    is_pw = repl._is_password_mode()
                    echo_text = "*" * len(line) if is_pw else line
                    stdout.write(RESTORE_CURSOR.encode())
                    _colored = f"{SGR_YELLOW}{echo_text}{SGR_RESET}\r\n"
                    stdout.write(_colored.encode())
                    replay_buf.append(_colored.encode())
                    stdout.write(SAVE_CURSOR.encode())
                    stdout.write(cup(scroll.input_row, 1).encode())
                    if autoreply_engine is not None:
                        autoreply_engine.cancel()
                    _wander_task = getattr(telnet_writer, "_wander_task", None)
                    if _wander_task is not None and not _wander_task.done():
                        _wander_task.cancel()
                    _discover_task = getattr(telnet_writer, "_discover_task", None)
                    if _discover_task is not None and not _discover_task.done():
                        _discover_task.cancel()
                    parts = expand_commands(line)
                    if parts and _TRAVEL_RE.match(parts[0]):
                        remainder = await _handle_travel_commands(
                            parts, telnet_writer, telnet_writer.log
                        )
                        if remainder:
                            telnet_writer.write(remainder[0] + "\r\n")  # type: ignore[arg-type]
                            if _ga_detected:
                                prompt_ready.clear()
                            if len(remainder) > 1:
                                await _send_chained(remainder, telnet_writer, telnet_writer.log)
                    elif parts:
                        telnet_writer.write(parts[0] + "\r\n")  # type: ignore[arg-type]
                        # Clear so next iteration waits for fresh GA/EOR.
                        if _ga_detected:
                            prompt_ready.clear()
                        if len(parts) > 1:
                            await _send_chained(parts, telnet_writer, telnet_writer.log)
                    else:
                        telnet_writer.write("\r\n")  # type: ignore[arg-type]

            try:
                await _run_repl_tasks(_read_server(), _read_input())
            finally:
                if autoreply_engine is not None:
                    autoreply_engine.cancel()
                # Reset cursor shape to terminal default (DECSCUSR).
                stdout.write(CURSOR_DEFAULT.encode())
                if mode_switched:
                    # Erase the DMZ separator bar and input/toolbar rows
                    # that prompt_toolkit briefly drew before the server
                    # switched to kludge mode.
                    _dmz_row = scroll.scroll_rows + 1
                    stdout.write(SAVE_CURSOR.encode())
                    stdout.write(cup(_dmz_row, 1).encode())
                    stdout.write(SGR_RESET.encode())
                    stdout.write(b"\x1b[J")  # ED0: clear to end of screen
                    stdout.write(RESTORE_CURSOR.encode())

        return mode_switched

    async def _repl_event_loop_basic(  # pylint: disable=too-many-statements,protected-access
        telnet_reader: Union[TelnetReader, TelnetReaderUnicode],
        telnet_writer: Union[TelnetWriter, TelnetWriterUnicode],
        term: Any,
        stdout: asyncio.StreamWriter,
    ) -> bool:
        """Fallback REPL event loop using ScrollRegion and BasicLineRepl."""
        from .client_shell import (  # pylint: disable=import-outside-toplevel,cyclic-import
            _transform_output,
            _flush_color_filter,
        )

        mode_switched = False

        async with _repl_scaffold(telnet_writer, term, stdout) as (scroll, _):
            stdout.write((cup(scroll.input_row, 1) + CLEAR_LINE).encode())

            basic_stdin = await term.connect_stdin()
            repl: BasicLineRepl = BasicLineRepl(telnet_writer, basic_stdin, telnet_writer.log)

            def _echo_autoreply_b(cmd: str) -> None:
                scroll.save_and_goto_input()
                scroll.restore_cursor()
                stdout.write(f"{SGR_CYAN}{cmd}{SGR_RESET}\r\n".encode())

            prompt_ready_b = asyncio.Event()
            prompt_ready_b.set()
            _ga_detected_b = False
            _prompt_pending_b = False

            def _on_prompt_signal_b(_cmd: bytes) -> None:
                nonlocal _ga_detected_b, _prompt_pending_b
                _ga_detected_b = True
                prompt_ready_b.set()
                _prompt_pending_b = True
                telnet_reader._wakeup_waiter()  # type: ignore[union-attr]

            from .telopt import GA, CMD_EOR  # pylint: disable=import-outside-toplevel

            telnet_writer.set_iac_callback(GA, _on_prompt_signal_b)
            telnet_writer.set_iac_callback(CMD_EOR, _on_prompt_signal_b)

            async def _wait_for_prompt_b() -> None:
                if not _ga_detected_b:
                    return
                try:
                    await asyncio.wait_for(prompt_ready_b.wait(), timeout=2.0)
                except asyncio.TimeoutError:
                    pass
                prompt_ready_b.clear()

            telnet_writer._wait_for_prompt = _wait_for_prompt_b
            telnet_writer._echo_command = _echo_autoreply_b
            telnet_writer._prompt_ready = prompt_ready_b

            _ar_rules_b = getattr(telnet_writer, "_autoreply_rules", None)
            autoreply_engine_b = None
            if _ar_rules_b:
                from .autoreply import AutoreplyEngine  # pylint: disable=import-outside-toplevel

                autoreply_engine_b = AutoreplyEngine(
                    _ar_rules_b,
                    telnet_writer,
                    telnet_writer.log,
                    echo_fn=_echo_autoreply_b,
                    wait_fn=_wait_for_prompt_b,
                )
                telnet_writer._autoreply_engine = autoreply_engine_b

            server_done = False

            async def _read_server() -> None:
                nonlocal server_done, mode_switched, _prompt_pending_b
                _esc_hold = b""
                while not server_done:
                    out = await telnet_reader.read(2**24)
                    if not out:
                        if telnet_reader.at_eof():
                            server_done = True
                            if _esc_hold:
                                stdout.write(_esc_hold)
                            _flush_color_filter(telnet_writer, stdout)
                            stdout.write(b"\r\nConnection closed by foreign host.\r\n")
                            return
                        if _prompt_pending_b and autoreply_engine_b is not None:
                            _prompt_pending_b = False
                            autoreply_engine_b.on_prompt()
                        continue
                    if isinstance(out, bytes):
                        out = out.decode("utf-8", errors="replace")
                    out = _transform_output(out, telnet_writer, False)
                    if autoreply_engine_b is not None:
                        autoreply_engine_b.feed(out)
                        if _prompt_pending_b:
                            _prompt_pending_b = False
                            autoreply_engine_b.on_prompt()
                    scroll.save_and_goto_input()
                    scroll.restore_cursor()
                    encoded = _esc_hold + out.encode()
                    encoded, _esc_hold = _split_incomplete_esc(encoded)
                    if encoded:
                        stdout.write(encoded)
                    if telnet_writer.mode == "kludge":
                        mode_switched = True
                        server_done = True
                        return

            async def _read_input() -> None:
                nonlocal server_done
                while not server_done:
                    stdout.write((cup(scroll.input_row, 1) + CLEAR_LINE).encode())
                    line = await repl.prompt()
                    if line is None:
                        server_done = True
                        telnet_writer.close()
                        return
                    if _ga_detected_b:
                        try:
                            await asyncio.wait_for(prompt_ready_b.wait(), timeout=2.0)
                        except asyncio.TimeoutError:
                            pass
                    echo_text = "*" * len(line) if telnet_writer.will_echo else line
                    scroll.save_and_goto_input()
                    scroll.restore_cursor()
                    stdout.write(f"{SGR_YELLOW}{echo_text}{SGR_RESET}\r\n".encode())
                    if autoreply_engine_b is not None:
                        autoreply_engine_b.cancel()
                    parts = expand_commands(line)
                    if parts and _TRAVEL_RE.match(parts[0]):
                        remainder = await _handle_travel_commands(
                            parts, telnet_writer, telnet_writer.log
                        )
                        if remainder:
                            telnet_writer.write(remainder[0] + "\r\n")  # type: ignore[arg-type]
                            if _ga_detected_b:
                                prompt_ready_b.clear()
                            if len(remainder) > 1:
                                await _send_chained(remainder, telnet_writer, telnet_writer.log)
                    elif parts:
                        telnet_writer.write(parts[0] + "\r\n")  # type: ignore[arg-type]
                        if _ga_detected_b:
                            prompt_ready_b.clear()
                        if len(parts) > 1:
                            await _send_chained(parts, telnet_writer, telnet_writer.log)
                    else:
                        telnet_writer.write("\r\n")  # type: ignore[arg-type]

            try:
                await _run_repl_tasks(_read_server(), _read_input())
            finally:
                if autoreply_engine_b is not None:
                    autoreply_engine_b.cancel()
                if mode_switched:
                    _dmz_row = scroll.scroll_rows + 1
                    stdout.write(SAVE_CURSOR.encode())
                    stdout.write(cup(_dmz_row, 1).encode())
                    stdout.write(SGR_RESET.encode())
                    stdout.write(b"\x1b[J")
                    stdout.write(RESTORE_CURSOR.encode())

        return mode_switched
