"""
Macro key binding support for the REPL client.

Provides :class:`Macro` for representing key-to-text bindings and
:func:`build_macro_dispatch` for building a blessed key name to handler
mapping.

Keys are stored as blessed key names (e.g. ``KEY_F1``, ``KEY_ALT_E``)
or single characters, matching :attr:`blessed.keyboard.Keystroke.name`
and ``str(keystroke)`` respectively.
"""

from __future__ import annotations

# std imports
import json
import logging
from typing import Any
from dataclasses import dataclass

__all__ = ("Macro", "load_macros", "save_macros", "build_macro_dispatch")


@dataclass
class Macro:
    """
    A single key-to-text macro binding.

    :param key: Blessed key name (e.g. ``KEY_F5``, ``KEY_ALT_E``).
    :param text: Text to insert/send, with ``;`` as command separators.
    """

    key: str
    text: str
    enabled: bool = True
    last_used: str = ""


def _parse_entries(entries: list[dict[str, str]]) -> list[Macro]:
    """Parse a list of macro entry dicts into :class:`Macro` instances."""
    macros: list[Macro] = []
    for entry in entries:
        key = entry.get("key", "").strip()
        text = entry.get("text", "")
        if not key:
            continue
        enabled = bool(entry.get("enabled", True))
        last_used = str(entry.get("last_used", ""))
        macros.append(Macro(key=key, text=text, enabled=enabled, last_used=last_used))
    return macros


def load_macros(path: str, session_key: str) -> list[Macro]:
    """
    Load macro definitions for a session from a JSON file.

    The file is keyed by session (``"host:port"``).  Each value is
    an object with a ``"macros"`` list.

    :param path: Path to the macros JSON file.
    :param session_key: Session identifier (``"host:port"``).
    :returns: List of :class:`Macro` instances.
    :raises FileNotFoundError: When *path* does not exist.
    :raises ValueError: When JSON structure is invalid.
    """
    with open(path, "r", encoding="utf-8") as fh:
        data: dict[str, Any] = json.load(fh)

    session_data: dict[str, Any] = data.get(session_key, {})
    entries: list[dict[str, str]] = session_data.get("macros", [])
    return _parse_entries(entries)


def save_macros(path: str, macros: list[Macro], session_key: str) -> None:
    """
    Save macro definitions for a session to a JSON file.

    Other sessions' data in the file is preserved.

    :param path: Path to the macros JSON file.
    :param macros: List of :class:`Macro` instances to save.
    :param session_key: Session identifier (``"host:port"``).
    """
    import os

    data: dict[str, Any] = {}
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)

    data[session_key] = {
        "macros": [
            {
                "key": m.key,
                "text": m.text,
                **({"enabled": False} if not m.enabled else {}),
                **({"last_used": m.last_used} if m.last_used else {}),
            }
            for m in macros
        ]
    }
    from ._paths import _atomic_write

    content = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    _atomic_write(path, content)


def build_macro_dispatch(macros: list[Macro], ctx: Any, log: logging.Logger) -> dict[str, Any]:
    """
    Build a blessed key name to handler mapping from macro defs.

    Keys are matched directly against :attr:`~blessed.keyboard.Keystroke.name`
    (for named keys like ``KEY_F1``) or ``str(keystroke)`` (for single chars).
    Macros bound to keys in :data:`blessed.line_editor.DEFAULT_KEYMAP` are
    skipped with a warning.

    :param macros: Macro definitions to bind.
    :param ctx: :class:`~telnetlib3.session_context.SessionContext` instance.
    :param log: Logger instance.
    :returns: Dict mapping blessed key names (or raw chars) to handlers.
    """
    import asyncio
    from datetime import datetime, timezone

    from blessed.line_editor import DEFAULT_KEYMAP  # pylint: disable=no-name-in-module

    from .client_repl import execute_macro_commands

    result: dict[str, Any] = {}
    for macro in macros:
        if not macro.enabled:
            continue
        if macro.key in DEFAULT_KEYMAP:
            log.warning("macro %r conflicts with editor keymap, skipping", macro.key)
            continue
        text = macro.text
        _macro_ref = macro

        async def _handler(_text: str = text, _m: Macro = _macro_ref) -> None:
            _m.last_used = datetime.now(timezone.utc).isoformat()
            if hasattr(ctx, "mark_macros_dirty"):
                ctx.mark_macros_dirty()
            task = asyncio.ensure_future(execute_macro_commands(_text, ctx, log))

            def _on_done(t: "asyncio.Task[None]") -> None:
                if not t.cancelled() and t.exception() is not None:
                    log.warning("macro execution failed: %s", t.exception())

            task.add_done_callback(_on_done)

        result[macro.key] = _handler
    return result
