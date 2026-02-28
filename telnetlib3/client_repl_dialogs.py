"""TUI subprocess management: confirmation dialogs, help screen, editor launchers."""

# std imports
import os
import sys
import asyncio
import logging
from typing import TYPE_CHECKING, Any, Optional

# local
from ._paths import _safe_terminal_size

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


def _confirm_dialog(
    title: str, body: str, warning: str = "", replay_buf: Optional[Any] = None
) -> bool:
    """
    Show a Textual confirmation dialog in a subprocess.

    Launches :func:`telnetlib3.client_tui.confirm_dialog_main` as a
    subprocess, reads the result from a temporary file, and restores
    terminal state on return.

    :param title: Dialog title.
    :param body: Body text.
    :param warning: Optional warning text displayed in red.
    :param replay_buf: Optional replay buffer for screen repaint.
    :returns: Whether the user confirmed.
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
    blessed_term = _get_term()
    sys.stdout.write(_terminal_cleanup())
    sys.stdout.write(blessed_term.change_scroll_region(0, blessed_term.height - 1))
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
    try:
        with open(result_path, "r", encoding="utf-8") as f:
            data = _json.load(f)
        confirmed = bool(data.get("confirmed", False))
    except (OSError, ValueError):
        pass
    finally:
        try:
            os.unlink(result_path)
        except OSError:
            pass

    return confirmed


def _randomwalk_dialog(replay_buf: Optional[Any] = None, session_key: str = "") -> Optional[str]:
    """
    Show the random walk dialog with visit-level parameter.

    Loads saved preferences from *session_key* (if provided) as defaults,
    and saves the user's choices back on confirmation.

    :param replay_buf: Optional replay buffer for screen repaint.
    :param session_key: Session key for loading/saving preferences.
    :returns: Command string (e.g. ``"`randomwalk 2 autosearch`"``) on
        confirm, or ``None`` on cancel.
    """
    import json as _json
    import tempfile
    import subprocess

    from .client_repl import _get_term, _blocking_fds, _terminal_cleanup, _restore_after_subprocess

    default_visit_level = 2
    default_auto_search = False
    default_auto_evaluate = False
    if session_key:
        from .rooms import load_prefs

        prefs = load_prefs(session_key)
        default_visit_level = int(prefs.get("randomwalk_visit_level", 2))
        default_auto_search = bool(prefs.get("randomwalk_auto_search", False))
        default_auto_evaluate = bool(prefs.get("randomwalk_auto_evaluate", False))

    fd, result_path = tempfile.mkstemp(suffix=".json", prefix="randomwalk-")
    os.close(fd)

    logfile = _get_logfile_path()
    cmd = [
        sys.executable,
        "-c",
        "import sys; from telnetlib3.client_tui import randomwalk_dialog_main; "
        "randomwalk_dialog_main(result_file=sys.argv[1],"
        " default_visit_level=sys.argv[2],"
        " default_auto_search=sys.argv[3],"
        " default_auto_evaluate=sys.argv[4],"
        " logfile=sys.argv[5])",
        result_path,
        str(default_visit_level),
        "1" if default_auto_search else "0",
        "1" if default_auto_evaluate else "0",
        logfile,
    ]

    global _editor_active  # noqa: PLW0603
    log = logging.getLogger(__name__)
    log.debug("randomwalk_dialog: launching subprocess")
    blessed_term = _get_term()
    sys.stdout.write(_terminal_cleanup())
    sys.stdout.write(blessed_term.change_scroll_region(0, blessed_term.height - 1))
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

    try:
        with open(result_path, "r", encoding="utf-8") as f:
            data = _json.load(f)
        if not data.get("confirmed", False):
            return None
        if session_key:
            from .rooms import load_prefs as _load_prefs
            from .rooms import save_prefs

            save_data = _load_prefs(session_key)
            save_data["randomwalk_visit_level"] = int(data.get("visit_level", default_visit_level))
            save_data["randomwalk_auto_search"] = bool(data.get("auto_search", default_auto_search))
            save_data["randomwalk_auto_evaluate"] = bool(
                data.get("auto_evaluate", default_auto_evaluate)
            )
            save_prefs(session_key, save_data)
        return str(data.get("command", f"`randomwalk 999 {default_visit_level}`"))
    except (OSError, ValueError):
        return None
    finally:
        try:
            os.unlink(result_path)
        except OSError:
            pass


def _strip_md(text: str) -> str:
    """Strip markdown bold/code markers from text."""
    import re

    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"`(.+?)`", r"\1", text)
    return text.strip()


def _render_help_md(has_gmcp: bool = False) -> list[str]:
    """
    Render keybindings help markdown into plain-text lines.

    :param has_gmcp: Whether GMCP room data is available.
    :rtype: list[str]
    """
    from .help import get_help

    md = get_help("keybindings")
    lines: list[str] = []
    in_header_row = False
    skip_section = False
    for raw in md.splitlines():
        stripped = raw.strip()
        if stripped.startswith("##"):
            heading = stripped.lstrip("# ").strip()
            if not has_gmcp and "GMCP" in heading:
                skip_section = True
                continue
            skip_section = False
            lines.append("")
            lines.append("  " + heading)
            lines.append("")
            in_header_row = True
        elif skip_section:
            continue
        elif stripped.startswith("|") and "---" in stripped:
            continue
        elif stripped.startswith("|"):
            cells = [_strip_md(c) for c in stripped.split("|")[1:-1]]
            if in_header_row:
                in_header_row = False
                continue
            if len(cells) >= 2 and cells[0]:
                lines.append(f"  {cells[0]:<16}{cells[1]}")
        elif stripped and not stripped.startswith("|"):
            lines.append("  " + _strip_md(stripped))
        elif lines and lines[-1] != "":
            lines.append("")
    return lines


def _show_help(
    macro_defs: "Any" = None, replay_buf: Optional[Any] = None, has_gmcp: bool = False
) -> None:
    """
    Launch the keybindings help viewer as a Textual TUI subprocess.

    :param macro_defs: Unused (kept for API compatibility).
    :param replay_buf: Optional replay buffer for screen repaint on return.
    :param has_gmcp: Unused (kept for API compatibility).
    """
    import subprocess

    from .client_repl import _get_term, _blocking_fds, _terminal_cleanup, _restore_after_subprocess

    logfile = _get_logfile_path()
    cmd = [
        sys.executable,
        "-c",
        "import sys; from telnetlib3.client_tui import show_help_main; "
        "show_help_main(topic=sys.argv[1], logfile=sys.argv[2])",
        "keybindings",
        logfile,
    ]

    log = logging.getLogger(__name__)
    global _editor_active  # noqa: PLW0603
    blessed_term = _get_term()
    sys.stdout.write(_terminal_cleanup())
    sys.stdout.write(blessed_term.change_scroll_region(0, blessed_term.height - 1))
    sys.stdout.flush()
    sys.stderr.flush()
    sys.__stderr__.flush()
    _editor_active = True
    try:
        with _blocking_fds():
            subprocess.run(cmd, check=False)
    except FileNotFoundError:
        log.warning("could not launch help viewer subprocess")
    finally:
        _editor_active = False
        _restore_after_subprocess(replay_buf)


def _launch_tui_editor(
    editor_type: str, ctx: "SessionContext", replay_buf: Optional[Any] = None
) -> None:
    """
    Launch a TUI editor for macros or autoreplies in a subprocess.

    :param editor_type: ``"macros"``, ``"autoreplies"``, or ``"highlights"``.
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
    elif editor_type == "highlights":
        path = ctx.highlights_file or os.path.join(_config_dir, "highlights.json")
        cmd = [
            sys.executable,
            "-c",
            "import sys; from telnetlib3.client_tui import edit_highlights_main; "
            "edit_highlights_main(sys.argv[1], sys.argv[2], logfile=sys.argv[3])",
            path,
            session_key,
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
    blessed_term = _get_term()
    sys.stdout.write(_terminal_cleanup())
    sys.stdout.write(blessed_term.change_scroll_region(0, blessed_term.height - 1))
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
    elif editor_type == "highlights":
        _reload_highlights(ctx, path, session_key, log)
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


def _reload_highlights(
    ctx: "SessionContext", path: str, session_key: str, log: logging.Logger
) -> None:
    """Reload highlight rules from disk after editing."""
    if not os.path.exists(path):
        return
    from .highlighter import load_highlights

    try:
        ctx.highlight_rules = load_highlights(path, session_key)
        ctx.highlights_file = path
        n_rules = len(ctx.highlight_rules)
        log.info("reloaded %d highlights from %s", n_rules, path)
    except ValueError as exc:
        log.warning("failed to reload highlights: %s", exc)


def _launch_chat_viewer(ctx: "SessionContext", replay_buf: Optional[Any] = None) -> None:
    """
    Launch the chat viewer TUI in a subprocess.

    :param ctx: Session context with chat state.
    :param replay_buf: Optional replay buffer for screen repaint on return.
    """
    import subprocess

    from .client_repl import _get_term, _blocking_fds, _terminal_cleanup, _restore_after_subprocess

    session_key = ctx.session_key
    if not session_key:
        return

    chat_file = ctx.chat_file
    if not chat_file:
        return

    ctx.chat_unread = 0

    # Find the channel with the most recent message for initial focus.
    initial_channel = ""
    if ctx.chat_messages:
        last_msg = ctx.chat_messages[-1]
        initial_channel = last_msg.get("channel", "")

    logfile = _get_logfile_path()
    cmd = [
        sys.executable,
        "-c",
        "import sys; from telnetlib3.client_tui import chat_viewer_main; "
        "chat_viewer_main(sys.argv[1], sys.argv[2],"
        " initial_channel=sys.argv[3], logfile=sys.argv[4])",
        chat_file,
        session_key,
        initial_channel,
        logfile,
    ]

    log = logging.getLogger(__name__)

    global _editor_active  # noqa: PLW0603
    log.debug("chat_viewer: launching subprocess")
    blessed_term = _get_term()
    sys.stdout.write(_terminal_cleanup())
    sys.stdout.write(blessed_term.change_scroll_region(0, blessed_term.height - 1))
    sys.stdout.flush()
    sys.stderr.flush()
    sys.__stderr__.flush()
    _editor_active = True
    try:
        with _blocking_fds():
            subprocess.run(cmd, check=False)
    except FileNotFoundError:
        log.warning("could not launch chat viewer subprocess")
    finally:
        _editor_active = False
        _restore_after_subprocess(replay_buf)


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
    blessed_term = _get_term()
    sys.stdout.write(_terminal_cleanup())
    sys.stdout.write(blessed_term.change_scroll_region(0, blessed_term.height - 1))
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
        task = asyncio.ensure_future(_fast_travel(steps, ctx, log, slow=slow))
        ctx.travel_task = task

        def _on_done(t: "asyncio.Task[None]") -> None:
            if ctx.travel_task is t:
                ctx.travel_task = None
            if not t.cancelled() and t.exception() is not None:
                log.warning("fast travel failed: %s", t.exception())

        task.add_done_callback(_on_done)
