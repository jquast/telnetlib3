"""Accessory functions."""

__all__ = ('name_unicode', 'eightbits', 'make_logger', 'get_encoding')


def name_unicode(ucs):
    """ Return 7-bit ascii printable of any string. """
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


def make_logger(loglevel='info', logfile=None):
    import logging
    fmt = '%(asctime)s %(levelname)s %(filename)s:%(lineno)d %(message)s'
    lvl = getattr(logging, loglevel.upper())
    logging.getLogger().setLevel(lvl)

    _cfg = {'format': fmt}
    if logfile:
        _cfg['filename'] = logfile
    logging.basicConfig(**_cfg)

    return logging.getLogger(__name__)


def get_encoding():
    import locale
    import codecs
    locale.setlocale(locale.LC_ALL, '')
    return codecs.lookup(locale.getpreferredencoding()).name


def repr_mapping(mapping):
    return ' '.join('='.join(map(str, kv)) for kv in mapping.items())
