"""
Special Line Character support for Telnet Linemode Option (:rfc:`1184`).
"""
from .accessories import eightbits, name_unicode
from .telopt import theNULL

__all__ = ('SLC', 'SLC_AYT', 'NSLC', 'BSD_SLC_TAB', 'generate_slctab',
           'Linemode', 'LMODE_MODE_REMOTE', 'SLC_SYNCH', 'SLC_IP', 'SLC_AYT',
           'SLC_ABORT', 'SLC_SUSP', 'SLC_EL', 'SLC_RP', 'SLC_XON', 'snoop',
           'generate_forwardmask', 'Forwardmask', 'name_slc_command',
           'LMODE_FORWARDMASK', 'LMODE_MODE', 'NSLC', 'LMODE_MODE',
           'LMODE_SLC', 'SLC', 'SLC_nosupport', 'SLC_DEFAULT', 'SLC_VARIABLE',
           'SLC_NOSUPPORT', 'SLC_ACK', 'SLC_CANTCHANGE', 'SLC_LNEXT', 'SLC_EC',
           'SLC_EW', 'SLC_EOF', 'SLC_AO',)

(SLC_NOSUPPORT, SLC_CANTCHANGE, SLC_VARIABLE, SLC_DEFAULT) = (
    bytes([const]) for const in range(4)) # 0, 1, 2, 3
(SLC_FLUSHOUT, SLC_FLUSHIN, SLC_ACK) = (
    bytes([2**const]) for const in range(5, 8))  # 32, 64, 128

SLC_LEVELBITS = 0x03
NSLC = 30
(SLC_SYNCH, SLC_BRK, SLC_IP, SLC_AO, SLC_AYT, SLC_EOR, SLC_ABORT, SLC_EOF,
    SLC_SUSP, SLC_EC, SLC_EL, SLC_EW, SLC_RP, SLC_LNEXT, SLC_XON, SLC_XOFF,
    SLC_FORW1, SLC_FORW2, SLC_MCL, SLC_MCR, SLC_MCWL, SLC_MCWR, SLC_MCBOL,
    SLC_MCEOL, SLC_INSRT, SLC_OVER, SLC_ECR, SLC_EWR, SLC_EBOL, SLC_EEOL
 ) = (bytes([const]) for const in range(1, NSLC + 1))

(LMODE_MODE, LMODE_FORWARDMASK, LMODE_SLC) = (
    bytes([const]) for const in range(1, 4))
(LMODE_MODE_REMOTE, LMODE_MODE_LOCAL, LMODE_MODE_TRAPSIG) = (
    bytes([const]) for const in range(3))
(LMODE_MODE_ACK, LMODE_MODE_SOFT_TAB, LMODE_MODE_LIT_ECHO) = (
    bytes([4]), bytes([8]), bytes([16]))


class SLC(object):
    def __init__(self, mask=SLC_DEFAULT, value=theNULL):
        """
        Defines the willingness to support a Special Linemode Character.

        Defined by its SLC support level, ``mask`` and default keyboard
        ASCII byte ``value`` (may be negotiated by client).
        """
        #   The default byte mask ``SLC_DEFAULT`` and value ``b'\x00'`` infer
        #   our willingness to support the option, but with no default value.
        #   The value must be negotiated by client to activate the callback.
        assert type(mask) is bytes and type(value) is bytes, (mask, value)
        assert len(mask) == 1 and len(value) == 1, (mask, value)
        self.mask = mask
        self.val = value

    @property
    def level(self):
        """ Returns SLC level of support.  """
        return bytes([ord(self.mask) & SLC_LEVELBITS])

    @property
    def nosupport(self):
        """ Returns True if SLC level is SLC_NOSUPPORT. """
        return self.level == SLC_NOSUPPORT

    @property
    def cantchange(self):
        """ Returns True if SLC level is SLC_CANTCHANGE. """
        return self.level == SLC_CANTCHANGE

    @property
    def variable(self):
        """ Returns True if SLC level is SLC_VARIABLE. """
        return self.level == SLC_VARIABLE

    @property
    def default(self):
        """ Returns True if SLC level is SLC_DEFAULT. """
        return self.level == SLC_DEFAULT

    @property
    def ack(self):
        """ Returns True if SLC_ACK bit is set. """
        return ord(self.mask) & ord(SLC_ACK)

    @property
    def flushin(self):
        """ Returns True if SLC_FLUSHIN bit is set. """
        return ord(self.mask) & ord(SLC_FLUSHIN)

    @property
    def flushout(self):
        """ Returns True if SLC_FLUSHIN bit is set.  """
        return ord(self.mask) & ord(SLC_FLUSHOUT)

    def set_value(self, value):
        """ Set SLC keyboard ascii value to ``byte``.  """
        assert type(value) is bytes and len(value) == 1, value
        self.val = value

    def set_mask(self, mask):
        """ Set SLC option mask, ``mask``.  """
        assert type(mask) is bytes and len(mask) == 1
        self.mask = mask

    def set_flag(self, flag):
        """ Set SLC option flag, ``flag``.  """
        assert type(flag) is bytes and len(flag) == 1
        self.mask = bytes([ord(self.mask) | ord(flag)])

    def __str__(self):
        """ SLC definition as string '(value, flag(|s))'. """
        flags = list()
        for flag in ('nosupport', 'variable', 'default', 'ack',
                     'flushin', 'flushout', 'cantchange', ):
            if getattr(self, flag):
                flags.append(flag)
        return '({value}, {flags})'.format(
            value=(name_unicode(self.val)
                   if self.val != _POSIX_VDISABLE
                   else '(DISABLED:\\xff)'),
            flags='|'.join(flags))


class SLC_nosupport(SLC):
    def __init__(self):
        """
        SLC definition inferring our unwillingness to support the option.
        """
        SLC.__init__(self, SLC_NOSUPPORT, _POSIX_VDISABLE)

#: SLC value may be changed, flushes input and output
_SLC_VARIABLE_FIO = bytes(
    [ord(SLC_VARIABLE) | ord(SLC_FLUSHIN) | ord(SLC_FLUSHOUT)])
#: SLC value may be changed, flushes input
_SLC_VARIABLE_FI = bytes(
    [ord(SLC_VARIABLE) | ord(SLC_FLUSHIN)])
#: SLC value may be changed, flushes output
_SLC_VARIABLE_FO = bytes(
    [ord(SLC_VARIABLE) | ord(SLC_FLUSHOUT)])
#: SLC function for this value is not supported
_POSIX_VDISABLE = b'\xff'

#: This SLC tab when sent to a BSD client warrants no reply; their
#  tabs match exactly. These values are found in ttydefaults.h of
#  termios family of functions.
BSD_SLC_TAB = {
    SLC_FORW1: SLC_nosupport(),  # unsupported; causes all buffered
    SLC_FORW2: SLC_nosupport(),  # characters to be sent immediately,
    SLC_EOF: SLC(SLC_VARIABLE,        b'\x04'),  # ^D VEOF
    SLC_EC: SLC(SLC_VARIABLE,         b'\x7f'),  # BS VERASE
    SLC_EL: SLC(SLC_VARIABLE,         b'\x15'),  # ^U VKILL
    SLC_IP: SLC(_SLC_VARIABLE_FIO,    b'\x03'),  # ^C VINTR
    SLC_ABORT: SLC(_SLC_VARIABLE_FIO, b'\x1c'),  # ^\ VQUIT
    SLC_XON: SLC(SLC_VARIABLE,        b'\x11'),  # ^Q VSTART
    SLC_XOFF: SLC(SLC_VARIABLE,       b'\x13'),  # ^S VSTOP
    SLC_EW: SLC(SLC_VARIABLE,         b'\x17'),  # ^W VWERASE
    SLC_RP: SLC(SLC_VARIABLE,         b'\x12'),  # ^R VREPRINT
    SLC_LNEXT: SLC(SLC_VARIABLE,      b'\x16'),  # ^V VLNEXT
    SLC_AO: SLC(_SLC_VARIABLE_FO,     b'\x0f'),  # ^O VDISCARD
    SLC_SUSP: SLC(_SLC_VARIABLE_FI,   b'\x1a'),  # ^Z VSUSP
    SLC_AYT: SLC(SLC_VARIABLE,        b'\x14'),  # ^T VSTATUS
    # no default value for break, sync, end-of-record,
    SLC_BRK: SLC(), SLC_SYNCH: SLC(), SLC_EOR: SLC(),
}


def generate_slctab(tabset=BSD_SLC_TAB):
    """ Returns full 'SLC Tab' for definitions found using ``tabset``.
        Functions not listed in ``tabset`` are set as SLC_NOSUPPORT.
    """
    #   ``slctab`` is a dictionary of SLC functions, such as SLC_IP,
    #   to a tuple of the handling character and support level.
    _slctab = {}
    for slc in [bytes([const]) for const in range(1, NSLC + 1)]:
        _slctab[slc] = tabset.get(slc, SLC_nosupport())
    return _slctab


def generate_forwardmask(binary_mode, tabset, ack=False):
    """
    Generate a :class:`~.Forwardmask` instance.

    Generate a 32-byte (``binary_mode`` is True) or 16-byte (False) Forwardmask
    instance appropriate for the specified ``slctab``.  A Forwardmask is formed
    by a bitmask of all 256 possible 8-bit keyboard ascii input, or, when not
    'outbinary', a 16-byte 7-bit representation of each value, and whether or
    not they should be "forwarded" by the client on the transport stream
    """
    num_bytes, msb = (32, 256) if binary_mode else (16, 127)
    mask32 = [theNULL] * num_bytes
    for mask in range(msb // 8):
        start = mask * 8
        last = start + 7
        byte = theNULL
        for char in range(start, last + 1):
            (func, slc_name, slc_def) = snoop(bytes([char]), tabset, dict())
            if func is not None and not slc_def.nosupport:
                # set bit for this character, it is a supported slc char
                byte = bytes([ord(byte) | 1])
            if char != last:
                # shift byte left for next character,
                # except for the final byte.
                byte = bytes([ord(byte) << 1])
        mask32[mask] = byte
    return Forwardmask(b''.join(mask32), ack)


def snoop(byte, slctab, slc_callbacks):
    """ Scan ``slctab`` for matching ``byte`` values.

        Returns (callback, func_byte, slc_definition) on match.
        Otherwise, (None, None, None). If no callback is assigned,
        the value of callback is always None.
    """
    for slc_func, slc_def in slctab.items():
        if byte == slc_def.val and slc_def.val != theNULL:
            return (slc_callbacks.get(slc_func, None), slc_func, slc_def)
    return (None, None, None)


class Linemode(object):
    """ """

    def __init__(self, mask=b'\x00'):
        """ A mask of ``LMODE_MODE_LOCAL`` means that all line editing is
            performed on the client side (default). A mask of theNULL (\x00)
            indicates that editing is performed on the remote side.
            Valid bit flags of mask are: ``LMODE_MODE_TRAPSIG``,
            ``LMODE_MODE_ACK``, ``LMODE_MODE_SOFT_TAB``, and
            ``LMODE_MODE_LIT_ECHO``.
        """
        assert type(mask) is bytes and len(mask) == 1, (repr(mask), mask)
        self.mask = mask

    def __eq__(self, other):
        """Compare by another Linemode (LMODE_MODE_ACK ignored)."""
        # the inverse OR(|) of acknowledge bit UNSET in comparator,
        # would be the AND OR(& ~) to compare modes without acknowledge
        # bit set.
        return (
            (ord(self.mask) | ord(LMODE_MODE_ACK)) ==
            (ord(other.mask) | ord(LMODE_MODE_ACK))
        )

    @property
    def local(self):
        """ True if linemode is local. """
        return bool(ord(self.mask) & ord(LMODE_MODE_LOCAL))

    @property
    def remote(self):
        """ True if linemode is remote. """
        return not self.local

    @property
    def trapsig(self):
        """ True if signals are trapped by client. """
        return bool(ord(self.mask) & ord(LMODE_MODE_TRAPSIG))

    @property
    def ack(self):
        """ Returns True if mode has been acknowledged. """
        return bool(ord(self.mask) & ord(LMODE_MODE_ACK))

    @property
    def soft_tab(self):
        """ Returns True if client will expand horizontal tab (\x09). """
        return bool(ord(self.mask) & ord(LMODE_MODE_SOFT_TAB))

    @property
    def lit_echo(self):
        """ Returns True if non-printable characters are displayed as-is. """
        return bool(ord(self.mask) & ord(LMODE_MODE_LIT_ECHO))

    def __str__(self):
        """ Returns string representation of line mode, for debugging """
        return 'remote' if self.remote else 'local'

    def __repr__(self):
        return '<{0!r}: {1}>'.format(
            self.mask, ', '.join([
                '{0}:{1}'.format(prop, getattr(self, prop))
                for prop in ('lit_echo', 'soft_tab', 'ack',
                             'trapsig', 'remote', 'local')])
        )


class Forwardmask(object):
    """ """

    def __init__(self, value, ack=False):
        """
        Forwardmask object using the bytemask value received by server.

        :param bytes value: bytemask ``value`` received by server after ``IAC SB
            LINEMODE DO FORWARDMASK``. It must be a bytearray of length 16 or 32.
        """
        assert isinstance(value, (bytes, bytearray)), value
        assert len(value) in (16, 32), len(value)
        self.value = value
        self.ack = ack

    def description_table(self):
        """
        Returns list of strings describing obj as a tabular ASCII map.
        """
        result = []
        MRK_CONT = '(...)'
        continuing = lambda: len(result) and result[-1] == MRK_CONT
        is_last = lambda mask: mask == len(self.value) - 1
        same_as_last = lambda row: (
            len(result) and result[-1].endswith(row.split()[-1]))

        for mask, byte in enumerate(self.value):
            if byte == 0:
                if continuing() and not is_last(mask):
                    continue
                row = '[%2d] %s' % (mask, eightbits(0),)
                if not same_as_last(row) or is_last(mask):
                    result.append(row)
                else:
                    result.append(MRK_CONT)
            else:
                start = mask * 8
                last = start + 7
                characters = ', '.join([name_unicode(chr(char))
                                        for char in range(start, last + 1)
                                        if char in self])
                result.append('[%2d] %s %s' % (
                    mask, eightbits(byte), characters,))
        return result

    def __str__(self):
        """Returns single string of binary 0 and 1 describing obj."""
        return '0b%s' % (''.join([value for (prefix, value) in [
            eightbits(byte).split('b') for byte in self.value]]),)

    def __contains__(self, number):
        """Whether forwardmask contains keycode ``number``."""
        mask, flag = number // 8, 2 ** (7 - (number % 8))
        return bool(self.value[mask] & flag)


#: List of globals that may match an slc function byte
_DEBUG_SLC_OPTS = dict([(value, key)
                        for key, value in locals().items() if key in
                        ('SLC_SYNCH', 'SLC_BRK', 'SLC_IP', 'SLC_AO', 'SLC_AYT',
                            'SLC_EOR', 'SLC_ABORT', 'SLC_EOF', 'SLC_SUSP',
                            'SLC_EC', 'SLC_EL', 'SLC_EW', 'SLC_RP',
                            'SLC_LNEXT', 'SLC_XON', 'SLC_XOFF', 'SLC_FORW1',
                            'SLC_FORW2', 'SLC_MCL', 'SLC_MCR', 'SLC_MCWL',
                            'SLC_MCWR', 'SLC_MCBOL', 'SLC_MCEOL', 'SLC_INSRT',
                            'SLC_OVER', 'SLC_ECR', 'SLC_EWR', 'SLC_EBOL',
                            'SLC_EEOL',)])


def name_slc_command(byte):
    """ Given an SLC ``byte``, return global mnemonic as string. """
    return (repr(byte) if byte not in _DEBUG_SLC_OPTS
            else _DEBUG_SLC_OPTS[byte])
