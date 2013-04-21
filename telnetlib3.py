#!/usr/bin/env python3
"""
Not yet for consumption.

This project tests Guido's 'tulip' project; the asynchronous networking model to become standard with python 3.4 by implementing the Telnet client and server protocols.

This project requires python 3.3.

the 'tulip' module is included, retrieved Apr. 2013
"""
import collections
import logging
import argparse
import sys

assert sys.version >= '3.3', 'Please use Python 3.3 or higher.'
import tulip

from telnetlib import LINEMODE, NAWS, NEW_ENVIRON, ENCRYPT, AUTHENTICATION
from telnetlib import BINARY, SGA, ECHO, STATUS, TTYPE, TSPEED, LFLOW
from telnetlib import XDISPLOC, IAC, DONT, DO, WONT, WILL, SE, NOP, DM, TM
from telnetlib import BRK, IP, AO, AYT, EC, EL, EOR, GA, SB
IS = bytes([0])
SEND = bytes([1])
EOF = bytes([236])
SUSP = bytes([237])
ABORT = bytes([238])

(SLC_SYNCH, SLC_BRK, SLC_IP, SLC_AO, SLC_AYT, SLC_EOR, SLC_ABORT,
        SLC_EOF, SLC_SUSP, SLC_EC, SLC_EL, SLC_EW, SLC_RP, SLC_LNEXT,
        SLC_XON, SLC_XOFF, SLC_FORW1, SLC_FORW2
        ) = (bytes([const]) for const in range(1,19))
(SLC_FLUSHIN, SLC_FLUSHOUT, SLC_ACK
        ) = (bytes([32]), bytes([64]), bytes([128]))
(SLC_NOSUPPORT, SLC_CANTCHANGE, SLC_VALUE, SLC_DEFAULT
        ) = (bytes([const]) for const in range(4))
(LFLOW_OFF, LFLOW_ON, LFLOW_RESTART_ANY, LFLOW_RESTART_XON
        ) = (bytes([const]) for const in range(4))

_DEBUG_OPTS = dict([(value, key)
    for key, value in globals().items() if key in
    ('LINEMODE', 'NAWS', 'NEW_ENVIRON', 'ENCRYPT', 'AUTHENTICATION',
        'BINARY', 'SGA', 'ECHO', 'STATUS', 'TTYPE', 'TSPEED', 'LFLOW',
        'XDISPLOC', 'IAC', 'DONT', 'DO', 'WONT', 'WILL', 'SE', 'NOP',
        'DM', 'TM', 'BRK', 'IP', 'ABORT', 'AO', 'AYT', 'EC', 'EL',
        'EOR', 'GA', 'SB', 'EOF', 'SUSP', 'ABORT',)])
_DEBUG_SLC_OPTS = dict([(value, key)
    for key, value in locals().items() if key in
    ('SLC_SYNCH', 'SLC_BRK', 'SLC_IP', 'SLC_AO', 'SLC_AYT', 'SLC_EOR',
        'SLC_ABORT', 'SLC_EOF', 'SLC_SUSP', 'SLC_EC', 'SLC_EL',
        'SLC_EW', 'SLC_RP', 'SLC_LNEXT', 'SLC_XON', 'SLC_XOFF',
        'SLC_FORW1', 'SLC_FORW2',)])
_DEBUG_SLC_BITMASK = dict([(value, key)
    for key, value in locals().items() if key in
    ('SLC_FLUSHIN', 'SLC_FLUSHOUT', 'SLC_ACK',)])
_DEBUG_SLC_MODIFIERS = dict([(value, key)
    for key, value in locals().items() if key in
    ('SLC_NOSUPPORT', 'SLC_CANTCHANGE', 'SLC_VALUE', 'SLC_DEFAULT',)])

def name_slc_command(byte):
    return (repr(byte) if byte not in _DEBUG_SLC_OPTS
            else _DEBUG_SLC_OPTS[byte])

def name_slc_modifier(byte):
    debug_str = (repr(byte) if byte not in _DEBUG_SLC_MODIFIERS
            else _DEBUG_SLC_MODIFIERS[byte])
    for modifier, key in _DEBUG_SLC_BITMASK.items():
        if ord(byte) & ord(modifier):
            debug_str += ',%s' % (key,)
    return debug_str

def name_command(byte):
    return (repr(byte) if byte not in _DEBUG_OPTS
            else _DEBUG_OPTS[byte])


class TelnetStreamReader(tulip.StreamReader):
    """
    This differs from StreamReader by processing bytes for telnet protocols.
    Handles all of the option negotiation and various sub-negotiations.
    """
    _iac_received = False
    _cmd_received = False
    _sb_received = False
    # ``pending_option`` is a dictionary of <opt> bytes that follow an IAC DO
    # or DONT, and contains a value of ``True`` until an IAC WILL or WONT has
    # been received by remote end. Sub-negotiation pending replies are keyed by
    # two bytes, SB + <opt>.
    pending_option = {}
    local_option = {}
    remote_option = {}
    request_env = "USER HOSTNAME UID TERM COLUMNS LINES DISPLAY LANG".split()

    def __init__(self, transport, client=None, server=None,
            debug=False, log=logging):
        """ This stream decodes bytes as seen by ``TelnetProtocol``.

        Because Server and Client support different capabilities,
        the mutually exclusive booleans ``client`` and ``server``
        indicates which end the protocol is attached to. The default
        is server, meaning, this stream reads _from_ a telnet clients. """
        assert client == None or server == None, (
                'Only client= or server= should be set, not both.')
        self.server = ((client == None and server in (None, True))
                       or client == False)
        self.transport = transport
        self._sb_buffer = collections.deque()
        self.debug = debug
        self.log = log
        tulip.StreamReader.__init__(self)

    def feed_data(self, data):
        """ Receiving bytes arrived by ``TelnetProtocol.data_received()``.

        Copy bytes from ``data`` into ``self.buffer`` through a state-logic
        flow, detecting and handling telnet commands and negotiation options.

        During subnegotiation, bytes received are buffered into
        ``self._sb_buffer``. The same maximum buffer size, ``self.limit``,
        applies as it does to ``self.buffer``.
        """
        if not data:
            return
        self.byte_count += len(data)

        for byte in data:
            self._parser(byte)

    def _parser(self, byte):
        """ This parser processes out-of-band Telnet data, marked by byte IAC.

        Extending or changing protocol capabilities shouldn't necessarily
        require deriving this method, but the methods it delegates to, mainly
        those methods beginning with 'handle' or 'parse'.

        States and their "codes", if applicable, are stored as booleans
        ``_iac_received`` and ``_sb_received``, and byte ``_cmd_received``.
        Values of these states are non-None when that state is active.

        Negotiated options are stored in dict ``self.local_option``,
        and ``self.remote_option``. Pending replies are noted with
        dict ``self.pending_option``.
        """
        byte = bytes([byte])
#        print('parse: %r (%s).' % (byte, name_command(byte),))
        if byte == IAC:
            self._iac_received = (not self._iac_received)
            if not self._iac_received:
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
            # with IAC SB already received, buffer until IAC + SB
            self._sb_buffer.append(byte)

        elif self._cmd_received:
            cmd, opt = self._cmd_received, byte

            # unset self.pending_option for any IAC WONT or WILL options,
            if cmd == WONT:
                if (DO, cmd) in self.pending_option:
                    self.pending_option[DO + opt] = False
                if (DONT, cmd) in self.pending_option:
                    self.pending_option[DONT + opt] = False
            if cmd == WILL:
                if (DO, cmd) in self.pending_option:
                    self.pending_option[DO + opt] = False
                if (DONT, cmd) in self.pending_option:
                    # This end previously requested remote end *not* to
                    # perform a a capability, but remote end has replied
                    # with a WILL. Occurs due to poor timing at negotiation
                    # time. DO STATUS is often used to settle the difference.
                    self.pending_option[DONT + opt] = False

            # parse IAC DO, DONT, WILL, and WONT responses.
            if self._cmd_received == DO:
                self.handle_do(opt)
            elif self._cmd_received == DONT:
                self.handle_dont(opt)
            elif self._cmd_received == WILL:
                self.handle_will(opt)
            elif self._cmd_received == WONT:
                self.handle_wont(opt)
            self._cmd_received = False

        else:
            # in-bound data
            self.buffer.append(byte)

    def iac(self, cmd, opt):
        """ Send IAC <cmd> <opt> to remote end.

        For iac ``cmd`` DO and DONT, ``self.pending_option[cmd + opt]``
        is set True if ``self.remote_option[opt]`` is not set, or remote
        option value is the inverse value of option requested.
        """
        assert cmd in (DO, DONT, WILL, WONT), (
                'Illegal IAC cmd, %r.' % (cmd,))
        remote_opt = self.remote_option.get(opt, None)
        if (cmd == DO and remote_opt in (False, None)
                or cmd == DONT and remote_opt in (True, None)):
            self.pending_option[cmd + opt] = True
        elif(cmd == WILL and self.local_option.get(opt, None) != True):
            self.local_option[opt] = True
        elif(cmd == WONT and self.local_option.get(opt, None) != False):
            self.local_option[opt] = False
        self.transport.write(IAC + cmd + opt)
        print('send IAC %s %s' % (name_command(cmd), name_command(opt),))

    def parse_iac_command(self, cmd):
        """
        Handle IAC commands BRK, IP, AO, AYT, EC, EL.

        Implementors of these options should derive this method.
        """
        print('recv IAC %s' % (name_command(cmd),))
        if cmd == BRK:
            self.handle_brk()
        elif cmd == IP:
            self.handle_ip()
        elif cmd == AO:
            self.handle_ao()
        elif cmd == AYT:
            self.handle_ayt()
        elif cmd == EC:
            self.handle_ec()
        elif cmd == EL:
            self.handle_el()
        elif cmd == EOR:
            self.handle_eor()
        elif cmd == EOF:
            self.handle_eof()
        elif cmd == SUSP:
            self.handle_susp()
        elif cmd == ABORT:
            self.handle_abort()
        elif cmd == NOP:
            pass

    def handle_sb_tspeed(self, buf):
        assert buf.popleft() == TSPEED
        print('tspeed: %r' % (buf,))

    def handle_sb_newenv(self, buf):
        assert buf.popleft() == NEW_ENVIRON
        chk_byte = buf.popleft()
        if not chk_byte in bytes([0, 2]):
            raise ValueError('Expected IS or INFO after IAC SB NEW_ENVIRON, '
                    'got %s' % (name_command(chk_byte),))
        breaks = list([idx for (idx, byte) in enumerate(buf)
                       if byte in (b'\x00', b'\x03')])
        for start, end in zip(breaks, breaks[1:]):
            # not desirable, how do we splice & split bytes ..?
            decoded = bytes([ord(byte) for byte in buf]).decode('ascii')
            pair = decoded[start + 1:end].split('\x01', 1)
            if 1 == len(pair):
                self.handle_env(pair[0], '')
            else:
                key, value = pair
                self.handle_env(key, value)


    def handle_sb_naws(self, buf):
        assert buf.popleft() == NAWS
        columns = str((256 * ord(buf[0])) + ord(buf[1]))
        rows = str((256 * ord(buf[2])) + ord(buf[3]))
        self.handle_naws(columns, rows)

    def handle_sb_lflow(self, buf):
        assert buf.popleft() == LFLOW
        assert self.local_option.get(LFLOW, None) is True, (
                'received IAC SB LFLOW wihout IAC DO LFLOW')

    def handle_sb_linemode(self, buf):
        print('linemode: %r' % (buf,))
        assert buf.popleft() == LINEMODE
        assert self.local_option.get(LINEMODE, None) is True, (
                'received IAC SB LINEMODE wihout IAC DO LINEMODE')
        MODE = bytes([1])
        EDIT = bytes([1])
        TRAPSIG = bytes([2])
        MODE_ACK = bytes([4])
        FORWARDMASK = bytes([2])
        SLC = bytes([3])
        cmd = buf.popleft()
        if cmd == MODE:
            mask = ord(buf.popleft())
            self.linemode['edit'] = bool(mask & ord(EDIT))
            self.linemode['trapsig'] = bool(mask & ord(TRAPSIG))
            self.linemode['mode_ack'] = bool(mask & ord(MODE_ACK))
        elif cmd == SLC:
            self.handle_sb_linemode_slc(buf)
        elif cmd in (DO, DONT, WILL, WONT):
            opt = buf.popleft()
            assert opt == FORWARDMASK, ('Illegal IAC SB LINEMODE %s %r' % (
                name_command(cmd), opt))
            if cmd == DO:
                self.handle_sb_linemode_forwardmask(buf)
            assert buf[1] == SLC

    def handle_sb_linemode_slc(self, buf):
        print('handle_sb_linemode_slc: %r' % (buf,))
        while True:
            func = buf.popleft()
            if ord(func) == 0:
                assert 0 == len(buf)
                return
            modifier = buf.popleft()
            char = buf.popleft()
            print('(func, modifier, char): (%s, %r, %r)' % (
                name_slc_command(func), name_slc_modifier(modifier), char))

    def handle_sb_linemode_forwardmask(self, buf):
        print('handle_sb_linemode_forwardmask: %r' % (buf,))

    def parse_subnegotiation(self, buf):
        """ Callback containing the sub-negotiation buffer. Called after
        IAC + SE is received, indicating the end of sub-negotiation command.

        SB options TTYPE, XDISPLOC, NEW_ENVIRON, NAWS, and STATUS, are
        supported. Changes to the default responses should derive callbacks
        ``handle_linemode``, ``handle_sb_ttype``, ``handle_sb_xdisploc``,
        ``handle_sb_newenviron``, ``handle_sb_status``, and ``handle_sb_news``.

        Implementors of additional SB options should extend this method. """
#        print('parse_sb: %r' % (buf,))
        if not buf:
            raise ValueError('SE: buffer empty')
        elif buf[0] == b'\x00':
            raise ValueError('SE: buffer is NUL')
        elif len(buf) < 2:
            raise ValueError('SE: buffer too short: %r' % (buf,))
        elif buf[0] == LINEMODE:
            self.handle_sb_linemode(buf)
        elif buf[0] == LFLOW:
            self.handle_sb_lflow(buf)
            if not self.server:
                raise ValueError('SE: received from server: LFLOW')
        elif buf[0] == NAWS:
            if not self.server:
                raise ValueError('SE: received from server: NAWS')
            self.handle_sb_naws(buf)
        elif buf[0] == NEW_ENVIRON:
            if not self.server:
                raise ValueError('SE: received from server: NEW_ENVIRON IS')
            self.handle_sb_newenv(buf)
        elif (buf[0], buf[1]) == (TTYPE, IS):
            if not self.server:
                raise ValueError('SE: received from server: TTYPE IS')
            self.handle_sb_ttype(buf)
        elif (buf[0], buf[1]) == (TSPEED, IS):
            if not self.server:
                raise ValueError('SE: received from server: TSPEED IS')
            self.handle_sb_tspeed(buf)
        elif (buf[0], buf[1]) == (XDISPLOC, IS):
            if not self.server:
                raise ValueError('SE: received from server: XDISPLOC IS')
            self.handle_sb_xdisploc(buf)
        elif (buf[0], buf[1]) == (STATUS, SEND):
            self.handle_sb_status()
        else:
            raise ValueError('SE: sub-negotiation unsupported: %r' % (buf,))

    def _send_status(self):
        """ Respond after DO STATUS received by DE (rfc859). """
        assert self.local_option.get('STATUS', None) is True, (
                u'Only the sender of IAC WILL STATUS may send '
                u'IAC SB STATUS IS.')
        response = collections.deque(bytes([IAC, SB, STATUS, IS]))
        for opt, status in self.local_option.items():
            # status is 'WILL' for local option states that are True,
            # and 'WONT' for options that are False.
            response.append(bytes([WILL if status else WONT, opt]))
        for opt, status in self.remote_option.items():
            # status is 'DO' for remote option states that are True,
            # or for any DO option requests pending reply. status is
            # 'DONT' for any remote option states that are False,
            # or for any DONT option requests pending reply.
            if status or DO + opt in self.pending_option:
                response.append(bytes([DO, opt]))
            elif not status or DONT + opt in self.pending_option:
                response.append(bytes([DONT, opt]))
        response.append(bytes([IAC, SE]))
        self.transport.write(response)

    def _request_sb_newenviron(self):
        """ Request sub-negotiation NEW_ENVIRON, RFC 1572. This should
        not be called directly, but by answer to WILL NEW_ENVIRON after DO
        request from server.
        """
        if self.pending_option.get(SB + NEW_ENVIRON, False):
            # avoid calling twice during pending reply
            return
        self.pending_option[SB + NEW_ENVIRON] = True
        response = collections.deque(bytes([IAC, SB, STATUS, IS,]))
        response.append(b''.join(([IAC, SB, NEW_ENVIRON, SEND, bytes([0])])))
        response.append(b'\x00'.join(self.request_env))
        response.append([b'\x03', IAC, SE,])
        self.transport.write(response)

    def handle_do(self, opt):
        """ Process byte 3 of series (IAC, DO, opt) received by remote end.

        answer WILL ECHO for servers, and BINARY, SGA, TM, LINEMODE, and
        STATUS for both clients and servers. Answer WONT for ENCRYPT, or
        any other capability.

        This method can be derived to modify or extend protocol capabilities.
        The result of a supported capability is a response of (IAC, WILL, opt)
        and the setting of ``self.local_option[opt]`` of ``True``. For
        unsupported capabilities, RFC specifies a response of (IAC, WONT, opt).
        Similarly, set ``self.local_option[opt]`` to ``False``.
        """
        print('handle_do(%s)' % (name_command(opt)))
        # options that we support
        if opt == ECHO and not self.server:
                raise ValueError('DO ECHO received on server end.')
        if opt == LINEMODE and self.server:
                raise ValueError('DO LINEMODE received on client end.')
        if opt in (ECHO, LINEMODE, BINARY, SGA, TM, LFLOW):
            if not self.local_option.get(opt, None):
                self.local_option[opt] = True
                self.iac(WILL, opt)
        elif opt == STATUS:
            # IAC DO STATUS is used to obtain request to have server
            # transmit status information. Only the sender of
            # WILL STATUS is free to transmit status information.
            if not self.local_option.get(opt, None):
                self.local_option[opt] = True
                self.iac(WILL, STATUS)
            self._send_status()
        else:
            if self.local_option.get(opt, None) is None:
                self.local_option[opt] = False
                self.iac(WONT, opt)
                raise ValueError('Unhandled: DO %s.' % (name_command(opt),))

    def handle_dont(self, opt):
        """ Process byte 3 of series (IAC, DONT, opt) received by remote end.

        The standard implementation "agrees" by replying with (IAC, WONT, opt)
        for all options received, unless said reply has already been sent.

        ``self.local_option[opt]`` is set ``False`` for the telnet command
        option byte, ``opt`` to note local option.
        """
        print('handle_dont(%s)' % (name_command(opt)))
        if self.local_option.get(opt, None) in (True, None):
            # option is unknown or True
            self.local_option[opt] = False
        self.iac(WONT, opt)

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
        print('handle_will(%s)' % (name_command(opt)))
        if opt == ECHO and self.server:
            raise ValueError('WILL ECHO received on server stream')
        elif opt == NAWS and not self.server:
            raise ValueError('WILL NAWS received on client stream')
        elif opt == XDISPLOC and not self.server:
            raise ValueError('WILL XDISPLOC received on client stream')
        elif opt == TTYPE and not self.server:
            raise ValueError('WILL TTYPE received on client stream')
        elif opt in (BINARY, SGA, ECHO, NAWS, LINEMODE):
            if not self.remote_option.get(opt, None):
                self.remote_option[opt] = True
            self.iac(DO, opt)
        elif opt == STATUS:
            if not self.remote_option.get(opt, None):
                self.remote_option[opt] = True
            self.transport.write(
                    b''.join([IAC, SB, STATUS, SEND, IAC, SE,]))
            # set pending for SB STATUS
            self.pending_option[SB + opt] = True
        elif opt == NEW_ENVIRON:
            if not self.remote_option.get(opt, None):
                self.remote_option[opt] = True
            self.transport.write(
                    b''.join([IAC, SB, NEW_ENVIRON, SEND, IS,]))
            self.transport.write(
                    b'\x00'.join([bytes(env, 'ascii')
                        for env in self.request_env]))
            self.transport.write(
                    b''.join([b'\x03', IAC, SE,]))
            # set pending for SB NEW_ENVIRON
            self.pending_option[SB + opt] = True
        elif opt == XDISPLOC:
            if not self.remote_option.get(opt, None):
                self.remote_option[opt] = True
            self.transport.write(
                    b''.join([IAC, SB, XDISPLOC, SEND, IAC, SE,]))
            # set pending for SB XDISPLOC
            self.pending_option[SB + opt] = True
        elif opt == TTYPE:
            if not self.remote_option.get(opt, None):
                self.remote_option[opt] = True
            self.transport.write(
                    b''.join([IAC, SB, TTYPE, SEND, IAC, SE,]))
            # set pending for SB TTYPE
            self.pending_option[SB + opt] = True
        elif opt == TSPEED:
            if not self.remote_option.get(opt, None):
                self.remote_option[opt] = True
            self.transport.write(
                    b''.join([IAC, SB, TSPEED, SEND, IAC, SE,]))
            # set pending for SB TSPEED
            self.pending_option[SB + opt] = True
        else:
            self.remote_option[opt] = False
            self.iac(DONT, opt)
            raise ValueError('Unhandled: WILL %s.' % (name_command(opt),))

    def handle_wont(self, opt):
        """ Process byte 3 of series (IAC, WONT, opt) received by remote end.

        The remote end requests we do not perform any number of capabilities.
        It really isn't possible to decline a WONT. RFC requires answering
        in the affirmitive with DONT.

        The default implementation agrees DONT for all capabilities.
        """
        print('handle_wont(%s)' % (name_command(opt)))
        if self.remote_option.get(opt, None) in (True, None):
            self.remote_option[opt] = False
        self.iac(DONT, opt)

    def handle_ip(self):
        """ XXX

        Handle Interrupt Process (IAC, IP), sent by clients by ^c. """
        self.buffer.append(b'\x03')

    def handle_abort(self):
        """ XXX

        Handle Abort (IAC, ABORT). Similar to "IAC IP", but means only to
        abort or terminate the process to which the NVT is connected.  """
        self.buffer.append(b'\x03')

    def handle_susp(self):
        """ XXX

        Handle Suspend Process (IAC, SUSP). Suspend the execution of the
        current process attached to the NVT in such a way that another
        process will take over control of the NVT, and the suspended
        process can be resumed at a later time.  If the receiving system
        does not support this functionality, it should be ignored.
        """
        self.buffer.append(b'\x1a')


    def handle_ao(self):
        """ XXX

        Handle Abort Output (IAC, AO), sent by clients to discard any remaining
        output. If the AO were received ... a reasonable implementation would
        be to suppress the remainder of the text string, but transmit the
        prompt character and the preceding <CR><LF>.
        """
        self.transport._buffer.clear()

    def handle_brk(self):
        """ XXX

        Handle Break (IAC, BRK), sent by clients to indicate BREAK keypress,
        this is *not* ctrl+c.  """
        pass

    def handle_ayt(self):
        """ XXX
        Handle Are You There (IAC, AYT), which provides the user with some
        visible (e.g., printable) evidence that the system is still up and
        running.  """
        pass

    def handle_ec(self):
        """ XXX
        Handle Erase Character (IAC, EC). Provides a function which deletes
        the last preceding undeleted character from the stream of data being
        supplied by the user ("Print position" is not calculated).  """
        try:
            self.buffer.pop()
        except IndexError:
            pass

    def handle_el(self):
        """ XXX
        Handle Erase Line (IAC, EL). Provides a function which deletes all
        the data in the current "line" of input. """
        byte = None
        while byte != '\r':
            try:
                byte = self.buffer.pop()
            except IndexError:
                break

    def handle_eor(self):
        """ XXX
        Handle End of Record (IAC, EOR). rfc885 """
        pass

    def handle_eof(self):
        """ XXX
        Handle End of Record (IAC, EOR). rfc885 """
        self.buffer.append(b'\x04')


    def handle_naws(self, width, height):
        """ XXX
        Receive new window size from NAWS protocol. """
        pass

    def handle_env(self, key, value):
        """ XXX
        Receive new environment variable value. """
        pass


class TelnetServer(tulip.protocols.Protocol):
    def __init__(self, log=logging, debug=False):
        self.log = log
        self.debug = debug

    def connection_made(self, transport):
        self.transport = transport
        self.stream = TelnetStreamReader(transport, server=True)
        self.stream.handle_naws = self.handle_winresize
        self.stream.handle_env = self.handle_env
        self._request_handle = self.start()

    def data_received(self, data):
        print('recv', repr(data))
        self.stream.feed_data(data)

    def eof_received(self):
        print('eof')

    def close(self):
        self._closing = True

    def banner(self):
        """ XXX
        """
        self.transport.write(b'Welcome to telnetlib3\r\n')
        self.stream.iac(WILL, BINARY)
        self.stream.iac(WILL, TM)
        self.stream.iac(WILL, LINEMODE)
        self.stream.iac(WONT, ENCRYPT)
        self.stream.iac(DO, TSPEED)
        self.stream.iac(DO, NAWS)
        self.stream.iac(DO, NEW_ENVIRON)

    def prompt(self):
        """ XXX
        """
        self.transport.write(b'\r\n >')
        if self.stream.local_option.get(SGA, None) != True:
            self.transport.write(GA)

    def handle_winresize(self, width, height):
        print('window size change, COLUMNS=%s, LINES=%s' % (width, height,))

    def handle_env(self, key, value):
        print("ENV['%s'] = '%s'" % (key, value,))

    def handle_line(self, buf):
        if self.stream.local_option.get(ECHO, None):
            self.transport.write(buf.rstrip() + b'\r\n')
        for byte in buf:
            self.handle_input(byte)
        self.prompt()

    def handle_input(self, byte):
        if self.stream.local_option.get(ECHO, None):
            self.transport.write(byte)
        print('Client input: %r' % (byte,))

    @tulip.task
    def start(self):
        """ Start processing of incoming bytes.

        If stream.local_option[LINEMODE] becomes True, this class
        calls the ``handle_line`` callback for each line of input
        received. Otherwise, ``handle_input`` callback for each
        in-band byte recieved. """
        self.banner()
        def is_linemode():
            # the combination of DO(ECHO) and DO(SGA) sent by
            # server is the equivalent of entering full-duplex,
            # character-at-a-time mode.
            print(self.local_option.get('LINEMODE', None))
            if (self.local_option.get('LINEMODE', None) in (None, False)
                    or (self.local_option.get('ECHO', None) and
                        self.local_option.get('SGA', None))):
                return False
            return True
        while True:
            try:
                # by default, telnet runs in a half-duplex linemode option,
                # which does not process input until a CR is received.
                if is_linemode():
                    buf = list()
                    while True:  # per rfc 854
                        # ... "CR LF" must be treated as a single "new line"
                        # character ...  the sequence "CR NUL" must be used
                        # where a carriage return alone is actually desired;
                        # ... CR character must be avoided in other contexts.
                        byte = yield from self.stream.read(1)
                        if byte == b'\r':
                            nbyte = yield from self.stream.read(1)
                            assert nbyte in (b'\n', b'\x00'), (
                                    'LF or NUL must follow CR: %r' % (nbyte,))
                        if byte in (IAC, u'\r'):
                            break
                    self.handle_line(b''.join(buf))
                else:
                    byte = yield from self.stream.read(1)
                    self.handle_input(byte)
            except tulip.CancelledError:
                self.log_debug('Ignored premature client disconnection.')
                break
            except Exception as exc:
                self.log_err(exc)
            finally:
                if self._closing:
                    self.transport.close()
                    break
        self._request_handle = None


ARGS = argparse.ArgumentParser(description="Run simple telnet server.")
ARGS.add_argument(
    '--host', action="store", dest='host',
    default='127.0.0.1', help='Host name')
ARGS.add_argument(
    '--port', action="store", dest='port',
    default=6023, type=int, help='Port number')
#    '--opt', action="store_true", dest='bool_name', help='desc')
#    '--opt', action="store", dest='value_name', help='desc')


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
