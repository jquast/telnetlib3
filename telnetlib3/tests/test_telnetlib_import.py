"""Test telnetlib shim and telnetlib3.telnetlib submodule imports."""

# std imports
import sys
import sysconfig

# 3rd party
import pytest


@pytest.mark.parametrize(
    "name, expected_type",
    [
        ("Telnet", type),
        ("IAC", bytes),
        ("WILL", bytes),
        ("WONT", bytes),
        ("DO", bytes),
        ("DONT", bytes),
        ("SB", bytes),
        ("SE", bytes),
        ("TELNET_PORT", int),
    ],
)
def test_import_telnetlib_names(name, expected_type):
    """``import telnetlib`` provides expected names and types."""
    import telnetlib

    assert isinstance(getattr(telnetlib, name), expected_type)


def test_telnetlib_Telnet_instantiable():
    """``Telnet()`` from the shim is instantiable."""
    from telnetlib import Telnet

    tn = Telnet()
    assert tn is not None
    tn.close()


def test_import_telnetlib3_telnetlib():
    """``import telnetlib3.telnetlib`` continues to work."""
    import telnetlib3.telnetlib

    assert telnetlib3.telnetlib.IAC is not None


def test_from_telnetlib3_import():
    """``from telnetlib3 import Telnet`` continues to work."""
    from telnetlib3 import IAC, TELNET_PORT, Telnet

    assert IAC is not None
    assert TELNET_PORT == 23
    assert callable(Telnet)


def test_drop_in_support():
    """``import telnetlib`` uses stdlib when available, shim otherwise."""
    import telnetlib as telnetlib_

    telnetlib_in_stdlib = sys.version_info < (3, 13)
    telnetlib_is_stdlib = telnetlib_.__file__.startswith(sysconfig.get_path("stdlib"))
    assert telnetlib_is_stdlib == telnetlib_in_stdlib
