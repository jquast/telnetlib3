"""
Consolidated XDG Base Directory paths for telnetlib3.

Provides config and data directory resolution following the `XDG Base Directory Specification
<https://specifications.freedesktop.org/basedir-spec/latest/>`_.

Constants are frozen at import time from environment variables.
"""

from __future__ import annotations

# std imports
import os
import hashlib
import pathlib
import tempfile

_XDG_CONFIG = os.environ.get("XDG_CONFIG_HOME", os.path.join(os.path.expanduser("~"), ".config"))
_XDG_DATA = os.environ.get(
    "XDG_DATA_HOME", os.path.join(os.path.expanduser("~"), ".local", "share")
)

CONFIG_DIR = os.path.join(_XDG_CONFIG, "telnetlib3")
DATA_DIR = os.path.join(_XDG_DATA, "telnetlib3")

SESSIONS_FILE = pathlib.Path(CONFIG_DIR) / "sessions.json"
HISTORY_FILE = os.path.join(DATA_DIR, "history")


def safe_session_slug(session_key: str) -> str:
    """
    Return a filesystem-safe slug for *session_key*.

    Uses a SHA-256 hash (first 12 hex chars) to avoid path traversal
    and special-character issues with arbitrary hostnames.

    :param session_key: Session identifier, typically ``host:port``.
    :returns: 12-character hex string.
    """
    return hashlib.sha256(session_key.encode("utf-8")).hexdigest()[:12]


def history_path(session_key: str) -> str:
    """
    Return per-session history file path.

    :param session_key: Session identifier, typically ``host:port``.
    :returns: Absolute path under :data:`DATA_DIR`.
    """
    return os.path.join(DATA_DIR, f"history-{safe_session_slug(session_key)}")


def chat_path(session_key: str) -> str:
    """
    Return per-session chat history file path.

    :param session_key: Session identifier, typically ``host:port``.
    :returns: Absolute path under :data:`DATA_DIR`.
    """
    return os.path.join(DATA_DIR, f"chat-{safe_session_slug(session_key)}.json")


def xdg_config_dir() -> pathlib.Path:
    """Return the XDG config directory for telnetlib3."""
    return pathlib.Path(CONFIG_DIR)


def xdg_data_dir() -> pathlib.Path:
    """Return the XDG data directory for telnetlib3."""
    return pathlib.Path(DATA_DIR)


def _atomic_write(path: str, content: str) -> None:
    """
    Atomically write *content* to *path* via temp file and :func:`os.replace`.

    :param path: Target file path.
    :param content: String content to write.
    """
    dir_path = os.path.dirname(path)
    os.makedirs(dir_path, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=dir_path, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise
