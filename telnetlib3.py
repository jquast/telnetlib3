#!/usr/bin/env python3
"""
This project implements a Telnet client and server protocol, analogous to
the standard ``telnetlib`` module, with a great many more capabilities.

This implementation uses the 'tulip' project, the asynchronous networking
model to become standard with python 3.4, and requires Python 3.3.

Public protocols are as follows:

    ``BasicTelnetServer``: does not insist on any telnet options on-connect,
        and defaults to the basic Telnet NVT (basic linemode).

    ``CharacterTelnetServer`` is suitable for character-at-a-time input,
        such as gnu readline, an interactive shell, pseudo-terminals, games,
        or BBS programs that provide line-editing or hotkey capabilities.
        This is done by negotiating (DONT, LINEMODE), (WILL/DO SGA),
        and (WILL ECHO) on-connect in the ``banner`` method.

Many standard and extended telnet RFC protocols are implemented with one
deviation: Telnet byte (IAC, DM) is described as having the TCP Urgent bit
set. This is not supported by tulip (argument ``errorfds`` to select.select).

Listed is a summary of RFC's implemented, sorted by publication date:

[1] RFC 854  Telnet Protocol Specification                        May 1983
[x] RFC 855  Telnet Option Specification                          May 1983
[x] RFC 856  Telnet Binary Transmission                           May 1983
[x] RFC 857  Telnet Echo Option                                   May 1983
[x] RFC 858  Telnet Supress Go Ahead Option                       May 1983
[x] RFC 859  Telnet Status Option                                 May 1983
[x] RFC 860  Telnet Timing mark Option                            May 1983
[x] RFC 861  Telnet Extended Options List                         May 1983
[x] RFC 885  Telnet End of Record Option                          Dec 1983
[x] RFC 930  Telnet Terminal Type Option                          Jan 1985
[x] RFC 1073 Telnet Window Size Option                            Oct 1988
[x] RFC 1079 Telnet Terminal Speed Option                         Dec 1988
[x] RFC 1091 Telnet Terminal-Type Option                          Feb 1989
[x] RFC 1086 Telnet X Display Location Option                     Mar 1989
[*] RFC 1116 Telnet Linemode Option                               Aug 1989
[*] RFC 1184 Telnet Linemode Option (extended options)            Oct 1990
[*] RFC 1123 Requirements for Internet Hosts                      Oct 1989
[*] RFC 1143 The Q Method of Implementing .. Option Negotiation   Feb 1990
[*] RFC 1372 Telnet Remote Flow Control Option                    Oct 1992

Additional Resources,
   "Telnet Protocol," MIL-STD-1782, U.S. Department of Defense, May 1984.
   "Mud Terminal Type Standard," http://tintin.sourceforge.net/mtts/
   "Telnet Protocol in C-Kermit 8.0 and Kermit 95 2.0"
       http://www.columbia.edu/kermit/telnet80.html
   "Comments on the new TELNET Protocol and its Implementation," RFC 559
   "Telnet Negotiation Concepts (http://lpc.psyc.eu/doc/concepts/negotiation


Listed below is a summary of compliance with RFC 1123, 'x' notes 'compliant',
and 'o' notes 'in progress':

                                                             /must
                                                            / /should
                                                           / / /may
CHECKLIST, per RFC 1123, 3.5. TELNET REQUIREMENTS SUMMARY / / / /should not
                                                         / / / / /must not
FEATURE                                         |SECTION/ / / / / /
------------------------------------------------|-------|-|-|-|-|-|
                                                |       | | | | | |
Option Negotiation                              | 3.2.1 |x| | | | |
  Avoid negotiation loops                       | 3.2.1 |x| | | | |
  Refuse unsupported options                    | 3.2.1 |x| | | | |
  Negotiation OK anytime on connection          | 3.2.1 | |x| | | |
  Default to NVT                                | 3.2.1 |x| | | | |
  Send official name in Term-Type option        | 3.2.8 |o| | | | |
  Accept any name in Term-Type option           | 3.2.8 |x| | | | |
  Implement Binary, Suppress-GA options         | 3.3.3 |x| | | | |
  Echo, Status, EOL, Ext-Opt-List options       | 3.3.3 | |x| | | |
  Implement Window-Size option if appropriate   | 3.3.3 | |x| | | |
  Server initiate mode negotiations             | 3.3.4 | |x| | | |
  User can enable/disable init negotiations     | 3.3.4 | |x| | | |
                                                |       | | | | | |
Go-Aheads                                       |       | | | | | |
  Non-GA server negotiate SUPPRESS-GA option    | 3.2.2 |x| | | | |
  User or Server accept SUPPRESS-GA option      | 3.2.2 |x| | | | |
  User Telnet ignore GA's                       | 3.2.2 | | |o| | |
                                                |       | | | | | |
Control Functions                               |       | | | | | |
  Support SE NOP DM IP AO AYT SB                | 3.2.3 |x| | | | |
  Support EOR EC EL Break                       | 3.2.3 | | |x| | |
  Ignore unsupported control functions          | 3.2.3 |x| | | | |
  User, Server discard urgent data up to DM     | 3.2.4 |x| | | | |
  User Telnet send "Synch" after IP, AO, AYT    | 3.2.4 | |o| | | |*
  Server Telnet reply Synch to IP               | 3.2.4 | | |o| | |
  Server Telnet reply Synch to AO               | 3.2.4 |x| | | | |
  User Telnet can flush output when send IP     | 3.2.4 | |x| | | |
                                                |       | | | | | |
Encoding                                        |       | | | | | |
  Send high-order bit in NVT mode               | 3.2.5 | | | |*| |
  Send high-order bit as parity bit             | 3.2.5 | | | | |x|
  Negot. BINARY if pass high-ord. bit to applic | 3.2.5 | |*| | | |
  Always double IAC data byte                   | 3.2.6 |x| | | | |
  Double IAC data byte in binary mode           | 3.2.7 |x| | | | |
  Obey Telnet cmds in binary mode               | 3.2.7 |x| | | | |
  End-of-line, CR NUL in binary mode            | 3.2.7 | | | | |o|
                                                |       | | | | | |
End-of-Line                                     |       | | | | | |
  EOL at Server same as local end-of-line       | 3.3.1 |x| | | | |
  ASCII Server accept CR LF or CR NUL for EOL   | 3.3.1 |x| | | | |
  User Telnet able to send CR LF, CR NUL, or LF | 3.3.1 |o| | | | |
    ASCII user able to select CR LF/CR NUL      | 3.3.1 | |o| | | |
    User Telnet default mode is CR LF           | 3.3.1 | |o| | | |
  Non-interactive uses CR LF for EOL            | 3.3.1 |o| | | | |
[x] Where RFC 854 implies that the other side may reject a request to
    enable an option, it means that you must accept such a rejection.
[x] It MUST therefore remember that it is negotiating a WILL/DO, and this
    negotiation state MUST be separate from the enabled state and from
    the disabled state.  During the negotiation state, any effects of
    having the option enabled MUST NOT be used.
[x] Rule: Remember DONT/WONT requests
[x] Rule: Prohibit new requests before completing old negotiation

[ ] Rule: When LINEMODE is turned on, and when in EDIT mode, when any normal
    line terminator on the client side operating system is typed, the
    line should be transmitted with "CR LF" as the line terminator.  When
    EDIT mode is turned off, a carriage return should be sent as "CR
    NUL", a line feed should be sent as LF, and any other key that cannot
    be mapped into an ASCII character, but means the line is complete
    (like a DOIT or ENTER key), should be sent as "CR LF".

[x] Rule: At no time should "DO LINEMODE" be negotiated in both directions of
    the Telnet connection.  The side that is the "DO LINEMODE" is considered
    to be the server side, and the side that is "WILL LINEMODE" is the client
    side.

[ ] Rule: At no time should "SB LINEMODE DO/DONT FORWARDMASK", be sent unless
    "DO LINEMODE" has been previously negotiated.  At no time should "SB
    LINEMODE WILL/WONT FORWARDMASK", be sent unless "WILL LINEMODE" has
    been previously negotiated.


Definitions



Local line editing  - means that all normal command line character
  processing, like "Erase Character" and "Erase Line", happen on the
  local system, and only when "CR LF" (or some other special character)
  is encountered is the edited data sent to the remote system.


Signal trapping  - means, for example, that if the user types the
  character associated with the IP function, then the "IAC IP" function
  is sent to the remote side instead of the character typed.  Remote
  signal trapping means, for example, that if the user types the
  character associated with the IP function, then the "IAC IP" function
  is not sent to the remote side, but rather the actual character typed
  is sent to the remote side.


"""
import collections
import logging
import argparse
import shlex
import time
import sys

assert sys.version >= '3.3', 'Please use Python 3.3 or higher.'
import tulip

from telnetlib import LINEMODE, NAWS, NEW_ENVIRON, BINARY, SGA, ECHO, STATUS
from telnetlib import TTYPE, TSPEED, LFLOW, XDISPLOC, IAC, DONT, DO, WONT
from telnetlib import WILL, SE, NOP, TM, DM, BRK, IP, AO, AYT, EC, EL, EOR
from telnetlib import GA, SB
EOF = bytes([236])
SUSP = bytes([237])
ABORT = bytes([238])
IS = bytes([0])
SEND = bytes([1])
(LFLOW_OFF, LFLOW_ON, LFLOW_RESTART_ANY, LFLOW_RESTART_XON
 ) = (bytes([const]) for const in range(4))
NSLC = 30
(SLC_SYNCH, SLC_BRK, SLC_IP, SLC_AO, SLC_AYT, SLC_EOR, SLC_ABORT, SLC_EOF,
    SLC_SUSP, SLC_EC, SLC_EL, SLC_EW, SLC_RP, SLC_LNEXT, SLC_XON, SLC_XOFF,
    SLC_FORW1, SLC_FORW2, SLC_MCL, SLC_MCR, SLC_MCWL, SLC_MCWR, SLC_MCBOL,
    SLC_MCEOL, SLC_INSRT, SLC_OVER, SLC_ECR, SLC_EWR, SLC_EBOL, SLC_EEOL
    ) = (bytes([const]) for const in range(1, NSLC + 1))
(SLC_FLUSHOUT, SLC_FLUSHIN, SLC_ACK
    ) = (bytes([32]), bytes([64]), bytes([128]))
(SLC_NOSUPPORT, SLC_CANTCHANGE, SLC_VARIABLE, SLC_DEFAULT
    ) = (bytes([const]) for const in range(4))
SLC_LEVELBITS = 0x03
LMODE_MODE = bytes([1])
LMODE_MODE_EDIT = bytes([1])
LMODE_MODE_TRAPSIG = bytes([2])
LMODE_MODE_ACK = bytes([4])
LMODE_MODE_SOFT_TAB = bytes([8])
LMODE_MODE_LIT_ECHO = bytes([16])
LMODE_FORWARDMASK = bytes([2])
LMODE_SLC = bytes([3])
SB_MAXSIZE = 2048
SLC_MAXSIZE = 6 * NSLC

# see: TelnetStreamReader._default_callbacks
DEFAULT_IAC_CALLBACKS = (
        (BRK, 'brk'), (IP, 'ip'), (AO, 'ao'), (AYT, 'ayt'), (EC, 'ec'),
        (EL, 'el'), (EOR, 'eor'), (EOF, 'eof'), (SUSP, 'susp'),
        (ABORT, 'abort'), (NOP, 'nop'), (DM, 'dm'), )
DEFAULT_SLC_CALLBACKS = (
        (SLC_SYNCH, 'dm'), (SLC_BRK, 'brk'), (SLC_IP, 'ip'),
        (SLC_AO, 'ao'), (SLC_AYT, 'ayt'), (SLC_EOR, 'eor'),
        (SLC_ABORT, 'abort'), (SLC_EOF, 'eof'), (SLC_SUSP, 'susp'),
        (SLC_EC, 'ec'), (SLC_EL, 'el'), (SLC_EW, 'ew'), (SLC_RP, 'rp'),
        (SLC_LNEXT, 'lnext'), (SLC_XON, 'xon'), (SLC_XOFF, 'xoff'), )
DEFAULT_EXT_CALLBACKS = (
        (TTYPE, 'ttype'), (TSPEED, 'tspeed'), (XDISPLOC, 'xdisploc'),
        (NEW_ENVIRON, 'env'), (NAWS, 'naws'), )

class SLC_definition(object):
    def __init__(self, mask, value):
        assert type(mask) is bytes and type(value) is bytes
        assert len(mask) == 1 and len(value) == 1
        self.mask = mask
        self.val = value

    @property
    def level(self):
        """ Returns SLC level of support
        """
        return bytes([ord(self.mask) & SLC_LEVELBITS])

    def nosupport(self):
        """ Returns True if SLC level is SLC_NOSUPPORT,
        """
        return bool(ord(self.level))

    @property
    def ack(self):
        """ Returns True if SLC_ACK bit is set
        """
        return ord(self.mask) & ord(SLC_ACK)

    @property
    def flushin(self):
        """ Returns True if SLC_FLUSHIN bit is set
        """
        return ord(self.mask) & ord(SLC_FLUSHIN)

    @property
    def flushout(self):
        """ Returns True if SLC_FLUSHIN bit is set
        """
        return ord(self.mask) & ord(SLC_FLUSHOUT)

    def set_value(self, value):
        """ Set SLC keyboard ascii value, ``byte``.
        """
        assert type(value) is bytes and len(value) == 1
        self.val = value

    def set_mask(self, mask):
        """ Set SLC mask, ``mask``.
        """
        assert type(mask) is bytes and len(mask) == 1
        self.mask = mask

    def set_flag(self, flag):
        """ Set SLC flag byte, ``flag``.
        """
        assert type(flag) is bytes and len(flag) == 1
        self.mask = bytes([ord(self.mask) | ord(flag)])

    def unset_flag(self, flag):
        """ Unset SLC flag byte, ``flag``.
        """
        self.mask = bytes([ord(self.mask) ^ ord(flag)])

    def __str__(self):
        """ Returns SLC definition as string '(flags, value)'.
        """
        flags = []
        if self.ack:
            flags.append('ack')
        if self.flushin:
            flags.append('flushin')
        if self.flushout:
            flags.append('flushout')
        return '(%s, %r)' % ('|'.join(flags) if flags else 'None', self.val)


class SLC_nosupport(SLC_definition):
    def __init__(self):
        SLC_definition.__init__(self, SLC_NOSUPPORT, _POSIX_VDISABLE)

# The following are default values for the SLC tab, set on initialization
# or when special SLC function (0, SLC_DEFAULT, 0) is received. If the
# client requests the same SLC values (as is the case for bsd telnet), then
# no further negotiation is required.

_POSIX_VDISABLE = b'\xff'  # note: same value as IAC (must be escaped!)

# Note: V* constant values are duplicated from termios, windows platforms
# may ImportError? See bsd telnetd sys_term.c:spcset for reference source.
_SLC_VARIABLE_FLUSHINOUT = bytes(
        [ord(SLC_VARIABLE) | ord(SLC_FLUSHIN) | ord(SLC_FLUSHOUT)])
_SLC_VARIABLE_FLUSHIN = bytes(
        [ord(SLC_VARIABLE) | ord(SLC_FLUSHIN)])
_SLC_VARIABLE_FLUSHOUT = bytes(
        [ord(SLC_VARIABLE) | ord(SLC_FLUSHOUT)])

DEFAULT_SLC_TAB = {
        # A simple SLC tab that offers nearly all characters for negotiation,
        SLC_FORW1: SLC_definition(SLC_NOSUPPORT, _POSIX_VDISABLE),
        SLC_FORW2: SLC_definition(SLC_NOSUPPORT, _POSIX_VDISABLE),
        SLC_EOF: SLC_definition(SLC_DEFAULT, b'\x00'),
        SLC_EC: SLC_definition(SLC_DEFAULT, b'\x00'),
        SLC_EL: SLC_definition(SLC_DEFAULT, b'\x00'),
        SLC_IP: SLC_definition(SLC_DEFAULT, b'\x00'),
        SLC_ABORT: SLC_definition(SLC_DEFAULT, b'\x00'),
        SLC_XON: SLC_definition(SLC_DEFAULT, b'\x00'),
        SLC_XOFF: SLC_definition(SLC_DEFAULT, b'\x00'),
        SLC_EW: SLC_definition(SLC_DEFAULT, b'\x00'),
        SLC_RP: SLC_definition(SLC_DEFAULT, b'\x00'),
        SLC_LNEXT: SLC_definition(SLC_DEFAULT, b'\x00'),
        SLC_AO: SLC_definition(SLC_DEFAULT, b'\x00'),
        SLC_SUSP: SLC_definition(SLC_DEFAULT, b'\x00'),
        SLC_AYT: SLC_definition(SLC_DEFAULT, b'\x00'),
        SLC_BRK: SLC_definition(SLC_DEFAULT, b'\x00'),
        SLC_SYNCH: SLC_definition(SLC_DEFAULT, b'\x00'),
        SLC_EOR: SLC_definition(SLC_DEFAULT, b'\x00'),
}

BSD_SLC_TAB = {
        # Special Line Characters supported by the AdvancedTelnetServer.
        # =
        # drivers had a "send" or "forward" button that could transmit
        # input before CR, as in the case of the line becoming too long to
        # handle locally ... legacy
        SLC_FORW1: SLC_definition(SLC_NOSUPPORT, _POSIX_VDISABLE),
        SLC_FORW2: SLC_definition(SLC_NOSUPPORT, _POSIX_VDISABLE),
        # VEOF ^D
        SLC_EOF: SLC_definition(SLC_VARIABLE, b'\x04'),
        # VERASE chr(127) (backspace)
        SLC_EC: SLC_definition(SLC_VARIABLE, b'\x7f'),
        # VKILL ^U
        SLC_EL: SLC_definition(SLC_VARIABLE, b'\x15'),
        # VINTR ^C
        SLC_IP: SLC_definition(bytes([ord(SLC_VARIABLE)
            | ord(SLC_FLUSHIN) | ord(SLC_FLUSHOUT)]), b'\x03'),
        # VQUIT ^\ (SIGQUIT)
        SLC_ABORT: SLC_definition(_SLC_VARIABLE_FLUSHINOUT, b'\x1c'),
        # VSTART ^Q
        SLC_XON: SLC_definition(SLC_VARIABLE, b'\x11'),
        # VSTOP, ^S
        SLC_XOFF: SLC_definition(SLC_VARIABLE, b'\x13'),
        # VWERASE, ^W
        SLC_EW: SLC_definition(SLC_VARIABLE, b'\x17'),
        # VREPRINT, ^R
        SLC_RP: SLC_definition(SLC_VARIABLE, b'\x12'),
        # VLNEXT, ^V
        SLC_LNEXT: SLC_definition(SLC_VARIABLE, b'\x16'),
        # VDISCARD, ^O
        SLC_AO: SLC_definition(_SLC_VARIABLE_FLUSHOUT, b'\x0f'),
        # VSUSP, ^Z
        SLC_SUSP: SLC_definition(_SLC_VARIABLE_FLUSHIN, b'\x1a'),
        # VSTATUS, ^T
        SLC_AYT: SLC_definition(SLC_VARIABLE, b'\x14'),
        # Break, Synch, and EOR are set
        # to SLC_DEFAULT with value 0; to
        # indicate that we have no default
        # for those values.
        # ==
        SLC_BRK: SLC_definition(SLC_DEFAULT, b'\x00'),
        SLC_SYNCH: SLC_definition(SLC_DEFAULT, b'\x00'),
        SLC_EOR: SLC_definition(SLC_DEFAULT, b'\x00'),
}

def _escape_iac(buf):
    rbuf = b''
    for byte in buf:
        if bytes([byte]) == IAC:
            rbuf += IAC + IAC
        else:
            rbuf += bytes([byte])
    return rbuf

class Linemode(object):
    def __init__(self, mask=b'\x00'):
        assert type(mask) is bytes and len(mask) == 1
        self.mask = mask

    def set_flag(self, flag):
        """ Set linemode bitmask ``flag``.
        """
        self.mask = bytes([ord(self.mask) | ord(flag)])

    def unset_flag(self, flag):
        """ Unset linemode bitmask ``flag``.
        """
        self.mask = bytes([ord(self.mask) ^ ord(flag)])

    @property
    def edit(self):
        """ Returns True if telnet stream is in EDIT mode.

            When set, the client side of the connection should process all
            input lines, performing any editing functions, and only send
            completed lines to the remote side.

            When unset, client side should *not* process any input from the
            user, and the server side should take care of all character
            processing that needs to be done.
        """
        return bool(ord(self.mask) & ord(LMODE_MODE_EDIT))

    @property
    def trapsig(self):
        """ Returns True if signals are trapped by client.

        When set, the client side should translate appropriate
        interrupts/signals to their Telnet equivalent.  (These would be
        IP, BRK, AYT, ABORT, EOF, and SUSP)

        When unset, the client should pass interrupts/signals as their
        normal ASCII values, if desired, or, trapped locally.
        """
        return bool(ord(self.mask) & ord(LMODE_MODE_TRAPSIG))

    @property
    def ack(self):
        """ Returns True if ack bit is set.
        """
        return bool(ord(self.mask) & ord(LMODE_MODE_ACK))

    def soft_tab(self):
        """ When set, the client will expand horizontal tab (\\x09)
            into the appropriate number of spaces.

            When unset, the client should allow horitzontal tab to
            pass through un-modified.
        """
        return bool(ord(self.mask) & ord(LMODE_MODE_SOFT_TAB))

    def lit_echo(self):
        """ When set, non-printable characters are displayed as a literal
            character, allowing control characters to write directly to
            the user's screen.

            When unset, the LIT_ECHO, the client side may echo the character
            in any manner that it desires (fe: '^C' for chr(3)).
        """
        return bool(ord(self.mask) & ord(LMODE_MODE_LIT_ECHO))

    def __str__(self):
        """ Returns string representation of line mode, for debugging """
        if self.mask == bytes([0]):
            return 'basic'
        flags = []
        # we say 'local' to indicate that 'edit' mode means that all
        # input processing is done locally, instead of the obtusely named
        # flag 'edit'
        if self.edit:
            flags.append('local')
        if self.trapsig:
            flags.append('trapsig')
        if self.soft_tab:
            flags.append('soft_tab')
        if self.lit_echo:
            flags.append('lit_echo')
        if self.ack:
            flags.append('ack')
        return '|'.join(flags)

class Forwardmask(object):
    def __init__(self, value):
        assert type(value) == bytes and len(value) == 32
        self.value = value

    def __repr__(self):
        """ Return list of terse strings describing the forwardmask
            bytes as their binary keyboard-mapped values.
        """
        result = []
        nil8 = _bin8(0)
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
                row = '[%2d] %s' % (mask, nil8,)
                if not same_as_last(row) or is_last(mask):
                    result.append(row)
                else:
                    result.append(MRK_CONT)
            else:
                start = mask * 8
                last = start + 7
                characters = ', '.join([ _name_char(char)
                    for char in range(start, last + 1)
                    if self.__contains__(char)])
                result.append ('[%2d] %s %s' % (
                    mask, _bin8(byte), characters,))
        return result

    def __str__(self):
        """ Display forwardmask as single 256-bit binary string
        """
        return '0b%s' % (''.join([value for (prefix, value) in [
            _bin8(byte).split('b') for byte in self.value]]),)

    def __contains__(self, number):
        """ Returns True if 8-bit character, by value ``number``,
            is forwarded by this mask.
        """
        mask, flag = number // 8, 2 ** (7 - (number % 8))
        return bool(self.value[mask] & flag)

class Option(dict):
    def __init__(self, name, log=logging, *args):
        self.name, self.log = name, log
        dict.__init__(self, *args)

    def __setitem__(self, key, value):
        if value != dict.get(self, key, None):
            descr = ' + '.join([_name_command(bytes([byte])) for byte in key])
            self.log.debug('%s[%s] = %s', self.name, descr, value,)
        dict.__setitem__(self, key, value)

class TelnetStreamReader(tulip.StreamReader):
    """
    This differs from StreamReader by processing bytes for telnet protocols.
    Handles all of the option negotiation and various sub-negotiations.

    Attributes::
     * ``pending_option`` is a dict of <opt> bytes that follow an IAC DO or
       DONT command, and contains a value of ``True`` until an IAC WILL or
       WONT has been received by remote end. Requests that expect an IAC SB
       Sub-negotiation reply are keyed by two bytes, SB + <opt>.
     * ``local_option`` is a dict of <opt> bytes that follow an IAC WILL or
       WONT command sent by local end to indicate local capability.  For
       example, if local_option[ECHO] is True, then this server should echo
       input received from client.
     * ``remote_option`` is a dict of <opt> bytes that follow an IAC WILL or
       WONT command received by remote end to indicate remote capability. For
       example, if remote_option[NAWS] (Negotiate about window size) is True,
       then the window dimensions of the remote client may be determined.
     * ``request_env`` is a list of terminal environment variables requested
       by the server after a client agrees to negotiate NEW_ENVIRON.
     * ``lflow_any`` is a boolean to indicate wether flow control should be
       disabled after it has been enbaled using XON (^s) when: any key is
       pressed (True), or only when XOFF (^q) is pressed (False, default).

    Because Server and Client support different capabilities, the mutually
    exclusive booleans ``client`` and ``server`` indicates which end the
    protocol is attached to. The default is *server*, meaning, this stream
    is attached to a server end, reading from a telnet client.
    """
    request_env = (
            "USER HOSTNAME UID TERM COLUMNS LINES DISPLAY LANG "
            "SYSTEMTYPE ACCT JOB PRINTER SFUTLNTVER SFUTLNTMODE").split()
    lflow_any = False
    forwardmask = None

    # state variables to track and assert command negotiation and response.
    _iac_received = False   # has IAC been recv?
    _slc_received = False   # has SLC value been received?
    _cmd_received = False   # has IAC (DO, DONT, WILL, WONT) been recv?
    _sb_received = False    # has IAC SB been recv?
    _tm_sent = False        # has IAC DO TM been sent?
    _dm_recv = False        # has IAC DM been recv?

    def __init__(self, transport, client=False, server=False,
                 debug=False, log=logging):
        """ Stream is decoded as a Telnet Server, unless
            keyword argument ``client`` is set to ``True``.
        """
        assert client is False or server is False, (
            "Arguments 'client' and 'server' are mutually exclusive")
        self.log, self.debug = (log, debug)
        self.transport = transport
        self.server = (client in (None, False) or server in (None, True))
        self._sb_buffer = collections.deque()
        self._slc_buffer = collections.deque()
        self._linemode = Linemode(bytes([0]))
        self._init_options()
        self._default_callbacks()
        self._default_slc()
        tulip.StreamReader.__init__(self)

    def _init_options(self):
        self.pending_option = Option('pending_option', self.log)
        self.local_option = Option('local_option', self.log)
        self.remote_option = Option('remote_option', self.log)

    def _default_callbacks(self):
        """ set default callback dictionaries ``_iac_callbacks``,
            ``_slc_callbacks``, and ``_ext_callbacks`` to default methods of
            matching names, such that IAC + <IP>, or, the SLC value negotiated
            for SLC_<IP>, signals a callback to method ``self.handle_<ip>``.
        """
        self._iac_callbacks = {}
        self._slc_callbacks = {}
        self._ext_callbacks = {}

        for iac_cmd, key in DEFAULT_IAC_CALLBACKS:
            self.set_iac_callback(iac_cmd, getattr(self, 'handle_%s' % (key,)))
        for slc_cmd, key in DEFAULT_SLC_CALLBACKS:
            self.set_slc_callback(slc_cmd, getattr(self, 'handle_%s' % (key,)))
        for ext_cmd, key in DEFAULT_EXT_CALLBACKS:
            self.set_ext_callback(ext_cmd, getattr(self, 'handle_%s' % (key,)))

    def _default_slc(self, tabset=DEFAULT_SLC_TAB):
        """ set property ``_slctab`` to default SLC tabset, unless it
            is unlisted (as is the case for SLC_MCL+), then set as
            SLC_NOSUPPORT _POSIX_VDISABLE (0xff) which incidentently
            is also IAC, and must be escaped as (0xff, 0xff) when sent.

            ``_slctab`` is a dictionary of SLC functions, such as SLC_IP,
            to a tuple of the handling character and support level.
        """
        self._slctab = {}
        for slc in range(NSLC + 1):
            self._slctab[bytes([slc])] = DEFAULT_SLC_TAB.get(bytes([slc]),
                    SLC_definition(SLC_NOSUPPORT, _POSIX_VDISABLE))

    def set_iac_callback(self, cmd, func):
        """ Register ``func`` as callback for receipt of IAC command ``cmd``.

            BRK, IP, AO, AYT, EC, EL, EOR, EOF, SUSP, ABORT, and NOP.

            These callbacks receive no arguments.

            _iac_callbacks is a dictionary keyed by telnet command byte,
            and its handling function.
        """
        assert callable(func), ('Argument func must be callable')
        self._iac_callbacks[cmd] = func

    def set_slc_callback(self, slc, func):
        """ Register ``func`` as callback for receipt of SLC character
        negotiated for the SLC command ``slc``.

            SLC_SYNCH, SLC_BRK, SLC_IP, SLC_AO, SLC_AYT, SLC_EOR, SLC_ABORT,
            SLC_EOF, SLC_SUSP, SLC_EC, SLC_EL, SLC_EW, SLC_RP, SLC_XON,
            SLC_XOFF, (...)

            These callbacks receive no arguments.

            _slc_callbacks is a dictionary keyed by telnet command byte,
            and its handling function.
            """
        assert callable(func), ('Argument func must be callable')
        assert (type(slc) == bytes and
                0 < ord(slc) < NSLC + 1), ('Uknown SLC byte: %r' % (slc,))
        self._slc_callbacks[slc] = func

    def set_ext_callback(self, cmd, func):
        """ Register ``func`` as callback for subnegotiation result of ``cmd``.

        cmd must be one of: TTYPE, TSPEED, XDISPLOC, NEW_ENVIRON, or NAWS.

        These callbacks may receive a number of arguments.

        Callbacks for ``TTYPE`` and ``XDISPLOC`` receive a single argument
        as a bytestring. ``NEW_ENVIRON`` and ``OLD_ENVIRON`` receive a
        single argument as dictionary. ``NAWS`` receives two integer
        arguments (width, height), and ``TSPEED`` receives two integer
        arguments (rx, tx).
        """
        assert cmd in (TTYPE, TSPEED, XDISPLOC, NEW_ENVIRON, NAWS)
        assert callable(func), ('Argument func must be callable')
        self._ext_callbacks[cmd] = func

    @property
    def is_linemode(self):
        """ Returns true if telnet stream appears to be in any sort of
            linemode.

            The default Network Terminal is always in linemode, unless
            explicitly set False (client sends: WONT, LINEMODE),
            or implied by server (server sends: WILL ECHO, WILL SGA).
        """
        if self.server:
            return self.remote_option.get(LINEMODE, False) or not (
                    self.local_option.get(ECHO, None) and
                    self.local_option.get(SGA, None))
        # same heuristic is reversed for client point of view,
        # XXX
        return self.local_option.get(LINEMODE, None) or (
                self.remote_option.get(ECHO, None) and
                self.remote_option.get(SGA, None))

    @property
    def linemode(self):
        """ Returns a Linemode instance, which may be tested by its boolean
            attributes ``edit``, ``trapsig``, ``soft_tab``, ``lit_echo``.

            Returns Returns None if ``is_linemode()`` is False (character
            at a time mode).
        """
        return (self._linemode if self.is_linemode else None)

    @property
    def is_server(self):
        """ Returns True if telnet stream is used for the server end. """
        return bool(self.server)

    @property
    def is_client(self):
        """ Returns True if telnet stream is used for the client end. """
        return bool(not self.server)

    @property
    def idle(self):
        """ Return time since bytes last received by remote end. This
            includes telnet commands, such as IAC + NOP. """
        return time.time() - self._last_input_time

    def write(self, data, oob=False):
        """ Write data bytes to transport end connected to stream reader.

            IAC is always escaped with IAC+IAC, appropriate for in-band
            data bytes and some out-of-band sub-negotiations that include
            \xff (SLC function NOSUPPORT, for instance).

            Inband data must be 7-bit ascii unless WILL BINARY has been sent,
            and replyed by DO. If ``oob`` is set ``True``, data is considered
            out-of-band and may set high bit.
        """
        assert isinstance(data, bytes), (
                'Expected bytes, got %s' % (type(data),))
        if not oob and not self.local_option.get(BINARY, None):
            for pos, byte in enumerate(data):
                assert byte < 128, (
                        '8-bit ascii at pos %d not valid for ascii, '
                        'Send IAC WILL BINARY first.')
        self.transport.write(_escape_iac(data))

    def write_iac(self, data):
        """ Write IAC data bytes to transport end connected to stream reader.

            IAC is never escaped, appropriate for out-of-band telnet commands.
        """
        self.transport.write(data)

    def ga(self):
        """ Send IAC GA (Go-Ahead) if SGA is declined.  Otherwise,
            nothing happens.

            "we would like to lobby for suppress GA the default. It appears
            that only a few Hosts require the GA's (AMES-67 and UCLA-CON)."
        """
        if not self.local_option.get(SGA, True):
            self.write_iac(IAC + GA)

    def iac(self, cmd, opt):
        """ Send IAC <cmd> <opt> to remote end.

        Various RFC assertions are made to assure only legal commands for
        client or server are sent, and is appropriate for the state of
        option processing, and registration of pending options are tracked.
        To prevent telnet loops, any previously pending option will not
        again be requested if a pending reply is already awaiting.
        """
        assert cmd in (DO, DONT, WILL, WONT), (
            'Illegal IAC cmd, %r.' % (cmd,))
        if opt == LINEMODE:
            if cmd == DO and not self.server:
                raise ValueError('DO LINEMODE may only be sent by server.')
            if cmd == WILL and self.server:
                raise ValueError('WILL LINEMODE may only be sent by client.')
        if opt == TM:
            # DO TM has special state tracking; bytes are thrown
            # away by sender of DO TM until replied by WILL or WONT TM.
            if cmd == DO:
                self._tm_sent = True
        elif cmd in (DO, DONT, WILL, WONT):
            if self.pending_option.get(cmd + opt, False):
                self.log.debug('skip %s + %s; pending_option = True',
                    _name_command(cmd), _name_command(opt))
                return
            self.pending_option[cmd + opt] = True
        elif cmd == WILL and not self.local_option.get(opt, None):
            self.local_option[opt] = True
        elif(cmd == WONT and self.local_option.get(opt, None) != False):
            self.local_option[opt] = False
        self.write_iac(IAC + cmd + opt)
        self.log.debug('send IAC %s %s' % (
            _name_command(cmd), _name_command(opt),))

    def feed_byte(self, byte):
        """ Receive byte arrived by ``TelnetProtocol.data_received()``.

        Copy bytes from ``data`` into ``self.buffer`` through a state-logic
        flow, detecting and handling telnet commands and negotiation options.

        Returns True if byte is part of an Out-of-Band sequence, and should
        not be echoed when DO ECHO has been requested by client.
        """
        assert type(byte) == bytes and len(byte) == 1
        self.byte_count += 1
        self._last_input_time = time.time()
        return self._parser(byte)

    def _parser(self, byte):
        """ Process all telnet bytes, a single byte at a time.

        Tracks state, and when out-of-band data, marked by byte IAC
        arrives, susbsequent bytes toggle or process negotiation through
        callbacks.

        Returns True if out of band data was handled, otherwise False.

        Extending or changing protocol capabilities shouldn't necessarily
        require deriving this method, but the methods it delegates to, mainly
        those methods beginning with 'handle_', or ``parse_iac_command``,
        and ``parse_subnegotiation``.

        As this parse receives a single byte at a time, active states are
        stored as booleans ``_iac_received``, ``_sb_received``,
        ``_dm_recv``, ``_tm_sent``, and behaves on in-band command data
        accordingly.

        The Value of ``_cmd_received`` is a basic telnet command byte and
        is non-None when that state is active.

        Negotiated options are stored in dict ``self.local_option``,
        and ``self.remote_option``.

        Pending replies are noted with ``self.pending_option``, keyed
        by one or more option bytes. the _negotiate() callback loop awaits
        replies for any pending options set True in this dictionary.
        """
        # _slc_received toggled true if inband character matches
        # a previously negotiated linemode SLC function value.
        self._slc_received = False
        oob = True
        if byte == IAC:
            self._iac_received = (not self._iac_received)
            if not self._iac_received:
                # we received an escaped IAC, but does it get
                # placed into main buffer or SB buffer?
                if self._sb_received:
                    self._sb_buffer.append(IAC)
                else:
                    self.buffer.append(IAC)

        elif self._iac_received:
            # with IAC already received parse the 2nd byte,
            cmd = byte
            if cmd in (DO, DONT, WILL, WONT):
                self._cmd_received = cmd
            elif cmd == SB:
                self._sb_received = True
            elif cmd == SE:
                try:
                    self.parse_subnegotiation(self._sb_buffer)
                finally:
                    self._sb_buffer.clear()
                self._sb_received = False
            else:
                self.parse_iac_command(byte)
            self._iac_received = False

        elif self._sb_received:
            # with IAC SB mark received, buffer until IAC SE.
            self._sb_buffer.append(byte)
            if len(self._sb_buffer) > SB_MAXSIZE:
                self.log.error('SB: buffer full')
                self._sb_buffer.clear()
                # remaining data becomes in-band
                self._sb_received = False

        elif self._cmd_received:
            # parse IAC DO, DONT, WILL, and WONT responses.
            cmd, opt = self._cmd_received, byte
            self.log.debug('recv IAC %s %s' % (
                _name_command(cmd), _name_command(opt),))
            if self._cmd_received == DO:
                self.handle_do(opt)
                if self.pending_option.get(WILL + opt, False):
                    self.pending_option[WILL + opt] = False
                if not self.local_option.get(opt, False):
                    self.local_option[opt] = True
            elif self._cmd_received == DONT:
                self.handle_dont(opt)
                if self.pending_option.get(WILL + opt, False):
                    self.pending_option[WILL + opt] = False
                if self.local_option.get(opt, True):
                    self.local_option[opt] = False
            elif self._cmd_received == WILL:
                if not self.pending_option.get(DO + opt):
                    self.log.debug('received unnegotiated WILL')
                    assert opt in (LINEMODE,), (
                            'Received WILL %s without corresponding DO' % (
                                _name_command(opt),))
                self.handle_will(opt)
                if self.pending_option.get(DO + opt, False):
                    self.pending_option[DO + opt] = False
                if self.pending_option.get(DONT + opt, False):
                    # This end previously requested remote end *not* to
                    # perform a capability, but remote end has replied
                    # with a WILL. Occurs due to poor timing at negotiation
                    # time. DO STATUS is often used to settle the difference.
                    self.pending_option[DONT + opt] = False
            elif self._cmd_received == WONT:
                self.handle_wont(opt)
                if self.pending_option.get(DO + opt, False):
                    self.pending_option[DO + opt] = False
                if self.pending_option.get(DONT + opt, False):
                    self.pending_option[DONT + opt] = False
            self._cmd_received = False
        elif self._dm_recv:
            # IAC DM was previously received; discard all input until
            # IAC DM is received again by remote end.
            self.log.debug('discarded by data-mark: %r' % (byte,))
        elif self._tm_sent:
            # IAC DO TM was previously sent; discard all input until
            # IAC WILL TM or IAC WONT TM is received by remote end.
            self.log.debug('discarded by timing-mark: %r' % (byte,))
        elif self.remote_option.get(LINEMODE, None):
            # inband data is tested for SLC characters when LINEMODE is True
            (callback, slc_name, slc_def) = self._slc_snoop(byte)
            if slc_name is not None:
                self.log.debug('_slc_snoop(%r): %s, callback is %s.',
                        byte, _name_slc_command(slc_name), callback.__name__)
                if slc_def.flushin:
                    # SLC_FLUSHIN not supported, requires SYNCH (urgent TCP).
                    #self.send_synch() XXX
                    pass
                if slc_def.flushout:
                    self.iac(WILL, TM)
                # allow caller to know which SLC function caused linemode
                # to process, even though CR was not yet discovered.
                self._slc_received = slc_name
            self.buffer.append(byte)
            if callback is not None:
                callback()
            # standard inband data unless an SLC function was recv
            oob = bool(self._slc_received)
        else:
            # standard inband data
            self.buffer.append(byte)
            oob = False
        return oob

    def _slc_snoop(self, byte):
        """
        Scan ``self._slctab`` for matching byte values.
        If any are discovered, the (callback, func_byte, slc_definition)
        is returned.

        Otherwise (None, None, None) is returned.
        """
        # scan byte for SLC function mappings, if any, return function
        for slc_func, slc_def in self._slctab.items():
            if byte == slc_def.val and slc_def.val != b'\x00':
                callback = self._slc_callbacks.get(slc_func, None)
                return (callback, slc_func, slc_def)
        return (None, None, None)

    def parse_iac_command(self, cmd):
        """ Handle IAC commands, calling self.handle_<cmd> where <cmd> is
        one of 'brk', 'ip', 'ao', 'ayt', 'ec', 'el', 'eor', 'eof', 'susp',
        or 'abort', if exists. Otherwise unhandled. Callbacks can be
        re-directed or extended using the ``set_iac_callback(cmd, func)``
        method.
        """
        if cmd in self._iac_callbacks:
            self._iac_callbacks[cmd]()
        else:
            raise ValueError('unsupported IAC sequence, %r' % (cmd,))

    def parse_subnegotiation(self, buf):
        """ Callback containing the sub-negotiation buffer. Called after
        IAC + SE is received, indicating the end of sub-negotiation command.

        SB options TTYPE, XDISPLOC, NEW_ENVIRON, NAWS, and STATUS, are
        supported. Changes to the default responses should derive callbacks
        ``handle_ttype``, ``handle_xdisploc``, ``handle_env``, and
        ``handle_naws``, or set their own callbacks using set.

        Implementors of additional SB options should extend this method. """
        assert buf, ('SE: buffer empty')
        assert buf[0] != b'\x00', ('SE: buffer is NUL')
        assert len(buf) > 1, ('SE: buffer too short: %r' % (buf,))
        cmd = buf[0]
        if self.pending_option.get(SB + cmd, False):
            self.pending_option[SB + cmd] = False
        else:
            self.log.debug('[SB + %s] unsolicited', _name_command(cmd))
        if cmd == LINEMODE:
            assert self.server, ('SE: received from server: LINEMODE')
            self._handle_sb_linemode(buf)
        elif cmd == LFLOW:
            assert self.server, ('SE: cannot recv from server: LFLOW')
            self._handle_sb_lflow(buf)
        elif cmd == NAWS:
            assert self.server, ('SE: cannot recv from server: NAWS')
            self._handle_sb_naws(buf)
        elif cmd == NEW_ENVIRON:
            assert self.server, ('SE: cannot recv from server: NEW_ENVIRON')
            self._handle_sb_newenv(buf)
        elif (cmd, buf[1]) == (TTYPE, IS):
            assert self.server, ('SE: cannot recv from server: TTYPE IS')
            self._handle_sb_ttype(buf)
        elif (cmd, buf[1]) == (TSPEED, IS):
            assert self.server, ('SE: cannot recv from server: TSPEED IS')
            self._handle_sb_tspeed(buf)
        elif (cmd, buf[1]) == (XDISPLOC, IS):
            assert self.server, ('SE: cannot recv from server: XDISPLOC IS')
            self._handle_sb_xdisploc(buf)
        elif (cmd, buf[1]) == (STATUS, SEND):
            assert len(buf) == 2, ('SE: STATUS SEND size mismatch')
            self._send_status()
        else:
            raise ValueError('SE: unhandled: %r' % (buf,))

    def _handle_sb_tspeed(self, buf):
        assert buf.popleft() == TSPEED
        assert buf.popleft() == IS
        rx, tx = str(), str()
        while len(buf):
            value = buf.popleft()
            if value == b',':
                break
            rx += value.decode('ascii')
        while len(buf):
            value = buf.popleft()
            if value == b',':
                break
            tx += value.decode('ascii')
        self.log.debug('sb_tspeed: %s, %s', rx, tx)
        self._ext_callbacks[TSPEED](int(rx), int(tx))

    def _handle_sb_xdisploc(self, buf):
        assert buf.popleft() == XDISPLOC
        assert buf.popleft() == IS
        xdisploc_str = b''.join(buf).decode('ascii')
        self.log.debug('sb_xdisploc: %s', xdisploc_str)
        self._ext_callbacks[XDISPLOC](xdisploc_str)

    def _handle_sb_ttype(self, buf):
        assert buf.popleft() == TTYPE
        assert buf.popleft() == IS
        ttype_str = b''.join(buf).decode('ascii')
        self.log.debug('sb_ttype: %s', ttype_str)
        self._ext_callbacks[TTYPE](ttype_str)

    def _handle_sb_newenv(self, buf):
        assert buf.popleft() == NEW_ENVIRON
        env = dict()
        chk_byte = buf.popleft()
        if not chk_byte in bytes([0, 2]):
            raise ValueError('Expected IS or INFO after IAC SB NEW_ENVIRON, '
                             'got %s' % (_name_command(chk_byte),))
        breaks = list([idx for (idx, byte) in enumerate(buf)
                       if byte in (b'\x00', b'\x03')])
        for start, end in zip(breaks, breaks[1:]):
            # not the best looking code, how do we splice & split bytes ..?
            decoded = bytes([ord(byte) for byte in buf]).decode('ascii')
            pair = decoded[start + 1:end].split('\x01', 1)
            if 2 == len(pair):
                key, value = pair
                env[key] = value
        self.log.debug('sb_env: %r', env)
        self._ext_callbacks[NEW_ENVIRON](env)

    def _handle_sb_naws(self, buf):
        assert buf.popleft() == NAWS
        columns = str((256 * ord(buf[0])) + ord(buf[1]))
        rows = str((256 * ord(buf[2])) + ord(buf[3]))
        self.log.debug('sb_naws: %s, %s', int(columns), int(rows))
        self._ext_callbacks[NAWS](int(columns), int(rows))

    def _handle_sb_lflow(self, buf):
        """ Handle receipt of (IAC, SB, LFLOW).
        """ # XXX
        assert buf.popleft() == LFLOW
        assert self.local_option.get(LFLOW, None) is True, (
            'received IAC SB LFLOW wihout IAC DO LFLOW')
        self.log.debug('sb_lflow: %r', buf)


    def send_linemode(self, linemode=None):
        """ Request the client switch to linemode ``linemode``, an
        of the Linemode class, or self._linemode by default.
        """
        assert self.is_server, (
                'SB LINEMODE LMODE_MODE cannot be sent by client')
        assert self.remote_option.get(LINEMODE, None), (
                'SB LINEMODE LMODE_MODE cannot be sent; '
                'WILL LINEMODE not received.')
        linemode = self._linemode if linemode is None else linemode
        self.write_iac(IAC + SB + LINEMODE)
        self.write_iac(LMODE_MODE + linemode.mask)
        self.write_iac(IAC + SE)
        self.log.debug('sent IAC SB LINEMODE MODE %s IAC SE', linemode)


    def _handle_sb_linemode(self, buf):
        assert buf.popleft() == LINEMODE
        cmd = buf.popleft()
        if cmd == LMODE_MODE:
            self._handle_sb_linemode_mode(buf)
        elif cmd == LMODE_SLC:
            self._handle_sb_linemode_slc(buf)
        elif cmd in (DO, DONT, WILL, WONT):
            opt = buf.popleft()
            self.log.debug('recv SB LINEMODE %s FORWARDMASK%s.',
                    _name_command(cmd), '(...)' if len(buf) else '')
            assert opt == LMODE_FORWARDMASK, (
                    'Illegal byte follows IAC SB LINEMODE %s: %r, '
                    ' expected LMODE_FORWARDMASK.' (_name_command(cmd), opt))
            self._handle_sb_forwardmask(cmd, buf)
        else:
            raise ValueError('Illegal IAC SB LINEMODE command, %r',
                _name_command(cmd),)

    def _handle_sb_linemode_mode(self, buf):
        assert len(buf) == 1
        self._linemode = Linemode(buf[0])
        self.log.info('linemode is %s.' % (self.linemode,))

    def _handle_sb_linemode_slc(self, buf):
        """ Process and reply to linemode slc command function triplets. """
        assert 0 == len(buf) % 3, ('SLC buffer must be byte triplets')
        self._slc_start()
        while len(buf):
            func = buf.popleft()
            flag = buf.popleft()
            value = buf.popleft()
            self._slc_process(func, SLC_definition(flag, value))
        self._slc_end()

    def _handle_sb_forwardmask(self, cmd, buf):
        # set and report about pending options by 2-byte opt,
        if self.is_server:
            assert self.remote_option.get(LINEMODE, None), (
                    'cannot recv LMODE_FORWARDMASK %s (%r) '
                    'without first sending DO LINEMODE.' % (cmd, buf,))
            assert cmd not in (DO, DONT), (
                    'cannot recv %s LMODE_FORWARDMASK on server end',
                    _name_command(cmd,))
        if self.is_client:
            assert self.local_option.get(LINEMODE, None), (
                    'cannot recv %s LMODE_FORWARDMASK without first '
                    ' sending WILL LINEMODE.')
            assert cmd not in (WILL, WONT), (
                    'cannot recv %s LMODE_FORWARDMASK on client end',
                    _name_command(cmd,))
            assert cmd not in (DONT) or len(buf) == 0, (
                    'Illegal bytes follow DONT LMODE_FORWARDMASK: %r' % (
                        buf,))
            assert cmd not in (DO) and len(buf), (
                    'bytes must follow DO LMODE_FORWARDMASK')

        # unset pending replies for reciept of WILL, WONT
        if cmd in (WILL, WONT):
            if self.pending_option.get(LMODE_FORWARDMASK, None):
                self.pending_option[LMODE_FORWARDMASK] = False
            else:
                self.log.debug('FORWARDMASK WILL/WONT unsolicited')
            self.remote_option[LMODE_FORWARDMASK] = cmd is WILL
        elif cmd == DO:
            self._handle_do_forwardmask(buf)
        elif cmd == DONT:
            self._handle_dont_forwardmask()

    def _handle_dont_forwardmask(self):
        """ Handles receipt of SB LINEMODE DONT FORWARDMASK
        """
        self.local_option[LMODE_FORWARDMASK] = False


    def _handle_do_forwardmask(self, buf):
        """ Handles buffer received in SB LINEMODE DO FORWARDMASK <buf>
        """ # XXX UNIMPLEMENTED: ( received on client )
        self.remote_option[LMODE_FORWARDMASK] = True
        pass


    def send_do_forwardmask(self):
        """ Sends SLC Forwardmask appropriate for the currently registered
        ``self._slctab`` to the client end.
        """
        opt = LMODE_FORWARDMASK
        opt_desc = 'SB + LINEMODE + DO + LMODE_FORWARDMASK'
        assert self.is_server, (
                '%s may only be sent by server end' % (opt_desc,))
        assert self.remote_option.get(LINEMODE, None), (
                'cannot send %s without first receiving '
                'IAC WILL LINEMODE.' % (opt_desc,))
        if self.pending_option.get(opt, False):
            self.log.warn('%s request is already pending.', opt_desc)
            return
        elif self.remote_option.get(opt, None) is False:
            self.log.warn('%s request previously declined.', opt_desc)
            return
        self.pending_option[opt] = True
        self.write_iac(IAC + SB + LINEMODE + DO + LMODE_FORWARDMASK)
        self.write(self.forwardmask.value, oob=True)
        self.log.debug('send IAC SB LINEMODE DO LMODE_FORWARDMASK,')
        for maskbit_descr in self.forwardmask.__repr__():
            self.log.debug(maskbit_descr)
        self.write_iac(IAC + SE)

    @property
    def forwardmask(self):
        """
            Forwardmask is formed by a 32-byte representation of all 256
            possible 8-bit keyboard input characters, or, when DONT BINARY
            has been transmitted, a 16-byte 7-bit representation, and whether
            or not they should be "forwarded" by the client on the transport
            stream.

            Characters requested for forwarded are any bytes matching a
            supported SLC function byte in self._slctab.

            The return value is an instance of ``Forwardmask``, which can
            be tested by using the __contains__ method::

                if b'\x03' in stream.linemode_forwardmask:
                    stream.write(b'Press ^C to exit.\r\n')
        """
        if self.local_option.get('BINARY', None) is False:
            num_bytes, msb = 16, 127
        else:
            num_bytes, msb = 32, 256
        mask32 = [b'\x00'] * num_bytes
        for mask in range(msb // 8):
            start = mask * 8
            last = start + 7
            byte = b'\x00'
            for char in range(start, last + 1):
                (func, slc_name, slc_def) = self._slc_snoop(bytes([char]))
                if func is not None and not slc_def.nosupport:
                    # set bit for this character, it is a supported slc char
                    byte = bytes([ord(byte) | 1])
                if char != last:
                    # shift byte left for next character,
                    # except for the final byte.
                    byte = bytes([ord(byte) << 1])
            mask32[mask] = byte
        return Forwardmask(b''.join(mask32))

# `````````````````````````````````````````````````````````````````````````````
# LINEMODE, translated from bsd telnet

    def _slc_end(self):
        """ Send any SLC pending SLC changes sotred in _slc_buffer """
        if 0 == len(self._slc_buffer):
            self.log.debug('slc_end: IAC SE')
        else:
            self.write(b''.join(self._slc_buffer), oob=True)
            self.log.debug('slc_end: (%r) IAC SE', b''.join(self._slc_buffer))
        self.write_iac(IAC + SE)
        self._slc_buffer.clear()

    def _slc_start(self):
        """ Send IAC SB LINEMODE SLC header """
        self.write_iac(IAC + SB + LINEMODE + LMODE_SLC)
        self.log.debug('slc_start: IAC + SB + LINEMODE + SLC')

    def _slc_send(self):
        """ Send all special characters that are supported """
        send_count = 0
        for func in range(NSLC + 1):
            if self._slctab[bytes([func])].nosupport:
                continue
            if func is 0 and not self.is_server:
                # only the server may send an octet with the first
                # byte (func) set as 0 (SLC_NOSUPPORT).
                continue
            self._slc_add(bytes([func]))
            send_count += 1
        self.log.debug('slc_send: %d', send_count)

    def _slc_add(self, func, slc_def=None):
        """ buffer slc triplet response as (function, flag, value),
            for the given SLC_func byte and slc_def instance providing
            byte attributes ``flag`` and ``val``. If no slc_def is provided,
            the slc definition of ``_slctab`` is used by key ``func``.
        """
        assert len(self._slc_buffer) < SLC_MAXSIZE, ('SLC: buffer full')
        if slc_def is None:
            slc_def = self._slctab[func]
        self.log.debug('_slc_add (%s, %s)',
            _name_slc_command(func), slc_def)
        self._slc_buffer.extend([func, slc_def.mask, slc_def.val])

    def _slc_process(self, func, slc_def):
        """ Process an SLC definition provided by remote end.

            Ensure the function definition is in-bounds and an SLC option
            we support. Store SLC_VARIABLE changes to self._slctab, keyed
            by SLC byte function ``func``.

            The special definition (0, SLC_DEFAULT|SLC_VARIABLE, 0) has the
            side-effect of replying with a full slc tabset, resetting to
            the default tabset, if indicated.  """
        self.log.debug('_slc_process %s mine=%s, his=%s',
                _name_slc_command(func), self._slctab[func], slc_def)

        # out of bounds checking
        if ord(func) > NSLC:
            self.log.warn('SLC not supported (out of range): (%r)', func)
            self._slc_add(func, SLC_nosupport())
            return

        # process special request
        if b'\x00' == func:
            if slc_def.level == SLC_DEFAULT:
                # client requests we send our default tab,
                self.log.info('SLC_DEFAULT')
                self._default_slc()
                self._slc_send()
            elif slc_def.level == SLC_VARIABLE:
                # client requests we send our current tab,
                self.log.info('SLC_VARIABLE')
                self._slc_send()
            else:
                self.log.warn('func(0) flag expected, got %s.', slc_def)
            return

        # evaluate slc
        mylevel, myvalue = (self._slctab[func].level, self._slctab[func].val)
        if slc_def.level == mylevel and myvalue == slc_def.val:
            self.log.debug('slc levels final: equal values')
            return
        elif slc_def.level == mylevel and slc_def.ack:
            self.log.debug('slc final final: ack bit set')
            return
        elif slc_def.ack:
            self.log.debug('slc value mismatch with ack bit set: (%r,%r)',
                    myvalue, slc_def.val)
            return
        else:
            self._slc_change(func, slc_def)

    def _slc_change(self, func, slc_def):
        """ Update SLC tabset with SLC definition provided by remote end.

            Modify prviate attribute ``_slctab`` appropriately for the level
            and value indicated, except for slc tab functions of SLC_NOSUPPORT.

            Reply as appropriate ..
        """
        hislevel, hisvalue = slc_def.level, slc_def.val
        mylevel, myvalue = self._slctab[func].level, self._slctab[func].val
        if hislevel == SLC_NOSUPPORT:
            # client end reports SLC_NOSUPPORT; use a
            # nosupport definition with ack bit set
            self._slctab[func] = SLC_nosupport()
            self._slctab[func].set_flag(SLC_ACK)
            self._slc_add(func)
            self.log.debug('hislevel == SLC_NOSUPPORT')
            return

        if hislevel == SLC_DEFAULT:
            # client end requests we use our default level
            if mylevel == SLC_DEFAULT:
                # client end telling us to use SLC_DEFAULT on an SLC we do not
                # support (such as SYNCH). Set flag to SLC_NOSUPPORT instead
                # of the SLC_DEFAULT value that it begins with
                self._slctab[func].set_mask(SLC_NOSUPPORT)
                self.log.debug('slc set to NOSUPPORT')
            else:
                # set current flag to the flag indicated in default tab
                self._slctab[func].set_mask(DEFAULT_SLC_TAB.get(func).mask)
                self.log.debug('slc set to default')
            # set current value to value indicated in default tab
            self._slctab[func].set_value(DEFAULT_SLC_TAB.get(func,
                SLC_nosupport()).val)
            self._slc_add(func)
            return

        # client wants to change to a new value, or,
        # refuses to change to our value, accept their value.
        if b'\x00' != self._slctab[func].val:
            self._slctab[func].set_value(slc_def.val)
            self._slctab[func].set_mask(slc_def.mask)
            slc_def.set_flag(SLC_ACK)
            self._slc_add(func, slc_def)
            self.log.debug('slc set by client %s' % (self._slctab[func],))
            return

        # if our byte value is b'\x00', it is not possible for us to support
        # this request. If our level is default, just ack whatever was sent.
        # it is a value we cannot change.
        if mylevel == SLC_DEFAULT:
            # If our level is default, store & ack whatever was sent
            self._slctab[func].set_mask(slc_def.mask)
            self._slctab[func].set_value(slc_def.val)
            slc_def.set_flag(SLC_ACK)
            self._slc_add(func, slc_def)
            self.log.debug('slc set by client %s' % (self._slctab[func],))
        elif slc_def.level == SLC_CANTCHANGE and mylevel == SLC_CANTCHANGE:
            # "degenerate to SLC_NOSUPPORT"
            self._slctab[func].set_mask(SLC_NOSUPPORT)
            self._slc_add(func)
            self.log.debug('slc cannot change')
        else:
            # mask current level to levelbits (clears ack),
            self._slctab[func].set_mask(self._slctab[func].level)
            if mylevel == SLC_CANTCHANGE:
                self._slctab[func].val = DEFAULT_SLC_TAB.get(
                        func, SLC_nosupport()).val
                self.log.debug('slc cannot change; import default')
            self.log.debug('slc levelbits')
            self._slc_add(func)

# `````````````````````````````````````````````````````````````````````````````
# DO, DONT, WILL, and WONT

    def handle_do(self, opt):
        """ Process byte 3 of series (IAC, DO, opt) received by remote end.

        This method can be derived to change or extend protocol capabilities.
        The result of a supported capability is a response of (IAC, WILL, opt)
        and the setting of ``self.local_option[opt]`` of ``True``.

        For unsupported capabilities, RFC specifies a response of
        (IAC, WONT, opt).  Similarly, set ``self.local_option[opt]``
        to ``False``.
        """
        self.log.debug('handle_do(%s)' % (_name_command(opt)))
        # options that we support
        if opt == ECHO and not self.server:
                raise ValueError('DO ECHO received on client end.')
        elif opt == LINEMODE and self.server:
                raise ValueError('DO LINEMODE received on server end.')
        elif opt == TM:
            # TIMING-MARK is always replied, and is not an 'option'
            self.iac(WILL, TM)
        elif opt in (ECHO, LINEMODE, BINARY, SGA, LFLOW):
            if not self.local_option.get(opt, None):
                self.iac(WILL, opt)
        elif opt == STATUS:
            # IAC DO STATUS is used to obtain request to have server
            # transmit status information. Only the sender of
            # WILL STATUS is free to transmit status information.
            if not self.local_option.get(opt, None):
                self.iac(WILL, STATUS)
            self._send_status()
        else:
            if self.local_option.get(opt, None) is None:
                self.iac(WONT, opt)
            raise ValueError('Unhandled: DO %s.' % (_name_command(opt),))

    def handle_dont(self, opt):
        """ Process byte 3 of series (IAC, DONT, opt) received by remote end.

        This only results in ``self.local_option[opt]`` set to ``False``.
        """
        self.log.debug('handle_dont(%s)' % (_name_command(opt)))
        self.local_option[opt] = False

    def handle_will(self, opt):
        """ Process byte 3 of series (IAC, DONT, opt) received by remote end.

        The remote end requests we perform any number of capabilities. Most
        implementations require an answer in the affirmative with DO, unless
        DO has meaning specific for only client or server end, and
        dissenting with DONT.

        WILL ECHO is only legally received _for clients_, answered with DO.
        WILL NAWS is only legally received _for servers_, answered with DO.
        BINARY and SGA are answered with DO.  STATUS, NEW_ENVIRON, XDISPLOC,
        and TTYPE is answered with sub-negotiation SEND. The env variables
        requested in response to WILL NEW_ENVIRON is specified by list
        ``self.request_env``. All others are replied with DONT.

        The result of a supported capability is a response of (IAC, DO, opt)
        and the setting of ``self.remote_option[opt]`` of ``True``. For
        unsupported capabilities, RFC specifies a response of (IAC, DONT, opt).
        Similarly, set ``self.remote_option[opt]`` to ``False``.  """
        self.log.debug('handle_will(%s)' % (_name_command(opt)))
        if opt == ECHO and self.is_server:
            raise ValueError('cannot recv WILL ECHO on server end')
        elif opt == LINEMODE and not self.is_server:
            raise ValueError('cannot recv WILL LINEMODE on client end')
        elif opt == NAWS and not self.is_server:
            raise ValueError('cannot recv WILL NAWS on client end')
        elif opt == XDISPLOC and not self.is_server:
            raise ValueError('cannot recv WILL XDISPLOC on client end')
        elif opt == TTYPE and not self.is_server:
            raise ValueError('cannot recv WILL TTYPE on client end')
        elif opt == TM and not self._tm_sent:
            raise ValueError('cannot recv WILL TM, must first send DO TM.')
#        elif opt == LFLOW and not self.is_server:
#            raise ValueError('WILL LFLOW not supported on client end')
        elif opt in (BINARY, SGA, ECHO, NAWS, LINEMODE):
            if not self.remote_option.get(opt, None):
                self.remote_option[opt] = True
                self.iac(DO, opt)
            if opt in (NAWS, LINEMODE):
                self.pending_option[SB + opt] = True
                if opt == LINEMODE:
                    # server sets the initial mode and sends forwardmask,
                    self.send_linemode()
                    self.send_do_forwardmask()
        elif opt == TM:
            self.log.debug('WILL TIMING-MARK')
            self._tm_sent = False
        elif opt == STATUS:
            if not self.remote_option.get(opt, None):
                self.remote_option[opt] = True
            self.request_status()
        elif opt == LFLOW:
            if not self.remote_option.get(opt, None):
                self.remote_option[opt] = True
            self.send_lineflow_mode()
        elif opt == NEW_ENVIRON:
            if not self.remote_option.get(opt, None):
                self.remote_option[opt] = True
            self.request_new_environ()
        elif opt == XDISPLOC:
            if not self.remote_option.get(opt, None):
                self.remote_option[opt] = True
            self.request_xdisploc()
        elif opt == TTYPE:
            if not self.remote_option.get(opt, None):
                self.remote_option[opt] = True
            self.request_ttype()
        elif opt == TSPEED:
            if not self.remote_option.get(opt, None):
                self.remote_option[opt] = True
            self.request_tspeed()
        else:
            self.remote_option[opt] = False
            self.iac(DONT, opt)
            raise ValueError('Unhandled: WILL %s.' % (_name_command(opt),))

    def handle_wont(self, opt):
        """ Process byte 3 of series (IAC, WONT, opt) received by remote end.

        (IAC, WONT, opt) is a negative acknolwedgement of (IAC, DO, opt) sent.

        The remote end requests we do not perform a telnet capability.

        It is not possible to decline a WONT. ``T.remote_option[opt]`` is set
        False to indicate the remote end's refusal to perform ``opt``.
        """
        self.log.debug('handle_wont(%s)' % (_name_command(opt)))
        if opt == TM and not self._tm_sent:
            raise ValueError('WONT TM received but DO TM was not sent')
        elif opt == TM:
            self.log.debug('WONT TIMING-MARK')
            self._tm_sent = False
        else:
            self.remote_option[opt] = False

# `````````````````````````````````````````````````````````````````````````````
# Extended Telnet RFC implementations

    def _send_status(self):
# XXX CHECK
        """ Respond after DO STATUS received by DE (rfc859). """
        assert self.pending_option.get(WILL + STATUS, None) is True, (
            u'Only the sender of IAC WILL STATUS may send '
            u'IAC SB STATUS IS.')
        response = collections.deque()
        response.extend([IAC, SB, STATUS, IS])
        for opt, status in self.local_option.items():
            # status is 'WILL' for local option states that are True,
            # and 'WONT' for options that are False.
            response.extend([WILL if status else WONT, opt])
        for opt, status in self.remote_option.items():
            # status is 'DO' for remote option states that are True,
            # or for any DO option requests pending reply. status is
            # 'DONT' for any remote option states that are False,
            # or for any DONT option requests pending reply.
            if status or DO + opt in self.pending_option:
                response.extend([DO, opt])
            elif not status or DONT + opt in self.pending_option:
                response.extend([DONT, opt])
        response.extend([IAC, SE])
        self.log.debug('send: %s', ', '.join([
            _name_command(byte) for byte in response]))
        self.write_iac(bytes([ord(byte) for byte in response]))
        if self.pending_option.get(WILL + STATUS, None):
            self.pending_option[WILL + STATUS] = False

    def _request_sb_newenviron(self):
        """ Request sub-negotiation NEW_ENVIRON, rfc 1572.

            Does nothing if (WILL, NEW_ENVIRON) has not yet been received,
            or an existing SB NEW_ENVIRON SEND request is already pending.
        """
        if not self.remote_option.get(NEW_ENVIRON, None):
            return
        if not self.pending_option.get(SB + NEW_ENVIRON, None):
            self.pending_option[SB + NEW_ENVIRON] = True
            response = collections.deque()
            response.extend([IAC, SB, NEW_ENVIRON, SEND, bytes([0])])
            response.extend(b'\x00'.join(self.request_env))
            response.extend([b'\x03', IAC, SE])
            self.write_iac(bytes([ord(byte) for byte in response]))

    def request_status(self):
        """ Send STATUS, SEND sub-negotiation, rfc859
            Does nothing if (WILL, STATUS) has not yet been received,
            or an existing SB STATUS SEND request is already pending. """
        if not self.remote_option.get(STATUS, None):
            return
        if not self.pending_option.get(SB + STATUS, None):
            self.pending_option[SB + STATUS] = True
            self.write_iac(
                b''.join([IAC, SB, STATUS, SEND, IAC, SE]))
            # set pending for SB STATUS
            self.pending_option[SB + STATUS] = True

    def send_lineflow_mode(self):
        """ Send LFLOW mode sub-negotiation, rfc1372
            Does nothing if (WILL, LFLOW) has not yet been received. """
        if not self.remote_option.get(LFLOW, None):
            return
        mode = LFLOW_RESTART_ANY if self.lflow_any else LFLOW_RESTART_XON
        self.write_iac(
                b''.join([IAC, SB, LFLOW, mode, IAC, SE]))

    def request_tspeed(self):
        """ Send TSPEED, SEND sub-negotiation, rfc1079.
            Does nothing if (WILL, TSPEED) has not yet been received.
            or an existing SB TSPEED SEND request is already pending. """
        if not self.remote_option.get(TSPEED, None):
            return
        if not self.pending_option.get(SB + TSPEED, None):
            self.pending_option[SB + TSPEED] = True
            response = [IAC, SB, TSPEED, SEND, IAC, SE]
            self.log.debug('send: %s', ', '.join([
                _name_command(byte) for byte in response]))
            self.write_iac(b''.join(response))

    def request_new_environ(self):
        """ Send NEW_ENVIRON, SEND, IS sub-negotiation, rfc1086.
            Does nothing if (WILL, NEW_ENVIRON) has not yet been received.
            or an existing SB NEW_ENVIRON SEND request is already pending. """
        if not self.remote_option.get(NEW_ENVIRON, None):
            return
        if not self.pending_option.get(SB + NEW_ENVIRON, None):
            self.pending_option[SB + NEW_ENVIRON] = True
            response = [IAC, SB, NEW_ENVIRON, SEND, IS]
            for idx, env in enumerate(self.request_env):
                response.extend([bytes(char, 'ascii') for char in env])
                if idx < len(self.request_env) - 1:
                    response.append(b'\x00')
            response.extend([b'\x03', IAC, SE])
            self.log.debug('send: %s, %r', ', '.join([
                _name_command(byte) for byte in response[:3]]), response[3:],)
            self.write_iac(b''.join(response))

    def request_xdisploc(self):
        """ Send XDISPLOC, SEND sub-negotiation, rfc1086.
            Does nothing if (WILL, XDISPLOC) has not yet been received.
            or an existing SB XDISPLOC SEND request is already pending. """
        if not self.remote_option.get(XDISPLOC, None):
            return
        if not self.pending_option.get(SB + XDISPLOC, None):
            self.pending_option[SB + XDISPLOC] = True
            response = [IAC, SB, XDISPLOC, SEND, IAC, SE]
            self.log.debug('send: %s', ', '.join([
                _name_command(byte) for byte in response]))
            self.write_iac(b''.join(response))

    def request_ttype(self):
        """ Send TTYPE SEND sub-negotiation, rfc930.
            Does nothing if (WILL, TTYPE) has not yet been received.
            or an existing SB TTYPE SEND request is already pending. """
        if not self.remote_option.get(TTYPE, None):
            return
        if not self.pending_option.get(SB + TTYPE, None):
            self.pending_option[SB + TTYPE] = True
            response = [IAC, SB, TTYPE, SEND, IAC, SE]
            self.log.debug('send: %s', ', '.join([
                _name_command(byte) for byte in response]))
            self.write_iac(b''.join(response))

# `````````````````````````````````````````````````````````````````````````````

    def handle_xdisploc(self, buf):
        """ Receive XDISPLAY using XDISPLOC protocol as string format
            '<host>:<dispnum>[.<screennum>]'.
        """

        pass

    def handle_ttype(self, ttype):
        """ Receive terminal type (TERM on unix systems) as string.
        """
        self.log.debug('Terminal type is %r', ttype)

    def handle_naws(self, width, height):
        """ Receive window size from NAWS protocol as integers.
        """
        self.log.debug('Terminal cols=%d, rows=%d', width, height)

    def handle_env(self, env):
        """ Receive environment variables from NEW_ENVIRON protocol as dict.
        """
        self.log.debug('env=%r', env)

    def handle_tspeed(self, rx, tx):
        """ Receive terminal speed from TSPEED protocol as integers.
        """
        self.log.debug('Terminal Speed rx:%d, tx:%d', rx, tx)

    def handle_ip(self):
        """ Handle Interrupt Process (IAC, IP) or SLC_IP.
        """
        self.log.debug('IAC IP: Interrupt Process')

    def handle_abort(self):
        """ Handle Abort (IAC, ABORT). Similar to Interrupt Process (IP),
            but means only to abort or terminate the process to which the
            NVT is connected.
        """
        self.log.debug('IAC ABORT: Abort')

    def handle_susp(self):
        """ Handle Suspend Process (IAC, SUSP). Suspend the execution of the
            current process attached to the NVT in such a way that another
            process will take over control of the NVT, and the suspended
            process can be resumed at a later time.

            If the receiving system does not support this functionality, it
            should be ignored.
        """
        self.log.debug('IAC SUSP: Suspend')

    def handle_ao(self):
        """ Handle Abort Output (IAC, AO). Discard any remaining output.

            "If the AO were received [...] a reasonable implementation would
            be to suppress the remainder of the text string, *but transmit the
            prompt character and the preceding <CR><LF>*."
        """
        self.log.debug('IAC AO: Abort Output')

    def handle_brk(self):
        """ Handle Break (IAC, BRK). Sent by clients to indicate BREAK
            keypress, or SLC_BREAK key mapping.  This is not the same as
            IP (^c), but a means to map sysystem-dependent break key such
            as found on an IBM Systems.
        """
        self.log.debug('IAC BRK: Break')

    def handle_ayt(self):
        """ Handle Are You There (IAC, AYT). Provides the user with some
            visible (e.g., printable) evidence that the system is still
            up and running.

            Terminal servers that respond to AYT usually print the status
            of the client terminal session, its speed, type, and options.
        """
        self.log.debug('IAC AYT: Are You There?')

    def handle_ec(self):
        """ Handle SLC Erase Character. Provides a function which deletes
            the last preceding undeleted character from data ready on
            current line of input.
        """
        self.log.debug('IAC EC: Erase Character')

    def handle_ew(self):
        """ Handle SLC Erase Word. Provides a function which deletes
            the last preceding undeleted character, and any subsequent
            bytes until next whitespace character from data ready on
            current line of input.
        """
        self.log.debug('IAC EC: Erase Word')

    def handle_rp(self):
        """ Handle SLC Repaint.
        """ # XXX
        self.log.debug('SLC RP: Repaint')

    def handle_lnext(self):
        """ Handle SLC LINE NEXT?
        """ # XXX
        self.log.debug('IAC LNEXT: Line Next')

    def handle_el(self):
        """ Handle Erase Line (IAC, EL). Provides a function which
            deletes all the data ready on current line of input.
        """
        self.log.debug('IAC EL: Erase Line')

    def handle_eor(self):
        """ Handle End of Record (IAC, EOR). rfc885
        """
        self.log.debug('IAC EOR: End of Record')

    def handle_eof(self):
        """ Handle End of Record (IAC, EOF). rfc885
        """
        self.log.debug('IAC EOF: End of File')

    def handle_nop(self):
        """ Callback does nothing when IAC + NOP is received.
        """
        self.log.debug('IAC NOP: Null Operation')

    def handle_dm(self):
        """ Callback toggles ``self._dm_recv``.  when IAC + DM
            or SLC_SYNCH is received. The DM byte is not tested
            for OOB/TCP Urgent, so it is not handled per RFC.
        """
        self._dm_recv = not self._dm_recv
        if self._dm_recv:
            self.log.debug('IAC DM: input ignored until next DM')
        else:
            self.log.debug('IAC DM: no longer ignoring input')

    def handle_xon(self):
        """ Called when IAC + XON or SLC_XON is received.
        """
        self.log.debug('IAC XON: Transmit On')

    def handle_xoff(self):
        """ Called when IAC + XOFF or SLC_XOFF is received.
        """
        self.log.debug('IAC XOFF: Transmit Off')

# `````````````````````````````````````````````````````````````````````````````

class BasicTelnetServer(tulip.protocols.Protocol):
    # toggled when '\r' is seen; for non-BINARY clients, assert that it must
    # be followed by either '\n' or '\0'.
    _carriage_returned = False
    _closing = False

    # the default telnet protocol only supports ascii input or output,
    # meaning bytes 0x7f(127) through 0xff(255) are off-limits!
    encoding = 'ascii'

    # Time period limits for _negotiate().
    CONNECT_MINWAIT = 0.15
    CONNECT_MAXWAIT = 0.50
    CONNECT_DEFERED = 0.1

    def __init__(self, log=logging, debug=False):
        self.log = log
        self.inp_command = collections.deque()
        self.debug = debug

    def connection_made(self, transport):
        self.transport = transport
        self.stream = TelnetStreamReader(transport, server=True, debug=True)
        # IAC + AYT, or ^T signals callback ``display_status``
        self.stream.set_iac_callback(AYT, self.display_status)
        self.stream.set_slc_callback(SLC_AYT, self.display_status)
        self.connect_time = time.time()
        self.banner()
        self._negotiate()

    def banner(self):
        """ XXX

        The banner method is called on-connect, displaying the
        login message, if any, and indicates the desired telnet options.

        The default does not indicate any telnet options, so the client is
        in line-at-a-time mode per RFC.
        """
        self.stream.write(b'Welcome to ')
        self.stream.write(bytes(__file__, 'ascii', 'replace'))
        self.stream.write(b'\r\n')

    def _negotiate(self, call_after=None):
        """
        Negotiate options before prompting for input, this method calls itself
        every CONNECT_DEFERED up to CONNECT_MAXWAIT until all pending_options
        have been negotiated. If maximum time expires, options left
        un-negotiated are displayed as a warning.
        When negotiation period is over, ``prompt()`` is called unless the
        argument ``call_after`` is specified to a callable.
        """
        call_after = self.prompt if call_after is None else call_after
        assert callable(call_after), ('call_after must be callable')
        loop = tulip.get_event_loop()
        wait_min = time.time() - self.connect_time <= self.CONNECT_MINWAIT
        wait_max = time.time() - self.connect_time <= self.CONNECT_MAXWAIT
        if wait_min or any(self.stream.pending_option.values()) and wait_max:
            loop.call_later(self.CONNECT_DEFERED, self._negotiate, call_after)
            return

        self.log.debug(self.transport.get_extra_info('addr', None))
        for option, pending in self.stream.pending_option.items():
            if pending:
                cmd = ' + '.join([
                    _name_command(bytes([byte])) for byte in option])
                self.log.warn('telnet reply not received for "%s"', cmd)
                self.stream.write(bytes('\r\nwarning: no reply received '
                    'for "%s"' % (cmd,), 'ascii'))
        loop.call_soon(call_after)

    def data_received(self, data):
        """ Process all data received on socket, passing each byte through
        TelnetStreamReader.feed_byte(), a state machine, which returns True
        if out-of-band data was processed.

        Otherwise the data is inband, and depending on LINEMODE, is deferred
        to self.handle_input or self.handle_line.
        """
        for byte in (bytes([value]) for value in data):
            oob = self.stream.feed_byte(byte)
            slc = self.stream._slc_received
            if oob and not slc:
                # processed an IAC command,
                continue
            if not self.stream.is_linemode:
                # character-at-a-time mode, handle_input each byte received
                self.handle_input(byte, slc)
                continue
            if slc:
                self.handle_line(slc)
                continue
            if not self._carriage_returned and byte in (b'\x0a', b'\x0d'):
                if not self.stream.local_option.get(BINARY, False):
                    self._carriage_returned = bool(byte == b'\x0d')
                # Carriage return
                self.handle_line()
                continue
            if self._carriage_returned and byte in (b'\x00', b'\x0a'):
                self._carriage_returned = False
                continue
            if self.stream.local_option.get('ECHO', None):
                self.transport.write(byte)
            self.inp_command.append(byte)
            self._carriage_returned = False

    def prompt(self, redraw=False):
        """ Prompts client end for input.  When ``redraw`` is ``True``, the
            prompt is re-displayed at the user's current screen row. GA
            (go-ahead) is signalled if SGA (supress go-ahead) is declined.
        """
        prefix = (b'\r\x1b[K' if redraw  # vt102 clear_eol
                else b'\r\n')
        client_inp = b''.join(self.inp_command)
        prompt_bytes = (prefix, bytes(__file__, 'ascii'), b'$ ', client_inp,)
        self.stream.write(b''.join(prompt_bytes))
        self.stream.ga()

    def handle_input(self, byte, slc=None):
        """ XXX Handle input received on character-at-a-time basis
            The default implementation provides simple line editing.

            If byte is a known SLC character, slc is the SLC function byte.
        """
        self.log_debug('recv: %r (slc=%s)', byte, slc)
        if (self.stream.local_option.get(ECHO, None)
                and byte.decode('ascii').isprintable()):
            self.stream.write(byte)
        if byte.decode('ascii').isprintable():
            self.inp_command.append(byte)
        elif byte in (b'\x0d', b'\x0a'):
            # carriage return
            self.handle_line()
        else:
            self.log_debug('unhandled byte')

    def bell(self):
        """ XXX

            Callback occurs when inband data is not valid during line editing,
            such as SLC EC (^H) at beginning of line.

            Default behavior is to write ASCII BEL to transport, unless
            stream is in character-at-a-time mode, linemode 'edit' is
            enabled, or 'lit_echo' is not enabled.
        """
        if not self.stream.is_linemode or (
                not self.stream.linemode.edit
                and self.stream.linemode.lit_echo):
            self.stream.write(b'\x07')
        self.log.debug('bell')

    def process_cmd(self, cmd):
        """ XXX

            Handle input line received on line-at-a-time basis. The
            default implementation provides simple help, version,
            quit, and status command processing.
        """
        cmd = cmd.rstrip()
        try:
            cmd, *args = shlex.split(cmd)
        except ValueError:
            args = []
        if cmd == 'quit':
            self.stream.write(b'\r\nBye!\r\n')
            self.close()
        elif cmd == 'version':
            self.stream.write(bytes(sys.version, 'ascii'))
        elif cmd == 'help':
            self.display_help(args)
        elif cmd == 'status':
            self.display_status()
        else:
            self.stream.write(b'\r\nCommand ')
            self.stream.write(bytes(repr(cmd), 'ascii'))
            self.stream.write(b', not understood.')

    def display_help(self, *args):
        self.stream.write(b'\r\nAvailable commands: \r\n')
        self.stream.write(b'quit, version, help, status')

    def display_status(self):
        self.stream.write(b'\r\n')
        self.stream.write(b'Linemode ')
        self.stream.write(b'ENABLED.'
                if self.stream.is_linemode else b'DISABLED')
        self.stream.write(b'\r\nConnected ')
        self.stream.write(bytes(
            '%d' % (time.time() - self.connect_time,), 'ascii'))
        self.stream.write(b's ago from ')
        self.stream.write(bytes(
            str(self.transport.get_extra_info('addr', None)), 'ascii'))
        self.stream.write(b'\r\nServer options: ')
        for num, (key, val) in enumerate(self.stream.local_option.items()):
            self.stream.write(bytes(
                '\r\n\t%s%s' % (
                    '!' if not val else '',
                    _name_command(key)), 'ascii'))
        self.stream.write(b'\r\nClient options: ')
        for num, (key, val) in enumerate(self.stream.remote_option.items()):
            self.stream.write(bytes(
                '\r\n\t%s%s' % (
                    '!' if not val else '',
                    _name_command(key)), 'ascii'))
        for option, pending in self.stream.pending_option.items():
            if pending:
                cmd = ' + '.join([
                    _name_command(bytes([byte])) for byte in option])
                self.stream.write(bytes('\r\nno reply for: %s' % (cmd,),
                    'ascii'))
        if self.stream.is_linemode:
            self.stream.write(bytes('\r\nLinemode is %s' % (
                self.stream.linemode,), 'ascii'))
        else:
            self.stream.write(bytes('\r\nKludge (ICANNON) mode' % (
                self.stream.linemode,), 'ascii'))

    def handle_line(self, slc=None):
        """ Callback when carriage return is received on input, or, when
        LINEMODE SLC is negotiated, the special linemode character byte
        function as ``slc``, such as SLC_EC for "erase character" (backspace).

        input buffered up to this point is queued as ``self.inp_command``,
        and either processed as a bytestring to ``process_command`` and
        cleared, or, when slc is non-None, manipulated. Such as SLC_EC
        causing the last byte of inp_command to be popped from the queue.
        """
        cmd = b''.join(self.inp_command).decode(self.encoding, 'replace')
        # convert collection of bytes to single bytestring, then decode
        if self.debug:
            slc_txt = _name_slc_command(slc) if slc is not None else None
            self.log_debug('handle_line: %r (slc=%s)', cmd, slc_txt)
        if not slc:
            try:
                self.process_cmd(cmd)
            finally:
                self.inp_command.clear()
            self.prompt()
        elif slc == SLC_EC:
            # erase character (backspace / char 127)
            if 0 == len(self.inp_command):
                self.bell()
            else:
                self.inp_command.pop()
                self.prompt(redraw=True)
        elif slc == SLC_EW:
            # erase word (^w)
            if len(self.inp_command) == 0:
                self.bell()
            else:
                self.inp_command.pop()
                while len(self.inp_command) and self.inp_command[-1] != b' ':
                    self.inp_command.pop()
                self.prompt(redraw=True)
        elif slc == SLC_EL:
            # echo '\b' * len(cmd) + ' ' * len(cmd) + '\b' * len(cmd) ?
            self.inp_command.clear()
            self.prompt(redraw=True)
        #elif slc == SLC_XON:
        #    #self.transport.resume()
        #elif slc == SLC_XOFF:
        #    #self.transport.pause()
        else:
            self.stream.write(b'\r\n ** ')
            self.stream.write(bytes(
                _name_slc_command(slc).split('_')[-1], 'ascii'))
            self.stream.write(b' ** ')
            self.inp_command.clear()
            self.prompt()

    def log_debug(self, *args, **kw):
        if self.debug:
            self.log.debug(*args, **kw)

    def eof_received(self):
        self.log.info('%s Connection closed by client',
                self.transport.get_extra_info('addr', None))

    def close(self):
        self.transport.close ()
        self._closing = True

class CharacterTelnetServer(BasicTelnetServer):
    def banner(self):
        self.stream.write(b'Welcome to ')
        self.stream.write(bytes(__file__, 'ascii', 'replace'))
        self.stream.write(b'\r\n')
        self.stream.iac(WILL, ECHO)
        self.stream.iac(WILL, SGA)

class AdvancedTelnetServer(BasicTelnetServer):

    def connection_made(self, transport):
        BasicTelnetServer.connection_made(self, transport)
        self.stream._default_slc(BSD_SLC_TAB)

    def banner(self):
        self.stream.write(b'Welcome to ')
        self.stream.write(bytes(__file__, 'ascii', 'replace'))
        self.stream.write(b'\r\n')
        self.stream.iac(WILL, SGA)
        self.stream.iac(WILL, ECHO)
        self.stream.iac(WILL, BINARY)
        # wait for response? if any, then hit 'em with:
        self.stream.iac(DO, BINARY)
        self.stream.iac(DO, TTYPE)
        self.stream.iac(DO, TSPEED)
        self.stream.iac(DO, XDISPLOC)
        self.stream.iac(DO, NEW_ENVIRON)
        self.stream.iac(DO, LINEMODE)
        self.stream.iac(DO, NAWS)
        self.stream.iac(DO, LFLOW)
        self.stream.iac(WILL, STATUS)

# `````````````````````````````````````````````````````````````````````````````
#
# debug routines for displaying raw telnet bytes

_DEBUG_OPTS = dict([(value, key)
                    for key, value in globals().items() if key in
                  ('LINEMODE', 'LMODE_FORWARDMASK', 'NAWS', 'NEW_ENVIRON', 'ENCRYPT',
                   'AUTHENTICATION', 'BINARY', 'SGA', 'ECHO', 'STATUS',
                   'TTYPE', 'TSPEED', 'LFLOW', 'XDISPLOC', 'IAC', 'DONT',
                   'DO', 'WONT', 'WILL', 'SE', 'NOP', 'DM', 'TM', 'BRK', 'IP',
                   'ABORT', 'AO', 'AYT', 'EC', 'EL', 'EOR', 'GA', 'SB', 'EOF',
                   'SUSP', 'ABORT',)])
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
_DEBUG_SLC_BITMASK = dict([(value, key)
                           for key, value in locals().items() if key in
                         ('SLC_FLUSHIN', 'SLC_FLUSHOUT', 'SLC_ACK',)])
_DEBUG_SLC_MODIFIERS = dict([(value, key)
                             for key, value in locals().items() if key in
                           ('SLC_NOSUPPORT', 'SLC_CANTCHANGE',
                               'SLC_VARIABLE', 'SLC_DEFAULT',)])

def _name_slc_command(byte):
    """ Given an SLC byte, return global mnumonic constant as string. """
    return (repr(byte) if byte not in _DEBUG_SLC_OPTS
            else _DEBUG_SLC_OPTS[byte])

def _name_slc_modifier(value):
    """ Given an SLC byte value, return string representing its modifiers. """
    #value = ord(byte)
    debug_str = ''
    for modifier, key in _DEBUG_SLC_BITMASK.items():
        if value & ord(modifier):
            debug_str += '%s,' % (key,)
            value = value ^ ord(modifier)
    byte = bytes([value])
    debug_str += (repr(byte) if byte not in _DEBUG_SLC_MODIFIERS
            else _DEBUG_SLC_MODIFIERS[byte])
    return debug_str

def _name_command(byte):
    """ Given an IAC byte, return its mnumonic global constant. """
    return (repr(byte) if byte not in _DEBUG_OPTS
            else _DEBUG_OPTS[byte])

def _bin8(number):
    """ return binary representation of ``number``, padded to 8 bytes. """
    prefix, value = bin(number).split('b')
    return '0b%0.8i' % (int(value),)

def _name_char(number):
    """ Return string of an 8-bit input character value, ``number``. """
    char = chr(number)
    if char.isprintable():
        return char
    if number == 0:
        return 'CTRL_SPACE'
    # XXX verify, 'ctrl+\\' prints as 'CTRL_|'
    if number <= ord('~') - ord('a'):
        return 'CTRL_%s' % (chr(ord('a') + (number - 1)).upper(),)
    if number == 127:
        return 'BACKSPACE'
    else:
        return repr(char)

# `````````````````````````````````````````````````````````````````````````````

ARGS = argparse.ArgumentParser(description="Run simple telnet server.")
ARGS.add_argument(
    '--host', action="store", dest='host',
    default='127.0.0.1', help='Host name')
ARGS.add_argument(
    '--port', action="store", dest='port',
    default=6023, type=int, help='Port number')


def main():
    args = ARGS.parse_args()
    if ':' in args.host:
        args.host, port = args.host.split(':', 1)
        args.port = int(port)
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)
    loop = tulip.get_event_loop()
    # 'start_serving' receives a Protocol class reference as arg1;
    # we use lambda to cause TelnetServer to be instantiated with
    # flag debug=True.
    f = loop.start_serving(
        lambda: BasicTelnetServer(debug=True), args.host, args.port)
#AdvancedTelnetServer
#CharacterTelnetServer
#LinemodeTelnetServer
    x = loop.run_until_complete(f)
    logger.info('serving on %s', x.getsockname())
    loop.run_forever()

if __name__ == '__main__':
    main()


# NOTES:
# although tintin++ strips ^t or ^c, and ^d disconnects the session,
# ^s and ^q come in raw; but only after return is pressed.
# -- further inspection of tintin reveals limited protocol processing


# character-at-a-time mode is essentially pass-thru
# to self.handle_input()
#     handle_line is called with current input buffer and slc
#     option set; it is expected that ^c does not add \x03 to
#     the input buffer, rather, calls handle_line with an
#     unfinished input buffer, and slc set to SLC_IP after
#     self.stream.handle_ip has been called.
# linemode processing buffers input until '\r', '\n', '\r\0',
# or '\r\n', using a state processor: first-match allows \r or
# \n, second pass ignores '\0' and '\n'.
#         pass 1; received '\r' and not in BINARY mode; expect
#         pass 2 to receive '\0', except for non-compliant clients,
#         which sent us '\n' instead.
#     pass 2; non-binary, ignore null byte, or (not rfc-compliant)
#     '\n' following '\r', which should only have occured in
#     BINARY mode.
#     telnet spec asserts carriage return should be indicated with
#     \r\0, except for BINARY mode. Very forgiving here, allowing
#     clients to send a single '\r' well, we simply pass the next

# rfc1184 extensions to rfc1116 Move cursor
#       one character left/right (SLC_MCL/SLC_MCR), move cursor one word
#       left/right (SLC_MCWL/SLC_MCWR), move cursor to begining/end of
#       line (SLC_MCBOL/SLC_MCEOL), enter insert/overstrike mode
#       (SLC_INSRT/SLC_OVER), erase one character/word to the right
#       (SLC_ECR/SLC_EWR), and erase to the beginning/end of the line
#       (SLC_EBOL/SLC_EEOL).


"""
    SOFT_TAB has meaning only to the end ..

    LIT_ECHO ...

    XON/XOFF and ECHO are within SLC does not set IAC sets per rfc,

    Need to implement flow control (^s); cease sending bytes on transport
    until ^q is received, tulip does not provide this interface.  --
    Directly pull _buffer to local value, .clear() it, then re-queue on ^q.
    -- found some discussion on python-tulip ML about a 'pause' method

    flush in/flush out flag handling of SLC characters

    description of, and handling of, local vs. remote line editing; it seems
    if linemode is 'edit', that 'aa^Hcd' is received as 'abcd',
    when linemode is not 'edit', then something like readline callbacks
    should be supplied for SLC characters; but otherwise is line oriented.

    Linemode.trapsig may be asserted by server; we shouldn't get any ^C when
    set, but, instead, get IAC IP only. When unset, we can get ^C raw, or,
    if an SLC function is requested, call that callback.

    Assert EOL with BINARY and LINEMODE EDIT option behavior.

    Test OOB data with 'DM' using stevens socat tool ..

    Issue with tulip -- doesn't handle OOB data, need to derive
    BaseSelectorEventLoop, ovverride:
        sock_recv(sock, n), _sock_recv(fut, registered, sock, n),
        sock_sendall(sock, data), _sock_sendall(fut, registered, sock, data),
    to accept additional argument [flags], like sock.send() and recv().
    Then, have data_received receive additional argument, urgent=True ?

    A series of callbacks for LINEMODE and standard EC, EL, etc; this should
    allow a readline-line interface to negotiate correct behavior, regardless
    of mode. Withholding on implementation: reaching for clarity without
    brevity.

    A simple telnet client .. with stdin as tulip  ..?

User Telnet interface                           |       | | | | | |
  Input & output all 7-bit characters           | 3.4.1 | |o| | | |
  Bypass local op sys interpretation            | 3.4.1 | |o| | | |
  Escape character                              | 3.4.1 |o| | | | |
     User-settable escape character             | 3.4.1 | |o| | | |
  Escape to enter 8-bit values                  | 3.4.1 | | |o| | |
  Can input IP, AO, AYT                         | 3.4.2 |o| | | | |
  Can input EC, EL, Break                       | 3.4.2 | |o| | | |
  Report TCP connection errors to user          | 3.4.3 | |o| | | |
  Optional non-default contact port             | 3.4.4 | |o| | | |
  Can spec: output flushed when IP sent         | 3.4.5 | |o| | | |
  Can manually restore output mode              | 3.4.5 | |o| | | |




"""

# Test SLC_CANTCHANGE
