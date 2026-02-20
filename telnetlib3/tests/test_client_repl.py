"""Tests for telnetlib3.client_repl and client_shell.ScrollRegion."""

# std imports
import sys
import types
import asyncio

# 3rd party
import pytest

if sys.platform == "win32":
    pytest.skip("POSIX-only tests", allow_module_level=True)

# local
from telnetlib3.client_repl import ScrollRegion  # noqa: E402
from telnetlib3.client_repl import HAS_PROMPT_TOOLKIT, BasicLineRepl  # noqa: E402


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
        will_echo=will_echo, log=types.SimpleNamespace(debug=lambda *a, **kw: None)
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

    def test_ctrl_bracket_binding_registered(self) -> None:
        """Ctrl+] key binding is registered on the session."""
        from prompt_toolkit.keys import Keys

        from telnetlib3.client_repl import PromptToolkitRepl

        writer = _mock_writer()
        repl = PromptToolkitRepl(writer, writer.log)
        bindings = repl._session.key_bindings.bindings
        bound_keys = [b.keys for b in bindings]
        assert (Keys.ControlSquareClose,) in bound_keys


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
        hist = _FilteredFileHistory(history_path, is_password=lambda: password_mode)
        hist.store_string("visible")
        content = (tmp_path / "history").read_text()
        assert "+visible" in content

    def test_dynamic_password_toggle(self, tmp_path) -> None:
        from telnetlib3.client_repl import _FilteredFileHistory

        password_mode = False
        history_path = str(tmp_path / "history")
        hist = _FilteredFileHistory(history_path, is_password=lambda: password_mode)
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


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only")
class TestAdjustedSendNaws:

    @pytest.mark.asyncio
    async def test_adjusted_naws_active_scroll(self) -> None:
        from telnetlib3.client_repl import _repl_scaffold

        writer = _mock_writer()
        writer.handle_send_naws = lambda: (24, 80)
        writer.local_option = types.SimpleNamespace(enabled=lambda _: False)
        writer.is_closing = lambda: False

        stdout, _ = _mock_stdout()
        term = types.SimpleNamespace(on_resize=None)

        async with _repl_scaffold(writer, term, stdout) as (scroll, _):
            result = writer.handle_send_naws()
            assert isinstance(result, tuple)
            assert len(result) == 2
            assert result[0] == scroll.scroll_rows

    @pytest.mark.asyncio
    async def test_adjusted_naws_inactive_returns_terminal_size(self) -> None:
        from telnetlib3.client_repl import _repl_scaffold

        writer = _mock_writer()
        writer.handle_send_naws = lambda: (24, 80)
        writer.local_option = types.SimpleNamespace(enabled=lambda _: False)
        writer.is_closing = lambda: False

        stdout, _ = _mock_stdout()
        term = types.SimpleNamespace(on_resize=None)

        patched_naws = None
        async with _repl_scaffold(writer, term, stdout) as (scroll, _):
            patched_naws = writer.handle_send_naws
        result = patched_naws()
        assert isinstance(result, tuple)
        assert len(result) == 2


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only")
class TestNawsRestoration:

    @pytest.mark.asyncio
    async def test_naws_restored_on_exception(self) -> None:
        """handle_send_naws is restored even if _repl_scaffold body raises."""
        from telnetlib3.client_repl import _repl_scaffold

        def orig_handler() -> tuple[int, int]:
            return (24, 80)

        writer = _mock_writer()
        writer.handle_send_naws = orig_handler
        writer.local_option = types.SimpleNamespace(enabled=lambda _: False)
        writer.is_closing = lambda: False

        stdout, _ = _mock_stdout()
        term = types.SimpleNamespace(on_resize=None)

        with pytest.raises(RuntimeError, match="injected"):
            async with _repl_scaffold(writer, term, stdout):
                raise RuntimeError("injected")

        assert writer.handle_send_naws is orig_handler

    @pytest.mark.asyncio
    async def test_naws_restored_on_normal_exit(self) -> None:
        """handle_send_naws is restored after normal scaffold exit."""
        from telnetlib3.client_repl import _repl_scaffold

        def orig_handler() -> tuple[int, int]:
            return (24, 80)

        writer = _mock_writer()
        writer.handle_send_naws = orig_handler
        writer.local_option = types.SimpleNamespace(enabled=lambda _: False)
        writer.is_closing = lambda: False

        stdout, _ = _mock_stdout()
        term = types.SimpleNamespace(on_resize=None)

        async with _repl_scaffold(writer, term, stdout) as (scroll, rc):
            assert writer.handle_send_naws is not orig_handler

        assert writer.handle_send_naws is orig_handler


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only")
class TestReplEventLoopDispatch:

    @pytest.mark.asyncio
    async def test_dispatches_to_basic_when_no_pt(self, monkeypatch) -> None:
        import telnetlib3.client_repl as cr

        monkeypatch.setattr(cr, "HAS_PROMPT_TOOLKIT", False)

        calls: list[str] = []

        async def _fake_basic(*args, **kwargs) -> bool:
            calls.append("basic")
            return False

        monkeypatch.setattr(cr, "_repl_event_loop_basic", _fake_basic)

        reader = asyncio.StreamReader()
        stdout, _ = _mock_stdout()
        writer = _mock_writer()
        term = types.SimpleNamespace(on_resize=None)

        result = await cr.repl_event_loop(reader, writer, term, stdout)
        assert result is False
        assert calls == ["basic"]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only")
@pytest.mark.skipif(not HAS_PROMPT_TOOLKIT, reason="prompt_toolkit not installed")
class TestReplEventLoopPt:

    @pytest.mark.asyncio
    async def test_pt_server_bytes_decoded(self) -> None:
        from telnetlib3.client_repl import _repl_event_loop_pt

        reader = asyncio.StreamReader()
        reader.feed_data(b"hello from server")
        reader.feed_eof()

        writer = _mock_writer()
        writer.handle_send_naws = lambda: (24, 80)
        writer.local_option = types.SimpleNamespace(enabled=lambda _: False)
        writer.is_closing = lambda: True
        writer.mode = "local"

        stdout, transport = _mock_stdout()
        term = types.SimpleNamespace(on_resize=None)

        result = await _repl_event_loop_pt(reader, writer, term, stdout)
        assert result is False
        output = bytes(transport.data).decode("utf-8", errors="replace")
        assert "hello from server" in output

    @pytest.mark.asyncio
    async def test_pt_empty_read_continues(self) -> None:
        from telnetlib3.client_repl import _repl_event_loop_pt

        call_count = 0
        original_data = [b"", b"hello", b""]

        class _FakeReader:
            _idx = 0

            async def read(self, n: int) -> bytes:
                if self._idx < len(original_data):
                    data = original_data[self._idx]
                    self._idx += 1
                    return data
                return b""

            def at_eof(self) -> bool:
                return self._idx >= len(original_data)

        reader = _FakeReader()
        writer = _mock_writer()
        writer.handle_send_naws = lambda: (24, 80)
        writer.local_option = types.SimpleNamespace(enabled=lambda _: False)
        writer.is_closing = lambda: True
        writer.mode = "local"

        stdout, transport = _mock_stdout()
        term = types.SimpleNamespace(on_resize=None)

        result = await _repl_event_loop_pt(reader, writer, term, stdout)
        assert result is False
        output = bytes(transport.data).decode("utf-8", errors="replace")
        assert "hello" in output

    @pytest.mark.asyncio
    async def test_pt_autoreply_integration(self) -> None:
        import re
        import logging

        from telnetlib3.autoreply import AutoreplyRule
        from telnetlib3.client_repl import _repl_event_loop_pt

        reader = asyncio.StreamReader()
        reader.feed_data(b"trigger line\n")
        reader.feed_eof()

        written: list[str] = []
        writer = _mock_writer()
        writer.handle_send_naws = lambda: (24, 80)
        writer.local_option = types.SimpleNamespace(enabled=lambda _: False)
        writer.is_closing = lambda: True
        writer.mode = "local"
        writer.write = lambda text: written.append(text)
        writer.log = logging.getLogger("test.pt_autoreply")
        writer._autoreply_rules = [AutoreplyRule(pattern=re.compile(r"trigger"), reply="reply<CR>")]

        stdout, _ = _mock_stdout()
        term = types.SimpleNamespace(on_resize=None)

        await _repl_event_loop_pt(reader, writer, term, stdout)
        await asyncio.sleep(0.15)
        assert any("reply" in w for w in written)


@pytest.mark.skipif(not HAS_PROMPT_TOOLKIT, reason="prompt_toolkit not installed")
class TestLaunchTuiEditor:

    def test_calls_run_in_terminal(self, monkeypatch) -> None:
        from telnetlib3.client_repl import _launch_tui_editor

        called_with: list[object] = []

        import prompt_toolkit.application as pta

        monkeypatch.setattr(pta, "run_in_terminal", lambda fn: called_with.append(fn))

        event = types.SimpleNamespace(app=types.SimpleNamespace())
        writer = types.SimpleNamespace()

        _launch_tui_editor(event, "macros", writer)
        assert len(called_with) == 1
        assert callable(called_with[0])

    def test_reload_macros_after_edit(self, tmp_path, monkeypatch) -> None:
        import json

        from telnetlib3.client_repl import _reload_macros

        macro_file = tmp_path / "macros.json"
        macro_file.write_text(json.dumps({"macros": [{"key": "f5", "text": "hello<CR>"}]}))

        import logging

        writer = types.SimpleNamespace(_macro_defs=[], _macros_file="")
        log = logging.getLogger("test.reload_macros")

        _reload_macros(writer, str(macro_file), log)
        assert len(writer._macro_defs) == 1
        assert writer._macros_file == str(macro_file)

    def test_reload_autoreplies_after_edit(self, tmp_path) -> None:
        import json

        from telnetlib3.client_repl import _reload_autoreplies

        ar_file = tmp_path / "autoreplies.json"
        ar_file.write_text(json.dumps({"autoreplies": [{"pattern": "hello", "reply": "world"}]}))

        import logging

        writer = types.SimpleNamespace(_autoreply_rules=[], _autoreplies_file="")
        log = logging.getLogger("test.reload_autoreplies")

        _reload_autoreplies(writer, str(ar_file), log)
        assert len(writer._autoreply_rules) == 1
        assert writer._autoreplies_file == str(ar_file)

    def test_reload_macros_missing_file(self, tmp_path) -> None:
        import logging

        from telnetlib3.client_repl import _reload_macros

        writer = types.SimpleNamespace(_macro_defs=["original"])
        log = logging.getLogger("test.reload_macros_missing")

        _reload_macros(writer, str(tmp_path / "nonexistent.json"), log)
        assert writer._macro_defs == ["original"]

    def test_reload_autoreplies_missing_file(self, tmp_path) -> None:
        import logging

        from telnetlib3.client_repl import _reload_autoreplies

        writer = types.SimpleNamespace(_autoreply_rules=["original"])
        log = logging.getLogger("test.reload_autoreplies_missing")

        _reload_autoreplies(writer, str(tmp_path / "nonexistent.json"), log)
        assert writer._autoreply_rules == ["original"]


async def _async_return(value: object) -> object:
    return value


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only")
class TestReplEventLoopBasic:

    @pytest.mark.asyncio
    async def test_server_eof(self) -> None:
        """Server EOF closes with 'Connection closed' message."""
        from telnetlib3.client_repl import _repl_event_loop_basic

        reader = asyncio.StreamReader()
        reader.feed_eof()

        written: list[str] = []
        closed = False

        writer = _mock_writer()
        writer.mode = "local"
        writer.handle_send_naws = lambda: (24, 80)
        writer.local_option = types.SimpleNamespace(enabled=lambda _: False)
        writer.is_closing = lambda: False

        def _close() -> None:
            nonlocal closed
            closed = True

        writer.close = _close
        writer.write = lambda data: written.append(data)

        stdin_reader = asyncio.StreamReader()
        stdin_reader.feed_eof()

        term = types.SimpleNamespace(
            on_resize=None,
            connect_stdin=lambda: _async_return(stdin_reader),
        )

        stdout, transport = _mock_stdout()
        result = await _repl_event_loop_basic(reader, writer, term, stdout)
        assert result is False
        output = bytes(transport.data).decode("utf-8", errors="replace")
        assert "Connection closed by foreign host." in output

    @pytest.mark.asyncio
    async def test_kludge_mode_switch(self) -> None:
        """When writer.mode becomes 'kludge' during read, returns True."""
        from telnetlib3.client_repl import _repl_event_loop_basic

        read_count = 0

        class _SwitchReader:
            async def read(self, n: int) -> str:
                nonlocal read_count
                read_count += 1
                if read_count == 1:
                    return "server data"
                return ""

            def at_eof(self) -> bool:
                return read_count > 1

        reader = _SwitchReader()

        writer = _mock_writer()
        writer.mode = "local"
        writer.handle_send_naws = lambda: (24, 80)
        writer.local_option = types.SimpleNamespace(enabled=lambda _: False)
        writer.is_closing = lambda: False
        writer.close = lambda: None
        written_data: list[str] = []
        writer.write = lambda d: written_data.append(d)

        orig_read = reader.read

        async def _patched_read(n: int) -> str:
            result = await orig_read(n)
            if result:
                writer.mode = "kludge"
            return result

        reader.read = _patched_read  # type: ignore[assignment]

        stdin_reader = asyncio.StreamReader()
        stdin_reader.feed_eof()

        term = types.SimpleNamespace(
            on_resize=None,
            connect_stdin=lambda: _async_return(stdin_reader),
        )

        stdout, _ = _mock_stdout()
        result = await _repl_event_loop_basic(reader, writer, term, stdout)
        assert result is True

    @pytest.mark.asyncio
    async def test_user_input_echo(self) -> None:
        """User input is echoed and sent to writer."""
        from telnetlib3.client_repl import _repl_event_loop_basic

        reader = asyncio.StreamReader()
        reader.feed_data(b"welcome prompt")

        written: list[str] = []
        closed = False
        writer = _mock_writer(will_echo=False)
        writer.mode = "local"
        writer.handle_send_naws = lambda: (24, 80)
        writer.local_option = types.SimpleNamespace(enabled=lambda _: False)
        writer.is_closing = lambda: False

        def _close() -> None:
            nonlocal closed
            closed = True

        writer.close = _close
        writer.write = lambda data: written.append(data)

        stdin_reader = asyncio.StreamReader()
        stdin_reader.feed_data(b"hello\n")

        async def _delayed_eof() -> None:
            await asyncio.sleep(0.05)
            stdin_reader.feed_eof()
            reader.feed_eof()

        term = types.SimpleNamespace(
            on_resize=None,
            connect_stdin=lambda: _async_return(stdin_reader),
        )

        stdout, transport = _mock_stdout()
        eof_task = asyncio.ensure_future(_delayed_eof())
        await _repl_event_loop_basic(reader, writer, term, stdout)
        await eof_task

        assert any("hello\r\n" in w for w in written)
        output = bytes(transport.data).decode("utf-8", errors="replace")
        assert "hello" in output

    @pytest.mark.asyncio
    async def test_password_masking(self) -> None:
        """Password input is masked with asterisks when will_echo=True."""
        from telnetlib3.client_repl import _repl_event_loop_basic

        reader = asyncio.StreamReader()
        reader.feed_data(b"login: ")

        written: list[str] = []
        closed = False
        writer = _mock_writer(will_echo=True)
        writer.mode = "local"
        writer.handle_send_naws = lambda: (24, 80)
        writer.local_option = types.SimpleNamespace(enabled=lambda _: False)
        writer.is_closing = lambda: False

        def _close() -> None:
            nonlocal closed
            closed = True

        writer.close = _close
        writer.write = lambda data: written.append(data)

        stdin_reader = asyncio.StreamReader()
        stdin_reader.feed_data(b"secret\n")

        async def _delayed_eof() -> None:
            await asyncio.sleep(0.05)
            stdin_reader.feed_eof()
            reader.feed_eof()

        term = types.SimpleNamespace(
            on_resize=None,
            connect_stdin=lambda: _async_return(stdin_reader),
        )

        stdout, transport = _mock_stdout()
        eof_task = asyncio.ensure_future(_delayed_eof())
        await _repl_event_loop_basic(reader, writer, term, stdout)
        await eof_task

        output = bytes(transport.data).decode("utf-8", errors="replace")
        assert "******" in output
        assert "secret" not in output
