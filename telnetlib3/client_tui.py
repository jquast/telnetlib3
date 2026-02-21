"""
Textual TUI session manager for telnetlib3-client.

Launched when ``telnetlib3-client`` is invoked without a host argument
and the ``textual`` package is installed (``pip install telnetlib3[tui]``).

Provides a saved-session list, per-session option editing with
fingerprint-based capability detection, and subprocess-based connection
launching.
"""

# pylint: disable=import-error
from __future__ import annotations

# std imports
import os
import sys
import json
import datetime
import subprocess
from typing import Any, ClassVar
from dataclasses import asdict, fields, dataclass

# 3rd party
from textual import events
from textual.app import App, ComposeResult
from textual.screen import Screen
from textual.binding import Binding
from textual.widgets import (
    Input,
    Label,
    Button,
    Footer,
    Select,
    Static,
    Switch,
    RadioSet,
    DataTable,
    RadioButton,
    ContentSwitcher,
)
from textual.containers import Vertical, Horizontal

_ENCODINGS = (
    "utf-8",
    "cp437",
    "latin-1",
    "ascii",
    "iso-8859-1",
    "iso-8859-2",
    "iso-8859-15",
    "cp1251",
    "koi8-r",
    "big5",
    "gbk",
    "euc-kr",
    "shift-jis",
    "atascii",
    "petscii",
)

_XDG_CONFIG = os.environ.get("XDG_CONFIG_HOME", os.path.join(os.path.expanduser("~"), ".config"))
_XDG_DATA = os.environ.get(
    "XDG_DATA_HOME", os.path.join(os.path.expanduser("~"), ".local", "share")
)

CONFIG_DIR = os.path.join(_XDG_CONFIG, "telnetlib3")
DATA_DIR = os.path.join(_XDG_DATA, "telnetlib3")

SESSIONS_FILE = os.path.join(CONFIG_DIR, "sessions.json")
HISTORY_FILE = os.path.join(DATA_DIR, "history")
DEFAULTS_KEY = "__defaults__"


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
    "ice-colors": "ice-colors",
    "ascii-eol": "ascii-eol",
    "ansi-keys": "ansi-keys",
    "ssl": "ssl",
    "ssl-no-verify": "ssl-no-verify",
    "no-repl": "use-repl",
    "loglevel": "loglevel",
    "logfile": "logfile",
}


def _handle_arrow_navigation(
    screen: Screen,  # type: ignore[type-arg]
    event: events.Key,
    button_col_selector: str,
    table_selector: str,
    form_selector: str = "",
) -> None:
    """
    Arrow key navigation between a button column, data table, and form.

    :param screen: The screen handling the key event.
    :param event: The key event.
    :param button_col_selector: CSS selector for the button column container.
    :param table_selector: CSS selector for the DataTable.
    :param form_selector: CSS selector for the inline form (optional).
    """
    focused = screen.focused
    buttons = list(screen.query(f"{button_col_selector} Button"))
    table = screen.query_one(table_selector, DataTable)

    # When the form is visible, handle navigation within form fields.
    if form_selector:
        try:
            form = screen.query_one(form_selector)
        except Exception:  # pylint: disable=broad-except
            form = None
        if form is not None and form.display:
            form_fields: list[Input | Switch | Button] = [
                w for w in form.query("Input, Switch, Button")
                if isinstance(w, (Input, Switch, Button))
            ]
            if focused in form_fields:
                idx = form_fields.index(focused)
                if event.key == "up" and idx > 0:
                    form_fields[idx - 1].focus()
                    event.prevent_default()
                elif event.key == "down" and idx < len(form_fields) - 1:
                    form_fields[idx + 1].focus()
                    event.prevent_default()
                elif event.key == "left" and isinstance(focused, (Switch, Button)):
                    if buttons:
                        screen.call_later(buttons[0].focus)
                    event.prevent_default()
                return
            if isinstance(focused, Button) and focused in buttons:
                if event.key == "right" and form_fields:
                    screen.call_later(form_fields[0].focus)
                    event.prevent_default()
                    return

    if isinstance(focused, Input):
        return

    if isinstance(focused, Button) and focused in buttons:
        idx = buttons.index(focused)
        if event.key == "up" and idx > 0:
            buttons[idx - 1].focus()
            event.prevent_default()
        elif event.key == "down" and idx < len(buttons) - 1:
            buttons[idx + 1].focus()
            event.prevent_default()
        elif event.key == "right":
            screen.call_later(table.focus)
            event.prevent_default()
    elif focused is table and event.key == "left":
        if buttons:
            screen.call_later(buttons[0].focus)
        event.prevent_default()


_TOOLTIP_CACHE: dict[str, str] | None = None


def _build_tooltips() -> dict[str, str]:
    """Extract help text from argparse and return ``{widget_id: help}``."""
    global _TOOLTIP_CACHE  # noqa: PLW0603  # pylint: disable=global-statement
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


@dataclass
class SessionConfig:
    """
    Persistent configuration for a single telnet session.

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


def _ensure_dirs() -> None:
    os.makedirs(CONFIG_DIR, exist_ok=True)
    os.makedirs(DATA_DIR, exist_ok=True)


def load_sessions() -> dict[str, SessionConfig]:
    """Load session configs from ``~/.config/telnetlib3/sessions.json``."""
    _ensure_dirs()
    if not os.path.exists(SESSIONS_FILE):
        return {}
    with open(SESSIONS_FILE, encoding="utf-8") as f:
        data = json.load(f)
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
    with open(SESSIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def build_command(  # pylint: disable=too-many-branches,too-complex
    config: SessionConfig,
) -> list[str]:
    """
    Build ``telnetlib3-client`` CLI arguments from *config*.

    Only emits flags that differ from the CLI defaults.
    """
    cmd = [
        sys.executable,
        "-c",
        "from telnetlib3.client import main; main()",
        config.host,
        str(config.port),
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
        Binding("m", "edit_macros", "Macros"),
        Binding("a", "edit_autoreplies", "Autoreplies"),
        Binding("d", "delete_session", "Delete"),
        Binding("enter", "connect", "Connect"),
        Binding("s", "edit_defaults", "Defaults"),
    ]

    CSS = """
    SessionListScreen {
        align: center top;
    }
    #session-panel {
        width: 80;
        height: 100%;
        border: round $surface-lighten-2;
        background: $surface;
        padding: 0 1;
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
        width: 14;
        height: auto;
        padding-right: 1;
    }
    #button-col Button {
        width: 100%;
        min-width: 0;
        margin-bottom: 0;
    }
    #connect-btn { background: #5b9bf5; color: #0a0a18; }
    #connect-btn:hover { background: #82b4f8; }
    #add-btn { background: #6cc644; color: #0a0a18; }
    #add-btn:hover { background: #8fd86a; }
    #delete-btn { background: #f45070; color: #0a0a18; }
    #delete-btn:hover { background: #f77a95; }
    #edit-btn { background: #e8a030; color: #0a0a18; }
    #edit-btn:hover { background: #f0ba60; }
    #macros-btn { background: #2ec4a8; color: #0a0a18; }
    #macros-btn:hover { background: #5cd8c0; }
    #autoreplies-btn { background: #a06ce4; color: #f0f0f0; }
    #autoreplies-btn:hover { background: #b88eee; }
    #defaults-btn { background: #6670a0; color: #e8ecf8; }
    #defaults-btn:hover { background: #8088b8; }
    """

    def __init__(self) -> None:
        """Initialize session list with empty session dict."""
        super().__init__()
        self._sessions: dict[str, SessionConfig] = {}

    def compose(self) -> ComposeResult:
        """Build the session list layout."""
        with Vertical(id="session-panel"):
            with Horizontal(id="session-body"):
                with Vertical(id="button-col"):
                    yield Button("Connect", id="connect-btn")
                    yield Button("New", id="add-btn")
                    yield Button("Delete", id="delete-btn")
                    yield Button("Edit", id="edit-btn")
                    yield Button("Macros", id="macros-btn")
                    yield Button("Autoreplies", id="autoreplies-btn")
                    yield Button("Defaults", id="defaults-btn")
                yield DataTable(id="session-table")
        yield Footer()

    def on_mount(self) -> None:
        """Load sessions and populate the data table."""
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

    # -- Arrow key navigation between buttons and table -----------------------

    def on_key(self, event: events.Key) -> None:
        """Arrow/Home/End keys navigate between buttons and the session table."""
        if event.key in ("home", "end"):
            table = self.query_one("#session-table", DataTable)
            if self.focused is table and table.row_count > 0:
                row = 0 if event.key == "home" else table.row_count - 1
                table.move_cursor(row=row)
                event.prevent_default()
        elif event.key in ("up", "down", "left", "right"):
            _handle_arrow_navigation(self, event, "#button-col", "#session-table")

    # -- Button handlers ----------------------------------------------------

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Dispatch button press to the appropriate action."""
        handlers = {
            "connect-btn": self.action_connect,
            "add-btn": self.action_new_session,
            "edit-btn": self.action_edit_session,
            "macros-btn": self.action_edit_macros,
            "autoreplies-btn": self.action_edit_autoreplies,
            "delete-btn": self.action_delete_session,
            "defaults-btn": self.action_edit_defaults,
            "quit-btn": self.action_quit_app,
        }
        handler = handlers.get(event.button.id or "")
        if handler:
            handler()

    def on_data_table_row_selected(self, _event: DataTable.RowSelected) -> None:
        """Connect on double-click or Enter."""
        self.action_connect()

    # -- Actions ------------------------------------------------------------

    def action_quit_app(self) -> None:
        """Exit the application."""
        self.app.exit()

    def action_new_session(self) -> None:
        """Open editor for a new session pre-filled with defaults."""
        defaults = self._sessions.get(DEFAULTS_KEY, SessionConfig())
        new_cfg = SessionConfig(**asdict(defaults))
        new_cfg.name = ""
        new_cfg.host = ""
        new_cfg.last_connected = ""
        self.app.push_screen(
            SessionEditScreen(config=new_cfg, is_new=True), callback=self._on_edit_result
        )

    def action_edit_session(self) -> None:
        """Open editor for the selected session."""
        key = self._selected_key()
        if key is None:
            self.notify("No session selected", severity="warning")
            return
        cfg = self._sessions[key]
        self.app.push_screen(SessionEditScreen(config=cfg), callback=self._on_edit_result)

    def action_delete_session(self) -> None:
        """Delete the selected session."""
        key = self._selected_key()
        if key is None:
            self.notify("No session selected", severity="warning")
            return
        del self._sessions[key]
        self._save()
        self._refresh_table()
        self.notify(f"Deleted {key}")

    def action_edit_defaults(self) -> None:
        """Open editor for the default session template."""
        defaults = self._sessions.get(DEFAULTS_KEY, SessionConfig(name=DEFAULTS_KEY))
        self.app.push_screen(
            SessionEditScreen(config=defaults, is_defaults=True), callback=self._on_defaults_result
        )

    def _session_key_for(self, cfg: SessionConfig) -> str:
        """Return ``host:port`` session key for config file lookups."""
        return f"{cfg.host}:{cfg.port}"

    def action_edit_macros(self) -> None:
        """Open macro editor for the selected session."""
        key = self._selected_key()
        if key is None:
            self.notify("No session selected", severity="warning")
            return
        cfg = self._sessions[key]
        path = os.path.join(CONFIG_DIR, "macros.json")
        sk = self._session_key_for(cfg)
        self.app.push_screen(
            MacroEditScreen(path=path, session_key=sk), callback=lambda saved: None
        )

    def action_edit_autoreplies(self) -> None:
        """Open autoreply editor for the selected session."""
        key = self._selected_key()
        if key is None:
            self.notify("No session selected", severity="warning")
            return
        cfg = self._sessions[key]
        path = os.path.join(CONFIG_DIR, "autoreplies.json")
        sk = self._session_key_for(cfg)
        self.app.push_screen(
            AutoreplyEditScreen(path=path, session_key=sk), callback=lambda saved: None
        )

    def action_connect(self) -> None:
        """Launch a telnet connection to the selected session."""
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
            _tsize = os.get_terminal_size()
            sys.stdout.write(f"\x1b[{_tsize.lines};{_tsize.columns}H\r\n")
            sys.stdout.flush()
            try:
                # stderr must NOT be piped — the child may launch
                # Textual subprocesses (F8/F9 editors) that write all
                # output to sys.__stderr__.  A piped stderr would send
                # that output into the pipe instead of the terminal,
                # hanging the editor.
                # pylint: disable-next=consider-using-with
                proc = subprocess.Popen(cmd)
                proc.wait()
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
                    "\x1b[m"  # reset SGR attributes
                    "\x1b[?25h"  # show cursor
                    "\x1b[?1049l"  # exit alternate screen
                    "\x1b[?1000l"  # disable mouse tracking (basic)
                    "\x1b[?1002l"  # disable button-event tracking
                    "\x1b[?1003l"  # disable all-motion tracking
                    "\x1b[?1006l"  # disable SGR mouse format
                    "\x1b[?2004l"  # disable bracketed paste
                )
                sys.stdout.flush()
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
    #timeout-label {
        width: 11;
        padding-top: 1;
        padding-left: 4;
    }
    #connect-timeout {
        max-width: 13;
    }
    #mode-repl-row {
        height: auto;
    }
    #mode-col {
        width: auto;
        max-width: 25;
        height: auto;
    }
    #repl-col {
        width: 1fr;
        height: auto;
        padding-top: 1;
        padding-left: 4;
    }
    #keys-eol-row {
        height: 3;
    }
    .dimmed {
        color: $text-muted;
    }
    #enc-label {
        width: 10;
        padding-top: 1;
    }
    #enc-errors-label {
        width: 12;
        padding-top: 1;
        padding-left: 4;
    }
    #encoding {
        max-width: 20;
    }
    #encoding-errors {
        max-width: 15;
    }
    #background-color {
        max-width: 12;
    }
    #colormatch {
        max-width: 14;
    }
    #palette-preview {
        width: 1fr;
        padding-top: 1;
    }
    #bottom-bar {
        height: 3;
        margin-top: 1;
    }
    #save-btn {
        dock: right;
    }
    #bottom-bar Button {
        margin-right: 1;
    }
    """

    def __init__(
        self, config: SessionConfig, is_defaults: bool = False, is_new: bool = False
    ) -> None:
        """Initialize edit screen with session config and mode flags."""
        super().__init__()
        self._config = config
        self._is_defaults = is_defaults
        self._is_new = is_new

    _TAB_IDS: ClassVar[list[tuple[str, str]]] = [
        ("Connection", "tab-connection"),
        ("Terminal", "tab-terminal"),
        ("Display", "tab-display"),
        ("Advanced", "tab-advanced"),
    ]

    def compose(self) -> ComposeResult:
        """Build the tabbed session editor layout."""
        cfg = self._config

        with Vertical(id="edit-panel"):
            title = (
                "Edit Defaults"
                if self._is_defaults
                else ("Add Session" if self._is_new else f"Edit: {cfg.name or cfg.host}")
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
                                value=cfg.name,
                                placeholder="session name",
                                id="name",
                                classes="field-input",
                            )
                        with Horizontal(classes="field-row"):
                            yield Label("Host:Port", classes="field-label")
                            yield Input(
                                value=cfg.host,
                                placeholder="hostname",
                                id="host",
                                classes="field-input",
                            )
                            yield Static(":", id="host-port-sep")
                            yield Input(value=str(cfg.port), placeholder="23", id="port")
                    with Horizontal(classes="switch-row"):
                        yield Label("SSL/TLS", classes="field-label")
                        yield Switch(value=cfg.ssl, id="ssl")
                        yield Label("Timeout", id="timeout-label")
                        yield Input(
                            value=str(cfg.connect_timeout),
                            id="connect-timeout",
                        )

                with Vertical(id="tab-terminal", classes="tab-pane"):
                    with Horizontal(classes="field-row"):
                        yield Label("TERM", classes="field-label")
                        yield Input(
                            value=cfg.term,
                            placeholder=os.environ.get("TERM", "unknown"),
                            id="term",
                            classes="field-input",
                        )
                    with Horizontal(id="mode-repl-row"):
                        with Vertical(id="mode-col"):
                            yield Label("Terminal Mode")
                            with RadioSet(id="mode-radio"):
                                yield RadioButton(
                                    "Auto-detect", value=cfg.mode == "auto", id="mode-auto"
                                )
                                yield RadioButton(
                                    "Raw mode", value=cfg.mode == "raw", id="mode-raw"
                                )
                                yield RadioButton(
                                    "Line mode", value=cfg.mode == "line", id="mode-line"
                                )
                        with Vertical(id="repl-col"):
                            with Horizontal(classes="switch-row"):
                                _repl_dim = "" if cfg.mode != "raw" else " dimmed"
                                yield Label(
                                    "Advanced REPL",
                                    id="repl-label",
                                    classes=f"field-label{_repl_dim}",
                                )
                                yield Switch(
                                    value=not cfg.no_repl, id="use-repl", disabled=cfg.mode == "raw"
                                )
                    _enc = cfg.encoding or "utf-8"
                    _is_retro = _enc.lower() in ("atascii", "petscii")
                    with Horizontal(classes="field-row"):
                        yield Label("Encoding", id="enc-label")
                        yield Select(
                            [(e, e) for e in _ENCODINGS],
                            value=_enc if _enc in _ENCODINGS else "utf-8",
                            id="encoding",
                            allow_blank=False,
                        )
                        yield Label("Errors", id="enc-errors-label")
                        yield Select(
                            [(v, v) for v in ("replace", "ignore", "strict")],
                            value=cfg.encoding_errors,
                            id="encoding-errors",
                        )
                    _dim = "" if _is_retro else " dimmed"
                    with Horizontal(id="keys-eol-row"):
                        with Horizontal(classes="switch-row"):
                            yield Label(
                                "ANSI Keys",
                                id="ansi-keys-label",
                                classes=f"field-label{_dim}",
                            )
                            yield Switch(
                                value=cfg.ansi_keys, id="ansi-keys", disabled=not _is_retro
                            )
                        with Horizontal(classes="switch-row"):
                            yield Label(
                                "ASCII EOL",
                                id="ascii-eol-label",
                                classes=f"field-label{_dim}",
                            )
                            yield Switch(
                                value=cfg.ascii_eol, id="ascii-eol", disabled=not _is_retro
                            )

                with Vertical(id="tab-display", classes="tab-pane"):
                    with Horizontal(classes="field-row"):
                        yield Label("Color Palette", classes="field-label")
                        yield Select(
                            [(v, v) for v in ("vga", "xterm", "none")],
                            value=cfg.colormatch,
                            id="colormatch",
                        )
                        yield Static("", id="palette-preview")
                    with Horizontal(classes="switch-row"):
                        yield Label("iCE Colors", classes="field-label")
                        yield Switch(value=cfg.ice_colors, id="ice-colors")

                with Vertical(id="tab-advanced", classes="tab-pane"):
                    with Horizontal(classes="field-row"):
                        yield Label("Send Environ", classes="field-label")
                        yield Input(
                            value=cfg.send_environ, id="send-environ", classes="field-input"
                        )

                    with Horizontal(classes="field-row"):
                        yield Label("Log Level, File", classes="field-label")
                        yield Select(
                            [
                                (v, v)
                                for v in ("trace", "debug", "info", "warn", "error", "critical")
                            ],
                            value=cfg.loglevel,
                            id="loglevel",
                        )
                        yield Input(
                            value=cfg.logfile,
                            placeholder="path",
                            id="logfile",
                            classes="field-input",
                        )

            with Horizontal(id="bottom-bar"):
                yield Button("Cancel", variant="error", id="cancel-btn")
                yield Button("Save", variant="success", id="save-btn")

    def on_mount(self) -> None:
        """Apply argparse-derived tooltips to form widgets."""
        tips = _build_tooltips()
        for widget_id, help_text in tips.items():
            try:
                widget = self.query_one(f"#{widget_id}")
                widget.tooltip = help_text
            except Exception:  # pylint: disable=broad-except
                pass
        self._update_palette_preview()

    def on_radio_set_changed(self, event: RadioSet.Changed) -> None:
        """Disable REPL switch when raw mode is selected."""
        if event.radio_set.id == "mode-radio":
            is_raw = event.pressed.id == "mode-raw"
            repl_switch = self.query_one("#use-repl", Switch)
            repl_switch.disabled = is_raw
            self.query_one("#repl-label", Label).set_class(is_raw, "dimmed")

    def on_select_changed(self, event: Select.Changed) -> None:
        """React to Select widget changes."""
        if event.select.id == "colormatch":
            self._update_palette_preview()
        elif event.select.id == "encoding":
            is_retro = str(event.value).lower() in ("atascii", "petscii")
            self.query_one("#ansi-keys", Switch).disabled = not is_retro
            self.query_one("#ascii-eol", Switch).disabled = not is_retro
            for label_id in ("#ansi-keys-label", "#ascii-eol-label"):
                label = self.query_one(label_id, Label)
                label.set_class(not is_retro, "dimmed")

    def on_switch_changed(self, event: Switch.Changed) -> None:
        """Update palette preview when ice_colors changes."""
        if event.switch.id == "ice-colors":
            self._update_palette_preview()

    def _update_palette_preview(self) -> None:
        """Render CP437 full-block color preview for the selected palette."""
        from .color_filter import PALETTES  # pylint: disable=import-outside-toplevel

        palette_name = self.query_one("#colormatch", Select).value
        preview = self.query_one("#palette-preview", Static)
        if palette_name == "none" or palette_name not in PALETTES:
            preview.update("")
            return
        palette = PALETTES[palette_name]
        ice = self.query_one("#ice-colors", Switch).value
        block = "\u2588"
        fg_blocks = "".join(f"[rgb({r},{g},{b})]{block}[/]" for r, g, b in palette)
        bg_count = 16 if ice else 8
        bg_blocks = "".join(f"[on rgb({r},{g},{b})] [/]" for r, g, b in palette[:bg_count])
        preview.update(f"FG: {fg_blocks}\nBG: {bg_blocks}")

    def _switch_to_tab(self, tab_id: str) -> None:
        """Activate the given tab and update button styling."""
        self.query_one("#tab-content", ContentSwitcher).current = tab_id
        for btn in self.query("#tab-bar Button"):
            btn.remove_class("active-tab")
            if btn.id == f"tabbtn-{tab_id}":
                btn.add_class("active-tab")

    def _active_tab_focusables(self) -> list[Any]:
        """Return focusable widgets in the currently visible tab pane."""
        current = self.query_one("#tab-content", ContentSwitcher).current
        if not current:
            return []
        pane = self.query_one(f"#{current}")
        return [w for w in pane.query("Input, Select, Switch, RadioButton") if not w.disabled]

    def on_key(self, event: events.Key) -> None:
        """Arrow key navigation for tabs, fields, and buttons."""
        focused = self.focused
        tab_buttons = list(self.query("#tab-bar Button"))
        bottom_buttons = list(self.query("#bottom-bar Button"))

        if focused in tab_buttons:
            idx = tab_buttons.index(focused)
            if event.key == "left" and idx > 0:
                target = tab_buttons[idx - 1]
                target.focus()
                tab_id = (target.id or "").replace("tabbtn-", "")
                if tab_id:
                    self._switch_to_tab(tab_id)
                event.prevent_default()
            elif event.key == "right" and idx < len(tab_buttons) - 1:
                target = tab_buttons[idx + 1]
                target.focus()
                tab_id = (target.id or "").replace("tabbtn-", "")
                if tab_id:
                    self._switch_to_tab(tab_id)
                event.prevent_default()
            elif event.key == "down":
                focusables = self._active_tab_focusables()
                if focusables:
                    focusables[0].focus()
                event.prevent_default()
            return

        if focused in bottom_buttons:
            idx = bottom_buttons.index(focused)
            if event.key == "left" and idx > 0:
                bottom_buttons[idx - 1].focus()
                event.prevent_default()
            elif event.key == "right" and idx < len(bottom_buttons) - 1:
                bottom_buttons[idx + 1].focus()
                event.prevent_default()
            elif event.key == "up":
                focusables = self._active_tab_focusables()
                if focusables:
                    focusables[-1].focus()
                event.prevent_default()
            return

        focusables = self._active_tab_focusables()
        if focused in focusables:
            idx = focusables.index(focused)
            if event.key == "up":
                if idx > 0:
                    focusables[idx - 1].focus()
                else:
                    current = self.query_one(
                        "#tab-content", ContentSwitcher
                    ).current
                    for btn in tab_buttons:
                        if btn.id == f"tabbtn-{current}":
                            btn.focus()
                            break
                event.prevent_default()
            elif event.key == "down":
                if idx < len(focusables) - 1:
                    focusables[idx + 1].focus()
                elif bottom_buttons:
                    bottom_buttons[0].focus()
                event.prevent_default()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle save, cancel, and tab switching buttons."""
        btn_id = event.button.id or ""
        if btn_id == "save-btn":
            self._on_save()
        elif btn_id == "cancel-btn":
            self.dismiss(None)
        elif btn_id.startswith("tabbtn-"):
            tab_id = btn_id[len("tabbtn-") :]
            self._switch_to_tab(tab_id)

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
        cfg.ssl_no_verify = False

        cfg.last_connected = self._config.last_connected

        cfg.term = self.query_one("#term", Input).value.strip()
        cfg.encoding = self.query_one("#encoding", Select).value  # type: ignore[assignment]
        cfg.encoding_errors = (  # type: ignore[assignment]
            self.query_one("#encoding-errors", Select).value
        )

        if self.query_one("#mode-raw", RadioButton).value:
            cfg.mode = "raw"
        elif self.query_one("#mode-line", RadioButton).value:
            cfg.mode = "line"
        else:
            cfg.mode = "auto"

        cfg.ansi_keys = self.query_one("#ansi-keys", Switch).value
        cfg.ascii_eol = self.query_one("#ascii-eol", Switch).value

        cfg.colormatch = self.query_one("#colormatch", Select).value  # type: ignore[assignment]
        cfg.background_color = "#000000"
        cfg.ice_colors = self.query_one("#ice-colors", Switch).value

        cfg.connect_timeout = _float_val(self.query_one("#connect-timeout", Input).value, 10.0)

        cfg.send_environ = (
            self.query_one("#send-environ", Input).value.strip()
            or "TERM,LANG,COLUMNS,LINES,COLORTERM"
        )
        cfg.always_will = self._config.always_will
        cfg.always_do = self._config.always_do
        cfg.loglevel = self.query_one("#loglevel", Select).value  # type: ignore[assignment]
        cfg.logfile = self.query_one("#logfile", Input).value.strip()
        cfg.no_repl = not self.query_one("#use-repl", Switch).value

        return cfg


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


class MacroEditScreen(Screen["bool | None"]):
    """Editor screen for macro key bindings."""

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "cancel_or_close", "Cancel", priority=True),
        Binding("plus", "reorder_hint", "Change Priority", key_display="+/-", show=True),
        Binding("enter", "save_hint", "Save", show=True),
    ]

    CSS = """
    MacroEditScreen { align: center middle; }
    #macro-panel {
        width: 91; height: 100%; max-height: 22;
        border: round $surface-lighten-2; background: $surface; padding: 1 1;
    }
    #macro-body { height: 1fr; }
    #macro-button-col {
        width: 13; height: auto; padding-right: 1;
    }
    #macro-button-col Button {
        width: 100%; min-width: 0; margin-bottom: 0;
    }
    #macro-copy { background: #6670a0; color: #e8ecf8; }
    #macro-copy:hover { background: #8088b8; }
    #macro-right { width: 1fr; height: 100%; }
    #macro-table { height: 1fr; min-height: 4; overflow-x: hidden; }
    #macro-form { height: 1fr; padding: 0; }
    #macro-form .field-row { height: 3; margin: 0; }
    #macro-form .switch-row { height: 3; margin: 0; }
    #macro-form Input { width: 1fr; border: tall grey; }
    #macro-form Input:focus { border: tall $accent; }
    #macro-form-buttons { height: 3; align-horizontal: right; }
    #macro-form-buttons Button { width: auto; min-width: 10; margin-left: 1; }
    .form-label { width: 8; padding-top: 1; }
    .form-label-short { width: 5; padding-top: 1; }
    .form-label-mid { width: 5; padding-top: 1; }
    .form-gap { width: 2; }
    .form-btn-spacer { width: 1; }
    """

    def __init__(self, path: str, session_key: str = "") -> None:
        """Initialize macro editor with file path and session key."""
        super().__init__()
        self._path = path
        self._session_key = session_key
        self._macros: list[tuple[str, str, bool]] = []
        self._editing_idx: int | None = None

    @property
    def _form_visible(self) -> bool:
        return self.query_one("#macro-form").display

    def compose(self) -> ComposeResult:
        """Build the macro editor layout."""
        with Vertical(id="macro-panel"):
            yield Static(
                f"Macro Editor — {self._session_key}" if self._session_key else "Macro Editor"
            )
            with Horizontal(id="macro-body"):
                with Vertical(id="macro-button-col"):
                    yield Button("Add", variant="success", id="macro-add")
                    yield Button("Edit", variant="warning", id="macro-edit")
                    yield Button("Copy", id="macro-copy")
                    yield Button("Delete", variant="error", id="macro-delete")
                    yield Button("Save", variant="primary", id="macro-save")
                    yield Button("Cancel", id="macro-close")
                with Vertical(id="macro-right"):
                    yield DataTable(id="macro-table")
                    with Vertical(id="macro-form"):
                        with Horizontal(classes="field-row"):
                            yield Label("Enabled", classes="form-label-short")
                            yield Switch(value=True, id="macro-enabled")
                            yield Label("", classes="form-gap")
                            yield Label("Key", classes="form-label-mid")
                            yield Input(placeholder="e.g. f5 or escape n", id="macro-key")
                        with Horizontal(classes="field-row"):
                            yield Label("Text", classes="form-label")
                            yield Input(placeholder="text with <CR> markers", id="macro-text")
                        with Horizontal(id="macro-form-buttons"):
                            yield Label(" ", classes="form-btn-spacer")
                            yield Button("Cancel", variant="default", id="macro-cancel-form")
                            yield Button("OK", variant="success", id="macro-ok")
        yield Footer()

    def on_mount(self) -> None:
        """Load macros from file and populate table."""
        table = self.query_one("#macro-table", DataTable)
        table.cursor_type = "row"
        table.add_columns("Key", "Text")
        self._load_from_file()
        self._refresh_table()
        self.query_one("#macro-form").display = False

    def _load_from_file(self) -> None:
        if not os.path.exists(self._path):
            return
        from .macros import load_macros  # pylint: disable=import-outside-toplevel

        try:
            macros = load_macros(self._path, self._session_key)
            self._macros = [(" ".join(m.keys), m.text, m.enabled) for m in macros]
        except (ValueError, FileNotFoundError):
            pass

    def _refresh_table(self) -> None:
        table = self.query_one("#macro-table", DataTable)
        table.clear()
        for i, (key, text, enabled) in enumerate(self._macros):
            status = "" if enabled else " (off)"
            table.add_row(key, text + status, key=str(i))

    def _show_form(self, key_val: str = "", text_val: str = "", enabled: bool = True) -> None:
        self.query_one("#macro-key", Input).value = key_val
        self.query_one("#macro-text", Input).value = text_val
        self.query_one("#macro-enabled", Switch).value = enabled
        self.query_one("#macro-table").display = False
        self.query_one("#macro-form").display = True
        self.query_one("#macro-add", Button).disabled = True
        self.query_one("#macro-edit", Button).disabled = True
        self.query_one("#macro-copy", Button).disabled = True
        self.query_one("#macro-key", Input).focus()

    def _hide_form(self) -> None:
        self.query_one("#macro-form").display = False
        self.query_one("#macro-table").display = True
        self._editing_idx = None
        self.query_one("#macro-add", Button).disabled = False
        self.query_one("#macro-edit", Button).disabled = False
        self.query_one("#macro-copy", Button).disabled = False
        self.query_one("#macro-table", DataTable).focus()

    def _submit_form(self) -> None:
        """Accept the current inline form values."""
        key_val = self.query_one("#macro-key", Input).value.strip()
        text_val = self.query_one("#macro-text", Input).value
        enabled = self.query_one("#macro-enabled", Switch).value
        if key_val:
            if self._editing_idx is not None:
                self._macros[self._editing_idx] = (key_val, text_val, enabled)
            else:
                self._macros.append((key_val, text_val, enabled))
            self._refresh_table()
        self._hide_form()

    def _selected_idx(self) -> int | None:
        table = self.query_one("#macro-table", DataTable)
        if table.row_count == 0:
            return None
        row_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
        return int(str(row_key.value))

    def _edit_selected(self) -> None:
        """Open the selected row for editing."""
        idx = self._selected_idx()
        if idx is not None and idx < len(self._macros):
            self._editing_idx = idx
            k, t, ena = self._macros[idx]
            self._show_form(k, t, ena)

    def _copy_selected(self) -> None:
        """Duplicate the selected row."""
        idx = self._selected_idx()
        if idx is not None and idx < len(self._macros):
            k, t, ena = self._macros[idx]
            self._macros.insert(idx + 1, (k, t, ena))
            self._refresh_table()
            table = self.query_one("#macro-table", DataTable)
            table.move_cursor(row=idx + 1)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Double-click or Enter on a table row opens it for editing."""
        idx = int(str(event.row_key.value))
        if idx < len(self._macros):
            self._editing_idx = idx
            k, t, ena = self._macros[idx]
            self._show_form(k, t, ena)

    def on_key(self, event: events.Key) -> None:
        """Arrow/Home/End/+/- keys navigate and reorder the macro table."""
        if event.key in ("home", "end"):
            table = self.query_one("#macro-table", DataTable)
            if self.focused is table and table.row_count > 0:
                row = 0 if event.key == "home" else table.row_count - 1
                table.move_cursor(row=row)
                event.prevent_default()
        elif event.key in ("up", "down", "left", "right"):
            _handle_arrow_navigation(
                self, event, "#macro-button-col", "#macro-table", "#macro-form"
            )
        elif event.key in ("plus", "minus") and not self._form_visible:
            self._reorder(event.key == "plus")

    def _reorder(self, move_down: bool) -> None:
        """Swap the selected row with its neighbour."""
        idx = self._selected_idx()
        if idx is None:
            return
        target = idx + 1 if move_down else idx - 1
        if target < 0 or target >= len(self._macros):
            return
        self._macros[idx], self._macros[target] = (
            self._macros[target], self._macros[idx]
        )
        self._refresh_table()
        table = self.query_one("#macro-table", DataTable)
        table.move_cursor(row=target)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Enter in an inline form input submits the form."""
        if self._form_visible:
            event.stop()
            self._submit_form()

    def action_cancel_or_close(self) -> None:
        """Escape closes the inline form, or dismisses the screen."""
        if self._form_visible:
            self._hide_form()
        else:
            self.dismiss(None)

    def action_reorder_hint(self) -> None:
        """No-op; +/- handled in on_key, binding exists for footer hint."""

    def action_save_hint(self) -> None:
        """No-op; enter handled by on_input_submitted, binding exists for footer hint."""

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle macro editor button presses."""
        btn = event.button.id or ""
        if btn == "macro-add":
            self._editing_idx = None
            self._show_form()
        elif btn == "macro-edit":
            self._edit_selected()
        elif btn == "macro-copy":
            self._copy_selected()
        elif btn == "macro-delete":
            if self._form_visible:
                self._hide_form()
            idx = self._selected_idx()
            if idx is not None and idx < len(self._macros):
                self._macros.pop(idx)
                self._refresh_table()
        elif btn == "macro-ok":
            self._submit_form()
        elif btn == "macro-cancel-form":
            self._hide_form()
        elif btn == "macro-save":
            if self._form_visible:
                self._submit_form()
            self._save_to_file()
            self.dismiss(True)
        elif btn == "macro-close":
            self.dismiss(None)

    def _save_to_file(self) -> None:
        from .macros import Macro, save_macros  # pylint: disable=import-outside-toplevel

        os.makedirs(os.path.dirname(self._path), exist_ok=True)
        macros = [Macro(keys=tuple(k.split()), text=t, enabled=ena) for k, t, ena in self._macros]
        save_macros(self._path, macros, self._session_key)


class AutoreplyEditScreen(Screen["bool | None"]):
    """Editor screen for autoreply rules."""

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "cancel_or_close", "Cancel", priority=True),
        Binding("plus", "reorder_hint", "Change Priority", key_display="+/-", show=True),
        Binding("enter", "save_hint", "Save", show=True),
    ]

    CSS = """
    AutoreplyEditScreen { align: center middle; }
    #autoreply-panel {
        width: 91; height: 100%; max-height: 23;
        border: round $surface-lighten-2; background: $surface; padding: 1 1;
    }
    #autoreply-body { height: 1fr; }
    #autoreply-button-col {
        width: 13; height: auto; padding-right: 1;
    }
    #autoreply-button-col Button {
        width: 100%; min-width: 0; margin-bottom: 0;
    }
    #autoreply-copy { background: #6670a0; color: #e8ecf8; }
    #autoreply-copy:hover { background: #8088b8; }
    #autoreply-right { width: 1fr; height: 100%; }
    #autoreply-table { height: 1fr; min-height: 4; overflow-x: hidden; }
    #autoreply-form { height: 1fr; padding: 0 0 0 4; }
    #autoreply-form .field-row { height: 3; margin: 0; }
    #autoreply-form Input { width: 1fr; border: tall grey; }
    #autoreply-form Input:focus { border: tall $accent; }
    #autoreply-form-buttons { height: 3; align-horizontal: right; }
    #autoreply-form-buttons Button { width: auto; min-width: 10; margin-left: 1; }
    #autoreply-form .form-label { width: 12; padding-top: 1; }
    #autoreply-form .form-label-short { width: 9; padding-top: 1; }
    #autoreply-form .form-label-mid { width: 9; padding-top: 1; }
    #autoreply-form .form-gap { width: 2; }
    #autoreply-form .form-btn-spacer { width: 1; }
    #autoreply-timeout { width: 8; }
    """

    def __init__(self, path: str, session_key: str = "", select_pattern: str = "") -> None:
        """Initialize autoreply editor with file path and session key."""
        super().__init__()
        self._path = path
        self._session_key = session_key
        self._select_pattern = select_pattern
        self._rules: list[tuple[str, str, bool, str, bool, bool, float, str]] = []
        self._editing_idx: int | None = None

    @property
    def _form_visible(self) -> bool:
        return self.query_one("#autoreply-form").display

    def compose(self) -> ComposeResult:
        """Build the autoreply editor layout."""
        with Vertical(id="autoreply-panel"):
            yield Static(
                f"Autoreply Editor — {self._session_key}"
                if self._session_key
                else "Autoreply Editor"
            )
            with Horizontal(id="autoreply-body"):
                with Vertical(id="autoreply-button-col"):
                    yield Button("Add", variant="success", id="autoreply-add")
                    yield Button("Edit", variant="warning", id="autoreply-edit")
                    yield Button("Copy", id="autoreply-copy")
                    yield Button("Delete", variant="error", id="autoreply-delete")
                    yield Button("Save", variant="primary", id="autoreply-save")
                    yield Button("Cancel", id="autoreply-close")
                with Vertical(id="autoreply-right"):
                    yield DataTable(id="autoreply-table")
                    with Vertical(id="autoreply-form"):
                        with Horizontal(classes="field-row"):
                            yield Label("Enabled", classes="form-label-short")
                            yield Switch(value=True, id="autoreply-enabled")
                            yield Label("", classes="form-gap")
                            yield Label("Pattern", classes="form-label-mid")
                            yield Input(placeholder="regex pattern", id="autoreply-pattern")
                        with Horizontal(classes="field-row"):
                            excl = Switch(value=False, id="autoreply-exclusive")
                            excl.tooltip = "Only this rule matches until cleared by prompt or until pattern"
                            yield Label("Exclusive", classes="form-label-short")
                            yield excl
                            yield Label("", classes="form-gap")
                            yield Label("Reply", classes="form-label-mid")
                            yield Input(
                                placeholder=r"reply with \1 refs and <CR>", id="autoreply-reply"
                            )
                        with Horizontal(classes="field-row"):
                            alw = Switch(value=False, id="autoreply-always")
                            alw.tooltip = "Match even while another exclusive rule is active"
                            yield Label("Always", classes="form-label-short")
                            yield alw
                            yield Label("", classes="form-gap")
                            yield Label("Until", classes="form-label-mid")
                            yield Input(
                                placeholder=r"optional: \1 died\.",
                                id="autoreply-until",
                            )
                        with Horizontal(classes="field-row"):
                            yield Label("Timeout", classes="form-label-short")
                            yield Input(
                                value="30.0", placeholder="seconds",
                                id="autoreply-timeout",
                            )
                            yield Label("", classes="form-gap")
                            yield Label("Post Cmd", classes="form-label-mid")
                            yield Input(
                                placeholder="optional: look<CR>",
                                id="autoreply-post-command",
                            )
                        with Horizontal(id="autoreply-form-buttons"):
                            yield Label(" ", classes="form-btn-spacer")
                            yield Button("Cancel", variant="default", id="autoreply-cancel-form")
                            yield Button("OK", variant="success", id="autoreply-ok")
        yield Footer()

    def on_mount(self) -> None:
        """Load autoreplies from file and populate table."""
        table = self.query_one("#autoreply-table", DataTable)
        table.cursor_type = "row"
        table.add_columns("#", "Pattern", "Reply", "Flags")
        self._load_from_file()
        self._refresh_table()
        self.query_one("#autoreply-form").display = False
        if self._select_pattern:
            for i, (pattern, *_rest) in enumerate(self._rules):
                if pattern == self._select_pattern:
                    table.move_cursor(row=i)
                    break

    def _load_from_file(self) -> None:
        if not os.path.exists(self._path):
            return
        from .autoreply import load_autoreplies  # pylint: disable=import-outside-toplevel

        try:
            rules = load_autoreplies(self._path, self._session_key)
            self._rules = [
                (r.pattern.pattern, r.reply, r.exclusive, r.until,
                 r.always, r.enabled, r.exclusive_timeout,
                 r.post_command)
                for r in rules
            ]
        except (ValueError, FileNotFoundError):
            pass

    def _refresh_table(self) -> None:
        table = self.query_one("#autoreply-table", DataTable)
        table.clear()
        for i, (pattern, reply, exclusive, until, always, enabled, _tout, _pcmd) in enumerate(
            self._rules
        ):
            flags = ""
            if not enabled:
                flags = "X"
            if exclusive:
                flags = (flags + " E*") if until else (flags + " E")
            if always:
                flags = (flags + " A") if flags else "A"
            table.add_row(str(i + 1), pattern, reply, flags.strip(), key=str(i))

    def _show_form(
        self,
        pattern_val: str = "",
        reply_val: str = "",
        exclusive: bool = False,
        until: str = "",
        always: bool = False,
        enabled: bool = True,
        exclusive_timeout: float = 30.0,
        post_command: str = "",
    ) -> None:
        self.query_one("#autoreply-pattern", Input).value = pattern_val
        self.query_one("#autoreply-reply", Input).value = reply_val
        self.query_one("#autoreply-until", Input).value = until
        self.query_one("#autoreply-post-command", Input).value = post_command
        self.query_one("#autoreply-exclusive", Switch).value = exclusive
        self.query_one("#autoreply-always", Switch).value = always
        self.query_one("#autoreply-enabled", Switch).value = enabled
        self.query_one("#autoreply-timeout", Input).value = str(exclusive_timeout)
        self.query_one("#autoreply-table").display = False
        self.query_one("#autoreply-form").display = True
        self.query_one("#autoreply-add", Button).disabled = True
        self.query_one("#autoreply-edit", Button).disabled = True
        self.query_one("#autoreply-copy", Button).disabled = True
        self.query_one("#autoreply-pattern", Input).focus()

    def _hide_form(self) -> None:
        self.query_one("#autoreply-form").display = False
        self.query_one("#autoreply-table").display = True
        self._editing_idx = None
        self.query_one("#autoreply-add", Button).disabled = False
        self.query_one("#autoreply-edit", Button).disabled = False
        self.query_one("#autoreply-copy", Button).disabled = False
        self.query_one("#autoreply-table", DataTable).focus()

    def _submit_form(self) -> None:
        """Accept the current inline form values."""
        pattern_val = self.query_one("#autoreply-pattern", Input).value.strip()
        reply_val = self.query_one("#autoreply-reply", Input).value
        until_val = self.query_one("#autoreply-until", Input).value.strip()
        post_cmd = self.query_one("#autoreply-post-command", Input).value.strip()
        exclusive = self.query_one("#autoreply-exclusive", Switch).value
        always = self.query_one("#autoreply-always", Switch).value
        enabled = self.query_one("#autoreply-enabled", Switch).value
        try:
            timeout_val = float(
                self.query_one("#autoreply-timeout", Input).value.strip() or "30"
            )
        except ValueError:
            timeout_val = 30.0
        if pattern_val:
            import re  # pylint: disable=import-outside-toplevel

            try:
                re.compile(pattern_val)
            except re.error as exc:
                self.notify(f"Invalid regex: {exc}", severity="error")
                return
            if self._editing_idx is not None:
                self._rules[self._editing_idx] = (
                    pattern_val, reply_val, exclusive, until_val, always,
                    enabled, timeout_val, post_cmd,
                )
            else:
                self._rules.append(
                    (pattern_val, reply_val, exclusive, until_val, always,
                     enabled, timeout_val, post_cmd)
                )
            self._refresh_table()
        self._hide_form()

    def _selected_idx(self) -> int | None:
        table = self.query_one("#autoreply-table", DataTable)
        if table.row_count == 0:
            return None
        row_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
        return int(str(row_key.value))

    def _edit_selected(self) -> None:
        """Open the selected row for editing."""
        idx = self._selected_idx()
        if idx is not None and idx < len(self._rules):
            self._editing_idx = idx
            p, r, excl, until, alw, ena, tout, pcmd = self._rules[idx]
            self._show_form(p, r, excl, until, alw, ena, tout, pcmd)

    def _copy_selected(self) -> None:
        """Duplicate the selected row."""
        idx = self._selected_idx()
        if idx is not None and idx < len(self._rules):
            self._rules.insert(idx + 1, self._rules[idx])
            self._refresh_table()
            table = self.query_one("#autoreply-table", DataTable)
            table.move_cursor(row=idx + 1)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Double-click or Enter on a table row opens it for editing."""
        idx = int(str(event.row_key.value))
        if idx < len(self._rules):
            self._editing_idx = idx
            p, r, excl, until, alw, ena, tout, pcmd = self._rules[idx]
            self._show_form(p, r, excl, until, alw, ena, tout, pcmd)

    def on_key(self, event: events.Key) -> None:
        """Arrow/Home/End/+/- keys navigate and reorder the autoreply table."""
        if event.key in ("home", "end"):
            table = self.query_one("#autoreply-table", DataTable)
            if self.focused is table and table.row_count > 0:
                row = 0 if event.key == "home" else table.row_count - 1
                table.move_cursor(row=row)
                event.prevent_default()
        elif event.key in ("up", "down", "left", "right"):
            _handle_arrow_navigation(
                self, event, "#autoreply-button-col", "#autoreply-table",
                "#autoreply-form",
            )
        elif event.key in ("plus", "minus") and not self._form_visible:
            self._reorder(event.key == "plus")

    def _reorder(self, move_down: bool) -> None:
        """Swap the selected row with its neighbour."""
        idx = self._selected_idx()
        if idx is None:
            return
        target = idx + 1 if move_down else idx - 1
        if target < 0 or target >= len(self._rules):
            return
        self._rules[idx], self._rules[target] = (
            self._rules[target], self._rules[idx]
        )
        self._refresh_table()
        table = self.query_one("#autoreply-table", DataTable)
        table.move_cursor(row=target)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Enter in an inline form input submits the form."""
        if self._form_visible:
            event.stop()
            self._submit_form()

    def action_cancel_or_close(self) -> None:
        """Escape closes the inline form, or dismisses the screen."""
        if self._form_visible:
            self._hide_form()
        else:
            self.dismiss(None)

    def action_reorder_hint(self) -> None:
        """No-op; +/- handled in on_key, binding exists for footer hint."""

    def action_save_hint(self) -> None:
        """No-op; enter handled by on_input_submitted, binding exists for footer hint."""

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle autoreply editor button presses."""
        btn = event.button.id or ""
        if btn == "autoreply-add":
            self._editing_idx = None
            self._show_form()
        elif btn == "autoreply-edit":
            self._edit_selected()
        elif btn == "autoreply-copy":
            self._copy_selected()
        elif btn == "autoreply-delete":
            if self._form_visible:
                self._hide_form()
            idx = self._selected_idx()
            if idx is not None and idx < len(self._rules):
                self._rules.pop(idx)
                self._refresh_table()
        elif btn == "autoreply-ok":
            self._submit_form()
        elif btn == "autoreply-cancel-form":
            self._hide_form()
        elif btn == "autoreply-save":
            if self._form_visible:
                self._submit_form()
            self._save_to_file()
            self.dismiss(True)
        elif btn == "autoreply-close":
            self.dismiss(None)

    def _save_to_file(self) -> None:
        import re  # pylint: disable=import-outside-toplevel

        from .autoreply import (  # pylint: disable=import-outside-toplevel
            AutoreplyRule,
            save_autoreplies,
        )

        os.makedirs(os.path.dirname(self._path), exist_ok=True)
        rules = []
        for p, r, excl, until, alw, ena, tout, pcmd in self._rules:
            rules.append(AutoreplyRule(
                pattern=re.compile(p, re.MULTILINE | re.DOTALL),
                reply=r,
                exclusive=excl,
                until=until,
                post_command=pcmd,
                always=alw,
                enabled=ena,
                exclusive_timeout=tout,
            ))
        save_autoreplies(self._path, rules, self._session_key)


class RoomBrowserScreen(Screen["bool | None"]):
    """Browser screen for GMCP room graph with search, bookmarks, fast travel."""

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "close", "Close", priority=True),
        Binding("enter", "fast_travel", "Travel", show=True),
        Binding("asterisk", "toggle_bookmark", "Bookmark", key_display="*", show=True,
                priority=True),
    ]

    CSS = """
    RoomBrowserScreen { align: center middle; }
    #room-panel {
        width: 91; height: 100%; max-height: 22;
        border: round $surface-lighten-2; background: $surface; padding: 1 1;
    }
    #room-body { height: 1fr; }
    #room-button-col {
        width: 13; height: auto; padding-right: 1;
    }
    #room-button-col Button {
        width: 100%; min-width: 0; margin-bottom: 0;
    }
    #room-right { width: 1fr; height: 100%; }
    #room-search { dock: top; margin-bottom: 1; }
    #room-table { height: 1fr; min-height: 4; overflow-x: hidden; }
    #room-status { height: 1; margin-top: 0; }
    """

    def __init__(
        self,
        rooms_path: str,
        session_key: str = "",
        current_room_file: str = "",
        fasttravel_file: str = "",
    ) -> None:
        """Initialize room browser."""
        super().__init__()
        self._rooms_path = rooms_path
        self._session_key = session_key
        self._current_room_file = current_room_file
        self._fasttravel_file = fasttravel_file
        self._all_rooms: list[tuple[str, str, str, int, bool]] = []

    def compose(self) -> ComposeResult:
        """Build the room browser layout."""
        title = f"Room Browser \u2014 {self._session_key}" if self._session_key else "Room Browser"
        with Vertical(id="room-panel"):
            yield Static(title)
            yield Input(placeholder="Search rooms...", id="room-search")
            with Horizontal(id="room-body"):
                with Vertical(id="room-button-col"):
                    travel_btn = Button("Travel", variant="success", id="room-travel")
                    travel_btn.tooltip = (
                        "Fast travel: move without stopping, skip exclusive autoreplies"
                    )
                    yield travel_btn
                    slow_btn = Button("Slow", variant="primary", id="room-slow-travel")
                    slow_btn.tooltip = (
                        "Slow travel: wait for autoreplies to finish in each room"
                    )
                    yield slow_btn
                    yield Button("Bookmark", variant="warning", id="room-bookmark")
                    yield Button("Close", id="room-close")
                with Vertical(id="room-right"):
                    yield DataTable(id="room-table")
                    yield Static("", id="room-status")
        yield Footer()

    def on_mount(self) -> None:
        """Load rooms from file and populate table."""
        table = self.query_one("#room-table", DataTable)
        table.cursor_type = "row"
        table.add_columns("\u2605", "Area", "Name", "Exits")
        self._load_rooms()
        self._refresh_table()
        self._select_current_room()

    def _select_current_room(self) -> None:
        """Move cursor to the current room row, if known."""
        if not self._current_room_file:
            return
        from telnetlib3.rooms import read_current_room  # pylint: disable=import-outside-toplevel

        current = read_current_room(self._current_room_file)
        if not current:
            return
        table = self.query_one("#room-table", DataTable)
        for row_idx, row_key in enumerate(table.rows):
            if row_key.value == current:
                table.move_cursor(row=row_idx)
                break

    def _load_rooms(self) -> None:
        """Load room data from JSON file."""
        if not os.path.exists(self._rooms_path):
            return
        from telnetlib3.rooms import load_rooms  # pylint: disable=import-outside-toplevel

        graph = load_rooms(self._rooms_path)
        self._all_rooms = [
            (r.num, r.name, r.area, len(r.exits), r.bookmarked)
            for r in graph.rooms.values()
        ]
        self._all_rooms.sort(key=lambda r: (not r[4], r[2].lower(), r[1].lower()))

    def _refresh_table(self, query: str = "") -> None:
        """Refresh table rows, filtering by search query."""
        table = self.query_one("#room-table", DataTable)
        table.clear()
        q = query.lower()
        for num, name, area, exits, bookmarked in self._all_rooms:
            if q and q not in name.lower() and q not in area.lower():
                continue
            star = "\u2605" if bookmarked else ""
            table.add_row(star, area, name, str(exits), key=num)
        status = self.query_one("#room-status", Static)
        n_shown = table.row_count
        n_total = len(self._all_rooms)
        if query:
            status.update(f"{n_shown}/{n_total} rooms shown")
        else:
            status.update(f"{n_total} rooms")

    def on_input_changed(self, event: Input.Changed) -> None:
        """Filter table when search input changes."""
        if event.input.id == "room-search":
            self._refresh_table(event.value)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses."""
        if event.button.id == "room-close":
            self.dismiss(None)
        elif event.button.id == "room-travel":
            self._do_fast_travel(slow=False)
        elif event.button.id == "room-slow-travel":
            self._do_fast_travel(slow=True)
        elif event.button.id == "room-bookmark":
            self._do_toggle_bookmark()

    def on_key(self, event: events.Key) -> None:
        """Arrow/Home/End keys navigate between search, buttons, and the room table."""
        if event.key in ("home", "end"):
            table = self.query_one("#room-table", DataTable)
            if self.focused is table and table.row_count > 0:
                row = 0 if event.key == "home" else table.row_count - 1
                table.move_cursor(row=row)
                event.prevent_default()
            return
        if event.key not in ("up", "down", "left", "right"):
            return
        focused = self.focused
        search = self.query_one("#room-search", Input)
        table = self.query_one("#room-table", DataTable)
        buttons = list(self.query("#room-button-col Button"))
        if focused is search:
            if event.key == "down":
                table.focus()
                event.prevent_default()
            elif event.key == "left" and buttons:
                buttons[0].focus()
                event.prevent_default()
            return
        if focused is table and event.key == "up":
            if table.cursor_coordinate.row == 0:
                search.focus()
                event.prevent_default()
                return
        _handle_arrow_navigation(self, event, "#room-button-col", "#room-table")

    def action_close(self) -> None:
        """Close the room browser."""
        self.dismiss(None)

    def action_fast_travel(self) -> None:
        """Initiate fast travel to the selected room."""
        self._do_fast_travel(slow=False)

    def action_toggle_bookmark(self) -> None:
        """Toggle bookmark on the selected room."""
        self._do_toggle_bookmark()

    def _do_toggle_bookmark(self) -> None:
        """Toggle bookmark flag on the currently selected room."""
        table = self.query_one("#room-table", DataTable)
        if table.row_count == 0:
            return
        row_key, _ = table.coordinate_to_cell_key(table.cursor_coordinate)
        num = row_key.value
        if num is None:
            return

        from telnetlib3.rooms import (  # pylint: disable=import-outside-toplevel
            load_rooms, save_rooms,
        )

        graph = load_rooms(self._rooms_path)
        graph.toggle_bookmark(num)
        save_rooms(self._rooms_path, graph)

        for i, (rnum, name, area, exits, bm) in enumerate(self._all_rooms):
            if rnum == num:
                self._all_rooms[i] = (rnum, name, area, exits, not bm)
                break
        self._all_rooms.sort(key=lambda r: (not r[4], r[2].lower(), r[1].lower()))
        search_val = self.query_one("#room-search", Input).value
        self._refresh_table(search_val)

    def _do_fast_travel(self, slow: bool = False) -> None:
        """Calculate path and write fast travel file."""
        table = self.query_one("#room-table", DataTable)
        if table.row_count == 0:
            return
        row_key, _ = table.coordinate_to_cell_key(table.cursor_coordinate)
        dst_num = row_key.value
        if dst_num is None:
            return

        from telnetlib3.rooms import (  # pylint: disable=import-outside-toplevel
            load_rooms,
            read_current_room,
            write_fasttravel,
        )

        current = read_current_room(self._current_room_file)
        if not current:
            status = self.query_one("#room-status", Static)
            status.update("No current room — move first")
            return

        if current == dst_num:
            status = self.query_one("#room-status", Static)
            status.update("Already in this room")
            return

        graph = load_rooms(self._rooms_path)
        path = graph.find_path_with_rooms(current, dst_num)
        if path is None:
            dst_name = ""
            for rnum, name, *_ in self._all_rooms:
                if rnum == dst_num:
                    dst_name = name
                    break
            status = self.query_one("#room-status", Static)
            status.update(f"No path found to {dst_name or dst_num}")
            return

        write_fasttravel(self._fasttravel_file, path, slow=slow)
        self.dismiss(True)


class _EditorApp(App[None]):
    """Minimal Textual app for standalone macro/autoreply editing."""

    def __init__(self, screen: Screen["bool | None"]) -> None:
        """Initialize with the editor screen to push."""
        super().__init__()
        self._editor_screen = screen

    def _set_pointer_shape(self, shape: str) -> None:
        """
        Disable pointer shape changes to prevent WriterThread deadlock.

        Textual writes escape sequences to set cursor shape on mouse move.
        When the PTY output buffer is full, ``WriterThread.write()`` blocks,
        and the bounded queue causes ``queue.put()`` to block the main
        asyncio thread, freezing the entire app.
        """

    def on_mount(self) -> None:
        """Push the editor screen."""
        self.push_screen(self._editor_screen, callback=lambda _: self.exit())


_faulthandler_file: Any = None  # pylint: disable=invalid-name


def _enable_faulthandler() -> None:
    """Enable faulthandler with SIGUSR1 for non-fatal traceback dumps."""
    global _faulthandler_file  # noqa: PLW0603  # pylint: disable=global-statement
    import signal  # pylint: disable=import-outside-toplevel
    import faulthandler  # pylint: disable=import-outside-toplevel

    if _faulthandler_file is None:
        _faulthandler_file = open(  # noqa: SIM115  # pylint: disable=consider-using-with
            "/tmp/textual-faulthandler.log", "a", encoding="utf-8"
        )
    faulthandler.enable(file=_faulthandler_file)
    faulthandler.register(signal.SIGUSR1, file=_faulthandler_file, all_threads=True)


def _patch_writer_thread_queue() -> None:
    """
    Make Textual's WriterThread queue unbounded.

    Textual's ``WriterThread`` uses a bounded queue (``maxsize=30``).
    When terminal output processing lags behind rapid re-renders
    (e.g. clicking between widgets), ``queue.put()`` blocks the main
    asyncio thread, freezing the entire app.  Setting the constant
    to 0 (unbounded) before the ``WriterThread`` is instantiated
    prevents the deadlock.
    """
    try:
        import textual.drivers._writer_thread as _wt  # pylint: disable=import-outside-toplevel

        _wt.MAX_QUEUED_WRITES = 0  # type: ignore[misc]
    except (ImportError, AttributeError):
        pass


def _restore_blocking_fds() -> None:
    """
    Restore blocking mode on stdin/stdout/stderr.

    The parent process may set ``O_NONBLOCK`` on the shared PTY file
    description (via asyncio ``connect_read_pipe`` or prompt_toolkit).
    Since stdin, stdout, and stderr all reference the same kernel file
    description, the child subprocess inherits non-blocking mode.
    Textual's ``WriterThread`` does not handle ``BlockingIOError``,
    so a non-blocking stderr causes the thread to die silently,
    freezing the app.
    """
    import os  # pylint: disable=import-outside-toplevel,redefined-outer-name,reimported

    for fd in (0, 1, 2):
        try:
            os.set_blocking(fd, True)
        except OSError:
            pass


def edit_macros_main(path: str, session_key: str = "") -> None:
    """Launch standalone macro editor TUI."""
    _restore_blocking_fds()
    _enable_faulthandler()
    _patch_writer_thread_queue()
    app = _EditorApp(MacroEditScreen(path=path, session_key=session_key))
    app.run()


def edit_autoreplies_main(
    path: str, session_key: str = "", select_pattern: str = ""
) -> None:
    """Launch standalone autoreply editor TUI."""
    _restore_blocking_fds()
    _enable_faulthandler()
    _patch_writer_thread_queue()
    app = _EditorApp(AutoreplyEditScreen(
        path=path, session_key=session_key, select_pattern=select_pattern,
    ))
    app.run()


def edit_rooms_main(
    rooms_path: str,
    session_key: str = "",
    current_room_file: str = "",
    fasttravel_file: str = "",
) -> None:
    """Launch standalone room browser TUI."""
    _restore_blocking_fds()
    _enable_faulthandler()
    _patch_writer_thread_queue()
    app = _EditorApp(RoomBrowserScreen(
        rooms_path=rooms_path,
        session_key=session_key,
        current_room_file=current_room_file,
        fasttravel_file=fasttravel_file,
    ))
    app.run()


class TelnetSessionApp(App[None]):
    """Textual TUI for managing telnetlib3 client sessions."""

    TITLE = "telnetlib3 Session Manager"
    ENABLE_COMMAND_PALETTE = False

    def _set_pointer_shape(self, shape: str) -> None:
        """Disable pointer shape changes to prevent WriterThread deadlock."""

    def on_mount(self) -> None:
        """Push the session list screen on startup."""
        self.push_screen(SessionListScreen())


def tui_main() -> None:
    """Launch the Textual TUI session manager."""
    app = TelnetSessionApp()
    app.run()
