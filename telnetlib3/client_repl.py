"""REPL and TUI components for linemode telnet client sessions."""

# pylint: disable=too-complex

# std imports
import sys
import time
import asyncio
import collections
import logging
from typing import Any, List, Tuple, Union, Callable, Optional

# local
from .stream_reader import TelnetReader, TelnetReaderUnicode
from .stream_writer import TelnetWriter, TelnetWriterUnicode

try:
    import prompt_toolkit
    import prompt_toolkit.application
    import prompt_toolkit.filters
    import prompt_toolkit.history
    import prompt_toolkit.key_binding
    import prompt_toolkit.auto_suggest
    import prompt_toolkit.styles

    HAS_PROMPT_TOOLKIT = True
except ImportError:
    HAS_PROMPT_TOOLKIT = False

PASSWORD_CHAR = "\u25cf"

# Number of bottom rows reserved for prompt_toolkit (input + toolbar).
_PT_RESERVE_BOTTOM = 2

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
)


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


def _hsv_to_rgb(h: float, s: float, v: float) -> Tuple[int, int, int]:
    """Convert HSV (h in [0,360), s/v in [0,1]) to (r, g, b) in [0,255]."""
    import colorsys  # pylint: disable=import-outside-toplevel

    r, g, b = colorsys.hsv_to_rgb(h / 360.0, s, v)
    return (int(r * 255), int(g * 255), int(b * 255))


def _vital_color(fraction: float, kind: str) -> str:
    """Return an RGB hex color for a vitals bar.

    :param fraction: 0.0 (empty) to 1.0 (full).
    :param kind: ``"hp"`` for red-to-green, ``"mp"`` for golden-yellow-to-blue.
    """
    fraction = max(0.0, min(1.0, fraction))
    if kind == "hp":
        # Stay red below 33%, then red→green over 33%–100%.
        hue = max(0.0, (fraction - 0.33) / 0.67) * 120.0
    else:
        # Stay golden yellow below 33%, then golden-yellow→blue over 33%–100%.
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
    """Build a labelled progress-bar with overlaid text.

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
        empty_style = "fg:#444444 bg:#1a1a1a"

    if mx > 0:
        label = f"{cur}/{mx} {pct}%"
    else:
        label = str(cur)

    # Center the label inside the bar width.
    # Filled portion uses spaces as background; empty portion uses ░
    # for visual texture, but preserves the label text itself.
    lpad = max(0, (width - len(label)) // 2)

    bg = list(" " * filled + "\u2591" * (width - filled))
    for i, ch in enumerate(label, start=lpad):
        if i < width:
            bg[i] = ch
    bar_text = "".join(bg[:width])

    filled_text = bar_text[:filled]
    empty_text = bar_text[filled:]

    prefix = "HP" if kind == "hp" else "MP"
    return [
        ("fg:#cccccc", prefix),
        ("fg:black", ":"),
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

    class _FilteredFileHistory(prompt_toolkit.history.FileHistory):  # type: ignore[misc]
        """
        FileHistory subclass that skips storing password inputs.

        :attr:`is_password` is a callable checked at store time — when
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

            @kb.add("c-]")
            def _escape_quit(event: Any) -> None:
                """Ctrl+] closes the connection, matching classic telnet."""
                event.app.exit(exception=EOFError)

            @kb.add("c-l")
            def _clear_repaint(event: Any) -> None:
                """Ctrl+L clears screen and replays recent output."""
                _repaint_screen(event, self._replay_buf)

            self._macro_defs = getattr(telnet_writer, "_macro_defs", None)
            if self._macro_defs is not None:
                from .macros import bind_macros  # pylint: disable=import-outside-toplevel

                bind_macros(kb, self._macro_defs, telnet_writer, log)

            @kb.add("f1")
            def _help_screen(event: Any) -> None:
                """F1 shows keybinding help on the alternate screen."""
                _show_help(event, self._macro_defs)

            @kb.add("f8")
            def _edit_macros(event: Any) -> None:
                """F8 opens macro editor TUI in subprocess."""
                _launch_tui_editor(event, "macros", telnet_writer, self._replay_buf)

            @kb.add("f9")
            def _edit_autoreplies(event: Any) -> None:
                """F9 opens autoreply editor TUI in subprocess."""
                _launch_tui_editor(event, "autoreplies", telnet_writer, self._replay_buf)

            self._rprompt_text = "F1 Help"
            self._autoreply_engine: Any = None
            self._last_hp: Optional[int] = None
            self._last_mp: Optional[int] = None
            self._hp_flash: float = 0.0
            self._mp_flash: float = 0.0

            self._style_normal = prompt_toolkit.styles.Style.from_dict({
                "": "fg:#cccccc bg:#2a0000",
                "bottom-toolbar": "noreverse",
                "bottom-toolbar.text": "",
                "rprompt-info": "fg:black",
                "rprompt": "fg:black bg:#2a0000",
                "auto-suggest": "fg:#666666",
            })
            self._style_autoreply = prompt_toolkit.styles.Style.from_dict({
                "": "fg:#000000 bg:#b8860b",
                "bottom-toolbar": "noreverse bg:#b8860b",
                "bottom-toolbar.text": "fg:#000000 bg:#b8860b",
                "rprompt-info": "fg:#000000 bg:#b8860b",
                "rprompt": "fg:#000000 bg:#b8860b",
                "rprompt-autoreply": "fg:#000000 bg:#b8860b bold",
                "auto-suggest": "fg:#666666",
            })
            self._style = self._style_normal
            _color_depth = None
            if os.environ.get("COLORTERM") in ("truecolor", "24bit"):
                from prompt_toolkit.output.color_depth import ColorDepth  # pylint: disable=import-outside-toplevel

                _color_depth = ColorDepth.TRUE_COLOR
            self._session: "prompt_toolkit.PromptSession[str]" = prompt_toolkit.PromptSession(
                history=self._history,
                auto_suggest=prompt_toolkit.auto_suggest.AutoSuggestFromHistory(),
                enable_history_search=True,
                key_bindings=kb,
                style=self._style,
                bottom_toolbar=self._get_toolbar,  # type: ignore[arg-type]
                color_depth=_color_depth,
                erase_when_done=True,
            )

        def _get_toolbar(self) -> "List[Tuple[str, str]]":
            """Return toolbar as formatted text tuples with GMCP and rprompt."""
            engine = self._autoreply_engine
            ar_active = (
                engine is not None
                and engine.exclusive_active
            )
            if ar_active:
                self._session.style = self._style_autoreply
            else:
                self._session.style = self._style_normal

            gmcp_data: "Optional[dict[str, Any]]" = getattr(
                self._writer, "_gmcp_data", None
            )

            bars: "List[Tuple[str, str]]" = []
            room_name = ""
            now = time.monotonic()
            if gmcp_data:
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
                        bars.extend(
                            _vital_bar(hp, maxhp, _BAR_WIDTH, "hp", flash=hp_flashing)
                        )
                    mp = vitals.get("mp", vitals.get("MP", vitals.get(
                        "mana", vitals.get("sp", vitals.get("SP")))))
                    maxmp = vitals.get("maxmp", vitals.get("maxMP", vitals.get(
                        "max_mp", vitals.get("maxsp", vitals.get("maxSP")))))
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
                        bars.extend(
                            _vital_bar(mp, maxmp, _BAR_WIDTH, "mp", flash=mp_flashing)
                        )

                room_info = gmcp_data.get("Room.Info", gmcp_data.get("Room.Name"))
                if isinstance(room_info, dict):
                    room_name = str(room_info.get("name", room_info.get("Name", "")))
                elif isinstance(room_info, str):
                    room_name = room_info

            if room_name:
                self._rprompt_text = room_name

            if ar_active:
                ar_label = f"Autoreply #{engine.exclusive_rule_index}"
                right_text = " " + ar_label
                rprompt_class = "class:rprompt-autoreply"
            else:
                right_text = " " + self._rprompt_text
                rprompt_class = "class:rprompt-info"
            right_width = len(right_text)

            cols = prompt_toolkit.application.get_app().output.get_size().columns

            bars_width = sum(
                _wcswidth(t) if t else 0 for _, t in bars
            )

            if bars:
                pad = max(1, cols - bars_width - right_width)
                result: "List[Tuple[str, str]]" = list(bars)
                result.append(("", " " * pad))
                result.append((rprompt_class, right_text))
                return result
            pad = max(1, cols - right_width)
            return [
                ("", " " * pad),
                (rprompt_class, right_text),
            ]

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
            re-evaluates it on every render — if the server toggles
            ``WILL ECHO`` while the prompt is already waiting, the
            display switches to masked input immediately.

            :returns: Input string, or ``None`` on EOF.
            """
            try:
                result: str = await self._session.prompt_async(
                    "",
                    is_password=prompt_toolkit.filters.Condition(self._is_password_mode),
                    rprompt=[("class:rprompt", " F1 Help ")],
                )
                return result
            except EOFError:
                return None
            except KeyboardInterrupt:
                return None


def _repaint_screen(_event: Any, replay_buf: Optional[OutputRingBuffer]) -> None:
    """
    Clear screen and replay recent output from the ring buffer.

    Re-establishes the DECSTBM scroll region and replays buffered
    output so recent MUD text reappears with colors intact.
    """
    import os as _os  # pylint: disable=import-outside-toplevel,redefined-outer-name

    def _run_repaint() -> None:
        try:
            _tsize = _os.get_terminal_size()
        except OSError:
            return
        fd = sys.stdout.fileno()
        was_blocking = _os.get_blocking(fd)
        _os.set_blocking(fd, True)
        try:
            scroll_bottom = max(1, _tsize.lines - _PT_RESERVE_BOTTOM)
            sys.stdout.write("\x1b[2J\x1b[H")
            sys.stdout.write(f"\x1b[1;{scroll_bottom}r")
            sys.stdout.write("\x1b[1;1H")
            if replay_buf is not None:
                data = replay_buf.replay()
                if data:
                    sys.stdout.write(data.decode("utf-8", errors="replace"))
            sys.stdout.write("\x1b7")
            _input_row = _tsize.lines - _PT_RESERVE_BOTTOM + 1
            for _r in range(_input_row, _tsize.lines + 1):
                sys.stdout.write(f"\x1b[{_r};1H\x1b[2K")
            sys.stdout.flush()
        finally:
            _os.set_blocking(fd, was_blocking)

    from prompt_toolkit.application import (  # pylint: disable=import-error,import-outside-toplevel
        get_app,
        run_in_terminal,
    )

    run_in_terminal(_run_repaint)
    app = get_app()
    app.renderer.reset()
    app.invalidate()


def _show_help(_event: Any, macro_defs: "Any" = None) -> None:
    """
    Display keybinding help on the alternate screen buffer.

    :param _event: prompt_toolkit key event (unused).
    :param macro_defs: Optional list of macro definitions to display.
    """
    import os  # pylint: disable=import-outside-toplevel,redefined-outer-name

    def _run_help() -> None:
        sys.stdout.write("\x1b[?1049h")
        sys.stdout.write("\x1b[H\x1b[2J")
        lines = [
            "",
            "  telnetlib3 \u2014 Keybindings",
            "",
            "  F1          This help screen",
            "  F8          Edit macros (TUI editor)",
            "  F9          Edit autoreplies (TUI editor)",
            "  Ctrl+]      Disconnect",
            "",
        ]
        if macro_defs:
            lines.append("  User macros:")
            for m in macro_defs:
                key = m.get("key", "?")
                text = m.get("text", "")
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

        sys.stdout.write("\x1b[?1049l")
        sys.stdout.flush()

        try:
            _tsize = os.get_terminal_size()
            scroll_bottom = max(1, _tsize.lines - _PT_RESERVE_BOTTOM)
            sys.stdout.write(f"\x1b[1;{scroll_bottom}r")
            sys.stdout.write(f"\x1b[{scroll_bottom};1H")
            sys.stdout.write("\x1b7")
            sys.stdout.flush()
        except OSError:
            pass

    from prompt_toolkit.application import (  # pylint: disable=import-error,import-outside-toplevel
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
    import os  # pylint: disable=import-outside-toplevel,redefined-outer-name
    import subprocess  # pylint: disable=import-outside-toplevel

    _xdg = os.environ.get("XDG_CONFIG_HOME", os.path.join(os.path.expanduser("~"), ".config"))
    _config_dir = os.path.join(_xdg, "telnetlib3")

    _session_key = getattr(writer, "_session_key", "")

    if editor_type == "macros":
        path = getattr(writer, "_macros_file", None) or os.path.join(_config_dir, "macros.json")
        entry = "edit_macros_main"
    else:
        path = getattr(writer, "_autoreplies_file", None) or os.path.join(
            _config_dir, "autoreplies.json"
        )
        entry = "edit_autoreplies_main"

    cmd = [
        sys.executable,
        "-c",
        f"from telnetlib3.client_tui import {entry}; " f"{entry}({path!r}, {_session_key!r})",
    ]

    log = logging.getLogger(__name__)

    def _run_editor() -> None:
        global _editor_active  # noqa: PLW0603  # pylint: disable=global-statement
        # Reset DECSTBM scroll region before launching TUI subprocess —
        # the Textual app uses the alternate screen buffer, but some
        # terminals share scroll region state across buffers, causing a
        # "doubling" effect where widgets render at the wrong row.
        sys.stdout.write("\x1b[r")
        sys.stdout.flush()
        # Flush and drain stderr — Textual writes all output to
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
            # Restore stdin blocking mode — the Textual subprocess shares
            # the kernel file description and may have set O_NONBLOCK.
            try:
                os.set_blocking(sys.stdin.fileno(), True)
            except OSError:
                pass
            # Reset terminal state the Textual subprocess may have left
            # behind (SGR attributes, mouse tracking, alternate screen).
            sys.stdout.write(
                "\x1b[m"  # reset SGR attributes
                "\x1b[?25h"  # show cursor
                "\x1b[?1049l"  # exit alternate screen
                "\x1b[?1000l"  # disable mouse tracking (basic)
                "\x1b[?1002l"  # disable button-event tracking
                "\x1b[?1003l"  # disable all-motion tracking
                "\x1b[?1006l"  # disable SGR mouse format
                "\x1b[?2004l"  # disable bracketed paste
            )
            # Full screen clear + scroll region restore + replay, same
            # as Ctrl+L, so the MUD output and prompt are fully intact.
            try:
                _tsize = os.get_terminal_size()
            except OSError:
                _tsize = os.terminal_size((80, 24))
            scroll_bottom = max(1, _tsize.lines - _PT_RESERVE_BOTTOM)
            sys.stdout.write("\x1b[2J\x1b[H")
            sys.stdout.write(f"\x1b[1;{scroll_bottom}r")
            sys.stdout.write("\x1b[1;1H")
            if replay_buf is not None:
                data = replay_buf.replay()
                if data:
                    sys.stdout.write(data.decode("utf-8", errors="replace"))
            sys.stdout.write("\x1b7")
            _input_row = _tsize.lines - _PT_RESERVE_BOTTOM + 1
            for _r in range(_input_row, _tsize.lines + 1):
                sys.stdout.write(f"\x1b[{_r};1H\x1b[2K")
            sys.stdout.flush()

        if editor_type == "macros":
            _reload_macros(writer, path, _session_key, log)
        else:
            _reload_autoreplies(writer, path, _session_key, log)

    from prompt_toolkit.application import (  # pylint: disable=import-error,import-outside-toplevel
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
    """Reload macro definitions from disk after editing."""
    import os  # pylint: disable=import-outside-toplevel,redefined-outer-name

    if not os.path.exists(path):
        return
    from .macros import load_macros  # pylint: disable=import-outside-toplevel

    try:
        # pylint: disable=protected-access
        writer._macro_defs = load_macros(path, session_key)  # type: ignore[union-attr]
        writer._macros_file = path  # type: ignore[union-attr]
        n_macros = len(writer._macro_defs)  # type: ignore[union-attr]
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
    import os  # pylint: disable=import-outside-toplevel,redefined-outer-name

    if not os.path.exists(path):
        return
    from .autoreply import load_autoreplies  # pylint: disable=import-outside-toplevel

    try:
        # pylint: disable=protected-access
        writer._autoreply_rules = load_autoreplies(path, session_key)  # type: ignore[union-attr]
        writer._autoreplies_file = path  # type: ignore[union-attr]
        n_rules = len(writer._autoreply_rules)  # type: ignore[union-attr]
        # pylint: enable=protected-access
        log.info("reloaded %d autoreplies from %s", n_rules, path)
    except ValueError as exc:
        log.warning("failed to reload autoreplies: %s", exc)


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
    import os
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
            """Number of rows in the scroll region."""
            return max(1, self._rows - self._reserve)

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

        def update_size(self, rows: int, cols: int) -> None:
            """Update dimensions and reapply scroll region."""
            old_input_row = self.input_row
            self._rows = rows
            self._cols = cols
            if self._active:
                for _r in range(old_input_row, old_input_row + self._reserve):
                    self._stdout.write(f"\x1b[{_r};1H\x1b[2K".encode())
                self._set_scroll_region()
                self._stdout.write(b"\x1b7")
                for _r in range(self.input_row, self.input_row + self._reserve):
                    self._stdout.write(f"\x1b[{_r};1H\x1b[2K".encode())
                self._dirty = True

        def _set_scroll_region(self) -> None:
            """Write DECSTBM escape sequence to set scroll region."""
            top = 1
            bottom = self.scroll_rows
            self._stdout.write(f"\x1b[{top};{bottom}r".encode())
            self._stdout.write(f"\x1b[{bottom};1H".encode())

        def _reset_scroll_region(self) -> None:
            """Reset scroll region to full terminal height."""
            self._stdout.write(f"\x1b[1;{self._rows}r".encode())

        def save_and_goto_input(self) -> None:
            """Save cursor, move to input line, clear it."""
            self._stdout.write(b"\x1b7")
            self._stdout.write(f"\x1b[{self.input_row};1H".encode())
            self._stdout.write(b"\x1b[2K")

        def restore_cursor(self) -> None:
            """Restore cursor to saved position in scroll region."""
            self._stdout.write(b"\x1b8")

        def __enter__(self) -> "ScrollRegion":
            self._set_scroll_region()
            self._active = True
            return self

        def __exit__(self, *_: Any) -> None:
            self._active = False
            self._reset_scroll_region()
            self._stdout.write(f"\x1b[{self._rows};1H".encode())

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
        all cursor management — server output is printed above the prompt
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
        from .client_shell import (  # pylint: disable=import-outside-toplevel,cyclic-import
            _transform_output,
            _flush_color_filter,
        )

        mode_switched = False

        _session_key = getattr(telnet_writer, "_session_key", "")
        _is_ssl = telnet_writer.get_extra_info("ssl_object") is not None
        _conn_info = _session_key + (" SSL" if _is_ssl else "")

        replay_buf = OutputRingBuffer()

        def _on_resize_repaint(_rows: int, _cols: int) -> None:
            # Clear the scroll area, replay buffered output, then
            # re-save cursor so _read_server picks up at the right spot.
            # Erase full screen and reposition to top-left.
            stdout.write(b"\x1b[2J\x1b[1;1H")
            data = replay_buf.replay()
            if data:
                stdout.write(data)
            # Save cursor at end of replayed content.
            stdout.write(b"\x1b7")
            # Clear reserved input rows.
            _input_row = _rows - _PT_RESERVE_BOTTOM + 1
            for _r in range(_input_row, _rows + 1):
                stdout.write(f"\x1b[{_r};1H\x1b[2K".encode())
            # Reset and redraw prompt_toolkit's input line from scratch.
            try:
                _app = prompt_toolkit.application.get_app()
                _app.renderer.reset()
                _app.invalidate()
            except RuntimeError:
                pass

        async with _repl_scaffold(
            telnet_writer, term, stdout,
            reserve_bottom=_PT_RESERVE_BOTTOM,
            on_resize=_on_resize_repaint,
        ) as (scroll, _):
            # Save initial scroll-region cursor position (DECSC).
            # _read_server restores this before writing, then re-saves.
            stdout.write(b"\x1b7")
            stdout.write(f"\x1b[{scroll.input_row};1H\x1b[2K".encode())
            # Set cursor to blinking bar for text input (DECSCUSR).
            stdout.write(b"\x1b[5 q")

            repl = PromptToolkitRepl(  # pylint: disable=possibly-used-before-assignment
                telnet_writer,
                telnet_writer.log,
                history_file=history_file,
                connection_info=_conn_info,
            )
            repl._replay_buf = replay_buf  # pylint: disable=protected-access

            def _insert_into_prompt(text: str) -> None:
                app = repl._session.app  # pylint: disable=protected-access
                if app and app.current_buffer:
                    app.current_buffer.insert_text(text)

            def _echo_autoreply(cmd: str) -> None:
                stdout.write(b"\x1b8")
                stdout.write(f"\x1b[36m{cmd}\x1b[m\r\n".encode())
                replay_buf.append(f"\x1b[36m{cmd}\x1b[m\r\n".encode())
                stdout.write(b"\x1b7")
                try:
                    _app = prompt_toolkit.application.get_app()
                    _cp = _app.renderer._cursor_pos
                    _row = scroll.input_row + _cp.y
                    _col = _cp.x + 1
                    stdout.write(f"\x1b[{_row};{_col}H".encode())
                    _app.invalidate()
                except RuntimeError:
                    stdout.write(
                        f"\x1b[{scroll.input_row};1H".encode()
                    )

            # EOR/GA-based command pacing: wait for server prompt signal
            # before sending each autoreply command.
            prompt_ready = asyncio.Event()
            prompt_ready.set()
            _ga_detected = False

            def _on_prompt_signal(_cmd: bytes) -> None:
                nonlocal _ga_detected
                _ga_detected = True
                prompt_ready.set()
                if autoreply_engine is not None:
                    autoreply_engine.on_prompt()

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

            autoreply_engine = None
            _ar_rules_ref: object = None

            def _refresh_autoreply_engine() -> None:
                nonlocal autoreply_engine, _ar_rules_ref
                cur_rules = getattr(telnet_writer, "_autoreply_rules", None)
                if cur_rules is _ar_rules_ref:
                    return
                _ar_rules_ref = cur_rules
                if autoreply_engine is not None:
                    autoreply_engine.cancel()
                    autoreply_engine = None
                if cur_rules:
                    from .autoreply import AutoreplyEngine  # pylint: disable=import-outside-toplevel

                    autoreply_engine = AutoreplyEngine(
                        cur_rules,
                        telnet_writer,
                        telnet_writer.log,
                        insert_fn=_insert_into_prompt,
                        echo_fn=_echo_autoreply,
                        wait_fn=_wait_for_prompt,
                    )
                repl._autoreply_engine = autoreply_engine

            _refresh_autoreply_engine()

            server_done = False

            async def _read_server() -> None:
                nonlocal server_done, mode_switched
                while not server_done:
                    out = await telnet_reader.read(2**24)
                    if not out:
                        if telnet_reader.at_eof():
                            server_done = True
                            _flush_color_filter(telnet_writer, stdout)
                            stdout.write(b"\x1b8")
                            stdout.write(b"\r\nConnection closed by foreign host.\r\n")
                            return
                        continue
                    if isinstance(out, bytes):
                        out = out.decode("utf-8", errors="replace")
                    out = _transform_output(out, telnet_writer, True)
                    _refresh_autoreply_engine()
                    if autoreply_engine is not None:
                        autoreply_engine.feed(out)
                    if _editor_active:
                        _editor_buffer.append(out.encode())
                        continue
                    stdout.write(b"\x1b8")
                    if _editor_buffer:
                        for chunk in _editor_buffer:
                            stdout.write(chunk)
                            replay_buf.append(chunk)
                        _editor_buffer.clear()
                    encoded = out.encode()
                    stdout.write(encoded)
                    replay_buf.append(encoded)
                    stdout.write(b"\x1b7")
                    # Reposition cursor where prompt_toolkit expects it,
                    # so delta rendering and user keystrokes land at the
                    # correct column.  Fall back to column 1 if no app.
                    try:
                        _app = prompt_toolkit.application.get_app()
                        _cp = _app.renderer._cursor_pos
                        _row = scroll.input_row + _cp.y
                        _col = _cp.x + 1
                        stdout.write(f"\x1b[{_row};{_col}H".encode())
                        _app.invalidate()
                    except RuntimeError:
                        stdout.write(
                            f"\x1b[{scroll.input_row};1H".encode()
                        )
                    if telnet_writer.mode == "kludge":
                        mode_switched = True
                        server_done = True
                        return

            async def _read_input() -> None:
                nonlocal server_done
                while not server_done:
                    stdout.write(f"\x1b[{scroll.input_row};1H\x1b[2K".encode())
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
                    # EOR/GA pacing: wait for server prompt before sending.
                    if _ga_detected:
                        try:
                            await asyncio.wait_for(
                                prompt_ready.wait(), timeout=2.0
                            )
                        except asyncio.TimeoutError:
                            pass
                    # pylint: disable-next=protected-access
                    is_pw = repl._is_password_mode()
                    echo_text = "*" * len(line) if is_pw else line
                    stdout.write(b"\x1b8")
                    stdout.write(f"\x1b[33m{echo_text}\x1b[m\r\n".encode())
                    replay_buf.append(f"\x1b[33m{echo_text}\x1b[m\r\n".encode())
                    stdout.write(b"\x1b7")
                    stdout.write(f"\x1b[{scroll.input_row};1H".encode())
                    telnet_writer.write(line + "\r\n")
                    # Clear so next iteration waits for fresh GA/EOR.
                    if _ga_detected:
                        prompt_ready.clear()

            try:
                await _run_repl_tasks(_read_server(), _read_input())
            finally:
                if autoreply_engine is not None:
                    autoreply_engine.cancel()
                # Reset cursor shape to terminal default (DECSCUSR).
                stdout.write(b"\x1b[0 q")

        return mode_switched

    async def _repl_event_loop_basic(
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
            stdout.write(f"\x1b[{scroll.input_row};1H\x1b[2K".encode())

            basic_stdin = await term.connect_stdin()
            repl: BasicLineRepl = BasicLineRepl(telnet_writer, basic_stdin, telnet_writer.log)

            def _echo_autoreply_b(cmd: str) -> None:
                scroll.save_and_goto_input()
                scroll.restore_cursor()
                stdout.write(f"\x1b[36m{cmd}\x1b[m\r\n".encode())

            prompt_ready_b = asyncio.Event()
            prompt_ready_b.set()
            _ga_detected_b = False

            def _on_prompt_signal_b(_cmd: bytes) -> None:
                nonlocal _ga_detected_b
                _ga_detected_b = True
                prompt_ready_b.set()
                if autoreply_engine_b is not None:
                    autoreply_engine_b.on_prompt()

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

            _ar_rules_b = getattr(telnet_writer, "_autoreply_rules", None)
            autoreply_engine_b = None
            if _ar_rules_b:
                from .autoreply import AutoreplyEngine  # pylint: disable=import-outside-toplevel

                autoreply_engine_b = AutoreplyEngine(
                    _ar_rules_b, telnet_writer, telnet_writer.log,
                    echo_fn=_echo_autoreply_b,
                    wait_fn=_wait_for_prompt_b,
                )

            server_done = False

            async def _read_server() -> None:
                nonlocal server_done, mode_switched
                while not server_done:
                    out = await telnet_reader.read(2**24)
                    if not out:
                        if telnet_reader.at_eof():
                            server_done = True
                            _flush_color_filter(telnet_writer, stdout)
                            stdout.write(b"\r\nConnection closed by foreign host.\r\n")
                            return
                        continue
                    if isinstance(out, bytes):
                        out = out.decode("utf-8", errors="replace")
                    out = _transform_output(out, telnet_writer, False)
                    if autoreply_engine_b is not None:
                        autoreply_engine_b.feed(out)
                    scroll.save_and_goto_input()
                    scroll.restore_cursor()
                    stdout.write(out.encode())
                    if telnet_writer.mode == "kludge":
                        mode_switched = True
                        server_done = True
                        return

            async def _read_input() -> None:
                nonlocal server_done
                while not server_done:
                    stdout.write(f"\x1b[{scroll.input_row};1H\x1b[2K".encode())
                    line = await repl.prompt()
                    if line is None:
                        server_done = True
                        telnet_writer.close()
                        return
                    if _ga_detected_b:
                        try:
                            await asyncio.wait_for(
                                prompt_ready_b.wait(), timeout=2.0
                            )
                        except asyncio.TimeoutError:
                            pass
                    echo_text = "*" * len(line) if telnet_writer.will_echo else line
                    scroll.save_and_goto_input()
                    scroll.restore_cursor()
                    stdout.write(f"\x1b[33m{echo_text}\x1b[m\r\n".encode())
                    telnet_writer.write(line + "\r\n")
                    if _ga_detected_b:
                        prompt_ready_b.clear()

            try:
                await _run_repl_tasks(_read_server(), _read_input())
            finally:
                if autoreply_engine_b is not None:
                    autoreply_engine_b.cancel()

        return mode_switched
