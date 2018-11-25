"""Accessory functions."""
# std imports
import pkg_resources
import importlib
import logging
import asyncio

__all__ = ('encoding_from_lang', 'name_unicode', 'eightbits', 'make_logger',
           'repr_mapping', 'function_lookup', 'make_reader_task')


def get_version():
    return pkg_resources.get_distribution("telnetlib3").version


def encoding_from_lang(lang):
    """
    Parse encoding from LANG environment value.

    Example::

        >>> encoding_from_lang('en_US.UTF-8@misc')
        'UTF-8'
    """
    encoding = lang
    if '.' in lang:
        _, encoding = lang.split('.', 1)
    if '@' in encoding:
        encoding, _ = encoding.split('@', 1)
    return encoding


def name_unicode(ucs):
    """Return 7-bit ascii printable of any string. """
    # more or less the same as curses.ascii.unctrl -- but curses
    # module is conditionally excluded from many python distributions!
    bits = ord(ucs)
    if 32 <= bits <= 126:
        # ascii printable as one cell, as-is
        rep = chr(bits)
    elif bits == 127:
        rep = "^?"
    elif bits < 32:
        rep = "^" + chr(((bits & 0x7f) | 0x20) + 0x20)
    else:
        rep = r'\x{:02x}'.format(bits)
    return rep


def eightbits(number):
    """
    Binary representation of ``number`` padded to 8 bits.

    Example::

        >>> eightbits(ord('a'))
        '0b01100001'
    """
    # useful only so far in context of a forwardmask or any bitmask.
    prefix, value = bin(number).split('b')
    return '0b%0.8i' % (int(value),)

_DEFAULT_LOGFMT = ' '.join(('%(asctime)s',
                            '%(levelname)s',
                            '%(filename)s:%(lineno)d',
                            '%(message)s'))
def make_logger(name, loglevel='info', logfile=None, logfmt=_DEFAULT_LOGFMT):
    """Create and return simple logger for given arguments."""
    lvl = getattr(logging, loglevel.upper())
    logging.getLogger().setLevel(lvl)

    _cfg = {'format': logfmt}
    if logfile:
        _cfg['filename'] = logfile
    logging.basicConfig(**_cfg)
    return logging.getLogger(name)

def repr_mapping(mapping):
    """Return printable string, 'key=value [key=value ...]' for mapping."""
    return ' '.join('='.join(map(str, kv)) for kv in mapping.items())

def function_lookup(pymod_path):
    """Return callable function target from standard module.function path."""
    module_name, func_name = pymod_path.rsplit('.', 1)
    module = importlib.import_module(module_name)
    shell_function = getattr(module, func_name)
    assert callable(shell_function), shell_function
    return shell_function

def make_reader_task(reader, size=2**12):
    """Return asyncio task wrapping coroutine of reader.read(size)."""
    return asyncio.ensure_future(reader.read(size))
