"""Tests for telnetlib3.client_repl and client_shell.ScrollRegion."""

import sys
import types
import asyncio

import pytest

if sys.platform == "win32":
    pytest.skip("POSIX-only tests", allow_module_level=True)

from telnetlib3.client_repl import ScrollRegion  # noqa: E402
from telnetlib3.client_repl import (  # noqa: E402
    HAS_PROMPT_TOOLKIT,
    BasicLineRepl,
)


class _MockTransport:
    def __init__(self) -> None:
        self.data = bytearray()
        self._closing = False

    def write(self, data: bytes) -> None:
        self.data.extend(data)

    def is_closing(self) -> bool:
        return self._closing


def _mock_stdout() -> "asyncio.StreamWriter":
    transport = _MockTransport()
    writer = types.SimpleNamespace(write=transport.write)
    return writer, transport  # type: ignore[return-value]


def _mock_writer(will_echo: bool = False) -> object:
    return types.SimpleNamespace(
        will_echo=will_echo,
        log=types.SimpleNamespace(debug=lambda *a, **kw: None),
    )


class TestScrollRegion:

    def test_scroll_rows_property(self) -> None:
        stdout, _ = _mock_stdout()
        sr = ScrollRegion(stdout, rows=24, cols=80, reserve_bottom=1)
        assert sr.scroll_rows == 23

    def test_scroll_rows_minimum(self) -> None:
        stdout, _ = _mock_stdout()
        sr = ScrollRegion(stdout, rows=1, cols=80, reserve_bottom=1)
        assert sr.scroll_rows == 1

    def test_input_row(self) -> None:
        stdout, _ = _mock_stdout()
        sr = ScrollRegion(stdout, rows=24, cols=80)
        assert sr.input_row == 24

    def test_decstbm_enter_exit(self) -> None:
        stdout, transport = _mock_stdout()
        with ScrollRegion(stdout, rows=24, cols=80, reserve_bottom=1) as sr:
            assert sr._active
            data_on_enter = bytes(transport.data)
            assert b"\x1b[1;23r" in data_on_enter
        data_on_exit = bytes(transport.data)
        assert b"\x1b[1;24r" in data_on_exit
        assert b"\x1b[24;1H" in data_on_exit

    def test_update_size(self) -> None:
        stdout, transport = _mock_stdout()
        with ScrollRegion(stdout, rows=24, cols=80, reserve_bottom=1) as sr:
            transport.data.clear()
            sr.update_size(30, 120)
            assert sr.scroll_rows == 29
            data = bytes(transport.data)
            assert b"\x1b[1;29r" in data

    def test_update_size_inactive(self) -> None:
        stdout, transport = _mock_stdout()
        sr = ScrollRegion(stdout, rows=24, cols=80, reserve_bottom=1)
        transport.data.clear()
        sr.update_size(30, 120)
        assert bytes(transport.data) == b""

    def test_save_and_goto_input(self) -> None:
        stdout, transport = _mock_stdout()
        sr = ScrollRegion(stdout, rows=24, cols=80)
        transport.data.clear()
        sr.save_and_goto_input()
        data = bytes(transport.data)
        assert b"\x1b7" in data
        assert b"\x1b[24;1H" in data
        assert b"\x1b[2K" in data

    def test_restore_cursor(self) -> None:
        stdout, transport = _mock_stdout()
        sr = ScrollRegion(stdout, rows=24, cols=80)
        transport.data.clear()
        sr.restore_cursor()
        assert bytes(transport.data) == b"\x1b8"


class TestBasicLineRepl:

    @pytest.mark.asyncio
    async def test_reads_line(self) -> None:
        reader = asyncio.StreamReader()
        reader.feed_data(b"hello world\n")
        writer = _mock_writer()
        repl = BasicLineRepl(writer, reader, writer.log)
        result = await repl.prompt()
        assert result == "hello world"

    @pytest.mark.asyncio
    async def test_strips_trailing_newline(self) -> None:
        reader = asyncio.StreamReader()
        reader.feed_data(b"test\n")
        writer = _mock_writer()
        repl = BasicLineRepl(writer, reader, writer.log)
        result = await repl.prompt()
        assert result == "test"

    @pytest.mark.asyncio
    async def test_eof_returns_none(self) -> None:
        reader = asyncio.StreamReader()
        reader.feed_eof()
        writer = _mock_writer()
        repl = BasicLineRepl(writer, reader, writer.log)
        result = await repl.prompt()
        assert result is None


class TestHasPromptToolkit:

    def test_is_boolean(self) -> None:
        assert isinstance(HAS_PROMPT_TOOLKIT, bool)


@pytest.mark.skipif(not HAS_PROMPT_TOOLKIT, reason="prompt_toolkit not installed")
class TestPromptToolkitRepl:

    def test_password_mode_detection(self) -> None:
        from telnetlib3.client_repl import PromptToolkitRepl

        writer = _mock_writer(will_echo=True)
        repl = PromptToolkitRepl(writer, writer.log)
        assert repl._is_password_mode() is True

    def test_no_password_mode(self) -> None:
        from telnetlib3.client_repl import PromptToolkitRepl

        writer = _mock_writer(will_echo=False)
        repl = PromptToolkitRepl(writer, writer.log)
        assert repl._is_password_mode() is False

    def test_uses_in_memory_history_by_default(self) -> None:
        from prompt_toolkit.history import InMemoryHistory
        from telnetlib3.client_repl import PromptToolkitRepl

        writer = _mock_writer()
        repl = PromptToolkitRepl(writer, writer.log)
        assert isinstance(repl._history, InMemoryHistory)

    def test_uses_file_history_when_path_given(self, tmp_path) -> None:
        from telnetlib3.client_repl import PromptToolkitRepl, _FilteredFileHistory

        history_path = str(tmp_path / "history")
        writer = _mock_writer()
        repl = PromptToolkitRepl(writer, writer.log, history_file=history_path)
        assert isinstance(repl._history, _FilteredFileHistory)


@pytest.mark.skipif(not HAS_PROMPT_TOOLKIT, reason="prompt_toolkit not installed")
class TestFilteredFileHistory:

    def test_stores_normal_input(self, tmp_path) -> None:
        from telnetlib3.client_repl import _FilteredFileHistory

        history_path = str(tmp_path / "history")
        hist = _FilteredFileHistory(history_path)
        hist.store_string("hello")
        content = (tmp_path / "history").read_text()
        assert "+hello" in content

    def test_skips_password_input(self, tmp_path) -> None:
        from telnetlib3.client_repl import _FilteredFileHistory

        history_path = str(tmp_path / "history")
        hist = _FilteredFileHistory(history_path, is_password=lambda: True)
        hist.store_string("secret123")
        assert not (tmp_path / "history").exists()

    def test_stores_when_not_password(self, tmp_path) -> None:
        from telnetlib3.client_repl import _FilteredFileHistory

        password_mode = False
        history_path = str(tmp_path / "history")
        hist = _FilteredFileHistory(
            history_path, is_password=lambda: password_mode
        )
        hist.store_string("visible")
        content = (tmp_path / "history").read_text()
        assert "+visible" in content

    def test_dynamic_password_toggle(self, tmp_path) -> None:
        from telnetlib3.client_repl import _FilteredFileHistory

        password_mode = False
        history_path = str(tmp_path / "history")
        hist = _FilteredFileHistory(
            history_path, is_password=lambda: password_mode
        )
        hist.store_string("visible")
        password_mode = True
        hist.store_string("secret")
        password_mode = False
        hist.store_string("also_visible")
        content = (tmp_path / "history").read_text()
        assert "secret" not in content
        assert "+visible" in content
        assert "+also_visible" in content

    def test_creates_parent_directories(self, tmp_path) -> None:
        from telnetlib3.client_repl import _make_history, _FilteredFileHistory

        history_path = str(tmp_path / "sub" / "dir" / "history")
        hist = _make_history(history_path)
        assert isinstance(hist, _FilteredFileHistory)
        assert (tmp_path / "sub" / "dir").is_dir()
