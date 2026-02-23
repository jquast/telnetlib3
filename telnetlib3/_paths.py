"""
Consolidated XDG Base Directory paths for telnetlib3.

Provides config and data directory resolution following the
`XDG Base Directory Specification
<https://specifications.freedesktop.org/basedir-spec/latest/>`_.
"""

from __future__ import annotations

import os
import pathlib

_XDG_CONFIG = os.environ.get(
    "XDG_CONFIG_HOME", os.path.join(os.path.expanduser("~"), ".config")
)
_XDG_DATA = os.environ.get(
    "XDG_DATA_HOME", os.path.join(os.path.expanduser("~"), ".local", "share")
)

CONFIG_DIR = os.path.join(_XDG_CONFIG, "telnetlib3")
DATA_DIR = os.path.join(_XDG_DATA, "telnetlib3")

SESSIONS_FILE = pathlib.Path(CONFIG_DIR) / "sessions.json"
HISTORY_FILE = os.path.join(DATA_DIR, "history")


def xdg_config_dir() -> pathlib.Path:
    """Return the XDG config directory for telnetlib3."""
    return pathlib.Path(CONFIG_DIR)


def xdg_data_dir() -> pathlib.Path:
    """Return the XDG data directory for telnetlib3."""
    return pathlib.Path(DATA_DIR)
