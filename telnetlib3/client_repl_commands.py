"""Command expansion, queuing, chained command sending, and macro execution."""

# std imports
import re
import asyncio
import logging
from time import monotonic as _monotonic
from typing import TYPE_CHECKING, Any, Optional, NamedTuple

if TYPE_CHECKING:
    from .session_context import SessionContext, _CommandQueue

# local
from .client_repl_render import _ELLIPSIS, _get_term, _wcswidth, _write_hint, _flash_bg_rgb

_REPEAT_RE = re.compile(r"^(\d+)([A-Za-z].*)$")
_BACKTICK_RE = re.compile(r"`[^`]*`")

_WHEN_RE = re.compile(r"^`when\s+(HP%|MP%)\s*(>=|<=|>|<|=)\s*(\d+)`$", re.IGNORECASE)
_UNTIL_RE = re.compile(r"^`until(?:\s+(\d+(?:\.\d+)?))?\s+(.+)`$")
_UNTILS_RE = re.compile(r"^`untils(?:\s+(\d+(?:\.\d+)?))?\s+(.+)`$")


class ExpandedCommands(NamedTuple):
    """
    Result of :func:`expand_commands_ex`.

    :param commands: Flat list of individual commands.
    :param immediate_set: Indices of commands whose preceding separator was ``|`` (send immediately,
        no GA/EOR wait).
    """

    commands: list[str]
    immediate_set: frozenset[int]


def expand_commands_ex(line: str) -> ExpandedCommands:
    r"""
    Split *line* on ``;`` and ``|`` (outside backticks) and expand repeat prefixes.

    Backtick-enclosed tokens (e.g. ```fast travel 123```, ```delay 1s```,
    ```until 4 died\\.```) are preserved verbatim -- they are not split
    on ``;`` or ``|`` and repeat expansion is not applied.

    ``;`` means *wait for GA/EOR* before the next command (default).
    ``|`` means *send immediately* without waiting.

    A segment like ``5e`` becomes ``['e', 'e', 'e', 'e', 'e']``.
    Only a leading integer followed immediately by an alphabetic
    character triggers expansion (e.g. ``5east`` -> 5 × ``east``).
    Segments without a leading digit are passed through unchanged.

    :param line: Raw user input line.
    :returns: :class:`ExpandedCommands` with commands and immediate indices.
    """
    placeholders: list[str] = []

    def _replace_bt(m: re.Match[str]) -> str:
        placeholders.append(m.group(0))
        return f"\x00BT{len(placeholders) - 1}\x00"

    protected = _BACKTICK_RE.sub(_replace_bt, line)

    # Split on ; and | while capturing the separator.
    _SEP_RE = re.compile(r"([;|])")
    tokens = _SEP_RE.split(protected)

    # tokens is alternating [segment, sep, segment, sep, ...].
    # Walk through tracking which separator precedes each segment.
    result: list[str] = []
    immediate_indices: set[int] = set()
    prev_sep = ";"  # first command has no preceding separator
    for tok_idx, tok in enumerate(tokens):
        if tok_idx % 2 == 1:
            # This is a separator.
            prev_sep = tok
            continue
        # This is a segment.
        stripped = tok.strip()
        if not stripped:
            continue

        # Restore backtick placeholders.
        while "\x00BT" in stripped:
            for i, orig in enumerate(placeholders):
                stripped = stripped.replace(f"\x00BT{i}\x00", orig)

        is_immediate = prev_sep == "|"

        if stripped.startswith("`") and stripped.endswith("`"):
            cmd_idx = len(result)
            result.append(stripped)
            if is_immediate:
                immediate_indices.add(cmd_idx)
            continue

        m = _REPEAT_RE.match(stripped)
        if m:
            count = min(int(m.group(1)), 200)
            cmd = m.group(2)
            first_idx = len(result)
            result.extend([cmd] * count)
            if is_immediate:
                immediate_indices.add(first_idx)
        else:
            cmd_idx = len(result)
            result.append(stripped)
            if is_immediate:
                immediate_indices.add(cmd_idx)

    return ExpandedCommands(result, frozenset(immediate_indices))


def expand_commands(line: str) -> list[str]:
    """
    Split *line* on ``;`` and ``|`` (outside backticks) and expand repeat prefixes.

    Convenience wrapper around :func:`expand_commands_ex` that returns
    only the command list (discarding separator metadata).

    :param line: Raw user input line.
    :returns: Flat list of individual commands.
    """
    return expand_commands_ex(line).commands


_TRAVEL_RE = re.compile(
    r"^`(fast travel|slow travel|return fast|return slow"
    r"|autodiscover|randomwalk|resume|home)\s*(.*?)`$",
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


_ACTIVE_CMD_BASE_FG = "#786050"


def _render_active_command(
    command: str,
    scroll: "Any",
    out: "asyncio.StreamWriter",
    flash_elapsed: float = -1.0,
    hint: str = "",
    progress: Optional[float] = None,
) -> int:
    """
    Render a single highlighted active command on the input row.

    The text is drawn in the base foreground colour.  During the flash
    window a background ramps from black toward the inverse RGB of the
    base colour and back.

    :param flash_elapsed: Seconds since the command was issued.
    :param hint: Right-aligned dim hint text (e.g. autoreply status).
    :param progress: Until timer progress ``0.0..1.0``, or ``None``.
    :returns: Display width of the rendered command text.
    """
    blessed_term = _get_term()
    cols = blessed_term.width
    normal = blessed_term.normal
    fg_sgr = str(blessed_term.color_hex(_ACTIVE_CMD_BASE_FG))

    bg_rgb = _flash_bg_rgb(_ACTIVE_CMD_BASE_FG, flash_elapsed)
    bg_sgr = str(blessed_term.on_color_rgb(*bg_rgb)) if bg_rgb else ""

    hint_w = len(hint) if hint else 0
    avail = cols - hint_w
    text = command[: avail - 1] if _wcswidth(command) >= avail else command
    w = _wcswidth(text)

    out.write(blessed_term.move_yx(scroll.input_row, 0).encode())
    out.write(f"{fg_sgr}{bg_sgr}{text}{normal}".encode())
    pad = avail - w
    if pad > 0:
        out.write((" " * pad).encode())
    _write_hint(hint, out, blessed_term, progress=progress)
    out.write(normal.encode())
    return w


def _clear_command_queue(ctx: "SessionContext") -> None:
    """Remove the command queue from *ctx* when chained send completes."""
    cq = ctx.command_queue
    if cq is not None:
        ctx.command_queue = None


def _render_command_queue(
    queue: "Optional[_CommandQueue]",
    scroll: "Any",
    out: "asyncio.StreamWriter",
    flash_elapsed: float = -1.0,
    hint: str = "",
    progress: Optional[float] = None,
) -> int:
    """
    Render the command queue on the input row.

    The active run uses the suggestion (dull) colour with an optional
    flash animation.  Pending runs use dim grey.  If the display is too
    wide it is truncated with an ellipsis.

    :param flash_elapsed: Seconds since last command change; drives flash.
    :param hint: Right-aligned dim hint text (e.g. autoreply status).
    :param progress: Until timer progress ``0.0..1.0``, or ``None``.
    :returns: Total display width of all rendered fragments.
    """
    if queue is None:
        return 0
    blessed_term = _get_term()
    cols = blessed_term.width
    hint_w = len(hint) if hint else 0
    avail = cols - hint_w

    runs = _collapse_runs(queue.commands, queue.current_idx)
    if not runs:
        return 0

    active_fg = str(blessed_term.color_hex(_ACTIVE_CMD_BASE_FG))
    bg_rgb = _flash_bg_rgb(_ACTIVE_CMD_BASE_FG, flash_elapsed)
    active_bg = str(blessed_term.on_color_rgb(*bg_rgb)) if bg_rgb else ""
    pending_sgr = str(blessed_term.color_rgb(120, 120, 120))
    normal = blessed_term.normal

    # Build fragments: (sgr, text) for each run.
    frags: list[tuple[str, str]] = []
    for text, start_idx, _end_idx in runs:
        is_active = start_idx <= queue.current_idx <= _end_idx
        sgr = f"{active_fg}{active_bg}" if is_active else pending_sgr
        frags.append((sgr, text))

    sep = " "
    total_w = 0
    built: list[tuple[str, str]] = []
    for idx, (sgr, text) in enumerate(frags):
        w = _wcswidth(text) + (1 if idx > 0 else 0)
        if total_w + w > avail - 1 and built:
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
    pad = avail - total_w
    if pad > 0:
        out.write((" " * pad).encode())
    _write_hint(hint, out, blessed_term, progress=progress)
    out.write(normal.encode())
    return total_w


async def _send_chained(
    commands: list[str],
    ctx: "SessionContext",
    log: logging.Logger,
    queue: "Optional[_CommandQueue]" = None,
    immediate_set: frozenset[int] = frozenset(),
) -> None:
    """
    Send multiple commands with GA/EOR pacing between each.

    The first command is assumed to have already been sent by the caller.
    This coroutine sends commands 2..N, waiting for the server prompt
    signal before each one.

    Commands whose index is in *immediate_set* (from a ``|`` separator)
    skip the GA/EOR wait and are sent immediately.

    When all commands in the list are identical (e.g. ``9e`` expanded to
    nine ``e`` commands), movement retry logic is applied: if the room
    does not change after a command, the same command is retried up to
    :data:`_MOVE_MAX_RETRIES` times with a delay between attempts.

    :param commands: List of commands (index 1+ will be sent).
    :param ctx: Session context.
    :param log: Logger.
    :param queue: Optional command queue for display and cancellation.
    :param immediate_set: Indices of commands that skip GA/EOR wait.
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

    from .autoreply import _DELAY_RE

    for _idx, cmd in enumerate(commands[1:], 1):
        if queue is not None:
            if queue.cancelled:
                return
            queue.current_idx = _idx
            queue.render()

        dm = _DELAY_RE.match(cmd)
        if dm:
            value = float(dm.group(1))
            unit = dm.group(2)
            delay = value / 1000.0 if unit == "ms" else value
            if delay > 0:
                if await _cancellable_sleep(delay):
                    return
            continue

        # Detect runs of identical commands (e.g. "9e;6n" expands to
        # e,e,...,n,n,...) — these need movement pacing even in mixed
        # lists.  A command is "repeated" if it matches the previous one.
        prev_cmd = commands[_idx - 1] if _idx > 0 else ""
        use_move_pacing = is_repeated or cmd == prev_cmd
        prev_room = ctx.current_room_num if use_move_pacing else ""

        if not use_move_pacing:
            # Mixed commands: GA/EOR pacing (unless immediate).
            if _idx not in immediate_set:
                if prompt_ready is not None:
                    prompt_ready.clear()
                if wait_fn is not None:
                    await wait_fn()
            log.debug("chained command: %r", cmd)
            if echo_fn is not None:
                echo_fn(cmd)
            ctx.active_command_time = _monotonic()
            if ctx.cx_dot is not None:
                ctx.cx_dot.trigger()
            if ctx.tx_dot is not None:
                ctx.tx_dot.trigger()
            ctx.writer.write(cmd + "\r\n")  # type: ignore[arg-type]
            ts = ctx.typescript_file
            if ts is not None and ctx.writer is not None and not ctx.writer.will_echo:
                ts.write(cmd + "\r\n")
                ts.flush()
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
            ctx.active_command_time = _monotonic()
            if ctx.cx_dot is not None:
                ctx.cx_dot.trigger()
            if ctx.tx_dot is not None:
                ctx.tx_dot.trigger()
            ctx.writer.write(cmd + "\r\n")  # type: ignore[arg-type]
            ts = ctx.typescript_file
            if ts is not None and ctx.writer is not None and not ctx.writer.will_echo:
                ts.write(cmd + "\r\n")
                ts.flush()

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
    Execute a macro text string, handling travel, delay, when, and until commands.

    Expands the text with :func:`expand_commands_ex`, then processes each
    part -- backtick-enclosed travel commands are routed through
    :func:`_handle_travel_commands`, delay commands pause execution,
    ``when`` commands gate on GMCP conditions, ``until``/``untils``
    commands wait for server output patterns, and plain commands are
    sent to the server with GA/EOR pacing (or immediately if ``|```
    separated).

    :param text: Raw macro text with ``;``/``|`` separators.
    :param ctx: Session context.
    :param log: Logger.
    """
    from .autoreply import _DELAY_RE, check_condition
    from .client_repl_travel import _handle_travel_commands

    expanded = expand_commands_ex(text)
    parts = list(expanded.commands)
    immediate_set = set(expanded.immediate_set)
    if not parts:
        return

    # snapshot starting room so ``return fast`` can navigate back
    ctx.macro_start_room = ctx.current_room_num

    wait_fn = ctx.wait_for_prompt
    echo_fn = ctx.echo_command
    prompt_ready = ctx.prompt_ready
    sent_count = 0

    idx = 0
    while idx < len(parts):
        cmd = parts[idx]

        # Travel command -- hand off the rest to _handle_travel_commands.
        if _TRAVEL_RE.match(cmd):
            remainder = await _handle_travel_commands(parts[idx:], ctx, log)
            parts = remainder
            immediate_set = set()
            idx = 0
            sent_count = 0
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

        # When condition gate.
        wm = _WHEN_RE.match(cmd)
        if wm:
            vital, op, val = wm.group(1), wm.group(2), wm.group(3)
            ok, desc = check_condition({vital: f"{op}{val}"}, ctx)
            if not ok:
                log.info("macro: when condition failed: %s", desc)
                break
            idx += 1
            continue

        # Until (case-insensitive wait for pattern).
        um = _UNTIL_RE.match(cmd)
        if um:
            timeout = float(um.group(1) or "4")
            pattern_str = um.group(2)
            engine = ctx.autoreply_engine
            if engine is not None:
                now = _monotonic()
                engine._until_start = now
                engine._until_deadline = now + timeout
                compiled = re.compile(pattern_str, re.IGNORECASE | re.MULTILINE | re.DOTALL)
                match = await engine.buffer.wait_for_pattern(compiled, timeout)
                engine._until_start = engine._until_deadline = 0.0
                if match is None:
                    log.info("macro: until timed out for %r", pattern_str)
                    break
            idx += 1
            continue

        # Untils (case-sensitive wait for pattern).
        us = _UNTILS_RE.match(cmd)
        if us:
            timeout = float(us.group(1) or "4")
            pattern_str = us.group(2)
            engine = ctx.autoreply_engine
            if engine is not None:
                now = _monotonic()
                engine._until_start = now
                engine._until_deadline = now + timeout
                # untils is case-SENSITIVE -- no IGNORECASE flag
                compiled = re.compile(pattern_str, re.MULTILINE | re.DOTALL)
                match = await engine.buffer.wait_for_pattern(compiled, timeout)
                engine._until_start = engine._until_deadline = 0.0
                if match is None:
                    log.info("macro: untils timed out for %r", pattern_str)
                    break
            idx += 1
            continue

        # Plain command -- send with pacing (or immediate if | separated).
        if sent_count > 0 and idx not in immediate_set:
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
        sent_count += 1
        idx += 1

    ctx.macro_start_room = ""
