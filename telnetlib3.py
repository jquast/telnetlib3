#!/usr/bin/env python3
"""
Not yet for consumption.

This project implements a Telnet client and server protocol,

It uses Guido's 'tulip' project; the asynchronous networking model
to become standard with python 3.4. *This project requires python 3.3*

Guido's 'tulip' module is included, retrieved Apr. 2013

[x] RFC 854  Telnet Protocol Specification                        May 1983
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
[*] RFC 1123 Requirements for Internet Hosts                      Oct 1989 *
[*] RFC 1143 The Q Method of Implementing .. Option Negotiation   Feb 1990
[*] RFC 1080 Telnet Remote Flow Control Option                    Nov 1988 *
[*] RFC 1372 Telnet Remote Flow Control Option                    Oct 1992 *

x = complete
* = in-progress

Additional Resources,
   "Telnet Protocol," MIL-STD-1782, U.S. Department of Defense, May 1984.
   "Mud Terminal Type Standard," http://tintin.sourceforge.net/mtts/
   "Telnet Protocol in C-Kermit 8.0 and Kermit 95 2.0 http://www.columbia.edu/kermit/telnet80.html

TODO:

    Need to implement flow control (^s); cease sending bytes on transport
    until ^q is received, tulip does not provide this interface.  --
    Directly pull _buffer to local value, .clear() it, then re-queue on ^q.

    A series of callbacks for LINEMODE and standard EC, EL, etc; this should
    allow a readline-line interface to negotiate correct behavior, regardless
    of mode. Withholding on implementation: reaching for clarity without
    brevity.

    A simple telnet client .. with stdin as tulip  ..?

    pending_option quirks for SB

WONT:

    Implement workarounds for misbehaving legacy clients (new bsd telnet
    makes several accomidations for supporting 4.4BSD-era telnet wonks)


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
  Send official name in Term-Type option        | 3.2.8 |x| | | | |*
  Accept any name in Term-Type option           | 3.2.8 |x| | | | |
  Implement Binary, Suppress-GA options         | 3.3.3 |x| | | | |
  Echo, Status, EOL, Ext-Opt-List options       | 3.3.3 | |x| | | |*
  Implement Window-Size option if appropriate   | 3.3.3 | |x| | | |
  Server initiate mode negotiations             | 3.3.4 | |x| | | |
  User can enable/disable init negotiations     | 3.3.4 | |x| | | |*
                                                |       | | | | | |
Go-Aheads                                       |       | | | | | |
  Non-GA server negotiate SUPPRESS-GA option    | 3.2.2 |x| | | | |*
  User or Server accept SUPPRESS-GA option      | 3.2.2 |x| | | | |*
  User Telnet ignore GA's                       | 3.2.2 | | |x| | |
                                                |       | | | | | |
Control Functions                               |       | | | | | |
  Support SE NOP DM IP AO AYT SB                | 3.2.3 |x| | | | |*
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
  Always double IAC data byte                   | 3.2.6 |x| | | | |*
  Double IAC data byte in binary mode           | 3.2.7 |x| | | | |*
  Obey Telnet cmds in binary mode               | 3.2.7 |x| | | | |*
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

note 1.
   Regarding OOB data, the 'TCP Urgent' bit is ignored in this
   implementation, as the underlying selectors do not handle this
   capability -- for good reason, an implementation in python could
   not be made to behave correctly for all of the available platforms.

   TODO: Test OOB data with 'DM' using stevens socat tool ..
"""
import collections
import logging
import argparse
import time
import sys
import os

assert sys.version >= '3.3', 'Please use Python 3.3 or higher.'
import tulip

from telnetlib import LINEMODE, NAWS, NEW_ENVIRON, ENCRYPT, AUTHENTICATION
from telnetlib import BINARY, SGA, ECHO, STATUS, TTYPE, TSPEED, LFLOW
from telnetlib import XDISPLOC, IAC, DONT, DO, WONT, WILL, SE, NOP, TM, DM
from telnetlib import BRK, IP, AO, AYT, EC, EL, EOR, GA, SB

IS = bytes([0])
SEND = bytes([1])
EOF = bytes([236])
SUSP = bytes([237])
ABORT = bytes([238])
(LFLOW_OFF, LFLOW_ON, LFLOW_RESTART_ANY, LFLOW_RESTART_XON
 ) = (bytes([const]) for const in range(4))
NSLC = 30
(SLC_SYNCH, SLC_BRK, SLC_IP, SLC_AO, SLC_AYT, SLC_EOR, SLC_ABORT,
 SLC_EOF, SLC_SUSP, SLC_EC, SLC_EL, SLC_EW, SLC_RP, SLC_LNEXT,
 SLC_XON, SLC_XOFF, SLC_FORW1, SLC_FORW2, SLC_MCL, SLC_MCR, SLC_MCWL,
 SLC_MCWR, SLC_MCBOL, SLC_MCEOL, SLC_INSRT, SLC_OVER, SLC_ECR, SLC_EWR,
 SLC_EBOL, SLC_EEOL
 ) = (bytes([const]) for const in range(1, NSLC + 1))
(SLC_FLUSHOUT, SLC_FLUSHIN, SLC_ACK
 ) = (bytes([32]), bytes([64]), bytes([128]))
(SLC_NOSUPPORT, SLC_CANTCHANGE, SLC_VARIABLE, SLC_DEFAULT
 ) = (bytes([const]) for const in range(4))
# SLC_LEVELBITS = 0x03 # XXX
LINEMODE_MODE = bytes([1])
LINEMODE_EDIT = bytes([1])
LINEMODE_TRAPSIG = bytes([2])
LINEMODE_MODE_ACK = bytes([4])
LINEMODE_FORWARDMASK = bytes([2])
LINEMODE_SLC = bytes([3])
# the V* constant values are duplicated from termios, windows platforms
# may ImportError? See bsd telnetd sys_term.c:spcset for reference source.
DEFAULT_SLC_TAB = {
        SLC_EOF: (b'\x04', SLC_VARIABLE),    # VEOF ^D
        SLC_FORW1: (b'\xff', SLC_NOSUPPORT), # VEOL, _POSIX_VDISABLE
        SLC_FORW2: (b'\xff', SLC_NOSUPPORT), # VEOL2, _POSIX_VDISABLE
        SLC_EC: (b'\x7f', SLC_VARIABLE),     # VERASE backspace
        SLC_EL: (b'\x15', SLC_VARIABLE),     # VKILL ^U
        SLC_IP: (b'\x03', bytes([ord(SLC_VARIABLE)
            | ord(SLC_FLUSHIN) | ord(SLC_FLUSHOUT)])),   # VINTR ^C
        SLC_ABORT: (b'\x1c', bytes([ord(SLC_VARIABLE)
            | ord(SLC_FLUSHIN) | ord(SLC_FLUSHOUT)])),   # VQUIT ^\ (SIGQUIT)
        SLC_XON: (b'\x11', SLC_VARIABLE),    # VSTART ^Q
        SLC_XOFF: (b'\x19', SLC_VARIABLE),   # VSTOP, ^S
        SLC_EW: (b'\x17', SLC_VARIABLE),     # VWERASE, ^W
        SLC_RP: (b'\x12', SLC_VARIABLE),     # VREPRINT, ^R
        SLC_LNEXT: (b'\x16', SLC_VARIABLE),  # VLNEXT, ^V
        SLC_AO: (b'\x0f', bytes([ord(SLC_VARIABLE)
            | ord(SLC_FLUSHOUT)])),          # VDISCARD, ^O
        SLC_SUSP: (b'\x1a', bytes([ord(SLC_VARIABLE)
            | ord(SLC_FLUSHIN)])),           # VSUSP, ^Z
        SLC_AYT: (b'\x14', SLC_VARIABLE),    # VSTATUS, ^T
        SLC_BRK: (b'\x00', SLC_DEFAULT),     # Break, Synch, and EOR are set
        SLC_SYNCH: (b'\x00', SLC_DEFAULT),   # to SLC_DEFAULT with value 0;
        SLC_EOR: (b'\x00', SLC_DEFAULT),     # no default, but go ahead.
    }


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
    _iac_received = False   # has IAC been recv?
    _cmd_received = False   # has IAC (DO, DONT, WILL, WONT) been recv?
    _sb_received = False    # has IAC SB been recv?
    _tm_sent = False        # has IAC DO TM been sent?
    _dm_recv = False        # has IAC DM been recv?
    # _iac_callbacks is a dictionary of telnet command options, such as IP,
    # ABORT, EL (Erase line) to the handling function, which receives no
    # arguments.
    _iac_callbacks = {}
    # _iac_slctab is a dictionary of SLC functions, such as SLC_IP,
    # to a tuple of the handling character and support level, such as
    # (b'\x08', SLC_VARIABLE)
    _slctab = {}
    pending_option = {}
    local_option = {}
    remote_option = {}
    lflow_any = False
    request_env = (
            "USER HOSTNAME UID TERM COLUMNS LINES DISPLAY LANG "
            "SYSTEMTYPE ACCT JOB PRINTER SFUTLNTVER SFUTLNTMODE").split()

    def __init__(self, transport, client=None, server=None,
                 debug=False, log=logging):
        """ By default, the stream is *decoded as a telnet server* unless
        keyword argument ``client`` is set to ``True``.  """
        assert client == None or server == None, (
            "Arguments 'client' and 'server' are mutually exclusive")
        self.server = client == False or (
                client in (None, False) and server in (None, True))
        self.transport = transport
        self._sb_buffer = collections.deque()
        self.log = log
        self.debug = debug
        tulip.StreamReader.__init__(self)
        # set default callback handlers for basic IAC commands
        for key, iac_cmd in (
                ('brk', BRK), ('ip', IP), ('ao', AO),
                ('ayt', AYT), ('ec', EC), ('el', EL),
                ('eor', EOR), ('eof', EOF), ('susp', SUSP),
                ('abort', ABORT), ('nop', NOP),
                ):
            self._iac_callbacks[iac_cmd] = getattr(self, 'handle_%s' % (key,))
        # set default tabset for both clients and terminals; most can be
        # changed by negotiation, but otherwise are not supported.
        nosupport = (b'\x00', SLC_NOSUPPORT)
        for slc in range(NSLC + 1):
            self._slctab[slc] = DEFAULT_SLC_TAB.get(slc, nosupport)

#    def set_slc_tab(slc, value):
#        pass

    def set_iac_callback(self, cmd, func):
        """ Register ``func`` as callback for receipt of IAC command ``cmd``.

        Examples: BRK, IP, AO, AYT, EC, EL, EOR, EOF, SUSP, ABORT, and NOP
        """
        self._iac_callbacks[cmd] = func

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
        elif cmd in (DO, DONT):
            if self.pending_option.get(cmd + opt, False):
                self.log.debug('skip %s + %s; pending_option = True',
                    _name_command(cmd), _name_command(opt))
                return
            self.pending_option[cmd + opt] = True
            self.log.debug('set pending_option[%s + %s] = True' % (
                _name_command(cmd), _name_command(opt),))
        elif(cmd == WILL and self.local_option.get(opt, None) != True):
            #self.local_option[opt] = True
            self.pending_option[cmd + opt] = True
            self.log.debug('set pending_option[%s + %s] = True' % (
                _name_command(cmd), _name_command(opt),))
        elif(cmd == WONT and self.local_option.get(opt, None) != False):
            #self.local_option[opt] = False
            self.pending_option[cmd + opt] = True
            self.log.debug('set pending_option[%s + %s] = True' % (
                _name_command(cmd), _name_command(opt),))
        self.transport.write(IAC + cmd + opt)
        self.log.debug('send IAC %s %s' % (
            _name_command(cmd), _name_command(opt),))

    @property
    def idle(self):
        """ Return time since bytes last received by remote end """
        return time.time() - self._last_input_time

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
        those methods beginning with 'handle', or parse_iac_command, and
        parse_subnegotiation.

        As this parse receives a single byte at a time, active states are
        stored as booleans ``_iac_received`` and ``_sb_received``, and behaves
        on in-band command data accordingly.  The Value of ``_cmd_received``
        is equal to the telnet command and is non-None when that state is
        active.

        Negotiated options are stored in dict ``self.local_option``,
        and ``self.remote_option``. Pending replies are noted with
        ``self.pending_option``, keyed by option byte.
        """
        if byte == IAC:
            self._iac_received = (not self._iac_received)
            if not self._iac_received:
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
                self.parse_subnegotiation(self._sb_buffer)
                self._sb_buffer.clear()
                self._sb_received = False
            else:
                self.parse_iac_command(byte)
            self._iac_received = False

        elif self._sb_received:
            # with IAC SB mark received, buffer until IAC SE.
            self._sb_buffer.append(byte)

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
            # in-bound data
            self.buffer.append(byte)
            return False
        return True

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
        if not buf:
            raise ValueError('SE: buffer empty')
        elif buf[0] == b'\x00':
            raise ValueError('SE: buffer is NUL')
        elif len(buf) < 2:
            raise ValueError('SE: buffer too short: %r' % (buf,))
        elif buf[0] == LINEMODE:
            if not self.server:
                raise ValueError('SE: received from server: LINEMODE')
            #self.log.debug('set pending_option[DO + LINEMODE] = False')
            self._handle_sb_linemode(buf)
        elif buf[0] == LFLOW:
            self._handle_sb_lflow(buf)
            if not self.server:
                raise ValueError('SE: received from server: LFLOW')
        elif buf[0] == NAWS:
            if not self.server:
                raise ValueError('SE: received from server: NAWS')
            self._handle_sb_naws(buf)
        elif buf[0] == NEW_ENVIRON:
            if not self.server:
                raise ValueError('SE: received from server: NEW_ENVIRON IS')
#            self.pending_option[DO + NEW_ENVIRON] = False
#            self.log.debug('set pending_option[DO + NEW_ENVIRON] = False')
            self._handle_sb_newenv(buf)
        elif (buf[0], buf[1]) == (TTYPE, IS):
            if not self.server:
                raise ValueError('SE: received from server: TTYPE IS')
#            self.pending_option[DO + TTYPE] = False
#            self.log.debug('set pending_option[DO + TTYPE] = False')
            self._handle_sb_ttype(buf)
        elif (buf[0], buf[1]) == (TSPEED, IS):
            if not self.server:
                raise ValueError('SE: received from server: TSPEED IS')
#            self.pending_option[DO + TSPEED] = False
#            #self.log.debug('set pending_option[DO + TSPEED] = False')
            self._handle_sb_tspeed(buf)
        elif (buf[0], buf[1]) == (XDISPLOC, IS):
            if not self.server:
                raise ValueError('SE: received from server: XDISPLOC IS')
#            self.pending_option[DO + XDISPLOC] = False
#            self.log.debug('set pending_option[DO + XDISPLOC] = False')
            self._handle_sb_xdisploc(buf)
        elif (buf[0], buf[1]) == (STATUS, SEND):
            assert len(buf) == 2, (
                    'IAC SB STATUS SEND not followed by IAC: %r' % (buf[2:]))
            self._send_status()
        else:
            raise ValueError('SE: sub-negotiation unsupported: %r' % (buf,))

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
            mask &= ord(LINEMODE_EDIT)
        if self.linemode['trapsig']:
            mask &= ord(LINEMODE_TRAPSIG)
        mask &= ord(LINEMODE_MODE_ACK)
        self.transport.write(IAC + SB)
        self.transport.write(LINEMODE + LINEMODE_MODE + mask)
        self.transport.write(IAC + SE)
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
            assert buf[1] == LINEMODE_SLC

    def _add_slc(self, func, modifier, char):
        """ Add SLC triplet (function, modifier, char) to SLC buffer.

        This describes, for instance,
        function 'BREAK' is SLC_VARIABLE 0x03 (^c).
        RFC1116 and bsd-telnetd/slc.c:add_slc(char func, char flag, cc_t val)
        """
        def escape_iac(byte):
            if byte == b'\xff':
                return b'\xff\xff'
            return byte
        self.transport.write(escape_iac(func))
        self.transport.write(escape_iac(modifier))
        self.transport.write(escape_iac(char))

#    def _send_slc(self):
#        """ Send all special characters that are supported """
#        for slc, 

    def _handle_sb_linemode_slc(self, buf):
        # IAC SB LINEMODE SLC
        # *ff fb 22*ff fa 22 03
        # SYNCH DEFAULT 0;
        # > 01 03 00
        # IP VARIABLE|FLUSHIN|FLUSHOUT 3;
        # > 03 62 03
        # AO VARIABLE 15;
        # > 04 02 0f
        # AYT VARIABLE 20;
        # > 05 02 14
        # ABORT VARIABLE|FLUSHIN|FLUSHOUT 28;
        # > 07 62 1c
        # EOF VARIABLE 4;
        # > 08 02 04
        # SUSP VARIABLE|FLUSHIN 26;
        # > 09 42 1a
        # EC VARIABLE 127;
        # > 0a 02 7f
        # EL VARIABLE 21;
        # > 0b 02 15
        # EW VARIABLE 23;
        # > 0c 02 17
        # RP VARIABLE 18;
        # > 0d 02 12
        # LNEXT VARIABLE 22;
        # > 0e 02 16
        # XON VARIABLE 17;
        # > 0f 02 11
        # XOFF VARIABLE 19;
        # > 10 02  13
        # FORW1 NOSUPPORT 255;
        # > 11 00 *ff *ff
        # FORW2 NOSUPPORT 255;
        # > 12 00 *ff *ff
        # IAC SB
        # *ff f0
        #print(repr(buf))
        while len(buf):
            func = buf.popleft()
            modifier = buf.popleft()
            char = buf.popleft()
            self.log.debug('(func, modifier, char): (%s, %s, %r)' % (
                _name_slc_command(func), _name_slc_modifier(modifier), char))

    def _send_status(self):
        """ Respond after DO STATUS received by DE (rfc859). """
        assert self.local_option.get(STATUS, None) is True, (
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
        self.transport.write(bytes([ord(byte) for byte in response]))

    def _request_sb_newenviron(self):
        """ Request sub-negotiation NEW_ENVIRON, RFC 1572. This should
        not be called directly, but by answer to WILL NEW_ENVIRON after DO
        request from server.
        """
        if self.pending_option.get(SB + NEW_ENVIRON, False):
            # avoid calling twice during pending reply
            return
        self.pending_option[SB + NEW_ENVIRON] = True
        response = collections.deque()
        response.extend([IAC, SB, NEW_ENVIRON, SEND, bytes([0])])
        response.extend(b'\x00'.join(self.request_env))
        response.extend([b'\x03', IAC, SE])
        self.transport.write(bytes([ord(byte) for byte in response]))

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
        if opt == LINEMODE and self.server:
                raise ValueError('DO LINEMODE received on server end.')
        elif opt == TM:
            # TIMING-MARK is always replied, and is not an 'option'
            self.iac(WILL, TM)
        if opt in (ECHO, LINEMODE, BINARY, SGA, LFLOW):
            if not self.local_option.get(opt, None):
            #    self.local_option[opt] = True
                self.iac(WILL, opt)
        elif opt == STATUS:
            # IAC DO STATUS is used to obtain request to have server
            # transmit status information. Only the sender of
            # WILL STATUS is free to transmit status information.
            if not self.local_option.get(opt, None):
                self.local_option[opt] = True
                self.log.debug('local_option[%s] = True', _name_command(opt))
                self.iac(WILL, STATUS)
            self._send_status()
        else:
            if self.local_option.get(opt, None) is None:
            #    self.local_option[opt] = False
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
        elif opt == TM:
            self.log.debug('WILL TIMING-MARK')
            self._tm_sent = False
        elif opt == STATUS:
            if not self.remote_option.get(opt, None):
                self.remote_option[opt] = True
                self.log.debug('remote_option[%s] = True', _name_command(opt))
            self.transport.write(
                b''.join([IAC, SB, STATUS, SEND, IAC, SE]))
            # set pending for SB STATUS
            self.pending_option[SB + opt] = True
        elif opt == LFLOW:
            if not self.remote_option.get(opt, None):
                self.remote_option[opt] = True
                self.log.debug('remote_option[%s] = True', _name_command(opt))
            mode = LFLOW_RESTART_ANY if self.lflow_any else LFLOW_RESTART_XON
            self.transport.write(
                    b''.join([IAC, SB, LFLOW, mode, IAC, SE]))
        elif opt == NEW_ENVIRON:
            if not self.remote_option.get(opt, None):
                self.remote_option[opt] = True
                self.log.debug('remote_option[%s] = True', _name_command(opt))
            response = [IAC, SB, NEW_ENVIRON, SEND, IS]
            for idx, env in enumerate(self.request_env):
                response.extend([bytes(char, 'ascii') for char in env])
                if idx < len(self.request_env) - 1:
                    response.append(b'\x00')
            response.extend([b'\x03', IAC, SE])
            self.log.debug('send: %s, %r', ', '.join([
                _name_command(byte) for byte in response[:3]]), response[3:],)
            self.transport.write(b''.join(response))
            # set pending for SB NEW_ENVIRON
            self.log.debug('set pending_option[SB + %s] = True' % (
                _name_command(opt),))
            self.pending_option[SB + opt] = True
        elif opt == XDISPLOC:
            if not self.remote_option.get(opt, None):
                self.log.debug('remote_option[%s] = True', _name_command(opt))
                self.remote_option[opt] = True
            response = [IAC, SB, XDISPLOC, SEND, IAC, SE]
            self.log.debug('send: %s', ', '.join([
                _name_command(byte) for byte in response]))
            self.transport.write(b''.join(response))
            # set pending for SB XDISPLOC
            self.log.debug('set pending_option[SB + %s] = True' % (
                _name_command(opt),))
            self.pending_option[SB + opt] = True
        elif opt == TTYPE:
            if not self.remote_option.get(opt, None):
                self.log.debug('remote_option[%s] = True', _name_command(opt))
                self.remote_option[opt] = True
            response = [IAC, SB, TTYPE, SEND, IAC, SE]
            self.log.debug('send: %s', ', '.join([
                _name_command(byte) for byte in response]))
            self.transport.write(b''.join(response))
            # set pending for SB TTYPE
            self.log.debug('set pending_option[SB + %s] = True' % (
                _name_command(opt),))
            self.pending_option[SB + opt] = True
        elif opt == TSPEED:
            if not self.remote_option.get(opt, None):
                self.log.debug('remote_option[%s] = True', _name_command(opt))
                self.remote_option[opt] = True
            response = [IAC, SB, TSPEED, SEND, IAC, SE]
            self.log.debug('send: %s', ', '.join([
                _name_command(byte) for byte in response]))
            self.transport.write(b''.join(response))
            # set pending for SB TSPEED
            self.log.debug('set pending_option[SB + %s] = True' % (
                _name_command(opt),))
            self.pending_option[SB + opt] = True
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

        Handle Interrupt Process (IAC, IP). """
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
        this is *not* ctrl+c, but a means to map sysystem-dependent break key
        such as found on an IBM PC Keyboard. """
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

# `````````````````````````````````````````````````````````````````````````````

class TelnetServer(tulip.protocols.Protocol):
    _inp_cr = False
    # newline byte sequence is extend to strings detected in linemode,
    # it does not change carriage return processing behavior.
    newline = bytes(os.linesep, 'ascii')
    def __init__(self, log=logging, debug=False):
        self.log = log
        self.inp_command = collections.deque()
        self.debug = debug

    def log_debug(self, *args, **kw):
        if self.debug:
            self.log.debug(*args, **kw)

    def connection_made(self, transport):
        self.transport = transport
        self.stream = TelnetStreamReader(transport, server=True, debug=True)
        self.banner()

    def eof_received(self):
        print('bye')

    def close(self):
        self._closing = True

    def banner(self):
        """ XXX
        """
        self.transport.write(b'Welcome to telnetlib3\r\n')
        self.stream.iac(DONT, AUTHENTICATION)
        self.stream.iac(WONT, ENCRYPT)
        self.stream.iac(DO, TTYPE)
        self.stream.iac(DO, TSPEED)
        self.stream.iac(DO, XDISPLOC)
        self.stream.iac(DO, NEW_ENVIRON)
        #self.stream.iac(DO, ENVIRON)
        self.stream.iac(WILL, SGA)
        self.stream.iac(DO, LINEMODE)
        self.stream.iac(DO, NAWS)
        self.stream.iac(WILL, STATUS)
        self.stream.iac(DO, LFLOW)
        # not yet testing or asserting
        #self.stream.iac(DO, BINARY)
        #self.stream.iac(WILL, BINARY)
        self.prompt()

    def prompt(self):
        """ XXX
        """
        self.transport.write(b'\r\n ')
        self.transport.write(bytes(__file__, 'ascii'))
        self.transport.write(b'$ ')
        if self.stream.local_option.get(SGA, None) != True:
            self.transport.write(GA)
            self.log_debug('GA!')
        self.log_debug('prompt')

    def handle_input(self, byte):
        """ XXX
        """
        self.log_debug('recv: %r', byte)

    def process_cmd(self, cmd):
        cmd = cmd.rstrip()
        try:
            cmd, *args = cmd.split()
        except ValueError:
            args = []
        self.transport.write(b'Command "')
        self.transport.write(bytes(cmd, 'ascii'))
        self.transport.write(b'" not understood.\r\n')

    def handle_line(self, inp):
        """ XXX
        """
        self.transport.write(b'\r\n')
        self.log_debug('recv: %r', inp)
        self.process_cmd(inp)
        self.prompt()

    def data_received(self, data):
        for byte in (bytes([value]) for value in data):
            if self.stream.feed_byte(byte):
                # processed telnet command
                continue
            # echo back input if DO ECHO sent by client, and input
            # byte received is printable. This is valid regardless of linemode
            if (self.stream.local_option.get(ECHO, None)
                    and byte.decode('ascii').isprintable()):
                self.transport.write(byte)
            # character-at-a-time mode is essentially pass-thru callback
            # to self.handle_input()
            if (not self.stream.remote_option.get(LINEMODE, None) or (
                    self.stream.local_option.get(ECHO, None) and
                    self.stream.local_option.get(SGA, None))):
                self.handle_input(byte)
            # linemode processing buffers input until '\r'
            if not self._inp_cr and byte == b'\r':
                self._inp_cr = True
                if not self.stream.local_option.get(BINARY, None):
                    self.inp_command.append(self.newline)
                else:
                    self.inp_command.append(byte)
                self.handle_line(b''.join(self.inp_command).decode('ascii'))
                self.inp_command.clear()
            elif self._inp_cr:
                if not self.stream.local_option.get(BINARY, None):
                    assert byte in (b'\n', b'\x00'), (
                            'LF or NUL must follow CR, got %r' % (byte,))
                else:
                    # even though in linemode, with binary set, keep passing
                    # bytes as we receive them, no matter their content, but
                    # it is still buffered until next command byte or CR!
                    self.inp_command.append(byte)
                    # XXX: would \r\n be sent, leaving 'input\r', '\nmore\r'?
                self._inp_cr = False
            else:
                # buffer command input
                self.inp_command.append(byte)


#    @tulip.task
#    def start(self):
#        """ Start processing of incoming bytes, calling ``handle_input(byte)``
#        for each in-band NVT character received. The stream reader's"""
#        self.banner()
#        while True:
#            try:
#                yield from self.stream.read(1)
#            except tulip.CancelledError:
#                self.log_debug('Ignored premature client disconnection.')
#                break
#            except Exception as exc:
#                self.log_err(exc)
#            finally:
#                if self._closing:
#                    self.transport.close()
#                    break
#        self._request_handle = None


# `````````````````````````````````````````````````````````````````````````````

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
                       'SLC_EOR', 'SLC_ABORT', 'SLC_EOF', 'SLC_SUSP', 'SLC_EC',
                       'SLC_EL', 'SLC_EW', 'SLC_RP', 'SLC_LNEXT', 'SLC_XON',
                       'SLC_XOFF', 'SLC_FORW1', 'SLC_FORW2',)])
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
        lambda: TelnetServer(debug=True), args.host, args.port)
    x = loop.run_until_complete(f)
    print('serving on', x.getsockname())
    loop.run_forever()

if __name__ == '__main__':
    main()
        # self._tm_received = True

