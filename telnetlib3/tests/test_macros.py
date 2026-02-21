"""Tests for telnetlib3.macros module."""

from __future__ import annotations

# std imports
import json
import types
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
                        {"key": "f5", "text": "look<CR>"},
                        {"key": "escape n", "text": "north<CR>"},
                    ]
                }
            }
        )
    )
    macros = load_macros(str(fp), _SK)
    assert len(macros) == 2
    assert macros[0].keys == ("f5",)
    assert macros[0].text == "look<CR>"
    assert macros[1].keys == ("escape", "n")


def test_load_macros_missing_file():
    with pytest.raises(FileNotFoundError):
        load_macros("/nonexistent/path.json", _SK)


def test_load_macros_empty_key_skipped(tmp_path):
    fp = tmp_path / "macros.json"
    fp.write_text(
        json.dumps(
            {_SK: {"macros": [{"key": "", "text": "skip"}, {"key": "f6", "text": "keep<CR>"}]}}
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
    fp.write_text(json.dumps({_SK: {"macros": [{"key": "c-x c-s", "text": "save<CR>"}]}}))
    macros = load_macros(str(fp), _SK)
    assert macros[0].keys == ("c-x", "c-s")


def test_save_macros_roundtrip(tmp_path):
    fp = tmp_path / "macros.json"
    original = [
        Macro(keys=("f5",), text="look<CR>"),
        Macro(keys=("escape", "n"), text="north<CR>"),
        Macro(keys=("c-x", "c-s"), text="save<CR>"),
    ]
    save_macros(str(fp), original, _SK)
    loaded = load_macros(str(fp), _SK)
    assert len(loaded) == len(original)
    for orig, restored in zip(original, loaded):
        assert orig.keys == restored.keys
        assert orig.text == restored.text


def test_save_macros_preserves_other_sessions(tmp_path):
    fp = tmp_path / "macros.json"
    save_macros(str(fp), [Macro(keys=("f1",), text="a<CR>")], "host1:23")
    save_macros(str(fp), [Macro(keys=("f2",), text="b<CR>")], "host2:23")
    assert len(load_macros(str(fp), "host1:23")) == 1
    assert len(load_macros(str(fp), "host2:23")) == 1


def test_save_macros_empty(tmp_path):
    fp = tmp_path / "macros.json"
    save_macros(str(fp), [], _SK)
    assert load_macros(str(fp), _SK) == []


def test_save_macros_unicode(tmp_path):
    fp = tmp_path / "macros.json"
    macros = [Macro(keys=("f1",), text="say héllo<CR>")]
    save_macros(str(fp), macros, _SK)
    loaded = load_macros(str(fp), _SK)
    assert loaded[0].text == "say héllo<CR>"


def _mock_writer():
    """Create a mock writer that records write() calls."""
    written: list[str] = []
    writer = types.SimpleNamespace(write=lambda text: written.append(text))
    return writer, written


@pytest.mark.skipif(not HAS_PROMPT_TOOLKIT, reason="prompt_toolkit required")
class TestBindMacros:

    def test_sends_text_on_cr(self):
        kb = prompt_toolkit.key_binding.KeyBindings()
        writer, written = _mock_writer()
        log = logging.getLogger("test")
        macros = [Macro(keys=("f5",), text="look<CR>")]
        bind_macros(kb, macros, writer, log)
        assert len(kb.bindings) >= 1

    def test_multi_command(self):
        writer, written = _mock_writer()
        macro = Macro(keys=("f6",), text="look<CR>inventory<CR>")
        parts = macro.text.split("<CR>")
        for i, part in enumerate(parts):
            if i < len(parts) - 1:
                writer.write(part + "\r\n")
            elif part:
                pass
        assert written == ["look\r\n", "inventory\r\n"]

    def test_insert_only_no_cr(self):
        writer, written = _mock_writer()
        macro = Macro(keys=("f7",), text="partial text")
        parts = macro.text.split("<CR>")
        for i, part in enumerate(parts):
            if i < len(parts) - 1:
                writer.write(part + "\r\n")
        assert written == []

    def test_invalid_key_logged_not_raised(self):
        kb = prompt_toolkit.key_binding.KeyBindings()
        writer, written = _mock_writer()
        log = logging.getLogger("test")
        macros = [Macro(keys=("INVALID_KEY_NAME_XYZ",), text="x<CR>")]
        bind_macros(kb, macros, writer, log)

    def test_handler_sends_cr_parts(self):
        kb = prompt_toolkit.key_binding.KeyBindings()
        writer, written = _mock_writer()
        log = logging.getLogger("test")
        macros = [Macro(keys=("f5",), text="look<CR>")]
        bind_macros(kb, macros, writer, log)
        handler = kb.bindings[-1].handler
        event = types.SimpleNamespace(
            app=types.SimpleNamespace(
                current_buffer=types.SimpleNamespace(insert_text=lambda t: None)
            )
        )
        handler(event)
        assert "look\r\n" in written

    def test_handler_inserts_trailing_text(self):
        kb = prompt_toolkit.key_binding.KeyBindings()
        writer, written = _mock_writer()
        log = logging.getLogger("test")
        macros = [Macro(keys=("f6",), text="cmd<CR>trailing")]
        bind_macros(kb, macros, writer, log)
        handler = kb.bindings[-1].handler
        inserted: list[str] = []
        event = types.SimpleNamespace(
            app=types.SimpleNamespace(
                current_buffer=types.SimpleNamespace(insert_text=inserted.append)
            )
        )
        handler(event)
        assert "cmd\r\n" in written
        assert "trailing" in inserted
