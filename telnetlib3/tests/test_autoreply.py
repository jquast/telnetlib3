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
    check_condition,
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


@pytest.mark.parametrize(
    "text,match_line,match_col,expected",
    [
        ("hello\nworld\n", None, None, "hello\nworld"),
        ("aaa\nbbb\nccc\n", 1, 0, "bbb\nccc"),
        ("aaa\nbbb\n", 0, 2, "a\nbbb"),
        ("one\n", 5, 0, ""),
        ("line1\npartial prompt", None, None, "line1\npartial prompt"),
        ("just a prompt", None, None, "just a prompt"),
    ],
)
def test_search_buffer_searchable_text(text, match_line, match_col, expected):
    buf = SearchBuffer(max_lines=100)
    buf.add_text(text)
    if match_line is not None:
        buf._last_match_line = match_line
        buf._last_match_col = match_col
    assert buf.get_searchable_text() == expected


def test_search_buffer_advance_match():
    buf = SearchBuffer(max_lines=100)
    buf.add_text("hello world\n")
    searchable = buf.get_searchable_text()
    m = re.search("world", searchable)
    assert m is not None
    buf.advance_match(m.start(), len(m.group(0)))
    remaining = buf.get_searchable_text()
    assert "world" not in remaining


_SK = "test.host:23"


def test_load_autoreplies_valid(tmp_path):
    fp = tmp_path / "autoreplies.json"
    fp.write_text(
        json.dumps(
            {
                _SK: {
                    "autoreplies": [
                        {"pattern": r"\d+ gold", "reply": "get gold;"},
                        {"pattern": r"(\w+) attacks", "reply": "kill \\1;"},
                    ]
                }
            }
        )
    )
    rules = load_autoreplies(str(fp), _SK)
    assert len(rules) == 2
    assert rules[0].pattern.pattern == r"\d+ gold"
    assert rules[0].reply == "get gold;"
    assert rules[1].reply == "kill \\1;"


def test_load_autoreplies_missing_file():
    with pytest.raises(FileNotFoundError):
        load_autoreplies("/nonexistent/path.json", _SK)


def test_load_autoreplies_invalid_regex(tmp_path):
    fp = tmp_path / "bad.json"
    fp.write_text(json.dumps({_SK: {"autoreplies": [{"pattern": "[invalid", "reply": "x"}]}}))
    with pytest.raises(ValueError, match="Invalid autoreply pattern"):
        load_autoreplies(str(fp), _SK)


def test_load_autoreplies_empty_pattern_skipped(tmp_path):
    fp = tmp_path / "empty.json"
    fp.write_text(
        json.dumps(
            {
                _SK: {
                    "autoreplies": [
                        {"pattern": "", "reply": "x"},
                        {"pattern": "valid", "reply": "y"},
                    ]
                }
            }
        )
    )
    rules = load_autoreplies(str(fp), _SK)
    assert len(rules) == 1


def test_load_autoreplies_empty_list(tmp_path):
    fp = tmp_path / "empty.json"
    fp.write_text(json.dumps({_SK: {"autoreplies": []}}))
    assert load_autoreplies(str(fp), _SK) == []


def test_load_autoreplies_no_session(tmp_path):
    fp = tmp_path / "autoreplies.json"
    fp.write_text(json.dumps({"other:23": {"autoreplies": [{"pattern": "x", "reply": "y"}]}}))
    assert load_autoreplies(str(fp), _SK) == []


def test_save_autoreplies_roundtrip(tmp_path):
    fp = tmp_path / "autoreplies.json"
    original = [
        AutoreplyRule(pattern=re.compile(r"\d+ gold"), reply="get gold;"),
        AutoreplyRule(
            pattern=re.compile(r"(\w+) attacks", re.MULTILINE | re.DOTALL), reply="kill \\1;"
        ),
    ]
    save_autoreplies(str(fp), original, _SK)
    loaded = load_autoreplies(str(fp), _SK)
    assert len(loaded) == len(original)
    for orig, restored in zip(original, loaded):
        assert orig.pattern.pattern == restored.pattern.pattern
        assert orig.reply == restored.reply


def test_save_autoreplies_preserves_other_sessions(tmp_path):
    fp = tmp_path / "autoreplies.json"
    r1 = [AutoreplyRule(pattern=re.compile("a"), reply="b")]
    r2 = [AutoreplyRule(pattern=re.compile("c"), reply="d")]
    save_autoreplies(str(fp), r1, "host1:23")
    save_autoreplies(str(fp), r2, "host2:23")
    assert len(load_autoreplies(str(fp), "host1:23")) == 1
    assert len(load_autoreplies(str(fp), "host2:23")) == 1


def test_save_autoreplies_empty(tmp_path):
    fp = tmp_path / "autoreplies.json"
    save_autoreplies(str(fp), [], _SK)
    assert load_autoreplies(str(fp), _SK) == []


def test_save_autoreplies_unicode(tmp_path):
    fp = tmp_path / "autoreplies.json"
    rules = [AutoreplyRule(pattern=re.compile("héllo"), reply="bonjour;")]
    save_autoreplies(str(fp), rules, _SK)
    loaded = load_autoreplies(str(fp), _SK)
    assert loaded[0].pattern.pattern == "héllo"
    assert loaded[0].reply == "bonjour;"


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
        ("`delay 100ms`", 0.1),
        ("`delay 1s`", 1.0),
        ("`delay 2.5s`", 2.5),
        ("`delay 500ms`", 0.5),
        ("`delay 0.5s`", 0.5),
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
@pytest.mark.parametrize(
    "pattern,flags,reply,feed_text,expected",
    [
        (r"hello", 0, "world;", "hello\n", ["world\r\n"]),
        (r"hello", 0, "world;", "hello", ["world\r\n"]),
        (r"a (\w+) (pheasant|duck)", 0, "kill \\2;", "a black pheasant\n", ["kill pheasant\r\n"]),
        (r"start.*end", re.DOTALL, "matched;", "start\nmiddle\nend\n", ["matched\r\n"]),
        (r"multi", 0, "cmd1;cmd2;", "multi\n", ["cmd1\r\n", "cmd2\r\n"]),
        (r"go", 0, "cmd1;cmd2;cmd3;", "go\n", ["cmd1\r\n", "cmd2\r\n", "cmd3\r\n"]),
        (r"trigger", 0, "no-cr-reply", "trigger\n", ["no-cr-reply\r\n"]),
        (r"trigger", 0, "`delay 0ms`;fast;", "trigger\n", ["fast\r\n"]),
    ],
)
async def test_autoreply_engine_feed_and_match(pattern, flags, reply, feed_text, expected):
    writer, written = _mock_writer()
    rules = [AutoreplyRule(pattern=re.compile(pattern, flags), reply=reply)]
    engine = AutoreplyEngine(rules, writer, writer.log)
    engine.feed(feed_text)
    await asyncio.sleep(0.05)
    for exp in expected:
        assert any(exp in w for w in written)


@pytest.mark.asyncio
async def test_autoreply_engine_mud_prompt_without_newline():
    writer, written = _mock_writer()
    rules = [AutoreplyRule(pattern=re.compile(r"What is your name\?"), reply="dingo;")]
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
    rules = [AutoreplyRule(pattern=re.compile(r"hello"), reply="world;")]
    engine = AutoreplyEngine(rules, writer, writer.log)
    engine.feed("hello\n")
    await asyncio.sleep(0.05)
    count1 = len(written)
    engine.feed("more text\n")
    await asyncio.sleep(0.05)
    assert len(written) == count1


@pytest.mark.asyncio
async def test_autoreply_engine_delay_execution():
    writer, written = _mock_writer()
    rules = [AutoreplyRule(pattern=re.compile(r"trigger"), reply="`delay 50ms`;delayed;")]
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
        AutoreplyRule(pattern=re.compile(r"alpha"), reply="`delay 100ms`;first;"),
        AutoreplyRule(pattern=re.compile(r"beta"), reply="second;"),
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
    rules = [AutoreplyRule(pattern=re.compile(r"slow"), reply="`delay 1s`;result;")]
    engine = AutoreplyEngine(rules, writer, writer.log)
    engine.feed("slow\n")
    await asyncio.sleep(0.01)
    engine.cancel()
    await asyncio.sleep(0.1)
    assert not any("result" in w for w in written)


@pytest.mark.asyncio
async def test_autoreply_engine_repeat_expansion():
    writer, written = _mock_writer()
    rules = [AutoreplyRule(pattern=re.compile(r"go"), reply="3e;2n;look;")]
    engine = AutoreplyEngine(rules, writer, writer.log)
    engine.feed("go\n")
    await asyncio.sleep(0.05)
    e_count = sum(1 for w in written if "e\r\n" in w)
    n_count = sum(1 for w in written if "n\r\n" in w)
    assert e_count == 3
    assert n_count == 2
    assert any("look\r\n" in w for w in written)


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


@pytest.mark.asyncio
async def test_autoreply_reply_without_semicolon_still_sends():
    writer, written = _mock_writer()
    inserted: list[str] = []
    rules = [AutoreplyRule(pattern=re.compile(r"Items here: (\w+)"), reply=r"pick up \1")]
    engine = AutoreplyEngine(rules, writer, writer.log, insert_fn=inserted.append)
    engine.feed("Items here: sword\n")
    await asyncio.sleep(0.05)
    assert any("pick up sword\r\n" in w for w in written)
    assert not inserted


@pytest.mark.asyncio
async def test_autoreply_insert_fn_with_cr_sends():
    writer, written = _mock_writer()
    inserted: list[str] = []
    rules = [AutoreplyRule(pattern=re.compile(r"hello"), reply="world;")]
    engine = AutoreplyEngine(rules, writer, writer.log, insert_fn=inserted.append)
    engine.feed("hello\n")
    await asyncio.sleep(0.05)
    assert any("world\r\n" in w for w in written)
    assert not inserted


@pytest.mark.asyncio
async def test_autoreply_no_insert_fn_sends_without_cr():
    writer, written = _mock_writer()
    rules = [AutoreplyRule(pattern=re.compile(r"hello"), reply="world")]
    engine = AutoreplyEngine(rules, writer, writer.log)
    engine.feed("hello\n")
    await asyncio.sleep(0.05)
    assert any("world\r\n" in w for w in written)


@pytest.mark.asyncio
async def test_autoreply_wait_fn_called_before_send():
    """wait_fn is awaited before second command, first sends immediately."""
    writer, written = _mock_writer()
    wait_calls: list[float] = []

    async def _fake_wait() -> None:
        wait_calls.append(asyncio.get_event_loop().time())

    rules = [AutoreplyRule(pattern=re.compile(r"go"), reply="cmd1;cmd2;")]
    engine = AutoreplyEngine(rules, writer, writer.log, wait_fn=_fake_wait)
    engine.feed("go\n")
    await asyncio.sleep(0.1)
    assert len(wait_calls) == 1
    assert any("cmd1\r\n" in w for w in written)
    assert any("cmd2\r\n" in w for w in written)


@pytest.mark.asyncio
async def test_autoreply_wait_fn_none_no_pacing():
    """When wait_fn is None, commands send immediately without pacing."""
    writer, written = _mock_writer()
    rules = [AutoreplyRule(pattern=re.compile(r"go"), reply="cmd1;cmd2;")]
    engine = AutoreplyEngine(rules, writer, writer.log, wait_fn=None)
    engine.feed("go\n")
    await asyncio.sleep(0.05)
    assert any("cmd1\r\n" in w for w in written)
    assert any("cmd2\r\n" in w for w in written)


@pytest.mark.asyncio
async def test_autoreply_wait_fn_timeout_does_not_hang():
    """wait_fn that blocks eventually times out and command still sends."""
    writer, written = _mock_writer()
    never_set = asyncio.Event()

    async def _blocking_wait() -> None:
        try:
            await asyncio.wait_for(never_set.wait(), timeout=0.05)
        except asyncio.TimeoutError:
            pass

    rules = [AutoreplyRule(pattern=re.compile(r"go"), reply="cmd;")]
    engine = AutoreplyEngine(rules, writer, writer.log, wait_fn=_blocking_wait)
    engine.feed("go\n")
    await asyncio.sleep(0.2)
    assert any("cmd\r\n" in w for w in written)


@pytest.mark.asyncio
async def test_autoreply_wait_fn_with_trailing_text():
    """First command sends immediately without wait_fn, even without ;."""
    writer, written = _mock_writer()
    wait_calls: list[int] = []

    async def _fake_wait() -> None:
        wait_calls.append(1)

    rules = [AutoreplyRule(pattern=re.compile(r"go"), reply="trailing")]
    engine = AutoreplyEngine(rules, writer, writer.log, wait_fn=_fake_wait)
    engine.feed("go\n")
    await asyncio.sleep(0.1)
    assert len(wait_calls) == 0
    assert any("trailing\r\n" in w for w in written)


@pytest.mark.asyncio
async def test_exclusive_rule_suppresses_later_matches():
    writer, written = _mock_writer()
    rules = [
        AutoreplyRule(pattern=re.compile(r"A (\w+) pheasant"), reply=r"kill \1;", exclusive=True),
        AutoreplyRule(pattern=re.compile(r"A (\w+) rabbit"), reply=r"kill \1;", exclusive=True),
        AutoreplyRule(pattern=re.compile(r"A (\w+) mouse"), reply=r"kill \1;", exclusive=True),
    ]
    engine = AutoreplyEngine(rules, writer, writer.log)
    engine.feed("A green pheasant\nA red rabbit\nA orange mouse\n")
    await asyncio.sleep(0.05)
    assert any("kill green\r\n" in w for w in written)
    assert not any("kill red\r\n" in w for w in written)
    assert not any("kill orange\r\n" in w for w in written)


@pytest.mark.asyncio
async def test_exclusive_rule_index_tracks_active_rule():
    writer, _ = _mock_writer()
    rules = [
        AutoreplyRule(pattern=re.compile(r"alpha"), reply="a;", exclusive=True),
        AutoreplyRule(pattern=re.compile(r"beta"), reply="b;"),
        AutoreplyRule(pattern=re.compile(r"gamma"), reply="g;", exclusive=True),
    ]
    engine = AutoreplyEngine(rules, writer, writer.log)
    assert engine.exclusive_active is False
    assert engine.exclusive_rule_index == 0

    engine.feed("alpha\n")
    await asyncio.sleep(0.05)
    assert engine.exclusive_active is True
    assert engine.exclusive_rule_index == 1

    engine.on_prompt()
    engine.on_prompt()
    assert engine.exclusive_active is False
    assert engine.exclusive_rule_index == 0

    engine.feed("gamma\n")
    engine.on_prompt()
    await asyncio.sleep(0.05)
    assert engine.exclusive_active is True
    assert engine.exclusive_rule_index == 3


@pytest.mark.asyncio
async def test_exclusive_cleared_by_on_prompt():
    """Two on_prompt() calls needed: first is skipped (same-chunk GA/EOR)."""
    writer, written = _mock_writer()
    rules = [
        AutoreplyRule(pattern=re.compile(r"A (\w+) pheasant"), reply=r"kill \1;", exclusive=True),
        AutoreplyRule(pattern=re.compile(r"A (\w+) rabbit"), reply=r"kill \1;", exclusive=True),
    ]
    engine = AutoreplyEngine(rules, writer, writer.log)
    engine.feed("A green pheasant\nA red rabbit\n")
    await asyncio.sleep(0.05)
    assert any("kill green\r\n" in w for w in written)
    assert not any("kill red\r\n" in w for w in written)

    engine.on_prompt()  # skipped (same-chunk)
    engine.on_prompt()  # actually clears exclusive (and clears buffer)
    engine.feed("A red rabbit\n")
    engine.on_prompt()  # triggers deferred match
    await asyncio.sleep(0.05)
    assert any("kill red\r\n" in w for w in written)


@pytest.mark.asyncio
async def test_exclusive_until_cleared_after_two_prompts():
    """Exclusive with until pattern clears after 2 on_prompt() calls."""
    writer, written = _mock_writer()
    rules = [
        AutoreplyRule(
            pattern=re.compile(r"A (\w+) mouse"),
            reply=r"kill \1;",
            exclusive=True,
            until=r"died\.|Kill what \?",
        ),
        AutoreplyRule(pattern=re.compile(r"treasure"), reply="take treasure;"),
    ]
    engine = AutoreplyEngine(rules, writer, writer.log)
    engine.feed("A brown mouse\n")
    await asyncio.sleep(0.05)
    assert engine.exclusive_active is True
    assert engine._until_pattern is not None

    engine.on_prompt()  # skipped (same-chunk GA/EOR)
    assert engine.exclusive_active is True

    engine.on_prompt()  # prompt_count=1
    assert engine.exclusive_active is True

    engine.on_prompt()  # prompt_count=2 -> bail out
    assert engine.exclusive_active is False
    assert engine._until_pattern is None

    engine.feed("treasure\n")
    engine.on_prompt()
    await asyncio.sleep(0.05)
    assert any("take treasure\r\n" in w for w in written)


@pytest.mark.asyncio
async def test_exclusive_until_match_clears_before_prompt_limit():
    """Until pattern match clears exclusive before the 2-prompt bail-out."""
    writer, _ = _mock_writer()
    rules = [
        AutoreplyRule(
            pattern=re.compile(r"A (\w+) mouse"),
            reply=r"kill \1;",
            exclusive=True,
            until=r"died\.|Kill what \?",
        )
    ]
    engine = AutoreplyEngine(rules, writer, writer.log)
    engine.feed("A brown mouse\n")
    await asyncio.sleep(0.05)
    assert engine.exclusive_active is True

    engine.feed("Kill what ?\n")
    assert engine.exclusive_active is False


@pytest.mark.asyncio
async def test_non_exclusive_allows_multiple():
    writer, written = _mock_writer()
    rules = [
        AutoreplyRule(pattern=re.compile(r"A (\w+) pheasant"), reply=r"kill \1;"),
        AutoreplyRule(pattern=re.compile(r"A (\w+) rabbit"), reply=r"kill \1;"),
    ]
    engine = AutoreplyEngine(rules, writer, writer.log)
    engine.feed("A green pheasant\nA red rabbit\n")
    await asyncio.sleep(0.05)
    assert any("kill green\r\n" in w for w in written)
    assert any("kill red\r\n" in w for w in written)


@pytest.mark.parametrize(
    "entries,checks",
    [
        (
            [
                {"pattern": "hello", "reply": "world;", "exclusive": True},
                {"pattern": "foo", "reply": "bar;"},
            ],
            [(0, "exclusive", True), (1, "exclusive", False)],
        ),
        (
            [
                {"pattern": "hello", "reply": "world;", "exclusive": True, "until": r"\1 done"},
                {"pattern": "foo", "reply": "bar;"},
            ],
            [(0, "until", r"\1 done"), (1, "until", "")],
        ),
        (
            [
                {"pattern": "died", "reply": "look;", "always": True},
                {"pattern": "hello", "reply": "world;"},
            ],
            [(0, "always", True), (1, "always", False)],
        ),
        (
            [
                {"pattern": "a", "reply": "b;", "exclusive": True, "exclusive_timeout": 10},
                {"pattern": "c", "reply": "d;"},
            ],
            [(0, "exclusive_timeout", 10.0), (1, "exclusive_timeout", 10.0)],
        ),
        (
            [{"pattern": "a", "reply": "b;", "enabled": False}, {"pattern": "c", "reply": "d;"}],
            [(0, "enabled", False), (1, "enabled", True)],
        ),
        (
            [{"pattern": "bear", "reply": "kill bear;", "when": {"HP%": ">50"}}],
            [(0, "when", {"HP%": ">50"})],
        ),
        ([{"pattern": "bear", "reply": "kill bear;"}], [(0, "when", {})]),
        (
            [
                {
                    "pattern": "monster",
                    "reply": "kill;",
                    "exclusive": True,
                    "until": "died",
                    "post_command": "look;",
                },
                {"pattern": "foo", "reply": "bar;"},
            ],
            [(0, "post_command", "look;"), (1, "post_command", "")],
        ),
    ],
)
def test_parse_entries_field(entries, checks):
    from telnetlib3.autoreply import _parse_entries

    rules = _parse_entries(entries)
    for idx, field, expected in checks:
        assert getattr(rules[idx], field) == expected


@pytest.mark.parametrize(
    "rule_kwargs,field,exp0,exp1,json_key,json_in_0,json_absent_1",
    [
        ({"exclusive": True}, "exclusive", True, False, "exclusive", True, True),
        (
            {"exclusive": True, "until": r"\1 died"},
            "until",
            r"\1 died",
            "",
            "until",
            r"\1 died",
            True,
        ),
        ({"always": True}, "always", True, False, "always", True, True),
        ({"enabled": False}, "enabled", False, True, "enabled", False, True),
        (
            {"exclusive": True, "until": r"\1 died", "post_command": "look;"},
            "post_command",
            "look;",
            "",
            "post_command",
            "look;",
            True,
        ),
    ],
)
def test_save_autoreplies_field_roundtrip(
    tmp_path, rule_kwargs, field, exp0, exp1, json_key, json_in_0, json_absent_1
):
    fp = tmp_path / "autoreplies.json"
    original = [
        AutoreplyRule(pattern=re.compile(r"(\w+) attacks"), reply=r"kill \1;", **rule_kwargs),
        AutoreplyRule(pattern=re.compile(r"foo"), reply="bar;"),
    ]
    save_autoreplies(str(fp), original, _SK)
    loaded = load_autoreplies(str(fp), _SK)
    assert getattr(loaded[0], field) == exp0
    assert getattr(loaded[1], field) == exp1

    with open(str(fp), "r") as fh:
        data = json.load(fh)
    entries = data[_SK]["autoreplies"]
    assert entries[0][json_key] == json_in_0
    if json_absent_1:
        assert json_key not in entries[1]


@pytest.mark.asyncio
async def test_exclusive_suppresses_feed_until_on_prompt():
    writer, written = _mock_writer()
    rules = [AutoreplyRule(pattern=re.compile(r"monster"), reply="kill;", exclusive=True)]
    engine = AutoreplyEngine(rules, writer, writer.log)
    engine.feed("monster\n")
    await asyncio.sleep(0.05)
    count = sum(1 for w in written if "kill\r\n" in w)
    assert count == 1

    engine.feed("monster again\n")
    await asyncio.sleep(0.05)
    count2 = sum(1 for w in written if "kill\r\n" in w)
    assert count2 == 1

    engine.on_prompt()  # skipped (same-chunk)
    engine.on_prompt()  # clears exclusive
    engine.feed("monster returns\n")
    engine.on_prompt()  # triggers deferred match
    await asyncio.sleep(0.05)
    count3 = sum(1 for w in written if "kill\r\n" in w)
    assert count3 == 2


@pytest.mark.asyncio
async def test_exclusive_until_clears_on_pattern():
    writer, written = _mock_writer()
    rules = [
        AutoreplyRule(
            pattern=re.compile(r"A (\w+) (\w+)"),
            reply=r"kill \2;",
            exclusive=True,
            until=r"\2 died\.",
        )
    ]
    engine = AutoreplyEngine(rules, writer, writer.log)
    engine.feed("A green pheasant\n")
    await asyncio.sleep(0.05)
    assert any("kill pheasant\r\n" in w for w in written)

    engine.feed("You scratch pheasant.\n")
    await asyncio.sleep(0.05)
    count = sum(1 for w in written if "kill" in w)
    assert count == 1

    engine.feed("pheasant died.\n")
    await asyncio.sleep(0.05)
    engine.feed("A red rabbit\n")
    await asyncio.sleep(0.05)
    assert any("kill rabbit\r\n" in w for w in written)


@pytest.mark.asyncio
async def test_exclusive_until_eor_does_not_clear():
    """When until is set, on_prompt() does NOT clear exclusive."""
    writer, written = _mock_writer()
    rules = [
        AutoreplyRule(
            pattern=re.compile(r"^fight (\w+)$", re.MULTILINE),
            reply=r"attack \1;",
            exclusive=True,
            until=r"target fell",
        )
    ]
    engine = AutoreplyEngine(rules, writer, writer.log)
    engine.feed("fight goblin\n")
    await asyncio.sleep(0.05)
    assert sum(1 for w in written if "attack goblin\r\n" in w) == 1

    engine.on_prompt()  # skipped (same-chunk)
    engine.on_prompt()  # ignored when until is set
    engine.feed("combat round\n")
    await asyncio.sleep(0.05)
    assert sum(1 for w in written if "attack goblin\r\n" in w) == 1

    engine.feed("target fell\n")
    await asyncio.sleep(0.05)
    engine.feed("fight goblin\n")
    engine.on_prompt()  # triggers deferred match
    await asyncio.sleep(0.05)
    assert sum(1 for w in written if "attack goblin\r\n" in w) == 2


@pytest.mark.asyncio
async def test_exclusive_until_group_substitution():
    writer, written = _mock_writer()
    rules = [
        AutoreplyRule(
            pattern=re.compile(r"fight (\w+)"),
            reply=r"attack \1;",
            exclusive=True,
            until=r"\1 fled\.",
        )
    ]
    engine = AutoreplyEngine(rules, writer, writer.log)
    engine.feed("fight dragon\n")
    await asyncio.sleep(0.05)
    assert any("attack dragon\r\n" in w for w in written)

    engine.feed("dragon breathes fire.\n")
    await asyncio.sleep(0.05)
    assert not any("attack" in w and w != "attack dragon\r\n" for w in written)

    engine.feed("dragon fled.\n")
    engine.feed("fight goblin\n")
    await asyncio.sleep(0.05)
    assert any("attack goblin\r\n" in w for w in written)


@pytest.mark.asyncio
async def test_skip_next_prompt_prevents_premature_clear():
    """First on_prompt() after exclusive activation is skipped."""
    writer, written = _mock_writer()
    rules = [
        AutoreplyRule(pattern=re.compile(r"A (\w+) pheasant"), reply=r"kill \1;", exclusive=True)
    ]
    engine = AutoreplyEngine(rules, writer, writer.log)
    engine.feed("A green pheasant\n")
    await asyncio.sleep(0.05)
    assert any("kill green\r\n" in w for w in written)

    engine.on_prompt()  # skipped (same-chunk GA/EOR)
    engine.feed("You attack pheasant.\n")
    await asyncio.sleep(0.05)
    assert sum(1 for w in written if "kill" in w) == 1

    engine.on_prompt()  # clears exclusive (second prompt)
    engine.feed("A blue pheasant\n")
    engine.on_prompt()  # triggers deferred match
    await asyncio.sleep(0.05)
    assert any("kill blue\r\n" in w for w in written)


@pytest.mark.asyncio
async def test_until_ignores_on_prompt():
    """When until is set, on_prompt() does not clear exclusive."""
    writer, written = _mock_writer()
    rules = [
        AutoreplyRule(
            pattern=re.compile(r"A (\w+) (\w+)"),
            reply=r"kill \2;",
            exclusive=True,
            until=r"\2 died\.",
        )
    ]
    engine = AutoreplyEngine(rules, writer, writer.log)
    engine.feed("A green pheasant\nA red rabbit\n")
    await asyncio.sleep(0.05)
    assert any("kill pheasant\r\n" in w for w in written)
    assert not any("kill rabbit\r\n" in w for w in written)

    engine.on_prompt()
    engine.on_prompt()
    engine.on_prompt()
    engine.feed("combat round\n")
    await asyncio.sleep(0.05)
    assert not any("kill rabbit\r\n" in w for w in written)

    engine.feed("pheasant died.\n")
    engine.feed("A red rabbit\n")
    engine.on_prompt()
    await asyncio.sleep(0.05)
    assert any("kill rabbit\r\n" in w for w in written)


@pytest.mark.asyncio
async def test_always_rule_fires_during_exclusive():
    writer, written = _mock_writer()
    rules = [
        AutoreplyRule(pattern=re.compile(r"A (\w+) pheasant"), reply=r"kill \1;", exclusive=True),
        AutoreplyRule(pattern=re.compile(r"died\."), reply="look;", always=True),
    ]
    engine = AutoreplyEngine(rules, writer, writer.log)
    engine.feed("A green pheasant\n")
    await asyncio.sleep(0.05)
    assert any("kill green\r\n" in w for w in written)

    engine.feed("pheasant died.\n")
    await asyncio.sleep(0.05)
    assert any("look\r\n" in w for w in written)


@pytest.mark.asyncio
async def test_always_rule_no_effect_when_not_exclusive():
    writer, written = _mock_writer()
    rules = [
        AutoreplyRule(pattern=re.compile(r"hello"), reply="world;"),
        AutoreplyRule(pattern=re.compile(r"foo"), reply="bar;", always=True),
    ]
    engine = AutoreplyEngine(rules, writer, writer.log)
    engine.feed("hello\nfoo\n")
    await asyncio.sleep(0.05)
    assert any("world\r\n" in w for w in written)
    assert any("bar\r\n" in w for w in written)


@pytest.mark.asyncio
async def test_exclusive_timeout_clears_suppression():
    writer, written = _mock_writer()
    rules = [
        AutoreplyRule(
            pattern=re.compile(r"monster"), reply="kill;", exclusive=True, exclusive_timeout=0.1
        )
    ]
    engine = AutoreplyEngine(rules, writer, writer.log)
    engine.feed("monster\n")
    await asyncio.sleep(0.05)
    assert sum(1 for w in written if "kill\r\n" in w) == 1

    engine.feed("monster again\n")
    await asyncio.sleep(0.05)
    assert sum(1 for w in written if "kill\r\n" in w) == 1

    await asyncio.sleep(0.1)
    engine.feed("monster returns\n")
    await asyncio.sleep(0.05)
    assert sum(1 for w in written if "kill\r\n" in w) == 2


@pytest.mark.asyncio
async def test_disabled_rule_skipped_by_engine():
    writer, written = _mock_writer()
    rules = [
        AutoreplyRule(pattern=re.compile(r"hello"), reply="world;", enabled=False),
        AutoreplyRule(pattern=re.compile(r"hello"), reply="backup;"),
    ]
    engine = AutoreplyEngine(rules, writer, writer.log)
    engine.feed("hello\n")
    await asyncio.sleep(0.05)
    assert not any("world\r\n" in w for w in written)
    assert any("backup\r\n" in w for w in written)


@pytest.mark.asyncio
async def test_disabled_always_rule_skipped():
    writer, written = _mock_writer()
    rules = [
        AutoreplyRule(pattern=re.compile(r"monster"), reply="kill;", exclusive=True),
        AutoreplyRule(pattern=re.compile(r"died\."), reply="look;", always=True, enabled=False),
    ]
    engine = AutoreplyEngine(rules, writer, writer.log)
    engine.feed("monster\n")
    await asyncio.sleep(0.05)
    engine.feed("pheasant died.\n")
    await asyncio.sleep(0.05)
    assert not any("look\r\n" in w for w in written)


@pytest.mark.asyncio
async def test_prompt_cycle_dedup_blocks_same_rule():
    """Once on_prompt activates cycle tracking, a rule fires at most once per cycle."""
    writer, written = _mock_writer()
    rules = [
        AutoreplyRule(
            pattern=re.compile(r"(^Corpse of|^\w+ times 'Corpse)", re.MULTILINE),
            reply="look in corpse;",
        )
    ]
    engine = AutoreplyEngine(rules, writer, writer.log)
    engine.on_prompt()

    engine.feed("Corpse of Goldfish\n")
    engine.feed("Two times 'Corpse of Barracuda'\n")
    engine.on_prompt()  # triggers deferred match; dedup blocks second hit
    await asyncio.sleep(0.05)
    assert sum(1 for w in written if "look in corpse\r\n" in w) == 1

    engine.feed("Corpse of Goldfish\n")
    engine.on_prompt()  # new cycle, rule can fire again
    await asyncio.sleep(0.05)
    assert sum(1 for w in written if "look in corpse\r\n" in w) == 2


@pytest.mark.asyncio
async def test_prompt_cycle_dedup_different_rules_both_fire():
    """Different rules can still fire in the same prompt cycle."""
    writer, written = _mock_writer()
    rules = [
        AutoreplyRule(pattern=re.compile(r"gold"), reply="get gold;"),
        AutoreplyRule(pattern=re.compile(r"corpse"), reply="look corpse;"),
    ]
    engine = AutoreplyEngine(rules, writer, writer.log)
    engine.on_prompt()

    engine.feed("gold and corpse\n")
    engine.on_prompt()  # triggers deferred match
    await asyncio.sleep(0.05)
    assert sum(1 for w in written if "get gold\r\n" in w) == 1
    assert sum(1 for w in written if "look corpse\r\n" in w) == 1


@pytest.mark.asyncio
async def test_prompt_cycle_dedup_inactive_without_on_prompt():
    """Without on_prompt, cycle dedup is not active -- same rule fires twice."""
    writer, written = _mock_writer()
    rules = [AutoreplyRule(pattern=re.compile(r"^trigger$", re.MULTILINE), reply="reply;")]
    engine = AutoreplyEngine(rules, writer, writer.log)

    engine.feed("trigger\n")
    await asyncio.sleep(0.05)
    engine.feed("trigger\n")
    await asyncio.sleep(0.05)
    assert sum(1 for w in written if "reply\r\n" in w) == 2


@pytest.mark.asyncio
async def test_prompt_cycle_dedup_always_rule():
    """Cycle dedup applies to always=True rules during exclusive mode."""
    writer, written = _mock_writer()
    rules = [
        AutoreplyRule(pattern=re.compile(r"monster"), reply="kill;", exclusive=True),
        AutoreplyRule(pattern=re.compile(r"corpse", re.MULTILINE), reply="loot;", always=True),
    ]
    engine = AutoreplyEngine(rules, writer, writer.log)
    engine.on_prompt()

    engine.feed("monster\n")
    engine.on_prompt()  # triggers deferred match, activates exclusive
    await asyncio.sleep(0.05)

    engine.feed("corpse here\n")
    await asyncio.sleep(0.05)
    assert sum(1 for w in written if "loot\r\n" in w) == 1

    engine.feed("another corpse\n")
    await asyncio.sleep(0.05)
    assert sum(1 for w in written if "loot\r\n" in w) == 1

    engine.on_prompt()
    engine.feed("corpse again\n")
    engine.on_prompt()  # triggers deferred match
    await asyncio.sleep(0.05)
    assert sum(1 for w in written if "loot\r\n" in w) == 2


def test_search_buffer_clear_resets_lines_and_position():
    buf = SearchBuffer(max_lines=100)
    buf.add_text("line1\nline2\n")
    buf._last_match_line = 1
    buf._last_match_col = 3
    buf.clear()
    assert buf.lines == []
    assert buf._last_match_line == 0
    assert buf._last_match_col == 0


def test_search_buffer_clear_preserves_partial():
    buf = SearchBuffer(max_lines=100)
    buf.add_text("line1\nprompt>> ")
    assert buf.partial == "prompt>> "
    buf.clear()
    assert buf.lines == []
    assert buf.partial == "prompt>> "
    assert buf.get_searchable_text() == "prompt>> "


@pytest.mark.asyncio
async def test_on_prompt_clears_buffer_prevents_stale_rematch():
    writer, written = _mock_writer()
    rules = [
        AutoreplyRule(pattern=re.compile(r"^Corpse of", re.MULTILINE), reply="look in corpse;")
    ]
    engine = AutoreplyEngine(rules, writer, writer.log)
    engine.on_prompt()

    engine.feed("Corpse of Goldfish\n")
    engine.on_prompt()  # triggers deferred match
    await asyncio.sleep(0.05)
    assert sum(1 for w in written if "look in corpse\r\n" in w) == 1

    engine.feed("No corpse here\n")
    engine.on_prompt()
    await asyncio.sleep(0.05)
    assert sum(1 for w in written if "look in corpse\r\n" in w) == 1


@pytest.mark.asyncio
async def test_on_prompt_clears_buffer_dotall_no_cross_record():
    writer, written = _mock_writer()
    rules = [
        AutoreplyRule(
            pattern=re.compile(r"Corpse contains:.*?(\d+ solaris)", re.DOTALL),
            reply=r"get all solaris;",
        )
    ]
    engine = AutoreplyEngine(rules, writer, writer.log)
    engine.on_prompt()

    engine.feed("Corpse contains:\n   A Mini-Shield\n")
    engine.on_prompt()  # triggers deferred match (no match)
    await asyncio.sleep(0.05)
    assert not any("get all solaris" in w for w in written)

    engine.feed("19 solaris on the ground\n")
    engine.on_prompt()  # triggers deferred match (no cross-record match)
    await asyncio.sleep(0.05)
    assert not any("get all solaris" in w for w in written)


@pytest.mark.asyncio
async def test_suppress_exclusive_skips_exclusive_rules():
    writer, written = _mock_writer()
    rules = [
        AutoreplyRule(pattern=re.compile(r"monster"), reply="kill;", exclusive=True),
        AutoreplyRule(pattern=re.compile(r"coin"), reply="get coin;"),
    ]
    engine = AutoreplyEngine(rules, writer, writer.log)
    engine.suppress_exclusive = True

    engine.feed("A monster appears\n")
    await asyncio.sleep(0.05)
    assert not any("kill" in w for w in written)
    assert engine.exclusive_active is False

    engine.on_prompt()
    engine.feed("A coin glitters\n")
    engine.on_prompt()  # triggers deferred match
    await asyncio.sleep(0.05)
    assert any("get coin\r\n" in w for w in written)


@pytest.mark.asyncio
async def test_suppress_exclusive_default_false():
    writer, _ = _mock_writer()
    rules = [AutoreplyRule(pattern=re.compile(r"x"), reply="y;")]
    engine = AutoreplyEngine(rules, writer, writer.log)
    assert engine.suppress_exclusive is False


@pytest.mark.asyncio
async def test_cancel_clears_exclusive_and_until():
    writer, written = _mock_writer()
    rules = [
        AutoreplyRule(
            pattern=re.compile(r"A (\w+) is here"),
            reply=r"kill \1;",
            exclusive=True,
            until=r"\1 died\.",
            post_command="look;",
        )
    ]
    engine = AutoreplyEngine(rules, writer, writer.log)
    engine.feed("A shark is here\n")
    await asyncio.sleep(0.05)
    assert engine.exclusive_active is True

    engine.cancel()
    assert engine.exclusive_active is False
    assert engine.reply_pending is False

    engine.feed("shark died.\n")
    await asyncio.sleep(0.05)
    assert not any("look" in w for w in written)


@pytest.mark.asyncio
async def test_sent_commands_not_matched_as_echo():
    writer, written = _mock_writer()
    rules = [AutoreplyRule(pattern=re.compile(r"corpse"), reply="look in corpse;")]
    engine = AutoreplyEngine(rules, writer, writer.log)
    engine._send_command("look in corpse")
    await asyncio.sleep(0.05)
    written.clear()

    engine.on_prompt()
    engine.feed("look in corpse\n")
    engine.on_prompt()  # triggers deferred match (echo suppressed)
    await asyncio.sleep(0.05)
    assert not any("look in corpse" in w for w in written)

    engine.feed("There is a corpse here.\n")
    engine.on_prompt()  # triggers deferred match
    await asyncio.sleep(0.05)
    assert any("look in corpse\r\n" in w for w in written)


@pytest.mark.asyncio
async def test_reply_pending_tracks_chain():
    writer, _ = _mock_writer()
    rules = [AutoreplyRule(pattern=re.compile(r"hello"), reply="world;")]
    engine = AutoreplyEngine(rules, writer, writer.log)
    assert engine.reply_pending is False

    engine.feed("hello\n")
    await asyncio.sleep(0.05)
    assert engine.reply_pending is False


@pytest.mark.asyncio
async def test_cycle_matched_tracks_matches():
    writer, _ = _mock_writer()
    rules = [AutoreplyRule(pattern=re.compile(r"hello"), reply="world;")]
    engine = AutoreplyEngine(rules, writer, writer.log)
    assert engine.cycle_matched is False

    engine.feed("hello\n")
    await asyncio.sleep(0.05)
    assert engine.cycle_matched is True

    engine.on_prompt()
    assert engine.cycle_matched is False


@pytest.mark.asyncio
async def test_post_command_queued_on_until_clear():
    writer, written = _mock_writer()
    rules = [
        AutoreplyRule(
            pattern=re.compile(r"A (\w+) is here"),
            reply=r"kill \1;",
            exclusive=True,
            until=r"\1 died\.",
            post_command="look;",
        )
    ]
    engine = AutoreplyEngine(rules, writer, writer.log)
    engine.feed("A shark is here\n")
    await asyncio.sleep(0.05)
    assert any("kill shark\r\n" in w for w in written)

    engine.feed("shark died.\n")
    await asyncio.sleep(0.05)
    assert any("look\r\n" in w for w in written)


@pytest.mark.asyncio
async def test_post_command_not_queued_on_timeout():
    writer, written = _mock_writer()
    rules = [
        AutoreplyRule(
            pattern=re.compile(r"A (\w+) is here"),
            reply=r"kill \1;",
            exclusive=True,
            until=r"\1 died\.",
            post_command="look;",
            exclusive_timeout=0.01,
        )
    ]
    engine = AutoreplyEngine(rules, writer, writer.log)
    engine.feed("A shark is here\n")
    await asyncio.sleep(0.05)
    assert any("kill shark\r\n" in w for w in written)

    await asyncio.sleep(0.02)
    engine.feed("combat continues\n")
    await asyncio.sleep(0.05)
    assert not any("look" in w for w in written)


@pytest.mark.asyncio
async def test_post_command_enables_multi_kill():
    writer, written = _mock_writer()
    rules = [
        AutoreplyRule(
            pattern=re.compile(r"A (\w+) is here"),
            reply=r"kill \1;",
            exclusive=True,
            until=r"\1 died\.",
            post_command="look;",
        )
    ]
    engine = AutoreplyEngine(rules, writer, writer.log)
    engine.feed("A shark is here\nAn octopus is here\n")
    await asyncio.sleep(0.05)
    assert sum(1 for w in written if "kill shark\r\n" in w) == 1
    assert not any("kill octopus" in w for w in written)

    engine.on_prompt()
    engine.on_prompt()
    engine.feed("shark died.\n")
    await asyncio.sleep(0.05)
    assert any("look\r\n" in w for w in written)

    engine.on_prompt()
    engine.on_prompt()
    engine.feed("A octopus is here\n")
    engine.on_prompt()  # triggers deferred match
    await asyncio.sleep(0.05)
    assert sum(1 for w in written if "kill octopus\r\n" in w) == 1


@pytest.mark.asyncio
async def test_post_command_cleared_by_on_prompt():
    writer, written = _mock_writer()
    rules = [
        AutoreplyRule(
            pattern=re.compile(r"fight (\w+)"),
            reply=r"attack \1;",
            exclusive=True,
            post_command="look;",
        )
    ]
    engine = AutoreplyEngine(rules, writer, writer.log)
    engine.feed("fight goblin\n")
    await asyncio.sleep(0.05)

    engine.on_prompt()
    engine.on_prompt()
    assert not any("look" in w for w in written)


@pytest.mark.asyncio
async def test_check_timeout_clears_exclusive():
    import time

    from telnetlib3.autoreply import AutoreplyRule, AutoreplyEngine

    writer, written = _mock_writer()

    rules = [
        AutoreplyRule(
            pattern=re.compile(r"fight"),
            reply="kill;",
            exclusive=True,
            until=r"died\.",
            exclusive_timeout=0.1,
        )
    ]
    engine = AutoreplyEngine(rules, writer, writer.log)
    engine.feed("fight goblin\n")
    await asyncio.sleep(0.05)
    assert engine.exclusive_active
    assert not engine.check_timeout()

    time.sleep(0.15)
    assert engine.check_timeout()
    assert not engine.exclusive_active


@pytest.mark.asyncio
async def test_non_exclusive_post_command():
    from telnetlib3.autoreply import AutoreplyRule, AutoreplyEngine

    writer, written = _mock_writer()

    rules = [
        AutoreplyRule(
            pattern=re.compile(r"solaris"), reply="get all solaris;", post_command="look;"
        )
    ]
    engine = AutoreplyEngine(rules, writer, writer.log)
    engine.feed("There are solaris on the ground.\n")
    await asyncio.sleep(0.15)
    assert any("get all solaris" in w for w in written)
    assert any("look" in w for w in written)


def _mock_writer_with_vitals(hp: int, maxhp: int, mp: int, maxmp: int):
    """Create a mock writer with GMCP vitals data."""
    written: list[str] = []
    writer = types.SimpleNamespace(
        write=lambda text: written.append(text),
        log=logging.getLogger("test"),
        _gmcp_data={
            "Char.Vitals": {"hp": str(hp), "maxhp": str(maxhp), "mp": str(mp), "maxmp": str(maxmp)}
        },
    )
    return writer, written


@pytest.mark.parametrize(
    "when, hp, maxhp, mp, maxmp, ok",
    [
        ({}, 50, 100, 50, 100, True),
        ({"HP%": ">50"}, 60, 100, 50, 100, True),
        ({"HP%": ">50"}, 50, 100, 50, 100, False),
        ({"HP%": ">50"}, 40, 100, 50, 100, False),
        ({"HP%": ">=50"}, 50, 100, 50, 100, True),
        ({"HP%": "<50"}, 40, 100, 50, 100, True),
        ({"HP%": "<50"}, 50, 100, 50, 100, False),
        ({"HP%": "<=50"}, 50, 100, 50, 100, True),
        ({"HP%": "=50"}, 50, 100, 50, 100, True),
        ({"HP%": "=50"}, 51, 100, 50, 100, False),
        ({"MP%": ">30"}, 80, 100, 40, 100, True),
        ({"MP%": ">30"}, 80, 100, 20, 100, False),
        ({"HP%": ">50", "MP%": ">30"}, 60, 100, 40, 100, True),
        ({"HP%": ">50", "MP%": ">30"}, 60, 100, 20, 100, False),
        ({"HP%": ">50", "MP%": ">30"}, 40, 100, 40, 100, False),
    ],
)
def test_check_condition(when, hp, maxhp, mp, maxmp, ok):
    writer, _ = _mock_writer_with_vitals(hp, maxhp, mp, maxmp)
    result, desc = check_condition(when, writer)
    assert result is ok
    if not ok:
        assert desc


def test_check_condition_no_gmcp():
    writer = types.SimpleNamespace(log=logging.getLogger("test"))
    ok, desc = check_condition({"HP%": ">50"}, writer)
    assert ok is True


def test_check_condition_no_vitals():
    writer = types.SimpleNamespace(log=logging.getLogger("test"), _gmcp_data={})
    ok, desc = check_condition({"HP%": ">50"}, writer)
    assert ok is True


def test_check_condition_zero_max():
    writer, _ = _mock_writer_with_vitals(50, 0, 50, 100)
    ok, desc = check_condition({"HP%": ">50"}, writer)
    assert ok is True


def test_check_condition_invalid_expr():
    writer, _ = _mock_writer_with_vitals(50, 100, 50, 100)
    ok, desc = check_condition({"HP%": "bad"}, writer)
    assert ok is True


def test_save_autoreplies_when_roundtrip(tmp_path):
    fp = tmp_path / "ar.json"
    rules = [
        AutoreplyRule(
            pattern=re.compile(r"bear"), reply="kill bear;", when={"HP%": ">50", "MP%": ">30"}
        )
    ]
    save_autoreplies(str(fp), rules, "test:23")
    loaded = load_autoreplies(str(fp), "test:23")
    assert loaded[0].when == {"HP%": ">50", "MP%": ">30"}


def test_save_autoreplies_when_empty_not_saved(tmp_path):
    fp = tmp_path / "ar.json"
    rules = [AutoreplyRule(pattern=re.compile(r"x"), reply="y;")]
    save_autoreplies(str(fp), rules, "test:23")
    raw = json.loads(fp.read_text())
    assert "when" not in raw["test:23"]["autoreplies"][0]


def test_save_autoreplies_immediate_roundtrip(tmp_path):
    fp = tmp_path / "ar.json"
    rules = [
        AutoreplyRule(pattern=re.compile(r"ship arrived"), reply="enter ship;", immediate=True)
    ]
    save_autoreplies(str(fp), rules, "test:23")
    loaded = load_autoreplies(str(fp), "test:23")
    assert loaded[0].immediate is True


def test_save_autoreplies_immediate_false_not_saved(tmp_path):
    fp = tmp_path / "ar.json"
    rules = [AutoreplyRule(pattern=re.compile(r"x"), reply="y;")]
    save_autoreplies(str(fp), rules, "test:23")
    raw = json.loads(fp.read_text())
    assert "immediate" not in raw["test:23"]["autoreplies"][0]


@pytest.mark.asyncio
async def test_engine_skips_rule_on_condition_fail():
    writer, written = _mock_writer_with_vitals(30, 100, 50, 100)
    rules = [AutoreplyRule(pattern=re.compile(r"bear"), reply="kill bear;", when={"HP%": ">50"})]
    engine = AutoreplyEngine(rules, writer, writer.log)
    engine.feed("A bear appears.\n")
    await asyncio.sleep(0.1)
    assert not any("kill bear" in w for w in written)
    failed = engine.condition_failed
    assert failed is not None
    assert failed[0] == 1
    assert "HP%" in failed[1]


@pytest.mark.asyncio
async def test_engine_fires_rule_when_condition_passes():
    writer, written = _mock_writer_with_vitals(80, 100, 50, 100)
    rules = [AutoreplyRule(pattern=re.compile(r"bear"), reply="kill bear;", when={"HP%": ">50"})]
    engine = AutoreplyEngine(rules, writer, writer.log)
    engine.feed("A bear appears.\n")
    await asyncio.sleep(0.1)
    assert any("kill bear" in w for w in written)
    assert engine.condition_failed is None


@pytest.mark.asyncio
async def test_condition_failed_clears_on_read():
    writer, _ = _mock_writer_with_vitals(30, 100, 50, 100)
    rules = [AutoreplyRule(pattern=re.compile(r"bear"), reply="kill bear;", when={"HP%": ">50"})]
    engine = AutoreplyEngine(rules, writer, writer.log)
    engine.feed("A bear appears.\n")
    await asyncio.sleep(0.1)
    assert engine.condition_failed is not None
    assert engine.condition_failed is None


@pytest.mark.asyncio
async def test_immediate_rule_fires_in_prompt_based_mode():
    writer, written = _mock_writer()
    rules = [
        AutoreplyRule(pattern=re.compile(r"normal trigger"), reply="normal;"),
        AutoreplyRule(pattern=re.compile(r"ship arrived"), reply="enter ship;", immediate=True),
    ]
    engine = AutoreplyEngine(rules, writer, writer.log)
    engine.on_prompt()
    assert engine._prompt_based is True

    engine.feed("ship arrived\n")
    await asyncio.sleep(0.1)
    assert any("enter ship" in w for w in written)


@pytest.mark.asyncio
async def test_non_immediate_rule_deferred_in_prompt_based_mode():
    writer, written = _mock_writer()
    rules = [AutoreplyRule(pattern=re.compile(r"normal trigger"), reply="normal;")]
    engine = AutoreplyEngine(rules, writer, writer.log)
    engine.on_prompt()
    assert engine._prompt_based is True

    engine.feed("normal trigger\n")
    await asyncio.sleep(0.1)
    assert not any("normal" in w for w in written)

    engine.on_prompt()
    await asyncio.sleep(0.1)
    assert any("normal" in w for w in written)


@pytest.mark.asyncio
async def test_immediate_and_normal_rules_together():
    writer, written = _mock_writer()
    rules = [
        AutoreplyRule(pattern=re.compile(r"normal"), reply="deferred;"),
        AutoreplyRule(pattern=re.compile(r"urgent"), reply="now;", immediate=True),
    ]
    engine = AutoreplyEngine(rules, writer, writer.log)
    engine.on_prompt()

    engine.feed("urgent event\nnormal event\n")
    await asyncio.sleep(0.1)
    assert any("now" in w for w in written)
    assert not any("deferred" in w for w in written)

    engine.on_prompt()
    await asyncio.sleep(0.1)
    assert any("deferred" in w for w in written)
