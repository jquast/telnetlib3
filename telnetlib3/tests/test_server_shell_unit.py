# std imports
import sys
import types
import asyncio

# 3rd party
import pytest

# local
from telnetlib3 import slc as slc_mod
from telnetlib3 import client_shell as cs
from telnetlib3 import guard_shells as gs
from telnetlib3 import server_shell as ss


class DummyWriter:
    def __init__(self, slctab=None):
        self.echos = []
        self.slctab = slctab or slc_mod.generate_slctab()
        # minimal attributes for do_toggle (unused here)
        self.local_option = types.SimpleNamespace(enabled=lambda opt: False)
        self.outbinary = False
        self.inbinary = False
        self.xon_any = False
        self.lflow = True

    def echo(self, data):
        self.echos.append(data)


def _run_readline(sequence):
    """Drive ss.readline coroutine with given sequence and return list of commands produced."""
    w = DummyWriter()
    gen = ss.readline(None, w)
    # prime the coroutine
    gen.send(None)
    cmds = []
    for ch in sequence:
        out = gen.send(ch)
        if out is not None:
            cmds.append(out)
    return cmds, w.echos


def test_readline_basic_and_crlf_and_backspace():
    # simple command, CR terminator
    cmds, echos = _run_readline("foo\r")
    assert cmds == ["foo"]
    assert "".join(echos).endswith("foo")  # echoed chars

    # CRLF pair: the LF after CR should be consumed and not yield an empty command
    cmds, echos = _run_readline("bar\r\n")
    assert cmds == ["bar"]

    # LF as terminator alone
    cmds, _ = _run_readline("baz\n")
    assert cmds == ["baz"]

    # CR NUL should be treated like CRLF (LF/NUL consumed after CR)
    cmds, _ = _run_readline("zip\r\x00zap\r\n")
    assert cmds == ["zip", "zap"]

    # backspace handling (^H and DEL): 'help' after correction
    cmds, echos = _run_readline("\bhel\blp\r")
    assert cmds == ["help"]
    # ensure backspace echoing placed sequence '\b \b'
    assert "\b \b" in "".join(echos)


def test_character_dump_yields_patterns_and_summary():
    it = ss.character_dump(1)  # enter loop
    first = next(it)
    second = next(it)
    assert first.startswith("/" * 80)
    assert second.startswith("\\" * 80)

    # when kb_limit is 0, no loop, only the summary line is yielded
    summary = list(ss.character_dump(0))[-1]
    assert summary.endswith("wrote 0 bytes")


def test_get_slcdata_contains_expected_sections():
    writer = DummyWriter(slctab=slc_mod.generate_slctab())
    out = ss.get_slcdata(writer)
    assert "Special Line Characters:" in out
    # a known supported mapping should appear (like SLC_EC)
    assert "SLC_EC" in out
    # and known unset entries should be listed
    assert "Unset by client:" in out and "SLC_BRK" in out
    # and some not-supported entries section is present
    assert "Not supported by server:" in out


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform == "win32", reason="Terminal class not available on Windows")
async def test_terminal_determine_mode_no_echo_returns_same(monkeypatch):
    # Build a dummy telnet_writer with will_echo False
    class TW:
        will_echo = False
        log = types.SimpleNamespace(debug=lambda *a, **k: None)

    # pytest captures stdin; provide a fake with fileno() for Terminal.__init__
    monkeypatch.setattr(sys, "stdin", types.SimpleNamespace(fileno=lambda: 0))

    term = cs.Terminal(TW())
    ModeDef = cs.Terminal.ModeDef

    # construct a plausible mode tuple (values aren't important here)
    base_mode = ModeDef(
        iflag=0xFFFF,
        oflag=0xFFFF,
        cflag=0xFFFF,
        lflag=0xFFFF,
        ispeed=38400,
        ospeed=38400,
        cc=[0] * 32,
    )

    result = term.determine_mode(base_mode)
    # must be the exact same object when will_echo is False
    assert result is base_mode


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform == "win32", reason="Terminal class not available on Windows")
async def test_terminal_determine_mode_will_echo_adjusts_flags(monkeypatch):
    # Build a dummy telnet_writer with will_echo True
    class TW:
        will_echo = True
        log = types.SimpleNamespace(debug=lambda *a, **k: None)

    # pytest captures stdin; provide a fake with fileno() for Terminal.__init__
    monkeypatch.setattr(sys, "stdin", types.SimpleNamespace(fileno=lambda: 0))

    term = cs.Terminal(TW())
    ModeDef = cs.Terminal.ModeDef
    t = cs.termios

    # Start with flags that should be cleared by determine_mode
    iflag = 0
    for flag in (t.BRKINT, t.ICRNL, t.INPCK, t.ISTRIP, t.IXON):
        iflag |= flag

    # oflag clears OPOST and ONLCR
    oflag = t.OPOST | getattr(t, "ONLCR", 0)

    # cflag: set PARENB and a size other than CS8 to ensure it flips
    cflag = t.PARENB | getattr(t, "CS7", 0) | getattr(t, "CREAD", 0)

    # lflag: will clear ICANON | IEXTEN | ISIG | ECHO
    lflag = t.ICANON | t.IEXTEN | t.ISIG | t.ECHO

    # cc array with different VMIN/VTIME values that should be overridden
    cc = [0] * 32
    cc[t.VMIN] = 0
    cc[t.VTIME] = 1

    base_mode = ModeDef(
        iflag=iflag,
        oflag=oflag,
        cflag=cflag,
        lflag=lflag,
        ispeed=38400,
        ospeed=38400,
        cc=list(cc),
    )

    new_mode = term.determine_mode(base_mode)

    # verify input flags cleared
    for flag in (t.BRKINT, t.ICRNL, t.INPCK, t.ISTRIP, t.IXON):
        assert not new_mode.iflag & flag

    # verify output flags cleared
    assert not new_mode.oflag & t.OPOST
    if hasattr(t, "ONLCR"):
        assert not new_mode.oflag & t.ONLCR

    # verify cflag: PARENB cleared, CS8 set, CSIZE cleared except CS8
    assert not new_mode.cflag & t.PARENB
    assert new_mode.cflag & t.CS8
    # CSIZE mask bits should be exactly CS8 now
    assert (new_mode.cflag & t.CSIZE) == t.CS8
    # CREAD (if present) should remain unchanged
    if hasattr(t, "CREAD") and (cflag & t.CREAD):
        assert new_mode.cflag & t.CREAD

    # verify lflag cleared for ICANON, IEXTEN, ISIG, ECHO
    for flag in (t.ICANON, t.IEXTEN, t.ISIG, t.ECHO):
        assert not new_mode.lflag & flag

    # cc changes
    assert new_mode.cc[t.VMIN] == 1
    assert new_mode.cc[t.VTIME] == 0


class MockReader:
    def __init__(self, data):
        self._data = list(data)
        self._idx = 0

    async def read(self, n):
        if self._idx >= len(self._data):
            return ""
        result = self._data[self._idx]
        self._idx += 1
        return result


class SlowReader:
    async def read(self, n):
        await asyncio.sleep(1.0)
        return ""


class MockWriter:
    def __init__(self):
        self.written = []
        self._closing = False
        self._extra = {"peername": ("127.0.0.1", 12345)}

    def write(self, data):
        self.written.append(data)

    async def drain(self):
        pass

    def get_extra_info(self, key, default=None):
        return self._extra.get(key, default)

    def is_closing(self):
        return self._closing

    def echo(self, data):
        self.written.append(data)


@pytest.mark.parametrize(
    "limit,acquires,expected_count,expected_results",
    [
        pytest.param(1, 1, 1, [True], id="single_acquire"),
        pytest.param(1, 2, 1, [True, False], id="over_limit"),
        pytest.param(3, 3, 3, [True, True, True], id="at_limit"),
        pytest.param(2, 3, 2, [True, True, False], id="over_limit_by_one"),
    ],
)
def test_connection_counter_acquire(limit, acquires, expected_count, expected_results):
    counter = gs.ConnectionCounter(limit)
    results = [counter.try_acquire() for _ in range(acquires)]
    assert results == expected_results
    assert counter.count == expected_count


def test_connection_counter_release():
    counter = gs.ConnectionCounter(2)
    assert counter.try_acquire()
    assert counter.try_acquire()
    assert counter.count == 2
    counter.release()
    assert counter.count == 1
    counter.release()
    counter.release()
    assert counter.count == 0


@pytest.mark.parametrize(
    "input_data,max_len,expected",
    [
        pytest.param("hi\r", 100, "hi", id="cr_terminator"),
        pytest.param("hi\n", 100, "hi", id="lf_terminator"),
        pytest.param("ab", 100, "ab", id="eof_no_terminator"),
        pytest.param("", 100, "", id="empty_input"),
        pytest.param("abcdefgh", 5, "abcde", id="truncated_at_max_len"),
    ],
)
@pytest.mark.asyncio
async def test_read_line_inner(input_data, max_len, expected):
    reader = MockReader(list(input_data))
    result = await gs._read_line_inner(reader, max_len)
    assert result == expected


@pytest.mark.asyncio
async def test_read_line_with_timeout_success():
    reader = MockReader(list("hello\r"))
    result = await gs._read_line(reader, timeout=5.0)
    assert result == "hello"


@pytest.mark.asyncio
async def test_read_line_with_timeout_expires():
    result = await gs._read_line(SlowReader(), timeout=0.01)
    assert result is None


@pytest.mark.asyncio
async def test_robot_shell_full_conversation():
    reader = MockReader(["y", "\r", "n", "o", "\r"])
    writer = MockWriter()
    await gs.robot_shell(reader, writer)
    written = "".join(writer.written)
    assert "Do robots dream of electric sheep?" in written
    assert "windowmakers" in written


@pytest.mark.asyncio
async def test_busy_shell_full_conversation():
    reader = MockReader(["h", "i", "\r", "x", "\r"])
    writer = MockWriter()
    await gs.busy_shell(reader, writer)
    written = "".join(writer.written)
    assert "Machine is busy" in written
    assert "distant explosion" in written


@pytest.mark.parametrize(
    "input_chars,expected",
    [
        pytest.param(["\x1b", "[", "A", "x"], "x", id="csi_sequence"),
        pytest.param(["\x1b", "X"], "X", id="esc_non_bracket"),
        pytest.param(["a"], "a", id="normal_char"),
        pytest.param([""], "", id="eof"),
        pytest.param(["\x1b", "[", "1", ";", "2", "H", "z"], "z", id="csi_with_params"),
        pytest.param(["\x1b", "[", ""], "", id="csi_no_final_byte"),
    ],
)
@pytest.mark.asyncio
async def test_filter_ansi(input_chars, expected):
    reader = MockReader(input_chars)
    writer = MockWriter()
    result = await ss.filter_ansi(reader, writer)
    assert result == expected


@pytest.mark.parametrize(
    "input_chars,expected",
    [
        pytest.param(["h", "e", "l", "l", "o", "\r"], "hello", id="basic"),
        pytest.param(["h", "x", "\x7f", "i", "\r"], "hi", id="with_backspace"),
        pytest.param(["\n", "\x00", "a", "\r"], "a", id="ignores_initial_lf_nul"),
        pytest.param(["a", ""], None, id="returns_none_on_eof"),
    ],
)
@pytest.mark.asyncio
async def test_readline2(input_chars, expected):
    reader = MockReader(input_chars)
    writer = MockWriter()
    result = await ss.readline2(reader, writer)
    assert result == expected


@pytest.mark.parametrize(
    "input_chars,closing,expected",
    [
        pytest.param(["a"], False, "a", id="normal"),
        pytest.param(["\x1b", "A", "x"], False, "x", id="skips_escape"),
        pytest.param([], True, None, id="returns_none_when_closing"),
    ],
)
@pytest.mark.asyncio
async def test_get_next_ascii(input_chars, closing, expected):
    reader = MockReader(input_chars)
    writer = MockWriter()
    writer._closing = closing
    result = await ss.get_next_ascii(reader, writer)
    assert result == expected


class CPRReader:
    def __init__(self, data):
        self._data = list(data)
        self._idx = 0

    async def read(self, n):
        if self._idx >= len(self._data):
            return b""
        result = self._data[self._idx]
        self._idx += 1
        return result


@pytest.mark.parametrize(
    "input_data,expected",
    [
        pytest.param([b"\x1b", b"[", b"1", b"0", b";", b"2", b"0", b"R"], (10, 20), id="valid_cpr"),
        pytest.param([b"\x1b", b"[", b"1", b";", b"1", b"R"], (1, 1), id="single_digit"),
        pytest.param(
            [b"\x1b", b"[", b"2", b"5", b";", b"8", b"0", b"R"], (25, 80), id="typical_size"
        ),
        pytest.param([b""], None, id="eof"),
        pytest.param(
            [b"g", b"a", b"r", b"\x1b", b"[", b"5", b";", b"3", b"R"], (5, 3), id="garbage_prefix"
        ),
    ],
)
@pytest.mark.asyncio
async def test_read_cpr_response(input_data, expected):
    reader = CPRReader(input_data)
    result = await gs._read_cpr_response(reader)
    assert result == expected


@pytest.mark.asyncio
async def test_read_cpr_response_string_input():
    class StringReader:
        def __init__(self):
            self._data = list("\x1b[5;10R")
            self._idx = 0

        async def read(self, n):
            if self._idx >= len(self._data):
                return ""
            result = self._data[self._idx]
            self._idx += 1
            return result

    reader = StringReader()
    result = await gs._read_cpr_response(reader)
    assert result == (5, 10)


class CPRMockWriter:
    def __init__(self):
        self.written = []

    def write(self, data):
        self.written.append(data)

    async def drain(self):
        pass


@pytest.mark.asyncio
async def test_get_cursor_position_success():
    reader = CPRReader([b"\x1b", b"[", b"1", b"0", b";", b"2", b"0", b"R"])
    writer = CPRMockWriter()
    result = await gs._get_cursor_position(reader, writer, timeout=1.0)
    assert result == (10, 20)
    assert "\x1b[6n" in writer.written


@pytest.mark.asyncio
async def test_get_cursor_position_timeout():
    result = await gs._get_cursor_position(SlowReader(), CPRMockWriter(), timeout=0.01)
    assert result == (None, None)


@pytest.mark.asyncio
async def test_get_cursor_position_eof():
    reader = CPRReader([b""])
    writer = CPRMockWriter()
    result = await gs._get_cursor_position(reader, writer, timeout=1.0)
    assert result == (None, None)


@pytest.mark.asyncio
async def test_measure_width_success(monkeypatch):
    positions = iter([(1, 5), (1, 7)])

    async def mock_get_cursor_position(reader, writer, timeout):
        return next(positions)

    monkeypatch.setattr(gs, "_get_cursor_position", mock_get_cursor_position)
    writer = CPRMockWriter()
    result = await gs._measure_width(None, writer, "ab", timeout=1.0)
    assert result == 2
    assert any("\x1b[5G" in w for w in writer.written)


@pytest.mark.asyncio
async def test_measure_width_first_cpr_fails(monkeypatch):
    async def mock_get_cursor_position(reader, writer, timeout):
        return (None, None)

    monkeypatch.setattr(gs, "_get_cursor_position", mock_get_cursor_position)
    result = await gs._measure_width(None, CPRMockWriter(), "x", timeout=1.0)
    assert result is None


@pytest.mark.asyncio
async def test_measure_width_second_cpr_fails(monkeypatch):
    call_count = [0]

    async def mock_get_cursor_position(reader, writer, timeout):
        call_count[0] += 1
        if call_count[0] == 1:
            return (1, 5)
        return (None, None)

    monkeypatch.setattr(gs, "_get_cursor_position", mock_get_cursor_position)
    result = await gs._measure_width(None, CPRMockWriter(), "x", timeout=1.0)
    assert result is None


@pytest.mark.asyncio
async def test_robot_check_returns_true_when_width_is_2(monkeypatch):
    async def mock_measure_width(reader, writer, text, timeout):
        return 2

    monkeypatch.setattr(gs, "_measure_width", mock_measure_width)
    result = await gs.robot_check(None, None, timeout=1.0)
    assert result is True


@pytest.mark.asyncio
async def test_robot_check_returns_false_when_width_is_not_2(monkeypatch):
    async def mock_measure_width(reader, writer, text, timeout):
        return 1

    monkeypatch.setattr(gs, "_measure_width", mock_measure_width)
    result = await gs.robot_check(None, None, timeout=1.0)
    assert result is False


@pytest.mark.asyncio
async def test_robot_check_returns_false_when_width_is_none(monkeypatch):
    async def mock_measure_width(reader, writer, text, timeout):
        return None

    monkeypatch.setattr(gs, "_measure_width", mock_measure_width)
    result = await gs.robot_check(None, None, timeout=1.0)
    assert result is False


@pytest.mark.asyncio
async def test_robot_shell_timeout_on_first_question(monkeypatch):
    call_count = [0]

    async def mock_readline_with_echo(reader, writer, timeout):
        call_count[0] += 1
        if call_count[0] == 1:
            return None
        return "response"

    monkeypatch.setattr(gs, "_readline_with_echo", mock_readline_with_echo)

    writer = MockWriter()
    await gs.robot_shell(MockReader([]), writer)
    written = "".join(writer.written)
    assert "Do robots dream of electric sheep?" in written
    assert call_count[0] == 1


@pytest.mark.asyncio
async def test_robot_shell_timeout_on_second_question(monkeypatch):
    call_count = [0]

    async def mock_readline_with_echo(reader, writer, timeout):
        call_count[0] += 1
        if call_count[0] == 1:
            return "y"
        if call_count[0] == 2:
            return None
        return "response"

    monkeypatch.setattr(gs, "_readline_with_echo", mock_readline_with_echo)

    writer = MockWriter()
    await gs.robot_shell(MockReader([]), writer)
    written = "".join(writer.written)
    assert "Do robots dream of electric sheep?" in written
    assert "windowmakers" in written
    assert call_count[0] == 2


@pytest.mark.asyncio
async def test_busy_shell_timeout_on_first_input(monkeypatch):
    call_count = [0]
    original_read_line = gs._read_line

    async def mock_read_line(reader, timeout, max_len=gs._MAX_INPUT):
        call_count[0] += 1
        if call_count[0] == 1:
            return None
        return await original_read_line(reader, timeout, max_len)

    monkeypatch.setattr(gs, "_read_line", mock_read_line)

    writer = MockWriter()
    await gs.busy_shell(MockReader([]), writer)
    written = "".join(writer.written)
    assert "Machine is busy" in written


@pytest.mark.asyncio
async def test_busy_shell_timeout_on_second_input(monkeypatch):
    call_count = [0]
    original_read_line = gs._read_line

    async def mock_read_line(reader, timeout, max_len=gs._MAX_INPUT):
        call_count[0] += 1
        if call_count[0] == 1:
            return "hi"
        if call_count[0] == 2:
            return None
        return await original_read_line(reader, timeout, max_len)

    monkeypatch.setattr(gs, "_read_line", mock_read_line)

    writer = MockWriter()
    await gs.busy_shell(MockReader([]), writer)
    written = "".join(writer.written)
    assert "Machine is busy" in written
    assert "distant explosion" in written
