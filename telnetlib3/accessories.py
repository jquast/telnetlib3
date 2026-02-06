"""Accessory functions."""

from __future__ import annotations

# std imports
import shlex
import asyncio
import logging
import importlib
from typing import TYPE_CHECKING, Any, Dict, Union, Mapping, Callable, Optional

if TYPE_CHECKING:  # pragma: no cover
    # local
    from .stream_reader import TelnetReader, TelnetReaderUnicode

__all__ = (
    "encoding_from_lang",
    "name_unicode",
    "eightbits",
    "make_logger",
    "repr_mapping",
    "function_lookup",
    "make_reader_task",
)


def get_version() -> str:
    """Return the current version of telnetlib3."""
    return "2.2.0"  # keep in sync with setup.py and docs/conf.py !!


def encoding_from_lang(lang: str) -> Optional[str]:
    """
    Parse encoding from LANG environment value.

    Returns the encoding portion if present, or None if the LANG value
    does not contain an encoding suffix (no '.' separator).

    :param str lang: LANG environment value (e.g., 'en_US.UTF-8@misc')
    :returns: Encoding string (e.g., 'UTF-8') or None if no encoding found
    :rtype: str or None

    Example::

        >>> encoding_from_lang('en_US.UTF-8@misc')
        'UTF-8'
        >>> encoding_from_lang('en_IL')
        None
    """
    if "." not in lang:
        return None
    _, encoding = lang.split(".", 1)
    if "@" in encoding:
        encoding, _ = encoding.split("@", 1)
    return encoding


def name_unicode(ucs: str) -> str:
    """Return 7-bit ascii printable of any string."""
    # more or less the same as curses.ascii.unctrl -- but curses
    # module is conditionally excluded from many python distributions!
    bits = ord(ucs)
    if 32 <= bits <= 126:
        # ascii printable as one cell, as-is
        rep = chr(bits)
    elif bits == 127:
        rep = "^?"
    elif bits < 32:
        rep = "^" + chr(((bits & 0x7F) | 0x20) + 0x20)
    else:
        rep = rf"\x{bits:02x}"
    return rep


def eightbits(number: int) -> str:
    """
    Binary representation of ``number`` padded to 8 bits.

    Example::

        >>> eightbits(ord('a'))
        '0b01100001'
    """
    # useful only so far in context of a forwardmask or any bitmask.
    _, value = bin(number).split("b")
    return f"0b{int(value):08d}"


_DEFAULT_LOGFMT = " ".join(
    ("%(asctime)s", "%(levelname)s", "%(filename)s:%(lineno)d", "%(message)s")
)


def make_logger(
    name: str,
    loglevel: str = "info",
    logfile: Optional[str] = None,
    logfmt: str = _DEFAULT_LOGFMT,
) -> logging.Logger:
    """Create and return simple logger for given arguments."""
    lvl = getattr(logging, loglevel.upper())

    _cfg: Dict[str, Any] = {"format": logfmt}
    if logfile:
        _cfg["filename"] = logfile
    logging.basicConfig(**_cfg)
    logging.getLogger().setLevel(lvl)
    logging.getLogger(name).setLevel(lvl)
    return logging.getLogger(name)


def repr_mapping(mapping: Mapping[str, Any]) -> str:
    """Return printable string, 'key=value [key=value ...]' for mapping."""
    return " ".join(f"{key}={shlex.quote(str(value))}" for key, value in mapping.items())


def function_lookup(pymod_path: str) -> Callable[..., Any]:
    """Return callable function target from standard module.function path."""
    module_name, func_name = pymod_path.rsplit(".", 1)
    module = importlib.import_module(module_name)
    shell_function = getattr(module, func_name)
    assert callable(shell_function), shell_function
    return shell_function


def make_reader_task(
    reader: "Union[TelnetReader, TelnetReaderUnicode]",
    size: int = 2**12,
) -> "asyncio.Task[Any]":
    """Return asyncio task wrapping coroutine of reader.read(size)."""
    return asyncio.ensure_future(reader.read(size))
