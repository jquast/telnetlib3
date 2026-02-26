"""Command expansion, queuing, chained command sending, and macro execution."""

# std imports
import re
import asyncio
import logging
from typing import TYPE_CHECKING, Any, NamedTuple, Optional

if TYPE_CHECKING:
    from .session_context import SessionContext, _CommandQueue

# local
from .client_repl_render import _ELLIPSIS, _get_term, _wcswidth

_REPEAT_RE = re.compile(r"^(\d+)([A-Za-z].*)$")
_BACKTICK_RE = re.compile(r"`[^`]*`")

_WHEN_RE = re.compile(
    r"^`when\s+(HP%|MP%)\s*(>=|<=|>|<|=)\s*(\d+)`$", re.IGNORECASE
)
_UNTIL_RE = re.compile(r"^`until(?:\s+(\d+(?:\.\d+)?))?\s+(.+)`$")
_UNTILS_RE = re.compile(r"^`untils(?:\s+(\d+(?:\.\d+)?))?\s+(.+)`$")


class ExpandedCommands(NamedTuple):
    """Result of :func:`expand_commands_ex`.

    :param commands: Flat list of individual commands.
    :param immediate_set: Indices of commands whose preceding separator
        was ``:`` (send immediately, no GA/EOR wait).
    """

    commands: list[str]
    immediate_set: frozenset[int]


def expand_commands(line: str) -> list[str]:
    """
    Split *line* on ``;`` (outside backticks) and expand repeat prefixes.

    Backtick-enclosed tokens (e.g. ```fast travel 123```, ```delay 1s```)
    are preserved verbatim -- they are not split on ``;`` and repeat
    expansion is not applied.

    A segment like ``5e`` becomes ``['e', 'e', 'e', 'e', 'e']``.
    Only a leading integer followed immediately by an alphabetic
    character triggers expansion (e.g. ``5east`` -> 5 × ``east``).
    Segments without a leading digit are passed through unchanged.

    :param line: Raw user input line.
    :returns: Flat list of individual commands.
    """
    # Replace backtick tokens with placeholders to protect from ; splitting.
    placeholders: list[str] = []

    def _replace_bt(m: re.Match[str]) -> str:
        placeholders.append(m.group(0))
        return f"\x00BT{len(placeholders) - 1}\x00"

    protected = _BACKTICK_RE.sub(_replace_bt, line)
    parts = protected.split(";") if ";" in protected else [protected]
    result: list[str] = []
    for part in parts:
        stripped = part.strip()
        if not stripped:
            continue
        # Restore backtick placeholders.
        while "\x00BT" in stripped:
            for i, orig in enumerate(placeholders):
                stripped = stripped.replace(f"\x00BT{i}\x00", orig)
        if stripped.startswith("`") and stripped.endswith("`"):
            result.append(stripped)
            continue
        m = _REPEAT_RE.match(stripped)
        if m:
            count = min(int(m.group(1)), 200)
            cmd = m.group(2)
            result.extend([cmd] * count)
        else:
            result.append(stripped)
    return result


_TRAVEL_RE = re.compile(
    r"^`(fast travel|slow travel|return fast|return slow"
    r"|autodiscover|randomwalk|resume)\s*(.*?)`$",
    re.IGNORECASE,
)

_COMMAND_DELAY = 0.25
_MOVE_MAX_RETRIES = 2


def _collapse_runs(commands: list[str], start: int = 0) -> list[tuple[str, int, int]]:
    """
    Collapse consecutive identical commands into display groups.

    :param commands: Full command list.
    :param start: Index to start collapsing from (earlier entries are skipped).
    :returns: List of ``(display_text, start_idx, end_idx)`` tuples.
    """
    if start >= len(commands):
        return []
    runs: list[tuple[str, int, int]] = []
    i = start
    while i < len(commands):
        cmd = commands[i]
        j = i
        while j + 1 < len(commands) and commands[j + 1] == cmd:
            j += 1
        count = j - i + 1
        text = f"{count}\u00d7{cmd}" if count > 1 else cmd
        runs.append((text, i, j))
        i = j + 1
    return runs


def _render_active_command(command: str, scroll: "Any", out: "asyncio.StreamWriter") -> None:
    """Render a single highlighted active command on the input row."""
    blessed_term = _get_term()
    cols = blessed_term.width
    active_sgr = blessed_term.on_color_rgb(255, 255, 255) + blessed_term.color_rgb(0, 0, 0)
    normal = blessed_term.normal

    text = command[: cols - 1] if _wcswidth(command) >= cols else command
    w = _wcswidth(text)

    out.write(blessed_term.move_yx(scroll.input_row, 0).encode())
    out.write(f"{active_sgr}{text}{normal}".encode())
    pad = cols - w
    if pad > 0:
        out.write((" " * pad).encode())
    out.write(normal.encode())


def _clear_command_queue(ctx: "SessionContext") -> None:
    """Remove the command queue from *ctx* when chained send completes."""
    cq = ctx.command_queue
    if cq is not None:
        ctx.command_queue = None


def _render_command_queue(
    queue: "Optional[_CommandQueue]", scroll: "Any", out: "asyncio.StreamWriter"
) -> None:
    """
    Render the command queue on the input row.

    The active run is highlighted with paper-white background / black foreground.  Pending runs use
    dim grey.  If the display is too wide it is truncated with an ellipsis.
    """
    if queue is None:
        return
    blessed_term = _get_term()
    cols = blessed_term.width

    runs = _collapse_runs(queue.commands, queue.current_idx)
    if not runs:
        return

    active_sgr = blessed_term.on_color_rgb(255, 255, 255) + blessed_term.color_rgb(0, 0, 0)
    pending_sgr = blessed_term.color_rgb(120, 120, 120)
    normal = blessed_term.normal

    # Build fragments: (sgr, text) for each run.
    frags: list[tuple[str, str]] = []
    for text, start_idx, _end_idx in runs:
        is_active = start_idx <= queue.current_idx <= _end_idx
        sgr = active_sgr if is_active else pending_sgr
        frags.append((sgr, text))

    sep = " "
    total_w = 0
    built: list[tuple[str, str]] = []
    for idx, (sgr, text) in enumerate(frags):
        w = _wcswidth(text) + (1 if idx > 0 else 0)
        if total_w + w > cols - 1 and built:
            built.append((pending_sgr, _ELLIPSIS))
            total_w += 1
            break
        if idx > 0:
            built.append(("", sep))
        built.append((sgr, text))
        total_w += w

    out.write(blessed_term.move_yx(scroll.input_row, 0).encode())
    for sgr, text in built:
        out.write(f"{sgr}{text}{normal}".encode())
    pad = cols - total_w
    if pad > 0:
        out.write((" " * pad).encode())
    out.write(normal.encode())


async def _send_chained(
    commands: list[str],
    ctx: "SessionContext",
    log: logging.Logger,
    queue: "Optional[_CommandQueue]" = None,
) -> None:
    """
    Send multiple commands with GA/EOR pacing between each.

    The first command is assumed to have already been sent by the caller.
    This coroutine sends commands 2..N, waiting for the server prompt
    signal before each one.

    When all commands in the list are identical (e.g. ``9e`` expanded to
    nine ``e`` commands), movement retry logic is applied: if the room
    does not change after a command, the same command is retried up to
    :data:`_MOVE_MAX_RETRIES` times with a delay between attempts.

    :param commands: List of commands (index 1+ will be sent).
    :param ctx: Session context.
    :param log: Logger.
    :param queue: Optional command queue for display and cancellation.
    """
    wait_fn = ctx.wait_for_prompt
    echo_fn = ctx.echo_command
    prompt_ready = ctx.prompt_ready
    room_changed = ctx.room_changed

    is_repeated = len(commands) > 1 and len(set(commands)) == 1

    async def _cancellable_sleep(delay: float) -> bool:
        """Sleep for *delay* seconds, returning ``True`` if cancelled."""
        if queue is None:
            await asyncio.sleep(delay)
            return False
        try:
            await asyncio.wait_for(queue.cancel_event.wait(), timeout=delay)
            return True
        except asyncio.TimeoutError:
            return False

    for _idx, cmd in enumerate(commands[1:], 1):
        if queue is not None:
            if queue.cancelled:
                return
            queue.current_idx = _idx
            queue.render()

        # Detect runs of identical commands (e.g. "9e;6n" expands to
        # e,e,...,n,n,...) — these need movement pacing even in mixed
        # lists.  A command is "repeated" if it matches the previous one.
        prev_cmd = commands[_idx - 1] if _idx > 0 else ""
        use_move_pacing = is_repeated or cmd == prev_cmd
        prev_room = ctx.current_room_num if use_move_pacing else ""

        if not use_move_pacing:
            # Mixed commands: GA/EOR pacing only.
            if prompt_ready is not None:
                prompt_ready.clear()
            if wait_fn is not None:
                await wait_fn()
            log.debug("chained command: %r", cmd)
            if echo_fn is not None:
                echo_fn(cmd)
            if ctx.cx_dot is not None:
                ctx.cx_dot.trigger()
            if ctx.tx_dot is not None:
                ctx.tx_dot.trigger()
            ctx.writer.write(cmd + "\r\n")  # type: ignore[arg-type]
            continue

        # Repeated commands: delay + room-change pacing with retry.
        for attempt in range(_MOVE_MAX_RETRIES + 1):
            if queue is not None and queue.cancelled:
                return
            # Always delay -- the first repeated command needs spacing
            # from the caller's initial send, and retries need a longer
            # back-off to respect the server's rate limit.
            delay = _COMMAND_DELAY if attempt == 0 else 1.0
            if await _cancellable_sleep(delay):
                return
            if room_changed is not None:
                room_changed.clear()
            if prompt_ready is not None:
                prompt_ready.clear()
            if attempt == 0:
                log.debug("chained command: %r", cmd)
                if echo_fn is not None:
                    echo_fn(cmd)
            else:
                log.info("chained retry %d: %r", attempt, cmd)
            if ctx.cx_dot is not None:
                ctx.cx_dot.trigger()
            if ctx.tx_dot is not None:
                ctx.tx_dot.trigger()
            ctx.writer.write(cmd + "\r\n")  # type: ignore[arg-type]

            if not prev_room:
                break

            # Wait briefly for room change -- GMCP typically arrives
            # within 100-200ms.  A short timeout keeps movement brisk
            # while still detecting rate-limit rejections.
            actual = ctx.current_room_num
            if actual != prev_room:
                break
            if room_changed is not None:
                try:
                    await asyncio.wait_for(room_changed.wait(), timeout=0.5)
                except asyncio.TimeoutError:
                    pass
                actual = ctx.current_room_num
            if actual != prev_room:
                break
            if attempt < _MOVE_MAX_RETRIES:
                log.info(
                    "room unchanged after %r, retrying (%d/%d)", cmd, attempt + 1, _MOVE_MAX_RETRIES
                )
            else:
                log.warning(
                    "room unchanged after %r, giving up after %d retries", cmd, _MOVE_MAX_RETRIES
                )
                return


async def execute_macro_commands(text: str, ctx: "SessionContext", log: logging.Logger) -> None:
    """
    Execute a macro text string, handling travel and delay commands.

    Expands the text with :func:`expand_commands`, then processes each
    part -- backtick-enclosed travel commands are routed through
    :func:`_handle_travel_commands`, delay commands pause execution,
    and plain commands are sent to the server with GA/EOR pacing.

    :param text: Raw macro text with ``;`` separators.
    :param ctx: Session context.
    :param log: Logger.
    """
    from .autoreply import _DELAY_RE
    from .client_repl_travel import _handle_travel_commands

    parts = expand_commands(text)
    if not parts:
        return

    # snapshot starting room so ``return fast`` can navigate back
    ctx.macro_start_room = ctx.current_room_num

    wait_fn = ctx.wait_for_prompt
    echo_fn = ctx.echo_command
    prompt_ready = ctx.prompt_ready

    idx = 0
    while idx < len(parts):
        cmd = parts[idx]

        # Travel command -- hand off the rest to _handle_travel_commands.
        if _TRAVEL_RE.match(cmd):
            remainder = await _handle_travel_commands(parts[idx:], ctx, log)
            # remainder contains post-travel commands; continue processing.
            parts = remainder
            idx = 0
            continue

        # Delay command.
        dm = _DELAY_RE.match(cmd)
        if dm:
            value = float(dm.group(1))
            unit = dm.group(2)
            delay = value / 1000.0 if unit == "ms" else value
            if delay > 0:
                await asyncio.sleep(delay)
            idx += 1
            continue

        # Plain command -- send with pacing.
        if idx > 0:
            if prompt_ready is not None:
                prompt_ready.clear()
            if wait_fn is not None:
                await wait_fn()
        log.info("macro: sending %r", cmd)
        if echo_fn is not None:
            echo_fn(cmd)
        if ctx.cx_dot is not None:
            ctx.cx_dot.trigger()
        if ctx.tx_dot is not None:
            ctx.tx_dot.trigger()
        ctx.writer.write(cmd + "\r\n")  # type: ignore[arg-type]
        idx += 1

    ctx.macro_start_room = ""
