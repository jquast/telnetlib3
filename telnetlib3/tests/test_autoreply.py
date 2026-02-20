"""Tests for telnetlib3.autoreply module."""

from __future__ import annotations

# std imports
import re
import json
import types
import asyncio
import logging
from unittest import mock

# 3rd party
import pytest

# local
from telnetlib3.autoreply import (
    _DELAY_RE,
    SearchBuffer,
    AutoreplyRule,
    AutoreplyEngine,
    _parse_delay,
    load_autoreplies,
    _substitute_groups,
)

# ---------------------------------------------------------------------------
# SearchBuffer
# ---------------------------------------------------------------------------


class TestSearchBuffer:

    def test_add_text_strips_ansi(self):
        buf = SearchBuffer(max_lines=100)
        buf.add_text("\x1b[31mhello\x1b[m\n")
        assert buf.lines == ["hello"]

    def test_add_text_no_newline_is_partial(self):
        buf = SearchBuffer(max_lines=100)
        result = buf.add_text("partial")
        assert result is False
        assert buf.partial == "partial"
        assert buf.lines == []

    def test_add_text_newline_completes_line(self):
        buf = SearchBuffer(max_lines=100)
        buf.add_text("partial")
        result = buf.add_text(" more\n")
        assert result is True
        assert buf.lines == ["partial more"]
        assert buf.partial == ""

    def test_add_text_multiple_lines(self):
        buf = SearchBuffer(max_lines=100)
        result = buf.add_text("line1\nline2\nline3\n")
        assert result is True
        assert buf.lines == ["line1", "line2", "line3"]

    def test_add_text_trailing_partial(self):
        buf = SearchBuffer(max_lines=100)
        buf.add_text("line1\npartial")
        assert buf.lines == ["line1"]
        assert buf.partial == "partial"

    def test_add_text_empty(self):
        buf = SearchBuffer(max_lines=100)
        result = buf.add_text("")
        assert result is False

    def test_cull_old_lines(self):
        buf = SearchBuffer(max_lines=3)
        buf.add_text("a\nb\nc\nd\ne\n")
        assert len(buf.lines) == 3
        assert buf.lines == ["c", "d", "e"]

    def test_cull_adjusts_match_position(self):
        buf = SearchBuffer(max_lines=3)
        buf.add_text("a\nb\n")
        buf._last_match_line = 1
        buf._last_match_col = 0
        buf.add_text("c\nd\ne\n")
        assert buf._last_match_line >= 0
        assert len(buf.lines) == 3

    def test_searchable_text_from_start(self):
        buf = SearchBuffer(max_lines=100)
        buf.add_text("hello\nworld\n")
        text = buf.get_searchable_text()
        assert text == "hello\nworld"

    def test_searchable_text_from_position(self):
        buf = SearchBuffer(max_lines=100)
        buf.add_text("aaa\nbbb\nccc\n")
        buf._last_match_line = 1
        buf._last_match_col = 0
        text = buf.get_searchable_text()
        assert text == "bbb\nccc"

    def test_searchable_text_with_col_offset(self):
        buf = SearchBuffer(max_lines=100)
        buf.add_text("aaa\nbbb\n")
        buf._last_match_line = 0
        buf._last_match_col = 2
        text = buf.get_searchable_text()
        assert text == "a\nbbb"

    def test_searchable_text_past_end(self):
        buf = SearchBuffer(max_lines=100)
        buf.add_text("one\n")
        buf._last_match_line = 5
        assert buf.get_searchable_text() == ""

    def test_advance_match(self):
        buf = SearchBuffer(max_lines=100)
        buf.add_text("hello world\n")
        searchable = buf.get_searchable_text()
        m = re.search("world", searchable)
        assert m is not None
        buf.advance_match(m.start(), len(m.group(0)))
        remaining = buf.get_searchable_text()
        assert "world" not in remaining


# ---------------------------------------------------------------------------
# load_autoreplies
# ---------------------------------------------------------------------------


class TestLoadAutoreplies:

    def test_load_valid(self, tmp_path):
        fp = tmp_path / "autoreplies.json"
        fp.write_text(
            json.dumps(
                {
                    "autoreplies": [
                        {"pattern": r"\d+ gold", "reply": "get gold<CR>"},
                        {"pattern": r"(\w+) attacks", "reply": "kill \\1<CR>"},
                    ]
                }
            )
        )
        rules = load_autoreplies(str(fp))
        assert len(rules) == 2
        assert rules[0].pattern.pattern == r"\d+ gold"
        assert rules[0].reply == "get gold<CR>"
        assert rules[1].reply == "kill \\1<CR>"

    def test_load_missing_file(self):
        with pytest.raises(FileNotFoundError):
            load_autoreplies("/nonexistent/path.json")

    def test_load_invalid_regex(self, tmp_path):
        fp = tmp_path / "bad.json"
        fp.write_text(json.dumps({"autoreplies": [{"pattern": "[invalid", "reply": "x"}]}))
        with pytest.raises(ValueError, match="Invalid autoreply pattern"):
            load_autoreplies(str(fp))

    def test_load_empty_pattern_skipped(self, tmp_path):
        fp = tmp_path / "empty.json"
        fp.write_text(
            json.dumps(
                {"autoreplies": [{"pattern": "", "reply": "x"}, {"pattern": "valid", "reply": "y"}]}
            )
        )
        rules = load_autoreplies(str(fp))
        assert len(rules) == 1

    def test_load_empty_list(self, tmp_path):
        fp = tmp_path / "empty.json"
        fp.write_text(json.dumps({"autoreplies": []}))
        rules = load_autoreplies(str(fp))
        assert rules == []


# ---------------------------------------------------------------------------
# _substitute_groups
# ---------------------------------------------------------------------------


class TestSubstituteGroups:

    def test_single_group(self):
        m = re.search(r"(\w+) gold", "50 gold coins")
        assert m is not None
        result = _substitute_groups("take \\1 gold", m)
        assert result == "take 50 gold"

    def test_multiple_groups(self):
        m = re.search(r"(\w+) (\w+)", "hello world")
        assert m is not None
        result = _substitute_groups("\\2 \\1", m)
        assert result == "world hello"

    def test_no_groups(self):
        m = re.search(r"hello", "hello world")
        assert m is not None
        result = _substitute_groups("say hello", m)
        assert result == "say hello"

    def test_invalid_group_index(self):
        m = re.search(r"(\w+)", "hello")
        assert m is not None
        result = _substitute_groups("\\1 \\5", m)
        assert result == "hello \\5"


# ---------------------------------------------------------------------------
# _parse_delay
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "token,expected",
    [("::100ms::", 0.1), ("::1s::", 1.0), ("::2.5s::", 2.5), ("::500ms::", 0.5), ("::0.5s::", 0.5)],
)
def test_parse_delay(token, expected):
    assert _parse_delay(token) == pytest.approx(expected)


# ---------------------------------------------------------------------------
# AutoreplyEngine
# ---------------------------------------------------------------------------


def _mock_writer():
    """Create a mock writer that records write() calls."""
    written: list[str] = []
    writer = types.SimpleNamespace(
        write=lambda text: written.append(text), log=logging.getLogger("test")
    )
    return writer, written


class TestAutoreplyEngine:

    @pytest.mark.asyncio
    async def test_feed_triggers_match(self):
        writer, written = _mock_writer()
        rules = [AutoreplyRule(pattern=re.compile(r"hello"), reply="world<CR>")]
        engine = AutoreplyEngine(rules, writer, writer.log)
        engine.feed("hello\n")
        # Wait for async task to execute.
        await asyncio.sleep(0.05)
        assert any("world\r\n" in w for w in written)

    @pytest.mark.asyncio
    async def test_feed_no_match_on_partial(self):
        writer, written = _mock_writer()
        rules = [AutoreplyRule(pattern=re.compile(r"hello"), reply="world<CR>")]
        engine = AutoreplyEngine(rules, writer, writer.log)
        engine.feed("hello")  # no newline
        await asyncio.sleep(0.05)
        assert not written

    @pytest.mark.asyncio
    async def test_no_double_trigger(self):
        writer, written = _mock_writer()
        rules = [AutoreplyRule(pattern=re.compile(r"hello"), reply="world<CR>")]
        engine = AutoreplyEngine(rules, writer, writer.log)
        engine.feed("hello\n")
        await asyncio.sleep(0.05)
        count1 = len(written)
        engine.feed("more text\n")
        await asyncio.sleep(0.05)
        assert len(written) == count1

    @pytest.mark.asyncio
    async def test_group_substitution(self):
        writer, written = _mock_writer()
        rules = [
            AutoreplyRule(pattern=re.compile(r"a (\w+) (pheasant|duck)"), reply="kill \\2<CR>")
        ]
        engine = AutoreplyEngine(rules, writer, writer.log)
        engine.feed("a black pheasant\n")
        await asyncio.sleep(0.05)
        assert any("kill pheasant\r\n" in w for w in written)

    @pytest.mark.asyncio
    async def test_delay_execution(self):
        writer, written = _mock_writer()
        rules = [AutoreplyRule(pattern=re.compile(r"trigger"), reply="::50ms::delayed<CR>")]
        engine = AutoreplyEngine(rules, writer, writer.log)
        engine.feed("trigger\n")
        await asyncio.sleep(0.01)
        assert not any("delayed" in w for w in written)
        await asyncio.sleep(0.1)
        assert any("delayed\r\n" in w for w in written)

    @pytest.mark.asyncio
    async def test_reply_chaining(self):
        writer, written = _mock_writer()
        rules = [
            AutoreplyRule(pattern=re.compile(r"alpha"), reply="::100ms::first<CR>"),
            AutoreplyRule(pattern=re.compile(r"beta"), reply="second<CR>"),
        ]
        engine = AutoreplyEngine(rules, writer, writer.log)
        engine.feed("alpha and beta\n")
        await asyncio.sleep(0.01)
        assert not any("first" in w for w in written)
        await asyncio.sleep(0.15)
        assert any("first\r\n" in w for w in written)
        assert any("second\r\n" in w for w in written)

    @pytest.mark.asyncio
    async def test_cancel(self):
        writer, written = _mock_writer()
        rules = [AutoreplyRule(pattern=re.compile(r"slow"), reply="::1s::result<CR>")]
        engine = AutoreplyEngine(rules, writer, writer.log)
        engine.feed("slow\n")
        await asyncio.sleep(0.01)
        engine.cancel()
        await asyncio.sleep(0.1)
        assert not any("result" in w for w in written)

    @pytest.mark.asyncio
    async def test_multiline_match(self):
        writer, written = _mock_writer()
        rules = [AutoreplyRule(pattern=re.compile(r"start.*end", re.DOTALL), reply="matched<CR>")]
        engine = AutoreplyEngine(rules, writer, writer.log)
        engine.feed("start\nmiddle\nend\n")
        await asyncio.sleep(0.05)
        assert any("matched\r\n" in w for w in written)

    @pytest.mark.asyncio
    async def test_echo_to_stdout(self):
        writer, written = _mock_writer()
        stdout_data: list[bytes] = []
        stdout = types.SimpleNamespace(write=lambda data: stdout_data.append(data))
        rules = [AutoreplyRule(pattern=re.compile(r"ping"), reply="pong<CR>")]
        engine = AutoreplyEngine(rules, writer, writer.log, stdout=stdout)
        engine.feed("ping\n")
        await asyncio.sleep(0.05)
        echo = b"".join(stdout_data).decode()
        assert "[auto]" in echo
        assert "pong" in echo

    @pytest.mark.asyncio
    async def test_multi_command_reply(self):
        writer, written = _mock_writer()
        rules = [AutoreplyRule(pattern=re.compile(r"multi"), reply="cmd1<CR>cmd2<CR>")]
        engine = AutoreplyEngine(rules, writer, writer.log)
        engine.feed("multi\n")
        await asyncio.sleep(0.05)
        assert any("cmd1\r\n" in w for w in written)
        assert any("cmd2\r\n" in w for w in written)
