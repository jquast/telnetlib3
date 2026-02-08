"""Tests for telnetlib3.mud encoding and decoding."""

# 3rd party
import pytest

# local
from telnetlib3.mud import (
    zmp_decode,
    atcp_decode,
    gmcp_decode,
    gmcp_encode,
    msdp_decode,
    msdp_encode,
    mssp_decode,
    mssp_encode,
    aardwolf_decode,
)
from telnetlib3.telopt import (
    MSDP_VAL,
    MSDP_VAR,
    MSSP_VAL,
    MSSP_VAR,
    MSDP_ARRAY_OPEN,
    MSDP_TABLE_OPEN,
    MSDP_ARRAY_CLOSE,
    MSDP_TABLE_CLOSE,
)


def test_gmcp_roundtrip() -> None:
    """Encode and decode GMCP with nested data."""
    package = "Char.Vitals"
    data = {"hp": 100, "maxhp": 120, "mp": 50}
    encoded = gmcp_encode(package, data)
    decoded_pkg, decoded_data = gmcp_decode(encoded)
    assert decoded_pkg == package
    assert decoded_data == data


def test_gmcp_package_only() -> None:
    """Encode and decode GMCP package without data."""
    package = "Core.Hello"
    encoded = gmcp_encode(package)
    assert encoded == b"Core.Hello"
    decoded_pkg, decoded_data = gmcp_decode(encoded)
    assert decoded_pkg == package
    assert decoded_data is None


def test_gmcp_nested_json() -> None:
    """Encode and decode GMCP with nested structures."""
    package = "Room.Info"
    data = {
        "name": "The Inn",
        "exits": ["north", "south"],
        "items": [{"name": "sword", "id": 123}, {"name": "shield", "id": 456}],
    }
    encoded = gmcp_encode(package, data)
    decoded_pkg, decoded_data = gmcp_decode(encoded)
    assert decoded_pkg == package
    assert decoded_data == data


def test_gmcp_decode_encoding_param() -> None:
    """gmcp_decode uses the encoding parameter for decoding."""
    decoded_pkg, decoded_data = gmcp_decode(b"Caf\xe9", encoding="latin-1")
    assert decoded_pkg == "Caf\xe9"
    assert decoded_data is None


def test_gmcp_decode_latin1_fallback() -> None:
    """gmcp_decode falls back to latin-1 when utf-8 fails."""
    decoded_pkg, decoded_data = gmcp_decode(b"Caf\xe9")
    assert decoded_pkg == "Caf\xe9"
    assert decoded_data is None


def test_gmcp_decode_invalid_json() -> None:
    """Decode GMCP with invalid JSON raises ValueError."""
    with pytest.raises(ValueError):
        gmcp_decode(b"Package {bad json}")


def test_msdp_simple() -> None:
    """Encode and decode simple MSDP variable."""
    variables = {"FOO": "bar"}
    encoded = msdp_encode(variables)
    expected = MSDP_VAR + b"FOO" + MSDP_VAL + b"bar"
    assert encoded == expected
    decoded = msdp_decode(encoded)
    assert decoded == variables


def test_msdp_multiple() -> None:
    """Encode and decode multiple MSDP variables."""
    variables = {"A": "1", "B": "2"}
    encoded = msdp_encode(variables)
    decoded = msdp_decode(encoded)
    assert decoded == variables


def test_msdp_nested_table() -> None:
    """Encode and decode MSDP with nested table."""
    variables = {"ROOM": {"NAME": "Inn", "EXITS": "north,south"}}
    encoded = msdp_encode(variables)
    assert MSDP_TABLE_OPEN in encoded
    assert MSDP_TABLE_CLOSE in encoded
    decoded = msdp_decode(encoded)
    assert decoded == variables


def test_msdp_array() -> None:
    """Encode and decode MSDP with array."""
    variables = {"LIST": ["a", "b", "c"]}
    encoded = msdp_encode(variables)
    assert MSDP_ARRAY_OPEN in encoded
    assert MSDP_ARRAY_CLOSE in encoded
    decoded = msdp_decode(encoded)
    assert decoded == variables


def test_msdp_mixed() -> None:
    """Encode and decode MSDP with mixed value types."""
    variables = {
        "NAME": "Player",
        "STATS": {"HP": "100", "MP": "50"},
        "SKILLS": ["sword", "shield", "magic"],
    }
    encoded = msdp_encode(variables)
    decoded = msdp_decode(encoded)
    assert decoded == variables


def test_msdp_empty_value() -> None:
    """Encode and decode MSDP with empty string value."""
    variables = {"KEY": ""}
    encoded = msdp_encode(variables)
    decoded = msdp_decode(encoded)
    assert decoded == variables


def test_msdp_decode_encoding_param() -> None:
    """msdp_decode uses the encoding parameter for decoding."""
    encoded = MSDP_VAR + b"KEY" + MSDP_VAL + b"Caf\xe9"
    decoded = msdp_decode(encoded, encoding="latin-1")
    assert decoded == {"KEY": "Caf\xe9"}


def test_msdp_decode_latin1_fallback() -> None:
    """msdp_decode falls back to latin-1 when utf-8 fails."""
    encoded = MSDP_VAR + b"KEY" + MSDP_VAL + b"Caf\xe9"
    decoded = msdp_decode(encoded)
    assert decoded == {"KEY": "Caf\xe9"}


def test_mssp_single_value() -> None:
    """Encode and decode MSSP with single values."""
    variables = {"NAME": "TestMUD", "UPTIME": "12345"}
    encoded = mssp_encode(variables)
    decoded = mssp_decode(encoded)
    assert decoded == variables


def test_mssp_multi_value() -> None:
    """Encode and decode MSSP with multi-value field."""
    variables = {"PORT": ["6023", "6024", "6025"]}
    encoded = mssp_encode(variables)
    decoded = mssp_decode(encoded)
    assert decoded == variables


def test_mssp_roundtrip() -> None:
    """Full roundtrip with mixed single and multi values."""
    variables = {
        "NAME": "TestMUD",
        "PORT": ["6023", "6024"],
        "CODEBASE": "Custom",
        "CONTACT": "admin@test.mud",
    }
    encoded = mssp_encode(variables)
    decoded = mssp_decode(encoded)
    assert decoded == variables


def test_mssp_decode_multi_returns_list() -> None:
    """Verify single value returns str, multiple values return list."""
    encoded = (
        MSSP_VAR
        + b"SINGLE"
        + MSSP_VAL
        + b"one"
        + MSSP_VAR
        + b"MULTI"
        + MSSP_VAL
        + b"first"
        + MSSP_VAL
        + b"second"
    )
    decoded = mssp_decode(encoded)
    assert decoded["SINGLE"] == "one"
    assert decoded["MULTI"] == ["first", "second"]


def test_mssp_decode_encoding_param() -> None:
    """mssp_decode uses the encoding parameter for decoding."""
    encoded = MSSP_VAR + b"NAME" + MSSP_VAL + b"\xc9toile"
    decoded = mssp_decode(encoded, encoding="latin-1")
    assert decoded == {"NAME": "\xc9toile"}


def test_mssp_decode_latin1_fallback() -> None:
    """mssp_decode falls back to latin-1 when utf-8 fails."""
    encoded = MSSP_VAR + b"NAME" + MSSP_VAL + b"\xc9toile"
    decoded = mssp_decode(encoded)
    assert decoded == {"NAME": "\xc9toile"}


def test_zmp_decode_ident() -> None:
    result = zmp_decode(b"zmp.ident\x00MudName\x001.0\x00A test MUD\x00")
    assert result == ["zmp.ident", "MudName", "1.0", "A test MUD"]


def test_zmp_decode_single_command() -> None:
    result = zmp_decode(b"zmp.ping\x00")
    assert result == ["zmp.ping"]


def test_zmp_decode_no_trailing_nul() -> None:
    result = zmp_decode(b"zmp.check\x00zmp.ping")
    assert result == ["zmp.check", "zmp.ping"]


def test_zmp_decode_empty() -> None:
    assert zmp_decode(b"") == []


def test_zmp_decode_latin1_fallback() -> None:
    result = zmp_decode(b"zmp.ident\x00Caf\xe9\x00")
    assert result == ["zmp.ident", "Caf\xe9"]


def test_atcp_decode_with_value() -> None:
    package, value = atcp_decode(b"Room.Exits ne,sw,nw")
    assert package == "Room.Exits"
    assert value == "ne,sw,nw"


def test_atcp_decode_no_value() -> None:
    package, value = atcp_decode(b"Conn.MXP")
    assert package == "Conn.MXP"
    assert value == ""


def test_atcp_decode_value_with_spaces() -> None:
    package, value = atcp_decode(b"Room.Name The Grand Hall")
    assert package == "Room.Name"
    assert value == "The Grand Hall"


def test_atcp_decode_empty() -> None:
    package, value = atcp_decode(b"")
    assert package == ""
    assert value == ""


def test_atcp_decode_latin1_fallback() -> None:
    package, value = atcp_decode(b"Room.Name Caf\xe9")
    assert package == "Room.Name"
    assert value == "Caf\xe9"


def test_aardwolf_decode_status() -> None:
    result = aardwolf_decode(bytes([100, 3]))
    assert result["channel"] == "status"
    assert result["channel_byte"] == 100
    assert result["data_byte"] == 3


def test_aardwolf_decode_tick() -> None:
    result = aardwolf_decode(bytes([101, 1]))
    assert result["channel"] == "tick"
    assert result["data_byte"] == 1


def test_aardwolf_decode_unknown_channel() -> None:
    result = aardwolf_decode(bytes([200, 5]))
    assert result["channel"] == "0xc8"
    assert result["channel_byte"] == 200
    assert result["data_byte"] == 5


def test_aardwolf_decode_empty() -> None:
    result = aardwolf_decode(b"")
    assert result["channel"] == "unknown"
    assert result["channel_byte"] == 0


def test_aardwolf_decode_single_byte() -> None:
    result = aardwolf_decode(bytes([102]))
    assert result["channel"] == "affect"
    assert "data_byte" not in result


def test_aardwolf_decode_long_payload() -> None:
    result = aardwolf_decode(bytes([100, 3, 4, 5]))
    assert result["channel"] == "status"
    assert result["data_bytes"] == bytes([3, 4, 5])
