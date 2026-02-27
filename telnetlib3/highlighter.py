"""
Output text highlighting engine for MUD client sessions.

Provides :class:`HighlightRule` for defining patterns and their terminal
formatting, :class:`HighlightEngine` for applying highlights to output lines
while preserving existing SGR sequences, and persistence via
:func:`load_highlights` / :func:`save_highlights`.
"""

from __future__ import annotations

# std imports
import os
import re
import json
import logging
from typing import TYPE_CHECKING, Any, Optional
from dataclasses import dataclass, field

# 3rd party
from wcwidth import iter_sequences, strip_sequences, iter_graphemes, width
from wcwidth.sgr_state import (
    _SGR_PATTERN,
    _SGR_STATE_DEFAULT,
    _sgr_state_update,
    _sgr_state_is_active,
    _sgr_state_to_sequence,
)

if TYPE_CHECKING:
    import blessed
    from .autoreply import AutoreplyRule
    from .session_context import SessionContext

__all__ = (
    "HighlightRule",
    "HighlightEngine",
    "load_highlights",
    "save_highlights",
    "validate_highlight",
)

_RE_FLAGS = re.IGNORECASE | re.MULTILINE | re.DOTALL
_DEFAULT_AUTOREPLY_HIGHLIGHT = "black_on_beige"

log = logging.getLogger(__name__)


@dataclass
class HighlightRule:
    """
    A single highlight pattern-action rule.

    :param pattern: Compiled regex pattern (case-insensitive).
    :param highlight: Blessed compoundable name, e.g. ``"blink_black_on_yellow"``.
    :param enabled: Whether this rule is active.
    :param stop_movement: Cancel discover/randomwalk when matched.
    :param builtin: ``True`` for the autoreply-pattern rule (undeletable).
    """

    pattern: re.Pattern[str]
    highlight: str
    enabled: bool = True
    stop_movement: bool = False
    builtin: bool = False
    case_sensitive: bool = False


def validate_highlight(term: blessed.Terminal, name: str) -> bool:
    """Return ``True`` if *name* is a valid blessed compoundable.

    :param term: Blessed terminal instance.
    :param name: Compoundable attribute name, e.g. ``"bold_red_on_white"``.
    """
    try:
        attr = getattr(term, name)
    except Exception:
        return False
    return callable(attr)


def _parse_entries(entries: list[dict[str, Any]]) -> list[HighlightRule]:
    """Parse a list of highlight entry dicts into :class:`HighlightRule` instances."""
    rules: list[HighlightRule] = []
    for entry in entries:
        pattern_str = entry.get("pattern", "")
        highlight = entry.get("highlight", "")
        if not pattern_str or not highlight:
            continue
        enabled = bool(entry.get("enabled", True))
        stop_movement = bool(entry.get("stop_movement", False))
        builtin = bool(entry.get("builtin", False))
        case_sensitive = bool(entry.get("case_sensitive", False))
        flags = re.MULTILINE | re.DOTALL
        if not case_sensitive:
            flags |= re.IGNORECASE
        try:
            compiled = re.compile(pattern_str, flags)
        except re.error as exc:
            raise ValueError(
                f"Invalid highlight pattern {pattern_str!r}: {exc}"
            ) from exc
        rules.append(
            HighlightRule(
                pattern=compiled,
                highlight=highlight,
                enabled=enabled,
                stop_movement=stop_movement,
                builtin=builtin,
                case_sensitive=case_sensitive,
            )
        )
    return rules


def load_highlights(path: str, session_key: str) -> list[HighlightRule]:
    """
    Load highlight rules for a session from a JSON file.

    :param path: Path to the highlights JSON file.
    :param session_key: Session identifier (``"host:port"``).
    :returns: List of :class:`HighlightRule` instances.
    :raises FileNotFoundError: When *path* does not exist.
    :raises ValueError: When JSON structure is invalid or regex fails.
    """
    with open(path, "r", encoding="utf-8") as fh:
        data: dict[str, Any] = json.load(fh)
    session_data: dict[str, Any] = data.get(session_key, {})
    entries: list[dict[str, Any]] = session_data.get("highlights", [])
    return _parse_entries(entries)


def save_highlights(
    path: str, rules: list[HighlightRule], session_key: str
) -> None:
    """
    Save highlight rules for a session to a JSON file.

    Other sessions' data in the file is preserved.

    :param path: Path to the highlights JSON file.
    :param rules: List of :class:`HighlightRule` instances to save.
    :param session_key: Session identifier (``"host:port"``).
    """
    data: dict[str, Any] = {}
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    data[session_key] = {
        "highlights": [
            {
                "pattern": r.pattern.pattern,
                "highlight": r.highlight,
                "enabled": r.enabled,
                "stop_movement": r.stop_movement,
                "builtin": r.builtin,
                **({"case_sensitive": True} if r.case_sensitive else {}),
            }
            for r in rules
        ]
    }
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)


class _CompiledRuleSet:
    """A single combined regex built from all highlight + autoreply patterns.

    Each source pattern becomes a named group ``_hl0``, ``_hl1``, etc.
    A single :meth:`finditer` call replaces N separate passes.
    """

    __slots__ = ("_combined", "_group_map")

    def __init__(
        self,
        rules: list[HighlightRule],
        autoreply_rules: list[AutoreplyRule],
        autoreply_highlight: str,
        autoreply_enabled: bool,
    ) -> None:
        parts: list[str] = []
        self._group_map: list[tuple[str, bool]] = []

        if autoreply_enabled:
            for ar in autoreply_rules:
                if not ar.enabled:
                    continue
                gname = f"_hl{len(parts)}"
                parts.append(f"(?P<{gname}>{ar.pattern.pattern})")
                self._group_map.append((autoreply_highlight, False))

        for rule in rules:
            if not rule.enabled:
                continue
            gname = f"_hl{len(parts)}"
            pat = rule.pattern.pattern
            if rule.case_sensitive:
                parts.append(f"(?P<{gname}>(?-i:{pat}))")
            else:
                parts.append(f"(?P<{gname}>{pat})")
            self._group_map.append((rule.highlight, rule.stop_movement))

        self._combined: Optional[re.Pattern[str]] = None
        if parts:
            try:
                self._combined = re.compile("|".join(parts), _RE_FLAGS)
            except re.error:
                self._combined = None

    def finditer(
        self, text: str
    ) -> list[tuple[int, int, str, bool]]:
        """Return non-overlapping ``(start, end, highlight, stop_movement)`` spans."""
        if self._combined is None:
            return []
        spans: list[tuple[int, int, str, bool]] = []
        for m in self._combined.finditer(text):
            gname = m.lastgroup
            if gname is None:
                continue
            idx = int(gname[3:])
            hl, stop = self._group_map[idx]
            start, end = m.start(), m.end()
            if spans and start < spans[-1][1]:
                continue
            spans.append((start, end, hl, stop))
        return spans


class HighlightEngine:
    """
    Applies highlight rules to output lines.

    Builds a single combined regex from all enabled highlight rules and
    autoreply patterns at init time. Each :meth:`process_line` call runs
    one :meth:`finditer` pass, not N separate ones.

    :param rules: User-defined highlight rules.
    :param autoreply_rules: Current autoreply rules (for builtin highlight).
    :param term: Blessed terminal instance.
    :param ctx: Session context (for stop_movement cancellation).
    :param autoreply_highlight: Blessed compoundable for autoreply pattern highlight.
    :param autoreply_enabled: Whether the builtin autoreply highlight is enabled.
    """

    def __init__(
        self,
        rules: list[HighlightRule],
        autoreply_rules: list[AutoreplyRule],
        term: blessed.Terminal,
        ctx: Optional[SessionContext] = None,
        autoreply_highlight: str = _DEFAULT_AUTOREPLY_HIGHLIGHT,
        autoreply_enabled: bool = True,
    ) -> None:
        self._term = term
        self._ctx = ctx
        self._rules = list(rules)
        self._ruleset = _CompiledRuleSet(
            rules, autoreply_rules, autoreply_highlight, autoreply_enabled,
        )
        self.enabled = True
        self._highlight_cache: dict[str, str] = {}

    def _get_highlight_seq(self, name: str) -> str:
        """Return the SGR sequence string for a blessed compoundable name."""
        if name not in self._highlight_cache:
            try:
                attr = getattr(self._term, name)
                self._highlight_cache[name] = str(attr)
            except Exception:
                self._highlight_cache[name] = ""
        return self._highlight_cache[name]

    def process_line(self, line: str) -> tuple[str, bool]:
        """Apply highlight rules to a single line of output.

        :param line: A single line of terminal output (may contain SGR sequences).
        :returns: ``(highlighted_line, had_matches)`` — the original line is
            returned unchanged when no rules match.
        """
        if not self.enabled:
            return line, False

        plain = strip_sequences(line)
        if not plain:
            return line, False

        spans = self._collect_spans(plain)
        if not spans:
            return line, False

        stop_notice = self._handle_stop_movement(spans)
        rebuilt = self._rebuild_line(line, plain, spans)
        if stop_notice:
            rebuilt = rebuilt.rstrip("\r\n") + stop_notice + "\r\n"
        return rebuilt, True

    def _collect_spans(
        self, plain: str
    ) -> list[tuple[int, int, str, bool]]:
        """Collect all highlight match spans from enabled rules.

        Delegates to the combined :class:`_CompiledRuleSet` for a single-pass
        :meth:`finditer` over all patterns.

        :returns: List of ``(start, end, highlight_name, stop_movement)``
            sorted by start position, with overlaps resolved (first rule wins).
        """
        return self._ruleset.finditer(plain)

    def _handle_stop_movement(
        self, spans: list[tuple[int, int, str, bool]]
    ) -> Optional[str]:
        """Cancel discover/randomwalk tasks if any span has stop_movement.

        :returns: Cyan-colored notice string to append, or ``None``.
        """
        ctx = self._ctx
        if ctx is None:
            return None
        cancelled: list[str] = []
        for _s, _e, _hl, stop in spans:
            if not stop:
                continue
            if ctx.discover_active and ctx.discover_task is not None:
                ctx.discover_task.cancel()
                ctx.discover_active = False
                ctx.discover_current = 0
                ctx.discover_total = 0
                cancelled.append("discover")
                log.info("highlighter: stop_movement cancelled discover")
            if ctx.randomwalk_active and ctx.randomwalk_task is not None:
                ctx.randomwalk_task.cancel()
                ctx.randomwalk_active = False
                ctx.randomwalk_current = 0
                ctx.randomwalk_total = 0
                cancelled.append("random walk")
                log.info("highlighter: stop_movement cancelled randomwalk")
            break
        if not cancelled:
            return None
        cyan = str(self._term.cyan)
        normal = str(self._term.normal)
        modes = ", ".join(cancelled)
        return f" {cyan}[stop: {modes} cancelled]{normal}"

    def _rebuild_line(
        self,
        line: str,
        plain: str,
        spans: list[tuple[int, int, str, bool]],
    ) -> str:
        """Rebuild *line* injecting highlight SGR at matched spans.

        Iterates through the original line using :func:`iter_sequences` to
        separate text from escape sequences. Tracks position in the stripped
        *plain* text to know when entering/exiting highlight spans. Preserves
        all original escape sequences and restores SGR state after each
        highlight span ends.
        """
        sgr_state = _SGR_STATE_DEFAULT
        span_idx = 0
        plain_pos = 0
        in_highlight = False
        output: list[str] = []

        for segment, is_seq in iter_sequences(line):
            if is_seq:
                if _SGR_PATTERN.match(segment):
                    sgr_state = _sgr_state_update(sgr_state, segment)
                if not in_highlight:
                    output.append(segment)
                continue

            for grapheme in iter_graphemes(segment):
                if span_idx < len(spans):
                    s_start, s_end, hl_name, _stop = spans[span_idx]

                    if not in_highlight and plain_pos >= s_start:
                        saved_sgr = sgr_state
                        hl_seq = self._get_highlight_seq(hl_name)
                        if hl_seq:
                            output.append(hl_seq)
                        in_highlight = True

                    if in_highlight and plain_pos >= s_end:
                        output.append("\x1b[0m")
                        restore = _sgr_state_to_sequence(saved_sgr)
                        if restore:
                            output.append(restore)
                        in_highlight = False
                        span_idx += 1

                        if span_idx < len(spans):
                            s_start, s_end, hl_name, _stop = spans[span_idx]
                            if plain_pos >= s_start:
                                saved_sgr = sgr_state
                                hl_seq = self._get_highlight_seq(hl_name)
                                if hl_seq:
                                    output.append(hl_seq)
                                in_highlight = True

                output.append(grapheme)
                plain_pos += len(grapheme)

        if in_highlight:
            output.append("\x1b[0m")
            restore = _sgr_state_to_sequence(sgr_state)
            if restore:
                output.append(restore)

        return "".join(output)
