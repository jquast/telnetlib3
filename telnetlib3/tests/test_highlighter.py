"""Tests for the highlighter module."""

from __future__ import annotations

import json
import re
import tempfile
import os
from unittest.mock import MagicMock, patch

import pytest

from telnetlib3.highlighter import (
    HighlightRule,
    HighlightEngine,
    _CompiledRuleSet,
    load_highlights,
    save_highlights,
    validate_highlight,
    _RE_FLAGS,
)


def _make_rule(
    pattern: str,
    highlight: str = "bold_red",
    enabled: bool = True,
    stop_movement: bool = False,
    builtin: bool = False,
) -> HighlightRule:
    return HighlightRule(
        pattern=re.compile(pattern, _RE_FLAGS),
        highlight=highlight,
        enabled=enabled,
        stop_movement=stop_movement,
        builtin=builtin,
    )


class _FormattingString(str):
    """Mimics blessed.FormattingString — a str subclass that is also callable."""

    def __call__(self, text: str = "") -> str:
        return f"{self}{text}\x1b[0m"


class _MockTerminal:
    """Minimal blessed.Terminal stand-in for highlight tests."""

    _STYLES = {
        "bold_red": _FormattingString("\x1b[1;31m"),
        "blink_black_on_yellow": _FormattingString("\x1b[5;30;43m"),
        "black_on_beige": _FormattingString("\x1b[30;43m"),
        "cyan": _FormattingString("\x1b[36m"),
        "normal": _FormattingString("\x1b[0m"),
    }

    def __getattr__(self, name: str) -> _FormattingString:
        if name in self._STYLES:
            return self._STYLES[name]
        raise AttributeError(name)


def _mock_term():
    return _MockTerminal()


class TestHighlightRuleLoadSave:
    """Load/save roundtrip for highlight rules."""

    def test_roundtrip(self, tmp_path):
        path = str(tmp_path / "highlights.json")
        rules = [
            _make_rule("dynamite", "blink_black_on_yellow"),
            _make_rule("danger", "bold_red", stop_movement=True),
        ]
        save_highlights(path, rules, "test:23")
        loaded = load_highlights(path, "test:23")
        assert len(loaded) == 2
        assert loaded[0].pattern.pattern == "dynamite"
        assert loaded[0].highlight == "blink_black_on_yellow"
        assert loaded[0].stop_movement is False
        assert loaded[1].stop_movement is True

    def test_empty_session(self, tmp_path):
        path = str(tmp_path / "highlights.json")
        save_highlights(path, [], "test:23")
        loaded = load_highlights(path, "other:99")
        assert loaded == []

    def test_invalid_regex(self, tmp_path):
        path = str(tmp_path / "highlights.json")
        data = {"test:23": {"highlights": [
            {"pattern": "[invalid", "highlight": "bold_red"}
        ]}}
        with open(path, "w") as fh:
            json.dump(data, fh)
        with pytest.raises(ValueError, match="Invalid highlight pattern"):
            load_highlights(path, "test:23")

    def test_preserves_other_sessions(self, tmp_path):
        path = str(tmp_path / "highlights.json")
        data = {"other:99": {"highlights": [
            {"pattern": "foo", "highlight": "bold_red"}
        ]}}
        with open(path, "w") as fh:
            json.dump(data, fh)
        save_highlights(path, [_make_rule("bar")], "test:23")
        with open(path) as fh:
            saved = json.load(fh)
        assert "other:99" in saved
        assert "test:23" in saved

    def test_builtin_flag_roundtrip(self, tmp_path):
        path = str(tmp_path / "highlights.json")
        rules = [_make_rule("autoreply", "black_on_beige", builtin=True)]
        save_highlights(path, rules, "test:23")
        loaded = load_highlights(path, "test:23")
        assert loaded[0].builtin is True


class TestValidateHighlight:

    def test_valid_compoundable(self):
        term = _mock_term()
        assert validate_highlight(term, "bold_red") is True

    def test_invalid_compoundable(self):
        term = _mock_term()
        assert validate_highlight(term, "nonexistent_style") is False


class TestCompiledRuleSet:

    def test_combines_enabled_autoreply_rules(self):
        from telnetlib3.autoreply import AutoreplyRule

        ar_rules = [
            AutoreplyRule(
                pattern=re.compile("foo", _RE_FLAGS), reply="bar", enabled=True
            ),
            AutoreplyRule(
                pattern=re.compile("baz", _RE_FLAGS), reply="qux", enabled=False
            ),
            AutoreplyRule(
                pattern=re.compile("quux", _RE_FLAGS), reply="x", enabled=True
            ),
        ]
        rs = _CompiledRuleSet([], ar_rules, "black_on_beige", True)
        spans = rs.finditer("foo and quux but not baz")
        highlights = [(s, e, hl) for s, e, hl, _stop in spans]
        assert ("foo", "black_on_beige") in [
            ("foo and quux but not baz"[s:e], hl) for s, e, hl in highlights
        ]
        assert ("quux", "black_on_beige") in [
            ("foo and quux but not baz"[s:e], hl) for s, e, hl in highlights
        ]
        matched_texts = {"foo and quux but not baz"[s:e] for s, e, hl in highlights}
        assert "baz" not in matched_texts

    def test_empty_rules(self):
        rs = _CompiledRuleSet([], [], "black_on_beige", True)
        assert rs.finditer("anything") == []

    def test_all_disabled(self):
        from telnetlib3.autoreply import AutoreplyRule

        ar_rules = [
            AutoreplyRule(
                pattern=re.compile("foo", _RE_FLAGS), reply="bar", enabled=False
            ),
        ]
        rs = _CompiledRuleSet([], ar_rules, "black_on_beige", True)
        assert rs.finditer("foo") == []

    def test_combines_highlight_and_autoreply(self):
        from telnetlib3.autoreply import AutoreplyRule

        ar_rules = [
            AutoreplyRule(
                pattern=re.compile("monster", _RE_FLAGS), reply="flee", enabled=True
            ),
        ]
        hl_rules = [_make_rule("dynamite", "bold_red")]
        rs = _CompiledRuleSet(hl_rules, ar_rules, "black_on_beige", True)
        spans = rs.finditer("a monster has dynamite")
        assert len(spans) == 2
        assert spans[0][2] == "black_on_beige"
        assert spans[1][2] == "bold_red"

    def test_overlap_first_wins(self):
        hl_rules = [
            _make_rule("abc", "bold_red"),
            _make_rule("bc", "black_on_beige"),
        ]
        rs = _CompiledRuleSet(hl_rules, [], "black_on_beige", True)
        spans = rs.finditer("xabcx")
        assert len(spans) == 1
        assert spans[0][2] == "bold_red"


class TestHighlightEngineProcessLine:

    def test_no_match_passthrough(self):
        engine = HighlightEngine(
            [_make_rule("dynamite")], [], _mock_term()
        )
        line = "nothing interesting here"
        result, matched = engine.process_line(line)
        assert result == line
        assert matched is False

    def test_simple_match(self):
        engine = HighlightEngine(
            [_make_rule("danger", "bold_red")], [], _mock_term()
        )
        line = "there is danger ahead"
        result, matched = engine.process_line(line)
        assert matched is True
        assert "danger" in result
        assert "\x1b[1;31m" in result
        assert "\x1b[0m" in result

    def test_case_insensitive(self):
        engine = HighlightEngine(
            [_make_rule("DANGER", "bold_red")], [], _mock_term()
        )
        line = "there is danger ahead"
        result, matched = engine.process_line(line)
        assert matched is True
        assert "\x1b[1;31m" in result

    def test_preserves_existing_sgr(self):
        engine = HighlightEngine(
            [_make_rule("def", "bold_red")], [], _mock_term()
        )
        line = "\x1b[36mabc def ghi\x1b[0m"
        result, matched = engine.process_line(line)
        assert matched is True
        assert "\x1b[36m" in result
        assert "\x1b[1;31m" in result

    def test_multiple_matches(self):
        engine = HighlightEngine(
            [_make_rule("cat", "bold_red")], [], _mock_term()
        )
        line = "the cat sat on the cat"
        result, matched = engine.process_line(line)
        assert matched is True
        assert result.count("\x1b[1;31m") == 2

    def test_disabled_engine(self):
        engine = HighlightEngine(
            [_make_rule("danger")], [], _mock_term()
        )
        engine.enabled = False
        line = "there is danger ahead"
        result, matched = engine.process_line(line)
        assert result == line
        assert matched is False

    def test_disabled_rule(self):
        engine = HighlightEngine(
            [_make_rule("danger", enabled=False)], [], _mock_term()
        )
        line = "there is danger ahead"
        result, matched = engine.process_line(line)
        assert result == line
        assert matched is False

    def test_empty_line(self):
        engine = HighlightEngine(
            [_make_rule("danger")], [], _mock_term()
        )
        result, matched = engine.process_line("")
        assert result == ""
        assert matched is False

    def test_sequence_only_line(self):
        engine = HighlightEngine(
            [_make_rule("danger")], [], _mock_term()
        )
        result, matched = engine.process_line("\x1b[0m")
        assert matched is False


class TestHighlightEngineStopMovement:

    def test_cancels_discover(self):
        ctx = MagicMock()
        ctx.discover_active = True
        ctx.discover_task = MagicMock()
        ctx.randomwalk_active = False
        engine = HighlightEngine(
            [_make_rule("danger", stop_movement=True)], [], _mock_term(), ctx=ctx
        )
        result, _ = engine.process_line("there is danger ahead")
        ctx.discover_task.cancel.assert_called_once()
        assert ctx.discover_active is False
        assert "[stop: discover cancelled]" in result

    def test_cancels_randomwalk(self):
        ctx = MagicMock()
        ctx.discover_active = False
        ctx.randomwalk_active = True
        ctx.randomwalk_task = MagicMock()
        engine = HighlightEngine(
            [_make_rule("danger", stop_movement=True)], [], _mock_term(), ctx=ctx
        )
        result, _ = engine.process_line("there is danger ahead")
        ctx.randomwalk_task.cancel.assert_called_once()
        assert ctx.randomwalk_active is False
        assert "[stop: random walk cancelled]" in result

    def test_no_stop_without_flag(self):
        ctx = MagicMock()
        ctx.discover_active = True
        ctx.discover_task = MagicMock()
        engine = HighlightEngine(
            [_make_rule("danger", stop_movement=False)], [], _mock_term(), ctx=ctx
        )
        engine.process_line("there is danger ahead")
        ctx.discover_task.cancel.assert_not_called()


class TestHighlightEngineAutoreplyBuiltin:

    def test_builtin_autoreply_highlight(self):
        from telnetlib3.autoreply import AutoreplyRule

        ar_rules = [
            AutoreplyRule(
                pattern=re.compile("monster", _RE_FLAGS), reply="flee", enabled=True
            ),
        ]
        engine = HighlightEngine(
            [], ar_rules, _mock_term(), autoreply_highlight="black_on_beige"
        )
        line = "A monster appears!"
        result, matched = engine.process_line(line)
        assert matched is True
        assert "\x1b[30;43m" in result

    def test_builtin_disabled(self):
        from telnetlib3.autoreply import AutoreplyRule

        ar_rules = [
            AutoreplyRule(
                pattern=re.compile("monster", _RE_FLAGS), reply="flee", enabled=True
            ),
        ]
        engine = HighlightEngine(
            [], ar_rules, _mock_term(), autoreply_enabled=False
        )
        line = "A monster appears!"
        result, matched = engine.process_line(line)
        assert matched is False


class TestAutoreplyCaseInsensitive:

    def test_case_insensitive_matching(self):
        from telnetlib3.autoreply import _parse_entries

        entries = [{"pattern": "DANGER", "reply": "flee"}]
        rules = _parse_entries(entries)
        assert rules[0].pattern.search("there is danger ahead") is not None
        assert rules[0].pattern.search("DANGER") is not None
        assert rules[0].pattern.search("Danger Zone") is not None
