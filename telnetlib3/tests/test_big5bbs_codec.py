"""Tests for the Big5-BBS hybrid codec."""

# std imports
import codecs

# 3rd party
import pytest

# local
import telnetlib3  # noqa: F401


def test_codec_lookup():
    info = codecs.lookup("big5bbs")
    assert info.name == "big5bbs"


def test_codec_alias_hyphen():
    codecs.lookup("big5bbs")
    info = codecs.lookup("big5-bbs")
    assert info.name == "big5bbs"


def test_codec_alias_underscore():
    codecs.lookup("big5bbs")
    info = codecs.lookup("big5_bbs")
    assert info.name == "big5bbs"


def test_ascii_passthrough():
    data = b"Hello, World!\n"
    assert data.decode("big5bbs") == "Hello, World!\n"


def test_valid_big5_pair():
    # Encode a known CJK character to Big5 and verify round-trip
    char = "夢"
    big5_bytes = char.encode("big5")
    assert big5_bytes.decode("big5bbs") == char


def test_valid_big5_pair_in_context():
    # Inline Big5 pair between ASCII text
    char = "夢"
    big5_bytes = char.encode("big5")
    data = b"test" + big5_bytes + b"end"
    assert data.decode("big5bbs") == "test" + char + "end"


def test_lone_lead_0xa1_before_esc():
    # 0xA1 is a Big5 lead byte; ESC is not a valid second byte → CP437 fallback
    # CP437 0xA1 = í (LATIN SMALL LETTER I WITH ACUTE)
    data = bytes([0xA1, 0x1B, 0x5B, 0x33, 0x32, 0x6D])  # 0xA1 ESC[32m
    result = data.decode("big5bbs")
    assert result[0] == "\u00ed"  # í
    assert result[1:] == "\x1b[32m"


def test_lone_lead_0xb0_before_esc():
    # CP437 0xB0 = ░ (LIGHT SHADE, U+2591)
    data = bytes([0xB0, 0x1B])
    result = data.decode("big5bbs")
    assert result == "\u2591\x1b"


def test_lone_lead_0xb6_before_esc():
    # CP437 0xB6 = ╢ (BOX DRAWINGS LIGHT VERTICAL AND LEFT, U+2562)
    data = bytes([0xB6, 0x1B])
    result = data.decode("big5bbs")
    assert result == "\u2562\x1b"


@pytest.mark.parametrize(
    "byte_val,expected_unicode",
    [
        (0xA1, "\u00ed"),  # í
        (0xA2, "\u00f3"),  # ó
        (0xA8, "\u00bf"),  # ¿
        (0xA9, "\u2310"),  # ⌐
        (0xAA, "\u00ac"),  # ¬
        (0xAB, "\u00bd"),  # ½
        (0xB0, "\u2591"),  # ░
        (0xB6, "\u2562"),  # ╢
        (0xBF, "\u2510"),  # ┐
        (0xC3, "\u251c"),  # ├
        (0xC6, "\u255e"),  # ╞
        (0xC7, "\u255f"),  # ╟
        (0xCA, "\u2569"),  # ╩
        (0xD1, "\u2564"),  # ╤
        (0xEE, "\u03b5"),  # ε
        (0xEF, "\u2229"),  # ∩
    ],
)
def test_lone_lead_bytes_cp437_fallback(byte_val, expected_unicode):
    # Each lone lead byte followed by ESC (invalid second byte) falls back to CP437
    data = bytes([byte_val, 0x1B])
    result = data.decode("big5bbs")
    assert result[0] == expected_unicode
    assert result[1] == "\x1b"


def test_split_across_chunks_big5_pair():
    # Lead byte in chunk 1, second byte in chunk 2 → valid Big5 pair
    char = "夢"
    big5_bytes = char.encode("big5")
    assert len(big5_bytes) == 2
    decoder = codecs.getincrementaldecoder("big5bbs")()
    result1 = decoder.decode(big5_bytes[:1], final=False)
    assert result1 == ""  # buffered
    result2 = decoder.decode(big5_bytes[1:], final=False)
    assert result2 == char


def test_split_lead_byte_final_true():
    # Lead byte at end of stream with final=True → CP437 fallback
    decoder = codecs.getincrementaldecoder("big5bbs")()
    result = decoder.decode(bytes([0xB0]), final=True)
    assert result == "\u2591"  # ░


def test_split_lead_byte_not_final():
    # Lead byte with final=False → buffered, returns empty string
    decoder = codecs.getincrementaldecoder("big5bbs")()
    result = decoder.decode(bytes([0xB0]), final=False)
    assert result == ""


def test_round_trip_big5_text():
    text = "夢想台灣"
    encoded = text.encode("big5bbs")
    decoded = encoded.decode("big5bbs")
    assert decoded == text


def test_getstate_setstate_preserves_pending_byte():
    decoder = codecs.getincrementaldecoder("big5bbs")()
    char = "夢"
    big5_bytes = char.encode("big5")
    decoder.decode(big5_bytes[:1], final=False)
    state = decoder.getstate()
    assert state[0] == big5_bytes[:1]

    decoder2 = codecs.getincrementaldecoder("big5bbs")()
    decoder2.setstate(state)
    result = decoder2.decode(big5_bytes[1:], final=True)
    assert result == char


def test_reset_clears_pending_byte():
    decoder = codecs.getincrementaldecoder("big5bbs")()
    char = "夢"
    big5_bytes = char.encode("big5")
    decoder.decode(big5_bytes[:1], final=False)
    decoder.reset()
    state = decoder.getstate()
    assert state[0] == b""


def test_mixed_stream():
    # Simulate a BBS art stream: Chinese text + lone art bytes + ANSI escape
    char = "夢"
    big5_bytes = char.encode("big5")
    data = big5_bytes + bytes([0xB0, 0x1B, 0x5B, 0x33, 0x32, 0x6D]) + b"text"
    result = data.decode("big5bbs")
    assert result == char + "\u2591\x1b[32mtext"


def test_incremental_encoder_ascii():
    encoder = codecs.getincrementalencoder("big5bbs")()
    assert encoder.encode("Hello") == b"Hello"


def test_incremental_encoder_big5():
    encoder = codecs.getincrementalencoder("big5bbs")()
    char = "夢"
    assert encoder.encode(char) == char.encode("big5")


def test_incremental_encoder_getstate():
    encoder = codecs.getincrementalencoder("big5bbs")()
    assert encoder.getstate() == 0
