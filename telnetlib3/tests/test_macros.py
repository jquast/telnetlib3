"""Tests for telnetlib3.macros module."""

from __future__ import annotations

# std imports
import json
import types
import asyncio
import logging

# 3rd party
import pytest

# local
from telnetlib3.macros import Macro, bind_macros, load_macros, save_macros

try:
    import prompt_toolkit.key_binding

    HAS_PROMPT_TOOLKIT = True
except ImportError:
    HAS_PROMPT_TOOLKIT = False


_SK = "test.host:23"


def test_load_macros_valid(tmp_path):
    fp = tmp_path / "macros.json"
    fp.write_text(
        json.dumps(
            {
                _SK: {
                    "macros": [
                        {"key": "f5", "text": "look;"},
                        {"key": "escape n", "text": "north;"},
                    ]
                }
            }
        )
    )
    macros = load_macros(str(fp), _SK)
    assert len(macros) == 2
    assert macros[0].keys == ("f5",)
    assert macros[0].text == "look;"
    assert macros[1].keys == ("escape", "n")


def test_load_macros_missing_file():
    with pytest.raises(FileNotFoundError):
        load_macros("/nonexistent/path.json", _SK)


def test_load_macros_empty_key_skipped(tmp_path):
    fp = tmp_path / "macros.json"
    fp.write_text(
        json.dumps(
            {_SK: {"macros": [{"key": "", "text": "skip"}, {"key": "f6", "text": "keep;"}]}}
        )
    )
    macros = load_macros(str(fp), _SK)
    assert len(macros) == 1
    assert macros[0].keys == ("f6",)


def test_load_macros_empty_list(tmp_path):
    fp = tmp_path / "macros.json"
    fp.write_text(json.dumps({_SK: {"macros": []}}))
    assert load_macros(str(fp), _SK) == []


def test_load_macros_no_session(tmp_path):
    fp = tmp_path / "macros.json"
    fp.write_text(json.dumps({"other.host:23": {"macros": [{"key": "f5", "text": "x"}]}}))
    assert load_macros(str(fp), _SK) == []


def test_load_macros_multi_key(tmp_path):
    fp = tmp_path / "macros.json"
    fp.write_text(json.dumps({_SK: {"macros": [{"key": "c-x c-s", "text": "save;"}]}}))
    macros = load_macros(str(fp), _SK)
    assert macros[0].keys == ("c-x", "c-s")


def test_save_macros_roundtrip(tmp_path):
    fp = tmp_path / "macros.json"
    original = [
        Macro(keys=("f5",), text="look;"),
        Macro(keys=("escape", "n"), text="north;"),
        Macro(keys=("c-x", "c-s"), text="save;"),
    ]
    save_macros(str(fp), original, _SK)
    loaded = load_macros(str(fp), _SK)
    assert len(loaded) == len(original)
    for orig, restored in zip(original, loaded):
        assert orig.keys == restored.keys
        assert orig.text == restored.text


def test_save_macros_preserves_other_sessions(tmp_path):
    fp = tmp_path / "macros.json"
    save_macros(str(fp), [Macro(keys=("f1",), text="a;")], "host1:23")
    save_macros(str(fp), [Macro(keys=("f2",), text="b;")], "host2:23")
    assert len(load_macros(str(fp), "host1:23")) == 1
    assert len(load_macros(str(fp), "host2:23")) == 1


def test_save_macros_empty(tmp_path):
    fp = tmp_path / "macros.json"
    save_macros(str(fp), [], _SK)
    assert load_macros(str(fp), _SK) == []


def test_save_macros_unicode(tmp_path):
    fp = tmp_path / "macros.json"
    macros = [Macro(keys=("f1",), text="say héllo;")]
    save_macros(str(fp), macros, _SK)
    loaded = load_macros(str(fp), _SK)
    assert loaded[0].text == "say héllo;"


def _mock_writer():
    """Create a mock writer that records write() calls."""
    written: list[str] = []
    prompt_ready = asyncio.Event()
    prompt_ready.set()

    async def _wait() -> None:
        await asyncio.sleep(0)

    writer = types.SimpleNamespace(
        write=lambda text: written.append(text),
        log=logging.getLogger("test"),
        _wait_for_prompt=_wait,
        _echo_command=lambda cmd: None,
        _prompt_ready=prompt_ready,
        _current_room_num="",
        _rooms_file="",
        _session_key="",
    )
    return writer, written


@pytest.mark.skipif(not HAS_PROMPT_TOOLKIT, reason="prompt_toolkit required")
class TestBindMacros:

    def test_sends_text_on_cr(self):
        kb = prompt_toolkit.key_binding.KeyBindings()
        writer, written = _mock_writer()
        log = logging.getLogger("test")
        macros = [Macro(keys=("f5",), text="look;")]
        bind_macros(kb, macros, writer, log)
        assert len(kb.bindings) >= 1

    def test_multi_command(self):
        from telnetlib3.client_repl import expand_commands
        writer, written = _mock_writer()
        macro = Macro(keys=("f6",), text="look;inventory;")
        cmds = expand_commands(macro.text)
        for cmd in cmds:
            writer.write(cmd + "\r\n")
        assert written == ["look\r\n", "inventory\r\n"]

    @pytest.mark.asyncio
    async def test_no_semicolon_still_sends(self):
        kb = prompt_toolkit.key_binding.KeyBindings()
        writer, written = _mock_writer()
        log = logging.getLogger("test")
        macros = [Macro(keys=("f7",), text="partial text")]
        bind_macros(kb, macros, writer, log)
        handler = kb.bindings[-1].handler
        event = types.SimpleNamespace(
            app=types.SimpleNamespace(
                current_buffer=types.SimpleNamespace(insert_text=lambda t: None)
            )
        )
        handler(event)
        await asyncio.sleep(0.05)
        assert "partial text\r\n" in written

    def test_invalid_key_logged_not_raised(self):
        kb = prompt_toolkit.key_binding.KeyBindings()
        writer, written = _mock_writer()
        log = logging.getLogger("test")
        macros = [Macro(keys=("INVALID_KEY_NAME_XYZ",), text="x;")]
        bind_macros(kb, macros, writer, log)

    @pytest.mark.asyncio
    async def test_handler_sends_cr_parts(self):
        kb = prompt_toolkit.key_binding.KeyBindings()
        writer, written = _mock_writer()
        log = logging.getLogger("test")
        macros = [Macro(keys=("f5",), text="look;")]
        bind_macros(kb, macros, writer, log)
        handler = kb.bindings[-1].handler
        event = types.SimpleNamespace(
            app=types.SimpleNamespace(
                current_buffer=types.SimpleNamespace(insert_text=lambda t: None)
            )
        )
        handler(event)
        await asyncio.sleep(0.05)
        assert "look\r\n" in written

    @pytest.mark.asyncio
    async def test_handler_sends_all_commands(self):
        kb = prompt_toolkit.key_binding.KeyBindings()
        writer, written = _mock_writer()
        log = logging.getLogger("test")
        macros = [Macro(keys=("f6",), text="cmd;trailing")]
        bind_macros(kb, macros, writer, log)
        handler = kb.bindings[-1].handler
        event = types.SimpleNamespace(
            app=types.SimpleNamespace(
                current_buffer=types.SimpleNamespace(insert_text=lambda t: None)
            )
        )
        handler(event)
        await asyncio.sleep(0.05)
        assert "cmd\r\n" in written
        assert "trailing\r\n" in written
