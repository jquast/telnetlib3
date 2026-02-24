"""Tests for telnetlib3.macros module."""

from __future__ import annotations

# std imports
import json
import logging

# 3rd party
import pytest

# local
from telnetlib3.macros import Macro, load_macros, save_macros

_SK = "test.host:23"


def test_load_macros_valid(tmp_path):
    fp = tmp_path / "macros.json"
    fp.write_text(
        json.dumps(
            {
                _SK: {
                    "macros": [
                        {"key": "KEY_F5", "text": "look;"},
                        {"key": "KEY_ALT_N", "text": "north;"},
                    ]
                }
            }
        )
    )
    macros = load_macros(str(fp), _SK)
    assert len(macros) == 2
    assert macros[0].key == "KEY_F5"
    assert macros[0].text == "look;"
    assert macros[1].key == "KEY_ALT_N"


def test_load_macros_missing_file():
    with pytest.raises(FileNotFoundError):
        load_macros("/nonexistent/path.json", _SK)


def test_load_macros_empty_key_skipped(tmp_path):
    fp = tmp_path / "macros.json"
    fp.write_text(
        json.dumps(
            {_SK: {"macros": [{"key": "", "text": "skip"}, {"key": "KEY_F6", "text": "keep;"}]}}
        )
    )
    macros = load_macros(str(fp), _SK)
    assert len(macros) == 1
    assert macros[0].key == "KEY_F6"


def test_load_macros_empty_list(tmp_path):
    fp = tmp_path / "macros.json"
    fp.write_text(json.dumps({_SK: {"macros": []}}))
    assert not load_macros(str(fp), _SK)


def test_load_macros_no_session(tmp_path):
    fp = tmp_path / "macros.json"
    fp.write_text(json.dumps({"other.host:23": {"macros": [{"key": "KEY_F5", "text": "x"}]}}))
    assert not load_macros(str(fp), _SK)


def test_save_macros_roundtrip(tmp_path):
    fp = tmp_path / "macros.json"
    original = [Macro(key="KEY_F5", text="look;"), Macro(key="KEY_ALT_N", text="north;")]
    save_macros(str(fp), original, _SK)
    loaded = load_macros(str(fp), _SK)
    assert len(loaded) == len(original)
    for orig, restored in zip(original, loaded):
        assert orig.key == restored.key
        assert orig.text == restored.text


def test_save_macros_preserves_other_sessions(tmp_path):
    fp = tmp_path / "macros.json"
    save_macros(str(fp), [Macro(key="KEY_F1", text="a;")], "host1:23")
    save_macros(str(fp), [Macro(key="KEY_F2", text="b;")], "host2:23")
    assert len(load_macros(str(fp), "host1:23")) == 1
    assert len(load_macros(str(fp), "host2:23")) == 1


def test_save_macros_empty(tmp_path):
    fp = tmp_path / "macros.json"
    save_macros(str(fp), [], _SK)
    assert not load_macros(str(fp), _SK)


def test_save_macros_unicode(tmp_path):
    fp = tmp_path / "macros.json"
    macros = [Macro(key="KEY_F1", text="say héllo;")]
    save_macros(str(fp), macros, _SK)
    loaded = load_macros(str(fp), _SK)
    assert loaded[0].text == "say héllo;"


def test_build_dispatch_skips_editor_keymap_conflicts(caplog):
    import types

    from telnetlib3.macros import build_macro_dispatch

    writer = types.SimpleNamespace(log=logging.getLogger("test"))
    macros = [
        Macro(key="KEY_LEFT", text="should be skipped"),
        Macro(key="KEY_ALT_E", text="should be kept"),
    ]
    with caplog.at_level(logging.WARNING):
        result = build_macro_dispatch(macros, writer, writer.log)
    assert "KEY_LEFT" not in result
    assert "KEY_ALT_E" in result
    assert "conflicts with editor keymap" in caplog.text


def test_expand_commands():
    from telnetlib3.client_repl import expand_commands

    cmds = expand_commands("look;inventory;")
    assert cmds == ["look", "inventory"]


def test_expand_commands_no_semicolon():
    from telnetlib3.client_repl import expand_commands

    cmds = expand_commands("partial text")
    assert cmds == ["partial text"]
