"""
Server output pattern matching and automatic reply engine.

Provides :class:`SearchBuffer` for accumulating ANSI-stripped server output
and :class:`AutoreplyEngine` for matching regex patterns and queuing replies
with delay/chaining support.
"""

from __future__ import annotations

# std imports
import os
import re
import json
import time
import asyncio
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Callable, Optional, Awaitable
from dataclasses import field, dataclass

# 3rd party
from wcwidth import strip_sequences

if TYPE_CHECKING:
    from .session_context import SessionContext

__all__ = (
    "AutoreplyRule",
    "SearchBuffer",
    "AutoreplyEngine",
    "load_autoreplies",
    "save_autoreplies",
    "check_condition",
)

_DELAY_RE = re.compile(r"^`delay\s+(\d+(?:\.\d+)?)(ms|s)`$")
_GROUP_RE = re.compile(r"\\(\d+)")
_COND_RE = re.compile(r"^(>=|<=|>|<|=)(\d+)$")

# Maps condition key to (current_keys, max_keys) for GMCP Char.Vitals lookup.
_VITAL_KEYS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "HP%": (("hp", "HP"), ("maxhp", "maxHP", "max_hp")),
    "MP%": (("mp", "MP", "mana", "sp", "SP"), ("maxmp", "maxMP", "max_mp", "maxsp", "maxSP")),
}


def _get_vital_pct(key: str, vitals: dict[str, Any]) -> Optional[int]:
    """Return the vital percentage (0-100+) for *key*, or ``None`` if unavailable."""
    spec = _VITAL_KEYS.get(key)
    if spec is None:
        return None
    cur_keys, max_keys = spec
    cur_raw = None
    for k in cur_keys:
        cur_raw = vitals.get(k)
        if cur_raw is not None:
            break
    max_raw = None
    for k in max_keys:
        max_raw = vitals.get(k)
        if max_raw is not None:
            break
    if cur_raw is None or max_raw is None:
        return None
    try:
        cur = int(cur_raw)
        mx = int(max_raw)
    except (TypeError, ValueError):
        return None
    if mx <= 0:
        return None
    return int(cur * 100 / mx)


def _compare(value: int, op: str, threshold: int) -> bool:
    """Evaluate ``value op threshold``."""
    if op == ">":
        return value > threshold
    if op == "<":
        return value < threshold
    if op == ">=":
        return value >= threshold
    if op == "<=":
        return value <= threshold
    if op == "=":
        return value == threshold
    raise ValueError(f"unknown operator: {op!r}")


def check_condition(when: dict[str, str], ctx: "SessionContext") -> tuple[bool, str]:
    """
    Check vital conditions against GMCP data on *ctx*.

    :param when: Condition dict, e.g. ``{"HP%": ">50", "MP%": ">30"}``.
    :param ctx: Session context with ``gmcp_data`` attribute.
    :returns: ``(ok, failure_description)`` -- *ok* is ``False`` when a
        condition is not met; *failure_description* explains which.
    """
    if not when:
        return True, ""
    gmcp: Optional[dict[str, Any]] = ctx.gmcp_data if ctx is not None else None
    if not gmcp:
        return True, ""
    vitals = gmcp.get("Char.Vitals")
    if not isinstance(vitals, dict):
        return True, ""
    for key, expr in when.items():
        m = _COND_RE.match(expr.strip())
        if not m:
            continue
        op, threshold = m.group(1), int(m.group(2))
        pct = _get_vital_pct(key, vitals)
        if pct is None:
            continue
        if not _compare(pct, op, threshold):
            return False, f"{key}{op}{threshold} (actual {pct}%)"
    return True, ""


@dataclass
class AutoreplyRule:
    r"""
    A single autoreply pattern-action rule.

    :param pattern: Compiled regex pattern.
    :param reply: Reply template with ``\1``/``\2`` group refs,
        ``;`` command separators, repeat prefixes (``3e``), and delay segments.
    :param when: Vital conditions that must be met for the rule to fire,
        e.g. ``{"HP%": ">50", "MP%": ">30"}``.
    """

    pattern: re.Pattern[str]
    reply: str
    exclusive: bool = False
    until: str = ""
    post_command: str = ""
    always: bool = False
    enabled: bool = True
    exclusive_timeout: float = 10.0
    when: dict[str, str] = field(default_factory=dict)
    immediate: bool = False
    last_fired: str = ""


def _parse_entries(entries: list[dict[str, str]]) -> list[AutoreplyRule]:
    """Parse a list of autoreply entry dicts into :class:`AutoreplyRule` instances."""
    rules: list[AutoreplyRule] = []
    for entry in entries:
        pattern_str = entry.get("pattern", "")
        reply = entry.get("reply", "")
        if not pattern_str:
            continue
        exclusive = bool(entry.get("exclusive", False))
        until = entry.get("until", "")
        post_command = entry.get("post_command", "")
        always = bool(entry.get("always", False))
        enabled = bool(entry.get("enabled", True))
        exclusive_timeout = float(entry.get("exclusive_timeout", 10.0))
        when_raw: Any = entry.get("when", {})
        when = dict(when_raw) if isinstance(when_raw, dict) else {}
        immediate = bool(entry.get("immediate", False))
        last_fired = str(entry.get("last_fired", ""))
        try:
            compiled = re.compile(pattern_str, re.MULTILINE | re.DOTALL)
        except re.error as exc:
            raise ValueError(f"Invalid autoreply pattern {pattern_str!r}: {exc}") from exc
        rules.append(
            AutoreplyRule(
                pattern=compiled,
                reply=reply,
                exclusive=exclusive,
                until=until,
                post_command=post_command,
                always=always,
                enabled=enabled,
                exclusive_timeout=exclusive_timeout,
                when=when,
                immediate=immediate,
                last_fired=last_fired,
            )
        )
    return rules


def load_autoreplies(path: str, session_key: str) -> list[AutoreplyRule]:
    """
    Load autoreply rules for a session from a JSON file.

    The file is keyed by session (``"host:port"``).  Each value is
    an object with an ``"autoreplies"`` list.

    :param path: Path to the autoreplies JSON file.
    :param session_key: Session identifier (``"host:port"``).
    :returns: List of :class:`AutoreplyRule` instances.
    :raises FileNotFoundError: When *path* does not exist.
    :raises ValueError: When JSON structure is invalid or regex fails.
    """
    with open(path, "r", encoding="utf-8") as fh:
        data: dict[str, Any] = json.load(fh)

    session_data: dict[str, Any] = data.get(session_key, {})
    entries: list[dict[str, str]] = session_data.get("autoreplies", [])
    return _parse_entries(entries)


def save_autoreplies(path: str, rules: list[AutoreplyRule], session_key: str) -> None:
    """
    Save autoreply rules for a session to a JSON file.

    Other sessions' data in the file is preserved.

    :param path: Path to the autoreplies JSON file.
    :param rules: List of :class:`AutoreplyRule` instances to save.
    :param session_key: Session identifier (``"host:port"``).
    """
    data: dict[str, Any] = {}
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)

    data[session_key] = {
        "autoreplies": [
            {
                "pattern": r.pattern.pattern,
                "reply": r.reply,
                **({"exclusive": True} if r.exclusive else {}),
                **({"until": r.until} if r.until else {}),
                **({"post_command": r.post_command} if r.post_command else {}),
                **({"always": True} if r.always else {}),
                **({"enabled": False} if not r.enabled else {}),
                **(
                    {"exclusive_timeout": r.exclusive_timeout}
                    if r.exclusive and r.exclusive_timeout != 10.0
                    else {}
                ),
                **({"when": dict(r.when)} if r.when else {}),
                **({"immediate": True} if r.immediate else {}),
                **({"last_fired": r.last_fired} if r.last_fired else {}),
            }
            for r in rules
        ]
    }
    from ._paths import _atomic_write

    content = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    _atomic_write(path, content)


def _substitute_groups(template: str, match: re.Match[str]) -> str:
    r"""
    Replace ``\1``, ``\2``, etc. with match group values.

    :param template: Reply template string.
    :param match: Regex match object.
    :returns: Template with groups substituted.
    """

    def _repl(m: re.Match[str]) -> str:
        idx = int(m.group(1))
        try:
            val = match.group(idx)
        except IndexError:
            return m.group(0)
        return val if val is not None else ""

    return _GROUP_RE.sub(_repl, template)


class SearchBuffer:
    """
    Accumulates stripped server output lines for regex matching.

    Maintains a rolling window of recent lines with ANSI sequences stripped.  Tracks the last match
    position so each new line is only searched from the position after the previous match.

    :param max_lines: Maximum number of lines to retain (default 100).
    """

    def __init__(self, max_lines: int = 100) -> None:
        """Initialize SearchBuffer with given line capacity."""
        self._lines: list[str] = []
        self._partial: str = ""
        self._max_lines = max_lines
        self._last_match_line: int = 0
        self._last_match_col: int = 0

    @property
    def lines(self) -> list[str]:
        """Complete lines accumulated so far."""
        return self._lines

    @property
    def partial(self) -> str:
        """Incomplete trailing line (no newline yet)."""
        return self._partial

    def add_text(self, text: str, echo_filter: Optional["set[str]"] = None) -> bool:
        """
        Add server output text, stripping ANSI sequences first.

        Splits on newlines and appends complete lines to the buffer.
        Incomplete trailing text is held in ``_partial`` until the
        next newline arrives.

        Complete lines whose stripped content exactly matches an entry
        in *echo_filter* are silently dropped (and removed from the
        set) so that echoed autoreply commands are never matched.

        :param text: Raw server output (may contain ANSI sequences).
        :param echo_filter: Set of sent command strings to suppress.
        :returns: ``True`` if new complete lines were added.
        """
        stripped = strip_sequences(text)
        if not stripped:
            return False

        parts = stripped.split("\n")

        # Prepend partial to first segment.
        parts[0] = self._partial + parts[0]

        if len(parts) == 1:
            # No newline in this chunk -- accumulate partial.
            self._partial = parts[0]
            return False

        # Last element is the new partial (may be empty string).
        self._partial = parts[-1]

        # Everything except the last element is a complete line.
        # Drop lines that are echoes of commands we sent.
        new_lines: list[str] = []
        for line in parts[:-1]:
            if echo_filter and line.strip() in echo_filter:
                echo_filter.discard(line.strip())
            else:
                new_lines.append(line)
        self._lines.extend(new_lines)
        self._cull()
        return True

    def get_searchable_text(self) -> str:
        """
        Return text from last match position forward.

        Joins lines from ``_last_match_line`` onward with newlines,
        including the current partial (incomplete) line so that
        prompts without trailing newlines can be matched.

        :returns: Searchable text substring.
        """
        if self._last_match_line >= len(self._lines) and not self._partial:
            return ""
        text = "\n".join(self._lines[self._last_match_line :])
        if self._partial:
            if text:
                text += "\n" + self._partial
            else:
                text = self._partial
        return text[self._last_match_col :]

    def advance_match(self, offset_in_searchable: int, length: int) -> None:
        """
        Update last match position past the given match.

        :param offset_in_searchable: Start offset of match within
            the text returned by :meth:`get_searchable_text`.
        :param length: Length of the match.
        """
        # Convert searchable-text offset back to absolute (line, col).
        abs_offset = self._last_match_col + offset_in_searchable + length
        for i in range(self._last_match_line, len(self._lines)):
            line_len = len(self._lines[i])
            if i > self._last_match_line:
                line_len += 1  # account for the \n separator
            if abs_offset <= line_len:
                self._last_match_line = i
                self._last_match_col = abs_offset
                return
            abs_offset -= line_len + (1 if i == self._last_match_line else 0)

        # Past the last line -- offset is within the partial.
        self._last_match_line = len(self._lines)
        self._last_match_col = abs_offset

    def clear(self) -> None:
        """Reset buffer for a new EOR/GA record, preserving partial line."""
        self._lines.clear()
        self._last_match_line = 0
        self._last_match_col = 0

    def reset_match_position(self) -> None:
        """Reset match position to start so retained text is re-searchable."""
        self._last_match_line = 0
        self._last_match_col = 0

    def _cull(self) -> None:
        """Remove oldest lines beyond *max_lines*, adjusting match position."""
        if len(self._lines) <= self._max_lines:
            return
        excess = len(self._lines) - self._max_lines
        self._lines = self._lines[excess:]
        self._last_match_line = max(0, self._last_match_line - excess)
        if self._last_match_line == 0 and excess > 0:
            self._last_match_col = 0


@dataclass
class _ExclusiveState:
    """Mutable bundle of exclusive-mode state variables."""

    active: bool = False
    rule_index: int = 0
    until_pattern: Optional[re.Pattern[str]] = None
    skip_next_prompt: bool = False
    deadline: float = 0.0
    post_command: str = ""
    prompt_count: int = 0

    def clear(self) -> None:
        """Reset all fields to defaults."""
        self.active = False
        self.rule_index = 0
        self.until_pattern = None
        self.skip_next_prompt = False
        self.deadline = 0.0
        self.post_command = ""
        self.prompt_count = 0


class AutoreplyEngine:
    """
    Matches server output against autoreply rules and queues replies.

    Replies are chained sequentially: if reply A has a 5s delay, reply B
    waits for A to complete before starting.

    :param rules: Autoreply rules to match against.
    :param ctx: Session context (provides writer for sending and GMCP data).
    :param log: Logger instance.
    :param max_lines: SearchBuffer capacity.
    """

    def __init__(
        self,
        rules: list[AutoreplyRule],
        ctx: "SessionContext",
        log: logging.Logger,
        max_lines: int = 100,
        insert_fn: Optional[Callable[[str], None]] = None,
        echo_fn: Optional[Callable[[str], None]] = None,
        wait_fn: Optional[Callable[[], Awaitable[None]]] = None,
    ) -> None:
        """Initialize AutoreplyEngine with rules and I/O handles."""
        self._rules = rules
        self._ctx = ctx
        self._log = log
        self._buffer = SearchBuffer(max_lines=max_lines)
        self._reply_chain: Optional[asyncio.Task[None]] = None
        self._insert_fn = insert_fn
        self._echo_fn = echo_fn
        self._wait_fn = wait_fn
        self._excl = _ExclusiveState()
        self._sent_commands: set[str] = set()
        self._sent_commands_max: int = int(os.environ.get("TELNETLIB3_SENT_COMMANDS_MAX", "10000"))
        self._prompt_based = False
        self._cycle_matched: set[int] = set()
        self._condition_blocked: set[int] = set()
        self._condition_retried: bool = False
        self._suppress_exclusive = False
        self._enabled = True
        self._last_matched_pattern: str = ""
        self._condition_failed: Optional[tuple[int, str]] = None

    def pop_condition_failed(self) -> Optional[tuple[int, str]]:
        """
        Return and clear the last condition failure.

        :returns: ``(rule_index_1based, description)`` if last match failed
            a condition, otherwise ``None``.
        """
        val = self._condition_failed
        self._condition_failed = None
        return val

    @property
    def buffer(self) -> SearchBuffer:
        """The underlying :class:`SearchBuffer`."""
        return self._buffer

    @property
    def exclusive_active(self) -> bool:
        """``True`` when an exclusive rule is suppressing normal matching."""
        return self._excl.active

    @property
    def exclusive_rule_index(self) -> int:
        """1-based index of the active exclusive rule, or 0 if none."""
        return self._excl.rule_index

    @property
    def suppress_exclusive(self) -> bool:
        """When ``True``, exclusive rules match but do not enter exclusive mode."""
        return self._suppress_exclusive

    @suppress_exclusive.setter
    def suppress_exclusive(self, value: bool) -> None:
        self._suppress_exclusive = value

    @property
    def enabled(self) -> bool:
        """When ``False``, all rule matching is suspended."""
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value

    @property
    def reply_pending(self) -> bool:
        """``True`` when a reply chain is still executing."""
        return self._reply_chain is not None and not self._reply_chain.done()

    @property
    def cycle_matched(self) -> bool:
        """``True`` if any rule matched in the current prompt cycle."""
        return len(self._cycle_matched) > 0

    @property
    def last_matched_pattern(self) -> str:
        """Pattern string of the most recently matched rule, or ``""``."""
        return self._last_matched_pattern

    def feed(self, text: str) -> None:
        """
        Feed server output text and check for matches.

        Called from the server output handler in both REPL and raw modes.  Searches after every
        chunk, including partial lines, so that MUD prompts without trailing newlines are matched.

        :param text: Server output text.
        """
        if not self._enabled:
            return
        self._buffer.add_text(text, self._sent_commands)

        if self._excl.active:
            # Check timeout.
            if self.check_timeout():
                self._buffer.add_text(text, self._sent_commands)
                # fall through to normal matching below
            # Check until pattern to see if exclusive should end.
            elif self._excl.until_pattern is not None:
                searchable = self._buffer.get_searchable_text()
                if searchable and self._excl.until_pattern.search(searchable):
                    self._log.info(
                        "autoreply: exclusive cleared by until pattern %r",
                        self._excl.until_pattern.pattern,
                    )
                    post = self._excl.post_command
                    self._excl.clear()
                    self._buffer.clear()
                    if post:
                        self._log.info("autoreply: queuing post_command %r", post)
                        if not post.rstrip().endswith(";"):
                            post = post.rstrip() + ";"
                        self._queue_reply(post)
                    return
                self._match_always_rules()
                return
            else:
                self._match_always_rules()
                return

        searchable = self._buffer.get_searchable_text()
        if not searchable:
            return

        # Once prompt-based mode is active (GA/EOR seen), defer normal
        # matching until on_prompt() so that replies are never fired
        # mid-output.  Rules with immediate=True still fire here so
        # that asynchronous MUD events (no trailing GA/EOR) are caught.
        if self._prompt_based:
            self._match_rules(immediate_only=True)
            return

        self._match_rules()

    def _match_rules(self, immediate_only: bool = False) -> None:
        """
        Run rule matching on buffered text.

        When *immediate_only* is ``False`` (default), all enabled rules
        are checked — called from :meth:`feed` before prompt-based mode
        and from :meth:`on_prompt` once prompt-based mode is active.

        When *immediate_only* is ``True``, only rules with
        ``immediate=True`` are checked and exclusive/post_command
        handling is skipped.
        """
        searchable = self._buffer.get_searchable_text()
        if not searchable:
            return

        log_prefix = "immediate rule" if immediate_only else "rule"
        max_iterations = len(self._rules) * 2
        found = True
        while found and max_iterations > 0:
            found = False
            max_iterations -= 1
            searchable = self._buffer.get_searchable_text()
            if not searchable:
                break
            for rule_idx, rule in enumerate(self._rules):
                if not rule.enabled:
                    continue
                if immediate_only and not rule.immediate:
                    continue
                if rule_idx in self._cycle_matched:
                    if immediate_only or self._prompt_based:
                        continue
                if rule_idx in self._condition_blocked:
                    continue
                match = rule.pattern.search(searchable)
                if match:
                    self._last_matched_pattern = rule.pattern.pattern
                    if not immediate_only and rule.exclusive and self._suppress_exclusive:
                        self._cycle_matched.add(rule_idx)
                        self._buffer.advance_match(match.start(), len(match.group(0)))
                        found = True
                        break
                    if rule.when:
                        ok, desc = check_condition(rule.when, self._ctx)
                        if not ok:
                            self._log.info(
                                "autoreply: %s #%d skipped," " condition failed: %s",
                                log_prefix,
                                rule_idx + 1,
                                desc,
                            )
                            self._condition_failed = (rule_idx + 1, desc)
                            self._condition_blocked.add(rule_idx)
                            found = True
                            break
                    self._cycle_matched.add(rule_idx)
                    self._buffer.advance_match(match.start(), len(match.group(0)))
                    rule.last_fired = datetime.now(timezone.utc).isoformat()
                    if hasattr(self._ctx, "mark_autoreplies_dirty"):
                        self._ctx.mark_autoreplies_dirty()
                    reply = _substitute_groups(rule.reply, match)
                    if not reply.rstrip().endswith(";"):
                        reply = reply.rstrip() + ";"
                    self._queue_reply(reply)
                    if not immediate_only:
                        if not rule.exclusive and rule.post_command:
                            post = _substitute_groups(rule.post_command, match)
                            if not post.rstrip().endswith(";"):
                                post = post.rstrip() + ";"
                            self._queue_reply(post)
                        if rule.exclusive:
                            self._excl.active = True
                            self._excl.rule_index = rule_idx + 1
                            self._excl.skip_next_prompt = True
                            self._excl.prompt_count = 0
                            self._excl.post_command = rule.post_command
                            self._excl.deadline = (
                                time.monotonic() + rule.exclusive_timeout
                                if rule.exclusive_timeout > 0
                                else 0.0
                            )
                            if rule.until:
                                until_str = _substitute_groups(rule.until, match)
                                try:
                                    self._excl.until_pattern = re.compile(
                                        until_str, re.MULTILINE | re.DOTALL
                                    )
                                except re.error:
                                    self._excl.until_pattern = None
                            else:
                                self._excl.until_pattern = None
                            return
                    found = True
                    break

    def _match_always_rules(self) -> None:
        """Check rules with ``always=True`` even during exclusive suppression."""
        searchable = self._buffer.get_searchable_text()
        if not searchable:
            return
        for rule_idx, rule in enumerate(self._rules):
            if not rule.enabled or not rule.always:
                continue
            if self._prompt_based and rule_idx in self._cycle_matched:
                continue
            match = rule.pattern.search(searchable)
            if match:
                self._last_matched_pattern = rule.pattern.pattern
                self._cycle_matched.add(rule_idx)
                self._buffer.advance_match(match.start(), len(match.group(0)))
                rule.last_fired = datetime.now(timezone.utc).isoformat()
                if hasattr(self._ctx, "mark_autoreplies_dirty"):
                    self._ctx.mark_autoreplies_dirty()
                reply = _substitute_groups(rule.reply, match)
                self._queue_reply(reply)

    def _queue_reply(self, reply_text: str) -> None:
        """
        Queue a reply, chaining after any pending reply task.

        :param reply_text: Fully substituted reply string.
        """
        prev = self._reply_chain

        async def _chained() -> None:
            if prev is not None and not prev.done():
                await prev
            await self._execute_reply(reply_text)

        self._reply_chain = asyncio.ensure_future(_chained())

    async def _execute_reply(self, reply_text: str) -> None:
        """
        Execute a single reply: split on ``;``, expand repeats, handle delays, send.

        Delay segments: ```delay Ns``` or ```delay Nms``` (e.g. ```delay 2s```,
        ```delay 500ms```).  Command segments support repeat prefixes
        (e.g. ``3e`` -> ``e;e;e``).

        :param reply_text: Fully substituted reply string.
        """
        from .client_repl import expand_commands

        cmds = expand_commands(reply_text)
        sent_count = 0
        for cmd in cmds:
            dm = _DELAY_RE.match(cmd)
            if dm:
                value = float(dm.group(1))
                unit = dm.group(2)
                delay = value / 1000.0 if unit == "ms" else value
                if delay > 0:
                    await asyncio.sleep(delay)
                continue

            # Skip wait_fn for the first command -- the match was
            # triggered by output that ended with a GA/EOR prompt
            # signal, so the server is already ready for input.
            if sent_count > 0 and self._wait_fn is not None:
                await self._wait_fn()
            self._send_command(cmd)
            sent_count += 1

    def _send_command(self, cmd: str) -> None:
        """
        Send a single command line to the server.

        :param cmd: Command text (without line ending).
        """
        if not cmd or not cmd.strip():
            return
        self._log.info("autoreply: sending %r", cmd)
        self._sent_commands.add(cmd.strip())
        if len(self._sent_commands) > self._sent_commands_max:
            self._sent_commands.clear()
        if self._echo_fn is not None:
            self._echo_fn(cmd)
        if self._ctx.cx_dot is not None:
            self._ctx.cx_dot.trigger()
        if self._ctx.tx_dot is not None:
            self._ctx.tx_dot.trigger()
        assert self._ctx.writer is not None
        self._ctx.writer.write(cmd + "\r\n")  # type: ignore[arg-type]

    def on_prompt(self) -> None:
        """
        Match accumulated text and clear per-cycle state on EOR/GA.

        In prompt-based mode, :meth:`feed` defers normal rule matching
        to this method so that replies are never fired mid-output.
        Matching runs on the full buffer before it is cleared.

        The first EOR/GA after an exclusive rule fires is skipped,
        because it belongs to the same server response that triggered
        the match (GA/EOR often arrives in a separate TCP chunk).

        When an ``until`` pattern is set, EOR/GA does **not** clear
        exclusive -- only the until pattern match or timeout can.

        Each EOR/GA resets the per-cycle deduplication set so that
        rules can match again in the next prompt cycle.
        """
        self._prompt_based = True
        if not self._enabled:
            return
        # Match on accumulated buffer before clearing -- this is where
        # deferred matches from feed() actually fire.
        if not self._excl.active:
            self._match_rules()
        # When rules matched text but their ``when`` condition failed
        # (e.g. HP too low), preserve the buffer so the text can be
        # retried on the next prompt cycle when conditions may have
        # changed (e.g. HP healed).  Only the condition-blocked rules
        # are re-eligible; rules that already fired keep their
        # _cycle_matched entry so they don't re-trigger on retained
        # text.  After one retry, clear normally to prevent loops.
        if self._condition_blocked and not self._condition_retried:
            self._condition_retried = True
            # Keep _cycle_matched for rules that fired; only remove
            # the blocked rules so they can retry.
            self._cycle_matched -= self._condition_blocked
            self._condition_blocked.clear()
            self._buffer.reset_match_position()
        else:
            self._condition_blocked.clear()
            self._condition_retried = False
            self._cycle_matched.clear()
            self._buffer.clear()
        if self._excl.skip_next_prompt:
            self._excl.skip_next_prompt = False
            return
        if self._excl.until_pattern is not None:
            self._excl.prompt_count += 1
            if self._excl.prompt_count >= 2:
                self._log.info(
                    "autoreply: exclusive cleared after %d prompts" " without until match",
                    self._excl.prompt_count,
                )
                self._excl.clear()
            return
        self._excl.active = False
        self._excl.rule_index = 0
        self._excl.post_command = ""

    def check_timeout(self) -> bool:
        """
        Check and clear exclusive mode if the deadline has passed.

        :returns: ``True`` if exclusive was cleared by timeout.
        """
        if self._excl.active and self._excl.deadline and time.monotonic() > self._excl.deadline:
            self._log.info("autoreply: exclusive timed out")
            self._excl.clear()
            self._buffer.clear()
            return True
        return False

    def cancel(self) -> None:
        """Cancel any pending reply chain and clear exclusive state."""
        if self._reply_chain is not None and not self._reply_chain.done():
            self._reply_chain.cancel()
            self._reply_chain = None
        self._excl.clear()
        self._condition_blocked.clear()
        self._condition_retried = False
        self._sent_commands.clear()
