"""Tests for telnetlib3.client_shell — Terminal mode handling."""

# std imports
import sys
import types

# 3rd party
import pytest

if sys.platform == "win32":
    pytest.skip("POSIX-only tests", allow_module_level=True)

# std imports
# std imports (POSIX only)
import termios  # noqa: E402

# local
from telnetlib3.client_shell import (  # noqa: E402
    _INPUT_XLAT,
    _INPUT_SEQ_XLAT,
    Terminal,
    InputFilter,
)


def _make_writer(will_echo: bool = False, raw_mode: bool = False) -> object:
    """Build a minimal mock writer with the attributes Terminal needs."""
    writer = types.SimpleNamespace(
        will_echo=will_echo,
        log=types.SimpleNamespace(debug=lambda *a, **kw: None),
    )
    if raw_mode:
        writer._raw_mode = True
    return writer


def _cooked_mode() -> "Terminal.ModeDef":
    """Return a typical cooked-mode ModeDef with canonical input enabled."""
    return Terminal.ModeDef(
        iflag=termios.BRKINT | termios.ICRNL | termios.IXON,
        oflag=termios.OPOST | termios.ONLCR,
        cflag=termios.CS8 | termios.CREAD,
        lflag=termios.ICANON | termios.ECHO | termios.ISIG | termios.IEXTEN,
        ispeed=termios.B38400,
        ospeed=termios.B38400,
        cc=[b'\x00'] * termios.NCCS,
    )


class TestDetermineMode:
    def test_linemode_when_no_echo_no_raw(self) -> None:
        writer = _make_writer(will_echo=False, raw_mode=False)
        term = Terminal.__new__(Terminal)
        term.telnet_writer = writer
        mode = _cooked_mode()
        result = term.determine_mode(mode)
        assert result is mode

    def test_raw_mode_when_will_echo(self) -> None:
        writer = _make_writer(will_echo=True, raw_mode=False)
        term = Terminal.__new__(Terminal)
        term.telnet_writer = writer
        mode = _cooked_mode()
        result = term.determine_mode(mode)
        assert result is not mode
        assert not (result.lflag & termios.ICANON)
        assert not (result.lflag & termios.ECHO)

    def test_raw_mode_when_force_raw(self) -> None:
        writer = _make_writer(will_echo=False, raw_mode=True)
        term = Terminal.__new__(Terminal)
        term.telnet_writer = writer
        mode = _cooked_mode()
        result = term.determine_mode(mode)
        assert result is not mode
        assert not (result.lflag & termios.ICANON)
        assert not (result.lflag & termios.ECHO)
        assert not (result.oflag & termios.OPOST)

    def test_raw_mode_when_both_echo_and_raw(self) -> None:
        writer = _make_writer(will_echo=True, raw_mode=True)
        term = Terminal.__new__(Terminal)
        term.telnet_writer = writer
        mode = _cooked_mode()
        result = term.determine_mode(mode)
        assert result is not mode
        assert not (result.lflag & termios.ICANON)
        assert not (result.lflag & termios.ECHO)


class TestInputXlat:
    def test_atascii_del_to_backspace(self) -> None:
        assert _INPUT_XLAT["atascii"][0x7F] == 0x7E

    def test_atascii_bs_to_backspace(self) -> None:
        assert _INPUT_XLAT["atascii"][0x08] == 0x7E

    def test_atascii_cr_to_eol(self) -> None:
        assert _INPUT_XLAT["atascii"][0x0D] == 0x9B

    def test_atascii_lf_to_eol(self) -> None:
        assert _INPUT_XLAT["atascii"][0x0A] == 0x9B

    def test_petscii_del_to_backspace(self) -> None:
        assert _INPUT_XLAT["petscii"][0x7F] == 0x14

    def test_petscii_bs_to_backspace(self) -> None:
        assert _INPUT_XLAT["petscii"][0x08] == 0x14

    def test_normal_bytes_not_in_xlat(self) -> None:
        xlat = _INPUT_XLAT["atascii"]
        for b in (ord('a'), ord('A'), ord('1'), ord(' ')):
            assert b not in xlat


class TestInputFilterAtascii:
    @staticmethod
    def _make_filter() -> InputFilter:
        return InputFilter(
            _INPUT_SEQ_XLAT["atascii"], _INPUT_XLAT["atascii"]
        )

    @pytest.mark.parametrize("seq,expected", list(_INPUT_SEQ_XLAT["atascii"].items()))
    def test_sequence_translated(self, seq: bytes, expected: bytes) -> None:
        f = self._make_filter()
        assert f.feed(seq) == expected

    def test_passthrough_ascii(self) -> None:
        f = self._make_filter()
        assert f.feed(b"hello") == b"hello"

    def test_mixed_text_and_sequence(self) -> None:
        f = self._make_filter()
        assert f.feed(b"hi\x1b[Alo") == b"hi\x1clo"

    def test_multiple_sequences(self) -> None:
        f = self._make_filter()
        assert f.feed(b"\x1b[A\x1b[B\x1b[C\x1b[D") == b"\x1c\x1d\x1f\x1e"

    def test_single_byte_xlat_applied(self) -> None:
        f = self._make_filter()
        assert f.feed(b"\x7f") == b"\x7e"
        assert f.feed(b"\x08") == b"\x7e"

    def test_cr_to_atascii_eol(self) -> None:
        f = self._make_filter()
        assert f.feed(b"\r") == b"\x9b"

    def test_lf_to_atascii_eol(self) -> None:
        f = self._make_filter()
        assert f.feed(b"\n") == b"\x9b"

    def test_text_with_enter(self) -> None:
        f = self._make_filter()
        assert f.feed(b"hello\r") == b"hello\x9b"

    def test_split_sequence_buffered(self) -> None:
        f = self._make_filter()
        assert f.feed(b"\x1b") == b""
        assert f.feed(b"[A") == b"\x1c"

    def test_split_sequence_mid_csi(self) -> None:
        f = self._make_filter()
        assert f.feed(b"\x1b[") == b""
        assert f.feed(b"A") == b"\x1c"

    def test_bare_esc_flushed_on_non_prefix(self) -> None:
        f = self._make_filter()
        assert f.feed(b"\x1b") == b""
        assert f.feed(b"x") == b"\x1bx"

    def test_delete_key_sequence(self) -> None:
        f = self._make_filter()
        assert f.feed(b"\x1b[3~") == b"\x7e"

    def test_ss3_arrow_keys(self) -> None:
        f = self._make_filter()
        assert f.feed(b"\x1bOA") == b"\x1c"
        assert f.feed(b"\x1bOD") == b"\x1e"


class TestInputFilterPetscii:
    @staticmethod
    def _make_filter() -> InputFilter:
        return InputFilter(
            _INPUT_SEQ_XLAT["petscii"], _INPUT_XLAT["petscii"]
        )

    @pytest.mark.parametrize("seq,expected", list(_INPUT_SEQ_XLAT["petscii"].items()))
    def test_sequence_translated(self, seq: bytes, expected: bytes) -> None:
        f = self._make_filter()
        assert f.feed(seq) == expected

    def test_home_key(self) -> None:
        f = self._make_filter()
        assert f.feed(b"\x1b[H") == b"\x13"

    def test_insert_key(self) -> None:
        f = self._make_filter()
        assert f.feed(b"\x1b[2~") == b"\x94"

    def test_single_byte_xlat_applied(self) -> None:
        f = self._make_filter()
        assert f.feed(b"\x7f") == b"\x14"


class TestInputFilterEmpty:
    def test_no_xlat_passthrough(self) -> None:
        f = InputFilter({}, {})
        assert f.feed(b"hello\x1b[Aworld") == b"hello\x1b[Aworld"

    def test_empty_feed(self) -> None:
        f = InputFilter({}, {})
        assert f.feed(b"") == b""
