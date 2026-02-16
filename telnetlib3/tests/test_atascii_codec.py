"""Tests for the ATASCII (Atari 8-bit) codec."""

# std imports
import codecs

# 3rd party
import pytest

# local
import telnetlib3  # noqa: F401
from telnetlib3.encodings import atascii


def test_codec_lookup():
    info = codecs.lookup("atascii")
    assert info.name == "atascii"


@pytest.mark.parametrize("alias", ["atari8bit", "atari_8bit"])
def test_codec_aliases(alias):
    info = codecs.lookup(alias)
    assert info.name == "atascii"


def test_ascii_letters_uppercase():
    data = bytes(range(0x41, 0x5B))
    assert data.decode("atascii") == "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def test_ascii_letters_lowercase():
    data = bytes(range(0x61, 0x7B))
    assert data.decode("atascii") == "abcdefghijklmnopqrstuvwxyz"


def test_digits():
    data = bytes(range(0x30, 0x3A))
    assert data.decode("atascii") == "0123456789"


def test_space():
    assert b"\x20".decode("atascii") == " "


@pytest.mark.parametrize(
    "byte_val,expected",
    [
        pytest.param(0x00, "\u2665", id="heart"),
        pytest.param(0x01, "\u251c", id="box_vert_right"),
        pytest.param(0x08, "\u25e2", id="lower_right_triangle"),
        pytest.param(0x09, "\u2597", id="quadrant_lower_right"),
        pytest.param(0x10, "\u2663", id="club"),
        pytest.param(0x12, "\u2500", id="horizontal_line"),
        pytest.param(0x14, "\u25cf", id="black_circle"),
        pytest.param(0x15, "\u2584", id="lower_half_block"),
        pytest.param(0x19, "\u258c", id="left_half_block"),
        pytest.param(0x1B, "\u241b", id="symbol_for_escape"),
    ],
)
def test_graphics_chars(byte_val, expected):
    assert bytes([byte_val]).decode("atascii") == expected


@pytest.mark.parametrize(
    "byte_val,expected",
    [
        pytest.param(0x1C, "\u2191", id="cursor_up"),
        pytest.param(0x1D, "\u2193", id="cursor_down"),
        pytest.param(0x1E, "\u2190", id="cursor_left"),
        pytest.param(0x1F, "\u2192", id="cursor_right"),
    ],
)
def test_cursor_arrows(byte_val, expected):
    assert bytes([byte_val]).decode("atascii") == expected


@pytest.mark.parametrize(
    "byte_val,expected",
    [
        pytest.param(0x60, "\u2666", id="diamond"),
        pytest.param(0x7B, "\u2660", id="spade"),
        pytest.param(0x7C, "|", id="pipe"),
        pytest.param(0x7D, "\u21b0", id="clear_screen"),
        pytest.param(0x7E, "\u25c0", id="backspace_triangle"),
        pytest.param(0x7F, "\u25b6", id="tab_triangle"),
    ],
)
def test_special_glyphs(byte_val, expected):
    assert bytes([byte_val]).decode("atascii") == expected


def test_atascii_eol():
    assert b"\x9b".decode("atascii") == "\n"


@pytest.mark.parametrize(
    "byte_val,expected",
    [
        pytest.param(0x82, "\u258a", id="left_three_quarters"),
        pytest.param(0x88, "\u25e4", id="upper_left_triangle"),
        pytest.param(0x89, "\u259b", id="quadrant_UL_UR_LL"),
        pytest.param(0x8A, "\u25e5", id="upper_right_triangle"),
        pytest.param(0x8B, "\u2599", id="quadrant_UL_LL_LR"),
        pytest.param(0x8C, "\u259f", id="quadrant_UR_LL_LR"),
        pytest.param(0x8D, "\u2586", id="lower_three_quarters"),
        pytest.param(0x8E, "\U0001fb85", id="upper_three_quarters"),
        pytest.param(0x8F, "\u259c", id="quadrant_UL_UR_LR"),
        pytest.param(0x94, "\u25d8", id="inverse_bullet"),
        pytest.param(0x95, "\u2580", id="upper_half_block"),
        pytest.param(0x96, "\U0001fb8a", id="right_three_quarters"),
        pytest.param(0x99, "\u2590", id="right_half_block"),
        pytest.param(0xA0, "\u2588", id="full_block"),
    ],
)
def test_inverse_distinct_glyphs(byte_val, expected):
    assert bytes([byte_val]).decode("atascii") == expected


def test_inverse_shared_glyphs():
    for byte_val in (0x80, 0x81, 0x83, 0x84, 0x85, 0x86, 0x87):
        normal = bytes([byte_val & 0x7F]).decode("atascii")
        inverse = bytes([byte_val]).decode("atascii")
        assert inverse == normal


def test_inverse_ascii_range():
    for byte_val in range(0xA1, 0xFB):
        normal = bytes([byte_val & 0x7F]).decode("atascii")
        inverse = bytes([byte_val]).decode("atascii")
        assert inverse == normal


def test_full_decode_no_crash():
    data = bytes(range(256))
    result = data.decode("atascii")
    assert len(result) == 256


def test_encode_eol():
    encoded, length = codecs.lookup("atascii").encode("\n")
    assert encoded == b"\x9b"
    assert length == 1


def test_encode_unique_chars():
    encoded, _ = codecs.lookup("atascii").encode("\u258a")
    assert encoded == b"\x82"
    encoded, _ = codecs.lookup("atascii").encode("\u25d8")
    assert encoded == b"\x94"
    encoded, _ = codecs.lookup("atascii").encode("\u2588")
    assert encoded == b"\xa0"


def test_encode_charmap_prefers_normal_byte():
    encoded, _ = codecs.lookup("atascii").encode("\u2665")
    assert encoded == b"\x00"
    encoded, _ = codecs.lookup("atascii").encode("A")
    assert encoded == b"\x41"


def test_incremental_decoder():
    decoder = codecs.getincrementaldecoder("atascii")()
    assert decoder.decode(b"\x00", False) == "\u2665"
    assert decoder.decode(b"AB", True) == "AB"


def test_incremental_encoder():
    encoder = codecs.getincrementalencoder("atascii")()
    assert encoder.encode("\u258a", False) == b"\x82"
    assert encoder.encode("\n", True) == b"\x9b"


def test_strict_error_on_unencodable():
    with pytest.raises(UnicodeEncodeError):
        "\u00e9".encode("atascii")


def test_replace_error_mode():
    result = "\u00e9".encode("atascii", errors="replace")
    assert result == b"\x3f"


def test_ignore_error_mode():
    result = "\u00e9".encode("atascii", errors="ignore")
    assert result == b""


def test_encode_cr_as_eol():
    encoded, length = codecs.lookup("atascii").encode("\r")
    assert encoded == b"\x9b"
    assert length == 1


def test_encode_crlf_as_single_eol():
    encoded, length = codecs.lookup("atascii").encode("\r\n")
    assert encoded == b"\x9b"
    assert length == 1


def test_encode_mixed_line_endings():
    encoded, _ = codecs.lookup("atascii").encode("hello\r\nworld\r")
    hello_eol = "hello\n".encode("atascii")
    world_eol = "world\n".encode("atascii")
    assert encoded == hello_eol + world_eol


def test_incremental_encoder_cr_then_lf():
    encoder = codecs.getincrementalencoder("atascii")()
    result = encoder.encode("hello\r", final=False)
    assert result == "hello".encode("atascii")
    result = encoder.encode("\nworld", final=True)
    assert result == "\nworld".encode("atascii")


def test_incremental_encoder_cr_then_other():
    encoder = codecs.getincrementalencoder("atascii")()
    result = encoder.encode("A\r", final=False)
    assert result == "A".encode("atascii")
    result = encoder.encode("B", final=True)
    assert result == "\nB".encode("atascii")


def test_incremental_encoder_cr_final():
    encoder = codecs.getincrementalencoder("atascii")()
    result = encoder.encode("end\r", final=True)
    assert result == "end\n".encode("atascii")


def test_incremental_encoder_reset():
    encoder = codecs.getincrementalencoder("atascii")()
    encoder.encode("A\r", final=False)
    encoder.reset()
    assert encoder.getstate() == 0


def test_incremental_encoder_getstate_setstate():
    encoder = codecs.getincrementalencoder("atascii")()
    encoder.encode("A\r", final=False)
    assert encoder.getstate() == 1
    state = encoder.getstate()
    encoder2 = codecs.getincrementalencoder("atascii")()
    encoder2.setstate(state)
    result = encoder2.encode("\n", final=True)
    assert result == b"\x9b"


def test_native_graphics_0x0a_0x0d():
    assert b"\x0a".decode("atascii") == "\u25e3"
    assert b"\x0d".decode("atascii") == "\U0001fb82"


def test_decoding_table_length():
    assert len(atascii.DECODING_TABLE) == 256
