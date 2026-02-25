"""TUI subprocess management: confirmation dialogs, help screen, editor launchers."""

# std imports
import os
import sys
import asyncio
import logging
from typing import TYPE_CHECKING, Any, Optional

# local
from .client_repl_render import CURSOR_HIDE

if TYPE_CHECKING:
    from .session_context import SessionContext

# Buffer for MUD data received while a TUI editor subprocess is running.
# The asyncio _read_server loop continues receiving MUD data during editor
# sessions; writing that data to the terminal fills the PTY buffer and
# deadlocks the editor's Textual WriterThread.  Data is queued here and
# replayed when the editor exits.
_editor_active = False
_editor_buffer: list[bytes] = []


def _get_logfile_path() -> str:
    """Return the path of the first FileHandler on the root logger, or ``""``."""
    for handler in logging.getLogger().handlers:
        if isinstance(handler, logging.FileHandler) and handler.baseFilename:
            return handler.baseFilename
    return ""


def _safe_terminal_size() -> str:
    """Return ``os.get_terminal_size()`` as a string, or ``"?"`` on error."""
    try:
        sz = os.get_terminal_size()
        return f"{sz.columns}x{sz.lines}"
    except OSError:
        return "?"


def _confirm_dialog(
    title: str, body: str, warning: str = "", replay_buf: Optional[Any] = None
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
    import json as _json
    import tempfile
    import subprocess

    from .client_repl import _get_term, _blocking_fds, _terminal_cleanup, _restore_after_subprocess

    fd, result_path = tempfile.mkstemp(suffix=".json", prefix="confirm-")
    os.close(fd)

    logfile = _get_logfile_path()
    cmd = [
        sys.executable,
        "-c",
        "import sys; from telnetlib3.client_tui import confirm_dialog_main; "
        "confirm_dialog_main(sys.argv[1], sys.argv[2],"
        " warning=sys.argv[3], result_file=sys.argv[4],"
        " logfile=sys.argv[5])",
        title,
        body,
        warning or "",
        result_path,
        logfile,
    ]

    global _editor_active  # noqa: PLW0603
    log = logging.getLogger(__name__)
    log.debug(
        "confirm_dialog: pre-subprocess fd0_blocking=%s fd1=%s fd2=%s "
        "stdin_isatty=%s stderr_isatty=%s "
        "TERM=%s COLORTERM=%s terminal_size=%s",
        os.get_blocking(0),
        os.get_blocking(1),
        os.get_blocking(2),
        sys.stdin.isatty(),
        sys.__stderr__.isatty(),
        os.environ.get("TERM", ""),
        os.environ.get("COLORTERM", ""),
        _safe_terminal_size(),
    )
    t = _get_term()
    sys.stdout.write(_terminal_cleanup())
    sys.stdout.write(t.change_scroll_region(0, t.height - 1))
    sys.stdout.flush()
    sys.stderr.flush()
    sys.__stderr__.flush()
    _editor_active = True
    try:
        with _blocking_fds():
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


def _show_help(
    macro_defs: "Any" = None, replay_buf: Optional[Any] = None, has_gmcp: bool = False
) -> None:
    """
    Display keybinding help on the alternate screen buffer.

    :param macro_defs: Optional list of macro definitions to display.
    :param replay_buf: Optional replay buffer for screen repaint on return.
    :param has_gmcp: Whether GMCP room data is available.
    """
    from .client_repl import _get_term, _restore_after_subprocess

    global _editor_active  # noqa: PLW0603
    t = _get_term()
    sys.stdout.write(CURSOR_HIDE)
    sys.stdout.write(t.enter_fullscreen)
    sys.stdout.write(t.home + t.clear)
    lines = ["", "  telnetlib3 \u2014 Keybindings", "", "  F1          This help screen"]
    if has_gmcp:
        lines += [
            "  F3          Random walk (explore random exits)",
            "  F4          Autodiscover (explore unvisited exits)",
            "  F5          Wander mode (visit same-named rooms)",
            "  F7          Browse rooms / fast travel",
        ]
    lines += [
        "  F8          Edit macros (TUI editor)",
        "  F9          Edit autoreplies (TUI editor)",
        "  Shift+F9    Toggle autoreplies on/off",
        "  Ctrl+L      Repaint screen",
        "  Ctrl+]      Disconnect",
        "",
        "  Line editing:",
        "  Left/Right     Move cursor",
        "  Home/Ctrl+A    Beginning of line",
        "  End/Ctrl+E     End of line",
        "  Ctrl+Left      Move word left",
        "  Ctrl+Right     Move word right",
        "  Backspace      Delete before cursor",
        "  Delete         Delete at cursor",
        "  Ctrl+K         Kill to end of line",
        "  Ctrl+U         Kill entire line",
        "  Ctrl+W         Kill word back",
        "  Ctrl+Y         Yank (paste killed text)",
        "  Ctrl+Z         Undo",
        "  Up/Down        History navigation",
        "",
        "  Command processing:",
        "  ;              Separator (e.g. get all;drop sword)",
        "  3n;2e          Repeat prefix (expands to n;n;n;e;e)",
        "",
    ]
    if macro_defs:
        lines.append("  User macros:")
        for m in macro_defs:
            key = m.key
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

    import select

    _editor_active = True
    try:
        with t.raw():
            os.set_blocking(sys.stdin.fileno(), True)
            select.select([sys.stdin.fileno()], [], [])
            os.read(sys.stdin.fileno(), 1)
    finally:
        _editor_active = False

    sys.stdout.write(t.exit_fullscreen)
    sys.stdout.flush()
    _restore_after_subprocess(replay_buf)


def _launch_tui_editor(
    editor_type: str, ctx: "SessionContext", replay_buf: Optional[Any] = None
) -> None:
    """
    Launch a TUI editor for macros or autoreplies in a subprocess.

    :param editor_type: ``"macros"`` or ``"autoreplies"``.
    :param ctx: Session context with file path and definition attributes.
    :param replay_buf: Optional replay buffer for screen repaint on return.
    """
    import subprocess

    from ._paths import CONFIG_DIR as _config_dir
    from .client_repl import _get_term, _blocking_fds, _terminal_cleanup, _restore_after_subprocess

    session_key = ctx.session_key

    logfile = _get_logfile_path()

    if editor_type == "macros":
        path = ctx.macros_file or os.path.join(_config_dir, "macros.json")
        from .rooms import rooms_path as _rooms_path_fn
        from .rooms import current_room_path as _current_room_path_fn

        rp = ctx.rooms_file or _rooms_path_fn(session_key)
        crp = ctx.current_room_file or _current_room_path_fn(session_key)
        cmd = [
            sys.executable,
            "-c",
            "import sys; from telnetlib3.client_tui import edit_macros_main; "
            "edit_macros_main(sys.argv[1], sys.argv[2],"
            " rooms_file=sys.argv[3], current_room_file=sys.argv[4],"
            " logfile=sys.argv[5])",
            path,
            session_key,
            rp,
            crp,
            logfile,
        ]
    else:
        path = ctx.autoreplies_file or os.path.join(_config_dir, "autoreplies.json")
        engine = ctx.autoreply_engine
        select = getattr(engine, "last_matched_pattern", "") if engine else ""
        cmd = [
            sys.executable,
            "-c",
            "import sys; from telnetlib3.client_tui import edit_autoreplies_main; "
            "edit_autoreplies_main(sys.argv[1], sys.argv[2],"
            " select_pattern=sys.argv[3], logfile=sys.argv[4])",
            path,
            session_key,
            select,
            logfile,
        ]

    log = logging.getLogger(__name__)

    global _editor_active  # noqa: PLW0603
    log.debug(
        "tui_editor: pre-subprocess fd0_blocking=%s fd1=%s fd2=%s "
        "stdin_isatty=%s stderr_isatty=%s editor_type=%s "
        "TERM=%s COLORTERM=%s terminal_size=%s",
        os.get_blocking(0),
        os.get_blocking(1),
        os.get_blocking(2),
        sys.stdin.isatty(),
        sys.__stderr__.isatty(),
        editor_type,
        os.environ.get("TERM", ""),
        os.environ.get("COLORTERM", ""),
        _safe_terminal_size(),
    )
    t = _get_term()
    sys.stdout.write(_terminal_cleanup())
    sys.stdout.write(t.change_scroll_region(0, t.height - 1))
    sys.stdout.flush()
    sys.stderr.flush()
    sys.__stderr__.flush()
    _editor_active = True
    try:
        with _blocking_fds():
            subprocess.run(cmd, check=False)
    except FileNotFoundError:
        log.warning("could not launch TUI editor subprocess")
    finally:
        _editor_active = False
        _restore_after_subprocess(replay_buf)

    if editor_type == "macros":
        _reload_macros(ctx, path, session_key, log)
    else:
        _reload_autoreplies(ctx, path, session_key, log)


def _reload_macros(ctx: "SessionContext", path: str, session_key: str, log: logging.Logger) -> None:
    """Reload macro definitions from disk and update dispatch."""
    if not os.path.exists(path):
        return
    from .macros import load_macros

    try:
        new_defs = load_macros(path, session_key)
        ctx.macro_defs = new_defs
        ctx.macros_file = path
        dispatch = ctx.key_dispatch
        if dispatch is not None:
            dispatch.set_macros(new_defs, ctx, log)
        log.info("reloaded %d macros from %s", len(new_defs), path)
    except ValueError as exc:
        log.warning("failed to reload macros: %s", exc)


def _reload_autoreplies(
    ctx: "SessionContext", path: str, session_key: str, log: logging.Logger
) -> None:
    """Reload autoreply rules from disk after editing."""
    if not os.path.exists(path):
        return
    from .autoreply import load_autoreplies

    try:
        ctx.autoreply_rules = load_autoreplies(path, session_key)
        ctx.autoreplies_file = path
        n_rules = len(ctx.autoreply_rules)
        log.info("reloaded %d autoreplies from %s", n_rules, path)
    except ValueError as exc:
        log.warning("failed to reload autoreplies: %s", exc)


def _launch_room_browser(ctx: "SessionContext", replay_buf: Optional[Any] = None) -> None:
    """
    Launch the room browser TUI in a subprocess.

    On return, check for a fast travel file and queue movement commands.

    :param ctx: Session context with session attributes.
    :param replay_buf: Optional replay buffer for screen repaint on return.
    """
    import subprocess

    from .client_repl import _get_term, _blocking_fds, _terminal_cleanup, _restore_after_subprocess
    from .client_repl_travel import _fast_travel

    session_key = ctx.session_key
    if not session_key:
        return

    from .rooms import rooms_path as _rooms_path_fn
    from .rooms import fasttravel_path as _fasttravel_path_fn
    from .rooms import read_fasttravel
    from .rooms import current_room_path as _current_room_path_fn

    rp = ctx.rooms_file or _rooms_path_fn(session_key)
    crp = ctx.current_room_file or _current_room_path_fn(session_key)
    ftp = _fasttravel_path_fn(session_key)

    logfile = _get_logfile_path()
    cmd = [
        sys.executable,
        "-c",
        "import sys; from telnetlib3.client_tui import edit_rooms_main; "
        "edit_rooms_main(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4],"
        " logfile=sys.argv[5])",
        rp,
        session_key,
        crp,
        ftp,
        logfile,
    ]

    log = logging.getLogger(__name__)

    global _editor_active  # noqa: PLW0603
    log.debug(
        "room_browser: pre-subprocess fd0_blocking=%s fd1=%s fd2=%s "
        "stdin_isatty=%s stderr_isatty=%s "
        "TERM=%s COLORTERM=%s terminal_size=%s",
        os.get_blocking(0),
        os.get_blocking(1),
        os.get_blocking(2),
        sys.stdin.isatty(),
        sys.__stderr__.isatty(),
        os.environ.get("TERM", ""),
        os.environ.get("COLORTERM", ""),
        _safe_terminal_size(),
    )
    t = _get_term()
    sys.stdout.write(_terminal_cleanup())
    sys.stdout.write(t.change_scroll_region(0, t.height - 1))
    sys.stdout.flush()
    sys.stderr.flush()
    sys.__stderr__.flush()
    _editor_active = True
    try:
        with _blocking_fds():
            subprocess.run(cmd, check=False)
    except FileNotFoundError:
        log.warning("could not launch room browser subprocess")
    finally:
        _editor_active = False
        _restore_after_subprocess(replay_buf)

    room_graph = ctx.room_graph
    if room_graph is not None:
        room_graph._load_adjacency()

    steps, slow = read_fasttravel(ftp)
    if steps:
        log.debug("fast travel: scheduling %d steps (slow=%s)", len(steps), slow)
        asyncio.ensure_future(_fast_travel(steps, ctx, log, slow=slow))
