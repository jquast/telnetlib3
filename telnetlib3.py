#!/usr/bin/env python3
"""
This project implement a Telnet server using the 'tulip' project of PEP 3156.
It requires Python 3.3. For convenience, the 'tulip' project is included.
"""
# (C)2013 Jeff Quast <contact@jeffquast.com>, ISC licensed.
import collections
import argparse
import logging
import codecs
import locale
import shlex
import time

import tulip

from telnetlib import LINEMODE, NAWS, NEW_ENVIRON, BINARY, SGA, ECHO, STATUS
from telnetlib import TTYPE, TSPEED, LFLOW, XDISPLOC, IAC, DONT, DO, WONT
from telnetlib import WILL, SE, NOP, TM, DM, BRK, IP, AO, AYT, EC, EL, EOR
from telnetlib import GA, SB, LOGOUT, EXOPL, CHARSET
(EOF, SUSP, ABORT) = bytes([236]), bytes([237]), bytes([238])  # rfc1184
(IS, SEND, INFO) = (bytes([const]) for const in range(3))
(LFLOW_OFF, LFLOW_ON, LFLOW_RESTART_ANY, LFLOW_RESTART_XON) = (
        bytes([const]) for const in range(4))
NSLC = 30
(SLC_SYNCH, SLC_BRK, SLC_IP, SLC_AO, SLC_AYT, SLC_EOR, SLC_ABORT, SLC_EOF,
    SLC_SUSP, SLC_EC, SLC_EL, SLC_EW, SLC_RP, SLC_LNEXT, SLC_XON, SLC_XOFF,
    SLC_FORW1, SLC_FORW2, SLC_MCL, SLC_MCR, SLC_MCWL, SLC_MCWR, SLC_MCBOL,
    SLC_MCEOL, SLC_INSRT, SLC_OVER, SLC_ECR, SLC_EWR, SLC_EBOL, SLC_EEOL) = (
            bytes([const]) for const in range(1, NSLC + 1))
(SLC_FLUSHOUT, SLC_FLUSHIN, SLC_ACK) = (
        bytes([32]), bytes([64]), bytes([128]))
(SLC_NOSUPPORT, SLC_CANTCHANGE, SLC_VARIABLE, SLC_DEFAULT) = (
        bytes([const]) for const in range(4))
(LMODE_MODE, LMODE_FORWARDMASK, LMODE_SLC) = (
        bytes([const]) for const in range(1, 4))
SLC_LEVELBITS = 0x03
(LMODE_MODE_REMOTE, LMODE_MODE_LOCAL, LMODE_MODE_TRAPSIG) = (
        bytes([const]) for const in range(3))
(LMODE_MODE_ACK, LMODE_MODE_SOFT_TAB, LMODE_MODE_LIT_ECHO) = (
    bytes([4]), bytes([8]), bytes([16]))

# see: TelnetStreamReader._default_callbacks
DEFAULT_IAC_CALLBACKS = (
        (BRK, 'brk'), (IP, 'ip'), (AO, 'ao'), (AYT, 'ayt'), (EC, 'ec'),
        (EL, 'el'), (EOR, 'eor'), (EOF, 'eof'), (SUSP, 'susp'),
        (ABORT, 'abort'), (NOP, 'nop'), (DM, 'dm'), (GA, 'ga'),
        (EOR, 'eor'), )
DEFAULT_SLC_CALLBACKS = (
        (SLC_SYNCH, 'dm'), (SLC_BRK, 'brk'), (SLC_IP, 'ip'),
        (SLC_AO, 'ao'), (SLC_AYT, 'ayt'), (SLC_EOR, 'eor'),
        (SLC_ABORT, 'abort'), (SLC_EOF, 'eof'), (SLC_SUSP, 'susp'),
        (SLC_EC, 'ec'), (SLC_EL, 'el'), (SLC_EW, 'ew'), (SLC_RP, 'rp'),
        (SLC_LNEXT, 'lnext'), (SLC_XON, 'xon'), (SLC_XOFF, 'xoff'), )
DEFAULT_EXT_CALLBACKS = (
        (TTYPE, 'ttype'), (TSPEED, 'tspeed'), (XDISPLOC, 'xdisploc'),
        (NEW_ENVIRON, 'env'), (NAWS, 'naws'), (LOGOUT, 'logout'),)

# `````````````````````````````````````````````````````````````````````````````
_POSIX_VDISABLE = b'\xff'
class SLC_definition(object):
    def __init__(self, mask=SLC_DEFAULT, value=b'\x00'):
        """ An SLC definition defines the willingness to support
            a Special Linemode Character, and is defined by its byte,
            ``mask`` and default keyboard ASCII byte ``value``.

            The special byte ``mask`` ``SLC_NOSUPPORT`` and value
            ``_POSIX_VDISABLE`` infer our unwillingness to support
            the option.

            The default byte ``mask`` ``SLC_DEFAULT`` and value
            ``b'\x00'`` infer our willingness to support the option.
            The value must first negotiated by the client by IAC SB
            LINEMODE SLC (...) to activate the SLC callback.
        """
        assert type(mask) is bytes and type(value) is bytes
        assert len(mask) == 1 and len(value) == 1
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
        """ Returns True if SLC_FLUSHIN bit is set. """
        return ord(self.mask) & ord(SLC_FLUSHOUT)

    def set_value(self, value):
        """ Set SLC keyboard ascii value, ``byte``. """
        assert type(value) is bytes and len(value) == 1
        self.val = value

    def set_mask(self, mask):
        """ Set SLC mask, ``mask``. """
        assert type(mask) is bytes and len(mask) == 1
        self.mask = mask

    def set_flag(self, flag):
        """ Set SLC flag byte, ``flag``. """
        assert type(flag) is bytes and len(flag) == 1
        self.mask = bytes([ord(self.mask) | ord(flag)])

    def unset_flag(self, flag):
        """ Unset SLC flag byte, ``flag``. """
        self.mask = bytes([ord(self.mask) ^ ord(flag)])

    def __str__(self):
        """ Returns SLC definition as string '(flag(|s), value)'. """
        flags = []
        if self.nosupport:
            flags.append('nosupport')
        if self.ack:
            flags.append('ack')
        if self.flushin:
            flags.append('flushin')
        if self.flushout:
            flags.append('flushout')
        return '(%s, %r)' % ('|'.join(flags) if flags else 'None',
                _name_char(ord(self.val)))

class SLC_nosupport(SLC_definition):
    def __init__(self):
        SLC_definition.__init__(self, SLC_NOSUPPORT, _POSIX_VDISABLE)

# The following are default values for the "Special Line Character" tabset,
# set on initialization of a TelnetStreamReader, or when special SLC function
# (0, SLC_DEFAULT, 0) is received.
_SLC_VARIABLE_FIO = bytes(
        [ord(SLC_VARIABLE) | ord(SLC_FLUSHIN) | ord(SLC_FLUSHOUT)])
_SLC_VARIABLE_FI = bytes(
        [ord(SLC_VARIABLE) | ord(SLC_FLUSHIN)])
_SLC_VARIABLE_FO = bytes(
        [ord(SLC_VARIABLE) | ord(SLC_FLUSHOUT)])

# A simple SLC tab that offers nearly all characters for negotiation (default)
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

# This SLC tab provides no reply from a bsd telnet client; they match exactly.
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

# `````````````````````````````````````````````````````````````````````````````
class Linemode(object):
    def __init__(self, mask=LMODE_MODE_LOCAL):
        """ A mask of ``LMODE_MODE_LOCAL`` means that all line editing is
            performed on the client side (default). A mask of b'\x00'
            indicates that editing is performed on the remote side. Valid
            flags are ``LMODE_MODE_TRAPSIG``, ``LMODE_MODE_ACK``,
            ``LMODE_MODE_SOFT_TAB``, ``LMODE_MODE_LIT_ECHO``.
        """
        assert type(mask) is bytes and len(mask) == 1
        self.mask = mask

    def set_flag(self, flag):
        """ Set linemode bitmask ``flag``.  """
        self.mask = bytes([ord(self.mask) | ord(flag)])

    def unset_flag(self, flag):
        """ Unset linemode bitmask ``flag``.  """
        self.mask = bytes([ord(self.mask) ^ ord(flag)])

    @property
    def remote(self):
        """ True if linemode processing is done on server end
            (remote processing).

            """
        return not self.local

    @property
    def local(self):
        """ True if telnet stream is in EDIT mode (local processing).

            When set, the client side of the connection should process all
            input lines, performing any editing functions, and only send
            completed lines to the remote side.

            When unset, client side should *not* process any input from the
            user, and the server side should take care of all character
            processing that needs to be done.
        """
        return bool(ord(self.mask) & ord(LMODE_MODE_LOCAL))

    @property
    def trapsig(self):
        """ True if signals are trapped by client.

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

    @property
    def soft_tab(self):
        """ When set, the client will expand horizontal tab (\\x09)
            into the appropriate number of spaces.

            When unset, the client should allow horitzontal tab to
            pass through un-modified. This status is only relevant
            for the client end.
        """
        return bool(ord(self.mask) & ord(LMODE_MODE_SOFT_TAB))

    @property
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
            return 'remote'
        flags = []
        # we say 'local' to indicate that 'edit' mode means that all
        # input processing is done locally, instead of the obtusely named
        # flag 'edit'
        if self.local:
            flags.append('local')
        else:
            flags.append('remote')
        if self.trapsig:
            flags.append('trapsig')
        if self.soft_tab:
            flags.append('soft_tab')
        if self.lit_echo:
            flags.append('lit_echo')
        if self.ack:
            flags.append('ack')
        return '|'.join(flags)

# `````````````````````````````````````````````````````````````````````````````
class Forwardmask(object):
    def __init__(self, value, ack=False):
        assert type(value) == bytes and len(value) == 32
        self.value = value
        self.ack = ack

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
                    for char in range(start, last + 1) if char in self])
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
            descr = ' + '.join([_name_command(bytes([byte]))
                for byte in key[:2]] + [repr(byte)
                    for byte in key[2:]])
            self.log.debug('%s[%s] = %s', self.name, descr, value,)
        dict.__setitem__(self, key, value)

class TelnetStreamReader():
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
     * ``request_env_values`` is a list of system environment variables
         requested by the server after a client agrees to negotiate OLD
         or NEW_ENVIRON.
     * ``lflow_any`` is a boolean to indicate wether flow control should be
       disabled after it has been enbaled using XON (^s) when: any key is
       pressed (True), or only when XOFF (^q) is pressed (False, default).

    Because Server and Client support different capabilities, the mutually
    exclusive booleans ``client`` and ``server`` indicates which end the
    protocol is attached to. The default is *server*, meaning, this stream
    is attached to a server end, reading from a telnet client.
    """
    request_env_values = (
            "USER HOSTNAME UID TERM COLUMNS LINES DISPLAY LANG SYSTEMTYPE "
            "ACCT JOB PRINTER SFUTLNTVER SFUTLNTMODE").split()
    lflow_any = False
    forwardmask = None

    # state variables to track and assert command negotiation and response.
    _iac_received = False   # has IAC been recv?
    _slc_received = False   # has SLC value been received?
    _cmd_received = False   # has IAC (DO, DONT, WILL, WONT) been recv?
    _sb_received = False    # has IAC SB been recv?
    _tm_sent = False        # has IAC DO TM been sent?
    _dm_recv = False        # has IAC DM been recv?
    SB_MAXSIZE = 2048
    SLC_MAXSIZE = 6 * NSLC

    def __init__(self, transport, client=False, server=False, log=logging):
        """ Stream is decoded as a Telnet Server, unless
            keyword argument ``client`` is set to ``True``.
        """
        assert client is False or server is False, (
            "Arguments 'client' and 'server' are mutually exclusive")
        self.log = log
        self.transport = transport
        self.server = (client in (None, False) or server in (None, True))
        self._sb_buffer = collections.deque()
        self._slc_buffer = collections.deque()
        self._linemode = Linemode()
        self._want_linemode = Linemode(bytes([
                ord(LMODE_MODE_REMOTE) | ord(LMODE_MODE_LIT_ECHO)]))
        self._forwardmask_enabled = False
        self._init_options()
        self._default_callbacks()
        self._default_slc(DEFAULT_SLC_TAB)
        tulip.StreamReader.__init__(self)

    def _init_options(self):
        self.pending_option = Option('pending_option', self.log)
        self.local_option = Option('local_option', self.log)
        self.remote_option = Option('remote_option', self.log)

    def _default_callbacks(self):
        """ Set default callback dictionaries ``_iac_callbacks``,
            ``_slc_callbacks``, and ``_ext_callbacks`` to default methods of
            matching names, such that IAC + IP, or, the SLC value negotiated
            for SLC_IP, signals a callback to method ``self.handle_ip``.
        """
        self._iac_callbacks = {}
        for iac_cmd, key in DEFAULT_IAC_CALLBACKS:
            self.set_iac_callback(iac_cmd, getattr(self, 'handle_%s' % (key,)))

        self._slc_callbacks = {}
        for slc_cmd, key in DEFAULT_SLC_CALLBACKS:
            self.set_slc_callback(slc_cmd, getattr(self, 'handle_%s' % (key,)))

        # extended callbacks may receive various arguments
        self._ext_callbacks = {}
        for ext_cmd, key in DEFAULT_EXT_CALLBACKS:
            self.set_ext_callback(ext_cmd, getattr(self, 'handle_%s' % (key,)))

    def _default_slc(self, tabset):
        """ set property ``_slctab`` to default SLC tabset, unless it
            is unlisted (as is the case for SLC_MCL+), then set as
            SLC_NOSUPPORT _POSIX_VDISABLE (0xff).

            ``_slctab`` is a dictionary of SLC functions, such as SLC_IP,
            to a tuple of the handling character and support level.
        """
        self._slctab = {}
        self._default_tabset = tabset
        for slc in range(NSLC + 1):
            self._slctab[bytes([slc])] = tabset.get(bytes([slc]),
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
        """ Register ``func`` as callbable for receipt of SLC character
            negotiated for the SLC command ``slc`` in  ``_slc_callbacks``,
            keyed by ``slc`` and valued by its handling function.

            SLC_SYNCH, SLC_BRK, SLC_IP, SLC_AO, SLC_AYT, SLC_EOR, SLC_ABORT,
            SLC_EOF, SLC_SUSP, SLC_EC, SLC_EL, SLC_EW, SLC_RP, SLC_XON,
            SLC_XOFF, (...)

            These callbacks receive no arguments.

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
        as a bytestring. ``NEW_ENVIRON`` receives a single argument as
        dictionary. ``NAWS`` receives two integer arguments (width, height),
        and ``TSPEED`` receives two integer arguments (rx, tx).
        """
        assert cmd in (TTYPE, TSPEED, XDISPLOC, NEW_ENVIRON, NAWS, LOGOUT,
                CHARSET)
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

            IAC (\xff) is always escaped by IAC IAC, unless oob=True.

            All standard telnet bytes, and bytes within an (IAC SB), (IAC SE)
            sub-negotiation buffer must always be escaped.

            8-bit ASCII data values greater than 128 cannot be sent inband
            unless WILL BINARY has been agreed, or ``oob`` is ``True``.

            If ``oob`` is set ``True``, data is considered
            out-of-band and may set high bit.
        """
        assert isinstance(data, (bytes, bytearray)), repr(data)
        # all inband telnet bytes, and subnegotiation databytes must
        # have the IAC ("is a command") escaped by a second IAC.
        _escape_iac = lambda buf: buf.replace(IAC, IAC+IAC)
        if not oob and not self.local_option.get(BINARY, None):
            for pos, byte in enumerate(data):
                assert byte < 128, (
                        'character value %d at pos %d not valid, '
                        'send IAC WILL BINARY first: %r' % (
                            byte, pos, data))
        self.transport.write(_escape_iac(data))

    def write_iac(self, data):
        """ Write IAC (is a command) data byte(s) to transport.

            IAC is never escaped.  """
        self.transport.write(data)

    def ga(self):
        """ Send IAC GA (Go-Ahead) if IAC DONT SGA was received, otherwise
            nothing happens.

            Only a few 1970-era hosts require GA (AMES-67, UCLA-CON). The GA
            signal is very useful for scripting, such as an 'expect'-like
            program flow, or for MUDs, indicating that the last-most received
            line is a prompt. Another example of GA is a nethack server
            (alt.nethack.org), that indicates to ai bots that it has received
            all screen updates.

            Those clients wishing to receive GA should send (DONT SGA). """
        if not self.local_option.get(SGA, True):
            self.write_iac(IAC + GA)

    def iac(self, cmd, opt):
        """ Send a 3-byte triplet IAC "is a command", cmd, byte.

        Returns True if the command was actually sent. Not all commands
        are legal in the context of client, server, or negotiation state.
        For instance a call to WILL, BINARY returns True, but a subsequent
        call would return False; because it is either in state tracker
        ``pending_option`` or ``local_option`` is already True.
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
                if self._tm_sent:
                    self.log.debug('skip IAC DO TM; must first recv WILL TM.')
                    return False
                self._tm_sent = True
        if cmd in (DO, WILL) and opt != TM:
            if self.pending_option.get(cmd + opt, False):
                self.log.debug('skip %s %s; pending_option = True',
                    _name_command(cmd), _name_command(opt))
                return False
            self.pending_option[cmd + opt] = True
        if cmd == WILL and opt != TM:
            if self.local_option.get(opt, None):
                self.log.debug('skip %s %s; local_option = True',
                    _name_command(cmd), _name_command(opt))
                return False
        if cmd == DONT:
            if self.remote_option.get(opt, None):
                # warning: some implementations incorrectly reply (DONT, opt),
                # for an option we already said we WONT. This would cause
                # telnet loops for implementations not doing state tracking!
                self.log.debug('skip %s %s; remote_option = True',
                    _name_command(cmd), _name_command(opt))
            self.remote_option[opt] = False
        elif cmd == WONT:
            self.local_option[opt] = False
        self.write_iac(IAC + cmd + opt)
        self.log.debug('send IAC %s %s' % (
            _name_command(cmd), _name_command(opt),))

    def feed_byte(self, byte):
        """ Receive byte arrived by ``TelnetServer.data_received()``.

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
            if len(self._sb_buffer) > self.SB_MAXSIZE:
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
                if self.handle_do(opt):
                    self.local_option[opt] = True
                    if self.pending_option.get(WILL + opt, False):
                        self.pending_option[WILL + opt] = False
            elif self._cmd_received == DONT:
                self.handle_dont(opt)
                if self.pending_option.get(WILL + opt, False):
                    self.pending_option[WILL + opt] = False
                self.local_option[opt] = False
            elif self._cmd_received == WILL:
                if not self.pending_option.get(DO + opt):
                    self.log.debug('DO %s unsolicited', _name_command(opt))
                self.handle_will(opt)
                if self.pending_option.get(DO + opt, False):
                    self.pending_option[DO + opt] = False
                if self.pending_option.get(DONT + opt, False):
                    self.pending_option[DONT + opt] = False
            elif self._cmd_received == WONT:
                self.handle_wont(opt)
                self.pending_option[DO + opt] = False
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
            # inband data is tested for SLC characters for LINEMODE
            (callback, slc_name, slc_def) = self._slc_snoop(byte)
            if slc_name is not None:
                self.log.debug('_slc_snoop(%r): %s, callback is %s.',
                        byte, _name_slc_command(slc_name),
                        callback.__name__ if callback is not None else None)
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
        ``handle_naws``, or set their own callbacks using
        ``set_extcall_backs(opt_byte, func)``.

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
        elif cmd in NEW_ENVIRON:
            assert len(buf) > 2, ('SE: buffer too short: %r' % (buf,))
            self._handle_sb_env(buf)
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

    def _handle_sb_env(self, buf):
        kind = buf.popleft()
        opt = buf.popleft()
        assert opt in (IS, INFO, SEND), opt
        assert kind == NEW_ENVIRON
        if opt == SEND:
            self._handle_sb_env_send(buf)
        if opt in (IS, INFO):
            assert self.server, ('SE: cannot recv from server: %s %s' % (
                _name_command(kind), 'IS' if opt == IS else 'INFO',))
            if opt == IS:
                if not self.pending_option.get(SB + kind + SEND + IS, None):
                    self.log.debug('%s IS unsolicited', _name_command(opt))
                self.pending_option[SB + kind + SEND + IS] = False
            if self.pending_option.get(SB + kind + SEND + IS, None) is False:
                # a pending option of value of 'False' means it previously
                # completed, subsequent environment values should have been
                # send as INFO ..
                self.log.debug('%s IS already recv; expected INFO.',
                        _name_command(kind))
            breaks = list([idx for (idx, byte) in enumerate(buf)
                           if byte in (b'\x00', b'\x03')])
            env = {}
            for start, end in zip(breaks, breaks[1:]):
                # not the best looking code, how do we splice & split bytes ..?
                decoded = bytes([ord(byte) for byte in buf]).decode('ascii')
                pair = decoded[start + 1:end].split('\x01', 1)
                if 2 == len(pair):
                    key, value = pair
                    env[key] = value
            self.log.debug('sb_env %s: %r', _name_command(opt), env)
            self._ext_callbacks[kind](env)
            return

    def _handle_sb_env_send(self, buf):
        raise NotImplementedError  # recv by client

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

    def handle_logout(self, cmd):
        """ Handle (IAC, DO/DONT/WILL/WONT, LOGOUT), RFC 727.

            Close the transport on receipt of DO,
            Reply DONT on receipt of WILL.
            Nothing is done on receipt of DONT or WONT LOGOFF.

            Only the server end may receive (DO, DONT).
            Only the client end may receive (WILL, WONT).
        """
        if cmd == DO:
            self.log.info('client requests DO LOGOUT')
            self.transport.close()
        elif cmd == DONT:
            self.log.info('client requests DONT LOGOUT')
        elif cmd == WILL:
            self.log.debug('recv WILL TIMEOUT (timeout warning)')
            self.log.debug('send IAC DONT LOGOUT')
            self.iac(DONT, LOGOUT)
        elif cmd == WONT:
            self.log.info('recv IAC WONT LOGOUT (server refuses logout')

    def send_linemode(self, linemode=None):
        """ Request the client switch to linemode ``linemode``, an
        of the Linemode class, or self._linemode by default.
        """
        assert self.is_server, (
                'SB LINEMODE LMODE_MODE cannot be sent by client')
        assert self.remote_option.get(LINEMODE, None), (
                'SB LINEMODE LMODE_MODE cannot be sent; '
                'WILL LINEMODE not received.')
        if linemode is not None:
            self.log.debug('Linemode is %s', linemode)
            self._linemode = linemode
        self.write_iac(IAC + SB + LINEMODE)
        self.write_iac(LMODE_MODE + self._linemode.mask)
        self.write_iac(IAC + SE)
        self.log.debug('sent IAC SB LINEMODE MODE %s IAC SE', self._linemode)


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
            raise ValueError('Illegal IAC SB LINEMODE command, %r' % (
                _name_command(cmd),))

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
        self.send_do_forwardmask()

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
        if cmd in (WILL, WONT):
            self._forwardmask_enabled = cmd is WILL
        elif cmd in (DO, DONT):
            self._forwardmask_enabled = cmd is DO
            if cmd == DO:
                self._handle_do_forwardmask(buf)

    def _handle_do_forwardmask(self, buf):
        """ Handles buffer received in SB LINEMODE DO FORWARDMASK <buf>
        """ # XXX UNIMPLEMENTED: ( received on client )
        pass

    def send_do_forwardmask(self):
        """ Sends SLC Forwardmask appropriate for the currently registered
            ``self._slctab`` to the client end.
        """
        assert self.is_server, (
                'DO FORWARDMASK may only be sent by server end')
        assert self.remote_option.get(LINEMODE, None), (
                'cannot send DO FORWARDMASK without receipt of WILL LINEMODE.')
        self.write_iac(IAC + SB + LINEMODE + DO + LMODE_FORWARDMASK)
        self.write(self.forwardmask.value, oob=True)
        self.log.debug('send IAC SB LINEMODE DO LMODE_FORWARDMASK,')
        for maskbit_descr in self.forwardmask.__repr__():
            self.log.debug('send %s', maskbit_descr)
        self.log.debug('send IAC SE')
        self.write_iac(IAC + SE)
        self.pending_option[SB + LINEMODE] = True

    @property
    def forwardmask(self):
        """ Forwardmask is formed by a 32-byte representation of all 256
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
        if self.local_option.get(BINARY, None) is False:
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
        return Forwardmask(b''.join(mask32), self._forwardmask_enabled)

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
        assert len(self._slc_buffer) < self.SLC_MAXSIZE, ('SLC: buffer full')
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
                self._default_slc(self._default_tabset)
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
            return
        elif slc_def.level == mylevel and slc_def.ack:
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
            return

        if hislevel == SLC_DEFAULT:
            # client end requests we use our default level
            if mylevel == SLC_DEFAULT:
                # client end telling us to use SLC_DEFAULT on an SLC we do not
                # support (such as SYNCH). Set flag to SLC_NOSUPPORT instead
                # of the SLC_DEFAULT value that it begins with
                self._slctab[func].set_mask(SLC_NOSUPPORT)
            else:
                # set current flag to the flag indicated in default tab
                self._slctab[func].set_mask(DEFAULT_SLC_TAB.get(func).mask)
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
        elif slc_def.level == SLC_CANTCHANGE and mylevel == SLC_CANTCHANGE:
            # "degenerate to SLC_NOSUPPORT"
            self._slctab[func].set_mask(SLC_NOSUPPORT)
            self._slc_add(func)
        else:
            # mask current level to levelbits (clears ack),
            self._slctab[func].set_mask(self._slctab[func].level)
            if mylevel == SLC_CANTCHANGE:
                self._slctab[func].val = DEFAULT_SLC_TAB.get(
                        func, SLC_nosupport()).val
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

        This method returns True if the opt enables the willingness of the
        remote end to accept a telnet capability, such as NAWS. It returns
        False for unsupported option, or an option invalid in that context,
        such as LOGOUT.
        """
        self.log.debug('handle_do(%s)' % (_name_command(opt)))
        # options that we support
        if opt == ECHO and not self.is_server:
            self.log.warn('cannot recv DO ECHO on client end.')
        elif opt == LINEMODE and self.is_server:
            self.log.warn('cannot recv DO LINEMODE on server end.')
        elif opt == LOGOUT and self.is_server:
            self.log.warn('cannot recv DO LOGOUT on client end')
        elif opt == TM:
            self.iac(WILL, TM)
        elif opt == LOGOUT:
            self._ext_callbacks[LOGOUT](DO)
        elif opt in (ECHO, LINEMODE, BINARY, SGA, LFLOW, EXOPL):
            if not self.local_option.get(opt, None):
                self.iac(WILL, opt)
            return True
        elif opt == STATUS:
            if not self.local_option.get(opt, None):
                self.iac(WILL, STATUS)
            self._send_status()
            return True
        else:
            if self.local_option.get(opt, None) is None:
                self.iac(WONT, opt)
            self.log.warn('Unhandled: DO %s.' % (_name_command(opt),))
        return False

    def handle_dont(self, opt):
        """ Process byte 3 of series (IAC, DONT, opt) received by remote end.

        This only results in ``self.local_option[opt]`` set to ``False``, with
        the exception of (IAC, DONT, LOGOUT), which only signals a callback
        to ``handle_logout(DONT)``.
        """
        self.log.debug('handle_dont(%s)' % (_name_command(opt)))
        if opt == LOGOUT:
            assert self.is_server, ('cannot recv DONT LOGOUT on server end')
            self._ext_callbacks[LOGOUT](DONT)
            return
        # many implementations (wrongly!) sent a WONT in reply to DONT. It
        # sounds reasonable, but it can and will cause telnet loops. (ruby?)
        # Correctly, a DONT can not be declined, so there is no need to
        # affirm in the negative.
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
        ``self.request_env_values``. All others are replied with DONT.

        The result of a supported capability is a response of (IAC, DO, opt)
        and the setting of ``self.remote_option[opt]`` of ``True``. For
        unsupported capabilities, RFC specifies a response of (IAC, DONT, opt).
        Similarly, set ``self.remote_option[opt]`` to ``False``.  """
        self.log.debug('handle_will(%s)' % (_name_command(opt)))
        if opt in (BINARY, SGA, ECHO, NAWS, LINEMODE):
            if opt == ECHO and self.is_server:
                raise ValueError('cannot recv WILL ECHO on server end')
            if opt in (NAWS, LINEMODE) and not self.is_server:
                raise ValueError('cannot recv WILL %s on client end' % (
                    _name_command(opt),))
            if not self.remote_option.get(opt, None):
                self.remote_option[opt] = True
                self.iac(DO, opt)
            if opt in (NAWS, LINEMODE):
                self.pending_option[SB + opt] = True
                if opt == LINEMODE:
                    # server sets the initial mode and sends forwardmask,
                    self.send_linemode(self._want_linemode)
        elif opt == TM:
            if opt == TM and not self._tm_sent:
                raise ValueError(
                        'cannot recv WILL TM, must first send DO TM.')
            self.log.debug('WILL TIMING-MARK, _tm_sent=False')
            self._tm_sent = False
        elif opt == LOGOUT:
            if opt == LOGOUT and not self.is_server:
                raise ValueError('cannot recv WILL LOGOUT on server end')
            self._ext_callbacks[LOGOUT](WILL)
        elif opt == STATUS:
            if not self.remote_option.get(opt, None):
                self.remote_option[opt] = True
            self.request_status()
        elif opt == LFLOW:
            if opt == LFLOW and not self.is_server:
                raise ValueError('WILL LFLOW not supported on client end')
            if not self.remote_option.get(opt, None):
                self.remote_option[opt] = True
            self.send_lineflow_mode()
        elif opt == NEW_ENVIRON:
            if not self.remote_option.get(opt, None):
                self.remote_option[opt] = True
            self.request_env()
        elif opt == CHARSET:
            if not self.remote_option.get(opt, None):
                self.remote_option[opt] = True
            self.request_charset()
        elif opt == XDISPLOC:
            if opt == XDISPLOC and not self.is_server:
                raise ValueError('cannot recv WILL XDISPLOC on client end')
            if not self.remote_option.get(opt, None):
                self.remote_option[opt] = True
            self.request_xdisploc()
        elif opt == TTYPE:
            if opt == TTYPE and not self.is_server:
                raise ValueError('cannot recv WILL TTYPE on client end')
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
        elif opt == LOGOUT:
            assert not (self.is_server), (
                'cannot recv WONT LOGOUT on server end')
            if not self.pending_option(DO + LOGOUT):
                self.log.warn('Server sent WONT LOGOUT unsolicited')
            self._ext_callbacks[LOGOUT](WONT)
        else:
            self.remote_option[opt] = False

# `````````````````````````````````````````````````````````````````````````````
# Extended Telnet RFC implementations

    def _send_status(self):
        """ Respond after DO STATUS received by client (rfc859). """
        assert (self.pending_option.get(WILL + STATUS, None) is True
                or self.local_option.get(STATUS, None) is True), (
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
        desc = 'LFLOW_RESTART_ANY' if self.lflow_any else 'LFLOW_RESTART_XON'
        self.write_iac(b''.join([IAC, SB, LFLOW, mode, IAC, SE]))
        self.log.debug('send: IAC SB LFLOW %s IAC SE', desc)

    def request_tspeed(self):
        """ Send TSPEED, SEND sub-negotiation, rfc1079.
            Does nothing if (WILL, TSPEED) has not yet been received.
            or an existing SB TSPEED SEND request is already pending. """
        if not self.remote_option.get(TSPEED, None):
            return
        if not self.pending_option.get(SB + TSPEED, None):
            self.pending_option[SB + TSPEED] = True
            response = [IAC, SB, TSPEED, SEND, IAC, SE]
            self.log.debug('send: IAC SB TSPEED SEND IAC SE')
            self.write_iac(b''.join(response))

    def request_charset(self):
        """ Request sub-negotiation CHARSET, rfc 2066.

            At least some modern MUD clients and popular asian telnet BBS
            systems use CHARSET, and reply 'UTF-8' (or 'GBK',).  """
        raise NotImplementedError

    def request_env(self):
        """ Request sub-negotiation NEW_ENVIRON, rfc 1572. May only be
            requested by the server end. Sends IAC SB ``kind`` SEND IS
            sub-negotiation, rfc1086, using list of ascii string values
            ``self.request_env_values``.

            Does nothing if (WILL, NEW_ENVIRON) has not yet been received,
            or an existing (SB NEW_ENVIRON SEND) request is already pending.
        """
        assert self.is_server
        kind = NEW_ENVIRON
        if not self.remote_option.get(kind, None):
            self.log.debug('cannot send SB %s SEND IS without receipt of '
                    'WILL %s' % (_name_command(kind), _name_command(kind),))
            return
        if self.pending_option.get(SB + kind + SEND + IS, None):
            self.log.debug('cannot send SB %s SEND IS, request pending.' % (
                _name_command(kind), _name_command(kind),))
            return
        self.pending_option[SB + kind + SEND + IS] = True
        response = collections.deque()
        response.extend([IAC, SB, kind, SEND, IS])
        for idx, env in enumerate(self.request_env_values):
            response.extend([bytes(char, 'ascii') for char in env])
            if idx < len(self.request_env_values) - 1:
                response.append(b'\x00')
        response.extend([b'\x03', IAC, SE])
        self.log.debug('send: %r', b''.join(response))
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
            self.log.debug('send: IAC SB XDISPLOC SEND IAC SE')
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
            self.log.debug('send: IAC SB TTYPE SEND IAC SE')
            self.write_iac(b''.join(response))

# `````````````````````````````````````````````````````````````````````````````

    def handle_xdisploc(self, xdisploc):
        """ XXX Receive XDISPLAY value ``xdisploc``, rfc1096.

            xdisploc string format is '<host>:<dispnum>[.<screennum>]'.
        """
        self.log.debug('X Display is %r', xdisploc)

    def handle_ttype(self, ttype):
        """ XXX Receive TTYPE value ``ttype, rfc1091.

            Often value of TERM, or analogous to client's emulation capability,
            common values for non-posix client replies are 'VT100', 'VT102',
            'ANSI', 'ANSI-BBS', or even a mud client identifier. RFC allows
            subsequent requests, the client may solicit multiple times, and
            the client indicates 'end of list' by cycling the return value.
        """
        self.log.debug('Terminal type is %r', ttype)

    def handle_naws(self, width, height):
        """ XXX Receive window size from NAWS protocol as integers.
        """
        self.log.debug('Terminal cols=%d, rows=%d', width, height)

    def handle_env(self, env):
        """ XXX Receive environment variables from OLD andNEW_ENVIRON protocol
            negotiation, as dictionary.
        """
        self.log.debug('env=%r', env)

    def handle_tspeed(self, rx, tx):
        """ XXX Receive terminal speed from TSPEED protocol as integers.
        """
        self.log.debug('Terminal Speed rx:%d, tx:%d', rx, tx)

    def handle_ip(self):
        """ XXX Handle Interrupt Process (IAC, IP) or SLC_IP.
        """
        self.log.debug('IAC IP: Interrupt Process')

    def handle_abort(self):
        """ XXX Handle Abort (IAC, ABORT).

            Similar to Interrupt Process (IP), but means only to abort or
            terminate the process to which the NVT is connected.
        """
        self.log.debug('IAC ABORT: Abort')

    def handle_susp(self):
        """ XXX Handle Suspend Process (IAC, SUSP), rfc1184.

            Suspends the execution of the current process attached to the NVT
            in such a way that another process will take over control of the
            NVT, and the suspended process can be resumed at a later time.

            If the receiving system does not support this functionality, it
            should be ignored.
        """
        self.log.debug('IAC SUSP: Suspend')

    def handle_eof(self):
        """ Handle End of Record (IAC, EOF), rfc1184.
        """
        self.log.debug('IAC EOF: End of File')


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

    def handle_nop(self):
        """ Callback does nothing when IAC + NOP is received.
        """
        self.log.debug('IAC NOP: Null Operation')

    def handle_ga(self):
        """ Callback does nothing when IAC + GA (Go Ahead)is received.
        """
        self.log.debug('IAC GA: Go-Ahead')

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
        self.transport.resume_writing()

    def handle_xoff(self):
        """ Called when IAC + XOFF or SLC_XOFF is received.
        """
        self.log.debug('IAC XOFF: Transmit Off')
        self.transport.pause_writing()

# `````````````````````````````````````````````````````````````````````````````

class TelnetServer(tulip.protocols.Protocol):
    # Telnet options can be negotiated at any time. However, it might be
    # wise to provide a suitable period to pass for initial telnet negotiations
    # to pass before firing ``prompt()``, such as SGA, or perhaps TERM.

    # For those rare clients such as PuTTy known to send negotiation demands
    # immediately after connect, we defer the prompt for at least
    # CONNECT_MINWAIT, and allow no longer than CONNECT_MAXWAIT to elapse
    # if pending telnet options are not replied to (with a warning) before
    # displaying the first prompt.
    CONNECT_MINWAIT = 0.35
    CONNECT_MAXWAIT = 4.00
    CONNECT_DEFERED = 0.15

    def __init__(self, log=logging, default_encoding='utf8'):
        self.log = log
        self._default_encoding = default_encoding
        self.inp_command = collections.deque()
        self._carriage_returned = False
        self._closing = False
        _decoder = None

    def connection_made(self, transport):
        """ hu"""
        self.transport = transport
        self.stream = TelnetStreamReader(transport, server=True)
        # wire AYT and SLC_AYT (^T) to callback ``status()``
        self.stream.set_iac_callback(AYT, self.display_status)
        self.stream.set_slc_callback(SLC_AYT, self.display_status)
        # wire IAC + cmd + LOGOUT to callback ``logout(cmd)``
        self.stream.set_ext_callback(LOGOUT, self.logout)
        # wire IAC EOR (end of record) to ``handle_line``
        self.stream.set_iac_callback(EOR, self.handle_line)
        # wire various 'interrupts', such as AO, IP to ``abort_output``
        self.stream.set_iac_callback(AO, self.abort_output)
        self.stream.set_iac_callback(IP, self.abort_output)
        self.stream.set_iac_callback(BRK, self.abort_output)
        self.stream.set_iac_callback(SUSP, self.abort_output)
        self.stream.set_iac_callback(ABORT, self.abort_output)
        # wire env, tspeed, ttype, naws, xdisploc to set environment
        # variables which can be inspected (or changed) with the
        # client-side 'set' command.
        self.stream.set_ext_callback(NEW_ENVIRON, self.env_update)
        self.stream.set_ext_callback(TTYPE, self.ttype_received)
        self.stream.set_ext_callback(XDISPLOC, self.xdisploc_received)
        self.stream.set_ext_callback(TSPEED, self.tspeed_received)
        self.stream.set_ext_callback(NAWS, self.naws_update)
        self.stream.set_ext_callback(CHARSET, self.charset_received)
        self.client_env = {}
        self.connect_time = time.time()
        self.banner()
        self._negotiate()

    @property
    def encoding(self):
        """ Returns the session's preferred encoding.

            Always 'ascii' unless BINARY has been negotiated, then the
            session value CHARSET is used, or constructor keyword
            argument ``default_encoding`` if undefined.
        """
        return (self.client_env.get('CHARSET', self._default_encoding)
                if self.stream.local_option.get(BINARY, None)
                else 'ascii')

    def echo(self, ucs):
        """ Write unicode string to transport using the preferred encoding.

            If the stream is not in BINARY mode, the string must be made of
            strictly 7-bit ascii characters (value less than 128). Otherwise,
            the session's preferred encoding is used (negotiated by CHARSET).
        """
        self.stream.write(bytes(ucs, self.encoding))

    def banner(self):
        """ The banner method is called on-connect, displaying the
            login banner, and indicates the desired telnet options.

            We send only (WILL, SGA, WILL, ECHO, DO, TTYPE).

            The "magic sequence" WILL SGA, WILL ECHO enables 'kludge' mode,
            the most frequent 'simple' client implementation and most
            compatible with cananical (line-seperated) processing, while
            still providing remote line editing for dumb clients.

            If a client replies to TTYPE, the callback ttype_received() will
            negotiate additional options for more advanced telnet clients the
            first time that it is fired.
        """
        self.echo ('Welcome to {0}!\r\n'.format(__file__,))
        self.stream.iac(WILL, SGA)
        self.stream.iac(WILL, ECHO)
        self.stream.iac(DO, TTYPE)

    def request_advanced_parms(self):
        """ Request advanced telnet options once the remote end has been
            identified as capable of at least TTYPE.
        """
        self.stream.iac(DO, LINEMODE)
        self.stream.iac(WILL, STATUS)
        self.stream.iac(WILL, LFLOW)
        self.stream.iac(DO, NEW_ENVIRON)
        self.stream.iac(WILL, BINARY)
        self.stream.iac(DO, BINARY)
        self.stream.iac(DO, TSPEED)
        self.stream.iac(DO, XDISPLOC)
        self.stream.iac(DO, NAWS)
        self.stream.iac(DO, CHARSET)
        self.stream.iac(DO, EOR)

    _advanced = False
    def ttype_received(self, ttype):
        """ Callback for TTYPE response. The first firing of this callback
            signals an advanced client and is awarded with
            ``request_advanced_params()``, requesting yet another TTYPE
            response, so that a termcap-compatible TERM value can be
            determined from a greater variaty of telnet clients, such as MUD
            clients, which reply 'xterm-256color' on the third and
            subsequent reply.
        """
        if not self._advanced:
            self._advanced = 1
            self.env_update({'TERM': ttype})
            self.request_advanced_parms()
        else:
            # second ttype response from request advanced params, if it is
            # the same as first, stop.
            if ttype == self.client_env['TERM']:
                return
            if self._advanced == 1:
                # assume first value was actually 'CLIENTINFO', in fact,
                # duplicate it, and store the new TERM repeatidly until
                # it matches 'CLIENTINFO', or last recv value of 'TERM'.
                self.env_update({'CLIENTINFO': self.client_env['TERM']})
            elif self._advanced > 8:
                return
            self._advanced += 1
            self.stream.request_ttype()

    def abort_output(self):
        """ Abort output waiting on transport, then call ``prompt()``.
        """
        # Note: abort output is suitable for a stuffed pipe that
        # cannot possibly eat another byte.
        self.transport.discard_output()
        self.log.debug('Abort Output')
        self.display_prompt()

    def _negotiate(self, call_after=None):
        """
        Negotiate options before prompting for input, this method calls itself
        every CONNECT_DEFERED up to CONNECT_MAXWAIT until all pending_options
        have been negotiated. If maximum time expires, options left
        un-negotiated are displayed as a warning.
        When negotiation period is over, ``display_prompt()`` is called unless
        the argument ``call_after`` is specified to a callable.
        """
        call_after = self.display_prompt if call_after is None else call_after
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
                self.log.warn('telnet reply not received for {0}'.format(cmd))
                self.echo('\r\nwarning: no reply received for {0}'.format(cmd))
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
            elif byte == DM:
                self.log.warn('May have received DM ...')  # XXX
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
            if self.stream.local_option.get(ECHO, None) is True:
                self.transport.write(byte)
            self.inp_command.append(byte)
            self._carriage_returned = False

    @property
    def prompt(self):
        """ Returns string suitable for prompt.  This implementation
            evaluates PS1 in a familiar way if set, otherwise returns '$ '.
        """
        return u'% '

    def display_prompt(self, redraw=False):
        """ Prompts client end for input.  When ``redraw`` is ``True``, the
            prompt is re-displayed at the user's current screen row. GA
            (go-ahead) is signalled if SGA (supress go-ahead) is declined.
        """
        try:
            client_inp = b''.join(self.inp_command).decode(self.encoding)
        except UnicodeDecodeError as err:
            self.log.warn(err)
            client_inp = u''
        if redraw:
            self.echo('\r\x1b[K')  # vt102 clear_eol
        else:
            self.echo('\r\n')
        self.echo(self.prompt)
        self.echo(client_inp)
        self.stream.ga()

    def handle_input(self, byte, slc=False):
        """ Handle input received on character-at-a-time basis.

            If byte matches an SLC character, ``slc`` is the function byte.
        """
        if not slc:
            ucs = self.get_decoder().decode(byte, final=False)
            if ucs is not None:
                if ucs.isprintable():
                    self.inp_command.append(byte)
                    self.echo(ucs)
                elif byte in (b'\x0d', b'\x0a'):
                    # carriage return
                    self.handle_line()
        else:
            self.log.debug('unhandled, %r, %r', byte, slc)

    def bell(self):
        """ Callback occurs when inband data is not valid during remote
            line editing, such as SLC EC (^H) at beginning of line.

            Default behavior is to write ASCII BEL to transport, unless
            stream is in character-at-a-time mode, linemode is done locally,
            or 'lit_echo' is not enabled.
        """
        if not self.stream.is_linemode or (
                not self.stream.linemode.local
                and self.stream.linemode.lit_echo):
            self.echo('\x07')

    def logout(self, opt=DO):
        if opt != DO:
            return self.stream.handle_logout(opt)
        self.echo('\r\nBye!\r\n')
        self.close()

    def process_cmd(self, cmd):
        """ Handle input line received on line-at-a-time basis.

        The default implementation provides commands: 'help', 'quit',
        'echo', 'set', and 'status'.
        """
        cmd, args = cmd.rstrip(), []
        if ' ' in cmd:
            cmd, *args = shlex.split(cmd)
        if cmd == 'help':
            self.display_help(*args)
        elif cmd == 'set':
            self.set_cmd(*args)
        elif cmd == 'quit':
            if len(args):
                self.echo('\r\nquit: close session.')
                return
            self.logout()
        elif cmd == 'echo':
            if not args:
                echo_on = (self.stream.local_option.get(ECHO, None) != False)
                self.echo('\r\n\techo is ')
                self.echo('ON' if echo_on else b'OFF')
                return
            elif args not in (['on'], ['ON'], ['off'], ['OFF'],):
                self.echo('\r\necho [on|off]: enable remote echo')
                return
            self.stream.iac(WILL if args in (['on'], ['ON']) else WONT, ECHO)
        elif cmd == 'status':
            if args:
                self.echo('\r\nstatus: displays session parameters')
            else:
                self.display_status()
        else:
            self.echo('\r\nCommand \'{0}\' not understood.'.format(cmd))

    def set_cmd(self, *args):
        """ Provide a simple interface for retrieving and setting session
            variables negotiated about with extended RFC options.
        """
        def usage():
            self.echo('\r\nset [option[=value]]: read/set env values')
        if not args:  # display all values
            self.echo('\r\n\t')
            self.echo('\r\n\t'.join(
                '%s=%r' % (key, value,)
                    for (key, value) in sorted(self.client_env.items())))
        elif (args[0] in ('-h', '--help',) or len(args) != 1):
            usage()
        elif '=' in args[0]:
            # 'set a=1' for value assignment, 'set a=' to clear
            var, value = args[0].split('=', 1)
            value = value.strip()
            if value:
                self.client_env[var] = value
            else:
                self.client_env.pop(var, None)
        else:
            variable_name = args[0].strip()
            # 'set a' to display single value
            if variable_name in self.client_env:
               self.echo('{0}={1}'.format(variable_name,
                   self.client_env[variable_name]))
            else:
                usage()


    def display_help(self, *args):
        self.echo('\r\nAvailable commands, command -h for help:\r\n')
        self.echo('quit, echo, set, status')

    def display_status(self):
        """ Output the status of the telnet session, options, keybindings, etc.
        """
        self.echo('\r\nConnected {0}s ago from {1}.'.format(
            time.time() - self.connect_time,
            self.transport.get_extra_info('addr', 'unknown')))

        self.echo('\r\nLinemode is {0}.'.format(
            'ENABLED' if self.stream.is_linemode else 'DISABLED'))

        self.echo('\r\nFlow control is {0}.'.format(
            'restart-any' if self.stream.lflow_any else 'xon'))

        self.echo('\r\nEncoding is {0}.'.format(self.encoding))

        local_opts = self.stream.local_option.items()
        remote_opts = self.stream.remote_option.items()
        pending_opts = self.stream.pending_option.items()
        list_do = [opt for opt, val in local_opts if val]
        list_dont = [opt for opt, val in local_opts if not val]
        list_will = [opt for opt, val in remote_opts if val]
        list_wont = [opt for opt, val in remote_opts if not val]
        pending = [opt for (opt, val) in pending_opts if val]

        self.echo('\r\nRemote options:')
        if list_do:
            self.echo('\r\n\tDO {0}.'.format(
                ', '.join([_name_commands(opt) for opt in list_do])))
        if list_dont:
            self.echo('\r\n\tDONT {0}.'.format(
                ', '.join([_name_commands(opt) for opt in list_dont])))
        if not list_do and not list_dont:
            self.echo('\r\n\tNone.')

        self.echo('\r\nLocal options:')
        if list_will:
            self.echo('\r\n\tWILL {0}.'.format(
                ', '.join([_name_commands(opt) for opt in list_will])))
        if list_dont:
            self.echo('\r\n\tWONT {0}.'.format(
                ', '.join([_name_commands(opt) for opt in list_wont])))
        if not list_will and not list_wont:
            self.echo('\r\n\tNone.')

        if pending:
            self.echo('\r\nTelnet options pending reply:\r\n\t')
            self.echo('\r\n\t'.join([_name_commands(opt) for opt in pending]))

        if not self.stream.is_linemode:
            self.echo('\r\nLinemode is `Kludge\'.')
        else:
            self.echo('\r\nLinemode is {0}.'.format(self.stream.linemode))
            self.stream.write(b'\r\nSpecial Line Characters:\r\n\t')
            slc_table = ['%-8s [%s]' % (
                _name_slc_command(slc).split('_', 1)[-1].lower(),
                    _name_char(ord(slc_def.val)),)
                    for slc, slc_def in self.stream._slctab.items()
                    if not slc_def.nosupport
                    and slc_def.val != b'\x00']
            self.echo('\r\n\t'.join(slc_table))

    def handle_line(self, slc=False):
        """ Callback received when:
            carriage return is received on input,
            WILL EOR has been negotiated and IAC EOR is received,
            LINEMODE SLC is negotiated and any SLC function character is
                received, indicated by value argument ``slc``.

        input buffered up to this point is queued as ``self.inp_command``,
        and either processed as a bytestring to ``process_command`` and
        cleared, or, when slc is non-None, manipulated. Such as SLC_EC
        causing the last byte of inp_command to be popped from the queue.
        """
        cmd = b''.join(self.inp_command).decode(self.encoding)
        # convert collection of bytes to single bytestring, then decode
        slc_txt = _name_slc_command(slc) if slc is not None else None
        self.log.debug('handle_line: %r (slc=%s)', cmd, slc_txt)
        if not slc:
            try:
                self.process_cmd(cmd)
            except Exception as err:
                self.echo('\r\n{0}'.format(err))
            finally:
                self.inp_command.clear()
            self.display_prompt()
        elif slc == SLC_RP:
            # repaint (^r)
            self.display_prompt(redraw=True)
        elif slc == SLC_EC:
            # erase character (backspace / char 127)
            if 0 == len(self.inp_command):
                self.bell()
            else:
                self.inp_command.pop()
            self.display_prompt(redraw=True)
        elif slc == SLC_EW:
            # erase word (^w)
            if len(self.inp_command) == 0:
                self.bell()
            else:
                self.inp_command.pop()
                while len(self.inp_command) and self.inp_command[-1] != b' ':
                    self.inp_command.pop()
            self.display_prompt(redraw=True)
        elif slc == SLC_EL:
            # erase line (^L)
            self.inp_command.clear()
            self.display_prompt(redraw=True)
        else:
            self.echo('\r\n ** {0} **'.format(
                _name_slc_command(slc).split('_')[-1]))
            self.inp_command.clear()
            self.display_prompt()

    def eof_received(self):
        self.log.info('%s Connection closed by client',
                self.transport.get_extra_info('addr', None))

    def close(self):
        self.transport.close ()
        self._closing = True

    def _get_decoder(self):
        """ Returns a persistent codecs.IncrementalDecoder for the preferred
            encoding.
        """
        if self._decoder is None or self._decoder._encoding != self.encoding:
            self._decoder = codecs.getincrementaldecoder(self.encoding)()
            self._decoder._encoding = self.encoding
        return self._decoder

    def env_update(self, env):
        " Callback receives no environment variables "
        self.client_env.update(env)
        self.log.debug('env_update: %r', env)

    def charset_received(self, charset):
        " Callback receives CHARSET value, rfc2066 "
        self.env_update({'CHARSET': charset.lower()})

    def naws_update(self, width, height):
        " Callback receives NAWS values, rfc1073 "
        self.env_update({'COLUMNS': str(width), 'LINES': str(height)})

    def xdisploc_received(self, xdisploc):
        " Callback receives XDISPLOC value, rfc1096 "
        self.env_update({'DISPLAY': xdisploc})

    def tspeed_received(self, rx, tx):
        " Callback receives TSPEED values, rfc1079 "
        self.env_update({'TSPEED': '%s,%s' % (rx, tx)})



class CharacterTelnetServer(TelnetServer):
    """ Implement a very simple character-at-a-time (kludge mode) server.
    """
    def banner(self):
        self.stream.write(b'Welcome to ')
        self.stream.write(bytes(__file__, 'ascii', 'replace'))
        self.stream.write(b'\r\n')
        self.stream.iac(WILL, ECHO)
        self.stream.iac(WILL, SGA)

# `````````````````````````````````````````````````````````````````````````````
#
# debug routines for displaying raw telnet bytes

_DEBUG_OPTS = dict([(value, key)
                    for key, value in globals().items() if key in
                  ('LINEMODE', 'LMODE_FORWARDMASK', 'NAWS', 'NEW_ENVIRON',
                      'ENCRYPT', 'AUTHENTICATION', 'BINARY', 'SGA', 'ECHO',
                      'STATUS', 'TTYPE', 'TSPEED', 'LFLOW', 'XDISPLOC', 'IAC',
                      'DONT', 'DO', 'WONT', 'WILL', 'SE', 'NOP', 'DM', 'TM',
                      'BRK', 'IP', 'ABORT', 'AO', 'AYT', 'EC', 'EL', 'EOR',
                      'GA', 'SB', 'EOF', 'SUSP', 'ABORT', 'LOGOUT',
                      'CHARSET')])
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

def _name_command(byte):
    """ Given an IAC byte, return its mnumonic global constant. """
    return (repr(byte) if byte not in _DEBUG_OPTS
            else _DEBUG_OPTS[byte])

def _name_commands(cmds, sep=' '):
    return ' '.join([
        _name_command(bytes([byte])) for byte in cmds])

def _bin8(number):
    """ return binary representation of ``number``, padded to 8 bytes. """
    prefix, value = bin(number).split('b')
    return '0b%0.8i' % (int(value),)

def _name_char(number):
    """ Return string of an 8-bit input character value, ``number``. """
    char = chr(number)
    if char.isprintable():
        return char
    if number <= 0:
        return 'None'
    elif number <= 26:
        return '^%s' % (chr(ord('a') + (number - 1)),)
    elif number <= 31:
        return '^%s' % (r'[\]^_'[number - 32])
    elif number == 127:
        return 'del'
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
    log = logging.getLogger()
    log.setLevel(logging.DEBUG)
    loop = tulip.get_event_loop()
    locale.setlocale(locale.LC_ALL, '')
    enc = locale.getpreferredencoding()
    func = loop.start_serving(lambda: TelnetServer(default_encoding=enc),
            args.host, args.port)

    for sock in loop.run_until_complete(func):
        logging.debug('serving on %s', sock.getsockname())
    loop.run_forever()

if __name__ == '__main__':
    main()

