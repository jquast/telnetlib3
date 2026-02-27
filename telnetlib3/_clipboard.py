"""System clipboard helpers (OSC 52 for copy, subprocess for paste)."""

import base64
import subprocess
import sys
from typing import Optional


def copy_to_clipboard(text: str, file: Optional[object] = None) -> None:
    """Write *text* to the system clipboard via an OSC 52 escape sequence.

    :param text: The text to copy.
    :param file: Writable binary stream (default ``sys.stdout.buffer``).

    The sequence ``ESC ] 52 ; c ; <base64> BEL`` is understood by most
    modern terminal emulators (xterm, iTerm2, kitty, WezTerm, …).
    """
    out = file if file is not None else sys.stdout.buffer
    payload = base64.b64encode(text.encode("utf-8")).decode("ascii")
    out.write(f"\x1b]52;c;{payload}\a".encode("ascii"))  # type: ignore[union-attr]
    out.flush()  # type: ignore[union-attr]


_PASTE_COMMANDS = (
    ("xclip", "-selection", "clipboard", "-o"),
    ("xsel", "--clipboard", "--output"),
    ("wl-paste", "--no-newline"),
    ("pbpaste",),
)


def paste_from_clipboard() -> str:
    """Read text from the system clipboard via an external helper.

    Tries, in order: ``xclip``, ``xsel``, ``wl-paste``, ``pbpaste``.
    Returns ``""`` when no clipboard tool is available.
    """
    for cmd in _PASTE_COMMANDS:
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                timeout=2,
                check=False,
            )
            if result.returncode == 0:
                return result.stdout.decode("utf-8", errors="replace")
        except FileNotFoundError:
            continue
    return ""
