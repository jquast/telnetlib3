# std imports

# 3rd party
import pytest

# local
from telnetlib3.slc import Forwardmask


def test_forwardmask_description_table_nonzero_byte():
    value = b"\x00" * 31 + b"\x01"
    fm = Forwardmask(value, ack=False)
    lines = fm.description_table()
    assert any("[31]" in line for line in lines)
    assert any("0b" in line for line in lines)


def test_forwardmask_str_binary():
    value = b"\xff" + b"\x00" * 31
    fm = Forwardmask(value, ack=False)
    assert str(fm).startswith("0b")
    assert "1" in str(fm)


def test_forwardmask_contains():
    value = bytearray(32)
    value[0] = 0x80
    fm = Forwardmask(bytes(value), ack=False)
    assert 0 in fm
    assert 1 not in fm
