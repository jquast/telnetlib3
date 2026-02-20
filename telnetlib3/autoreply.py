"""
Server output pattern matching and automatic reply engine.

Provides :class:`SearchBuffer` for accumulating ANSI-stripped server output
and :class:`AutoreplyEngine` for matching regex patterns and queuing replies
with delay/chaining support.
"""

from __future__ import annotations

# std imports
import re
import json
import asyncio
import logging
from typing import Any, Union, Optional
from dataclasses import dataclass

# 3rd party
from wcwidth import strip_sequences

# local
from .stream_writer import TelnetWriter, TelnetWriterUnicode

__all__ = (
    "AutoreplyRule",
    "SearchBuffer",
    "AutoreplyEngine",
    "load_autoreplies",
    "save_autoreplies",
)

_DELAY_RE = re.compile(r"::(\d+(?:\.\d+)?)(ms|s)::")
_CR_TOKEN = "<CR>"
_GROUP_RE = re.compile(r"\\(\d+)")

# Echo format for auto-sent commands (dim cyan).
_ECHO_FMT = "\x1b[36;2m[auto] {}\x1b[m\r\n"


@dataclass
class AutoreplyRule:
    r"""
    A single autoreply pattern-action rule.

    :param pattern: Compiled regex pattern.
    :param reply: Reply template with ``\1``/``\2`` group refs,
        ``<CR>`` send markers, and ``::Ns::`` delays.
    """

    pattern: re.Pattern[str]
    reply: str


def load_autoreplies(path: str) -> list[AutoreplyRule]:
    """
    Load autoreply rules from a JSON file.

    :param path: Path to the autoreplies JSON file.
    :returns: List of :class:`AutoreplyRule` instances.
    :raises FileNotFoundError: When *path* does not exist.
    :raises ValueError: When JSON structure is invalid or regex fails.
    """
    with open(path, "r", encoding="utf-8") as fh:
        data: dict[str, Any] = json.load(fh)

    entries: list[dict[str, str]] = data.get("autoreplies", [])
    rules: list[AutoreplyRule] = []
    for entry in entries:
        pattern_str = entry.get("pattern", "")
        reply = entry.get("reply", "")
        if not pattern_str:
            continue
        try:
            compiled = re.compile(pattern_str, re.MULTILINE | re.DOTALL)
        except re.error as exc:
            raise ValueError(f"Invalid autoreply pattern {pattern_str!r}: {exc}") from exc
        rules.append(AutoreplyRule(pattern=compiled, reply=reply))
    return rules


def save_autoreplies(path: str, rules: list[AutoreplyRule]) -> None:
    """
    Save autoreply rules to a JSON file.

    :param path: Path to the autoreplies JSON file.
    :param rules: List of :class:`AutoreplyRule` instances to save.
    """
    data = {"autoreplies": [{"pattern": r.pattern.pattern, "reply": r.reply} for r in rules]}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


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


def _parse_delay(token: str) -> float:
    """
    Parse a delay value string into seconds.

    :param token: Numeric string portion of a delay token.
    :returns: Delay in seconds.
    """
    m = _DELAY_RE.match(token)
    if m is None:
        return 0.0
    value = float(m.group(1))
    unit = m.group(2)
    if unit == "ms":
        value /= 1000.0
    return value


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

    def add_text(self, text: str) -> bool:
        """
        Add server output text, stripping ANSI sequences first.

        Splits on newlines and appends complete lines to the buffer.
        Incomplete trailing text is held in ``_partial`` until the
        next newline arrives.

        :param text: Raw server output (may contain ANSI sequences).
        :returns: ``True`` if new complete lines were added.
        """
        stripped = strip_sequences(text)
        if not stripped:
            return False

        parts = stripped.split("\n")

        # Prepend partial to first segment.
        parts[0] = self._partial + parts[0]

        if len(parts) == 1:
            # No newline in this chunk — accumulate partial.
            self._partial = parts[0]
            return False

        # Last element is the new partial (may be empty string).
        self._partial = parts[-1]

        # Everything except the last element is a complete line.
        new_lines = parts[:-1]
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

        # Past the last line — offset is within the partial.
        self._last_match_line = len(self._lines)
        self._last_match_col = abs_offset

    def _cull(self) -> None:
        """Remove oldest lines beyond *max_lines*, adjusting match position."""
        if len(self._lines) <= self._max_lines:
            return
        excess = len(self._lines) - self._max_lines
        self._lines = self._lines[excess:]
        self._last_match_line = max(0, self._last_match_line - excess)
        if self._last_match_line == 0 and excess > 0:
            self._last_match_col = 0


class AutoreplyEngine:
    """
    Matches server output against autoreply rules and queues replies.

    Replies are chained sequentially: if reply A has a 5s delay, reply B
    waits for A to complete before starting.

    :param rules: Autoreply rules to match against.
    :param writer: Telnet writer for sending replies.
    :param log: Logger instance.
    :param stdout: Optional stdout writer for echoing auto-sent commands.
    :param max_lines: SearchBuffer capacity.
    """

    def __init__(
        self,
        rules: list[AutoreplyRule],
        writer: Union[TelnetWriter, TelnetWriterUnicode],
        log: logging.Logger,
        stdout: Optional[asyncio.StreamWriter] = None,
        max_lines: int = 100,
    ) -> None:
        """Initialize AutoreplyEngine with rules and I/O handles."""
        self._rules = rules
        self._writer = writer
        self._log = log
        self._stdout = stdout
        self._buffer = SearchBuffer(max_lines=max_lines)
        self._reply_chain: Optional[asyncio.Task[None]] = None

    @property
    def buffer(self) -> SearchBuffer:
        """The underlying :class:`SearchBuffer`."""
        return self._buffer

    def feed(self, text: str) -> None:
        """
        Feed server output text and check for matches.

        Called from the server output handler in both REPL and raw
        modes.  Searches after every chunk, including partial lines,
        so that MUD prompts without trailing newlines are matched.

        :param text: Server output text.
        """
        self._buffer.add_text(text)

        searchable = self._buffer.get_searchable_text()
        if not searchable:
            return

        # Search for all matching rules. We re-fetch searchable text
        # after each match since advance_match changes the window.
        # Cap iterations to len(rules) * 2 as a safety valve — one
        # chunk of text should never produce more matches than that.
        max_iterations = len(self._rules) * 2
        found = True
        while found and max_iterations > 0:
            found = False
            max_iterations -= 1
            searchable = self._buffer.get_searchable_text()
            if not searchable:
                break
            for rule in self._rules:
                match = rule.pattern.search(searchable)
                if match:
                    self._buffer.advance_match(match.start(), len(match.group(0)))
                    reply = _substitute_groups(rule.reply, match)
                    self._queue_reply(reply)
                    found = True
                    break  # re-fetch searchable text and start over

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
        Execute a single reply: parse delays, split on <CR>, send.

        :param reply_text: Fully substituted reply string.
        """
        # Split the reply on delay tokens first.
        parts = _DELAY_RE.split(reply_text)

        # parts comes in groups: [text, value, unit, text, value, unit, ...]
        # When there are no delays, parts is just [reply_text].
        i = 0
        while i < len(parts):
            text_segment = parts[i]
            i += 1

            # Process text segment: split on <CR> and send.
            if text_segment:
                cr_parts = text_segment.split(_CR_TOKEN)
                for j, cmd in enumerate(cr_parts):
                    if j < len(cr_parts) - 1:
                        # This segment ends with <CR> — send it.
                        self._send_command(cmd)
                    elif cmd:
                        # Trailing text without <CR> — send anyway
                        # (autoreplies typically end with <CR>).
                        self._send_command(cmd)

            # Process delay if present.
            if i + 1 < len(parts):
                value = float(parts[i])
                unit = parts[i + 1]
                delay = value / 1000.0 if unit == "ms" else value
                i += 2
                if delay > 0:
                    await asyncio.sleep(delay)

    def _send_command(self, cmd: str) -> None:
        """
        Send a single command line to the server.

        :param cmd: Command text (without line ending).
        """
        if not cmd or not cmd.strip():
            return
        self._log.info("autoreply: sending %r", cmd)
        self._writer.write(cmd + "\r\n")  # type: ignore[arg-type]
        if self._stdout is not None:
            echo = _ECHO_FMT.format(cmd)
            self._stdout.write(echo.encode())

    def cancel(self) -> None:
        """Cancel any pending reply chain."""
        if self._reply_chain is not None and not self._reply_chain.done():
            self._reply_chain.cancel()
            self._reply_chain = None
