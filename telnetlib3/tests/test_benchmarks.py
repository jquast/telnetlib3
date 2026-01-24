"""Benchmarks for telnetlib3."""

# local
from telnetlib3.slc import (
    BSD_SLC_TAB,
    SLC,
    SLC_DEFAULT,
    SLC_IP,
    SLC_VARIABLE,
    Forwardmask,
    Linemode,
    generate_forwardmask,
    generate_slctab,
    name_slc_command,
    snoop,
)
from telnetlib3.telopt import name_command, name_commands


def test_snoop_match(benchmark):
    """Benchmark snoop() with matching SLC character."""
    slctab = generate_slctab()
    benchmark(snoop, b"\x03", slctab, {})


def test_snoop_no_match(benchmark):
    """Benchmark snoop() with non-matching character."""
    slctab = generate_slctab()
    benchmark(snoop, b"A", slctab, {})


def test_generate_slctab(benchmark):
    """Benchmark generate_slctab()."""
    benchmark(generate_slctab)


def test_generate_slctab_with_tabset(benchmark):
    """Benchmark generate_slctab() with BSD tabset."""
    benchmark(generate_slctab, BSD_SLC_TAB)


def test_generate_forwardmask_binary(benchmark):
    """Benchmark generate_forwardmask() in binary mode."""
    benchmark(generate_forwardmask, True, BSD_SLC_TAB)


def test_generate_forwardmask_ascii(benchmark):
    """Benchmark generate_forwardmask() in ASCII mode."""
    benchmark(generate_forwardmask, False, BSD_SLC_TAB)


def test_slc_level_property(benchmark):
    """Benchmark SLC.level property access."""
    slc = SLC(SLC_VARIABLE, b"\x03")
    benchmark(lambda: slc.level)


def test_slc_nosupport_property(benchmark):
    """Benchmark SLC.nosupport property access."""
    slc = SLC(SLC_DEFAULT, b"\x00")
    benchmark(lambda: slc.nosupport)


def test_linemode_local_property(benchmark):
    """Benchmark Linemode.local property."""
    lm = Linemode(b"\x02")
    benchmark(lambda: lm.local)


def test_forwardmask_contains(benchmark):
    """Benchmark Forwardmask.__contains__()."""
    fm = generate_forwardmask(False, BSD_SLC_TAB)
    benchmark(lambda: 3 in fm)


def test_name_command_known(benchmark):
    """Benchmark name_command() with known option."""
    benchmark(name_command, b"\xff")


def test_name_command_unknown(benchmark):
    """Benchmark name_command() with unknown option."""
    benchmark(name_command, b"\x99")


def test_name_commands(benchmark):
    """Benchmark name_commands() with multiple bytes."""
    benchmark(name_commands, b"\xff\xfb\x18")


def test_name_slc_command(benchmark):
    """Benchmark name_slc_command()."""
    benchmark(name_slc_command, SLC_IP)
