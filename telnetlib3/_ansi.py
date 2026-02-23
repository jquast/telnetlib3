"""Named constants for ANSI/VT escape sequences.

Replaces raw ``\\x1b[...]`` literals scattered across the codebase with
readable, self-documenting names.  Only sequences actually used by the
telnetlib3 client are included.
"""

CLEAR_SCREEN: str = "\x1b[2J"
HOME: str = "\x1b[H"
CLEAR_LINE: str = "\x1b[2K"
SCROLL_RESET: str = "\x1b[r"

SAVE_CURSOR: str = "\x1b7"
RESTORE_CURSOR: str = "\x1b8"
SHOW_CURSOR: str = "\x1b[?25h"

SGR_RESET: str = "\x1b[m"
ENTER_ALT_SCREEN: str = "\x1b[?1049h"
EXIT_ALT_SCREEN: str = "\x1b[?1049l"

DISABLE_MOUSE_BASIC: str = "\x1b[?1000l"
DISABLE_MOUSE_BUTTON: str = "\x1b[?1002l"
DISABLE_MOUSE_ALL: str = "\x1b[?1003l"
DISABLE_MOUSE_SGR: str = "\x1b[?1006l"

DISABLE_BRACKETED_PASTE: str = "\x1b[?2004l"

CURSOR_BLINKING_BAR: str = "\x1b[5 q"
CURSOR_DEFAULT: str = "\x1b[0 q"

SGR_CYAN: str = "\x1b[36m"
SGR_YELLOW: str = "\x1b[33m"

CLEAR_HOME: str = CLEAR_SCREEN + HOME
"""``CSI 2J CSI H`` -- erase display and move cursor to row 1 col 1."""

TERMINAL_CLEANUP: str = (
    SGR_RESET
    + SHOW_CURSOR
    + EXIT_ALT_SCREEN
    + DISABLE_MOUSE_BASIC
    + DISABLE_MOUSE_BUTTON
    + DISABLE_MOUSE_ALL
    + DISABLE_MOUSE_SGR
    + DISABLE_BRACKETED_PASTE
)
"""Reset terminal state after a subprocess that may have altered it."""


def cup(row: int, col: int) -> str:
    """CUP -- cursor position (1-indexed).

    :param row: 1-indexed row number.
    :param col: 1-indexed column number.
    :returns: ``CSI row ; col H`` escape sequence.
    """
    return f"\x1b[{row};{col}H"


def decstbm(top: int, bottom: int) -> str:
    """DECSTBM -- set top and bottom margins (scroll region).

    :param top: First row of the scroll region (1-indexed).
    :param bottom: Last row of the scroll region (1-indexed).
    :returns: ``CSI top ; bottom r`` escape sequence.
    """
    return f"\x1b[{top};{bottom}r"
