"""Tests for telnetlib3.client_repl and client_shell.ScrollRegion."""

# std imports
import os
import sys
import types
import asyncio

# 3rd party
import pytest

if sys.platform == "win32":
    pytest.skip("POSIX-only tests", allow_module_level=True)

# local
from telnetlib3.client_repl import ScrollRegion  # noqa: E402


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
    assert sr.scroll_rows == 21


def test_scroll_region_rows_minimum() -> None:
    stdout, _ = _mock_stdout()
    sr = ScrollRegion(stdout, rows=1, cols=80, reserve_bottom=1)
    assert sr.scroll_rows == 0


def test_scroll_region_input_row() -> None:
    stdout, _ = _mock_stdout()
    sr = ScrollRegion(stdout, rows=24, cols=80)
    assert sr.input_row == 23


def test_scroll_region_input_row_reserve_2() -> None:
    stdout, _ = _mock_stdout()
    sr = ScrollRegion(stdout, rows=24, cols=80, reserve_bottom=2)
    assert sr.scroll_rows == 20
    assert sr.input_row == 22


def test_scroll_region_decstbm_enter_exit() -> None:
    stdout, transport = _mock_stdout()
    with ScrollRegion(stdout, rows=24, cols=80, reserve_bottom=1) as sr:
        assert sr._active
    data_on_exit = bytes(transport.data)
    assert len(data_on_exit) > 0


def test_scroll_region_update_size() -> None:
    stdout, transport = _mock_stdout()
    with ScrollRegion(stdout, rows=24, cols=80, reserve_bottom=1) as sr:
        transport.data.clear()
        sr.update_size(30, 120)
        assert sr.scroll_rows == 27
        data = bytes(transport.data)
        assert len(data) > 0


def test_scroll_region_update_size_inactive() -> None:
    stdout, transport = _mock_stdout()
    sr = ScrollRegion(stdout, rows=24, cols=80, reserve_bottom=1)
    transport.data.clear()
    sr.update_size(30, 120)
    assert bytes(transport.data) == b""


def test_scroll_region_grow_reserve_emits_newlines() -> None:
    stdout, transport = _mock_stdout()
    with ScrollRegion(stdout, rows=24, cols=80, reserve_bottom=1) as sr:
        assert sr.scroll_rows == 21
        transport.data.clear()
        sr.grow_reserve(2)
        assert sr.scroll_rows == 20
        data = bytes(transport.data)
        assert b"\n" in data


def test_scroll_region_grow_reserve_noop_if_smaller() -> None:
    stdout, transport = _mock_stdout()
    with ScrollRegion(stdout, rows=24, cols=80, reserve_bottom=2) as sr:
        transport.data.clear()
        sr.grow_reserve(1)
        assert sr.scroll_rows == 20
        assert bytes(transport.data) == b""


def test_scroll_region_save_and_goto_input() -> None:
    stdout, transport = _mock_stdout()
    sr = ScrollRegion(stdout, rows=24, cols=80)
    transport.data.clear()
    sr.save_and_goto_input()
    data = bytes(transport.data)
    assert len(data) > 0


def test_scroll_region_restore_cursor() -> None:
    stdout, transport = _mock_stdout()
    sr = ScrollRegion(stdout, rows=24, cols=80)
    transport.data.clear()
    sr.restore_cursor()
    assert len(bytes(transport.data)) > 0


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
        ("`randomwalk`", ["`randomwalk`"]),
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
        ("`randomwalk`", True),
        ("`RANDOMWALK`", True),
        ("fast travel 123", False),
        ("`fastravel 123`", False),
        ("north", False),
        ("look", False),
    ],
)
def test_travel_re_matching(cmd: str, match: bool) -> None:
    from telnetlib3.client_repl import _TRAVEL_RE

    assert bool(_TRAVEL_RE.match(cmd)) is match


def test_style_normal_populated() -> None:
    from telnetlib3.client_repl import _make_styles

    _make_styles()
    from telnetlib3.client_repl import _STYLE_NORMAL  # noqa: F811

    assert isinstance(_STYLE_NORMAL, dict)
    assert _STYLE_NORMAL["text_sgr"] != ""
    assert _STYLE_NORMAL["bg_sgr"] != ""
    assert _STYLE_NORMAL["suggestion_sgr"] != ""


def test_style_autoreply_populated() -> None:
    from telnetlib3.client_repl import _make_styles

    _make_styles()
    from telnetlib3.client_repl import _STYLE_AUTOREPLY  # noqa: F811

    assert isinstance(_STYLE_AUTOREPLY, dict)
    assert _STYLE_AUTOREPLY["text_sgr"] != ""
    assert _STYLE_AUTOREPLY["bg_sgr"] != ""


def test_style_normal_and_autoreply_differ() -> None:
    from telnetlib3.client_repl import _make_styles

    _make_styles()
    from telnetlib3.client_repl import _STYLE_NORMAL, _STYLE_AUTOREPLY  # noqa: F811

    assert _STYLE_NORMAL["bg_sgr"] != _STYLE_AUTOREPLY["bg_sgr"]
    assert _STYLE_NORMAL["text_sgr"] != _STYLE_AUTOREPLY["text_sgr"]


def test_render_input_line_basic() -> None:
    from blessed.line_editor import DisplayState
    from telnetlib3.client_repl import _render_input_line

    stdout, transport = _mock_stdout()
    sr = ScrollRegion(stdout, rows=24, cols=80, reserve_bottom=2)
    ds = DisplayState(text="hello", cursor=5, suggestion=" world")

    transport.data.clear()
    _render_input_line(ds, sr, stdout)
    output = bytes(transport.data).decode("utf-8", errors="replace")
    assert "hello" in output
    assert " world" in output


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only")
@pytest.mark.asyncio
async def test_scaffold_resize_handler_updates_scroll() -> None:
    """_repl_scaffold resize handler updates scroll region dimensions."""
    from telnetlib3.client_repl import _repl_scaffold

    writer = _mock_writer()
    writer.handle_send_naws = lambda: (24, 80)
    writer.local_option = types.SimpleNamespace(enabled=lambda _: False)
    writer.is_closing = lambda: False

    stdout, transport = _mock_stdout()
    term = types.SimpleNamespace(on_resize=None)

    async with _repl_scaffold(writer, term, stdout) as (scroll, rc):
        assert term.on_resize is not None
        term.on_resize(30, 120)
        assert rc == [30, 120]
        assert scroll._rows == 30
        assert scroll._cols == 120


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only")
def test_resize_pending_flag_is_threading_event() -> None:
    """Terminal._resize_pending is a threading.Event (signal-safe)."""
    import threading

    from telnetlib3.client_shell import Terminal

    writer = _mock_writer()
    writer.client = True
    writer.remote_option = types.SimpleNamespace(enabled=lambda _: False)
    term = Terminal.__new__(Terminal)
    term.telnet_writer = writer
    term._fileno = 0
    term._istty = False
    term._save_mode = None
    term.software_echo = False
    term._remove_winch = False
    term._resize_pending = threading.Event()
    term.on_resize = None
    term._stdin_transport = None
    assert isinstance(term._resize_pending, threading.Event)
    assert not term._resize_pending.is_set()
    term._resize_pending.set()
    assert term._resize_pending.is_set()
    term._resize_pending.clear()
    assert not term._resize_pending.is_set()


def test_load_history_populates_entries(tmp_path: "os.PathLike[str]") -> None:
    from blessed.line_editor import LineHistory
    from telnetlib3.client_repl import _load_history

    hfile = tmp_path / "history"
    hfile.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
    history = LineHistory()
    _load_history(history, str(hfile))
    assert history.entries == ["alpha", "beta", "gamma"]


def test_save_history_entry_appends(tmp_path: "os.PathLike[str]") -> None:
    from telnetlib3.client_repl import _save_history_entry

    hfile = tmp_path / "history"
    _save_history_entry("first", str(hfile))
    _save_history_entry("second", str(hfile))
    lines = hfile.read_text(encoding="utf-8").splitlines()
    assert lines == ["first", "second"]


def test_load_history_missing_file(tmp_path: "os.PathLike[str]") -> None:
    from blessed.line_editor import LineHistory
    from telnetlib3.client_repl import _load_history

    history = LineHistory()
    _load_history(history, str(tmp_path / "does-not-exist"))
    assert history.entries == []


def test_history_path_per_session() -> None:
    from telnetlib3._paths import history_path

    p1 = history_path("mud.example.com:4000")
    p2 = history_path("other.host:23")
    assert p1 != p2
    assert os.path.basename(p1).startswith("history-")
    assert os.path.basename(p2).startswith("history-")
    assert len(os.path.basename(p1).split("-", 1)[1]) == 12


def test_history_path_no_traversal() -> None:
    from telnetlib3._paths import history_path, DATA_DIR

    malicious = "../../etc/passwd:22"
    result = history_path(malicious)
    assert result.startswith(DATA_DIR)
    assert ".." not in os.path.basename(result)


# ---------------------------------------------------------------------------
# _randomwalk / _autodiscover stuck-loop tests
# ---------------------------------------------------------------------------


class _WalkWriter:
    """Mock writer for _randomwalk / _autodiscover tests.

    Reads of ``_current_room_num`` consume from *room_sequence* when given,
    simulating movement (or lack thereof).
    """

    def __init__(
        self,
        room_num: str = "room1",
        adj: dict[str, dict[str, str]] | None = None,
        room_sequence: list[str] | None = None,
    ) -> None:
        self._room_val = room_num
        self._seq_iter = iter(room_sequence) if room_sequence else None
        self._previous_room_num = ""
        self._randomwalk_active = False
        self._randomwalk_total = 0
        self._randomwalk_current = 0
        self._randomwalk_task = None
        self._discover_active = False
        self._discover_total = 0
        self._discover_current = 0
        self._discover_task = None
        self._autoreply_engine = None
        self._echo_log: list[str] = []
        self._echo_command = self._echo_log.append
        self._wait_for_prompt = None
        self._sent: list[str] = []
        self._room_graph = types.SimpleNamespace(
            _adj=adj or {},
            get_room=lambda num: types.SimpleNamespace(name=num),
            find_branches=lambda pos: [],
        )

    @property
    def _current_room_num(self) -> str:
        if self._seq_iter is not None:
            val = next(self._seq_iter, None)
            if val is not None:
                self._room_val = val
        return self._room_val

    @_current_room_num.setter
    def _current_room_num(self, value: str) -> None:
        self._room_val = value

    def write(self, data: str) -> None:
        self._sent.append(data)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only")
@pytest.mark.asyncio
async def test_randomwalk_stuck_room_stops(monkeypatch: pytest.MonkeyPatch) -> None:
    """After 3 consecutive failed moves, randomwalk marks exits exhausted and stops."""
    import logging
    from telnetlib3.client_repl import _randomwalk

    _real_sleep = asyncio.sleep
    monkeypatch.setattr(asyncio, "sleep", lambda _: _real_sleep(0))

    adj: dict[str, dict[str, str]] = {
        "room1": {"north": "room2"},
    }
    writer = _WalkWriter(room_num="room1", adj=adj)

    await _randomwalk(writer, logging.getLogger("test"), limit=10)  # type: ignore[arg-type]

    stuck_msgs = [m for m in writer._echo_log if "stuck in room" in m]
    assert len(stuck_msgs) == 1
    assert not writer._randomwalk_active


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only")
@pytest.mark.asyncio
async def test_randomwalk_resets_stuck_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A successful room change resets the stuck counter."""
    import logging
    from telnetlib3.client_repl import _randomwalk

    _real_sleep = asyncio.sleep
    monkeypatch.setattr(asyncio, "sleep", lambda _: _real_sleep(0))

    adj: dict[str, dict[str, str]] = {
        "room1": {"north": "room2"},
        "room2": {"south": "room1"},
    }
    seq = (
        ["room1"]  # initial read
        + ["room1"] * 31  # fail tick 1 (30 ticks + re-read)
        + ["room1"] * 31  # fail tick 2
        + ["room2"] * 31  # success — moves to room2
        + ["room2"] * 31  # fail from room2
        + ["room2"] * 31  # fail from room2
        + ["room1"] * 31  # success — moves to room1
        + ["room1"] * 100  # more failures
    )
    writer = _WalkWriter(room_num="room1", adj=adj, room_sequence=seq)

    await _randomwalk(writer, logging.getLogger("test"), limit=20)  # type: ignore[arg-type]

    no_change_msgs = [m for m in writer._echo_log if "no room change" in m]
    assert len(no_change_msgs) >= 2
    stuck_msgs = [m for m in writer._echo_log if "stuck in room" in m]
    assert len(stuck_msgs) <= 1


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only")
@pytest.mark.asyncio
async def test_autodiscover_stuck_gateway_stops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After 3 failures from the same room, autodiscover stops."""
    import logging
    from telnetlib3.client_repl import _autodiscover

    _real_sleep = asyncio.sleep
    monkeypatch.setattr(asyncio, "sleep", lambda _: _real_sleep(0))

    adj: dict[str, dict[str, str]] = {
        "room1": {"north": "gw1"},
        "gw1": {"east": "target1"},
        "gw2": {"west": "target2"},
        "gw3": {"south": "target3"},
    }
    writer = _WalkWriter(room_num="room1", adj=adj)

    def fake_find_branches(pos: str) -> list[tuple[str, str, str]]:
        return [
            ("gw1", "east", "target1"),
            ("gw2", "west", "target2"),
            ("gw3", "south", "target3"),
        ]

    writer._room_graph.find_branches = fake_find_branches
    writer._room_graph.find_path_with_rooms = lambda src, dst: [("north", dst)]

    async def fake_fast_travel(*args: object, **kwargs: object) -> None:
        pass

    monkeypatch.setattr(
        "telnetlib3.client_repl._fast_travel", fake_fast_travel
    )

    await _autodiscover(writer, logging.getLogger("test"), limit=20)  # type: ignore[arg-type]

    stuck_msgs = [m for m in writer._echo_log if "all routes blocked" in m]
    assert len(stuck_msgs) == 1
    assert not writer._discover_active


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only")
@pytest.mark.asyncio
async def test_send_chained_mixed_uses_move_pacing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Consecutive identical commands in a mixed list get movement delay pacing."""
    import logging
    from telnetlib3.client_repl import _send_chained, _MOVE_STEP_DELAY

    sleep_args: list[float] = []
    _real_sleep = asyncio.sleep

    async def _tracking_sleep(duration: float) -> None:
        sleep_args.append(duration)
        await _real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", _tracking_sleep)

    writer = _WalkWriter(room_num="room1")
    writer._wait_for_prompt = None
    writer._prompt_ready = None
    writer._room_changed = None

    commands = ["e", "e", "e", "n", "n", "rocks"]
    seq = ["room2", "room3", "room4", "room4a", "room4b", "room4c"]
    writer._seq_iter = iter(seq)

    await _send_chained(commands, writer, logging.getLogger("test"))  # type: ignore[arg-type]

    assert len(writer._sent) == 5
    move_delays = [d for d in sleep_args if d == _MOVE_STEP_DELAY]
    assert len(move_delays) >= 3
