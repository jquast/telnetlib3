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
    assert not repl._rprompt_text


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
    writer.write = written.append
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
    writer.write = written.append
    writer.log = logging.getLogger("test.pt_autoreply_reload")
    writer._autoreply_rules = None

    stdout, _ = _mock_stdout()
    term = types.SimpleNamespace(on_resize=None)

    new_rules = [AutoreplyRule(pattern=re.compile(r"reload trigger"), reply="reloaded;")]

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

    monkeypatch.setattr(pta, "run_in_terminal", called_with.append)

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


@pytest.mark.parametrize(
    "line,expected",
    [
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
    ],
)
def test_expand_commands(line: str, expected: list[str]) -> None:
    from telnetlib3.client_repl import expand_commands

    assert expand_commands(line) == expected


@pytest.mark.parametrize(
    "value,expected",
    [
        (0, "0"),
        (999, "999"),
        (1000, "1.0k"),
        (1500, "1.5k"),
        (12345, "12.3k"),
        (999900, "999.9k"),
        (1000000, "1.0m"),
        (1500000, "1.5m"),
        (123456789, "123.5m"),
    ],
)
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
