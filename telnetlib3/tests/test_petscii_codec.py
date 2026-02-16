"""Tests for the PETSCII (Commodore 64/128) codec."""

# std imports
import codecs

# 3rd party
import pytest

# local
import telnetlib3  # noqa: F401
from telnetlib3.encodings import petscii


def test_codec_lookup():
    info = codecs.lookup("petscii")
    assert info.name == "petscii"


@pytest.mark.parametrize("alias", ["cbm", "commodore", "c64", "c128"])
def test_codec_aliases(alias):
    info = codecs.lookup(alias)
    assert info.name == "petscii"


def test_digits():
    data = bytes(range(0x30, 0x3A))
    assert data.decode("petscii") == "0123456789"


def test_space():
    assert b"\x20".decode("petscii") == " "


def test_lowercase_at_41_5A():
    data = bytes(range(0x41, 0x5B))
    assert data.decode("petscii") == "abcdefghijklmnopqrstuvwxyz"


def test_uppercase_at_C1_DA():
    data = bytes(range(0xC1, 0xDB))
    assert data.decode("petscii") == "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def test_return():
    assert b"\x0d".decode("petscii") == "\r"


@pytest.mark.parametrize(
    "byte_val,expected",
    [
        pytest.param(0x5C, "\u00a3", id="pound_sign"),
        pytest.param(0x5E, "\u2191", id="up_arrow"),
        pytest.param(0x5F, "\u2190", id="left_arrow"),
        pytest.param(0x61, "\u2660", id="spade"),
        pytest.param(0x7E, "\u03c0", id="pi"),
        pytest.param(0x78, "\u2663", id="club"),
        pytest.param(0x7A, "\u2666", id="diamond"),
        pytest.param(0x73, "\u2665", id="heart"),
    ],
)
def test_graphics_chars(byte_val, expected):
    assert bytes([byte_val]).decode("petscii") == expected


def test_full_decode_no_crash():
    data = bytes(range(256))
    result = data.decode("petscii")
    assert len(result) == 256


def test_encode_lowercase():
    encoded, length = codecs.lookup("petscii").encode("hello")
    assert encoded == bytes([0x48, 0x45, 0x4C, 0x4C, 0x4F])
    assert length == 5


def test_encode_uppercase():
    encoded, length = codecs.lookup("petscii").encode("HELLO")
    assert encoded == b"\xc8\xc5\xcc\xcc\xcf"
    assert length == 5


def test_round_trip_digits():
    for byte_val in range(0x30, 0x3A):
        original = bytes([byte_val])
        decoded = original.decode("petscii")
        re_encoded = decoded.encode("petscii")
        assert re_encoded == original


def test_incremental_decoder():
    decoder = codecs.getincrementaldecoder("petscii")()
    assert decoder.decode(b"\xc1", False) == "A"
    assert decoder.decode(b"\x42\x43", True) == "bc"


def test_incremental_encoder():
    encoder = codecs.getincrementalencoder("petscii")()
    assert encoder.encode("A", False) == b"\xc1"
    assert encoder.encode("bc", True) == b"\x42\x43"


def test_decoding_table_length():
    assert len(petscii.DECODING_TABLE) == 256
