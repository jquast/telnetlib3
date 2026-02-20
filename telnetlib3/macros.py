"""
Macro key binding support for the REPL client.

Provides :class:`Macro` for representing key-to-text bindings and
:func:`bind_macros` for registering them on a prompt_toolkit
:class:`~prompt_toolkit.key_binding.KeyBindings` instance.
"""

from __future__ import annotations

# std imports
import json
import logging
from typing import Any, Union
from dataclasses import dataclass

# local
from .stream_writer import TelnetWriter, TelnetWriterUnicode

__all__ = ("Macro", "load_macros", "save_macros", "bind_macros")

_CR_TOKEN = "<CR>"


@dataclass
class Macro:
    """
    A single key-to-text macro binding.

    :param keys: Sequence of prompt_toolkit key names.
    :param text: Text to insert/send, with ``<CR>`` as send markers.
    """

    keys: tuple[str, ...]
    text: str


def _parse_entries(entries: list[dict[str, str]]) -> list[Macro]:
    """Parse a list of macro entry dicts into :class:`Macro` instances."""
    macros: list[Macro] = []
    for entry in entries:
        key_str = entry.get("key", "").strip()
        text = entry.get("text", "")
        if not key_str:
            continue
        keys = tuple(key_str.split())
        macros.append(Macro(keys=keys, text=text))
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
    import os  # pylint: disable=import-outside-toplevel

    data: dict[str, Any] = {}
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)

    data[session_key] = {
        "macros": [{"key": " ".join(m.keys), "text": m.text} for m in macros]
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def bind_macros(
    kb: Any,
    macros: list[Macro],
    writer: Union[TelnetWriter, TelnetWriterUnicode],
    log: logging.Logger,
) -> None:
    r"""
    Register macro key bindings on a prompt_toolkit KeyBindings instance.

    For each macro, adds a handler that splits ``text`` on ``<CR>`` markers
    and sends each segment followed by ``\r\n`` to the writer.  Text after
    the last ``<CR>`` (if any) is inserted into the current prompt buffer
    without sending.

    :param kb: prompt_toolkit ``KeyBindings`` to register on.
    :param macros: Macro definitions to bind.
    :param writer: Telnet writer for sending commands.
    :param log: Logger instance.
    """
    for macro in macros:
        _bind_one(kb, macro, writer, log)


def _bind_one(
    kb: Any, macro: Macro, writer: Union[TelnetWriter, TelnetWriterUnicode], log: logging.Logger
) -> None:
    """
    Bind a single macro to the KeyBindings instance.

    :param kb: prompt_toolkit ``KeyBindings``.
    :param macro: Macro to bind.
    :param writer: Telnet writer.
    :param log: Logger instance.
    """
    keys = macro.keys
    text = macro.text

    try:

        @kb.add(*keys)  # type: ignore[untyped-decorator]
        def _handler(event: Any, _text: str = text) -> None:
            parts = _text.split(_CR_TOKEN)
            for i, part in enumerate(parts):
                if i < len(parts) - 1:
                    log.info("macro: sending %r", part)
                    writer.write(part + "\r\n")  # type: ignore[arg-type]
                elif part:
                    event.app.current_buffer.insert_text(part)

    except (ValueError, KeyError) as exc:
        log.warning("macro: could not bind %s: %s", keys, exc)
