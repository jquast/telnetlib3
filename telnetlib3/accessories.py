"""Accessory functions."""

__all__ = ('name_unicode', 'eightbits')

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
