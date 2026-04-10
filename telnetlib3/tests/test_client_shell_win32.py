"""
Tests for telnetlib3.client_shell_win32 (Windows terminal mode handling).

Runs cross-platform by injecting a mock ``blessed`` module before import.
"""

# std imports
import sys
import types
import asyncio
import threading
import contextlib
from unittest import mock

# 3rd party
import pytest

# Inject a minimal blessed stub so the module can be imported on Linux.
if "blessed" not in sys.modules:
    _mock_blessed = types.ModuleType("blessed")

    class _MockBlessedTerminal:
        is_a_tty = False

        @contextlib.contextmanager
        def raw(self):
            yield

        def inkey(self, timeout=None):
            return ""

    _mock_blessed.Terminal = _MockBlessedTerminal
    sys.modules["blessed"] = _mock_blessed

# local
from telnetlib3._session_context import TelnetSessionContext  # noqa: E402
from telnetlib3.client_shell_win32 import Terminal  # noqa: E402


class _MockOption:
    def __init__(self, opts: "dict[bytes, bool]") -> None:
        self._opts = opts

    def enabled(self, key: bytes) -> bool:
        return self._opts.get(key, False)


def _make_writer(
    will_echo: bool = False,
    raw_mode: "bool | None" = None,
    will_sga: bool = False,
    local_sga: bool = False,
) -> object:
    from telnetlib3.telopt import SGA

    ctx = TelnetSessionContext()
    ctx.raw_mode = raw_mode
    return types.SimpleNamespace(
        will_echo=will_echo,
        client=True,
        remote_option=_MockOption({SGA: will_sga}),
        local_option=_MockOption({SGA: local_sga}),
        log=types.SimpleNamespace(debug=lambda *a, **kw: None),
        ctx=ctx,
    )


def _make_term(writer: object, istty: bool = False) -> Terminal:
    term = Terminal.__new__(Terminal)
    term.telnet_writer = writer
    term._bt = sys.modules["blessed"].Terminal()
    term._istty = istty
    term._save_mode = None
    term.software_echo = False
    term._raw_ctx = None
    term._resize_pending = threading.Event()
    term.on_resize = None
    term._stop_resize = threading.Event()
    term._stop_stdin = threading.Event()
    term._resize_thread = None
    term._stdin_thread = None
    return term


def _cooked() -> Terminal.ModeDef:
    return Terminal.ModeDef(raw=False, echo=True)


def test_modedef_fields() -> None:
    m = Terminal.ModeDef(raw=True, echo=False)
    assert m.raw is True
    assert m.echo is False


@pytest.mark.parametrize("suppress_echo", [True, False])
def test_make_raw(suppress_echo: bool) -> None:
    term = _make_term(_make_writer())
    result = term._make_raw(_cooked(), suppress_echo=suppress_echo)
    assert result.raw is True
    assert result.echo is not suppress_echo


def test_suppress_echo_clears_echo() -> None:
    result = Terminal._suppress_echo(Terminal.ModeDef(raw=False, echo=True))
    assert result.echo is False
    assert result.raw is False


def test_suppress_echo_preserves_raw() -> None:
    result = Terminal._suppress_echo(Terminal.ModeDef(raw=True, echo=True))
    assert result.raw is True
    assert result.echo is False


def test_server_will_sga_remote() -> None:
    assert _make_term(_make_writer(will_sga=True))._server_will_sga() is True


def test_server_will_sga_local() -> None:
    assert _make_term(_make_writer(local_sga=True))._server_will_sga() is True


def test_server_will_sga_absent() -> None:
    assert _make_term(_make_writer())._server_will_sga() is False


def test_get_mode_not_tty_returns_none() -> None:
    term = _make_term(_make_writer(), istty=False)
    assert term.get_mode() is None


def test_get_mode_tty_returns_cooked_modedef() -> None:
    term = _make_term(_make_writer(), istty=True)
    mode = term.get_mode()
    assert mode is not None
    assert mode.raw is False
    assert mode.echo is True


def test_set_mode_none_is_noop() -> None:
    term = _make_term(_make_writer())
    term.set_mode(None)  # should not raise
    assert term._raw_ctx is None


def test_set_mode_raw_enters_context() -> None:
    entered = []

    class _TrackingCtx:
        def __enter__(self):
            entered.append(True)
            return self

        def __exit__(self, *_):
            pass

    term = _make_term(_make_writer())
    term._bt = mock.Mock()
    term._bt.raw.return_value = _TrackingCtx()
    term.set_mode(Terminal.ModeDef(raw=True, echo=False))
    assert len(entered) == 1
    assert term._raw_ctx is not None


def test_set_mode_cooked_exits_context() -> None:
    closed = []

    class _TrackingCtx:
        def close(self):
            closed.append(True)

    term = _make_term(_make_writer())
    term._raw_ctx = _TrackingCtx()
    term.set_mode(Terminal.ModeDef(raw=False, echo=True))
    assert len(closed) == 1
    assert term._raw_ctx is None


def test_set_mode_raw_when_already_raw_is_noop() -> None:
    term = _make_term(_make_writer())
    existing_ctx = mock.MagicMock()
    term._raw_ctx = existing_ctx
    term.set_mode(Terminal.ModeDef(raw=True, echo=False))
    existing_ctx.__enter__.assert_not_called()


@pytest.mark.parametrize(
    "will_echo,will_sga,raw_mode",
    [(False, False, None), (False, False, False), (True, False, False)],
)
def test_determine_mode_unchanged(will_echo: bool, will_sga: bool, raw_mode: "bool | None") -> None:
    term = _make_term(_make_writer(will_echo=will_echo, will_sga=will_sga, raw_mode=raw_mode))
    mode = _cooked()
    assert term.determine_mode(mode) is mode


@pytest.mark.parametrize(
    "will_echo,will_sga,raw_mode",
    [(True, True, None), (False, True, None), (False, False, True), (True, False, True)],
)
def test_determine_mode_goes_raw(will_echo: bool, will_sga: bool, raw_mode: "bool | None") -> None:
    term = _make_term(_make_writer(will_echo=will_echo, will_sga=will_sga, raw_mode=raw_mode))
    result = term.determine_mode(_cooked())
    assert result.raw is True


def test_determine_mode_will_echo_only_suppresses_echo() -> None:
    term = _make_term(_make_writer(will_echo=True, will_sga=False, raw_mode=None))
    result = term.determine_mode(_cooked())
    assert result.raw is False
    assert result.echo is False


def test_determine_mode_will_sga_only_sets_software_echo() -> None:
    term = _make_term(_make_writer(will_echo=False, will_sga=True, raw_mode=None))
    term.determine_mode(_cooked())
    assert term.software_echo is True


def test_determine_mode_explicit_raw_suppresses_echo() -> None:
    term = _make_term(_make_writer(raw_mode=True))
    result = term.determine_mode(_cooked())
    assert result.raw is True
    assert result.echo is False


def test_check_auto_mode_not_istty_returns_none() -> None:
    term = _make_term(_make_writer(will_echo=True, will_sga=True), istty=False)
    assert term.check_auto_mode(switched_to_raw=False, last_will_echo=False) is None


def test_check_auto_mode_no_change_returns_none() -> None:
    term = _make_term(_make_writer(will_echo=False, will_sga=False), istty=True)
    term._save_mode = _cooked()
    assert term.check_auto_mode(switched_to_raw=False, last_will_echo=False) is None


def test_check_auto_mode_suppress_echo_only() -> None:
    term = _make_term(_make_writer(will_echo=True, will_sga=False), istty=True)
    term._save_mode = _cooked()
    set_modes: list[Terminal.ModeDef] = []
    term.set_mode = set_modes.append  # type: ignore[method-assign]
    result = term.check_auto_mode(switched_to_raw=False, last_will_echo=False)
    assert result == (False, True, False)
    assert len(set_modes) == 1
    assert set_modes[0].echo is False
    assert set_modes[0].raw is False


def test_check_auto_mode_sga_goes_raw() -> None:
    term = _make_term(_make_writer(will_echo=False, will_sga=True), istty=True)
    term._save_mode = _cooked()
    set_modes: list[Terminal.ModeDef] = []
    term.set_mode = set_modes.append  # type: ignore[method-assign]
    result = term.check_auto_mode(switched_to_raw=False, last_will_echo=False)
    assert result is not None
    switched_to_raw, _, _ = result
    assert switched_to_raw is True
    assert set_modes[0].raw is True


def test_check_auto_mode_echo_changed_while_raw() -> None:
    term = _make_term(_make_writer(will_echo=True, will_sga=False), istty=True)
    term._save_mode = _cooked()
    set_modes: list[Terminal.ModeDef] = []
    term.set_mode = set_modes.append  # type: ignore[method-assign]
    result = term.check_auto_mode(switched_to_raw=True, last_will_echo=False)
    assert result is not None
    _, last_will_echo, local_echo = result
    assert last_will_echo is True
    assert local_echo is False


def test_check_auto_mode_already_raw_no_echo_change_returns_none() -> None:
    term = _make_term(_make_writer(will_echo=True, will_sga=False), istty=True)
    term._save_mode = _cooked()
    assert term.check_auto_mode(switched_to_raw=True, last_will_echo=True) is None


def test_setup_winch_not_istty_skips() -> None:
    term = _make_term(_make_writer(), istty=False)
    term.setup_winch()
    assert term._resize_thread is None


@pytest.mark.asyncio
async def test_setup_winch_starts_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    monkeypatch.setattr(os, "get_terminal_size", lambda *_: os.terminal_size((80, 24)))
    term = _make_term(_make_writer(), istty=True)
    term.setup_winch()
    assert term._resize_thread is not None
    assert term._resize_thread.is_alive()
    term.cleanup_winch()


@pytest.mark.asyncio
async def test_cleanup_winch_stops_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    monkeypatch.setattr(os, "get_terminal_size", lambda *_: os.terminal_size((80, 24)))
    term = _make_term(_make_writer(), istty=True)
    term.setup_winch()
    assert term._resize_thread is not None
    term.cleanup_winch()
    assert term._stop_resize.is_set()
    assert term._resize_thread is None


def test_setup_winch_os_error_skips(monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    monkeypatch.setattr(os, "get_terminal_size", mock.Mock(side_effect=OSError))
    term = _make_term(_make_writer(), istty=True)
    term.setup_winch()
    assert term._resize_thread is None


@pytest.mark.asyncio
async def test_resize_poll_detects_change(monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    sizes = [os.terminal_size((80, 24)), os.terminal_size((100, 30))]
    call_count = 0

    def _fake_gts():
        nonlocal call_count
        s = sizes[min(call_count, len(sizes) - 1)]
        call_count += 1
        return s

    monkeypatch.setattr(os, "get_terminal_size", _fake_gts)
    term = _make_term(_make_writer(), istty=True)
    term.setup_winch()
    await asyncio.sleep(0.7)
    term.cleanup_winch()
    assert term._resize_pending.is_set()


def test_disconnect_stdin_sets_stop_flag() -> None:
    term = _make_term(_make_writer())
    reader = mock.Mock(spec=asyncio.StreamReader)
    term.disconnect_stdin(reader)
    assert term._stop_stdin.is_set()


def test_disconnect_stdin_feeds_eof() -> None:
    term = _make_term(_make_writer())
    reader = mock.Mock(spec=asyncio.StreamReader)
    term.disconnect_stdin(reader)
    reader.feed_eof.assert_called_once()


@pytest.mark.asyncio
async def test_make_stdout_write_and_drain() -> None:
    term = _make_term(_make_writer())
    buf = bytearray()
    with mock.patch("sys.stdout") as mock_stdout:
        mock_stdout.buffer = mock.Mock()
        mock_stdout.buffer.write = buf.extend
        mock_stdout.buffer.flush = mock.Mock()
        writer = await term.make_stdout()
        writer.write(b"hello")
        await writer.drain()
    assert bytes(buf) == b"hello"
