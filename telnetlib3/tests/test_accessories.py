# 3rd party
import pytest

# local
from telnetlib3.accessories import eightbits, name_unicode, encoding_from_lang


@pytest.mark.parametrize(
    "given,expected",
    sorted(
        {
            chr(0): r"^@",
            chr(1): r"^A",
            chr(26): r"^Z",
            chr(29): r"^]",
            chr(31): r"^_",
            chr(32): r" ",
            chr(126): r"~",
            chr(127): r"^?",
            chr(128): r"\x80",
            chr(254): r"\xfe",
            chr(255): r"\xff",
        }.items()
    ),
)
def test_name_unicode(given, expected):
    """Test mapping of ascii table to name_unicode result."""
    assert name_unicode(given) == expected


@pytest.mark.parametrize(
    "given,expected",
    sorted(
        {0: "0b00000000", 127: "0b01111111", 128: "0b10000000", 255: "0b11111111"}.items()
    ),
)
def test_eightbits(given, expected):
    """Test mapping of bit values to binary appearance string."""
    assert eightbits(given) == expected


@pytest.mark.parametrize(
    "given,expected",
    sorted(
        {
            "en_US.UTF-8@misc": "UTF-8",
            "en_US.UTF-8": "UTF-8",
            "abc.def": "def",
            ".def@ghi": "def",
        }.items()
    ),
)
def test_encoding_from_lang(given, expected):
    """Test inference of encoding from LANG value."""
    assert encoding_from_lang(given) == expected


@pytest.mark.parametrize(
    "given,expected",
    sorted(
        {"en_IL": None, "en_US": None, "C": None, "POSIX": None, "UTF-8": None}.items()
    ),
)
def test_encoding_from_lang_no_encoding(given, expected):
    """Test LANG values without encoding suffix return None."""
    assert encoding_from_lang(given) == expected
