from teldisp import name_unicode, eightbits

theNULL = bytes([0])
(SLC_NOSUPPORT, SLC_CANTCHANGE, SLC_VARIABLE, SLC_DEFAULT) = (
        bytes([const]) for const in range(4))
(SLC_FLUSHOUT, SLC_FLUSHIN, SLC_ACK) = (
        bytes([32]), bytes([64]), bytes([128]))
SLC_LEVELBITS = 0x03

NSLC = 30
(SLC_SYNCH, SLC_BRK, SLC_IP, SLC_AO, SLC_AYT, SLC_EOR, SLC_ABORT, SLC_EOF,
    SLC_SUSP, SLC_EC, SLC_EL, SLC_EW, SLC_RP, SLC_LNEXT, SLC_XON, SLC_XOFF,
    SLC_FORW1, SLC_FORW2, SLC_MCL, SLC_MCR, SLC_MCWL, SLC_MCWR, SLC_MCBOL,
    SLC_MCEOL, SLC_INSRT, SLC_OVER, SLC_ECR, SLC_EWR, SLC_EBOL, SLC_EEOL) = (
            bytes([const]) for const in range(1, NSLC + 1))


# TODO: was modelled after slc.c; pythonize it
class SLC_definition(object):
    def __init__(self, mask=SLC_DEFAULT, value=theNULL):
        """ .. class:SLC_definition(mask : byte, value: byte)

            Defines the willingness to support a Special Linemode Character,
            defined by its SLC support level, ``mask`` and default keyboard
            ASCII byte ``value`` (may be negotiated by client).

            The default byte mask ``SLC_DEFAULT`` and value ``b'\x00'`` infer
            our willingness to support the option, but with no default value.
            The value must be negotiated by client to activate the callback.
        """
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
    def ack(self):
        """ Returns True if SLC_ACK bit is set. """
        return ord(self.mask) & ord(SLC_ACK)

    @property
    def flushin(self):
        """ Returns True if SLC_FLUSHIN bit is set. """
        return ord(self.mask) & ord(SLC_FLUSHIN)

    @property
    def flushout(self):
        """ .. method::flushout() -> bool

            Returns True if SLC_FLUSHIN bit is set.
        """
        return ord(self.mask) & ord(SLC_FLUSHOUT)

    def set_value(self, value):
        """ .. method::set_value(value : byte)

            Set SLC keyboard ascii value to ``byte``.
        """
        assert type(value) is bytes and len(value) == 1, value
        self.val = value

    def set_mask(self, mask):
        """ .. method::set_mask(mask : byte)

            Set SLC option mask, ``mask``.
        """
        assert type(mask) is bytes and len(mask) == 1
        self.mask = mask

    def set_flag(self, flag):
        """ .. method::set_flag(flag : byte)

            Set SLC option flag, ``flag``.
        """
        assert type(flag) is bytes and len(flag) == 1
        self.mask = bytes([ord(self.mask) | ord(flag)])

    def unset_flag(self, flag):
        """ .. method::unset_flag(flag : byte)

            Unset SLC flag byte, ``flag``.
        """
        self.mask = bytes([ord(self.mask) ^ ord(flag)])

    def __str__(self):
        """ SLC definition as string '(flag(|s), value)'. """
        flags = []
        if self.nosupport:
            flags.append('nosupport')
        if self.ack:
            flags.append('ack')
        if self.flushin:
            flags.append('flushin')
        if self.flushout:
            flags.append('flushout')
        return '({}, {})'.format(
                '|'.join(flags) if flags else 'None',
                name_unicode(self.val.decode('iso8859-1')))

class SLC_nosupport(SLC_definition):
    def __init__(self):
        """ .. class:SLC_nosupport()

            Returns SLC definition with byte mask ``SLC_NOSUPPORT`` and value
            ``_POSIX_VDISABLE``, infering our unwillingness to support the
            option.
        """
        SLC_definition.__init__(self, SLC_NOSUPPORT, _POSIX_VDISABLE)

class Forwardmask(object):
    def __init__(self, value, ack=False):
        """ .. class:: ForwardMask(value : bytes, ack: bool)

        Initialize a ForwardMask object using the bytemask value
        received by server with IAC SB LINEMODE DO FORWARDMASK. It
        must be a full 32-bit bytearray.
        """
        assert isinstance(value, (bytes, bytearray)), value
        assert len(value) in (16, 32), len(value)
        self.value = value
        self.ack = ack

    def __repr__(self):
        """ .. method:: __repr__() -> type(list)

            Returns list of strings describing obj as a tabular ASCII map.
        """
        result = []
        MRK_CONT = '(...)'
        def same_as_last(row):
            return len(result) and result[-1].endswith(row.split()[-1])
        def continuing():
            return len(result) and result[-1] == MRK_CONT
        def is_last(mask):
            return mask == len(self.value) - 1
        for mask, byte in enumerate(self.value):
            if byte is 0:
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
                characters = ', '.join([ name_unicode(chr(char))
                    for char in range(start, last + 1) if char in self])
                result.append ('[%2d] %s %s' % (
                    mask, eightbits(byte), characters,))
        return result

    def __str__(self):
        """ .. method:: __str__ -> type(str)

            Returns single string of binary 0 and 1 describing obj.
        """
        return '0b%s' % (''.join([value for (prefix, value) in [
            eightbits(byte).split('b') for byte in self.value]]),)

    def __contains__(self, number):
        """ .. method:: __contains__(number : int) -> type(bool)

            ``True`` if forwardmask has keycode ``number``, else ``False``.
        """
        mask, flag = number // 8, 2 ** (7 - (number % 8))
        return bool(self.value[mask] & flag)

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

#: A simple SLC tab that offers nearly all characters for negotiation,
#  but has no default values of its own, soliciting them from client.
DEFAULT_SLC_TAB = {
        SLC_FORW1: SLC_definition(SLC_NOSUPPORT, _POSIX_VDISABLE),
        SLC_FORW2: SLC_definition(SLC_NOSUPPORT, _POSIX_VDISABLE),
        SLC_EOF: SLC_definition(), SLC_EC: SLC_definition(),
        SLC_EL: SLC_definition(), SLC_IP: SLC_definition(),
        SLC_ABORT: SLC_definition(), SLC_XON: SLC_definition(),
        SLC_XOFF: SLC_definition(), SLC_EW: SLC_definition(),
        SLC_RP: SLC_definition(), SLC_LNEXT: SLC_definition(),
        SLC_AO: SLC_definition(), SLC_SUSP: SLC_definition(),
        SLC_AYT: SLC_definition(), SLC_BRK: SLC_definition(),
        SLC_SYNCH: SLC_definition(), SLC_EOR: SLC_definition(), }


#: This SLC tab when sent to a BSD client warrants no reply; their
#  tabs match exactly. These values are found in ttydefaults.h of
#  termios family of functions.
BSD_SLC_TAB = {
        SLC_FORW1: SLC_definition(SLC_NOSUPPORT, _POSIX_VDISABLE),
        SLC_FORW2: SLC_definition(SLC_NOSUPPORT, _POSIX_VDISABLE),
        SLC_EOF: SLC_definition(  # ^D VEOF
            SLC_VARIABLE, b'\x04'), SLC_EC: SLC_definition(  # BS VERASE
            SLC_VARIABLE, b'\x7f'), SLC_EL: SLC_definition(  # ^U VKILL
            SLC_VARIABLE, b'\x15'), SLC_IP: SLC_definition(  # ^C VINTR
            _SLC_VARIABLE_FIO, b'\x03'), SLC_ABORT: SLC_definition(  # ^\ VQUIT
            _SLC_VARIABLE_FIO, b'\x1c'), SLC_XON: SLC_definition(  # ^Q VSTART
            SLC_VARIABLE, b'\x11'), SLC_XOFF: SLC_definition(  # ^S VSTOP
            SLC_VARIABLE, b'\x13'), SLC_EW: SLC_definition(  # ^W VWERASE
            SLC_VARIABLE, b'\x17'), SLC_RP: SLC_definition(  # ^R VREPRINT
            SLC_VARIABLE, b'\x12'), SLC_LNEXT: SLC_definition(  # ^V VLNEXT
            SLC_VARIABLE, b'\x16'), SLC_AO: SLC_definition(  # ^O VDISCARD
            _SLC_VARIABLE_FO, b'\x0f'), SLC_SUSP: SLC_definition(  # ^Z VSUSP
            _SLC_VARIABLE_FI, b'\x1a'), SLC_AYT: SLC_definition(  # ^T VSTATUS
            SLC_VARIABLE, b'\x14'), SLC_BRK: SLC_definition(),
            SLC_SYNCH: SLC_definition(), SLC_EOR: SLC_definition(), }

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
    """ Given an SLC byte, return global mnumonic constant as string. """
    return (repr(byte) if byte not in _DEBUG_SLC_OPTS
            else _DEBUG_SLC_OPTS[byte])

