#!/usr/bin/env python3
"""
This project implements a Telnet client and server protocol, analogous to
the standard ``telnetlib`` module, with a great many more capabilities.

This implementation uses the 'tulip' project, the asynchronous networking
model to become standard with python 3.4, and requires Python 3.3.

The ``BasicTelnetServer`` protocol does not insist on any telnet options on
connect through the ``banner`` method, and the client defaults to the basic
Telnet NVT.

The ``CharacterTelnetServer`` protocol is suitable for character-at-a-time
input, such as would be attached directly to gnu readline or an interactive
shell or pty, games such as nethack, or some telnet bulletin board systems.
It insists on (DONT, LINEMODE), (SGA, ECHO).

The ``LinemodeTelnetServer`` protocol attempts to negotiate the extended
LINEMODE as described in rfc1116. the ``handle_line`` callback is called
at carriage return, or, when SLC characters are processed on input,
with argument ``slc`` as the function (such as SLC_EOF when user presses ^D).

Many standard and extended telnet RFC protocols are implemented, which are
not negotiated about or understood with the default ``telnetlib`` python
module. A summary of RFC's implemented (or not implemented) below:

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
[*] RFC 1123 Requirements for Internet Hosts                      Oct 1989
[*] RFC 1143 The Q Method of Implementing .. Option Negotiation   Feb 1990
[*] RFC 1372 Telnet Remote Flow Control Option                    Oct 1992

x = complete
* = in-progress

1. DM (Data Mark) with TCP Urgent bit set is not supported, the underlying
   framework does not support it (third argument to select, errorfds).

Missing:
    BSD Telnetd implements workarounds for 4.4BSD era clients, that is,
    those that reply (WILL, ECHO).

Additional Resources,
   "Telnet Protocol," MIL-STD-1782, U.S. Department of Defense, May 1984.
   "Mud Terminal Type Standard," http://tintin.sourceforge.net/mtts/
   "Telnet Protocol in C-Kermit 8.0 and Kermit 95 2.0 http://www.columbia.edu/kermit/telnet80.html
   "Comments on the new TELNET Protocol and its Implementation," RFC 559


TODO:

    flush in/flush out flag

    Test OOB data with 'DM' using stevens socat tool ..

    Issue with tulip -- doesn't handle OOB data, need to derive
    BaseSelectorEventLoop, ovverride:
        sock_recv(sock, n), _sock_recv(fut, registered, sock, n),
        sock_sendall(sock, data), _sock_sendall(fut, registered, sock, data),
    to accept additional argument [flags], like sock.send() and recv().
    Then, have data_received receive additional argument, urgent=True ?

    Need to implement flow control (^s); cease sending bytes on transport
    until ^q is received, tulip does not provide this interface.  --
    Directly pull _buffer to local value, .clear() it, then re-queue on ^q.

    A series of callbacks for LINEMODE and standard EC, EL, etc; this should
    allow a readline-line interface to negotiate correct behavior, regardless
    of mode. Withholding on implementation: reaching for clarity without
    brevity.

    A simple telnet client .. with stdin as tulip  ..?


CHECKLIST, per RFC 1123, 3.5. TELNET REQUIREMENTS SUMMARY
                                                             /must
                                                            / /should
                                                           / / /may
                                                          / / / /should not
                                                         / / / / /must not
FEATURE                                         |SECTION/ / / / / /
------------------------------------------------|-------|-|-|-|-|-|
                                                |       | | | | | |
Option Negotiation                              | 3.2.1 |x| | | | |
  Avoid negotiation loops                       | 3.2.1 |x| | | | |
  Refuse unsupported options                    | 3.2.1 |x| | | | |
  Negotiation OK anytime on connection          | 3.2.1 | |x| | | |
  Default to NVT                                | 3.2.1 |x| | | | |
  Send official name in Term-Type option        | 3.2.8 |x| | | | |
  Accept any name in Term-Type option           | 3.2.8 |x| | | | |
  Implement Binary, Suppress-GA options         | 3.3.3 |x| | | | |
  Echo, Status, EOL, Ext-Opt-List options       | 3.3.3 | |x| | | |*
  Implement Window-Size option if appropriate   | 3.3.3 | |x| | | |
  Server initiate mode negotiations             | 3.3.4 | |x| | | |
  User can enable/disable init negotiations     | 3.3.4 | |x| | | |
                                                |       | | | | | |
Go-Aheads                                       |       | | | | | |
  Non-GA server negotiate SUPPRESS-GA option    | 3.2.2 |x| | | | |
  User or Server accept SUPPRESS-GA option      | 3.2.2 |x| | | | |
  User Telnet ignore GA's                       | 3.2.2 | | |x| | |
                                                |       | | | | | |
Control Functions                               |       | | | | | |
  Support SE NOP DM IP AO AYT SB                | 3.2.3 |x| | | | |  note 1.
  Support EOR EC EL Break                       | 3.2.3 | | |x| | |*
  Ignore unsupported control functions          | 3.2.3 |x| | | | |*
  User, Server discard urgent data up to DM     | 3.2.4 |x| | | | |* note 1.
  User Telnet send "Synch" after IP, AO, AYT    | 3.2.4 | |x| | | |*
  Server Telnet reply Synch to IP               | 3.2.4 | | |x| | |*
  Server Telnet reply Synch to AO               | 3.2.4 |x| | | | |*
  User Telnet can flush output when send IP     | 3.2.4 | |x| | | |*
                                                |       | | | | | |
Encoding                                        |       | | | | | |
  Send high-order bit in NVT mode               | 3.2.5 | | | |x| |
  Send high-order bit as parity bit             | 3.2.5 | | | | |x|
  Negot. BINARY if pass high-ord. bit to applic | 3.2.5 | |x| | | |
  Always double IAC data byte                   | 3.2.6 |x| | | | |
  Double IAC data byte in binary mode           | 3.2.7 |x| | | | |
  Obey Telnet cmds in binary mode               | 3.2.7 |x| | | | |
  End-of-line, CR NUL in binary mode            | 3.2.7 | | | | |x|
                                                |       | | | | | |
End-of-Line                                     |       | | | | | |
  EOL at Server same as local end-of-line       | 3.3.1 |x| | | | |
  ASCII Server accept CR LF or CR NUL for EOL   | 3.3.1 |x| | | | |
  User Telnet able to send CR LF, CR NUL, or LF | 3.3.1 |x| | | | |
    ASCII user able to select CR LF/CR NUL      | 3.3.1 | |x| | | |
    User Telnet default mode is CR LF           | 3.3.1 | |x| | | |
  Non-interactive uses CR LF for EOL            | 3.3.1 |x| | | | |
                                                |       | | | | | |
User Telnet interface                           |       | | | | | |*
  Input & output all 7-bit characters           | 3.4.1 | |x| | | |*
  Bypass local op sys interpretation            | 3.4.1 | |x| | | |*
  Escape character                              | 3.4.1 |x| | | | |*
     User-settable escape character             | 3.4.1 | |x| | | |*
  Escape to enter 8-bit values                  | 3.4.1 | | |x| | |*
  Can input IP, AO, AYT                         | 3.4.2 |x| | | | |*
  Can input EC, EL, Break                       | 3.4.2 | |x| | | |*
  Report TCP connection errors to user          | 3.4.3 | |x| | | |*
  Optional non-default contact port             | 3.4.4 | |x| | | |*
  Can spec: output flushed when IP sent         | 3.4.5 | |x| | | |*
  Can manually restore output mode              | 3.4.5 | |x| | | |*

[x] Where RFC 854 implies that the other side may reject a request to
    enable an option, it means that you must accept such a rejection.

[x] It MUST therefore remember that it is negotiating a WILL/DO, and this
    negotiation state MUST be separate from the enabled state and from
    the disabled state.  During the negotiation state, any effects of
    having the option enabled MUST NOT be used.

[x] Rule: Remember DONT/WONT requests
[x] Rule: Prohibit new requests before completing old negotiation

*: MISSING

"""
import collections
import logging
import argparse
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
DEFAULT_CALLBACKS = ((BRK, 'brk'), (IP, 'ip'), (AO, 'ao'), (AYT, 'ayt'),
        (EC, 'ec'), (EL, 'el'), (EOR, 'eor'), (EOF, 'eof'), (SUSP, 'susp'),
        (ABORT, 'abort'), (NOP, 'nop'), (DM, 'dm'),)
        # see: TelnetStreamReader._default_callbacks
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
SLC_IAC_CALLBACKS = (
        (SLC_SYNCH, 'dm'), (SLC_BRK, 'brk'), (SLC_AO, 'ao'),
        (SLC_AYT, 'ayt'), (SLC_EOR, 'eor'), (SLC_ABORT, 'abort'),
        (SLC_EOF, 'eof'), (SLC_SUSP, 'susp'), (SLC_EC, 'ec'),
        (SLC_EL, 'el'), (SLC_XON, 'xon'), (SLC_XOFF, 'xoff'),
        (SLC_IP, 'ip'), )
(SLC_FLUSHOUT, SLC_FLUSHIN, SLC_ACK
    ) = (bytes([32]), bytes([64]), bytes([128]))
(SLC_NOSUPPORT, SLC_CANTCHANGE, SLC_VARIABLE, SLC_DEFAULT
    ) = (bytes([const]) for const in range(4))
SLC_LEVELBITS = 0x03
LINEMODE_MODE = bytes([1])
LINEMODE_EDIT = bytes([1])
LINEMODE_TRAPSIG = bytes([2])
LINEMODE_MODE_ACK = bytes([4])
LINEMODE_FORWARDMASK = bytes([2])
LINEMODE_SLC = bytes([3])
SB_MAXSIZE = 4*1024 # 4k
SLC_MAXSIZE = 4*1024 # 4k

class SLC_definition(object):
    def __init__(self, flag, value):
        self.flag = flag
        self.val = value

    @property
    def level(self):
        """ Returns SLC level of support """
        return bytes([ord(self.val) & SLC_LEVELBITS])

    @property
    def ack(self):
        """ Returns True if SLC_ACK bit is set """
        return ord(self.flag) & ord(SLC_ACK)

    @property
    def flushin(self):
        """ Returns True if SLC_FLUSHIN bit is set """
        return ord(self.flag) & ord(SLC_FLUSHIN)

    @property
    def flushout(self):
        """ Returns True if SLC_FLUSHIN bit is set """
        return ord(self.flag) & ord(SLC_FLUSHOUT)

class SLC_nosupport(SLC_definition):
    def __init__(self):
        SLC_definition.__init__(self, SLC_NOSUPPORT, _POSIX_VDISABLE)

# the V* constant values are duplicated from termios, windows platforms
# may ImportError? See bsd telnetd sys_term.c:spcset for reference source.

_POSIX_VDISABLE = b'\xff'  # note: same value as IAC (must be escaped!)
DEFAULT_SLC_TAB = {
        # The following cannot be supported. Just following bsd telnetd,
        # VEOL, _POSIX_VDISABLE
        SLC_FORW1: SLC_definition(SLC_NOSUPPORT, _POSIX_VDISABLE),
        # VEOL2, _POSIX_VDISABLE
        SLC_FORW2: SLC_definition(SLC_NOSUPPORT, _POSIX_VDISABLE),
        # The following are default values, it is given to the user,
        # and the user can indicate its desire to use them with a request
        # of SLC_DEFAULT. Otherwise, they may chose to change them, and we
        # are always OK with that.
        # VEOF ^D
        SLC_EOF: SLC_definition(SLC_VARIABLE, b'\x04'),
        # VERASE backspace
        SLC_EC: SLC_definition(SLC_VARIABLE, b'\x7f'),
        # VKILL ^U
        SLC_EL: SLC_definition(SLC_VARIABLE, b'\x15'),
        # VINTR ^C
        SLC_IP: SLC_definition(bytes([ord(SLC_VARIABLE)
            | ord(SLC_FLUSHIN) | ord(SLC_FLUSHOUT)]), b'\x03'),
        # VQUIT ^\ (SIGQUIT)
        SLC_ABORT: SLC_definition(bytes([ord(SLC_VARIABLE)
            | ord(SLC_FLUSHIN) | ord(SLC_FLUSHOUT)]), b'\x1c'),
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
        SLC_AO: SLC_definition(bytes([ord(SLC_VARIABLE)
            | ord(SLC_FLUSHOUT)]), b'\x0f'),
        # VSUSP, ^Z
        SLC_SUSP: SLC_definition(bytes([ord(SLC_VARIABLE)
            | ord(SLC_FLUSHIN)]), b'\x1a'),
        # VSTATUS, ^T
        SLC_AYT: SLC_definition(SLC_VARIABLE, b'\x14'),
        # Break, Synch, and EOR are set
        # to SLC_DEFAULT with value 0; to
        # indicate that we have no default
        # for those values.
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
    pending_option = {}
    local_option = {}
    remote_option = {}
    request_env = (
            "USER HOSTNAME UID TERM COLUMNS LINES DISPLAY LANG "
            "SYSTEMTYPE ACCT JOB PRINTER SFUTLNTVER SFUTLNTMODE").split()
    lflow_any = False

    _iac_received = False   # has IAC been recv?
    _slc_received = False   # has SLC value been received?
    _cmd_received = False   # has IAC (DO, DONT, WILL, WONT) been recv?
    _sb_received = False    # has IAC SB been recv?
    _tm_sent = False        # has IAC DO TM been sent?
    _dm_recv = False        # has IAC DM been recv?
    # _iac_callbacks is a dictionary of telnet command options, such as IP,
    # ABORT, EL (Erase line) to the handling function, which receives no
    # arguments.
    _iac_callbacks = {}
    # _slc_callbacks is same dictionary, but keyed by SLC function bytes.
    _slc_callbacks = {}
    # _iac_slctab is a dictionary of SLC functions, such as SLC_IP,
    # to a tuple of the handling character and support level, such as
    # (b'\x08', SLC_VARIABLE)
    _slctab = {}
    def __init__(self, transport, client=None, server=None,
                 debug=False, log=logging):
        """ By default, the stream is *decoded as a telnet server* unless
        keyword argument ``client`` is set to ``True``.  """
        self.log = log
        self.debug = debug
        assert client == None or server == None, (
            "Arguments 'client' and 'server' are mutually exclusive")
        self.server = client == False or (
                client in (None, False) and server in (None, True))
        tulip.StreamReader.__init__(self)
        # transport is necessary, as telnet commands often require a
        # series acknowledgements, replies, or requests.
        self.transport = transport
        # sub-negotiation buffer holds bytes between (IAC, SB) and (IAC, SE).
        self._sb_buffer = collections.deque()
        # slc buffer holds slc response, using _slc_add
        self._slc_buffer = collections.deque()
        # initialize callback handlers for basic IAC commands
        self._default_callbacks()
        # initialize linemode negotiation tab with defaults
        self._default_slc()

    def _default_callbacks(self):
        """ set property ``_iac_callbacks`` and ``_slc_callbacks`` to default
        method callbacks of matching names, such that IAC + IP, or the value
        negotiated for SLC_IP signals a callback to `self.handle_ip``.
        """
        for iac_cmd, key in DEFAULT_CALLBACKS:
            self.set_iac_callback(iac_cmd, getattr(self, 'handle_%s' % (key,)))
        for slc_cmd, key in SLC_IAC_CALLBACKS:
            self.set_slc_callback(slc_cmd, getattr(self, 'handle_%s' % (key,)))

    def _default_slc(self):
        """ set property ``_slctab`` to default SLC tabset, unless it
            is unlisted (as is the case for SLC_MCL+), then set as
            SLC_NOSUPPORT _POSIX_VDISABLE (0xff) which incidentently
            is also IAC, and must be escaped. """
        for slc in range(NSLC + 1):
            self._slctab[bytes([slc])] = DEFAULT_SLC_TAB.get(bytes([slc]),
                    SLC_definition(SLC_NOSUPPORT, _POSIX_VDISABLE))

    def write(self, data):
        """ Write data bytes to transport end connected to stream reader.

            IAC is always escaped with IAC+IAC
        """
        self.transport.write(_escape_iac(data))

    def write_iac(self, data):
        """ Write IAC data bytes to transport end connected to stream reader.

            IAC is never escaped.
        """
        self.transport.write(data)

    def set_iac_callback(self, cmd, func):
        """ Register ``func`` as callback for receipt of IAC command ``cmd``.

            BRK, IP, AO, AYT, EC, EL, EOR, EOF, SUSP, ABORT, and NOP.
        """
        assert callable(func), ('Argument func must be callable')
        self._iac_callbacks[cmd] = func

    def set_slc_callback(self, slc, func):
        """ Register ``func`` as callback for receipt of SLC character
        negotiated for the SLC command ``slc``.

            SLC_SYNCH, SLC_BRK, SLC_IP, SLC_AO, SLC_AYT, SLC_EOR, SLC_ABORT,
            SLC_EOF, SLC_SUSP, SLC_EC, SLC_EL, SLC_EW, SLC_RP, SLC_LNEXT,
            SLC_XON, SLC_XOFF, (...) """
        assert callable(func), ('Argument func must be callable')
        self._slc_callbacks[slc] = func

    def ga(self):
        """ Send IAC GA (Go-Ahead) if SGA is declined.  Otherwise,
            nothing happens.

            "we would like to lobby for suppress GA the default. It appears
            that only a few Hosts require the GA's (AMES-67 and UCLA-CON)." """
        if not self.local_option.get(SGA, True):
            self.write_iac(IAC + GA)

    @property
    def is_linemode(self):
        """ Returns true if telnet stream appears to be in linemode.

        The default Network Terminal is always in linemode, unless
        explicitly set False (client sends: WONT, LINEMODE),
        or implied by server (server sends: WILL ECHO, WILL SGA). """
        if self.server:
            return self.remote_option.get(LINEMODE, False) or not (
                    self.local_option.get(ECHO, None) and
                    self.local_option.get(SGA, None))
        # same heuristic is reversed for client point of view,
        return self.local_option.get(LINEMODE, None) or (
                self.remote_option.get(ECHO, None) and
                self.remote_option.get(SGA, None))

    @property
    def idle(self):
        """ Return time since bytes last received by remote end. This
            includes telnet commands, such as IAC + NOP. """
        return time.time() - self._last_input_time

    def iac(self, cmd, opt):
        """ Send IAC <cmd> <opt> to remote end.

        This method has many side-effects for asynchronous telnet negotiation
        option state tracking. Various RFC assertions are made to assure only
        legal commands for client or server are sent, and is appropriate for
        the state of option processing.

        For iac ``cmd`` DO and DONT, ``self.pending_option[cmd + opt]``
        is set True.
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
            self.log.debug('set pending_option[%s + %s] = True' % (
                _name_command(cmd), _name_command(opt),))
        elif cmd == WILL and not self.local_option.get(opt, None):
            self.pending_option[cmd + opt] = True
            self.log.debug('set pending_option[%s + %s] = True' % (
                _name_command(cmd), _name_command(opt),))
        elif(cmd == WONT and self.local_option.get(opt, None) != False):
            self.pending_option[cmd + opt] = True
            self.log.debug('set pending_option[%s + %s] = True' % (
                _name_command(cmd), _name_command(opt),))
        self.write_iac(IAC + cmd + opt)
        self.log.debug('send IAC %s %s' % (
            _name_command(cmd), _name_command(opt),))

    def feed_byte(self, byte):
        """ Receive byte arrived by ``TelnetProtocol.data_received()``.

        Copy bytes from ``data`` into ``self.buffer`` through a state-logic
        flow, detecting and handling telnet commands and negotiation options.

        Returns True if byte is par of out of band sequence (and should not
        be echoed when ECHO is requested by client). """
        assert type(byte) == bytes and len(byte) == 1
        self.byte_count += 1
        self._last_input_time = time.time()
        return self._parser(byte)

    def _parser(self, byte):
        """ This parser processes all telnet data, tracks state, and when
        out-of-band Telnet data, marked by byte IAC arrives, susbsequent
        bytes toggle or process negotiation through callbacks.

        Returns True if out of band data was handled, otherwise False.

        Extending or changing protocol capabilities shouldn't necessarily
        require deriving this method, but the methods it delegates to, mainly
        those methods beginning with 'handle_', or ``parse_iac_command``,
        and ``parse_subnegotiation``.

        As this parse receives a single byte at a time, active states are
        stored as booleans ``_iac_received`` and ``_sb_received``, and behaves
        on in-band command data accordingly.  The Value of ``_cmd_received``
        is equal to the telnet command and is non-None when that state is
        active.

        Negotiated options are stored in dict ``self.local_option``,
        and ``self.remote_option``. Pending replies are noted with
        ``self.pending_option``, keyed by option byte.
        """
        # _slc_received toggled true if inband character matches an SLC value.
        self._slc_received = False
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
                    self.log.debug('set pending_option[%s + %s] = False',
                        _name_command(WILL), _name_command(opt),)
                if not self.local_option.get(opt, False):
                    self.local_option[opt] = True
                    self.log.debug('set local_option[%s] = True',
                        _name_command(opt),)
            elif self._cmd_received == DONT:
                self.handle_dont(opt)
                if self.pending_option.get(WILL + opt, False):
                    self.pending_option[WILL + opt] = False
                    self.log.debug('set pending_option[%s + %s] = False',
                        _name_command(WILL), _name_command(opt),)
                if self.local_option.get(opt, True):
                    self.local_option[opt] = False
                    self.log.debug('set local_option[%s] = False',
                        _name_command(opt),)
            elif self._cmd_received == WILL:
                if not self.pending_option.get(DO + opt):
                    self.log.debug('received unnegotiated WILL')
                    assert opt in (LINEMODE,), (
                            'Received WILL %s without corresponding DO' % (
                                _name_command(opt),))
                self.handle_will(opt)
                if self.pending_option.get(DO + opt, False):
                    self.pending_option[DO + opt] = False
                    self.log.debug('set pending_option[%s + %s] = False',
                        _name_command(DO), _name_command(opt),)
                if self.pending_option.get(DONT + opt, False):
                    # This end previously requested remote end *not* to
                    # perform a a capability, but remote end has replied
                    # with a WILL. Occurs due to poor timing at negotiation
                    # time. DO STATUS is often used to settle the difference.
                    self.pending_option[DONT + opt] = False
                    self.log.debug('set pending_option[%s + %s] = False',
                        'DONT', _name_command(opt),)
            elif self._cmd_received == WONT:
                self.handle_wont(opt)
                if self.pending_option.get(DO + opt, False):
                    self.pending_option[DO + opt] = False
                    self.log.debug('set pending_option[%s + %s] = False',
                        'DO', _name_command(opt),)
                if self.pending_option.get(DONT + opt, False):
                    self.pending_option[DONT + opt] = False
                    self.log.debug('set pending_option[%s + %s] = False',
                        'DONT', _name_command(opt),)
            self._cmd_received = False
        elif self._dm_recv:
            # IAC DM was previously received; discard all input until
            # IAC DM is received again by remote end.
            self.log.debug('discarded by data-mark: %r' % (byte,))
        elif self._tm_sent:
            # IAC DO TM was previously sent; discard all input until
            # IAC WILL TM or IAC WONT TM is received by remote end.
            self.log.debug('discarded by timing-mark: %r' % (byte,))
        else:
            (func, slc_name, slc_def) = self._slc_snoop(byte)
            if slc_name is not None:
                if slc_def.flushin:
                    # SLC_FLUSHIN not supported, requires SYNCH (urgent TCP).
                    pass
                    #self.send_synch()
                if slc_def.flushout:
                    self.iac(WILL, TM)
                # allow caller to know which SLC function caused linemode
                # to process, even though CR was not yet discovered.
                self._slc_received = slc_name
            self.buffer.append(byte)
            if func is not None:
                func()
            return self._slc_received
        return True

    def _slc_snoop(self, byte):
        # scan byte for SLC function mappings, if any, return function
        for slc_func, slc_def in self._slctab.items():
            if byte == slc_def.val and slc_def.val != b'\x00':
                self.log.debug('recv slc byte (func, flag): (%s, %s)',
                        _name_slc_command(slc_func),
                        _name_slc_modifier(slc_def.flag))
                return (self._slc_callbacks.get(slc_func, None),
                        slc_func, slc_def)
        return None, None, None

    def parse_iac_command(self, cmd):
        """ Handle IAC commands, calling self.handle_<cmd> where <cmd> is
        one of 'brk', 'ip', 'ao', 'ayt', 'ec', 'el', 'eor', 'eof', 'susp',
        or 'abort', if exists. Otherwise unhandled. Callbacks can be
        re-directed or extended using the ``set_iac_callback(cmd, func)``
        method.
        """
        if cmd == DM:
            self._dm_recv = True
            # IAC DM was previously received; discard all input until
            # IAC DM is received again by remote end.
            self.log.debug('DM received, input ignored until DM')
        elif cmd in self._iac_callbacks:
            self._iac_callbacks[cmd]()
        else:
            raise ValueError('unsupported IAC sequence, %r' % (cmd,))

    def handle_sb_linemode_forwardmask(self, buf):
        self.log.debug('handle_sb_linemode_forwardmask: %r' % (buf,))

    def parse_subnegotiation(self, buf):
        """ Callback containing the sub-negotiation buffer. Called after
        IAC + SE is received, indicating the end of sub-negotiation command.

        SB options TTYPE, XDISPLOC, NEW_ENVIRON, NAWS, and STATUS, are
        supported. Changes to the default responses should derive callbacks
        ``handle_ttype``, ``handle_xdisploc``, ``handle_env``, and
        ``handle_naws``.

        Implementors of additional SB options should extend this method. """
        assert buf, ('SE: buffer empty')
        assert buf[0] != b'\x00', ('SE: buffer is NUL')
        assert len(buf) > 1, ('SE: buffer too short: %r' % (buf,))
        cmd = buf[0]
        if self.pending_option.get(SB + cmd, False):
            self.pending_option[SB + cmd] = False
            self.log.debug('set pending_option[SB + %s] = False',
                    _name_command(cmd))
        else:
            self.log.warn('[SB + %s] unexpected', _name_command(cmd))
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
        self.handle_tspeed(int(rx), int(tx))

    def _handle_sb_xdisploc(self, buf):
        assert buf.popleft() == XDISPLOC
        assert buf.popleft() == IS
        xdisploc_str = b''.join(buf).decode('ascii')
        self.log.debug('sb_xdisploc: %s', xdisploc_str)
        self.handle_xdisploc(xdisploc_str)

    def _handle_sb_ttype(self, buf):
        assert buf.popleft() == TTYPE
        assert buf.popleft() == IS
        ttype_str = b''.join(buf).decode('ascii')
        self.log.debug('sb_ttype: %s', ttype_str)
        self.handle_ttype(ttype_str)

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
        self.handle_env(env)

    def _handle_sb_naws(self, buf):
        assert buf.popleft() == NAWS
        columns = str((256 * ord(buf[0])) + ord(buf[1]))
        rows = str((256 * ord(buf[2])) + ord(buf[3]))
        self.log.debug('sb_naws: %s, %s', int(columns), int(rows))
        self.handle_naws(int(columns), int(rows))

    def _handle_sb_lflow(self, buf):
        assert buf.popleft() == LFLOW
        assert self.local_option.get(LFLOW, None) is True, (
            'received IAC SB LFLOW wihout IAC DO LFLOW')
        self.log.debug('sb_lflow: %r', buf)

    def _send_sb_linemode_mode(self):
        LINEMODE_EDIT = bytes([1])
        LINEMODE_TRAPSIG = bytes([2])
        mask = 0
        if self.linemode['edit']:
            mask |= ord(LINEMODE_EDIT)
        if self.linemode['trapsig']:
            mask |= ord(LINEMODE_TRAPSIG)
        mask = bytes([mask | ord(LINEMODE_MODE_ACK)])
        self.write_iac(IAC + SB + LINEMODE + LINEMODE_MODE + mask + IAC + SE)
        self.log.debug('parse linemode to mask: %r' % (self.linemode,))
        self.log.debug('send IAC SB LINEMODE MODE %r IAC SE' % (mask,))

    def _handle_sb_linemode_cmd(self, mask):
        self.linemode['edit'] = bool(mask & ord(LINEMODE_EDIT))
        self.linemode['trapsig'] = bool(mask & ord(LINEMODE_TRAPSIG))
        ack = bool(mask & ord(LINEMODE_MODE_ACK))
        self.debug('recv linemode%s: mask: %r from mask %r' % (
            ' acknowledgement' if ack else '', mask, self.linemode))
        self._send_sb_linemode_mode()

    def _handle_sb_linemode(self, buf):
        assert buf.popleft() == LINEMODE
        cmd = buf.popleft()
        if cmd == LINEMODE_MODE:
            mask = ord(buf.popleft())
            self._handle_sb_linemode_cmd(mask)
        elif cmd == LINEMODE_SLC:
            self._handle_sb_linemode_slc(buf)
        elif cmd in (DO, DONT, WILL, WONT):
            opt = buf.popleft()
            assert opt == LINEMODE_FORWARDMASK, (
                    'Illegal IAC SB LINEMODE %s %r' % (
                        _name_command(cmd), opt))
            if cmd == DO:
                self.handle_sb_linemode_forwardmask(buf)
            assert buf[1] == LINEMODE_SLC # ?

# `````````````````````````````````````````````````````````````````````````````
# LINEMODE, translated from bsd telnet

    def _handle_sb_linemode_slc(self, buf):
        assert 0 == len(buf) % 3, ('SLC buffer must be byte triplets')
        while len(buf):
            func = buf.popleft()
            flag = buf.popleft()
            value = buf.popleft()
            self._slc_process(func, SLC_definition(flag, value))
        self._slc_send()


    def _slc_send(self):
        """ Send all special characters that are supported """
        if 0 == len(self._slc_buffer):
            self.log.debug('slc_send: buffer empty')
            return
        # do not escape an IAC byte header, but escape slc
        # buffer reply (_POSIX_VDISABLE(0xff) becomes IAC + IAC (0xffff))
        self.write_iac(IAC + SB + LINEMODE + LINEMODE_SLC)
        self.write(bytes([ord(byte) for byte in self._slc_buffer]))
        self.write_iac(IAC + SE)
        self.log.debug('slc_send: IAC + SB + LINEMODE + SLC + %r + IAC + SE',
                b''.join(self._slc_buffer))
        self._slc_buffer.clear()


    def _slc_add(self, func, slc_def=None):
        """ buffer slc triplet response as (function, flag, value),
            for the given SLC_func byte and slc_def instance providing
            byte attributes ``flag`` and ``val``. If no slc_def is provided,
            the slc definition of ``_slctab`` is used by key ``func``.
        """
        assert len(self._slc_buffer) < SLC_MAXSIZE, ('SLC: buffer full')
        if slc_def is None:
            slc_def = self._slctab[func]
        self._slc_buffer.extend([func, slc_def.flag, slc_def.val])
        self.log.debug('_slc_add (%s, %s, %r)',
            _name_slc_command(func),
            _name_slc_modifier(slc_def.flag),
            slc_def.val)

    def _slc_process(self, func, slc_def):
        """ Process an SLC definition provided by remote end.

            Ensure the function definition is in-bounds and an SLC option
            we support. Store SLC_VARIABLE changes to self._slctab, keyed
            by SLC byte function ``func``.

            The special definition (0, SLC_DEFAULT|SLC_VARIABLE, 0) has the
            side-effect of replying with a full slc tabset, resetting to
            the default tabset, if indicated.  """
        self.log.debug('_slc_process (%s, %s, %r)',
            _name_slc_command(func),
            _name_slc_modifier(slc_def.flag),
            slc_def.val)

        # out of bounds checking
        if ord(func) > NSLC:
            self.log.warn('SLC not supported: (%r)', func)
            self._slc_add(func, SLC_nosupport())
            return

        # process special request (0, SLC_DEFAUT, 0) and (0, SLC_VARIABLE, 0).
        if 0 == ord(func):
            if slc_def.level == SLC_DEFAULT:
                self.log.info('SLC_DEFAULT')
                self._default_slc()
                self._slc_send()
            elif slc_def.level == SLC_VARIABLE:
                self.log.info('SLC_VARIABLE')
                self._slc_send()
            return

        # update slc tabset
        mylevel, myvalue = (self._slctab[func].level, self._slctab[func].val)
        if slc_def.level == mylevel and (
                myvalue == slc_def.val or slc_def.ack):
            # ignore if: function level is same as ours, and
            # value is equal to ours or the ack bit is set.
            return
        elif slc_def.ack:
            # also ignore if: ack bit is set but value is unequal to ours,
            # aparently a timing issue -- additional sub-negotiations settle
            # the issue.
            self.log.debug('slc ack bit set for value mismatch: (%r,%r)',
                    myvalue, slc_def.val)
        else:
            self._slc_change(func, slc_def)

    def _slc_change(self, func, slc_def):
        """ Update SLC tabset with SLC definition provided by remote end.

            Modify prviate attribute ``_slctab`` appropriately for the level
            and value indicated, except for slc tab functions of SLC_NOSUPPORT.

            Reply as appropriate ..
        """
        if slc_def.level == SLC_NOSUPPORT:
            # client end reports SLC_NOSUPPORT; use a
            # nosupport definition with ack bit set
            self._slctab[func] = SLC_nosupport()
            self._slctab[func].flag = bytes(
                    [ord(SLC_NOSUPPORT) | ord(SLC_ACK)])
            self._slc_add(func)
            return

        mylevel, myvalue = (self._slctab[func].level, self._slctab[func].val)
        if slc_def.level == SLC_DEFAULT:
            # client end requests we use our default level
            if mylevel == SLC_DEFAULT:
                # client end telling us to use SLC_DEFAULT on an SLC we do not
                # support (such as SYNCH). Set flag to SLC_NOSUPPORT instead
                # of the SLC_DEFAULT value that it begins with
                self._slctab[func].flag = SLC_NOSUPPORT
            else:
                # set current flag to the flag indicated in default tab
                self._slctab[func].flag = DEFAULT_SLC_TAB.get(func).flag
            # set current value to value indicated in default tab
            self._slctab[func].val = DEFAULT_SLC_TAB.get(
                    func, SLC_nosupport()).val
            # write response to SLC buffer
            self._slc_add(func)
            return

        # client wants to change to a new value or refuses to change to
        # our value. if our byte value is b'\x00', it is a value we cannot
        # change.
        if self._slctab[func].val != b'\x00':
            self._slctab[func].val = slc_def.val
            self._slctab[func].flag = slc_def.flag
            slc_def.flag = bytes([ord(slc_def.flag) | ord(SLC_ACK)])
            self._slc_add(func, slc_def)
        else:
            if mylevel == SLC_DEFAULT:
                # If our level is default, just ack whatever was sent
                flag = ord(slc_def.flag)
                flag |= SLC_ACK
                self._slctab[func].flag = bytes([flag])
                self._slctab[func].val = slc_def.val
            elif slc_def.level == SLC_CANTCHANGE and mylevel == SLC_CANTCHANGE:
                # remove levelbit, and set to SLC_NOSUPPORT
                flag = ord(slc_def.flag)
                flag &= ~SLC_LEVELBITS
                flag |= SLC_NOSUPPORT
                self._slctab[func].flag = bytes([flag])
            else:
                flag = ord(slc_def.flag)
                flag &= ~SLC_LEVELBITS
                flag |= ord(mylevel)
                self._slctab[func].flag = flag
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
        if opt == ECHO and self.server:
            raise ValueError('WILL ECHO received on server end')
        elif opt == NAWS and not self.server:
            raise ValueError('WILL NAWS received on client end')
        elif opt == XDISPLOC and not self.server:
            raise ValueError('WILL XDISPLOC received on client end')
        elif opt == TTYPE and not self.server:
            raise ValueError('WILL TTYPE received on client end')
        elif opt == TM and not self._tm_sent:
            raise ValueError('WILL TM received but DO TM was not sent')
        elif opt == LFLOW and not self.server:
            raise ValueError('WILL LFLOW not supported on client end')
        elif opt in (BINARY, SGA, ECHO, NAWS, LINEMODE):
            if not self.remote_option.get(opt, None):
                self.remote_option[opt] = True
                self.log.debug('remote_option[%s] = True', _name_command(opt))
                self.iac(DO, opt)
            if opt in (NAWS, LINEMODE):
                self.pending_option[SB + opt] = True
                self.log.debug('set pending_option[SB + %s] = False',
                        _name_command(opt))
        elif opt == TM:
            self.log.debug('WILL TIMING-MARK')
            self._tm_sent = False
        elif opt == STATUS:
            if not self.remote_option.get(opt, None):
                self.remote_option[opt] = True
                self.log.debug('remote_option[%s] = True', _name_command(opt))
            self.request_status()
        elif opt == LFLOW:
            if not self.remote_option.get(opt, None):
                self.remote_option[opt] = True
                self.log.debug('remote_option[%s] = True', _name_command(opt))
            self.send_lineflow_mode()
        elif opt == NEW_ENVIRON:
            if not self.remote_option.get(opt, None):
                self.remote_option[opt] = True
                self.log.debug('remote_option[%s] = True', _name_command(opt))
            self.request_new_environ()
        elif opt == XDISPLOC:
            if not self.remote_option.get(opt, None):
                self.log.debug('remote_option[%s] = True', _name_command(opt))
                self.remote_option[opt] = True
            self.request_xdisploc()
        elif opt == TTYPE:
            if not self.remote_option.get(opt, None):
                self.log.debug('remote_option[%s] = True', _name_command(opt))
                self.remote_option[opt] = True
            self.request_ttype()
        elif opt == TSPEED:
            if not self.remote_option.get(opt, None):
                self.log.debug('remote_option[%s] = True', _name_command(opt))
                self.remote_option[opt] = True
            self.request_tspeed()
        else:
            self.log.debug('set remote_option[%s] = False' % (
                _name_command(opt),))
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
            self.log.debug('set remote_option[%s] = False' % (
                _name_command(opt),))
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
            self.log.debug('set pending_option[WILL + %s] = False',
                    _name_command(STATUS))
            self.pending_option[WILL + STATUS] = False

    def _request_sb_newenviron(self):
        """ Request sub-negotiation NEW_ENVIRON, RFC 1572. This should
        not be called directly, but by answer to WILL NEW_ENVIRON after DO
        request from server.
        """
        self.log.debug('set pending_option[SB + %s] = True' % (
            _name_command(NEW_ENVIRON),))
        self.pending_option[SB + NEW_ENVIRON] = True
        response = collections.deque()
        response.extend([IAC, SB, NEW_ENVIRON, SEND, bytes([0])])
        response.extend(b'\x00'.join(self.request_env))
        response.extend([b'\x03', IAC, SE])
        self.write_iac(bytes([ord(byte) for byte in response]))

    def request_status(self):
        """ Send STATUS, SEND sub-negotiation, rfc859
            Does nothing if (WILL, STATUS) has not yet been received. """
        if not self.remote_option.get(STATUS, None):
            return
        self.write_iac(
            b''.join([IAC, SB, STATUS, SEND, IAC, SE]))
        # set pending for SB STATUS
        self.log.debug('set pending_option[SB + %s] = True' % (
            _name_command(STATUS),))
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
            Does nothing if (WILL, TSPEED) has not yet been received. """
        if not self.remote_option.get(TSPEED, None):
            return
        response = [IAC, SB, TSPEED, SEND, IAC, SE]
        self.log.debug('send: %s', ', '.join([
            _name_command(byte) for byte in response]))
        self.write_iac(b''.join(response))
        # set pending for SB TSPEED
        self.log.debug('set pending_option[SB + %s] = True' % (
            _name_command(TSPEED),))
        self.pending_option[SB + TSPEED] = True

    def request_new_environ(self):
        """ Send NEW_ENVIRON, SEND, IS sub-negotiation, rfc1086.
            Does nothing if (WILL, NEW_ENVIRON) has not yet been received. """
        if not self.remote_option.get(NEW_ENVIRON, None):
            return
        response = [IAC, SB, NEW_ENVIRON, SEND, IS]
        for idx, env in enumerate(self.request_env):
            response.extend([bytes(char, 'ascii') for char in env])
            if idx < len(self.request_env) - 1:
                response.append(b'\x00')
        response.extend([b'\x03', IAC, SE])
        self.log.debug('send: %s, %r', ', '.join([
            _name_command(byte) for byte in response[:3]]), response[3:],)
        self.write_iac(b''.join(response))
        # set pending for SB NEW_ENVIRON
        self.log.debug('set pending_option[SB + %s] = True' % (
            _name_command(NEW_ENVIRON),))
        self.pending_option[SB + NEW_ENVIRON] = True

    def request_xdisploc(self):
        """ Send XDISPLOC, SEND sub-negotiation, rfc1086.
            Does nothing if (WILL, XDISPLOC) has not yet been received. """
        if not self.remote_option.get(XDISPLOC, None):
            return
        response = [IAC, SB, XDISPLOC, SEND, IAC, SE]
        self.log.debug('send: %s', ', '.join([
            _name_command(byte) for byte in response]))
        self.write_iac(b''.join(response))
        # set pending for SB XDISPLOC
        self.log.debug('set pending_option[SB + %s] = True' % (
            _name_command(XDISPLOC),))
        self.pending_option[SB + XDISPLOC] = True

    def request_ttype(self):
        """ Send TTYPE SEND sub-negotiation, rfc930.
            Does nothing if (WILL, TTYPE) has not yet been received. """
        if not self.remote_option.get(TTYPE, None):
            return
        response = [IAC, SB, TTYPE, SEND, IAC, SE]
        self.log.debug('send: %s', ', '.join([
            _name_command(byte) for byte in response]))
        self.write_iac(b''.join(response))
        # set pending for SB TTYPE
        self.log.debug('set pending_option[SB + %s] = True' % (
            _name_command(TTYPE),))
        self.pending_option[SB + TTYPE] = True

    def handle_xdisploc(self, buf):
        """ XXX
        Receive XDISPLAY environment variable.

        The X display location is an NVT ASCII string.  This string follows
        the normal Unix convention used for the DISPLAY environment variable,
        e.g.,

                  <host>:<dispnum>[.<screennum>]

        """
        pass

    def handle_ttype(self, ttype):
        """ XXX
        Receive TERM environment variable.
        """
        pass

    def handle_naws(self, width, height):
        """ XXX
        Receive new window size from NAWS protocol. """
        pass

    def handle_env(self, env):
        """ XXX
        Receive new environment variable value. """
        pass

    def handle_tspeed(self, rx, tx):
        """ XXX
        Receive new terminal size from TSPEED protocol. """
        pass

    def handle_ip(self):
        """ XXX

        Handle Interrupt Process (IAC, IP) or SLC_IP. """
        pass

    def handle_abort(self):
        """ XXX

        Handle Abort (IAC, ABORT). Similar to "IAC IP", but means only to
        abort or terminate the process to which the NVT is connected.  """
        pass

    def handle_susp(self):
        """ XXX

        Handle Suspend Process (IAC, SUSP). Suspend the execution of the
        current process attached to the NVT in such a way that another
        process will take over control of the NVT, and the suspended
        process can be resumed at a later time.  If the receiving system
        does not support this functionality, it should be ignored.
        """
        pass

    def handle_ao(self):
        """ XXX

        Handle Abort Output (IAC, AO), sent by clients to discard any remaining
        output.

        "If the AO were received [...] a reasonable implementation would
        be to suppress the remainder of the text string, *but transmit the
        prompt character and the preceding <CR><LF>*."
        """
        pass

    def handle_brk(self):
        """ XXX

        Handle Break (IAC, BRK), sent by clients to indicate BREAK keypress,
        or SLC_BREAK key mapping.  This is *not* ctrl+c, but a means to map
        sysystem-dependent break key such as found on an IBM PC Keyboard. """
        pass

    def handle_ayt(self):
        """ XXX
        Handle Are You There (IAC, AYT), which provides the user with some
        visible (e.g., printable) evidence that the system is still up and
        running.

        Terminal servers that respond to AYT usually print the status of the
        client terminal session, its speed, type, and options. """
        pass

    def handle_ec(self):
        """ XXX
        Handle Erase Character (IAC, EC). Provides a function which deletes
        the last preceding undeleted character from the stream of data being
        supplied by the user ("Print position" is not calculated).  """
        pass

    def handle_el(self):
        """ XXX
        Handle Erase Line (IAC, EL). Provides a function which deletes all
        the data in the current "line" of input. """
        pass

    def handle_eor(self):
        """ XXX
        Handle End of Record (IAC, EOR). rfc885 """
        pass

    def handle_eof(self):
        """ XXX
        Handle End of Record (IAC, EOR). rfc885 """
        pass

    def handle_nop(self):
        """ Accepts nothing, Does nothing, Returns nothing.

        Called when IAC + NOP is received.  """
        pass

    def handle_dm(self):
        """ Accepts nothing, Does nothing, Returns nothing.

        Called when IAC + DM or SLC_SYNCH is received.  """
        pass

    def handle_xon(self):
        """ Accepts nothing, Does nothing, Returns nothing.

        Called when IAC + XON or SLC_XON is received.  """
        pass

    def handle_xoff(self):
        """ Accepts nothing, Does nothing, Returns nothing.

        Called when IAC + XOFF or SLC_XOFF is received.  """
        pass



# `````````````````````````````````````````````````````````````````````````````

class BasicTelnetServer(tulip.protocols.Protocol):
    # toggled when '\r' is seen; for non-BINARY clients, assert that it must
    # be followed by either '\n' or '\0'.
    _carriage_returned = False
    # Connection period to wait for negotiation before displaying prompt
    CONNECT_MAXWAIT = 1.5
    CONNECT_DEFERED = 0.1

    def __init__(self, log=logging, debug=False):
        self.log = log
        self.inp_command = collections.deque()
        self.debug = debug

    def connection_made(self, transport):
        self.transport = transport
        self.stream = TelnetStreamReader(transport, server=True, debug=True)
        self.connect_time = time.time()
        self.banner()

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
        # does support, toggle on here at server end, or,
        # negotiate from client end. The default telnet 
        #self.stream.iac(WILL, ECHO)
        #self.stream.iac(DO, TTYPE)
        #self.stream.iac(DO, TSPEED)
        #self.stream.iac(DO, XDISPLOC)
        #self.stream.iac(DO, NEW_ENVIRON)
        #self.stream.iac(WILL, SGA)
        #self.stream.iac(DO, LINEMODE)
        #self.stream.iac(DO, NAWS)
        #self.stream.iac(WILL, STATUS)
        #self.stream.iac(DO, LFLOW)

        # not yet testing or asserting
        #self.stream.iac(DO, BINARY)
        #self.stream.iac(WILL, BINARY)

        # not implemented
        #self.stream.iac(DO, ENVIRON)

        self._negotiate()

    CONNECT_MAXWAIT = 1.5
    CONNECT_DEFERED = 0.1
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
        if any(self.stream.pending_option.values()) and (
                time.time() - self.connect_time <= self.CONNECT_MAXWAIT):
            loop.call_later(self.CONNECT_DEFERED, self._negotiate, call_after)
            return

        self.log.debug(self.transport.get_extra_info('addr', None))
        for option, pending in self.stream.pending_option.items():
            if pending:
                self.log.warn('telnet reply not received for "%s"',
                        ' + '.join([_name_command(bytes([byte]))
                            for byte in option]))
        loop.call_soon(call_after)

    def data_received(self, data):
        """ Process all data received on socket.
        It is necessary to pass each byte through stream.feed_byte(), which
        returns True if out-of-band data was processed.

        Otherwise the data is in-band, and depending on LINEMODE, is deferred
        to self.handle_input or self.handle_line.

        TODO: codecs.incrementaldecoder
        """
        for byte in (bytes([value]) for value in data):
            slc = None
            if self.stream.feed_byte(byte):
                slc = self.stream._slc_received
                if not slc:
                    # processed some telnet command/negotiation
                    continue
            # echo back input if DO ECHO sent by client, and input
            # byte received is printable. This is valid regardless of linemode
            # XXX really? seems you wouldn't want it ...
            # character-at-a-time mode is essentially pass-thru
            # to self.handle_input()
            if not self.stream.is_linemode:
                self.handle_input(byte, slc)
            elif slc is not None:
                # handle_line is called with current input buffer and slc
                # option set; it is expected that ^c does not add \x03 to
                # the input buffer, rather, calls handle_line with an
                # unfinished input buffer, and slc set to SLC_IP after
                # self.stream.handle_ip has been called.
                cmd = b''.join(self.inp_command).decode('ascii', 'replace')
                self.handle_line(cmd, slc)
            # linemode processing buffers input until '\r'
            elif not self._carriage_returned and byte == b'\x0d':
                if not self.stream.local_option.get(BINARY, False):
                    self._carriage_returned = True
                self.inp_command.append(byte)
                cmd = b''.join(self.inp_command).decode('ascii', 'replace')
                self.inp_command.clear()
                self.handle_line(cmd)
            elif self._carriage_returned:
                assert byte in (b'\x0a', b'\x00'), (
                        'LF or NUL must follow CR, got %r' % (byte,))
                self._carriage_returned = False
            else:
                # buffer command input
                self.inp_command.append(byte)

    def prompt(self):
        """ XXX Prompt client end for input
        """
        self.stream.write(bytes('\r\n%s $ ' % (__file__), 'ascii'))
        # unless (DO, SGA) is received by client, the 'GA' character must
        # be sent, this indicates that server is ready to receive input.
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
            cmd = b''.join(self.inp_command).decode('ascii', 'replace')
            try:
                self.handle_line(cmd)
            finally:
                self.inp_command.clear()
        else:
            self.log_debug('unhandled byte')

    def process_cmd(self, cmd):
        """ XXX Handle input line received on line-at-a-time basis
            The default implementation provides simple command processing.
        """
        cmd = cmd.rstrip()
        try:
            cmd, *args = cmd.split()
        except ValueError:
            args = []
        if cmd == 'quit':
            self.stream.write(b'\r\nBye!\r\n')
            self.transport.close()
        elif cmd == 'version':
            self.stream.write('\r\n')
            self.stream.write(bytes(sys.version, 'ascii'))
        elif cmd == 'set':
            self.stream.write('\r\n')
        else:
            self.stream.write(b'\r\nCommand "')
            self.stream.write(bytes(cmd, 'ascii', 'replace'))
            self.stream.write(b'" not understood.')

    def handle_line(self, inp, slc=None):
        """ XXX Handle input received on line-by-line basis; slc is non-None,
        identifying the SLC character detected on input
        """
        self.log_debug('recv: %r (slc=%s)', inp,
                _name_slc_command(slc) if slc is not None else None)
        try:
            self.process_cmd(inp)
        finally:
            self.prompt()

    def log_debug(self, *args, **kw):
        if self.debug:
            self.log.debug(*args, **kw)

    def eof_received(self):
        self.log.info('%s Connection closed by client',
                self.transport.get_extra_info('addr', None))

    def close(self):
        self.transport.close()
        self._closing = True

class CharacterTelnetServer(BasicTelnetServer):
    def banner(self):
        self.stream.write(b'Welcome to ')
        self.stream.write(bytes(__file__, 'ascii', 'replace'))
        self.stream.write(b'\r\n')
        self.stream.iac(WILL, ECHO)
        self.stream.iac(WILL, SGA)
        self._negotiate()

class LinemodeTelnetServer(BasicTelnetServer):
    def banner(self):
        self.stream.write(b'Welcome to ')
        self.stream.write(bytes(__file__, 'ascii', 'replace'))
        self.stream.write(b'\r\n')
        self.stream.iac(WONT, ECHO)
        self.stream.iac(WILL, SGA)
        self.stream.iac(DO, LINEMODE)
        self._negotiate()

class AdvancedTelnetServer(BasicTelnetServer):
    def banner(self):
        self.stream.write(b'Welcome to ')
        self.stream.write(bytes(__file__, 'ascii', 'replace'))
        self.stream.write(b'\r\n')
        self.stream.iac(WILL, SGA)
        self.stream.iac(DO, TTYPE)
        self.stream.iac(DO, TSPEED)
        self.stream.iac(DO, XDISPLOC)
        self.stream.iac(DO, NEW_ENVIRON)
        self.stream.iac(DO, LINEMODE)
        self.stream.iac(DO, NAWS)
        self.stream.iac(WILL, STATUS)
        self.stream.iac(DO, LFLOW)
        self._negotiate()

# `````````````````````````````````````````````````````````````````````````````
#
# debug routines for appropriately naming telnet bytes

_DEBUG_OPTS = dict([(value, key)
                    for key, value in globals().items() if key in
                  ('LINEMODE', 'NAWS', 'NEW_ENVIRON', 'ENCRYPT',
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

def _name_slc_modifier(byte):
    """ Given an SLC byte, return string representing its modifiers. """
    value = ord(byte)
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
        lambda: AdvancedTelnetServer(debug=True), args.host, args.port)
    x = loop.run_until_complete(f)
    logger.info('serving on %s', x.getsockname())
    loop.run_forever()

if __name__ == '__main__':
    main()
