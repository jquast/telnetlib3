import asyncio
import types
import sys

import pytest

from telnetlib3 import server_shell as ss
from telnetlib3 import slc as slc_mod
from telnetlib3 import client_shell as cs


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
    """
    Drive ss.readline coroutine with given sequence and return list of commands produced.
    """
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
        assert not (new_mode.iflag & flag)

    # verify output flags cleared
    assert not (new_mode.oflag & t.OPOST)
    if hasattr(t, "ONLCR"):
        assert not (new_mode.oflag & t.ONLCR)

    # verify cflag: PARENB cleared, CS8 set, CSIZE cleared except CS8
    assert not (new_mode.cflag & t.PARENB)
    assert new_mode.cflag & t.CS8
    # CSIZE mask bits should be exactly CS8 now
    assert (new_mode.cflag & t.CSIZE) == t.CS8
    # CREAD (if present) should remain unchanged
    if hasattr(t, "CREAD") and (cflag & t.CREAD):
        assert new_mode.cflag & t.CREAD

    # verify lflag cleared for ICANON, IEXTEN, ISIG, ECHO
    for flag in (t.ICANON, t.IEXTEN, t.ISIG, t.ECHO):
        assert not (new_mode.lflag & flag)

    # cc changes
    assert new_mode.cc[t.VMIN] == 1
    assert new_mode.cc[t.VTIME] == 0
