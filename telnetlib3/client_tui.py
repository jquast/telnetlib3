"""Textual TUI session manager for telnetlib3-client.

Launched when ``telnetlib3-client`` is invoked without a host argument
and the ``textual`` package is installed (``pip install telnetlib3[tui]``).

Provides a saved-session list, per-session option editing with
fingerprint-based capability detection, and subprocess-based connection
launching.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import os
import subprocess
import sys
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any, ClassVar

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import (
    Button,
    ContentSwitcher,
    DataTable,
    Footer,
    Input,
    Label,
    RadioButton,
    RadioSet,
    Select,
    Static,
    Switch,
)

# ---------------------------------------------------------------------------
# Constants -- XDG Base Directory paths
# ---------------------------------------------------------------------------

_XDG_CONFIG = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
_XDG_DATA = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))

CONFIG_DIR = _XDG_CONFIG / "telnetlib3"
DATA_DIR = _XDG_DATA / "telnetlib3"

SESSIONS_FILE = CONFIG_DIR / "sessions.json"
HISTORY_FILE = DATA_DIR / "history"
DEFAULTS_KEY = "__defaults__"

# ---------------------------------------------------------------------------
# Tooltip Extraction from argparse
# ---------------------------------------------------------------------------

# Map CLI flag names (without leading --) to TUI widget IDs.
_FLAG_TO_WIDGET: dict[str, str] = {
    "term": "term",
    "encoding": "encoding",
    "encoding-errors": "encoding-errors",
    "force-binary": "force-binary",
    "raw-mode": "mode-raw",
    "line-mode": "mode-line",
    "connect-timeout": "connect-timeout",
    "send-environ": "send-environ",
    "always-will": "always-will",
    "always-do": "always-do",
    "colormatch": "colormatch",
    "background-color": "background-color",
    "reverse-video": "reverse-video",
    "ice-colors": "ice-colors",
    "ascii-eol": "ascii-eol",
    "ansi-keys": "ansi-keys",
    "ssl": "ssl",

    "ssl-no-verify": "ssl-no-verify",
    "no-repl": "no-repl",
    "loglevel": "loglevel",
    "logfile": "logfile",
}


_TOOLTIP_CACHE: dict[str, str] | None = None


def _build_tooltips() -> dict[str, str]:
    """Extract help text from argparse and return ``{widget_id: help}``."""
    global _TOOLTIP_CACHE  # noqa: PLW0603
    if _TOOLTIP_CACHE is not None:
        return _TOOLTIP_CACHE
    from .client import _get_argument_parser  # pylint: disable=import-outside-toplevel

    parser = _get_argument_parser()
    tips: dict[str, str] = {}
    for action in parser._actions:  # pylint: disable=protected-access
        if not action.help:
            continue
        for opt in action.option_strings:
            flag = opt.lstrip("-")
            widget_id = _FLAG_TO_WIDGET.get(flag)
            if widget_id:
                tips[widget_id] = action.help
    _TOOLTIP_CACHE = tips
    return tips

# ---------------------------------------------------------------------------
# Data Model
# ---------------------------------------------------------------------------


@dataclass
class SessionConfig:
    """Persistent configuration for a single telnet session.

    Field defaults mirror the CLI defaults in
    :func:`telnetlib3.client._get_argument_parser`.
    """

    # Metadata
    name: str = ""
    last_connected: str = ""

    # Connection
    host: str = ""
    port: int = 23
    ssl: bool = False
    ssl_cafile: str = ""
    ssl_no_verify: bool = False

    # Terminal
    term: str = ""  # empty = use $TERM at runtime
    speed: int = 38400
    encoding: str = "utf8"
    force_binary: bool = True
    encoding_errors: str = "replace"

    # Mode: "auto", "raw", or "line"
    mode: str = "auto"

    # Display
    colormatch: str = "vga"
    color_brightness: float = 1.0
    color_contrast: float = 1.0
    background_color: str = "#000000"
    reverse_video: bool = False
    ice_colors: bool = True

    # Input
    ansi_keys: bool = False
    ascii_eol: bool = False

    # Negotiation
    connect_minwait: float = 0.0
    connect_maxwait: float = 4.0
    connect_timeout: float = 10.0

    # Environment
    send_environ: str = "TERM,LANG,COLUMNS,LINES,COLORTERM"

    # Advanced
    always_will: str = ""  # comma-separated option names
    always_do: str = ""
    loglevel: str = "warn"
    logfile: str = ""
    no_repl: bool = False


# ---------------------------------------------------------------------------
# JSON Persistence
# ---------------------------------------------------------------------------


def _ensure_dirs() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def load_sessions() -> dict[str, SessionConfig]:
    """Load session configs from ``~/.config/telnetlib3/sessions.json``."""
    _ensure_dirs()
    if not SESSIONS_FILE.exists():
        return {}
    data = json.loads(SESSIONS_FILE.read_text(encoding="utf-8"))
    known = {f.name for f in fields(SessionConfig)}
    result: dict[str, SessionConfig] = {}
    for key, val in data.items():
        filtered = {k: v for k, v in val.items() if k in known}
        result[key] = SessionConfig(**filtered)
    return result


def save_sessions(sessions: dict[str, SessionConfig]) -> None:
    """Save session configs to ``~/.config/telnetlib3/sessions.json``."""
    _ensure_dirs()
    data = {key: asdict(cfg) for key, cfg in sessions.items()}
    SESSIONS_FILE.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )



# ---------------------------------------------------------------------------
# Command Builder
# ---------------------------------------------------------------------------


def build_command(config: SessionConfig) -> list[str]:
    """Build ``telnetlib3-client`` CLI arguments from *config*.

    Only emits flags that differ from the CLI defaults.
    """
    cmd = [
        sys.executable, "-c",
        "from telnetlib3.client import main; main()",
        config.host, str(config.port),
    ]

    if config.term:
        cmd.extend(["--term", config.term])
    if config.encoding != "utf8":
        cmd.extend(["--encoding", config.encoding])
    if config.speed != 38400:
        cmd.extend(["--speed", str(config.speed)])
    if config.encoding_errors != "replace":
        cmd.extend(["--encoding-errors", config.encoding_errors])

    if config.mode == "raw":
        cmd.append("--raw-mode")
    elif config.mode == "line":
        cmd.append("--line-mode")

    if config.colormatch != "vga":
        cmd.extend(["--colormatch", config.colormatch])
    if config.color_brightness != 1.0:
        cmd.extend(["--color-brightness", str(config.color_brightness)])
    if config.color_contrast != 1.0:
        cmd.extend(["--color-contrast", str(config.color_contrast)])
    if config.background_color != "#000000":
        cmd.extend(["--background-color", config.background_color])
    if config.reverse_video:
        cmd.append("--reverse-video")
    if not config.ice_colors:
        cmd.append("--no-ice-colors")
    if config.ansi_keys:
        cmd.append("--ansi-keys")
    if config.ascii_eol:
        cmd.append("--ascii-eol")

    if config.connect_minwait != 0.0:
        cmd.extend(["--connect-minwait", str(config.connect_minwait)])
    if config.connect_maxwait != 4.0:
        cmd.extend(["--connect-maxwait", str(config.connect_maxwait)])
    if config.connect_timeout > 0 and config.connect_timeout != 10.0:
        cmd.extend(["--connect-timeout", str(config.connect_timeout)])

    if config.send_environ != "TERM,LANG,COLUMNS,LINES,COLORTERM":
        cmd.extend(["--send-environ", config.send_environ])

    for opt in config.always_will.split(","):
        opt = opt.strip()
        if opt:
            cmd.extend(["--always-will", opt])
    for opt in config.always_do.split(","):
        opt = opt.strip()
        if opt:
            cmd.extend(["--always-do", opt])

    if config.loglevel != "warn":
        cmd.extend(["--loglevel", config.loglevel])
    if config.logfile:
        cmd.extend(["--logfile", config.logfile])

    if config.ssl:
        cmd.append("--ssl")
    if config.ssl_cafile:
        cmd.extend(["--ssl-cafile", config.ssl_cafile])
    if config.ssl_no_verify:
        cmd.append("--ssl-no-verify")
    if config.no_repl:
        cmd.append("--no-repl")

    return cmd



# ---------------------------------------------------------------------------
# TUI Screens
# ---------------------------------------------------------------------------


def _relative_time(iso_str: str) -> str:
    """Return a short relative-time string like ``'5m ago'`` or ``'3d ago'``."""
    if not iso_str:
        return ""
    try:
        then = datetime.datetime.fromisoformat(iso_str)
        delta = datetime.datetime.now() - then
        seconds = int(delta.total_seconds())
        if seconds < 0:
            return ""
        if seconds < 60:
            return f"{seconds}s ago"
        minutes = seconds // 60
        if minutes < 60:
            return f"{minutes}m ago"
        hours = minutes // 60
        if hours < 24:
            return f"{hours}h ago"
        days = hours // 24
        return f"{days}d ago"
    except (ValueError, TypeError):
        return iso_str[:10]


class SessionListScreen(Screen[None]):
    """Main screen: table of saved sessions with action buttons."""

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("q", "quit_app", "Quit"),
        Binding("n", "new_session", "New"),
        Binding("e", "edit_session", "Edit"),
        Binding("d", "delete_session", "Delete"),
        Binding("enter", "connect", "Connect"),
        Binding("s", "edit_defaults", "Defaults"),
    ]

    CSS = """
    SessionListScreen {
        align: center middle;
    }
    #session-panel {
        width: 74;
        height: 100%;
        max-height: 24;
        border: round $surface-lighten-2;
        background: $surface;
        padding: 1 1;
    }
    #session-body {
        height: 1fr;
    }
    #session-table {
        width: 1fr;
        height: 100%;
        min-height: 5;
        overflow-x: hidden;
    }
    #button-col {
        width: 13;
        height: auto;
        padding-right: 1;
    }
    #button-col Button {
        width: 100%;
        min-width: 0;
        margin-bottom: 0;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._sessions: dict[str, SessionConfig] = {}

    def compose(self) -> ComposeResult:

        with Vertical(id="session-panel"):
            with Horizontal(id="session-body"):
                with Vertical(id="button-col"):
                    yield Button("Connect", variant="primary", id="connect-btn")
                    yield Button("New", variant="success", id="add-btn")
                    yield Button("Edit", variant="warning", id="edit-btn")
                    yield Button("Delete", variant="error", id="delete-btn")
                    yield Button("Defaults", id="defaults-btn")
                    yield Button("Quit", id="quit-btn")
                yield DataTable(id="session-table")
        yield Footer()

    def on_mount(self) -> None:
        self._sessions = load_sessions()
        table = self.query_one("#session-table", DataTable)
        table.cursor_type = "row"
        table.add_columns("Name", "Host", "Port", "Enc", "Last")
        self._refresh_table()

    def _refresh_table(self) -> None:
        table = self.query_one("#session-table", DataTable)
        table.clear()
        for key, cfg in self._sessions.items():
            if key == DEFAULTS_KEY:
                continue
            table.add_row(
                cfg.name or key,
                cfg.host,
                str(cfg.port),
                cfg.encoding,
                _relative_time(cfg.last_connected),
                key=key,
            )

    def _save(self) -> None:
        save_sessions(self._sessions)

    def _session_keys(self) -> list[str]:
        return [k for k in self._sessions if k != DEFAULTS_KEY]

    def _selected_key(self) -> str | None:
        table = self.query_one("#session-table", DataTable)
        if table.row_count == 0:
            return None
        row_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
        return str(row_key.value)

    # -- Button handlers ----------------------------------------------------

    def on_button_pressed(self, event: Button.Pressed) -> None:
        handlers = {
            "connect-btn": self.action_connect,
            "add-btn": self.action_new_session,
            "edit-btn": self.action_edit_session,
            "delete-btn": self.action_delete_session,
            "defaults-btn": self.action_edit_defaults,
            "quit-btn": self.action_quit_app,
        }
        handler = handlers.get(event.button.id or "")
        if handler:
            handler()

    def on_data_table_row_selected(self, _event: DataTable.RowSelected) -> None:
        self.action_connect()

    # -- Actions ------------------------------------------------------------

    def action_quit_app(self) -> None:
        self.app.exit()

    def action_new_session(self) -> None:
        defaults = self._sessions.get(DEFAULTS_KEY, SessionConfig())
        new_cfg = SessionConfig(**asdict(defaults))
        new_cfg.name = ""
        new_cfg.host = ""
        new_cfg.last_connected = ""
        self.app.push_screen(
            SessionEditScreen(config=new_cfg, is_new=True),
            callback=self._on_edit_result,
        )

    def action_edit_session(self) -> None:
        key = self._selected_key()
        if key is None:
            self.notify("No session selected", severity="warning")
            return
        cfg = self._sessions[key]
        self.app.push_screen(
            SessionEditScreen(config=cfg),
            callback=self._on_edit_result,
        )

    def action_delete_session(self) -> None:
        key = self._selected_key()
        if key is None:
            self.notify("No session selected", severity="warning")
            return
        del self._sessions[key]
        self._save()
        self._refresh_table()
        self.notify(f"Deleted {key}")

    def action_edit_defaults(self) -> None:
        defaults = self._sessions.get(DEFAULTS_KEY, SessionConfig(name=DEFAULTS_KEY))
        self.app.push_screen(
            SessionEditScreen(config=defaults, is_defaults=True),
            callback=self._on_defaults_result,
        )

    def action_connect(self) -> None:
        key = self._selected_key()
        if key is None:
            self.notify("No session selected", severity="warning")
            return
        cfg = self._sessions[key]
        if not cfg.host:
            self.notify("No host configured", severity="error")
            return

        cfg.last_connected = datetime.datetime.now().isoformat()
        self._save()

        cmd = build_command(cfg)
        with self.app.suspend():
            # Move to bottom-right and print newline so the TUI
            # scrolls cleanly off screen before the client starts.
            sys.stdout.write(
                f"\x1b[{os.get_terminal_size().lines};"
                f"{os.get_terminal_size().columns}H\r\n"
            )
            sys.stdout.flush()
            child_stderr = ""
            try:
                proc = subprocess.Popen(cmd, stderr=subprocess.PIPE)
                proc.wait()
                if proc.stderr:
                    child_stderr = proc.stderr.read().decode("utf-8", errors="replace")
            except KeyboardInterrupt:
                proc.terminate()
                proc.wait(timeout=3)
            finally:
                # The child process shares the kernel file description
                # for stdin/stdout.  asyncio's connect_read_pipe sets
                # O_NONBLOCK on the shared description, which persists
                # after the child exits.  Textual's input loop expects
                # blocking reads — restore before Textual resumes.
                os.set_blocking(sys.stdin.fileno(), True)
                # Reset terminal to known-good state — the child may
                # have left raw mode, SGR attributes, mouse tracking,
                # or alternate screen active.
                sys.stdout.write(
                    "\x1b[m"        # reset SGR attributes
                    "\x1b[?25h"     # show cursor
                    "\x1b[?1049l"   # exit alternate screen
                    "\x1b[?1000l"   # disable mouse tracking (basic)
                    "\x1b[?1002l"   # disable button-event tracking
                    "\x1b[?1003l"   # disable all-motion tracking
                    "\x1b[?1006l"   # disable SGR mouse format
                    "\x1b[?2004l"   # disable bracketed paste
                )
                sys.stdout.flush()
            if proc.returncode and proc.returncode != 0:
                # After exiting alternate screen the cursor is restored
                # to its pre-TUI position.  Move to the bottom so the
                # error and prompt appear at the end of the screen.
                lines = os.get_terminal_size().lines
                sys.stdout.write(f"\x1b[{lines};1H")
                sys.stdout.flush()
                if child_stderr.strip():
                    print(f"\n{child_stderr.strip()}")
                input("\n[Press Enter to return to session manager]")
        self._refresh_table()

    # -- Callbacks ----------------------------------------------------------

    def _on_edit_result(self, config: SessionConfig | None) -> None:
        if config is None:
            return
        key = config.name or config.host
        if not key:
            return
        self._sessions[key] = config
        self._save()
        self._refresh_table()
        self._select_row(key)

    def _select_row(self, key: str) -> None:
        """Move the table cursor to the row with the given key."""
        table = self.query_one("#session-table", DataTable)
        for row_idx, row_key in enumerate(table.rows):
            if str(row_key.value) == key:
                table.move_cursor(row=row_idx)
                break

    def _on_defaults_result(self, config: SessionConfig | None) -> None:
        if config is None:
            return
        self._sessions[DEFAULTS_KEY] = config
        self._save()


class SessionEditScreen(Screen[SessionConfig | None]):
    """Full-screen form for adding or editing a session."""

    CSS = """
    SessionEditScreen {
        align: center middle;
    }
    #edit-panel {
        width: 100%;
        max-width: 65;
        height: 100%;
        max-height: 26;
        border: round $surface-lighten-2;
        background: $surface;
        padding: 1 1;
    }
    #tab-bar {
        height: 1;
        margin-bottom: 1;
    }
    #tab-bar Button {
        min-width: 0;
        height: 1;
        margin: 0 1 0 0;
        border: none;
        background: $surface-lighten-1;
    }
    #tab-bar Button.active-tab {
        background: $accent;
        color: $text;
        text-style: bold;
    }
    #tab-content {
        height: 1fr;
    }
    .tab-pane {
        height: auto;
    }
    .field-row {
        height: 3;
        margin-bottom: 0;
    }
    .field-label {
        width: 14;
        padding-top: 1;
    }
    .field-input {
        width: 1fr;
    }
    .switch-row {
        height: 3;
    }
    #host-port-sep {
        width: 1;
        padding-top: 1;
    }
    #port {
        max-width: 14;
    }
    #bottom-bar {
        height: 3;
        margin-top: 1;
    }
    #bottom-bar Button {
        margin-right: 1;
    }
    """

    def __init__(
        self,
        config: SessionConfig,
        is_defaults: bool = False,
        is_new: bool = False,
    ) -> None:
        super().__init__()
        self._config = config
        self._is_defaults = is_defaults
        self._is_new = is_new

    _TAB_IDS: ClassVar[list[tuple[str, str]]] = [
        ("Connection", "tab-connection"),
        ("Terminal", "tab-terminal"),
        ("Mode", "tab-mode"),
        ("Display", "tab-display"),
        ("Advanced", "tab-advanced"),
    ]

    def compose(self) -> ComposeResult:
        cfg = self._config

        with Vertical(id="edit-panel"):
            title = "Edit Defaults" if self._is_defaults else (
                "Add Session" if self._is_new else f"Edit: {cfg.name or cfg.host}"
            )
            yield Static(title, id="edit-title")

            with Horizontal(id="tab-bar"):
                for i, (label, tab_id) in enumerate(self._TAB_IDS):
                    btn = Button(label, id=f"tabbtn-{tab_id}")
                    if i == 0:
                        btn.add_class("active-tab")
                    yield btn

            with ContentSwitcher(id="tab-content", initial="tab-connection"):
                with Vertical(id="tab-connection", classes="tab-pane"):
                    if not self._is_defaults:
                        with Horizontal(classes="field-row"):
                            yield Label("Name", classes="field-label")
                            yield Input(
                                value=cfg.name, placeholder="session name",
                                id="name", classes="field-input",
                            )
                        with Horizontal(classes="field-row"):
                            yield Label("Host:Port", classes="field-label")
                            yield Input(
                                value=cfg.host, placeholder="hostname",
                                id="host", classes="field-input",
                            )
                            yield Static(":", id="host-port-sep")
                            yield Input(
                                value=str(cfg.port), placeholder="23",
                                id="port",
                            )
                    with Horizontal(classes="field-row"):
                        yield Label("Connection Timeout", classes="field-label")
                        yield Input(
                            value=str(cfg.connect_timeout),
                            id="connect-timeout", classes="field-input",
                        )
                    with Horizontal(classes="switch-row"):
                        yield Label("SSL/TLS", classes="field-label")
                        yield Switch(value=cfg.ssl, id="ssl")
                        yield Label("Skip Verify", classes="field-label")
                        yield Switch(value=cfg.ssl_no_verify, id="ssl-no-verify")

                with Vertical(id="tab-terminal", classes="tab-pane"):
                    with Horizontal(classes="field-row"):
                        yield Label("TERM", classes="field-label")
                        yield Input(
                            value=cfg.term,
                            placeholder=os.environ.get("TERM", "unknown"),
                            id="term", classes="field-input",
                        )
                    with Horizontal(classes="field-row"):
                        yield Label("Encoding, Errors", classes="field-label")
                        yield Input(
                            value=cfg.encoding, id="encoding", classes="field-input",
                        )
                        yield Select(
                            [(v, v) for v in ("replace", "ignore", "strict")],
                            value=cfg.encoding_errors,
                            id="encoding-errors",
                        )
                    with Horizontal(classes="switch-row"):
                        yield Label("Disable REPL", classes="field-label")
                        yield Switch(value=cfg.no_repl, id="no-repl")

                with Vertical(id="tab-mode", classes="tab-pane"):
                    yield Label("Terminal Mode")
                    with RadioSet(id="mode-radio"):
                        yield RadioButton(
                            "Auto-detect", value=cfg.mode == "auto", id="mode-auto",
                        )
                        yield RadioButton(
                            "Raw mode", value=cfg.mode == "raw", id="mode-raw",
                        )
                        yield RadioButton(
                            "Line mode", value=cfg.mode == "line", id="mode-line",
                        )
                    with Horizontal(classes="switch-row"):
                        yield Label("ANSI Keys", classes="field-label")
                        yield Switch(value=cfg.ansi_keys, id="ansi-keys")
                    with Horizontal(classes="switch-row"):
                        yield Label("ASCII EOL", classes="field-label")
                        yield Switch(value=cfg.ascii_eol, id="ascii-eol")

                with Vertical(id="tab-display", classes="tab-pane"):
                    with Horizontal(classes="field-row"):
                        yield Label("Color Palette", classes="field-label")
                        yield Select(
                            [(v, v) for v in (
                                "vga", "ega", "cga", "xterm", "none",
                            )],
                            value=cfg.colormatch,
                            id="colormatch",
                        )
                    with Horizontal(classes="field-row"):
                        yield Label("Background", classes="field-label")
                        yield Input(
                            value=cfg.background_color,
                            id="background-color", classes="field-input",
                        )
                    with Horizontal(classes="switch-row"):
                        yield Label("Reverse Video", classes="field-label")
                        yield Switch(value=cfg.reverse_video, id="reverse-video")
                    with Horizontal(classes="switch-row"):
                        yield Label("iCE Colors", classes="field-label")
                        yield Switch(value=cfg.ice_colors, id="ice-colors")

                with Vertical(id="tab-advanced", classes="tab-pane"):
                    with Horizontal(classes="field-row"):
                        yield Label("Send Environ", classes="field-label")
                        yield Input(
                            value=cfg.send_environ,
                            id="send-environ", classes="field-input",
                        )
                    with Horizontal(classes="field-row"):
                        yield Label("Always WILL", classes="field-label")
                        yield Input(
                            value=cfg.always_will,
                            placeholder="comma-separated option names",
                            id="always-will", classes="field-input",
                        )
                    with Horizontal(classes="field-row"):
                        yield Label("Always DO", classes="field-label")
                        yield Input(
                            value=cfg.always_do,
                            placeholder="comma-separated option names",
                            id="always-do", classes="field-input",
                        )
                    with Horizontal(classes="field-row"):
                        yield Label("Log Level, File", classes="field-label")
                        yield Select(
                            [(v, v) for v in (
                                "trace", "debug", "info", "warn", "error",
                                "critical",
                            )],
                            value=cfg.loglevel,
                            id="loglevel",
                        )
                        yield Input(
                            value=cfg.logfile,
                            placeholder="path",
                            id="logfile", classes="field-input",
                        )

            with Horizontal(id="bottom-bar"):
                yield Button("Save", variant="success", id="save-btn")
                yield Button("Cancel", variant="error", id="cancel-btn")

    def on_mount(self) -> None:
        """Apply argparse-derived tooltips to form widgets."""
        tips = _build_tooltips()
        for widget_id, help_text in tips.items():
            try:
                widget = self.query_one(f"#{widget_id}")
                widget.tooltip = help_text
            except Exception:  # pylint: disable=broad-except
                pass

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id or ""
        if btn_id == "save-btn":
            self._on_save()
        elif btn_id == "cancel-btn":
            self.dismiss(None)
        elif btn_id.startswith("tabbtn-"):
            tab_id = btn_id[len("tabbtn-"):]
            self.query_one("#tab-content", ContentSwitcher).current = tab_id
            for btn in self.query("#tab-bar Button"):
                btn.remove_class("active-tab")
            event.button.add_class("active-tab")

    # -- Save ---------------------------------------------------------------

    def _on_save(self) -> None:
        config = self._collect_config()
        self.dismiss(config)

    def _collect_config(self) -> SessionConfig:
        """Read all widget values back into a :class:`SessionConfig`."""
        cfg = SessionConfig()

        if not self._is_defaults:
            cfg.name = self.query_one("#name", Input).value.strip()
            cfg.host = self.query_one("#host", Input).value.strip()
            cfg.port = _int_val(self.query_one("#port", Input).value, 23)
        else:
            cfg.name = DEFAULTS_KEY

        cfg.ssl = self.query_one("#ssl", Switch).value
        cfg.ssl_no_verify = self.query_one("#ssl-no-verify", Switch).value

        cfg.last_connected = self._config.last_connected

        cfg.term = self.query_one("#term", Input).value.strip()
        cfg.encoding = self.query_one("#encoding", Input).value.strip() or "utf8"
        cfg.encoding_errors = self.query_one("#encoding-errors", Select).value  # type: ignore[assignment]

        if self.query_one("#mode-raw", RadioButton).value:
            cfg.mode = "raw"
        elif self.query_one("#mode-line", RadioButton).value:
            cfg.mode = "line"
        else:
            cfg.mode = "auto"

        cfg.ansi_keys = self.query_one("#ansi-keys", Switch).value
        cfg.ascii_eol = self.query_one("#ascii-eol", Switch).value

        cfg.colormatch = self.query_one("#colormatch", Select).value  # type: ignore[assignment]
        cfg.background_color = (
            self.query_one("#background-color", Input).value.strip() or "#000000"
        )
        cfg.reverse_video = self.query_one("#reverse-video", Switch).value
        cfg.ice_colors = self.query_one("#ice-colors", Switch).value

        cfg.connect_timeout = _float_val(
            self.query_one("#connect-timeout", Input).value, 10.0
        )

        cfg.send_environ = (
            self.query_one("#send-environ", Input).value.strip()
            or "TERM,LANG,COLUMNS,LINES,COLORTERM"
        )
        cfg.always_will = self.query_one("#always-will", Input).value.strip()
        cfg.always_do = self.query_one("#always-do", Input).value.strip()
        cfg.loglevel = self.query_one("#loglevel", Select).value  # type: ignore[assignment]
        cfg.logfile = self.query_one("#logfile", Input).value.strip()
        cfg.no_repl = self.query_one("#no-repl", Switch).value

        return cfg


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _int_val(text: str, default: int) -> int:
    try:
        return int(text.strip())
    except (ValueError, TypeError):
        return default


def _float_val(text: str, default: float) -> float:
    try:
        return float(text.strip())
    except (ValueError, TypeError):
        return default


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------


class TelnetSessionApp(App[None]):
    """Textual TUI for managing telnetlib3 client sessions."""

    TITLE = "telnetlib3 Session Manager"
    ENABLE_COMMAND_PALETTE = False

    def on_mount(self) -> None:
        self.push_screen(SessionListScreen())


def tui_main() -> None:
    """Launch the Textual TUI session manager."""
    app = TelnetSessionApp()
    app.run()
