def eightbits(number):
    """ return binary representation of ``number``, padded to 8 bytes. """
    prefix, value = bin(number).split('b')
    return '0b%0.8i' % (int(value),)


def name_unicode(ucs):
    """ Return 7-bit ascii printable of any string. """
    if ord(ucs) < ord(' ') or ord(ucs) == 127:
        ucs = r'^{}'.format(chr(ord(ucs) ^ ord('@')))
    elif ord(ucs) > 127 or not ucs.isprintable():
        ucs = r'\x{:02x}'.format(ord(ucs))
    return ucs
