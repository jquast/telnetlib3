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
        will_echo=will_echo,
        log=types.SimpleNamespace(debug=lambda *a, **kw: None),
        get_extra_info=lambda name, default=None: default,
        set_iac_callback=lambda cmd, func: None,
    )


def test_scroll_region_rows_property() -> None:
    stdout, _ = _mock_stdout()
    sr = ScrollRegion(stdout, rows=24, cols=80, reserve_bottom=1)
    assert sr.scroll_rows == 23


def test_scroll_region_rows_minimum() -> None:
    stdout, _ = _mock_stdout()
    sr = ScrollRegion(stdout, rows=1, cols=80, reserve_bottom=1)
    assert sr.scroll_rows == 1


def test_scroll_region_input_row() -> None:
    stdout, _ = _mock_stdout()
    sr = ScrollRegion(stdout, rows=24, cols=80)
    assert sr.input_row == 24


def test_scroll_region_input_row_reserve_2() -> None:
    stdout, _ = _mock_stdout()
    sr = ScrollRegion(stdout, rows=24, cols=80, reserve_bottom=2)
    assert sr.scroll_rows == 22
    assert sr.input_row == 23


def test_scroll_region_decstbm_enter_exit() -> None:
    stdout, transport = _mock_stdout()
    with ScrollRegion(stdout, rows=24, cols=80, reserve_bottom=1) as sr:
        assert sr._active
        data_on_enter = bytes(transport.data)
        assert b"\x1b[1;23r" in data_on_enter
    data_on_exit = bytes(transport.data)
    assert b"\x1b[1;24r" in data_on_exit
    assert b"\x1b[24;1H" in data_on_exit


def test_scroll_region_update_size() -> None:
    stdout, transport = _mock_stdout()
    with ScrollRegion(stdout, rows=24, cols=80, reserve_bottom=1) as sr:
        transport.data.clear()
        sr.update_size(30, 120)
        assert sr.scroll_rows == 29
        data = bytes(transport.data)
        assert b"\x1b[1;29r" in data


def test_scroll_region_update_size_inactive() -> None:
    stdout, transport = _mock_stdout()
    sr = ScrollRegion(stdout, rows=24, cols=80, reserve_bottom=1)
    transport.data.clear()
    sr.update_size(30, 120)
    assert bytes(transport.data) == b""


def test_scroll_region_grow_reserve_emits_newlines() -> None:
    stdout, transport = _mock_stdout()
    with ScrollRegion(stdout, rows=24, cols=80, reserve_bottom=1) as sr:
        assert sr.scroll_rows == 23
        transport.data.clear()
        sr.grow_reserve(2)
        assert sr.scroll_rows == 22
        data = bytes(transport.data)
        assert b"\x1b[23;1H" in data
        assert b"\n" in data
        assert b"\x1b[1;22r" in data


def test_scroll_region_grow_reserve_noop_if_smaller() -> None:
    stdout, transport = _mock_stdout()
    with ScrollRegion(stdout, rows=24, cols=80, reserve_bottom=2) as sr:
        transport.data.clear()
        sr.grow_reserve(1)
        assert sr.scroll_rows == 22
        assert bytes(transport.data) == b""


def test_scroll_region_save_and_goto_input() -> None:
    stdout, transport = _mock_stdout()
    sr = ScrollRegion(stdout, rows=24, cols=80)
    transport.data.clear()
    sr.save_and_goto_input()
    data = bytes(transport.data)
    assert b"\x1b7" in data
    assert b"\x1b[24;1H" in data
    assert b"\x1b[2K" in data


def test_scroll_region_restore_cursor() -> None:
    stdout, transport = _mock_stdout()
    sr = ScrollRegion(stdout, rows=24, cols=80)
    transport.data.clear()
    sr.restore_cursor()
    assert bytes(transport.data) == b"\x1b8"


@pytest.mark.asyncio
async def test_basic_line_repl_reads_line() -> None:
    reader = asyncio.StreamReader()
    reader.feed_data(b"hello world\n")
    writer = _mock_writer()
    repl = BasicLineRepl(writer, reader, writer.log)
    assert await repl.prompt() == "hello world"


@pytest.mark.asyncio
async def test_basic_line_repl_strips_trailing_newline() -> None:
    reader = asyncio.StreamReader()
    reader.feed_data(b"test\n")
    writer = _mock_writer()
    repl = BasicLineRepl(writer, reader, writer.log)
    assert await repl.prompt() == "test"


@pytest.mark.asyncio
async def test_basic_line_repl_eof_returns_none() -> None:
    reader = asyncio.StreamReader()
    reader.feed_eof()
    writer = _mock_writer()
    repl = BasicLineRepl(writer, reader, writer.log)
    assert await repl.prompt() is None


def test_has_prompt_toolkit_is_boolean() -> None:
    assert isinstance(HAS_PROMPT_TOOLKIT, bool)


@pytest.mark.skipif(not HAS_PROMPT_TOOLKIT, reason="prompt_toolkit not installed")
def test_pt_repl_password_mode_detection() -> None:
    from telnetlib3.client_repl import PromptToolkitRepl

    writer = _mock_writer(will_echo=True)
    repl = PromptToolkitRepl(writer, writer.log)
    assert repl._is_password_mode() is True


@pytest.mark.skipif(not HAS_PROMPT_TOOLKIT, reason="prompt_toolkit not installed")
def test_pt_repl_no_password_mode() -> None:
    from telnetlib3.client_repl import PromptToolkitRepl

    writer = _mock_writer(will_echo=False)
    repl = PromptToolkitRepl(writer, writer.log)
    assert repl._is_password_mode() is False


@pytest.mark.skipif(not HAS_PROMPT_TOOLKIT, reason="prompt_toolkit not installed")
def test_pt_repl_uses_in_memory_history_by_default() -> None:
    from prompt_toolkit.history import InMemoryHistory

    from telnetlib3.client_repl import PromptToolkitRepl

    writer = _mock_writer()
    repl = PromptToolkitRepl(writer, writer.log)
    assert isinstance(repl._history, InMemoryHistory)


@pytest.mark.skipif(not HAS_PROMPT_TOOLKIT, reason="prompt_toolkit not installed")
def test_pt_repl_uses_file_history_when_path_given(tmp_path) -> None:
    from telnetlib3.client_repl import PromptToolkitRepl, _FilteredFileHistory

    history_path = str(tmp_path / "history")
    writer = _mock_writer()
    repl = PromptToolkitRepl(writer, writer.log, history_file=history_path)
    assert isinstance(repl._history, _FilteredFileHistory)


@pytest.mark.skipif(not HAS_PROMPT_TOOLKIT, reason="prompt_toolkit not installed")
def test_pt_repl_ctrl_bracket_binding_registered() -> None:
    """Ctrl+] key binding is registered on the session."""
    from prompt_toolkit.keys import Keys

    from telnetlib3.client_repl import PromptToolkitRepl

    writer = _mock_writer()
    repl = PromptToolkitRepl(writer, writer.log)
    bindings = repl._session.key_bindings.bindings
    bound_keys = [b.keys for b in bindings]
    assert (Keys.ControlSquareClose,) in bound_keys


@pytest.mark.skipif(not HAS_PROMPT_TOOLKIT, reason="prompt_toolkit not installed")
def test_pt_repl_bracketed_paste_binding_registered() -> None:
    from prompt_toolkit.keys import Keys

    from telnetlib3.client_repl import PromptToolkitRepl

    writer = _mock_writer()
    repl = PromptToolkitRepl(writer, writer.log)
    bindings = repl._session.key_bindings.bindings
    bound_keys = [b.keys for b in bindings]
    assert (Keys.BracketedPaste,) in bound_keys


@pytest.mark.skipif(not HAS_PROMPT_TOOLKIT, reason="prompt_toolkit not installed")
def test_pt_repl_toolbar_contains_connection_info() -> None:
    from telnetlib3.client_repl import PromptToolkitRepl

    writer = _mock_writer()
    repl = PromptToolkitRepl(writer, writer.log, connection_info="mud.example.com:4000 SSL")
    assert repl._rprompt_text == "mud.example.com:4000 SSL"


@pytest.mark.skipif(not HAS_PROMPT_TOOLKIT, reason="prompt_toolkit not installed")
def test_pt_repl_toolbar_without_connection_info() -> None:
    from telnetlib3.client_repl import PromptToolkitRepl

    writer = _mock_writer()
    repl = PromptToolkitRepl(writer, writer.log)
    assert repl._rprompt_text == ""


@pytest.mark.skipif(not HAS_PROMPT_TOOLKIT, reason="prompt_toolkit not installed")
def test_pt_repl_f1_binding_registered() -> None:
    from prompt_toolkit.keys import Keys

    from telnetlib3.client_repl import PromptToolkitRepl

    writer = _mock_writer()
    repl = PromptToolkitRepl(writer, writer.log)
    bindings = repl._session.key_bindings.bindings
    bound_keys = [b.keys for b in bindings]
    assert (Keys.F1,) in bound_keys


@pytest.mark.skipif(not HAS_PROMPT_TOOLKIT, reason="prompt_toolkit not installed")
def test_filtered_history_stores_normal_input(tmp_path) -> None:
    from telnetlib3.client_repl import _FilteredFileHistory

    history_path = str(tmp_path / "history")
    hist = _FilteredFileHistory(history_path)
    hist.store_string("hello")
    content = (tmp_path / "history").read_text()
    assert "+hello" in content


@pytest.mark.skipif(not HAS_PROMPT_TOOLKIT, reason="prompt_toolkit not installed")
def test_filtered_history_skips_password_input(tmp_path) -> None:
    from telnetlib3.client_repl import _FilteredFileHistory

    history_path = str(tmp_path / "history")
    hist = _FilteredFileHistory(history_path, is_password=lambda: True)
    hist.store_string("secret123")
    assert not (tmp_path / "history").exists()


@pytest.mark.skipif(not HAS_PROMPT_TOOLKIT, reason="prompt_toolkit not installed")
def test_filtered_history_stores_when_not_password(tmp_path) -> None:
    from telnetlib3.client_repl import _FilteredFileHistory

    password_mode = False
    history_path = str(tmp_path / "history")
    hist = _FilteredFileHistory(history_path, is_password=lambda: password_mode)
    hist.store_string("visible")
    content = (tmp_path / "history").read_text()
    assert "+visible" in content


@pytest.mark.skipif(not HAS_PROMPT_TOOLKIT, reason="prompt_toolkit not installed")
def test_filtered_history_dynamic_password_toggle(tmp_path) -> None:
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


@pytest.mark.skipif(not HAS_PROMPT_TOOLKIT, reason="prompt_toolkit not installed")
def test_filtered_history_creates_parent_directories(tmp_path) -> None:
    from telnetlib3.client_repl import _make_history, _FilteredFileHistory

    history_path = str(tmp_path / "sub" / "dir" / "history")
    hist = _make_history(history_path)
    assert isinstance(hist, _FilteredFileHistory)
    assert (tmp_path / "sub" / "dir").is_dir()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only")
@pytest.mark.asyncio
async def test_adjusted_naws_active_scroll() -> None:
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


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only")
@pytest.mark.asyncio
async def test_adjusted_naws_inactive_returns_terminal_size() -> None:
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
@pytest.mark.asyncio
async def test_naws_restored_on_exception() -> None:
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


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only")
@pytest.mark.asyncio
async def test_naws_restored_on_normal_exit() -> None:
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
@pytest.mark.asyncio
async def test_dispatch_to_basic_when_no_pt(monkeypatch) -> None:
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
@pytest.mark.asyncio
async def test_pt_server_bytes_decoded() -> None:
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


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only")
@pytest.mark.skipif(not HAS_PROMPT_TOOLKIT, reason="prompt_toolkit not installed")
@pytest.mark.asyncio
async def test_pt_empty_read_continues() -> None:
    from telnetlib3.client_repl import _repl_event_loop_pt

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


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only")
@pytest.mark.skipif(not HAS_PROMPT_TOOLKIT, reason="prompt_toolkit not installed")
@pytest.mark.asyncio
async def test_pt_autoreply_integration() -> None:
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
    writer._autoreply_rules = [AutoreplyRule(pattern=re.compile(r"trigger"), reply="reply;")]

    stdout, _ = _mock_stdout()
    term = types.SimpleNamespace(on_resize=None)

    await _repl_event_loop_pt(reader, writer, term, stdout)
    await asyncio.sleep(0.15)
    assert any("reply" in w for w in written)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only")
@pytest.mark.skipif(not HAS_PROMPT_TOOLKIT, reason="prompt_toolkit not installed")
@pytest.mark.asyncio
async def test_pt_autoreply_hot_reload() -> None:
    import re
    import logging

    from telnetlib3.autoreply import AutoreplyRule
    from telnetlib3.client_repl import _repl_event_loop_pt

    reader = asyncio.StreamReader()

    written: list[str] = []
    writer = _mock_writer()
    writer.handle_send_naws = lambda: (24, 80)
    writer.local_option = types.SimpleNamespace(enabled=lambda _: False)
    writer.is_closing = lambda: True
    writer.mode = "local"
    writer.close = lambda: None
    writer.write = lambda text: written.append(text)
    writer.log = logging.getLogger("test.pt_autoreply_reload")
    writer._autoreply_rules = None

    stdout, _ = _mock_stdout()
    term = types.SimpleNamespace(on_resize=None)

    new_rules = [
        AutoreplyRule(pattern=re.compile(r"reload trigger"), reply="reloaded;")
    ]

    async def _inject_and_eof() -> None:
        await asyncio.sleep(0)
        writer._autoreply_rules = new_rules
        reader.feed_data(b"reload trigger\n")
        await asyncio.sleep(0.1)
        reader.feed_eof()

    asyncio.ensure_future(_inject_and_eof())
    await _repl_event_loop_pt(reader, writer, term, stdout)
    await asyncio.sleep(0.15)
    assert any("reloaded" in w for w in written)


@pytest.mark.skipif(not HAS_PROMPT_TOOLKIT, reason="prompt_toolkit not installed")
def test_launch_tui_editor_calls_run_in_terminal(monkeypatch) -> None:
    from telnetlib3.client_repl import _launch_tui_editor

    called_with: list[object] = []

    import prompt_toolkit.application as pta

    monkeypatch.setattr(pta, "run_in_terminal", lambda fn: called_with.append(fn))

    event = types.SimpleNamespace(app=types.SimpleNamespace())
    writer = types.SimpleNamespace()

    _launch_tui_editor(event, "macros", writer)
    assert len(called_with) == 1
    assert callable(called_with[0])


@pytest.mark.skipif(not HAS_PROMPT_TOOLKIT, reason="prompt_toolkit not installed")
@pytest.mark.parametrize(
    "reload_func,file_key,data_key,attr,file_attr",
    [
        ("_reload_macros", "macros", "macros", "_macro_defs", "_macros_file"),
        (
            "_reload_autoreplies",
            "autoreplies",
            "autoreplies",
            "_autoreply_rules",
            "_autoreplies_file",
        ),
    ],
)
def test_reload_after_edit(tmp_path, reload_func, file_key, data_key, attr, file_attr) -> None:
    import json
    import logging

    import telnetlib3.client_repl as cr

    fn = getattr(cr, reload_func)
    sk = "test.host:23"
    if data_key == "macros":
        payload = {sk: {data_key: [{"key": "f5", "text": "hello;"}]}}
    else:
        payload = {sk: {data_key: [{"pattern": "hello", "reply": "world"}]}}
    data_file = tmp_path / f"{file_key}.json"
    data_file.write_text(json.dumps(payload))

    writer = types.SimpleNamespace(**{attr: [], file_attr: ""})
    log = logging.getLogger(f"test.reload_{file_key}")

    fn(writer, str(data_file), sk, log)
    assert len(getattr(writer, attr)) == 1
    assert getattr(writer, file_attr) == str(data_file)


@pytest.mark.skipif(not HAS_PROMPT_TOOLKIT, reason="prompt_toolkit not installed")
@pytest.mark.parametrize(
    "reload_func,attr",
    [("_reload_macros", "_macro_defs"), ("_reload_autoreplies", "_autoreply_rules")],
)
def test_reload_missing_file(tmp_path, reload_func, attr) -> None:
    import logging

    import telnetlib3.client_repl as cr

    fn = getattr(cr, reload_func)
    writer = types.SimpleNamespace(**{attr: ["original"]})
    log = logging.getLogger(f"test.{reload_func}_missing")

    fn(writer, str(tmp_path / "nonexistent.json"), "test:23", log)
    assert getattr(writer, attr) == ["original"]


@pytest.mark.skipif(not HAS_PROMPT_TOOLKIT, reason="prompt_toolkit not installed")
def test_reload_macros_rebinds_keys(tmp_path) -> None:
    import json
    import logging
    import prompt_toolkit.key_binding

    import telnetlib3.client_repl as cr

    sk = "test.host:23"
    kb = prompt_toolkit.key_binding.KeyBindings()
    writer = types.SimpleNamespace(_macro_defs=[], _macros_file="", _pt_kb=kb)
    log = logging.getLogger("test.reload_rebind")

    initial_count = len(kb.bindings)

    data_file = tmp_path / "macros.json"
    payload = {sk: {"macros": [{"key": "escape h", "text": "hello;"}]}}
    data_file.write_text(json.dumps(payload))

    cr._reload_macros(writer, str(data_file), sk, log)
    assert len(writer._macro_defs) == 1
    assert len(kb.bindings) > initial_count


async def _async_return(value: object) -> object:
    return value


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only")
@pytest.mark.asyncio
async def test_basic_event_loop_server_eof() -> None:
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

    term = types.SimpleNamespace(on_resize=None, connect_stdin=lambda: _async_return(stdin_reader))

    stdout, transport = _mock_stdout()
    result = await _repl_event_loop_basic(reader, writer, term, stdout)
    assert result is False
    output = bytes(transport.data).decode("utf-8", errors="replace")
    assert "Connection closed by foreign host." in output


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only")
@pytest.mark.asyncio
async def test_basic_event_loop_kludge_mode_switch() -> None:
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

    term = types.SimpleNamespace(on_resize=None, connect_stdin=lambda: _async_return(stdin_reader))

    stdout, _ = _mock_stdout()
    result = await _repl_event_loop_basic(reader, writer, term, stdout)
    assert result is True


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only")
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "will_echo,input_data,server_data,check_written,check_output,check_absent",
    [
        (False, b"hello\n", b"welcome prompt", "hello\r\n", "hello", None),
        (True, b"secret\n", b"login: ", None, "******", "secret"),
    ],
)
async def test_basic_event_loop_user_input(
    will_echo, input_data, server_data, check_written, check_output, check_absent
) -> None:
    from telnetlib3.client_repl import _repl_event_loop_basic

    reader = asyncio.StreamReader()
    reader.feed_data(server_data)

    written: list[str] = []
    closed = False
    writer = _mock_writer(will_echo=will_echo)
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
    stdin_reader.feed_data(input_data)

    async def _delayed_eof() -> None:
        await asyncio.sleep(0.05)
        stdin_reader.feed_eof()
        reader.feed_eof()

    term = types.SimpleNamespace(on_resize=None, connect_stdin=lambda: _async_return(stdin_reader))

    stdout, transport = _mock_stdout()
    eof_task = asyncio.ensure_future(_delayed_eof())
    await _repl_event_loop_basic(reader, writer, term, stdout)
    await eof_task

    output = bytes(transport.data).decode("utf-8", errors="replace")
    if check_written is not None:
        assert any(check_written in w for w in written)
    assert check_output in output
    if check_absent is not None:
        assert check_absent not in output


@pytest.mark.asyncio
async def test_fast_travel_corrects_stale_room_id() -> None:
    """Travel continues when room name matches but ID changed."""
    import logging
    from telnetlib3.rooms import Room, RoomGraph
    from telnetlib3.client_repl import _fast_travel

    graph = RoomGraph()
    graph.rooms["room_a"] = Room(num="room_a", name="Start", exits={"west": "old_id"})
    graph.rooms["old_id"] = Room(num="old_id", name="Main street", exits={"west": "room_c"})
    graph.rooms["new_id"] = Room(num="new_id", name="Main street", exits={"west": "room_c"})
    graph.rooms["room_c"] = Room(num="room_c", name="End", exits={})

    written: list[str] = []
    room_changed = asyncio.Event()

    async def _wait_for_prompt() -> None:
        await asyncio.sleep(0)

    def _echo(msg: str) -> None:
        pass

    writer = types.SimpleNamespace(
        write=lambda s: written.append(s),
        _room_graph=graph,
        _current_room_num="room_a",
        _wait_for_prompt=_wait_for_prompt,
        _echo_command=_echo,
        _prompt_ready=asyncio.Event(),
        _room_changed=room_changed,
        _autoreply_engine=None,
    )

    steps: list[tuple[str, str]] = [("west", "old_id"), ("west", "room_c")]

    async def _simulate_room_change() -> None:
        await asyncio.sleep(0)
        writer._current_room_num = "new_id"
        room_changed.set()

    task = asyncio.ensure_future(_simulate_room_change())
    await _fast_travel(
        [steps[0]], writer, logging.getLogger("test"),
    )
    await task

    assert writer._current_room_num == "new_id"
    assert graph.rooms["room_a"].exits["west"] == "new_id"


@pytest.mark.asyncio
async def test_fast_travel_aborts_on_wrong_room_name() -> None:
    """Travel aborts when arriving at a room with a different name."""
    import logging
    from telnetlib3.rooms import Room, RoomGraph
    from telnetlib3.client_repl import _fast_travel

    graph = RoomGraph()
    graph.rooms["room_a"] = Room(num="room_a", name="Start", exits={"west": "expected"})
    graph.rooms["expected"] = Room(num="expected", name="Main street", exits={})
    graph.rooms["wrong"] = Room(num="wrong", name="Dark forest", exits={})

    echoed: list[str] = []
    room_changed = asyncio.Event()

    async def _wait_for_prompt() -> None:
        await asyncio.sleep(0)

    writer = types.SimpleNamespace(
        write=lambda s: None,
        _room_graph=graph,
        _current_room_num="room_a",
        _wait_for_prompt=_wait_for_prompt,
        _echo_command=lambda msg: echoed.append(msg),
        _prompt_ready=asyncio.Event(),
        _room_changed=room_changed,
        _autoreply_engine=None,
    )

    async def _simulate_room_change() -> None:
        await asyncio.sleep(0)
        writer._current_room_num = "wrong"
        room_changed.set()

    task = asyncio.ensure_future(_simulate_room_change())
    await _fast_travel(
        [("west", "expected")], writer, logging.getLogger("test"),
    )
    await task

    assert any("stopped" in msg for msg in echoed)


@pytest.mark.asyncio
async def test_autowander_visits_same_named_rooms() -> None:
    import logging
    from telnetlib3.rooms import Room, RoomGraph
    from telnetlib3.client_repl import _autowander

    graph = RoomGraph()
    graph.rooms["r1"] = Room(
        num="r1", name="A dusty road", last_visited="2024-01-03",
        exits={"east": "r2"},
    )
    graph.rooms["r2"] = Room(
        num="r2", name="A dusty road", last_visited="2024-01-01",
        exits={"west": "r1"},
    )

    written: list[str] = []
    echoed: list[str] = []

    async def _wait_for_prompt() -> None:
        await asyncio.sleep(0)

    writer = types.SimpleNamespace(
        write=lambda s: written.append(s),
        _room_graph=graph,
        _current_room_num="r1",
        _wait_for_prompt=_wait_for_prompt,
        _echo_command=lambda msg: echoed.append(msg),
        _prompt_ready=asyncio.Event(),
        _room_changed=asyncio.Event(),
        _autoreply_engine=None,
        _wander_active=False,
        _wander_current=0,
        _wander_total=0,
        _wander_task=None,
    )

    async def _simulate_arrival() -> None:
        await asyncio.sleep(0)
        writer._current_room_num = "r2"
        writer._room_changed.set()

    task = asyncio.ensure_future(_simulate_arrival())
    await _autowander(writer, logging.getLogger("test"))
    await task

    assert any("east" in w for w in written)
    assert not writer._wander_active
    assert writer._wander_task is None


@pytest.mark.asyncio
async def test_autowander_no_matches() -> None:
    import logging
    from telnetlib3.rooms import Room, RoomGraph
    from telnetlib3.client_repl import _autowander

    graph = RoomGraph()
    graph.rooms["r1"] = Room(num="r1", name="Unique Room", exits={})

    echoed: list[str] = []
    writer = types.SimpleNamespace(
        write=lambda s: None,
        _room_graph=graph,
        _current_room_num="r1",
        _echo_command=lambda msg: echoed.append(msg),
        _wander_active=False,
        _wander_current=0,
        _wander_total=0,
        _wander_task=None,
    )

    await _autowander(writer, logging.getLogger("test"))
    assert any("no matching" in msg for msg in echoed)
    assert not writer._wander_active


@pytest.mark.asyncio
async def test_autowander_no_graph() -> None:
    import logging
    from telnetlib3.client_repl import _autowander

    echoed: list[str] = []
    writer = types.SimpleNamespace(
        _room_graph=None,
        _current_room_num="r1",
        _echo_command=lambda msg: echoed.append(msg),
        _wander_active=False,
        _wander_current=0,
        _wander_total=0,
        _wander_task=None,
    )

    await _autowander(writer, logging.getLogger("test"))
    assert any("no room data" in msg for msg in echoed)


@pytest.mark.asyncio
async def test_autowander_skips_unreachable() -> None:
    import logging
    from telnetlib3.rooms import Room, RoomGraph
    from telnetlib3.client_repl import _autowander

    graph = RoomGraph()
    graph.rooms["r1"] = Room(
        num="r1", name="Road", last_visited="2024-01-01", exits={},
    )
    graph.rooms["r2"] = Room(
        num="r2", name="Road", last_visited="2024-01-02", exits={},
    )

    echoed: list[str] = []
    writer = types.SimpleNamespace(
        write=lambda s: None,
        _room_graph=graph,
        _current_room_num="r1",
        _echo_command=lambda msg: echoed.append(msg),
        _wander_active=False,
        _wander_current=0,
        _wander_total=0,
        _wander_task=None,
    )

    await _autowander(writer, logging.getLogger("test"))
    assert any("no path" in msg for msg in echoed)
    assert not writer._wander_active


@pytest.mark.asyncio
async def test_autowander_cancellation_cleans_up() -> None:
    import logging
    from telnetlib3.rooms import Room, RoomGraph
    from telnetlib3.client_repl import _autowander

    graph = RoomGraph()
    graph.rooms["r1"] = Room(
        num="r1", name="Road", last_visited="2024-01-01",
        exits={"east": "r2"},
    )
    graph.rooms["r2"] = Room(
        num="r2", name="Road", last_visited="2024-01-02",
        exits={"east": "r3"},
    )
    graph.rooms["r3"] = Room(
        num="r3", name="Road", last_visited="2024-01-03",
        exits={},
    )

    async def _wait_for_prompt() -> None:
        await asyncio.sleep(10)

    writer = types.SimpleNamespace(
        write=lambda s: None,
        _room_graph=graph,
        _current_room_num="r1",
        _wait_for_prompt=_wait_for_prompt,
        _echo_command=lambda msg: None,
        _prompt_ready=asyncio.Event(),
        _room_changed=asyncio.Event(),
        _autoreply_engine=None,
        _wander_active=False,
        _wander_current=0,
        _wander_total=0,
        _wander_task=None,
    )

    task = asyncio.ensure_future(_autowander(writer, logging.getLogger("test")))
    writer._wander_task = task
    await asyncio.sleep(0.01)
    assert writer._wander_active
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not writer._wander_active
    assert writer._wander_task is None


@pytest.mark.asyncio
async def test_autowander_guard_already_active() -> None:
    import logging
    from telnetlib3.client_repl import _autowander

    writer = types.SimpleNamespace(
        _wander_active=True,
        _wander_current=1,
        _wander_total=5,
        _wander_task=None,
    )

    await _autowander(writer, logging.getLogger("test"))
    assert writer._wander_active


@pytest.mark.asyncio
async def test_autowander_retries_leg_on_rate_limit() -> None:
    import logging
    from unittest.mock import patch, AsyncMock
    from telnetlib3.rooms import Room, RoomGraph
    from telnetlib3.client_repl import _autowander

    graph = RoomGraph()
    graph.rooms["r1"] = Room(
        num="r1", name="A dusty road", last_visited="2024-01-03",
        exits={"east": "r2"},
    )
    graph.rooms["r2"] = Room(
        num="r2", name="A dusty road", last_visited="2024-01-01",
        exits={"west": "r1"},
    )

    echoed: list[str] = []
    call_count = 0

    writer = types.SimpleNamespace(
        write=lambda s: None,
        _room_graph=graph,
        _current_room_num="r1",
        _wait_for_prompt=AsyncMock(),
        _echo_command=lambda msg: echoed.append(msg),
        _prompt_ready=asyncio.Event(),
        _room_changed=asyncio.Event(),
        _autoreply_engine=None,
        _wander_active=False,
        _wander_current=0,
        _wander_total=0,
        _wander_task=None,
    )

    async def _fake_fast_travel(steps, w, log, slow=False, destination=""):
        nonlocal call_count
        call_count += 1
        if call_count >= 2:
            w._current_room_num = "r2"

    with patch("telnetlib3.client_repl._fast_travel", side_effect=_fake_fast_travel):
        await _autowander(writer, logging.getLogger("test"))

    assert call_count >= 2
    assert not writer._wander_active
    assert any("retry" in e for e in echoed)


@pytest.mark.asyncio
async def test_fast_travel_reroutes_on_wrong_room() -> None:
    import logging
    from telnetlib3.client_repl import _fast_travel
    from telnetlib3.rooms import RoomGraph, Room

    graph = RoomGraph()
    graph.rooms["A"] = Room(num="A", name="Start", exits={"east": "B"})
    graph.rooms["B"] = Room(num="B", name="Middle", exits={"east": "C", "west": "A"})
    graph.rooms["C"] = Room(num="C", name="End", exits={"west": "B"})
    graph.rooms["X"] = Room(num="X", name="Detour", exits={"north": "C", "south": "A"})

    written: list[str] = []
    echoed: list[str] = []
    room_seq = iter(["X", "C"])

    async def _wait() -> None:
        await asyncio.sleep(0)

    def _move(s: str) -> None:
        written.append(s)
        try:
            writer._current_room_num = next(room_seq)
            writer._room_changed.set()
        except StopIteration:
            pass

    writer = types.SimpleNamespace(
        write=_move,
        _room_graph=graph,
        _current_room_num="A",
        _wait_for_prompt=_wait,
        _echo_command=lambda msg: echoed.append(msg),
        _prompt_ready=asyncio.Event(),
        _room_changed=asyncio.Event(),
        _autoreply_engine=None,
    )

    steps = [("east", "B"), ("east", "C")]
    await _fast_travel(steps, writer, logging.getLogger("test"), destination="C")

    assert writer._current_room_num == "C"
    assert any("re-routing" in e for e in echoed)


@pytest.mark.asyncio
async def test_send_chained_sends_remaining_commands() -> None:
    import logging
    from telnetlib3.client_repl import _send_chained

    written: list[str] = []
    echoed: list[str] = []
    prompt_ready = asyncio.Event()
    prompt_ready.set()

    async def _wait_for_prompt() -> None:
        await asyncio.sleep(0)

    writer = types.SimpleNamespace(
        write=lambda s: written.append(s),
        _wait_for_prompt=_wait_for_prompt,
        _echo_command=lambda msg: echoed.append(msg),
        _prompt_ready=prompt_ready,
    )

    commands = ["up", "get Crysknife", "down"]
    await _send_chained(commands, writer, logging.getLogger("test"))
    assert "get Crysknife\r\n" in written
    assert "down\r\n" in written
    assert "up\r\n" not in written
    assert "get Crysknife" in echoed
    assert "down" in echoed


@pytest.mark.asyncio
async def test_send_chained_repeated_retries_on_rate_limit() -> None:
    import logging
    from telnetlib3.client_repl import _send_chained

    written: list[str] = []
    send_count = 0

    async def _wait_for_prompt() -> None:
        await asyncio.sleep(0)

    room_changed = asyncio.Event()
    writer = types.SimpleNamespace(
        write=lambda s: written.append(s),
        _wait_for_prompt=_wait_for_prompt,
        _echo_command=lambda msg: None,
        _prompt_ready=asyncio.Event(),
        _room_changed=room_changed,
        _current_room_num="r1",
    )
    writer._prompt_ready.set()

    orig_write = writer.write

    def _tracking_write(s: str) -> None:
        nonlocal send_count
        orig_write(s)
        send_count += 1
        if send_count >= 3:
            writer._current_room_num = "r2"
            room_changed.set()

    writer.write = _tracking_write

    commands = ["east", "east", "east"]
    await _send_chained(commands, writer, logging.getLogger("test"))
    east_sends = [w for w in written if "east" in w]
    assert len(east_sends) >= 3
    assert writer._current_room_num == "r2"


@pytest.mark.asyncio
async def test_send_chained_mixed_commands_no_retry() -> None:
    import logging
    from telnetlib3.client_repl import _send_chained

    written: list[str] = []

    async def _wait_for_prompt() -> None:
        await asyncio.sleep(0)

    writer = types.SimpleNamespace(
        write=lambda s: written.append(s),
        _wait_for_prompt=_wait_for_prompt,
        _echo_command=lambda msg: None,
        _prompt_ready=asyncio.Event(),
        _current_room_num="r1",
    )
    writer._prompt_ready.set()

    commands = ["look", "east", "glance"]
    await _send_chained(commands, writer, logging.getLogger("test"))
    assert "east\r\n" in written
    assert "glance\r\n" in written


@pytest.mark.parametrize("line,expected", [
    ("5e", ["e"] * 5),
    ("3north", ["north"] * 3),
    ("5east", ["east"] * 5),
    ("6e;9n;rocks", ["e"] * 6 + ["n"] * 9 + ["rocks"]),
    ("look", ["look"]),
    ("n;e;s;w", ["n", "e", "s", "w"]),
    ("2n;look;3s", ["n", "n", "look", "s", "s", "s"]),
    ("42", ["42"]),
    ("100", ["100"]),
    ("2 apples", ["2 apples"]),
    ("", []),
    ("`fast travel 42`", ["`fast travel 42`"]),
    ("look;`delay 1s`;north", ["look", "`delay 1s`", "north"]),
    ("`autowander`", ["`autowander`"]),
    ("3e;`slow travel 99`", ["e", "e", "e", "`slow travel 99`"]),
])
def test_expand_commands(line: str, expected: list[str]) -> None:
    from telnetlib3.client_repl import expand_commands
    assert expand_commands(line) == expected


@pytest.mark.parametrize("value,expected", [
    (0, "0"),
    (999, "999"),
    (1000, "1.0k"),
    (1500, "1.5k"),
    (12345, "12.3k"),
    (999900, "999.9k"),
    (1000000, "1.0m"),
    (1500000, "1.5m"),
    (123456789, "123.5m"),
])
def test_fmt_value(value: int, expected: str) -> None:
    from telnetlib3.client_repl import _fmt_value
    assert _fmt_value(value) == expected


@pytest.mark.parametrize(
    "data, flush, hold",
    [
        (b"", b"", b""),
        (b"hello", b"hello", b""),
        (b"\x1b[32mgreen\x1b[0m", b"\x1b[32mgreen\x1b[0m", b""),
        (b"text\x1b", b"text", b"\x1b"),
        (b"text\x1b[", b"text", b"\x1b["),
        (b"text\x1b[1", b"text", b"\x1b[1"),
        (b"text\x1b[1;33", b"text", b"\x1b[1;33"),
        (b"text\x1b[1;33;48;2;255;128;0", b"text", b"\x1b[1;33;48;2;255;128;0"),
        (b"text\x1b[1;33m", b"text\x1b[1;33m", b""),
        (b"\x1b[1m\x1b", b"\x1b[1m", b"\x1b"),
        (b"\x1b[1m\x1b[32m", b"\x1b[1m\x1b[32m", b""),
        (b"text\x1b]8;;http://x", b"text", b"\x1b]8;;http://x"),
        (b"text\x1b]8;;\x07", b"text\x1b]8;;\x07", b""),
        (b"text\x1b]0;title\x1b\\", b"text\x1b]0;title\x1b\\", b""),
        (b"text\x1bP0;1|data", b"text", b"\x1bP0;1|data"),
        (b"text\x1bP0;1|data\x1b\\", b"text\x1bP0;1|data\x1b\\", b""),
        (b"text\x1b7", b"text\x1b7", b""),
        (b"text\x1b(B", b"text\x1b(B", b""),
    ],
)
def test_split_incomplete_esc(data: bytes, flush: bytes, hold: bytes) -> None:
    from telnetlib3.client_repl import _split_incomplete_esc
    got_flush, got_hold = _split_incomplete_esc(data)
    assert got_flush == flush
    assert got_hold == hold
    assert got_flush + got_hold == data


@pytest.mark.parametrize(
    "cmd, match",
    [
        ("`fast travel 123`", True),
        ("`slow travel 456`", True),
        ("`return fast`", True),
        ("`return slow`", True),
        ("`Fast Travel 123`", True),
        ("`SLOW TRAVEL 789`", True),
        ("`fast travel`", True),
        ("`autowander`", True),
        ("`AUTOWANDER`", True),
        ("fast travel 123", False),
        ("`fastravel 123`", False),
        ("north", False),
        ("look", False),
    ],
)
def test_travel_re_matching(cmd: str, match: bool) -> None:
    from telnetlib3.client_repl import _TRAVEL_RE
    assert bool(_TRAVEL_RE.match(cmd)) is match


@pytest.mark.asyncio
async def test_handle_travel_commands_fast(tmp_path) -> None:
    import logging
    from telnetlib3.rooms import RoomGraph, Room, save_rooms
    from telnetlib3.client_repl import _handle_travel_commands

    rp = tmp_path / "rooms.json"
    g = RoomGraph()
    g.rooms["1"] = Room(num="1", name="Start", exits={"north": "2"})
    g.rooms["2"] = Room(num="2", name="End", exits={"south": "1"})
    save_rooms(str(rp), g)

    written: list[str] = []
    room_changed = asyncio.Event()

    async def _wait() -> None:
        await asyncio.sleep(0)

    writer = types.SimpleNamespace(
        write=lambda s: written.append(s),
        _room_graph=g,
        _current_room_num="1",
        _rooms_file=str(rp),
        _session_key="test:23",
        _wait_for_prompt=_wait,
        _echo_command=lambda msg: None,
        _prompt_ready=asyncio.Event(),
        _room_changed=room_changed,
        _autoreply_engine=None,
        suppress_exclusive=False,
    )

    async def _sim() -> None:
        await asyncio.sleep(0)
        writer._current_room_num = "2"
        room_changed.set()

    task = asyncio.ensure_future(_sim())
    remainder = await _handle_travel_commands(
        ["`fast travel 2`", "order tonic"], writer, logging.getLogger("test"),
    )
    await task

    assert remainder == ["order tonic"]
    assert any("north" in w for w in written)


@pytest.mark.asyncio
async def test_handle_travel_commands_return_already_here(tmp_path) -> None:
    """```return fast``` at current room is a no-op (0-step path)."""
    import logging
    from telnetlib3.rooms import RoomGraph, Room, save_rooms
    from telnetlib3.client_repl import _handle_travel_commands

    rp = tmp_path / "rooms.json"
    g = RoomGraph()
    g.rooms["5"] = Room(num="5", name="Start", exits={"east": "6"})
    g.rooms["6"] = Room(num="6", name="End", exits={"west": "5"})
    save_rooms(str(rp), g)

    writer = types.SimpleNamespace(
        write=lambda s: None,
        _room_graph=g,
        _current_room_num="5",
        _rooms_file=str(rp),
        _session_key="test:23",
        _wait_for_prompt=lambda: asyncio.sleep(0),
        _echo_command=lambda msg: None,
        _prompt_ready=asyncio.Event(),
        _room_changed=asyncio.Event(),
        _autoreply_engine=None,
        suppress_exclusive=False,
    )

    remainder = await _handle_travel_commands(
        ["`return fast`", "look"], writer, logging.getLogger("test"),
    )
    assert remainder == ["look"]


@pytest.mark.asyncio
async def test_handle_travel_commands_no_travel() -> None:
    import logging
    from telnetlib3.client_repl import _handle_travel_commands

    writer = types.SimpleNamespace(
        _current_room_num="1",
        _rooms_file="",
        _session_key="",
    )

    remainder = await _handle_travel_commands(
        ["look", "north"], writer, logging.getLogger("test"),
    )
    assert remainder == ["look", "north"]


@pytest.mark.asyncio
async def test_execute_macro_commands_plain() -> None:
    import logging
    from telnetlib3.client_repl import execute_macro_commands

    written: list[str] = []
    prompt_ready = asyncio.Event()
    prompt_ready.set()

    async def _wait() -> None:
        await asyncio.sleep(0)

    writer = types.SimpleNamespace(
        write=lambda s: written.append(s),
        log=logging.getLogger("test"),
        _wait_for_prompt=_wait,
        _echo_command=lambda cmd: None,
        _prompt_ready=prompt_ready,
        _current_room_num="",
        _rooms_file="",
        _session_key="",
    )

    await execute_macro_commands("look;north;", writer, logging.getLogger("test"))
    assert "look\r\n" in written
    assert "north\r\n" in written


@pytest.mark.asyncio
async def test_execute_macro_commands_delay() -> None:
    import logging
    import time
    from telnetlib3.client_repl import execute_macro_commands

    written: list[str] = []
    prompt_ready = asyncio.Event()
    prompt_ready.set()

    async def _wait() -> None:
        await asyncio.sleep(0)

    writer = types.SimpleNamespace(
        write=lambda s: written.append(s),
        log=logging.getLogger("test"),
        _wait_for_prompt=_wait,
        _echo_command=lambda cmd: None,
        _prompt_ready=prompt_ready,
        _current_room_num="",
        _rooms_file="",
        _session_key="",
    )

    t0 = time.monotonic()
    await execute_macro_commands("look;`delay 50ms`;north;", writer, logging.getLogger("test"))
    elapsed = time.monotonic() - t0
    assert elapsed >= 0.04
    assert "look\r\n" in written
    assert "north\r\n" in written


@pytest.mark.asyncio
async def test_execute_macro_commands_travel(tmp_path) -> None:
    import logging
    from telnetlib3.rooms import RoomGraph, Room, save_rooms
    from telnetlib3.client_repl import execute_macro_commands

    rp = tmp_path / "rooms.json"
    g = RoomGraph()
    g.rooms["1"] = Room(num="1", name="Start", exits={"north": "2"})
    g.rooms["2"] = Room(num="2", name="End", exits={"south": "1"})
    save_rooms(str(rp), g)

    written: list[str] = []
    room_changed = asyncio.Event()
    prompt_ready = asyncio.Event()
    prompt_ready.set()

    async def _wait() -> None:
        await asyncio.sleep(0)

    writer = types.SimpleNamespace(
        write=lambda s: written.append(s),
        log=logging.getLogger("test"),
        _room_graph=g,
        _current_room_num="1",
        _rooms_file=str(rp),
        _session_key="test:23",
        _wait_for_prompt=_wait,
        _echo_command=lambda cmd: None,
        _prompt_ready=prompt_ready,
        _room_changed=room_changed,
        _autoreply_engine=None,
        suppress_exclusive=False,
    )

    async def _sim() -> None:
        await asyncio.sleep(0)
        writer._current_room_num = "2"
        room_changed.set()

    task = asyncio.ensure_future(_sim())
    await execute_macro_commands(
        "look;`fast travel 2`;score;", writer, logging.getLogger("test"),
    )
    await task
    assert "look\r\n" in written
    assert any("north" in w for w in written)
    assert "score\r\n" in written
