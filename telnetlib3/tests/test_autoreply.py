"""Tests for telnetlib3.autoreply module."""

from __future__ import annotations

# std imports
import re
import json
import types
import asyncio
import logging

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
    save_autoreplies,
    _substitute_groups,
)


def test_search_buffer_add_text_strips_ansi():
    buf = SearchBuffer(max_lines=100)
    buf.add_text("\x1b[31mhello\x1b[m\n")
    assert buf.lines == ["hello"]


def test_search_buffer_add_text_no_newline_is_partial():
    buf = SearchBuffer(max_lines=100)
    result = buf.add_text("partial")
    assert result is False
    assert buf.partial == "partial"
    assert buf.lines == []


def test_search_buffer_add_text_newline_completes_line():
    buf = SearchBuffer(max_lines=100)
    buf.add_text("partial")
    result = buf.add_text(" more\n")
    assert result is True
    assert buf.lines == ["partial more"]
    assert buf.partial == ""


def test_search_buffer_add_text_multiple_lines():
    buf = SearchBuffer(max_lines=100)
    result = buf.add_text("line1\nline2\nline3\n")
    assert result is True
    assert buf.lines == ["line1", "line2", "line3"]


def test_search_buffer_add_text_trailing_partial():
    buf = SearchBuffer(max_lines=100)
    buf.add_text("line1\npartial")
    assert buf.lines == ["line1"]
    assert buf.partial == "partial"


def test_search_buffer_add_text_empty():
    buf = SearchBuffer(max_lines=100)
    assert buf.add_text("") is False


def test_search_buffer_cull_old_lines():
    buf = SearchBuffer(max_lines=3)
    buf.add_text("a\nb\nc\nd\ne\n")
    assert len(buf.lines) == 3
    assert buf.lines == ["c", "d", "e"]


def test_search_buffer_cull_adjusts_match_position():
    buf = SearchBuffer(max_lines=3)
    buf.add_text("a\nb\n")
    buf._last_match_line = 1
    buf._last_match_col = 0
    buf.add_text("c\nd\ne\n")
    assert buf._last_match_line >= 0
    assert len(buf.lines) == 3


def test_search_buffer_cull_preserves_nonzero_match_line():
    buf = SearchBuffer(max_lines=3)
    buf.add_text("a\nb\nc\nd\n")
    buf._last_match_line = 3
    buf._last_match_col = 1
    buf.add_text("e\n")
    assert buf._last_match_line == 2
    assert buf._last_match_col == 1


def test_search_buffer_searchable_text_from_start():
    buf = SearchBuffer(max_lines=100)
    buf.add_text("hello\nworld\n")
    assert buf.get_searchable_text() == "hello\nworld"


def test_search_buffer_searchable_text_from_position():
    buf = SearchBuffer(max_lines=100)
    buf.add_text("aaa\nbbb\nccc\n")
    buf._last_match_line = 1
    buf._last_match_col = 0
    assert buf.get_searchable_text() == "bbb\nccc"


def test_search_buffer_searchable_text_with_col_offset():
    buf = SearchBuffer(max_lines=100)
    buf.add_text("aaa\nbbb\n")
    buf._last_match_line = 0
    buf._last_match_col = 2
    assert buf.get_searchable_text() == "a\nbbb"


def test_search_buffer_searchable_text_past_end():
    buf = SearchBuffer(max_lines=100)
    buf.add_text("one\n")
    buf._last_match_line = 5
    assert buf.get_searchable_text() == ""


def test_search_buffer_searchable_text_includes_partial():
    buf = SearchBuffer(max_lines=100)
    buf.add_text("line1\npartial prompt")
    text = buf.get_searchable_text()
    assert "partial prompt" in text
    assert text == "line1\npartial prompt"


def test_search_buffer_searchable_text_partial_only():
    buf = SearchBuffer(max_lines=100)
    buf.add_text("just a prompt")
    assert buf.get_searchable_text() == "just a prompt"


def test_search_buffer_advance_match():
    buf = SearchBuffer(max_lines=100)
    buf.add_text("hello world\n")
    searchable = buf.get_searchable_text()
    m = re.search("world", searchable)
    assert m is not None
    buf.advance_match(m.start(), len(m.group(0)))
    remaining = buf.get_searchable_text()
    assert "world" not in remaining


def test_load_autoreplies_valid(tmp_path):
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


def test_load_autoreplies_missing_file():
    with pytest.raises(FileNotFoundError):
        load_autoreplies("/nonexistent/path.json")


def test_load_autoreplies_invalid_regex(tmp_path):
    fp = tmp_path / "bad.json"
    fp.write_text(json.dumps({"autoreplies": [{"pattern": "[invalid", "reply": "x"}]}))
    with pytest.raises(ValueError, match="Invalid autoreply pattern"):
        load_autoreplies(str(fp))


def test_load_autoreplies_empty_pattern_skipped(tmp_path):
    fp = tmp_path / "empty.json"
    fp.write_text(
        json.dumps(
            {"autoreplies": [{"pattern": "", "reply": "x"}, {"pattern": "valid", "reply": "y"}]}
        )
    )
    rules = load_autoreplies(str(fp))
    assert len(rules) == 1


def test_load_autoreplies_empty_list(tmp_path):
    fp = tmp_path / "empty.json"
    fp.write_text(json.dumps({"autoreplies": []}))
    assert load_autoreplies(str(fp)) == []


def test_save_autoreplies_roundtrip(tmp_path):
    fp = tmp_path / "autoreplies.json"
    original = [
        AutoreplyRule(pattern=re.compile(r"\d+ gold"), reply="get gold<CR>"),
        AutoreplyRule(
            pattern=re.compile(r"(\w+) attacks", re.MULTILINE | re.DOTALL),
            reply="kill \\1<CR>",
        ),
    ]
    save_autoreplies(str(fp), original)
    loaded = load_autoreplies(str(fp))
    assert len(loaded) == len(original)
    for orig, restored in zip(original, loaded):
        assert orig.pattern.pattern == restored.pattern.pattern
        assert orig.reply == restored.reply


def test_save_autoreplies_empty(tmp_path):
    fp = tmp_path / "autoreplies.json"
    save_autoreplies(str(fp), [])
    assert load_autoreplies(str(fp)) == []


def test_save_autoreplies_unicode(tmp_path):
    fp = tmp_path / "autoreplies.json"
    rules = [AutoreplyRule(pattern=re.compile("héllo"), reply="bonjour<CR>")]
    save_autoreplies(str(fp), rules)
    loaded = load_autoreplies(str(fp))
    assert loaded[0].pattern.pattern == "héllo"
    assert loaded[0].reply == "bonjour<CR>"


def test_substitute_groups_single():
    m = re.search(r"(\w+) gold", "50 gold coins")
    assert m is not None
    assert _substitute_groups("take \\1 gold", m) == "take 50 gold"


def test_substitute_groups_multiple():
    m = re.search(r"(\w+) (\w+)", "hello world")
    assert m is not None
    assert _substitute_groups("\\2 \\1", m) == "world hello"


def test_substitute_groups_none():
    m = re.search(r"hello", "hello world")
    assert m is not None
    assert _substitute_groups("say hello", m) == "say hello"


def test_substitute_groups_invalid_index():
    m = re.search(r"(\w+)", "hello")
    assert m is not None
    assert _substitute_groups("\\1 \\5", m) == "hello \\5"


@pytest.mark.parametrize(
    "token,expected",
    [
        ("::100ms::", 0.1),
        ("::1s::", 1.0),
        ("::2.5s::", 2.5),
        ("::500ms::", 0.5),
        ("::0.5s::", 0.5),
        ("invalid", 0.0),
    ],
)
def test_parse_delay(token, expected):
    assert _parse_delay(token) == pytest.approx(expected)


def _mock_writer():
    """Create a mock writer that records write() calls."""
    written: list[str] = []
    writer = types.SimpleNamespace(
        write=lambda text: written.append(text), log=logging.getLogger("test")
    )
    return writer, written


@pytest.mark.asyncio
async def test_autoreply_engine_feed_triggers_match():
    writer, written = _mock_writer()
    rules = [AutoreplyRule(pattern=re.compile(r"hello"), reply="world<CR>")]
    engine = AutoreplyEngine(rules, writer, writer.log)
    engine.feed("hello\n")
    await asyncio.sleep(0.05)
    assert any("world\r\n" in w for w in written)


@pytest.mark.asyncio
async def test_autoreply_engine_feed_matches_partial_line():
    writer, written = _mock_writer()
    rules = [AutoreplyRule(pattern=re.compile(r"hello"), reply="world<CR>")]
    engine = AutoreplyEngine(rules, writer, writer.log)
    engine.feed("hello")
    await asyncio.sleep(0.05)
    assert any("world\r\n" in w for w in written)


@pytest.mark.asyncio
async def test_autoreply_engine_mud_prompt_without_newline():
    writer, written = _mock_writer()
    rules = [
        AutoreplyRule(
            pattern=re.compile(r"What is your name\?"),
            reply="dingo<CR>",
        )
    ]
    engine = AutoreplyEngine(rules, writer, writer.log)
    engine.feed("Welcome to the Mini-MUD!\n")
    await asyncio.sleep(0.05)
    assert not any("dingo" in w for w in written)
    engine.feed("What is your name? ")
    await asyncio.sleep(0.05)
    assert any("dingo\r\n" in w for w in written)


@pytest.mark.asyncio
async def test_autoreply_engine_no_double_trigger():
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
async def test_autoreply_engine_group_substitution():
    writer, written = _mock_writer()
    rules = [
        AutoreplyRule(
            pattern=re.compile(r"a (\w+) (pheasant|duck)"), reply="kill \\2<CR>"
        )
    ]
    engine = AutoreplyEngine(rules, writer, writer.log)
    engine.feed("a black pheasant\n")
    await asyncio.sleep(0.05)
    assert any("kill pheasant\r\n" in w for w in written)


@pytest.mark.asyncio
async def test_autoreply_engine_delay_execution():
    writer, written = _mock_writer()
    rules = [AutoreplyRule(pattern=re.compile(r"trigger"), reply="::50ms::delayed<CR>")]
    engine = AutoreplyEngine(rules, writer, writer.log)
    engine.feed("trigger\n")
    await asyncio.sleep(0.01)
    assert not any("delayed" in w for w in written)
    await asyncio.sleep(0.1)
    assert any("delayed\r\n" in w for w in written)


@pytest.mark.asyncio
async def test_autoreply_engine_reply_chaining():
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
async def test_autoreply_engine_cancel():
    writer, written = _mock_writer()
    rules = [AutoreplyRule(pattern=re.compile(r"slow"), reply="::1s::result<CR>")]
    engine = AutoreplyEngine(rules, writer, writer.log)
    engine.feed("slow\n")
    await asyncio.sleep(0.01)
    engine.cancel()
    await asyncio.sleep(0.1)
    assert not any("result" in w for w in written)


@pytest.mark.asyncio
async def test_autoreply_engine_multiline_match():
    writer, written = _mock_writer()
    rules = [
        AutoreplyRule(pattern=re.compile(r"start.*end", re.DOTALL), reply="matched<CR>")
    ]
    engine = AutoreplyEngine(rules, writer, writer.log)
    engine.feed("start\nmiddle\nend\n")
    await asyncio.sleep(0.05)
    assert any("matched\r\n" in w for w in written)


@pytest.mark.asyncio
async def test_autoreply_engine_echo_to_stdout():
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
async def test_autoreply_engine_multi_command_reply():
    writer, written = _mock_writer()
    rules = [AutoreplyRule(pattern=re.compile(r"multi"), reply="cmd1<CR>cmd2<CR>")]
    engine = AutoreplyEngine(rules, writer, writer.log)
    engine.feed("multi\n")
    await asyncio.sleep(0.05)
    assert any("cmd1\r\n" in w for w in written)
    assert any("cmd2\r\n" in w for w in written)


@pytest.mark.asyncio
async def test_autoreply_engine_trailing_text_without_cr():
    writer, written = _mock_writer()
    rules = [AutoreplyRule(pattern=re.compile(r"trigger"), reply="no-cr-reply")]
    engine = AutoreplyEngine(rules, writer, writer.log)
    engine.feed("trigger\n")
    await asyncio.sleep(0.05)
    assert any("no-cr-reply\r\n" in w for w in written)


@pytest.mark.asyncio
async def test_autoreply_engine_zero_delay():
    writer, written = _mock_writer()
    rules = [AutoreplyRule(pattern=re.compile(r"trigger"), reply="::0ms::fast<CR>")]
    engine = AutoreplyEngine(rules, writer, writer.log)
    engine.feed("trigger\n")
    await asyncio.sleep(0.05)
    assert any("fast\r\n" in w for w in written)


def test_autoreply_engine_cancel_when_idle():
    writer, _ = _mock_writer()
    engine = AutoreplyEngine([], writer, writer.log)
    engine.cancel()
    assert engine._reply_chain is None


def test_send_command_empty_string():
    writer, written = _mock_writer()
    engine = AutoreplyEngine([], writer, writer.log)
    engine._send_command("")
    assert not written


def test_send_command_whitespace_only():
    writer, written = _mock_writer()
    engine = AutoreplyEngine([], writer, writer.log)
    engine._send_command("   ")
    assert not written


def test_send_command_valid():
    writer, written = _mock_writer()
    engine = AutoreplyEngine([], writer, writer.log)
    engine._send_command("look")
    assert "look\r\n" in written
