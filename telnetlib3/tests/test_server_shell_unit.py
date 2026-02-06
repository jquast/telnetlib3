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
        self.local_option = types.SimpleNamespace(enabled=lambda opt: False)
        self.outbinary = False
        self.inbinary = False
        self.xon_any = False
        self.lflow = True

    def echo(self, data):
        self.echos.append(data)


def _run_readline(sequence):
    w = DummyWriter()
    gen = ss.readline(None, w)
    gen.send(None)
    cmds = []
    for ch in sequence:
        out = gen.send(ch)
        if out is not None:
            cmds.append(out)
    return cmds, w.echos


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


class _MockProtocol:
    def __init__(self, never_send_ga=False):
        self.never_send_ga = never_send_ga


class MockWriter:
    def __init__(self, protocol=None):
        self.written = []
        self._closing = False
        self._extra = {"peername": ("127.0.0.1", 12345)}
        self.protocol = protocol or _MockProtocol()
        self.ga_calls = []

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

    def close(self):
        self._closing = True

    def send_ga(self):
        self.ga_calls.append(True)
        return True


def test_readline_basic_and_crlf_and_backspace():
    cmds, echos = _run_readline("foo\r")
    assert cmds == ["foo"]
    assert "".join(echos).endswith("foo")

    cmds, _ = _run_readline("bar\r\n")
    assert cmds == ["bar"]

    cmds, _ = _run_readline("baz\n")
    assert cmds == ["baz"]

    cmds, _ = _run_readline("zip\r\x00zap\r\n")
    assert cmds == ["zip", "zap"]

    cmds, echos = _run_readline("\bhel\blp\r")
    assert cmds == ["help"]
    assert "\b \b" in "".join(echos)


def test_character_dump_yields_patterns_and_summary():
    it = ss.character_dump(1)
    assert next(it).startswith("/" * 80)
    assert next(it).startswith("\\" * 80)
    assert list(ss.character_dump(0))[-1].endswith("wrote 0 bytes")


def test_get_slcdata_contains_expected_sections():
    out = ss.get_slcdata(DummyWriter(slctab=slc_mod.generate_slctab()))
    assert "Special Line Characters:" in out
    assert "SLC_EC" in out
    assert "Unset by client:" in out and "SLC_BRK" in out
    assert "Not supported by server:" in out


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform == "win32", reason="requires termios")
async def test_terminal_determine_mode(monkeypatch):
    monkeypatch.setattr(sys, "stdin", types.SimpleNamespace(fileno=lambda: 0))
    tw = types.SimpleNamespace(
        will_echo=False,
        log=types.SimpleNamespace(debug=lambda *a, **k: None),
    )
    term = cs.Terminal(tw)
    mode = cs.Terminal.ModeDef(0xFFFF, 0xFFFF, 0xFFFF, 0xFFFF, 38400, 38400, [0] * 32)
    assert term.determine_mode(mode) is mode

    tw.will_echo = True
    t = cs.termios
    cc = [0] * 32
    cc[t.VMIN] = 0
    cc[t.VTIME] = 1
    mode = cs.Terminal.ModeDef(
        t.BRKINT | t.ICRNL | t.INPCK | t.ISTRIP | t.IXON,
        t.OPOST | getattr(t, "ONLCR", 0),
        t.PARENB | getattr(t, "CS7", 0),
        t.ICANON | t.IEXTEN | t.ISIG | t.ECHO,
        38400,
        38400,
        list(cc),
    )
    new = term.determine_mode(mode)
    for flag in (t.BRKINT, t.ICRNL, t.INPCK, t.ISTRIP, t.IXON):
        assert not new.iflag & flag
    assert not new.oflag & t.OPOST
    assert not new.cflag & t.PARENB
    assert new.cflag & t.CS8
    for flag in (t.ICANON, t.IEXTEN, t.ISIG, t.ECHO):
        assert not new.lflag & flag
    assert new.cc[t.VMIN] == 1
    assert new.cc[t.VTIME] == 0


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
    assert await gs._read_line_inner(MockReader(list(input_data)), max_len) == expected


@pytest.mark.asyncio
async def test_read_line_with_timeout():
    assert await gs._read_line(MockReader(list("hello\r")), timeout=5.0) == "hello"
    assert await gs._read_line(SlowReader(), timeout=0.01) is None


@pytest.mark.asyncio
async def test_robot_shell_full_conversation():
    writer = MockWriter()
    await gs.robot_shell(MockReader(["y", "\r", "n", "o", "\r"]), writer)
    written = "".join(writer.written)
    assert "Do robots dream of electric sheep?" in written
    assert "windowmakers" in written


@pytest.mark.asyncio
async def test_busy_shell_full_conversation():
    writer = MockWriter()
    await gs.busy_shell(MockReader(["h", "i", "\r", "x", "\r"]), writer)
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
    assert await ss.filter_ansi(MockReader(input_chars), MockWriter()) == expected


@pytest.mark.parametrize(
    "input_chars,expected",
    [
        pytest.param(["h", "e", "l", "l", "o", "\r"], "hello", id="basic"),
        pytest.param(["h", "x", "\x7f", "i", "\r"], "hi", id="with_backspace"),
        pytest.param(["\n", "\x00", "a", "\r"], "a", id="ignores_initial_lf_nul"),
        pytest.param(["a", ""], None, id="returns_none_on_eof"),
        pytest.param(["\x7f", "a", "\r"], "a", id="backspace_on_empty"),
        pytest.param(["\b", "\b", "x", "\r"], "x", id="multiple_backspace_on_empty"),
    ],
)
@pytest.mark.asyncio
async def test_readline2(input_chars, expected):
    assert await ss.readline2(MockReader(input_chars), MockWriter()) == expected


@pytest.mark.parametrize(
    "input_chars,closing,expected",
    [
        pytest.param(["a"], False, "a", id="normal"),
        pytest.param(["\x1b", "A", "x"], False, "x", id="skips_escape"),
        pytest.param([], True, None, id="returns_none_when_closing"),
        pytest.param(["\x1b", "1", "A", "x"], False, "x", id="escape_non_letter"),
    ],
)
@pytest.mark.asyncio
async def test_get_next_ascii(input_chars, closing, expected):
    writer = MockWriter()
    writer._closing = closing
    assert await ss.get_next_ascii(MockReader(input_chars), writer) == expected


@pytest.mark.parametrize(
    "input_data,expected",
    [
        pytest.param([b"\x1b", b"[", b"1", b"0", b";", b"2", b"0", b"R"], (10, 20), id="valid"),
        pytest.param([b"\x1b", b"[", b"1", b";", b"1", b"R"], (1, 1), id="single_digit"),
        pytest.param([b"\x1b", b"[", b"2", b"5", b";", b"8", b"0", b"R"], (25, 80), id="typical"),
        pytest.param([b""], None, id="eof"),
        pytest.param(
            [b"g", b"a", b"r", b"\x1b", b"[", b"5", b";", b"3", b"R"], (5, 3), id="garbage_prefix"
        ),
        pytest.param([b"R", b"\x1b", b"[", b"3", b";", b"7", b"R"], (3, 7), id="R_without_match"),
        pytest.param(list("\x1b[5;10R"), (5, 10), id="string_input"),
    ],
)
@pytest.mark.asyncio
async def test_read_cpr_response(input_data, expected):
    assert await gs._read_cpr_response(MockReader(input_data)) == expected


@pytest.mark.asyncio
async def test_read_cpr_response_unicode_decode_error():
    class BadReader:
        def __init__(self):
            self._call = 0

        async def read(self, n):
            self._call += 1
            if self._call == 1:
                raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid")
            return b""

    assert await gs._read_cpr_response(BadReader()) is None


@pytest.mark.asyncio
async def test_get_cursor_position_success():
    reader = MockReader([b"\x1b", b"[", b"1", b"0", b";", b"2", b"0", b"R"])
    writer = MockWriter()
    assert await gs._get_cursor_position(reader, writer, timeout=1.0) == (10, 20)
    assert "\x1b[6n" in writer.written


@pytest.mark.asyncio
async def test_get_cursor_position_failure():
    assert await gs._get_cursor_position(SlowReader(), MockWriter(), timeout=0.01) == (None, None)
    assert await gs._get_cursor_position(MockReader([b""]), MockWriter(), timeout=1.0) == (
        None,
        None,
    )


@pytest.mark.parametrize(
    "responses,expected",
    [
        pytest.param([(1, 5), (1, 7)], 2, id="success"),
        pytest.param([(None, None)], None, id="first_cpr_fails"),
        pytest.param([(1, 5), (None, None)], None, id="second_cpr_fails"),
    ],
)
@pytest.mark.asyncio
async def test_measure_width(monkeypatch, responses, expected):
    responses_iter = iter(responses)

    async def mock_gcp(reader, writer, timeout):
        return next(responses_iter)

    monkeypatch.setattr(gs, "_get_cursor_position", mock_gcp)
    assert await gs._measure_width(None, MockWriter(), "ab", timeout=1.0) == expected


@pytest.mark.parametrize(
    "width,expected",
    [
        pytest.param(2, True, id="width_2"),
        pytest.param(1, False, id="width_1"),
        pytest.param(None, False, id="width_none"),
    ],
)
@pytest.mark.asyncio
async def test_robot_check(monkeypatch, width, expected):
    async def mock_measure(r, w, text, timeout):
        return width

    monkeypatch.setattr(gs, "_measure_width", mock_measure)
    assert await gs.robot_check(None, None, timeout=1.0) is expected


@pytest.mark.asyncio
async def test_readline_with_echo_timeout():
    assert await gs._readline_with_echo(SlowReader(), MockWriter(), timeout=0.01) is None


@pytest.mark.parametrize(
    "timeout_at",
    [pytest.param(1, id="first_question"), pytest.param(2, id="second_question")],
)
@pytest.mark.asyncio
async def test_robot_shell_timeout(monkeypatch, timeout_at):
    call_count = [0]

    async def mock_readline_with_echo(reader, writer, timeout):
        call_count[0] += 1
        if call_count[0] == timeout_at:
            return None
        return "y"

    monkeypatch.setattr(gs, "_readline_with_echo", mock_readline_with_echo)
    await gs.robot_shell(MockReader([]), MockWriter())
    assert call_count[0] == timeout_at


@pytest.mark.asyncio
async def test_busy_shell_timeout(monkeypatch):
    call_count = [0]

    async def mock_read_line(reader, timeout, max_len=gs._MAX_INPUT):
        call_count[0] += 1
        return None if call_count[0] == 1 else "hi"

    monkeypatch.setattr(gs, "_read_line", mock_read_line)
    await gs.busy_shell(MockReader([]), MockWriter())
    assert call_count[0] == 2


@pytest.mark.asyncio
async def test_ask_question_blank_then_answer(monkeypatch):
    call_count = [0]

    async def mock_readline_with_echo(reader, writer, timeout):
        call_count[0] += 1
        return "   " if call_count[0] == 1 else "answer"

    monkeypatch.setattr(gs, "_readline_with_echo", mock_readline_with_echo)
    assert await gs._ask_question(None, MockWriter(), "q? ", timeout=5.0) == "answer"
    assert call_count[0] == 2


@pytest.mark.parametrize(
    "never_send_ga,expect_ga",
    [
        pytest.param(False, True, id="ga_sent"),
        pytest.param(True, False, id="ga_suppressed"),
    ],
)
@pytest.mark.asyncio
async def test_telnet_server_shell_ga(never_send_ga, expect_ga):
    reader = MockReader(list("quit\r"))
    writer = MockWriter(protocol=_MockProtocol(never_send_ga=never_send_ga))
    await ss.telnet_server_shell(reader, writer)
    assert (len(writer.ga_calls) >= 1) == expect_ga


@pytest.mark.asyncio
async def test_telnet_server_shell_dump_with_delay(monkeypatch):
    slept = []
    _real_sleep = asyncio.sleep
    monkeypatch.setattr(asyncio, "sleep", lambda d: slept.append(d) or _real_sleep(0))
    reader = MockReader(list("dump 0 1000\r") + list("quit\r"))
    writer = MockWriter()
    await ss.telnet_server_shell(reader, writer)
    written = "".join(writer.written)
    assert "kb_limit=0" in written
    assert "delay=1" in written


@pytest.mark.asyncio
async def test_telnet_server_shell_dump_with_explicit_kb():
    writer = MockWriter()
    await ss.telnet_server_shell(MockReader(list("dump 0\r") + list("quit\r")), writer)
    written = "".join(writer.written)
    assert "kb_limit=0" in written
    assert "wrote 0 bytes" in written


@pytest.mark.asyncio
async def test_telnet_server_shell_dump_closing():
    class _ClosingWriter(MockWriter):
        def write(self, data):
            super().write(data)
            if "kb_limit=" in data:
                self._closing = True

    w1 = _ClosingWriter()
    await ss.telnet_server_shell(MockReader(list("dump\r") + list("quit\r")), w1)
    assert "kb_limit=1000" in "".join(w1.written)

    w2 = _ClosingWriter()
    await ss.telnet_server_shell(MockReader(list("dump 1\r") + list("quit\r")), w2)
    assert "1 OK" not in "".join(w2.written)
