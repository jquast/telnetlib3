"""Module provides :class:`TelnetWriter` and :class:`TelnetWriterUnicode`."""
# std imports
import asyncio
import collections
import logging
import struct
import sys

# local imports
from . import slc
from .telopt import (ABORT, ACCEPTED, AO, AYT, BINARY, BRK, CHARSET, CMD_EOR,
                     DM, DO, DONT, EC, ECHO, EL, EOF, EOR, ESC, GA, IAC, INFO,
                     IP, IS, LFLOW, LFLOW_OFF, LFLOW_ON, LFLOW_RESTART_ANY,
                     LFLOW_RESTART_XON, LINEMODE, LOGOUT, NAWS, NEW_ENVIRON,
                     NOP, REJECTED, REQUEST, SB, SE, SEND, SGA, SNDLOC, STATUS,
                     SUSP, TM, TSPEED, TTABLE_ACK, TTABLE_NAK, TTABLE_IS,
                     TTABLE_REJECTED, TTYPE, USERVAR, VALUE, VAR, WILL, WONT,
                     XDISPLOC, name_command, name_commands, theNULL)


__all__ = ('TelnetWriter', 'TelnetWriterUnicode', )


class TelnetWriter(asyncio.StreamWriter):
    #: Total bytes sent to :meth:`~.feed_byte`
    byte_count = 0

    #: Whether flow control is enabled.
    lflow = True

    #: Whether flow control enabled by Transmit-Off (XOFF) (Ctrl-s), should
    #: re-enable Transmit-On (XON) only on receipt of XON (Ctrl-q).  When
    #: False, any keypress from client re-enables transmission.
    xon_any = False

    #: Whether the last byte received by :meth:`~.feed_byte` is the beginning
    #: of an IAC command.
    iac_received = None

    #: Whether the last byte received by :meth:`~.feed_byte` begins an IAC
    #: command sequence.
    cmd_received = None

    #: Whether the last byte received by :meth:`~.feed_byte` is a matching
    #: special line character value, if negotiated.
    slc_received = None

    #: SLC function values and callbacks are fired for clients in Kludge
    #: mode not otherwise capable of negotiating LINEMODE, providing
    #: transport remote editing function callbacks for dumb clients.
    slc_simulated = True

    default_slc_tab = slc.BSD_SLC_TAB

    #: Initial line mode requested by server if client supports LINEMODE
    #: negotiation (remote line editing and literal echo of control chars)
    default_linemode = slc.Linemode(
        bytes([ord(slc.LMODE_MODE_REMOTE) | ord(slc.LMODE_MODE_LIT_ECHO)]))

    def __init__(self, transport, protocol, *, client=False, server=False,
                 reader=None, loop=None, log=None):
        """
        A writer interface for the telnet protocol.

        Telnet IAC Interpreter.

        Almost all negotiation actions are performed through the writer
        interface, as any action requires writing bytes to the underling
        stream.  This class implements :meth:`~.feed_byte`, which acts as a
        Telnet *Is-A-Command* (IAC) interpreter.

        The significance of the last byte passed to this method is tested
        by instance attribute :attr:`~.is_oob`, following the call to
        :meth:`~.feed_byte` to determine whether the given byte is in or out
        of band.

        A minimal Telnet Protocol method,
        :meth:`asyncio.Protocol.data_received`, should forward each byte to
        :meth:`~.feed_byte`, which returns True to indicate the given byte should be
        forwarded to a Protocol reader method.

        :param bool client: Whether the IAC interpreter should react from
            the client point of view.
        :param bool server: Whether the IAC interpreter should react from
            the server point of view.
        :param logging.Logger log: target logger, if None is given, one is
            created using the namespace ``'telnetlib3.stream_writer'``.
        :param asyncio.AbstractEventLoop loop: set the event loop to
            use.  The return value of :func:`asyncio.get_event_loop` is used
            when unset.
        """
        # fix tests in 3.8
        if loop is None and sys.version_info[:2] >= (3, 8):
            loop = asyncio.get_event_loop()

        asyncio.StreamWriter.__init__(self, transport, protocol, reader, loop)

        if not any((client, server)) or all((client, server)):
            raise TypeError("keyword arguments `client', and `server' "
                            "are mutually exclusive.")
        self._server = server
        self.log = log or logging.getLogger(__name__)

        #: Dictionary of telnet option byte(s) that follow an
        #: IAC-DO or IAC-DONT command, and contains a value of ``True``
        #: until IAC-WILL or IAC-WONT has been received by remote end.
        self.pending_option = Option('pending_option', self.log)

        #: Dictionary of telnet option byte(s) that follow an
        #: IAC-WILL or IAC-WONT command, sent by our end,
        #: indicating state of local capabilities.
        self.local_option = Option('local_option', self.log)

        #: Dictionary of telnet option byte(s) that follow an
        #: IAC-WILL or IAC-WONT command received by remote end,
        #: indicating state of remote capabilities.
        self.remote_option = Option('remote_option', self.log)

        #: Sub-negotiation buffer
        self._sb_buffer = collections.deque()

        #: SLC buffer
        self._slc_buffer = collections.deque()

        #: SLC Tab (SLC Functions and their support level, and ascii value)
        self.slctab = slc.generate_slctab(self.default_slc_tab)

        #: Represents LINEMODE MODE negotiated or requested by client.
        #: attribute ``ack`` returns True if it is in use.
        self._linemode = slc.Linemode()

        self._connection_closed = False

        # Set default callback handlers to local methods.  A base protocol
        # wishing not to wire any callbacks at all may simply allow our stream
        # to gracefully log and do nothing about in most cases.
        self._iac_callback = {}
        for iac_cmd, key in ((BRK, 'brk'), (IP, 'ip'),
                             (AO, 'ao'), (AYT, 'ayt'),
                             (EC, 'ec'), (EL, 'el'),
                             (EOF, 'eof'), (SUSP, 'susp'),
                             (ABORT, 'abort'), (NOP, 'nop'),
                             (DM, 'dm'), (GA, 'ga'),
                             (CMD_EOR, 'eor'), (TM, 'tm')):
            self.set_iac_callback(
                cmd=iac_cmd, func=getattr(self, 'handle_{}'.format(key)))

        self._slc_callback = {}
        for slc_cmd, key in (
                (slc.SLC_SYNCH, 'dm'), (slc.SLC_BRK, 'brk'),
                (slc.SLC_IP, 'ip'), (slc.SLC_AO, 'ao'),
                (slc.SLC_AYT, 'ayt'), (slc.SLC_EOR, 'eor'),
                (slc.SLC_ABORT, 'abort'), (slc.SLC_EOF, 'eof'),
                (slc.SLC_SUSP, 'susp'), (slc.SLC_EC, 'ec'),
                (slc.SLC_EL, 'el'), (slc.SLC_EW, 'ew'),
                (slc.SLC_RP, 'rp'), (slc.SLC_LNEXT, 'lnext'),
                (slc.SLC_XON, 'xon'), (slc.SLC_XOFF, 'xoff'),):
            self.set_slc_callback(
                slc_byte=slc_cmd, func=getattr(self, 'handle_{}'.format(key)))

        self._ext_callback = {}
        for ext_cmd, key in (
            (LOGOUT, 'logout'), (SNDLOC, 'sndloc'), (NAWS, 'naws'),
            (TSPEED, 'tspeed'), (TTYPE, 'ttype'), (XDISPLOC, 'xdisploc'),
            (NEW_ENVIRON, 'environ'), (CHARSET, 'charset'),
        ):
            self.set_ext_callback(
                cmd=ext_cmd, func=getattr(self, 'handle_{}'.format(key)))

        self._ext_send_callback = {}
        for ext_cmd, key in (
                (TTYPE, 'ttype'), (TSPEED, 'tspeed'), (XDISPLOC, 'xdisploc'),
                (NAWS, 'naws'), (SNDLOC, 'sndloc')):
            self.set_ext_send_callback(
                cmd=ext_cmd, func=getattr(self, 'handle_send_{}'.format(key)))

        for ext_cmd, key in (
                (CHARSET, 'charset'), (NEW_ENVIRON, 'environ')):
            _cbname = ('handle_send_server_' if self.server else
                       'handle_send_client_')
            self.set_ext_send_callback(
                cmd=ext_cmd, func=getattr(self, _cbname + key))

    @property
    def connection_closed(self):
        return self._connection_closed

    # Base protocol methods

    def close(self):
        if self.connection_closed:
            return
        super().close()
        # break circular refs
        self._ext_callback.clear()
        self._ext_send_callback.clear()
        self._slc_callback.clear()
        self._iac_callback.clear()
        self.fn_encoding = None
        self._protocol = None
        self._transport = None
        self._connection_closed = True

    def __repr__(self):
        """Description of stream encoding state."""
        info = ['TelnetWriter']
        if self.server:
            info.append('server')
            endpoint = 'client'
        else:
            info.append('client')
            endpoint = 'server'

        info.append('mode:{self.mode}'.format(self=self))

        # IAC options
        info.append('{0}lineflow'.format('+' if self.lflow else '-'))
        info.append('{0}xon_any'.format('+' if self.xon_any else '-'))
        info.append('{0}slc_sim'.format('+' if self.slc_simulated else '-'))

        # IAC negotiation status
        _failed_reply = sorted([name_commands(opt) for (opt, val)
                                in self.pending_option.items()
                                if val])
        if _failed_reply:
            info.append('failed-reply:{opts}'.format(
                opts=','.join(_failed_reply)))

        _local = sorted([name_commands(opt) for (opt, val)
                         in self.local_option.items()
                         if self.local_option.enabled(opt)])
        if _local:
            localpoint = 'server' if self.server else 'client'
            info.append('{kind}-will:{opts}'.format(
                kind=localpoint, opts=','.join(_local)))

        _remote = sorted([
            name_commands(opt) for (opt, val)
            in self.remote_option.items()
            if self.remote_option.enabled(opt)])
        if _remote:
            info.append('{kind}-will:{opts}'.format(
                kind=endpoint, opts=','.join(_remote)))

        return '<{0}>'.format(' '.join(info))

    def write(self, data):
        """
        Write a bytes object to the protocol transport.

        :rtype: None
        """
        self._write(data)

    def writelines(self, lines):
        """
        Write unicode strings to transport.

        Note that newlines are not added.  The sequence can be any iterable
        object producing strings. This is equivalent to calling write() for
        each string.
        """
        self.write(b''.join(lines))

    def feed_byte(self, byte):
        """
        Feed a single byte into Telnet option state machine.

        :param int byte: an 8-bit byte value as integer (0-255), or
            a bytes array.  When a bytes array, it must be of length
            1.
        :rtype bool: Whether the given ``byte`` is "in band", that is, should
            be duplicated to a connected terminal or device.  ``False`` is
            returned for an ``IAC`` command for each byte until its completion.
        """
        self.byte_count += 1
        self.slc_received = None

        # list of IAC commands needing 3+ bytes (mbs: multibyte sequence)
        iac_mbs = (DO, DONT, WILL, WONT, SB)

        # cmd received is toggled False, unless its a mbs, then it is the
        # actual command that was received in (opt, byte) form.
        self.cmd_received = self.cmd_received in iac_mbs and self.cmd_received

        if byte == IAC:
            self.iac_received = (not self.iac_received)
            if not self.iac_received and self.cmd_received == SB:
                # SB buffer receives escaped IAC values
                self._sb_buffer.append(IAC)

        elif self.iac_received and not self.cmd_received:
            # parse 2nd byte of IAC
            self.cmd_received = cmd = byte
            if cmd not in iac_mbs:
                # DO, DONT, WILL, WONT are 3-byte commands, expect more.
                # Any other, expect a callback.  Otherwise this protocol
                # does not comprehend the remote end's request.
                if cmd not in self._iac_callback:
                    raise ValueError('IAC {0}({1!r}): not a legal 2-byte cmd'
                                     .format(name_command(cmd), cmd))
                self._iac_callback[cmd](cmd)
            self.iac_received = False

        elif self.iac_received and self.cmd_received == SB:
            # parse 2nd byte of IAC while while already within
            # IAC SB sub-negotiation buffer, assert command is SE.
            self.cmd_received = cmd = byte
            if cmd != SE:
                self.log.error('sub-negotiation buffer interrupted '
                               'by IAC {}'.format(name_command(cmd)))
                self._sb_buffer.clear()
            else:
                # sub-negotiation end (SE), fire handle_subnegotiation
                self.log.debug('sub-negotiation cmd {} SE completion byte'
                               .format(name_command(self._sb_buffer[0])))
                try:
                    self.handle_subnegotiation(self._sb_buffer)
                finally:
                    self._sb_buffer.clear()
                    self.iac_received = False
            self.iac_received = False

        elif self.cmd_received == SB:
            # continue buffering of sub-negotiation command.
            self._sb_buffer.append(byte)
            assert len(self._sb_buffer) < (1 << 15)  # 32k SB buffer

        elif self.cmd_received:
            # parse 3rd and final byte of IAC DO, DONT, WILL, WONT.
            cmd, opt = self.cmd_received, byte
            self.log.debug('recv IAC {} {}'.format(
                name_command(cmd), name_command(opt)))
            try:
                if cmd == DO:
                    try:
                        self.local_option[opt] = self.handle_do(opt)
                    finally:
                        if self.pending_option.enabled(WILL + opt):
                            self.pending_option[WILL + opt] = False
                elif cmd == DONT:
                    try:
                        self.handle_dont(opt)
                    finally:
                        self.pending_option[WILL + opt] = False
                        self.local_option[opt] = False
                elif cmd == WILL:
                    if not self.pending_option.enabled(DO + opt) and opt != TM:
                        self.log.debug('WILL {} unsolicited'.format(
                            name_command(opt)))
                    try:
                        self.handle_will(opt)
                    finally:
                        if self.pending_option.enabled(DO + opt):
                            self.pending_option[DO + opt] = False
                        # informed client, 'DONT', client responded with
                        # illegal 'WILL' response, cancel any pending option.
                        # Very unlikely state!
                        if self.pending_option.enabled(DONT + opt):
                            self.pending_option[DONT + opt] = False
                else:
                    # cmd is 'WONT'
                    self.handle_wont(opt)
                    self.pending_option[DO + opt] = False
            finally:
                # toggle iac_received on any ValueErrors/AssertionErrors raised
                self.iac_received = False
                self.cmd_received = (opt, byte)

        elif (self.mode == 'remote' or
              self.mode == 'kludge' and self.slc_simulated):
            # 'byte' is tested for SLC characters
            (callback, slc_name, slc_def) = slc.snoop(
                byte, self.slctab, self._slc_callback)

            # Inform caller which SLC function occurred by this attribute.
            self.slc_received = slc_name
            if callback:
                self.log.debug('slc.snoop({!r}): {}, callback is {}.'
                               .format(byte, slc.name_slc_command(slc_name),
                                       callback.__name__))
                callback(slc_name)

        # whether this data should be forwarded (to the reader)
        return not self.is_oob

    # Our protocol methods

    def get_extra_info(self, name, default=None):
        """Get optional server protocol information."""
        return self._protocol.get_extra_info(name, default)

    @property
    def protocol(self):
        """The protocol attached to this stream."""
        return self._protocol

    @property
    def server(self):
        """Whether this stream is of the server's point of view."""
        return bool(self._server)

    @property
    def client(self):
        """Whether this stream is of the client's point of view."""
        return bool(not self._server)

    @property
    def inbinary(self):
        """
        Whether binary data is expected to be received on reader, :rfc:`856`.
        """
        return self.remote_option.enabled(BINARY)

    @property
    def outbinary(self):
        """Whether binary data may be written to the writer, :rfc:`856`."""
        return self.local_option.enabled(BINARY)

    def echo(self, data):
        """
        Conditionally write ``data`` to transport when "remote echo" enabled.

        :param bytes data: string received as input, conditionally written.
        :rtype: None

        The default implementation depends on telnet negotiation willingness
        for local echo, only an RFC-compliant telnet client will correctly
        set or unset echo accordingly by demand.
        """
        assert self.server, ('Client never performs echo of input received.')
        if self.will_echo:
            self.write(data=data)

    @property
    def will_echo(self):
        """
        Whether Server end is expected to echo back input sent by client.

        From server perspective: the server should echo (duplicate) client
        input back over the wire, the client is awaiting this data to indicate
        their input has been received.

        From client perspective: the server will not echo our input, we should
        chose to duplicate our input to standard out ourselves.
        """
        return ((self.server and self.local_option.enabled(ECHO)) or
                (self.client and self.remote_option.enabled(ECHO)))

    @property
    def mode(self):
        """
        String describing NVT mode.

        :rtype str: One of:

            ``kludge``: Client acknowledges WILL-ECHO, WILL-SGA. character-at-
                a-time and remote line editing may be provided.

            ``local``: Default NVT half-duplex mode, client performs line
                editing and transmits only after pressing send (usually CR)

            ``remote``: Client supports advanced remote line editing, using
                mixed-mode local line buffering (optionally, echoing) until
                send, but also transmits buffer up to and including special
                line characters (SLCs).
        """
        if self.remote_option.enabled(LINEMODE):
            if self._linemode.local:
                return 'local'
            return 'remote'
        if self.server:
            if (self.local_option.enabled(ECHO) and
                    self.local_option.enabled(SGA)):
                return 'kludge'
            return 'local'
        if (self.remote_option.enabled(ECHO) and
                self.remote_option.enabled(SGA)):
            return 'kludge'
        return 'local'

    @property
    def is_oob(self):
        """The previous byte should not be received by the API stream."""
        return (self.iac_received or self.cmd_received)

    @property
    def linemode(self):
        """
        Linemode instance for stream.

        .. note:: value is meaningful after successful LINEMODE negotiation,
            otherwise does not represent the linemode state of the stream.

        Attributes of the stream's active linemode may be tested using boolean
        instance attributes, ``edit``, ``trapsig``, ``soft_tab``, ``lit_echo``,
        ``remote``, ``local``.
        """
        return self._linemode

    def send_iac(self, buf):
        """
        Send a command starting with IAC (base 10 byte value 255).

        No transformations of bytes are performed.  Normally, if the
        byte value 255 is sent, it is escaped as ``IAC + IAC``.  This
        method ensures it is not escaped,.
        """
        assert isinstance(buf, (bytes, bytearray)), buf
        assert buf and buf.startswith(IAC), buf
        self._transport.write(buf)

    def iac(self, cmd, opt=b''):
        """
        Send Is-A-Command 3-byte negotiation command.

        Returns True if command was sent. Not all commands are legal in the
        context of client, server, or pending negotiation state, emitting a
        relevant debug warning to the log handler if not sent.
        """
        if cmd not in (DO, DONT, WILL, WONT):
            raise ValueError("Expected DO, DONT, WILL, WONT, got {0}."
                             .format(name_command(cmd)))

        if cmd == DO and opt not in (TM, LOGOUT):
            if self.remote_option.enabled(opt):
                self.log.debug('skip {} {}; remote_option = True'.format(
                    name_command(cmd), name_command(opt)))
                self.pending_option[cmd + opt] = False
                return False

        if cmd in (DO, WILL):
            if self.pending_option.enabled(cmd + opt):
                self.log.debug('skip {} {}; pending_option = True'.format(
                    name_command(cmd), name_command(opt)))
                return False
            self.pending_option[cmd + opt] = True

        if cmd == WILL and opt not in (TM,):
            if self.local_option.enabled(opt):
                self.log.debug('skip {} {}; local_option = True'.format(
                    name_command(cmd), name_command(opt)))
                self.pending_option[cmd + opt] = False
                return False

        if cmd == DONT and opt not in (LOGOUT,):
            # IAC-DONT-LOGOUT is not a rejection of the negotiation option
            if (opt in self.remote_option and
                    not self.remote_option.enabled(opt)):
                self.log.debug('skip {} {}; remote_option = False'.format(
                    name_command(cmd), name_command(opt)))
                return False
            self.remote_option[opt] = False

        if cmd == WONT:
            self.local_option[opt] = False

        self.log.debug('send IAC {} {}'.format(
            name_command(cmd), name_command(opt)))
        self.send_iac(IAC + cmd + opt)
        return True

# Public methods for transmission signaling
#

    def send_ga(self):
        """
        Transmit IAC GA (Go-Ahead).

        Returns True if sent.  If IAC-DO-SGA has been received, then
        False is returned and IAC-GA is not transmitted.
        """
        if self.local_option.enabled(SGA):
            self.log.debug('cannot send GA with receipt of DO SGA')
            return False

        self.log.debug('send IAC GA')
        self.send_iac(IAC + GA)
        return True

    def send_eor(self):
        """
        Transmit IAC CMD_EOR (End-of-Record), :rfc:`885`.

        Returns True if sent. If IAC-DO-EOR has not been received,
        False is returned and IAC-CMD_EOR is not transmitted.
        """
        if not self.local_option.enabled(EOR):
            self.log.debug('cannot send CMD_EOR without receipt of DO EOR')
            return False

        self.log.debug('send IAC CMD_EOR')
        self.send_iac(IAC + CMD_EOR)
        return True

    # Public methods for notifying about, or soliciting state options.
    #

    def request_status(self):
        """
        Send ``IAC-SB-STATUS-SEND`` sub-negotiation (:rfc:`859`).

        This method may only be called after ``IAC-WILL-STATUS`` has been
        received. Returns True if status request was sent.
        """
        if not self.remote_option.enabled(STATUS):
            self.log.debug('cannot send SB STATUS SEND '
                           'without receipt of WILL STATUS')
        elif not self.pending_option.enabled(SB + STATUS):
            response = [IAC, SB, STATUS, SEND, IAC, SE]
            self.log.debug('send IAC SB STATUS SEND IAC SE')
            self.send_iac(b''.join(response))
            self.pending_option[SB + STATUS] = True
            return True
        else:
            self.log.info('cannot send SB STATUS SEND, request pending.')
        return False

    def request_tspeed(self):
        """
        Send IAC-SB-TSPEED-SEND sub-negotiation, :rfc:`1079`.

        This method may only be called after ``IAC-WILL-TSPEED`` has been
        received. Returns True if TSPEED request was sent.
        """
        if not self.remote_option.enabled(TSPEED):
            self.log.debug('cannot send SB TSPEED SEND '
                           'without receipt of WILL TSPEED')
        elif not self.pending_option.enabled(SB + TSPEED):
            self.pending_option[SB + TSPEED] = True
            response = [IAC, SB, TSPEED, SEND, IAC, SE]
            self.log.debug('send IAC SB TSPEED SEND IAC SE')
            self.send_iac(b''.join(response))
            self.pending_option[SB + TSPEED] = True
            return True
        else:
            self.log.debug('cannot send SB TSPEED SEND, request pending.')
        return False

    def request_charset(self):
        """
        Request sub-negotiation CHARSET, :rfc:`2066`.

        Returns True if request is valid for telnet state, and was sent.

        The sender requests that all text sent to and by it be encoded in
        one of character sets specified by string list ``codepages``, which
        is determined by function value returned by callback registered using
        :meth:`set_ext_send_callback` with value ``CHARSET``.
        """
        if not self.remote_option.enabled(CHARSET):
            self.log.debug('cannot send SB CHARSET REQUEST '
                           'without receipt of WILL CHARSET')
            return False

        if self.pending_option.enabled(SB + CHARSET):
            self.log.debug('cannot send SB CHARSET REQUEST, request pending.')
            return False

        codepages = self._ext_send_callback[CHARSET]()

        sep = ' '
        response = collections.deque()
        response.extend([IAC, SB, CHARSET, REQUEST])
        response.extend([bytes(sep, 'ascii')])
        response.extend([bytes(sep.join(codepages), 'ascii')])
        response.extend([IAC, SE])
        self.log.debug('send IAC SB CHARSET REQUEST {} IAC SE'.format(
            sep.join(codepages)))
        self.send_iac(b''.join(response))
        self.pending_option[SB + CHARSET] = True
        return True

    def request_environ(self):
        """
        Request sub-negotiation NEW_ENVIRON, :rfc:`1572`.

        Returns True if request is valid for telnet state, and was sent.
        """
        assert self.server, 'SB NEW_ENVIRON SEND may only be sent by server'

        if not self.remote_option.enabled(NEW_ENVIRON):
            self.log.debug('cannot send SB NEW_ENVIRON SEND IS '
                           'without receipt of WILL NEW_ENVIRON')
            return False

        request_list = self._ext_send_callback[NEW_ENVIRON]()

        if not request_list:
            self.log.debug('request_environ: server protocol makes no demand, '
                           'no request will be made.')
            return False

        if self.pending_option.enabled(SB + NEW_ENVIRON):
            self.log.debug('cannot send SB NEW_ENVIRON SEND IS, '
                           'request pending.')
            return False

        response = collections.deque()
        response.extend([IAC, SB, NEW_ENVIRON, SEND])

        for env_key in request_list:
            if env_key in (VAR, USERVAR):
                # VAR followed by IAC,SE indicates "send all the variables",
                # whereas USERVAR indicates "send all the user variables".
                # In today's era, there is little distinction between them.
                response.append(env_key)
            else:
                response.extend([VAR])
                response.extend([_escape_environ(env_key.encode('ascii'))])
        response.extend([IAC, SE])
        self.log.debug('request_environ: {!r}'.format(b''.join(response)))
        self.pending_option[SB + NEW_ENVIRON] = True
        self.send_iac(b''.join(response))
        return True

    def request_xdisploc(self):
        """
        Send XDISPLOC, SEND sub-negotiation, :rfc:`1086`.

        Returns True if request is valid for telnet state, and was sent.
        """
        assert self.server, (
            'SB XDISPLOC SEND may only be sent by server end')
        if not self.remote_option.enabled(XDISPLOC):
            self.log.debug('cannot send SB XDISPLOC SEND'
                           'without receipt of WILL XDISPLOC')
        if not self.pending_option.enabled(SB + XDISPLOC):
            response = [IAC, SB, XDISPLOC, SEND, IAC, SE]
            self.log.debug('send IAC SB XDISPLOC SEND IAC SE')
            self.pending_option[SB + XDISPLOC] = True
            self.send_iac(b''.join(response))
            return True

        self.log.debug('cannot send SB XDISPLOC SEND, request pending.')
        return False

    def request_ttype(self):
        """
        Send TTYPE SEND sub-negotiation, :rfc:`930`.

        Returns True if request is valid for telnet state, and was sent.
        """
        assert self.server, (
            'SB TTYPE SEND may only be sent by server end')
        if not self.remote_option.enabled(TTYPE):
            self.log.debug('cannot send SB TTYPE SEND'
                           'without receipt of WILL TTYPE')
        if not self.pending_option.enabled(SB + TTYPE):
            response = [IAC, SB, TTYPE, SEND, IAC, SE]
            self.log.debug('send IAC SB TTYPE SEND IAC SE')
            self.pending_option[SB + TTYPE] = True
            self.send_iac(b''.join(response))
            return True
        else:
            self.log.debug('cannot send SB TTYPE SEND, request pending.')
        return False

    def request_forwardmask(self, fmask=None):
        """
        Request the client forward their terminal control characters.

        Characters are indicated in the :class:`~.Forwardmask` instance
        ``fmask``.  When fmask is None, a forwardmask is generated for the SLC
        characters registered by :attr:`~.slctab`.
        """
        assert self.server, (
            'DO FORWARDMASK may only be sent by server end')
        if not self.remote_option.enabled(LINEMODE):
            self.log.debug('cannot send SB LINEMODE DO'
                           'without receipt of WILL LINEMODE')
        else:
            if fmask is None:
                opt = SB + LINEMODE + slc.LMODE_FORWARDMASK
                forwardmask_enabled = (
                    self.server and self.local_option.get(opt, False)
                ) or self.remote_option.get(opt, False)
                fmask = slc.generate_forwardmask(
                    binary_mode=self.local_option.enabled(BINARY),
                    tabset=self.slctab, ack=forwardmask_enabled)

            assert isinstance(fmask, slc.Forwardmask), fmask

            self.log.debug('send IAC SB LINEMODE DO LMODE_FORWARDMASK::')
            for maskbit_descr in fmask.description_table():
                self.log.debug('  {}'.format(maskbit_descr))
            self.log.debug('send IAC SE')

            self.send_iac(IAC + SB + LINEMODE + DO + slc.LMODE_FORWARDMASK)
            self._transport.write(fmask.value)
            self.send_iac(IAC + SE)

            return True
        return False

    def send_lineflow_mode(self):
        """Send LFLOW mode sub-negotiation, :rfc:`1372`.

        Returns True if request is valid for telnet state, and was sent.
        """
        if self.client:
            self.log.error('only server may send IAC SB LINEFLOW <MODE>')
        elif not self.remote_option.enabled(LFLOW):
            self.log.error('cannot send IAC SB LFLOW '
                           'without receipt of WILL LFLOW')
        else:
            if self.xon_any:
                (mode, desc) = (LFLOW_RESTART_ANY, 'LFLOW_RESTART_ANY')
            else:
                (mode, desc) = (LFLOW_RESTART_XON, 'LFLOW_RESTART_XON')
            self.log.debug('send IAC SB LFLOW {} IAC SE'.format(desc))
            self.send_iac(b''.join([IAC, SB, LFLOW, mode, IAC, SE]))
            return True
        return False

    def send_linemode(self, linemode=None):
        """
        Set and Inform other end to agree to change to linemode, ``linemode``.

        An instance of the Linemode class, or self.linemode when unset.
        """
        if not (self.local_option.enabled(LINEMODE) or
                self.remote_option.enabled(LINEMODE)):
            assert False, ('Cannot send LINEMODE-MODE without first '
                           '(DO, WILL) LINEMODE received.')

        if linemode is not None:
            self.log.debug('set Linemode {0!r}'.format(linemode))
            self._linemode = linemode

        self.log.debug('send IAC SB LINEMODE LINEMODE-MODE {0!r} IAC SE'
                       .format(self._linemode))

        self.send_iac(IAC + SB + LINEMODE + slc.LMODE_MODE)
        self._transport.write(self._linemode.mask)
        self.send_iac(IAC + SE)

# Public is-a-command (IAC) callbacks
#
    def set_iac_callback(self, cmd, func):
        """
        Register callable ``func`` as callback for IAC ``cmd``.

        BRK, IP, AO, AYT, EC, EL, CMD_EOR, EOF, SUSP, ABORT, and NOP.

        These callbacks receive a single argument, the IAC ``cmd`` which
        triggered it.
        """
        assert callable(func), ('Argument func must be callable')
        assert cmd in (BRK, IP, AO, AYT, EC, EL, CMD_EOR, EOF, SUSP,
                       ABORT, NOP, DM, GA, TM), name_command(cmd)
        self._iac_callback[cmd] = func

    def handle_nop(self, cmd):
        """Handle IAC No-Operation (NOP)."""
        self.log.debug('IAC NOP: Null Operation (unhandled).')

    def handle_ga(self, cmd):
        """Handle IAC Go-Ahead (GA)."""
        self.log.debug('IAC GA: Go-Ahead (unhandled).')

    def handle_dm(self, cmd):
        """Handle IAC Data-Mark (DM)."""
        self.log.debug('IAC DM: Data-Mark (unhandled).')

# Public mixed-mode SLC and IAC callbacks
#
    def handle_el(self, byte):
        """
        Handle IAC Erase Line (EL, SLC_EL).

        Provides a function which discards all the data ready on current
        line of input. The prompt should be re-displayed.
        """
        self.log.debug('IAC EL: Erase Line (unhandled).')

    def handle_eor(self, byte):
        """Handle IAC End of Record (CMD_EOR, SLC_EOR)."""
        self.log.debug('IAC EOR: End of Record (unhandled).')

    def handle_abort(self, byte):
        """
        Handle IAC Abort (ABORT, SLC_ABORT).

        Similar to Interrupt Process (IP), but means only to abort or
        terminate the process to which the NVT is connected.
        """
        self.log.debug('IAC ABORT: Abort (unhandled).')

    def handle_eof(self, byte):
        """Handle IAC End of Record (EOF, SLC_EOF)."""
        self.log.debug('IAC EOF: End of File (unhandled).')

    def handle_susp(self, byte):
        """
        Handle IAC Suspend Process (SUSP, SLC_SUSP).

        Suspends the execution of the current process attached to the NVT
        in such a way that another process will take over control of the
        NVT, and the suspended process can be resumed at a later time.

        If the receiving system does not support this functionality, it
        should be ignored.
        """
        self.log.debug('IAC SUSP: Suspend (unhandled).')

    def handle_brk(self, byte):
        """
        Handle IAC Break (BRK, SLC_BRK).

        Sent by clients to indicate BREAK keypress. This is not the same
        as IP (^c), but a means to map sysystem-dependent break key such
        as found on an IBM Systems.
        """
        self.log.debug('IAC BRK: Break (unhandled).')

    def handle_ayt(self, byte):
        """
        Handle IAC Are You There (AYT, SLC_AYT).

        Provides the user with some visible (e.g., printable) evidence
        that the system is still up and running.
        """
        self.log.debug('IAC AYT: Are You There? (unhandled).')

    def handle_ip(self, byte):
        """Handle IAC Interrupt Process (IP, SLC_IP)."""
        self.log.debug('IAC IP: Interrupt Process (unhandled).')

    def handle_ao(self, byte):
        """
        Handle IAC Abort Output (AO) or SLC_AO.

        Discards any remaining output on the transport buffer.

            [...] a reasonable implementation would be to suppress the
            remainder of the text string, but transmit the prompt character
            and the preceding <CR><LF>.
        """
        self.log.debug('IAC AO: Abort Output, unhandled.')

    def handle_ec(self, byte):
        """
        Handle IAC Erase Character (EC, SLC_EC).

        Provides a function which deletes the last preceding undeleted
        character from data ready on current line of input.
        """
        self.log.debug('IAC EC: Erase Character (unhandled).')

    def handle_tm(self, cmd):
        """
        Handle IAC (WILL, WONT, DO, DONT) Timing Mark (TM).

        TM is essentially a NOP that any IAC interpreter must answer, if at
        least it answers WONT to unknown options (required), it may still
        be used as a means to accurately measure the "ping" time.
        """
        self.log.debug('IAC TM: Received {} TM (Timing Mark).'
                       .format(name_command(cmd)))

# public Special Line Mode (SLC) callbacks
#
    def set_slc_callback(self, slc_byte, func):
        """
        Register ``func`` as callable for receipt of ``slc_byte``.

        :param bytes slc_byte: any of SLC_SYNCH, SLC_BRK, SLC_IP, SLC_AO,
            SLC_AYT, SLC_EOR, SLC_ABORT, SLC_EOF, SLC_SUSP, SLC_EC, SLC_EL,
            SLC_EW, SLC_RP, SLC_XON, SLC_XOFF ...
        :param Callable func: These callbacks receive a single argument: the
            SLC function byte that fired it. Some SLC and IAC functions are
            intermixed; which signaling mechanism used by client can be tested
            by evaluating this argument.
        """
        assert callable(func), ('Argument func must be callable')
        assert (type(slc_byte) == bytes and
                0 < ord(slc_byte) < slc.NSLC + 1
                ), ('Uknown SLC byte: {!r}'.format(slc_byte))
        self._slc_callback[slc_byte] = func

    def handle_ew(self, slc):
        """
        Handle SLC_EW (Erase Word).

        Provides a function which deletes the last preceding undeleted
        character, and any subsequent bytes until next whitespace character
        from data ready on current line of input.
        """
        self.log.debug('SLC EC: Erase Word (unhandled).')

    def handle_rp(self, slc):
        """Handle SLC Repaint (RP)."""
        self.log.debug('SLC RP: Repaint (unhandled).')

    def handle_lnext(self, slc):
        """Handle SLC Literal Next (LNEXT) (Next character is received raw)."""
        self.log.debug('SLC LNEXT: Literal Next (unhandled)')

    def handle_xon(self, byte):
        """Handle SLC Transmit-On (XON)."""
        self.log.debug('SLC XON: Transmit On (unhandled).')

    def handle_xoff(self, byte):
        """Handle SLC Transmit-Off (XOFF)."""
        self.log.debug('SLC XOFF: Transmit Off.')

# public Telnet extension callbacks
#
    def set_ext_send_callback(self, cmd, func):
        """
        Register callback for inquires of sub-negotiation of ``cmd``.

        :param Callable func: A callable function for the given ``cmd`` byte.
            Note that the return type must match those documented.
        :param bytes cmd: These callbacks must return any number of arguments,
            for each registered ``cmd`` byte, respectively:

            * SNDLOC: for clients, returning one argument: the string
              describing client location, such as ``b'ROOM 641-A'``,
              :rfc:`779`.

            * NAWS: for clients, returning two integer arguments (width,
              height), such as (80, 24), :rfc:`1073`.

            * TSPEED: for clients, returning two integer arguments (rx, tx)
              such as (57600, 57600), :rfc:`1079`.

            * TTYPE: for clients, returning one string, usually the terminfo(5)
              database capability name, such as 'xterm', :rfc:`1091`.

            * XDISPLOC: for clients, returning one string, the DISPLAY host
              value, in form of <host>:<dispnum>[.<screennum>], :rfc:`1096`.

            * NEW_ENVIRON: for clients, returning a dictionary of (key, val)
              pairs of environment item values, :rfc:`1408`.

            * CHARSET: for clients, receiving iterable of strings of character
              sets requested by server, callback must return one of those
              strings given, :rfc:`2066`.
        """
        assert cmd in (SNDLOC, NAWS, TSPEED, TTYPE, XDISPLOC,
                       NEW_ENVIRON, CHARSET), cmd
        assert callable(func), ('Argument func must be callable')
        self._ext_send_callback[cmd] = func

    def set_ext_callback(self, cmd, func):
        """
        Register ``func`` as callback for receipt of ``cmd`` negotiation.

        :param bytes cmd: One of the following listed bytes:

        * ``LOGOUT``: for servers and clients, receiving one argument.
          Server end may receive DO or DONT as argument ``cmd``, indicating
          client's wish to disconnect, or a response to WILL, LOGOUT,
          indicating it's wish not to be automatically disconnected.  Client
          end may receive WILL or WONT, indicating server's wish to disconnect,
          or acknowledgment that the client will not be disconnected.

        * ``SNDLOC``: for servers, receiving one argument: the string
          describing the client location, such as ``'ROOM 641-A'``, :rfc:`779`.

        * ``NAWS``: for servers, receiving two integer arguments (width,
          height), such as (80, 24), :rfc:`1073`.

        * ``TSPEED``: for servers, receiving two integer arguments (rx, tx)
          such as (57600, 57600), :rfc:`1079`.

        * ``TTYPE``: for servers, receiving one string, usually the
          terminfo(5) database capability name, such as 'xterm', :rfc:`1091`.

        * ``XDISPLOC``: for servers, receiving one string, the DISPLAY
          host value, in form of ``<host>:<dispnum>[.<screennum>]``,
          :rfc:`1096`.

        * ``NEW_ENVIRON``: for servers, receiving a dictionary of
          ``(key, val)`` pairs of remote client environment item values,
          :rfc:`1408`.

        * ``CHARSET``: for servers, receiving one string, the character set
          negotiated by client. :rfc:`2066`.
        """
        assert cmd in (LOGOUT, SNDLOC, NAWS, TSPEED, TTYPE,
                       XDISPLOC, NEW_ENVIRON, CHARSET), cmd
        assert callable(func), ('Argument func must be callable')
        self._ext_callback[cmd] = func

    def handle_xdisploc(self, xdisploc):
        """Receive XDISPLAY value ``xdisploc``, :rfc:`1096`."""
        #   xdisploc string format is '<host>:<dispnum>[.<screennum>]'.
        self.log.debug('X Display is {}'.format(xdisploc))

    def handle_send_xdisploc(self):
        """Send XDISPLAY value ``xdisploc``, :rfc:`1096`."""
        #   xdisploc string format is '<host>:<dispnum>[.<screennum>]'.
        self.log.warning('X Display requested, sending empty string.')
        return ''

    def handle_sndloc(self, location):
        """Receive LOCATION value ``location``, :rfc:`779`."""
        self.log.debug('Location is {}'.format(location))

    def handle_send_sndloc(self):
        """Send LOCATION value ``location``, :rfc:`779`."""
        self.log.warning('Location requested, sending empty response.')
        return ''

    def handle_ttype(self, ttype):
        """
        Receive TTYPE value ``ttype``, :rfc:`1091`.

        A string value that represents client's emulation capability.

        Some example values: VT220, VT100, ANSITERM, ANSI, TTY, and 5250.
        """
        self.log.debug('Terminal type is {!r}'.format(ttype))

    def handle_send_ttype(self):
        """Send TTYPE value ``ttype``, :rfc:`1091`."""
        self.log.warning('Terminal type requested, sending empty string.')
        return ''

    def handle_naws(self, width, height):
        """Receive window size ``width`` and ``height``, :rfc:`1073`."""
        self.log.debug('Terminal cols={}, rows={}'.format(width, height))

    def handle_send_naws(self):
        """Send window size ``width`` and ``height``, :rfc:`1073`."""
        self.log.warning('Terminal size requested, sending 80x24.')
        return 80, 24

    def handle_environ(self, env):
        """Receive environment variables as dict, :rfc:`1572`."""
        self.log.debug('Environment values are {!r}'.format(env))

    def handle_send_client_environ(self, keys):
        """
        Send environment variables as dict, :rfc:`1572`.

        If argument ``keys`` is empty, then all available values should be
        sent. Otherwise, ``keys`` is a set of environment keys explicitly
        requested.
        """
        self.log.debug('Environment values requested, sending {{}}.')
        return dict()

    def handle_send_server_environ(self):
        """Server requests environment variables as list, :rfc:`1572`."""
        self.log.debug('Environment values offered, requesting [].')
        return []

    def handle_tspeed(self, rx, tx):
        """Receive terminal speed from TSPEED as int, :rfc:`1079`."""
        self.log.debug('Terminal Speed rx:{}, tx:{}'.format(rx, tx))

    def handle_send_tspeed(self):
        """Send terminal speed from TSPEED as int, :rfc:`1079`."""
        self.log.debug('Terminal Speed requested, sending 9600,9600.')
        return 9600, 9600

    def handle_charset(self, charset):
        """Receive character set as string, :rfc:`2066`."""
        self.log.debug('Character set: {}'.format(charset))

    def handle_send_client_charset(self, charsets):
        """
        Send character set selection as string, :rfc:`2066`.

        Given the available encodings presented by the server, select and
        return only one.  Returning an empty string indicates that no
        selection is made (request is ignored).
        """
        assert not self.server
        self.log.debug('Character Set requested')
        return ''

    def handle_send_server_charset(self, charsets):
        """Send character set (encodings) offered to client, :rfc:`2066`."""
        assert self.server
        return ['UTF-8']

    def handle_logout(self, cmd):
        """
        Handle (IAC, (DO | DONT | WILL | WONT), LOGOUT), :rfc:`727`.

        Only the server end may receive (DO, DONT).
        Only the client end may receive (WILL, WONT).
        """
        # Close the transport on receipt of DO, Reply DONT on receipt
        # of WILL.  Nothing is done on receipt of DONT or WONT LOGOFF.
        if cmd == DO:
            assert self.server, (cmd, LOGOUT)
            self.log.debug('client requests DO LOGOUT')
            self._transport.close()
        elif cmd == DONT:
            assert self.server, (cmd, LOGOUT)
            self.log.debug('client requests DONT LOGOUT')
        elif cmd == WILL:
            assert self.client, (cmd, LOGOUT)
            self.log.debug('recv WILL TIMEOUT (timeout warning)')
            self.log.debug('send IAC DONT LOGOUT')
            self.iac(DONT, LOGOUT)
        elif cmd == WONT:
            assert self.client, (cmd, LOGOUT)
            self.log.debug('recv IAC WONT LOGOUT (server refuses logout')

# public derivable methods DO, DONT, WILL, and WONT negotiation
#
    def handle_do(self, opt):
        """
        Process byte 3 of series (IAC, DO, opt) received by remote end.

        This method can be derived to change or extend protocol capabilities,
        for most cases, simply returning True if supported, False otherwise.

        In special cases of various RFC statutes, state is stored and
        answered in willing affirmative, with the exception of:

        - DO TM is *always* answered WILL TM, even if it was already
          replied to.  No state is stored ("Timing Mark"), and the IAC
          callback registered by :meth:`set_ext_callback` for cmd TM
          is called with argument byte ``DO``.
        - DO LOGOUT executes extended callback registered by cmd LOGOUT
          with argument DO (indicating a request for voluntary logoff).
        - DO STATUS sends state of all local, remote, and pending options.
        """
        # For unsupported capabilities, RFC specifies a response of
        # (IAC, WONT, opt).  Similarly, set ``self.local_option[opt]``
        # to ``False``.
        #
        # This method returns True if the opt enables the willingness of the
        # remote end to accept a telnet capability, such as NAWS. It returns
        # False for unsupported option, or an option invalid in that context,
        # such as LOGOUT.
        self.log.debug('handle_do({})'.format(name_command(opt)))
        if opt == ECHO and self.client:
            # What do we have here? A Telnet Server attempting to
            # fingerprint us as a broken 4.4BSD Telnet Client, which
            # would respond 'WILL ECHO'.  Let us just reply WONT--some
            # servers, such as dgamelaunch (nethack.alt.org) freeze up
            # unless we answer IAC-WONT-ECHO.
            self.iac(WONT, ECHO)
        elif self.server and opt in (LINEMODE, TTYPE, NAWS,
                                     NEW_ENVIRON, XDISPLOC, LFLOW):
            raise ValueError('cannot recv DO {0} on server end (ignored).'
                             .format(name_command(opt)))
        elif self.client and opt in (LOGOUT,):
            raise ValueError('cannot recv DO {0} on client end (ignored).'
                             .format(name_command(opt)))
        elif opt == TM:
            # timing mark is special: simply by replying, the effect
            # is accomplished ('will' or 'wont' is non-consequential):
            # the distant end is able to "time" our response. More
            # importantly, ensure that the IAC interpreter is, in fact,
            # interpreting, and, that all IAC commands up to this point
            # have been processed.
            self.iac(WILL, TM)
            self._iac_callback[TM](DO)

        elif opt == LOGOUT:
            self._ext_callback[LOGOUT](DO)

        elif opt in (ECHO, LINEMODE, BINARY, SGA, LFLOW, EOR, TTYPE,
                     NEW_ENVIRON, XDISPLOC, TSPEED, CHARSET, NAWS, STATUS):

            # first time we've agreed, respond accordingly.
            if not self.local_option.enabled(opt):
                self.iac(WILL, opt)

            # and respond with status for some,
            if opt == NAWS:
                self._send_naws()
            elif opt == STATUS:
                self._send_status()

            # and expect a follow-up sub-negotiation for these others.
            elif opt in (LFLOW, TTYPE, NEW_ENVIRON, XDISPLOC,
                         TSPEED, CHARSET, LINEMODE):
                self.pending_option[SB + opt] = True

        else:
            self.log.debug('DO {0} not supported.'.format(name_command(opt)))
            if self.local_option.get(opt, None) is None:
                self.iac(WONT, opt)
            return False
        return True

    def handle_dont(self, opt):
        """
        Process byte 3 of series (IAC, DONT, opt) received by remote end.

        This only results in ``self.local_option[opt]`` set to ``False``, with
        the exception of (IAC, DONT, LOGOUT), which only signals a callback
        to ``handle_logout(DONT)``.
        """
        self.log.debug('handle_dont({})'.format(name_command(opt)))
        if opt == LOGOUT:
            assert self.server, ('cannot recv DONT LOGOUT on server end')
            self._ext_callback[LOGOUT](DONT)
        # many implementations (wrongly!) sent a WONT in reply to DONT. It
        # sounds reasonable, but it can and will cause telnet loops. (ruby?)
        # Correctly, a DONT can not be declined, so there is no need to
        # affirm in the negative.

    def handle_will(self, opt):
        """
        Process byte 3 of series (IAC, DONT, opt) received by remote end.

        The remote end requests we perform any number of capabilities. Most
        implementations require an answer in the affirmative with DO, unless
        DO has meaning specific for only client or server end, and
        dissenting with DONT.

        WILL ECHO may only be received *for clients*, answered with DO.
        WILL NAWS may only be received *for servers*, answered with DO.
        BINARY and SGA are answered with DO.  STATUS, NEW_ENVIRON, XDISPLOC,
        and TTYPE is answered with sub-negotiation SEND. The env variables
        requested in response to WILL NEW_ENVIRON is "SEND ANY".
        All others are replied with DONT.

        The result of a supported capability is a response of (IAC, DO, opt)
        and the setting of ``self.remote_option[opt]`` of ``True``. For
        unsupported capabilities, RFC specifies a response of (IAC, DONT, opt).
        Similarly, set ``self.remote_option[opt]`` to ``False``.
        """
        self.log.debug('handle_will({})'.format(name_command(opt)))

        if opt in (BINARY, SGA, ECHO, NAWS, LINEMODE, EOR, SNDLOC):
            if opt == ECHO and self.server:
                raise ValueError('cannot recv WILL ECHO on server end')
            elif opt in (NAWS, LINEMODE, SNDLOC) and self.client:
                raise ValueError('cannot recv WILL {} on client end'
                                 .format(name_command(opt),))
            if not self.remote_option.enabled(opt):
                self.iac(DO, opt)
                self.remote_option[opt] = True
            if opt in (NAWS, LINEMODE, SNDLOC):
                # expect to receive some sort of follow-up subnegotiation
                self.pending_option[SB + opt] = True
                if opt == LINEMODE:
                    # server sets the initial mode and sends forwardmask,
                    self.send_linemode(self.default_linemode)

        elif opt == TM:
            if opt == TM and not self.pending_option.enabled(DO + TM):
                raise ValueError('cannot recv WILL TM, must first send DO TM.')
            self._iac_callback[TM](WILL)
            self.remote_option[opt] = True

        elif opt == LOGOUT:
            if self.client:
                raise ValueError('cannot recv WILL LOGOUT on server end')
            self._ext_callback[LOGOUT](WILL)

        elif opt == STATUS:
            # Though unnecessary, if the other end claims support for STATUS,
            # we put them to the test by requesting their status.
            self.remote_option[opt] = True
            self.request_status()

        elif opt in (XDISPLOC, TTYPE, TSPEED, NEW_ENVIRON, LFLOW, CHARSET):
            # CHARSET is bi-directional: "WILL CHARSET indicates the sender
            # REQUESTS permission to, or AGREES to, use CHARSET option
            # sub-negotiation to choose a character set."; however, the
            # selected encoding is, regarding SB CHARSET REQUEST, "The sender
            # requests that all text sent to and by it be encoded in one of the
            # specified character sets. "
            #
            # Though Others -- XDISPLOC, TTYPE, TSPEED, are 1-directional.
            if not self.server and opt not in (CHARSET,):
                raise ValueError('cannot recv WILL {} on client end.'
                                 .format(name_command(opt)))
            self.remote_option[opt] = True

            # call one of the following callbacks.
            {
                XDISPLOC: self.request_xdisploc,
                TTYPE: self.request_ttype,
                TSPEED: self.request_tspeed,
                CHARSET: self.request_charset,
                NEW_ENVIRON: self.request_environ,
                LFLOW: self.send_lineflow_mode,
            }[opt]()

        else:
            # option value of -1 toggles opt.unsupported()
            self.iac(DONT, opt)
            self.remote_option[opt] = -1
            self.log.warning('Unhandled: WILL {}.'.format(name_command(opt),))
            self.local_option[opt] = -1
            if self.pending_option.enabled(DO + opt):
                self.pending_option[DO + opt] = False

    def handle_wont(self, opt):
        """
        Process byte 3 of series (IAC, WONT, opt) received by remote end.

        (IAC, WONT, opt) is a negative acknowledgment of (IAC, DO, opt) sent.

        The remote end requests we do not perform a telnet capability.

        It is not possible to decline a WONT. ``T.remote_option[opt]`` is set
        False to indicate the remote end's refusal to perform ``opt``.
        """
        self.log.debug('handle_wont({})'.format(name_command(opt)))
        if opt == TM and not self.pending_option.enabled(DO + TM):
            raise ValueError('WONT TM received but DO TM was not sent')
        elif opt == TM:
            self.log.debug('WONT TIMING-MARK')
            self.remote_option[opt] = False
        elif opt == LOGOUT:
            assert not (self.server), (
                'cannot recv WONT LOGOUT on server end')
            if not self.pending_option.enabled(DO + LOGOUT):
                self.log.warning('Server sent WONT LOGOUT unsolicited')
            self._ext_callback[LOGOUT](WONT)
        else:
            self.remote_option[opt] = False

# public derivable Sub-Negotation parsing
#
    def handle_subnegotiation(self, buf):
        """
        Callback for end of sub-negotiation buffer.

            SB options handled here are TTYPE, XDISPLOC, NEW_ENVIRON,
            NAWS, and STATUS, and are delegated to their ``handle_``
            equivalent methods. Implementors of additional SB options
            should extend this method.
        """
        if not buf:
            raise ValueError('SE: buffer empty')
        if buf[0] == theNULL:
            raise ValueError('SE: buffer is NUL')
        if len(buf) == 1:
            raise ValueError('SE: buffer too short: {!r}'.format(buf))

        cmd = buf[0]
        if self.pending_option.enabled(SB + cmd):
            self.pending_option[SB + cmd] = False
        else:
            self.log.debug('[SB + {}] unsolicited'.format(name_command(cmd)))

        fn_call = {LINEMODE: self._handle_sb_linemode,
                   LFLOW: self._handle_sb_lflow,
                   NAWS: self._handle_sb_naws,
                   SNDLOC: self._handle_sb_sndloc,
                   NEW_ENVIRON: self._handle_sb_environ,
                   CHARSET: self._handle_sb_charset,
                   TTYPE: self._handle_sb_ttype,
                   TSPEED: self._handle_sb_tspeed,
                   XDISPLOC: self._handle_sb_xdisploc,
                   STATUS: self._handle_sb_status
                   }.get(cmd)
        if fn_call is None:
            raise ValueError('SB unhandled: cmd={}, buf={!r}'
                             .format(name_command(cmd), buf))

        fn_call(buf)

    # Our Private API methods

    @staticmethod
    def _escape_iac(buf):
        r"""Replace bytes in buf ``IAC`` (``b'\xff'``) by ``IAC IAC``."""
        return buf.replace(IAC, IAC + IAC)

    def _write(self, buf, escape_iac=True):
        """
        Write bytes to transport, conditionally escaping IAC.

        :param bytes buf: bytes to write to transport.
        :param bool escape_iac: whether bytes in buffer ``buf`` should be
            escape bytes ``IAC``.  This should be set ``False`` for direct
            writes of ``IAC`` commands.
        """
        if not isinstance(buf, (bytes, bytearray)):
            raise TypeError("buf expected bytes, got {0}".format(type(buf)))

        if escape_iac:
            # when escape_iac is True, we may safely assume downstream
            # application has provided an encoded string.  If force_binary
            # is unset, we enforce strict adherence of BINARY protocol
            # negotiation.
            if (not self._protocol.force_binary and not self.outbinary):
                # check each byte position by index to report location
                for position, byte in enumerate(buf):
                    if byte >= 128:
                        raise TypeError(
                            'Byte value {0!r} at index {1} not valid, '
                            'send IAC WILL BINARY first: buf={2!r}'.format(
                                byte, position, buf))
            buf = self._escape_iac(buf)

        self._transport.write(buf)

    # Private sub-negotiation (SB) routines

    def _handle_sb_charset(self, buf):
        cmd = buf.popleft()
        assert cmd == CHARSET
        opt = buf.popleft()
        if opt == REQUEST:
            # "<Sep>  is a separator octet, the value of which is chosen by the
            # sender.  Examples include a space or a semicolon."
            sep = buf.popleft()
            # decode any offered character sets (b'CHAR-SET')
            # to a python-normalized unicode string ('charset').
            offers = [charset.decode('ascii')
                      for charset in b''.join(buf).split(sep)]
            selected = self._ext_send_callback[CHARSET](offers)
            if selected is None:
                self.log.debug('send IAC SB CHARSET REJECTED IAC SE')
                self.send_iac(IAC + SB + CHARSET + REJECTED + IAC + SE)
            else:
                response = collections.deque()
                response.extend([IAC, SB, CHARSET, ACCEPTED])
                response.extend([bytes(selected, 'ascii')])
                response.extend([IAC, SE])
                self.log.debug('send IAC SB CHARSET ACCEPTED {} IAC SE'
                               .format(selected))
                self.send_iac(b''.join(response))
        elif opt == ACCEPTED:
            charset = b''.join(buf).decode('ascii')
            self.log.debug('recv IAC SB CHARSET ACCEPTED {} IAC SE'
                           .format(charset))
            self._ext_callback[CHARSET](charset)
        elif opt == REJECTED:
            self.log.warning('recv IAC SB CHARSET REJECTED IAC SE')
        elif opt in (TTABLE_IS, TTABLE_ACK, TTABLE_NAK, TTABLE_REJECTED):
            raise NotImplementedError('Translation table command received '
                                      'but not supported: {!r}'.format(opt))
        else:
            raise ValueError('Illegal option follows IAC SB CHARSET: {!r}.'
                             .format(opt))

    def _handle_sb_tspeed(self, buf):
        """Callback handles IAC-SB-TSPEED-<buf>-SE."""
        cmd = buf.popleft()
        opt = buf.popleft()
        assert cmd == TSPEED, (cmd, name_command(cmd))
        assert opt in (IS, SEND), opt
        opt_kind = {IS: 'IS', SEND: 'SEND'}.get(opt)
        self.log.debug('recv {} {}: {!r}'.format(
            name_command(cmd), opt_kind, b''.join(buf),))

        if opt == IS:
            assert self.server, ('SE: cannot recv from server: {} {}'
                                 .format(name_command(cmd), opt_kind,))
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
            self.log.debug('sb_tspeed: {}, {}'.format(rx, tx))
            try:
                rx, tx = int(rx), int(tx)
            except ValueError as err:
                self.log.error('illegal TSPEED values received '
                               '(rx={!r}, tx={!r}: {}', rx, tx, err)
                return
            self._ext_callback[TSPEED](rx, tx)
        elif opt == SEND:
            assert self.client, ('SE: cannot recv from client: {} {}'
                                 .format(name_command(cmd), opt_kind,))
            (rx, tx) = self._ext_send_callback[TSPEED]()
            assert (type(rx), type(tx),) == (int, int), (rx, tx)
            brx = '{}'.format(rx).encode('ascii')
            btx = '{}'.format(tx).encode('ascii')
            response = [IAC, SB, TSPEED, IS, brx, b',', btx, IAC, SE]
            self.log.debug('send: IAC SB TSPEED IS {0!r},{1!r} IAC SE'
                           .format(brx, btx))
            self.send_iac(b''.join(response))
            if self.pending_option.enabled(WILL + TSPEED):
                self.pending_option[WILL + TSPEED] = False

    def _handle_sb_xdisploc(self, buf):
        """Callback handles IAC-SB-XIDISPLOC-<buf>-SE."""
        cmd = buf.popleft()
        opt = buf.popleft()

        assert cmd == XDISPLOC, (cmd, name_command(cmd))
        assert opt in (IS, SEND), opt
        opt_kind = {IS: 'IS', SEND: 'SEND'}.get(opt)
        self.log.debug('recv {} {}: {!r}'.format(
            name_command(cmd), opt_kind, b''.join(buf),))

        if opt == IS:
            assert self.server, ('SE: cannot recv from server: {} {}'
                                 .format(name_command(cmd), opt,))
            xdisploc_str = b''.join(buf).decode('ascii')
            self.log.debug('recv IAC SB XDISPLOC IS {0!r} IAC SE'
                           .format(xdisploc_str))
            self._ext_callback[XDISPLOC](xdisploc_str)
        elif opt == SEND:
            assert self.client, ('SE: cannot recv from client: {} {}'
                                 .format(name_command(cmd), opt,))
            xdisploc_str = self._ext_send_callback[XDISPLOC]().encode('ascii')
            response = [IAC, SB, XDISPLOC, IS, xdisploc_str, IAC, SE]
            self.log.debug('send IAC SB XDISPLOC IS {0!r} IAC SE'
                           .format(xdisploc_str))
            self.send_iac(b''.join(response))
            if self.pending_option.enabled(WILL + XDISPLOC):
                self.pending_option[WILL + XDISPLOC] = False

    def _handle_sb_ttype(self, buf):
        """Callback handles IAC-SB-TTYPE-<buf>-SE."""
        cmd = buf.popleft()
        opt = buf.popleft()

        assert cmd == TTYPE, name_command(cmd)
        assert opt in (IS, SEND), opt
        opt_kind = {IS: 'IS', SEND: 'SEND'}.get(opt)
        self.log.debug('recv {} {}: {!r}'.format(
            name_command(cmd), opt_kind, b''.join(buf),))

        if opt == IS:
            assert self.server, ('SE: cannot recv from server: {} {}'
                                 .format(name_command(cmd), opt,))
            ttype_str = b''.join(buf).decode('ascii')
            self.log.debug('recv IAC SB TTYPE IS {0!r}'
                           .format(ttype_str))
            self._ext_callback[TTYPE](ttype_str)
        elif opt == SEND:
            assert self.client, ('SE: cannot recv from client: {} {}'
                                 .format(name_command(cmd), opt,))
            ttype_str = self._ext_send_callback[TTYPE]().encode('ascii')
            response = [IAC, SB, TTYPE, IS, ttype_str, IAC, SE]
            self.log.debug('send IAC SB TTYPE IS {0!r} IAC SE'
                           .format(ttype_str))
            self.send_iac(b''.join(response))
            if self.pending_option.enabled(WILL + TTYPE):
                self.pending_option[WILL + TTYPE] = False

    def _handle_sb_environ(self, buf):
        """
        Callback handles (IAC, SB, NEW_ENVIRON, <buf>, SE), :rfc:`1572`.

        For requests beginning with IS, or subsequent requests beginning
        with INFO, any callback registered by :meth:`set_ext_callback` of
        cmd NEW_ENVIRON is passed a dictionary of (key, value) replied-to
        by client.

        For requests beginning with SEND, the callback registered by
        ``set_ext_send_callback`` is provided with a list of keys
        requested from the server; or None if only VAR and/or USERVAR
        is requested, indicating to "send them all".
        """
        cmd = buf.popleft()
        opt = buf.popleft()

        assert cmd == NEW_ENVIRON, (cmd, name_command(cmd))
        assert opt in (IS, SEND, INFO), opt
        opt_kind = {IS: 'IS', INFO: 'INFO', SEND: 'SEND'}.get(opt)
        self.log.debug('recv {} {}: {!r}'.format(
            name_command(cmd), opt_kind, b''.join(buf),))

        env = _decode_env_buf(b''.join(buf))

        if opt in (IS, INFO):
            assert self.server, ('SE: cannot recv from server: {} {}'
                                 .format(name_command(cmd), opt_kind,))
            if opt == IS:
                if not self.pending_option.enabled(SB + cmd):
                    self.log.debug('{} {} unsolicited'
                                   .format(name_command(cmd), opt_kind))
                self.pending_option[SB + cmd] = False
            elif (self.pending_option.get(SB + cmd, None)
                    is False):
                # a pending option of value of 'False' means it was previously
                # completed, subsequent environment values *should* have been
                # sent as command INFO ...
                self.log.warning('{} IS already recv; expected INFO.'
                                 .format(name_command(cmd)))
            if env:
                self._ext_callback[cmd](env)
        elif opt == SEND:
            assert self.client, ('SE: cannot recv from client: {} {}'
                                 .format(name_command(cmd), opt_kind))
            # client-side, we do _not_ honor the 'send all VAR' or 'send all
            # USERVAR' requests -- it is a small bit of a security issue.
            send_env = _encode_env_buf(
                self._ext_send_callback[NEW_ENVIRON](env.keys()))
            response = [IAC, SB, NEW_ENVIRON, IS, send_env, IAC, SE]
            self.log.debug('env send: {!r}'.format(response))
            self.send_iac(b''.join(response))
            if self.pending_option.enabled(WILL + TTYPE):
                self.pending_option[WILL + TTYPE] = False

    def _handle_sb_sndloc(self, buf):
        """Fire callback for IAC-SB-SNDLOC-<buf>-SE (:rfc:`779`)."""
        assert buf.popleft() == SNDLOC
        location_str = b''.join(buf).decode('ascii')
        self._ext_callback[SNDLOC](location_str)

    def _send_naws(self):
        """Fire callback for IAC-DO-NAWS from server."""
        # Similar to the callback method order fired by _handle_sb_naws(),
        # we expect our parameters in order of (rows, cols), matching the
        # termios.TIOCGWINSZ and terminfo(5) cup capability order.
        rows, cols = self._ext_send_callback[NAWS]()

        # NAWS limits columns and rows to a size of 0-65535 (unsigned short).
        #
        # >>> struct.unpack('!HH', b'\xff\xff\xff\xff')
        # (65535, 65535).
        rows, cols = max(min(65535, rows), 0), max(min(65535, cols), 0)

        # NAWS is sent in (col, row) order:
        #
        #    IAC SB NAWS WIDTH[1] WIDTH[0] HEIGHT[1] HEIGHT[0] IAC SE
        #
        value = self._escape_iac(struct.pack('!HH', cols, rows))
        response = [IAC, SB, NAWS, value, IAC, SE]
        self.log.debug('send IAC SB NAWS (rows={0}, cols={1}) IAC SE'
                       .format(rows, cols))
        self.send_iac(b''.join(response))

    def _handle_sb_naws(self, buf):
        """Fire callback for IAC-SB-NAWS-<cols_rows[4]>-SE (:rfc:`1073`)."""
        cmd = buf.popleft()
        assert cmd == NAWS, name_command(cmd)
        assert len(buf) == 4, (
            'bad NAWS length {}: {!r}'.format(len(buf), buf)
        )
        assert self.remote_option.enabled(NAWS), (
            'received IAC SB NAWS without receipt of IAC WILL NAWS')
        # note a similar formula:
        #
        #    cols, rows = ((256 * buf[0]) + buf[1],
        #                  (256 * buf[2]) + buf[3])
        cols, rows = struct.unpack('!HH', b''.join(buf))
        self.log.debug('recv IAC SB NAWS (cols={0}, rows={1}) IAC SE'
                       .format(cols, rows))

        # Flip the bytestream order (cols, rows) -> (rows, cols).
        #
        # This is for good reason: it matches the termios.TIOCGWINSZ
        # structure, which also matches the terminfo(5) capability, 'cup'.
        self._ext_callback[NAWS](rows, cols)

    def _handle_sb_lflow(self, buf):
        """Callback responds to IAC SB LFLOW, :rfc:`1372`."""
        buf.popleft()  # LFLOW
        if not self.local_option.enabled(LFLOW):
            raise ValueError('received IAC SB LFLOW without '
                             'first receiving IAC DO LFLOW.')
        opt = buf.popleft()
        if opt in (LFLOW_OFF, LFLOW_ON):
            self.lflow = opt is LFLOW_ON
            self.log.debug('LFLOW (toggle-flow-control) {}'.format(
                'ON' if self.lflow else 'OFF'))

        elif opt in (LFLOW_RESTART_ANY, LFLOW_RESTART_XON):
            self.xon_any = opt is LFLOW_RESTART_XON
            self.log.debug('LFLOW (toggle-flow-control) {}'.format(
                'RESTART_ANY' if self.xon_any else 'RESTART_XON'))

        else:
            raise ValueError(
                'Unknown IAC SB LFLOW option received: {!r}'.format(buf))

    def _handle_sb_status(self, buf):
        """
        Callback responds to IAC SB STATUS, :rfc:`859`.

        This method simply delegates to either of :meth:`_receive_status`
        or :meth:`_send_status`.
        """
        buf.popleft()
        opt = buf.popleft()
        if opt == SEND:
            self._send_status()
        elif opt == IS:
            self._receive_status(buf)
        else:
            raise ValueError('Illegal byte following IAC SB STATUS: {!r}, '
                             'expected SEND or IS.'.format(opt))

    def _receive_status(self, buf):
        """
        Callback responds to IAC SB STATUS IS, :rfc:`859`.

        :param bytes buf: sub-negotiation byte buffer containing status data.

        This implementation does its best to analyze our perspective's state
        to the state options given.  Any discrepancies are reported to the
        error log, but no action is taken.
        """
        for pos in range(len(buf) // 2):
            cmd = buf.popleft()
            try:
                opt = buf.popleft()
            except IndexError:
                # a remainder in division step-by-two, presumed nonsense.
                raise ValueError('STATUS incomplete at pos {}, cmd: {}'
                                 .format(pos, name_command(cmd)))

            matching = False
            if cmd not in (DO, DONT, WILL, WONT):
                raise ValueError('STATUS invalid cmd at pos {}: {}, '
                                 'expected DO DONT WILL WONT.'
                                 .format(pos, cmd))

            if cmd in (DO, DONT):
                _side = 'local'
                enabled = self.local_option.enabled(opt)
                matching = ((cmd == DO and enabled) or
                            (cmd == DONT and not enabled))
            else:  # (WILL, WONT)
                _side = 'remote'
                enabled = self.remote_option.enabled(opt)
                matching = ((cmd == WILL and enabled) or
                            (cmd == WONT and not enabled))
            _mode = 'enabled' if enabled else 'not enabled'

            if not matching:
                self.log.error('STATUS {cmd} {opt}: disagreed, '
                               '{side} option is {mode}.'.format(
                                   cmd=name_command(cmd),
                                   opt=name_command(opt),
                                   side=_side, mode=_mode))
                self.log.error('remote {!r} is {}'.format(
                    [(name_commands(_opt), _val)
                     for _opt, _val in self.remote_option.items()],
                    self.remote_option.enabled(opt)))
                self.log.error(' local {!r} is {}'.format(
                    [(name_commands(_opt), _val)
                     for _opt, _val in self.local_option.items()],
                    self.local_option.enabled(opt)))
                continue
            self.log.debug('STATUS {} {} (agreed).'.format(name_command(cmd),
                                                           name_command(opt)))

    def _send_status(self):
        """Callback responds to IAC SB STATUS SEND, :rfc:`859`."""
        if not (self.pending_option.enabled(WILL + STATUS) or
                self.local_option.enabled(STATUS)):
            raise ValueError('Only sender of IAC WILL STATUS '
                             'may reply by IAC SB STATUS IS.')

        response = collections.deque()
        response.extend([IAC, SB, STATUS, IS])
        for opt, status in self.local_option.items():
            # status is 'WILL' for local option states that are True,
            # and 'WONT' for options that are False.
            if opt == STATUS:
                continue
            response.extend([WILL if status else WONT, opt])
        for opt, status in self.remote_option.items():
            # status is 'DO' for remote option states that are True,
            # or for any DO option requests pending reply. status is
            # 'DONT' for any remote option states that are False,
            # or for any DONT option requests pending reply.
            if opt == STATUS:
                continue
            if status or DO + opt in self.pending_option:
                response.extend([DO, opt])
            elif not status or DONT + opt in self.pending_option:
                response.extend([DONT, opt])
        response.extend([IAC, SE])
        self.log.debug('send IAC SB STATUS IS {} IAC SE'.format(' '.join([
            name_command(byte) for byte in list(response)[4:-2]])))
        self.send_iac(b''.join(response))
        if self.pending_option.enabled(WILL + STATUS):
            self.pending_option[WILL + STATUS] = False

# Special Line Character and other LINEMODE functions.
#
    def _handle_sb_linemode(self, buf):
        """Callback responds to bytes following IAC SB LINEMODE."""
        buf.popleft()
        opt = buf.popleft()
        if opt == slc.LMODE_MODE:
            self._handle_sb_linemode_mode(buf)
        elif opt == slc.LMODE_SLC:
            self._handle_sb_linemode_slc(buf)
        elif opt in (DO, DONT, WILL, WONT):
            sb_opt = buf.popleft()
            if sb_opt != slc.LMODE_FORWARDMASK:
                raise ValueError(
                    'Illegal byte follows IAC SB LINEMODE {}: {!r}, '
                    ' expected LMODE_FORWARDMASK.'
                    .format(name_command(opt), sb_opt))
            self.log.debug('recv IAC SB LINEMODE {} LMODE_FORWARDMASK,'
                           .format(name_command(opt)))
            self._handle_sb_forwardmask(LINEMODE, buf)
        else:
            raise ValueError('Illegal IAC SB LINEMODE option {!r}'.format(opt))

    def _handle_sb_linemode_mode(self, mode):
        """
        Callback handles mode following IAC SB LINEMODE LINEMODE_MODE.

        :param bytes mode: a single byte

        Result of agreement to enter ``mode`` given applied by setting the
        value of ``self.linemode``, and sending acknowledgment if necessary.
        """
        suggest_mode = slc.Linemode(mode[0])

        self.log.debug('recv IAC SB LINEMODE LINEMODE-MODE {0!r} IAC SE'
                       .format(suggest_mode.mask))

        if not suggest_mode.ack:
            # This implementation acknowledges and sets local linemode
            # to *any* setting the remote end suggests, requiring a
            # reply.  See notes later under server receipt of acknowledged
            # linemode.
            self.send_linemode(linemode=slc.Linemode(
                mask=bytes([ord(suggest_mode.mask) | ord(slc.LMODE_MODE_ACK)]))
            )
            return

        # " In all cases, a response is never generated to a MODE
        #   command that has the MODE_ACK bit set."
        #
        # simply: cannot call self.send_linemode() here forward.

        if self.client:
            if self._linemode != suggest_mode:
                # " When a MODE command is received with the MODE_ACK bit set,
                #   and the mode is different that what the current mode is,
                #   the client will ignore the new mode"
                #
                self.log.warning('server mode differs from local mode, '
                                 'though ACK bit is set. Local mode will '
                                 'remain.')
                self.log.warning('!remote: {0!r}'.format(suggest_mode))
                self.log.warning('  local: {0!r}'.format(self._linemode))
                return

            self.log.debug('Linemode matches, acknowledged by server.')
            self._linemode = suggest_mode
            return

        # as a server, we simply honor whatever is given.  This is also
        # problematic in some designers may wish to implement shells
        # that specifically do not honor some parts of the bitmask, we
        # must provide them an any/force-on/force-off mode-table interface.
        if self._linemode != suggest_mode:
            self.log.debug('We suggested, - {0!r}'.format(self._linemode))
            self.log.debug('Client choses + {0!r}'.format(suggest_mode))
        else:
            self.log.debug('Linemode agreed by client: {0!r}'
                           .format(self._linemode))

        self._linemode = suggest_mode

    def _handle_sb_linemode_slc(self, buf):
        """
        Callback handles IAC-SB-LINEMODE-SLC-<buf>.

        Processes SLC command function triplets found in ``buf`` and replies
        accordingly.
        """
        if not len(buf) - 2 % 3:
            raise ValueError('SLC buffer wrong size: expect multiple of 3: {}'
                             .format(len(buf) - 2))
        self._slc_start()
        while len(buf):
            func = buf.popleft()
            flag = buf.popleft()
            value = buf.popleft()
            slc_def = slc.SLC(flag, value)
            self._slc_process(func, slc_def)
        self._slc_end()
        self.request_forwardmask()

    def _slc_end(self):
        """Transmit SLC commands buffered by :meth:`_slc_send`."""
        if len(self._slc_buffer):
            self.log.debug('send (slc_end): {!r}'
                           .format(b''.join(self._slc_buffer)))
            buf = b''.join(self._slc_buffer)
            self._transport.write(self._escape_iac(buf))
            self._slc_buffer.clear()

        self.log.debug('slc_end: [..] IAC SE')
        self.send_iac(IAC + SE)

    def _slc_start(self):
        """Send IAC SB LINEMODE SLC header."""
        self.log.debug('slc_start: IAC SB LINEMODE SLC [..]')
        self.send_iac(IAC + SB + LINEMODE + slc.LMODE_SLC)

    def _slc_send(self, slctab=None):
        """
        Send supported SLC characters of current tabset, or specified tabset.

        :param dict slctab: SLC byte tabset as dictionary, such as
            slc.BSD_SLC_TAB.
        """
        send_count = 0
        slctab = slctab or self.slctab
        for func in range(slc.NSLC + 1):
            if func == 0 and self.client:
                # only the server may send an octet with the first
                # byte (func) set as 0 (SLC_NOSUPPORT).
                continue

            _default = slc.SLC_nosupport()
            if self.slctab.get(bytes([func]), _default).nosupport:
                continue

            self._slc_add(bytes([func]))
            send_count += 1
        self.log.debug('slc_send: {} functions queued.'.format(send_count))

    def _slc_add(self, func, slc_def=None):
        """
        Prepare slc triplet response (function, flag, value) for transmission.

        For the given SLC_func byte and slc_def instance providing
        byte attributes ``flag`` and ``val``. If no slc_def is provided,
        the slc definition of ``slctab`` is used by key ``func``.
        """
        if slc_def is None:
            slc_def = self.slctab[func]
        self.log.debug('_slc_add ({:<10} {})'.format(
            slc.name_slc_command(func) + ',', slc_def))
        if len(self._slc_buffer) >= slc.NSLC * 6:
            raise ValueError('SLC: buffer full!')
        self._slc_buffer.extend([func, slc_def.mask, slc_def.val])

    def _slc_process(self, func, slc_def):
        """
        Process an SLC definition provided by remote end.

        Ensure the function definition is in-bounds and an SLC option
        we support. Store SLC_VARIABLE changes to self.slctab, keyed
        by SLC byte function ``func``.

        The special definition (0, SLC_DEFAULT|SLC_VARIABLE, 0) has the
        side-effect of replying with a full slc tabset, resetting to
        the default tabset, if indicated.
        """
        # out of bounds checking
        if ord(func) > slc.NSLC:
            self.log.warning('SLC not supported (out of range): ({!r})'
                             .format(func))
            self._slc_add(func, slc.SLC_nosupport())
            return

        # process special request
        if func == theNULL:
            if slc_def.level == slc.SLC_DEFAULT:
                # client requests we send our default tab,
                self.log.debug('_slc_process: client request SLC_DEFAULT')
                self._slc_send(self.default_slc_tab)
            elif slc_def.level == slc.SLC_VARIABLE:
                # client requests we send our current tab,
                self.log.debug('_slc_process: client request SLC_VARIABLE')
                self._slc_send()
            else:
                self.log.warning('func(0) flag expected, got {}.'.format(slc_def))
            return

        self.log.debug('_slc_process {:<9} mine={}, his={}'.format(
            slc.name_slc_command(func), self.slctab[func], slc_def))

        # evaluate slc
        mylevel, myvalue = (self.slctab[func].level, self.slctab[func].val)
        if slc_def.level == mylevel and myvalue == slc_def.val:
            return
        elif slc_def.level == mylevel and slc_def.ack:
            return
        elif slc_def.ack:
            self.log.debug('slc value mismatch with ack bit set: ({!r},{!r})'
                           .format(myvalue, slc_def.val))
            return
        else:
            self._slc_change(func, slc_def)

    def _slc_change(self, func, slc_def):
        """
        Update SLC tabset with SLC definition provided by remote end.

        Modify private attribute ``slctab`` appropriately for the level
        and value indicated, except for slc tab functions of value
        SLC_NOSUPPORT and reply as appropriate through :meth:`_slc_add`.
        """
        hislevel = slc_def.level
        mylevel = self.slctab[func].level
        if hislevel == slc.SLC_NOSUPPORT:
            # client end reports SLC_NOSUPPORT; use a
            # nosupport definition with ack bit set
            self.slctab[func] = slc.SLC_nosupport()
            self.slctab[func].set_flag(slc.SLC_ACK)
            self._slc_add(func)
            return

        if hislevel == slc.SLC_DEFAULT:
            # client end requests we use our default level
            if mylevel == slc.SLC_DEFAULT:
                # client end telling us to use SLC_DEFAULT on an SLC we do not
                # support (such as SYNCH). Set flag to SLC_NOSUPPORT instead
                # of the SLC_DEFAULT value that it begins with
                self.slctab[func].set_mask(slc.SLC_NOSUPPORT)
            else:
                # set current flag to the flag indicated in default tab
                self.slctab[func].set_mask(
                    self.default_slc_tab.get(func).mask)
            # set current value to value indicated in default tab
            self.default_slc_tab.get(func, slc.SLC_nosupport())
            self.slctab[func].set_value(slc_def.val)
            self._slc_add(func)
            return

        # client wants to change to a new value, or,
        # refuses to change to our value, accept their value.
        if self.slctab[func].val != theNULL:
            self.slctab[func].set_value(slc_def.val)
            self.slctab[func].set_mask(slc_def.mask)
            slc_def.set_flag(slc.SLC_ACK)
            self._slc_add(func, slc_def)
            return

        # if our byte value is b'\x00', it is not possible for us to support
        # this request. If our level is default, just ack whatever was sent.
        # it is a value we cannot change.
        if mylevel == slc.SLC_DEFAULT:
            # If our level is default, store & ack whatever was sent
            self.slctab[func].set_mask(slc_def.mask)
            self.slctab[func].set_value(slc_def.val)
            slc_def.set_flag(slc.SLC_ACK)
            self._slc_add(func, slc_def)
        elif (slc_def.level == slc.SLC_CANTCHANGE and
              mylevel == slc.SLC_CANTCHANGE):
            # "degenerate to SLC_NOSUPPORT"
            self.slctab[func].set_mask(slc.SLC_NOSUPPORT)
            self._slc_add(func)
        else:
            # mask current level to levelbits (clears ack),
            self.slctab[func].set_mask(self.slctab[func].level)
            if mylevel == slc.SLC_CANTCHANGE:
                slc_def = self.default_slc_tab.get(
                    func, slc.SLC_nosupport())
                self.slctab[func].val = slc_def.val
            self._slc_add(func)

    def _handle_sb_forwardmask(self, cmd, buf):
        """
        Callback handles request for LINEMODE <cmd> LMODE_FORWARDMASK.

        :param bytes cmd: one of DO, DONT, WILL, WONT.
        :param bytes buf: bytes following IAC SB LINEMODE DO FORWARDMASK.
        """
        # set and report about pending options by 2-byte opt,
        # not well tested, no known implementations exist !
        if self.server:
            assert self.remote_option.enabled(LINEMODE), (
                'cannot recv LMODE_FORWARDMASK {} ({!r}) '
                'without first sending DO LINEMODE.'
                .format(cmd, buf,))
            assert cmd not in (DO, DONT,), (
                'cannot recv {} LMODE_FORWARDMASK on server end'
                .format(name_command(cmd)))
        if self.client:
            assert self.local_option.enabled(LINEMODE), (
                'cannot recv {} LMODE_FORWARDMASK without first '
                ' sending WILL LINEMODE.'
                .format(name_command(cmd)))
            assert cmd not in (WILL, WONT,), (
                'cannot recv {} LMODE_FORWARDMASK on client end'
                .format(name_command(cmd)))
            assert cmd not in (DONT,) or len(buf) == 0, (
                'Illegal bytes follow DONT LMODE_FORWARDMASK: {!r}'
                .format(buf))
            assert cmd not in (DO,) and len(buf), (
                'bytes must follow DO LMODE_FORWARDMASK')

        opt = SB + LINEMODE + slc.LMODE_FORWARDMASK
        if cmd in (WILL, WONT,):
            self.remote_option[opt] = bool(cmd is WILL)
        elif cmd in (DO, DONT,):
            self.local_option[opt] = bool(cmd is DO)
            if cmd == DO:
                self._handle_do_forwardmask(buf)

    def _handle_do_forwardmask(self, buf):
        """
        Callback handles request for LINEMODE DO FORWARDMASK.

        :param bytes buf: bytes following IAC SB LINEMODE DO FORWARDMASK.
        :raises NotImplementedError
        """
        raise NotImplementedError


class TelnetWriterUnicode(TelnetWriter):
    """
    A Unicode StreamWriter interface for Telnet protocol.

    See ancestor class, :class:`TelnetWriter` for details.

    Requires the ``fn_encoding`` callback, receiving mutually boolean keyword
    argument ``outgoing=True`` to determine what encoding should be used to
    decode the value in the direction specified.

    The encoding may be conditionally negotiated by CHARSET, :rfc:`2066`, or
    discovered by ``LANG`` environment variables by NEW_ENVIRON, :rfc:`1572`.
    """

    def __init__(self, transport, protocol, fn_encoding, *,
                 encoding_errors='strict', **kwds):
        self.fn_encoding = fn_encoding
        self.encoding_errors = encoding_errors
        super().__init__(transport, protocol, **kwds)

    def encode(self, string, errors):
        """
        Encode ``string`` using protocol-preferred encoding.

        :param str errors: same as meaning in :meth:`codecs.Codec.encode`.  When None,
            value of ``encoding_errors`` given to class initializer is used.
        :param str errors: same as meaning in :meth:`codecs.Codec.encode`, when
            ``None`` (default), value of class initializer keyword argument,
            ``encoding_errors``.

        .. note: though a unicode interface, when ``outbinary`` mode has not
            been protocol negotiated, ``fn_encoding`` strictly enforces 7-bit
            ASCII range (ordinal byte values less than 128), as a strict
            compliance of the telnet RFC.
        """
        encoding = self.fn_encoding(outgoing=True)
        return bytes(string, encoding, errors or self.encoding_errors)

    def write(self, string, errors=None):
        """
        Write unicode string to transport, using protocol-preferred encoding.

        :param str string: unicode string text to write to endpoint using the
            protocol's preferred encoding.  When the protocol ``encoding``
            keyword is explicitly set to ``False``, the given string should be
            only raw ``b'bytes'``.
        :param str errors: same as meaning in :meth:`codecs.Codec.encode`, when
            ``None`` (default), value of class initializer keyword argument,
            ``encoding_errors``.
        :rtype: None
        """
        errors = errors or self.encoding_errors
        self._write(self.encode(string, errors))

    def writelines(self, lines, errors=None):
        """
        Write unicode strings to transport.

        Note that newlines are not added.  The sequence can be any iterable
        object producing strings. This is equivalent to calling write() for
        each string.
        """
        self.write(string=u''.join(lines), errors=errors)

    def echo(self, string, errors=None):
        """
        Conditionally write ``string`` to transport when "remote echo" enabled.

        :param str string: string received as input, conditionally written.
        :param str errors: same as meaning in :meth:`codecs.Codec.encode`.

        This method may only be called from the server perspective.  The
        default implementation depends on telnet negotiation willingness for
        local echo: only an RFC-compliant telnet client will correctly set or
        unset echo accordingly by demand.
        """
        assert self.server, ('Client never performs echo of input received.')
        if self.will_echo:
            self.write(string=string, errors=errors)


class Option(dict):
    """
    Telnet option state negotiation helper class.

    This class simply acts as a logging decorator for state changes of
    a dictionary describing telnet option negotiation.
    """

    def __init__(self, name, log):
        """
        Class initializer.

        :param str name: decorated name representing option class, such as
            'local', 'remote', or 'pending'.
        :param logging.Logger log: logging instance where debug information
            of state changes is recorded (as DEBUG).
        """
        self.name, self.log = name, log
        dict.__init__(self)

    def enabled(self, key):
        """
        Return True if option is enabled.

        :param bytes key: telnet option
        :rtype: bool
        """
        return bool(self.get(key, None) is True)

    def __setitem__(self, key, value):
        # the real purpose of this class, tracking state negotiation.
        if value != dict.get(self, key, None):
            descr = ' + '.join([name_command(bytes([byte]))
                                for byte in key[:2]
                                ] + [repr(byte) for byte in key[2:]])
            self.log.debug('{}[{}] = {}'.format(self.name, descr, value))
        dict.__setitem__(self, key, value)


def _escape_environ(buf):
    """
    Return new buffer with VAR and USERVAR escaped, if present in ``buf``.

    :param bytes buf: given bytes buffer
    :returns: bytes buffer with escape characters inserted.
    :rtype: bytes
    """
    return buf.replace(VAR, ESC + VAR).replace(USERVAR, ESC + USERVAR)


def _unescape_environ(buf):
    """
    Return new buffer with escape characters removed for VAR and USERVAR.

    :param bytes buf: given bytes buffer
    :returns: bytes buffer with escape characters removed.
    :rtype: bytes
    """
    return buf.replace(ESC + VAR, VAR).replace(ESC + USERVAR, USERVAR)


def _encode_env_buf(env):
    """
    Encode dictionary for transmission as environment variables, :rfc:`1572`.

    :param bytes buf: dictionary of environment values.
    :returns: bytes buffer meant to follow sequence IAC SB NEW_ENVIRON IS.
        It is not terminated by IAC SE.
    :rtype: bytes

    Returns bytes array ``buf`` for use in sequence (IAC, SB,
    NEW_ENVIRON, IS, <buf>, IAC, SE) as set forth in :rfc:`1572`.
    """
    buf = collections.deque()
    for key, value in env.items():
        buf.append(VAR)
        buf.extend([_escape_environ(key.encode('ascii'))])
        buf.append(VALUE)
        buf.extend([_escape_environ('{}'.format(value).encode('ascii'))])
    return b''.join(buf)


def _decode_env_buf(buf):
    """
    Decode environment values to dictionary, :rfc:`1572`.

    :param bytes buf: bytes array following sequence IAC SB NEW_ENVIRON
        SEND or IS up to IAC SE.
    :returns: dictionary representing the environment values decoded from buf.
    :rtype: dict

    This implementation does not distinguish between ``USERVAR`` and ``VAR``.
    """
    env = {}

    # build table of (non-escaped) delimiters by index of buf[].
    breaks = [idx for (idx, byte) in enumerate(buf)
              if (bytes([byte]) in (VAR, USERVAR,) and
                  (idx == 0 or bytes([buf[idx - 1]]) != ESC))]

    for idx, ptr in enumerate(breaks):
        # find buf[] starting, ending positions, begin after
        # buf[0], which is currently valued VAR or USERVAR
        start = ptr + 1
        if idx == len(breaks) - 1:
            end = len(buf)
        else:
            end = breaks[idx + 1]

        pair = buf[start:end].split(VALUE, 1)
        key = _unescape_environ(pair[0]).decode('ascii', 'strict')
        if len(pair) == 1:
            value = ''
        else:
            value = _unescape_environ(pair[1]).decode('ascii', 'strict')
        env[key] = value

    return env
