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

def escape_quote(args, quote_char="'", join_char=' '):
    """ .. function::quote(args : list, quote_char="'") -> string

        Supplement shlex.quote, returning list of strings ``args``
        joined by ``join_char`` and quoted by ``quote_char`` if
        ``join_char`` is used within that argument. For example:

        >>> print(escape_quote(['x', 'y', 'zz y']))
        "x y 'zz y'"
    """
    def quoted(arg):
        return (''.join(quote_char, arg, quote_char)
                if join_char in arg else arg)
    return join_char.join([quoted(arg) for arg in args] if args else [])

def postfix(buf, using=' '):
    """ .. function::postfix(buf : string, using=' ') -> string

        Returns buffer postfixed with ``using`` if non-empty.
    """
    return '{}{}'.format(buf, using) if buf else ''

def prefix(buf, using=' '):
    """ .. function::prefix(buf : string, using=' ') -> string

        Returns buffer prefixed with ``using`` if non-empty.
    """
    return '{}{}'.format(buf, using) if buf else ''


