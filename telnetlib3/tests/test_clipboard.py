"""Tests for telnetlib3._clipboard and REPL clipboard handlers."""

import io
import subprocess
from unittest import mock

import pytest

from telnetlib3._clipboard import (
    _PASTE_COMMANDS,
    copy_to_clipboard,
    paste_from_clipboard,
)


class TestCopyToClipboard:
    """Tests for copy_to_clipboard (OSC 52)."""

    def test_osc52_sequence(self):
        buf = io.BytesIO()
        copy_to_clipboard("hello", file=buf)
        # "hello" → base64 "aGVsbG8="
        assert buf.getvalue() == b"\x1b]52;c;aGVsbG8=\a"

    def test_empty_string(self):
        buf = io.BytesIO()
        copy_to_clipboard("", file=buf)
        assert buf.getvalue() == b"\x1b]52;c;\a"

    def test_unicode(self):
        buf = io.BytesIO()
        copy_to_clipboard("\u2603", file=buf)
        data = buf.getvalue()
        assert data.startswith(b"\x1b]52;c;")
        assert data.endswith(b"\a")

    def test_default_stdout(self):
        with mock.patch("sys.stdout") as mock_stdout:
            mock_stdout.buffer = io.BytesIO()
            copy_to_clipboard("x")
            assert b"\x1b]52;c;" in mock_stdout.buffer.getvalue()


class TestPasteFromClipboard:
    """Tests for paste_from_clipboard (subprocess)."""

    def test_xclip(self):
        fake = subprocess.CompletedProcess(
            args=["xclip"], returncode=0, stdout=b"pasted"
        )
        with mock.patch("subprocess.run", return_value=fake) as m:
            result = paste_from_clipboard()
        assert result == "pasted"
        m.assert_called_once_with(
            ("xclip", "-selection", "clipboard", "-o"),
            capture_output=True,
            timeout=2,
            check=False,
        )

    def test_fallback_to_xsel(self):
        def side_effect(cmd, **_kwargs):
            if cmd[0] == "xclip":
                raise FileNotFoundError
            return subprocess.CompletedProcess(
                args=list(cmd), returncode=0, stdout=b"from-xsel"
            )

        with mock.patch("subprocess.run", side_effect=side_effect):
            assert paste_from_clipboard() == "from-xsel"

    def test_pbpaste(self):
        def side_effect(cmd, **_kwargs):
            if cmd[0] in ("xclip", "xsel", "wl-paste"):
                raise FileNotFoundError
            return subprocess.CompletedProcess(
                args=list(cmd), returncode=0, stdout=b"mac-paste"
            )

        with mock.patch("subprocess.run", side_effect=side_effect):
            assert paste_from_clipboard() == "mac-paste"

    def test_no_tool_available(self):
        with mock.patch("subprocess.run", side_effect=FileNotFoundError):
            assert paste_from_clipboard() == ""

    def test_nonzero_exit_tries_next(self):
        call_count = 0

        def side_effect(cmd, **_kwargs):
            nonlocal call_count
            call_count += 1
            if cmd[0] == "xclip":
                return subprocess.CompletedProcess(
                    args=list(cmd), returncode=1, stdout=b""
                )
            return subprocess.CompletedProcess(
                args=list(cmd), returncode=0, stdout=b"ok"
            )

        with mock.patch("subprocess.run", side_effect=side_effect):
            assert paste_from_clipboard() == "ok"
        assert call_count == 2

    def test_paste_commands_tuple(self):
        assert len(_PASTE_COMMANDS) == 4
        assert _PASTE_COMMANDS[0][0] == "xclip"
        assert _PASTE_COMMANDS[-1][0] == "pbpaste"


class TestReplClipboardHandlers:
    """Tests for _clipboard_copy / _clipboard_paste keymap handlers."""

    def test_copy_handler_copies_line(self):
        from telnetlib3.client_repl import _clipboard_copy

        editor = mock.MagicMock()
        editor.line = "hello world"
        with mock.patch("telnetlib3._clipboard.copy_to_clipboard") as m:
            result = _clipboard_copy(editor)
        m.assert_called_once_with("hello world")
        assert not result.changed

    def test_copy_handler_empty_line(self):
        from telnetlib3.client_repl import _clipboard_copy

        editor = mock.MagicMock()
        editor.line = ""
        with mock.patch("telnetlib3._clipboard.copy_to_clipboard") as m:
            result = _clipboard_copy(editor)
        m.assert_not_called()
        assert not result.changed

    def test_paste_handler_inserts_text(self):
        from blessed.line_editor import LineEditResult

        from telnetlib3.client_repl import _clipboard_paste

        editor = mock.MagicMock()
        editor.insert_text.return_value = LineEditResult(changed=True)
        with mock.patch(
            "telnetlib3._clipboard.paste_from_clipboard", return_value="pasted"
        ):
            result = _clipboard_paste(editor)
        editor.insert_text.assert_called_once_with("pasted")
        assert result.changed

    def test_paste_handler_empty_clipboard(self):
        from telnetlib3.client_repl import _clipboard_paste

        editor = mock.MagicMock()
        with mock.patch(
            "telnetlib3._clipboard.paste_from_clipboard", return_value=""
        ):
            result = _clipboard_paste(editor)
        editor.insert_text.assert_not_called()
        assert not result.changed
