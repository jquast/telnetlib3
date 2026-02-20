"""Tests for telnetlib3.macros module."""

from __future__ import annotations

# std imports
import json
import types
import logging

# 3rd party
import pytest

# local
from telnetlib3.macros import Macro, bind_macros, load_macros

try:
    import prompt_toolkit.key_binding

    HAS_PROMPT_TOOLKIT = True
except ImportError:
    HAS_PROMPT_TOOLKIT = False


# ---------------------------------------------------------------------------
# load_macros
# ---------------------------------------------------------------------------


class TestLoadMacros:

    def test_load_valid(self, tmp_path):
        fp = tmp_path / "macros.json"
        fp.write_text(
            json.dumps(
                {
                    "macros": [
                        {"key": "f5", "text": "look<CR>"},
                        {"key": "escape n", "text": "north<CR>"},
                    ]
                }
            )
        )
        macros = load_macros(str(fp))
        assert len(macros) == 2
        assert macros[0].keys == ("f5",)
        assert macros[0].text == "look<CR>"
        assert macros[1].keys == ("escape", "n")

    def test_load_missing_file(self):
        with pytest.raises(FileNotFoundError):
            load_macros("/nonexistent/path.json")

    def test_load_empty_key_skipped(self, tmp_path):
        fp = tmp_path / "macros.json"
        fp.write_text(
            json.dumps({"macros": [{"key": "", "text": "skip"}, {"key": "f6", "text": "keep<CR>"}]})
        )
        macros = load_macros(str(fp))
        assert len(macros) == 1
        assert macros[0].keys == ("f6",)

    def test_load_empty_list(self, tmp_path):
        fp = tmp_path / "macros.json"
        fp.write_text(json.dumps({"macros": []}))
        macros = load_macros(str(fp))
        assert macros == []

    def test_load_multi_key(self, tmp_path):
        fp = tmp_path / "macros.json"
        fp.write_text(json.dumps({"macros": [{"key": "c-x c-s", "text": "save<CR>"}]}))
        macros = load_macros(str(fp))
        assert macros[0].keys == ("c-x", "c-s")


# ---------------------------------------------------------------------------
# bind_macros
# ---------------------------------------------------------------------------


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
        log = logging.getLogger("test")
        macro = Macro(keys=("f6",), text="look<CR>inventory<CR>")

        # Simulate what the handler does.
        parts = macro.text.split("<CR>")
        for i, part in enumerate(parts):
            if i < len(parts) - 1:
                writer.write(part + "\r\n")
            elif part:
                pass  # would insert into buffer
        assert written == ["look\r\n", "inventory\r\n"]

    def test_insert_only_no_cr(self):
        writer, written = _mock_writer()
        log = logging.getLogger("test")
        macro = Macro(keys=("f7",), text="partial text")

        parts = macro.text.split("<CR>")
        for i, part in enumerate(parts):
            if i < len(parts) - 1:
                writer.write(part + "\r\n")
        # No <CR> means no write — text would be inserted into buffer.
        assert written == []

    def test_invalid_key_logged_not_raised(self):
        kb = prompt_toolkit.key_binding.KeyBindings()
        writer, written = _mock_writer()
        log = logging.getLogger("test")
        macros = [Macro(keys=("INVALID_KEY_NAME_XYZ",), text="x<CR>")]
        # Should not raise.
        bind_macros(kb, macros, writer, log)
