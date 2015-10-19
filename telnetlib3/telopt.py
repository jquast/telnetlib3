import collections
import logging
import struct
from telnetlib import (
    LINEMODE, NAWS, NEW_ENVIRON, BINARY, SGA, ECHO, STATUS,
    TTYPE, TSPEED, LFLOW, XDISPLOC, IAC, DONT, DO, WONT,
    WILL, SE, NOP, TM, DM, BRK, IP, AO, AYT, EC, EL, EOR,
    GA, SB, LOGOUT, CHARSET, SNDLOC, theNULL,
    # not supported or used,
    ENCRYPT, AUTHENTICATION, TN3270E, XAUTH, RSP,
    COM_PORT_OPTION, SUPPRESS_LOCAL_ECHO, TLS, KERMIT,
    SEND_URL, FORWARD_X, PRAGMA_LOGON, SSPI_LOGON,
    PRAGMA_HEARTBEAT, EXOPL, X3PAD, VT3270REGIME, TTYLOC,
    SUPDUPOUTPUT, SUPDUP, DET, BM, XASCII, RCP, NAMS,
    RCTE, NAOL, NAOP, NAOCRD, NAOHTS, NAOHTD, NAOFFD,
    NAOVTS, NAOVTD, NAOLFD)

from . import slc

__all__ = ('TelnetStream', 'name_command', 'name_commands')

(EOF, SUSP, ABORT, CMD_EOR) = (
    bytes([const]) for const in range(236, 240))
(IS, SEND, INFO) = (bytes([const]) for const in range(3))
(VAR, VALUE, ESC, USERVAR) = (bytes([const]) for const in range(4))
(LFLOW_OFF, LFLOW_ON, LFLOW_RESTART_ANY, LFLOW_RESTART_XON) = (
    bytes([const]) for const in range(4))
(REQUEST, ACCEPTED, REJECTED, TTABLE_IS, TTABLE_REJECTED,
    TTABLE_ACK, TTABLE_NAK) = (bytes([const]) for const in range(1, 8))
(MCCP_COMPRESS, MCCP2_COMPRESS) = (bytes([85]), bytes([86]))
_MAXSIZE_SB = 1 << 15
_MAXSIZE_SLC = slc.NSLC * 6


class TelnetStream:
    """
       This class implements a ``feed_byte()`` method that acts as a
       Telnet Is-A-Command (IAC) interpreter.

       The significance of the last byte passed to this method is tested
       by instance attributes following the call. A minimal Telnet Service
       Protocol ``data_received`` method should forward each byte, or begin
       forwarding at receipt of IAC until ``is_oob`` tests ``False``.
    """
    MODE_LOCAL = 'local'
    MODE_REMOTE = 'remote'
    MODE_KLUDGE = 'kludge'

    #: a list of system environment variables requested by the server after
    # a client agrees to negotiate NEW_ENVIRON.
    default_env_request = (
        "USER HOSTNAME UID TERM COLUMNS LINES DISPLAY LANG SYSTEMTYPE "
        "ACCT JOB PRINTER SFUTLNTVER SFUTLNTMODE LC_ALL VISUAL EDITOR "
        "LC_COLLATE LC_CTYPE LC_MESSAGES LC_MONETARY LC_NUMERIC LC_TIME"
    ).split()
    default_slc_tab = slc.BSD_SLC_TAB
    default_codepages = (
        'UTF-8', 'UTF-16', 'US-ASCII', 'LATIN1', 'BIG5',
        'GBK', 'SHIFTJIS', 'GB18030', 'KOI8-R', 'KOI8-U',
    ) + tuple(
        # "Part 12 was slated for Latin/Devanagari, but abandoned in 1997"
        'ISO8859-{}'.format(iso) for iso in range(1, 16) if iso != 12
    ) + tuple(
        'CP{}'.format(cp) for cp in (
            154, 437, 500, 737, 775, 850, 852, 855, 856, 857,
            860, 861, 862, 863, 864, 865, 866, 869, 874, 875,
            932, 949, 950, 1006, 1026, 1140, 1250, 1251, 1252,
            1253, 1254, 1255, 1257, 1257, 1258, 1361,
        )
    )

    @property
    def mode(self):
        """ String describing NVT mode, one of:

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
            return (self.MODE_LOCAL
                    if self._linemode.local
                    else self.MODE_REMOTE)
        if self.is_server:
            return (self.MODE_KLUDGE
                    if self.local_option.enabled(ECHO)
                    and self.local_option.enabled(SGA)
                    else self.MODE_LOCAL)
        return (self.MODE_KLUDGE
                if self.remote_option.enabled(ECHO)
                and self.remote_option.enabled(SGA)
                else self.MODE_LOCAL)

    @property
    def is_server(self):
        """ Returns True if stream is used for server-end. """
        return bool(self._is_server)

    @property
    def is_client(self):
        """ Returns True if stream is used for client-end.  """
        return bool(not self._is_server)

    @property
    def is_oob(self):
        """ Last byte processed by ``feed_byte()`` should not be received
            in-band: not duplicated to the client if remote ECHO is enabled,
            and not inserted into an input buffer.
        """
        return (self.iac_received or self.cmd_received)

    @property
    def linemode(self):
        """ Linemode instance for stream, or None if stream is in Kludge mode.
        """
        #   A description of the linemode entered may be tested using boolean
        #   instance attributes ``edit``, ``trapsig``, ``soft_tab``,
        #   ``lit_echo``, ``remote``, ``local``.
        return (self._linemode if self.mode != 'kludge' else None)

    def __init__(self, transport, client=False, server=False, log=logging):
        """
        .. class::TelnetServer(transport, client=False, server=False,
                               log=logging)

        Server and Client streams negotiate about capabilities from different
        perspectives, so the mutually exclusive booleans ``client`` and
        ``server`` (default) indicates which end the protocol is attached to.

        Extending or changing protocol capabilities should extend, override,
        or register their own callables, for the local iac, slc, and ext
        callback handlers; mainly those beginning with ``handle``, or by
        registering using the methods beginning with ``set_callback``.
        """
        assert not client is False or not server is False, (
            "Arguments 'client' and 'server' are mutually exclusive")
        self.log = log
        self.transport = transport
        self._is_server = (
            client in (None, False) or
            server not in (None, False))

        #: Total bytes sent to ``feed_byte()``
        self.byte_count = 0

        #: Wether flow control is enabled.
        self.lflow = True

        #: Wether flow control enabled by Transmit-Off (XOFF) (defaults
        #  to Ctrl-s), should re-enable Transmit-On (XON) only on receipt
        #  of the XON key (Ctrl-q). Or, when unset, any keypress from client
        #  re-enables transmission (XON).
        self.xon_any = False

        #: boolean is set ``True`` if last byte sent to ``feed_byte()`` is the
        #  beginning of an IAC command (\xff).
        self.iac_received = False

        #: IAC command byte value if the last byte sent to ``feed_byte()`` is
        #  part of an IAC command sequence, such as *WILL* or *SB*.
        self.cmd_received = False

        #: SLC function value if last byte sent to ``feed_byte()`` is a
        #  matching special line chracter value, False otherwise.
        self.slc_received = False

        #: SLC function values and callbacks are fired for clients in
        #  Kludge mode not otherwise capable of negotiating them, providing
        #  remote editing facilities for dumb clients.
        self.slc_simulated = True

        #: Dictionary of telnet option byte(s) that follow an
        # IAC-DO or IAC-DONT command, and contains a value of ``True``
        # until IAC-WILL or IAC-WONT has been received by remote end.
        self.pending_option = Option('pending_option', self.log)

        #: Dictionary of telnet option byte(s) that follow an
        # IAC-WILL or IAC-WONT command, sent by our end,
        # indicating state of local capabilities.
        self.local_option = Option('local_option', self.log)

        #: Dictionary of telnet option byte(s) that follow an
        # IAC-WILL or IAC-WONT command received by remote end,
        # indicating state of remote capabilities.
        self.remote_option = Option('remote_option', self.log)

        #: Sub-negotiation buffer
        self._sb_buffer = collections.deque()

        #: SLC buffer
        self._slc_buffer = collections.deque()

        #: SLC Tab (SLC Functions and their support level, and ascii value)
        self.slctab = slc.generate_slctab(self.default_slc_tab)

        #: Represents LINEMODE MODE negotiated or requested by client.
        #  attribute ``ack`` returns True if it is in use.
        self._linemode = slc.Linemode()

        #: bool implementing Flow Control, False when XOFF received,
        #  pending data buffered into self._write_buffer
        self.writing = True
        self._write_buffer = collections.deque()

        #: Initial line mode requested by server if client supports LINEMODE
        #  negotiation (remote line editing and literal echo of control chars)
        self.default_linemode = slc.Linemode(bytes([
            ord(slc.LMODE_MODE_REMOTE) | ord(slc.LMODE_MODE_LIT_ECHO)]))

        # Set default callback handlers to local methods.
        self._iac_callback = {}
        for iac_cmd, key in (
                (BRK, 'brk'), (IP, 'ip'), (AO, 'ao'), (AYT, 'ayt'), (EC, 'ec'),
                (EL, 'el'), (EOF, 'eof'), (SUSP, 'susp'), (ABORT, 'abort'),
                (NOP, 'nop'), (DM, 'dm'), (GA, 'ga'), (CMD_EOR, 'eor'),
                (TM, 'tm'), ):
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
                (NEW_ENVIRON, 'env'), (CHARSET, 'charset'),
                ):
            self.set_ext_callback(
                cmd=ext_cmd, func=getattr(self, 'handle_{}'.format(key)))

        self._ext_send_callback = {}
        for ext_cmd, key in (
                (TTYPE, 'ttype'), (TSPEED, 'tspeed'), (XDISPLOC, 'xdisploc'),
                (NEW_ENVIRON, 'env'), (NAWS, 'naws'), (SNDLOC, 'sndloc'),
                (CHARSET, 'charset'), ):
            self.set_ext_send_callback(
                cmd=ext_cmd, func=getattr(self, 'handle_send_{}'.format(key)))

    def __str__(self):
        """ XXX Returns string suitable for status of telnet stream.
        """
        all_option_states = describe_stream_option_states(self)
        if all_option_states:
            return '{{{}}}'.format(', '.join(
                ['{!r}: {!r}'.format(key, ','.join([_opt for _opt in options]))
                 for key, options in all_option_states.items()
                 if len(options)]))
        return '(no negotiation performed)'

    def feed_byte(self, byte):
        """
        Feed a single byte into Telnet option state machine.

        :param int byte: an 8-bit byte value as integer (0-255), or
            a bytes array.  When a bytes array, it must be of length
            1.

        This is the entry point of the 'IAC Interpreter', to be fed a single
        byte at a time.
        """
        # Python 3 lesson: it is more convenient to work with a bytes array of
        # length 1 than it is to work with "a byte", as, iterating over a bytes
        # array produces an integer, it must be translated back to a bytes type
        # so that it may be used with comparators against equal types.
        if isinstance(byte, int):
            assert 0 <= byte <= 255
            byte = bytes([byte])
        assert isinstance(byte, (bytes, bytearray)), byte
        assert len(byte) == 1, byte
        self.byte_count += 1
        self.slc_received = False
        # list of IAC commands needing 3+ bytes (mbs: multibyte sequence)
        iac_mbs = (DO, DONT, WILL, WONT, SB)
        # cmd received is toggled false, unless its a mbs.
        self.cmd_received = self.cmd_received in iac_mbs and self.cmd_received

        if byte == IAC:
            self.iac_received = (not self.iac_received)
            if not self.iac_received and self.cmd_received == SB:
                # SB buffer receives escaped IAC values
                self._sb_buffer.append(IAC)

        elif self.iac_received and not self.cmd_received:
            # parse 2nd byte of IAC, even if recv under SB
            self.cmd_received = cmd = byte
            if cmd not in iac_mbs:
                # DO, DONT, WILL, WONT are 3-byte commands and
                # SB can be of any length. Otherwise, this 2nd byte
                # is the final IAC sequence command byte.
                assert cmd in self._iac_callback, name_command(cmd)
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
            assert len(self._sb_buffer) < _MAXSIZE_SB

        elif self.cmd_received:
            # parse 3rd and final byte of IAC DO, DONT, WILL, WONT.
            cmd, opt = self.cmd_received, byte
            self.log.debug('recv IAC {} {}'.format(
                name_command(cmd), name_command(opt)))
            try:
                if cmd == DO:
                    if self.handle_do(opt):
                        self.local_option[opt] = True
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
                        if self.pending_option.enabled(DONT + opt):
                            self.pending_option[DONT + opt] = False
                elif cmd == WONT:
                    self.handle_wont(opt)
                    self.pending_option[DO + opt] = False
            finally:
                # toggle iac_received on any ValueErrors/AssertionErrors raised
                self.iac_received = False
                self.cmd_received = (opt, byte)

        elif self.pending_option.enabled(DO + TM):
            # IAC DO TM was previously sent; discard all input until
            # IAC WILL TM or IAC WONT TM is received by remote end.
            self.log.debug('discarded by timing-mark: {!r}'.format(byte))

        elif (self.mode == 'remote' or
                self.mode == 'kludge' and self.slc_simulated):
            # 'byte' is tested for SLC characters
            (callback, slc_name, slc_def) = slc.snoop(
                byte, self.slctab, self._slc_callback)
            if slc_name is not None:
                self.log.debug('_slc_snoop({!r}): {}, callback is {}.'.format(
                    byte, slc.name_slc_command(slc_name),
                    callback.__name__ if callback is not None
                    else None))
                if slc_def.flushin:
                    # SLC_FLUSHIN not supported, requires SYNCH? (urgent TCP).
                    # XXX or TM?
                    pass
                if slc_def.flushout:
                    # XXX
                    # We must call transport.pause_writing, create a new send
                    # buffer without incompleted IAC bytes, call
                    # discard_output, write new buffer, then resume_writing
                    pass
                # allow caller to know which SLC function caused linemode
                # to process, even though CR was not yet discovered.
                self.slc_received = slc_name
            if callback is not None:
                callback(slc_name)
        else:
            # standard inband data
            return
        if not self.writing and self.xon_any and not self.is_oob:
                # sub-negotiation end (SE), fire handle_subnegotiation
            # any key after XOFF enables XON
            self._slc_callback[slc.SLC_XON](byte)

    def write(self, data, oob=False):
        """
        Write a single to transport end connected to stream reader.

        Bytes matching IAC (\xff, Is-A-Command) are escaped by (IAC, IAC)
        here, unless ``oob`` is ``True``.  When oob is True, data is always
        written regardless of XON/XOFF.  Otherwise, this data may be
        held in-memory until XON (the default state).
        """
        # Note(jquast): Our OOB implementation is not RFC-correct, but we're
        # having a very difficult time implementing these old MSG_OOB-flagged
        # tcp socket writes.  We may have to provide a unix-only derived impl.?
        #
        # is 'oob' even the correct keyword, here?
        assert isinstance(data, (bytes, bytearray)), repr(data)
        if not oob and not self.local_option.enabled(BINARY):
            for pos, byte in enumerate(data):
                assert byte < 128, (
                    'character value {} at pos {} not valid, send '
                    'IAC WILL BINARY first: {}'.format(byte, pos, data))
        data = data if oob else _escape_iac(data)
        if self.writing or oob:
            self.log.debug('write: {!r}'.format(data))
            self.transport.write(data)
        else:
            # XOFF (Transmit Off) enabled, buffer in-band data until XON.
            self.log.debug('buffered (XOFF): {!r}'.format(data))
            self._write_buffer.extend([data])

    def send_iac(self, data):
        """ .. method: send_iac(self, data : bytes)

            no transformations of bytes are performed, only complete
            iac commands are legal (unless ``partial`` is set ``false``).
        """
        assert isinstance(data, (bytes, bytearray)), data
        assert data and data.startswith(IAC)
        self.transport.write(data)

    def iac(self, cmd, opt):
        """ .. method: iac(self, cmd : bytes, opt : bytes)

            Send Is-A-Command (IAC) 2 or 3-byte command option.

            Returns True if command was sent. Not all commands are legal in the
            context of client, server, or pending negotiation state, emitting a
            relevant debug warning to the log handler if not sent.
        """
        assert (cmd in (DO, DONT, WILL, WONT)
                ), ('Unknown IAC {}.'.format(name_command(cmd)))
        if opt == LINEMODE:
            if cmd == DO and self.is_client:
                raise ValueError('DO LINEMODE may only be sent by server.')
            if cmd == WILL and self.is_server:
                raise ValueError('WILL LINEMODE may only be sent by client.')
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
        if cmd == DONT and opt not in (LOGOUT,):  # XXX any other exclusions?
            if self.remote_option.enabled(opt):
                # warning: some implementations incorrectly reply (DONT, opt),
                # for an option we already said we WONT. This would cause
                # telnet loops for implementations not doing state tracking!
                self.log.debug('skip {} {}; remote_option = True'.format(
                    name_command(cmd), name_command(opt)))
            self.remote_option[opt] = False
        if cmd == WONT:
            self.local_option[opt] = False
        self.log.debug('send IAC {} {}'.format(
            name_command(cmd), name_command(opt)))
        self.send_iac(IAC + cmd + opt)

# Public methods for transmission signaling
#

    def send_ga(self):
        """ .. method:: send_ga() -> bool

            Send IAC-GA (go ahead) only if IAC-DONT-SGA was received.
            Returns True if GA was sent.
        """
        if not self.local_option.enabled(SGA):
            self.log.debug('send IAC GA')
            self.send_iac(IAC + GA)
            return True
        return False

    def send_eor(self):
        """ .. method:: request_eor() -> bool

            Send IAC EOR (End-of-Record) only if IAC DO CMD_EOR was received.
            Returns True if EOR was sent.
        """
        if not self.local_option.enabled(CMD_EOR):
            self.log.debug('cannot send IAC EOR '
                           'without receipt of DO CMD_EOR')
        else:
            self.log.debug('send IAC EOR')
            self.send_iac(IAC + EOR)
            return True
        return False

# Public methods for notifying about, or soliciting state options.
#
    def request_status(self):
        """ .. method:: request_status() -> bool

            Send IAC-SB-STATUS-SEND sub-negotiation (rfc859) only if
            IAC-WILL-STATUS has been received. Returns True if status
            request was sent.
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
            self.log.debug('cannot send SB STATUS SEND, request pending.')
        return False

    def request_tspeed(self):
        """ .. method:: request_tspeed() -> bool

            Send IAC-SB-TSPEED-SEND sub-negotiation (rfc1079) only if
            IAC-WILL-TSPEED has been received. Returns True if tspeed
            request was sent.
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

    def request_charset(self, codepages=None, sep=' '):
        """ .. method:: request_charset(codepages : list, sep : string) -> bool

            Request sub-negotiation CHARSET, rfc 2066. Returns True if request
            is valid for telnet state, and was sent.

            The sender requests that all text sent to and by it be encoded in
            one of character sets specifed by string list ``codepages``.
        """
        codepages = self.default_codepages if codepages is None else codepages
        if not self.remote_option.enabled(CHARSET):
            self.log.debug('cannot send SB CHARSET REQUEST '
                           'without receipt of WILL CHARSET')
        elif not self.pending_option.enabled(SB + CHARSET):
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
        else:
            self.log.debug('cannot send SB CHARSET REQUEST, request pending.')
        return False

    def request_env(self, env=None, all_var=True, all_uservar=True):
        """ .. method:: request_env(env : list) -> bool

            Request sub-negotiation NEW_ENVIRON, rfc 1572.
            Returns True if request is valid for telnet state, and was sent.

            ``env`` is list ascii uppercase keys of values requested. Default
            value is when unset is instance attribute ``default_env_request``.
            Returns True if request is valid for telnet state, and was sent.
        """
        kind = NEW_ENVIRON
        request_env_values = self.default_env_request if env is None else env
        assert self.is_server, (
            'SB {} SEND may only be sent by server end'
            .format(name_command(kind)))
        if not self.remote_option.enabled(kind):
            self.log.debug('cannot send SB {} SEND IS '
                           'without receipt of WILL {}'.format(
                               name_command(kind), name_command(kind)))
        elif not self.pending_option.enabled(SB + kind):
            response = collections.deque()
            response.extend([IAC, SB, kind, SEND])
            if request_env_values:
                for env_key in request_env_values:
                    byte_env_key = _escape_env(env_key.encode('ascii'))
                    response.extend([VAR])
                    response.extend([byte_env_key])
            if all_var:
                # VAR fllowed by IAC,SE or USERVAR indicates
                # "send all the variables"
                response.append(VAR)
            if all_uservar:
                # USERVAR fllowed by IAC,SE or VAR indicates
                # "send all the user variables"
                response.append(USERVAR)
            response.extend([IAC, SE])
            self.log.debug('request_env: {!r}'.format(b''.join(response)))
            self.pending_option[SB + kind] = True
            self.send_iac(b''.join(response))
            return True
        else:
            self.log.debug('cannot send SB {} SEND IS, '
                           'request pending.'.format(name_command(kind)))
        return False

    def request_xdisploc(self):
        """ .. method:: request_xdisploc() -> bool

            Send XDISPLOC, SEND sub-negotiation, rfc1086.
            Returns True if request is valid for telnet state, and was sent.
        """
        assert self.is_server, (
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
        else:
            self.log.debug('cannot send SB XDISPLOC SEND, request pending.')
        return False

    def request_ttype(self):
        """ .. method:: request_ttype() -> bool

            Send TTYPE SEND sub-negotiation, rfc930.
            Returns True if request is valid for telnet state, and was sent.
        """
        assert self.is_server, (
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
        """ Request the client forward the control characters indicated
            in the Forwardmask class instance ``fmask``. When fmask is
            None, a forwardmask is generated for the SLC characters
            registered by ``slctab``.
        """
        assert self.is_server, (
            'DO FORWARDMASK may only be sent by server end')
        if not self.remote_option.enabled(LINEMODE):
            self.log.debug('cannot send SB LINEMODE DO'
                           'without receipt of WILL LINEMODE')
        else:
            if fmask is None:
                opt = SB + LINEMODE + slc.LMODE_FORWARDMASK
                forwardmask_enabled = (
                    self.is_server and self.local_option.get(opt, False)
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
            self.transport.write(fmask.value)
            self.send_iac(IAC + SE)

            return True
        return False

    def send_lineflow_mode(self):
        """
        .. method send_lineflow_mode() -> bool

        Send LFLOW mode sub-negotiation, rfc1372.
        """
        if self.is_client:
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

        An instance of the Linemode class, or self._linemode when unset.
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
        self.transport.write(self._linemode.mask)
        self.send_iac(IAC + SE)

# Public is-a-command (IAC) callbacks
#
    def set_iac_callback(self, cmd, func):
        """ Register callable ``func`` as callback for IAC ``cmd``.

            BRK, IP, AO, AYT, EC, EL, CMD_EOR, EOF, SUSP, ABORT, and NOP.

            These callbacks receive a single argument, the IAC ``cmd`` which
            triggered it.
        """
        assert callable(func), ('Argument func must be callable')
        assert cmd in (BRK, IP, AO, AYT, EC, EL, CMD_EOR, EOF, SUSP,
                       ABORT, NOP, DM, GA, TM), name_command(cmd)
        self._iac_callback[cmd] = func

    def handle_nop(self, cmd):
        """ XXX Handle IAC No-Operation (NOP).
        """
        self.log.debug('IAC NOP: Null Operation (unhandled).')

    def handle_ga(self, cmd):
        """ XXX Handle IAC Go-Ahead (GA).

            Typically, GA is only a legacy protocol for half-duplex terminals
            such as a teletype, which cannot receive output while also sending
            input.
        """
        self.log.debug('IAC GA: Go-Ahead (unhandled).')

    def handle_dm(self, cmd):
        """ XXX Handle IAC Data-Mark (DM)

            The TCP transport is not tested for OOB/TCP Urgent, so an old
            teletype half-duplex terminal may inadvertantly send unintended
            control sequences up until now,

            Oh well.  """
        self.log.debug('IAC DM: Data-Mark (unhandled).')
        #: ``True`` if the last byte sent to ``feed_byte()`` was the end
        #  of an *IAC DM* has been received. MSG_OOB not implemented, so
        #  this mechanism _should not be implmeneted_.
        #self._dm_recv = True
        #self.iac(DM)

# Public mixed-mode SLC and IAC callbacks
#
    def handle_el(self, byte):
        """ XXX Handle IAC Erase Line (EL, SLC_EL).

            Provides a function which discards all the data ready on current
            line of input. The prompt should be re-displayed.
        """
        self.log.debug('IAC EL: Erase Line (unhandled).')

    def handle_eor(self, byte):
        """ XXX Handle IAC End of Record (EOR, SLC_EOR).
        """
        self.log.debug('IAC EOR: End of Record (unhandled).')

    def handle_abort(self, byte):
        """ XXX Handle IAC Abort (ABORT, SLC_ABORT).

            Similar to Interrupt Process (IP), but means only to abort or
            terminate the process to which the NVT is connected.
        """
        self.log.debug('IAC ABORT: Abort (unhandled).')

    def handle_eof(self, byte):
        """ XXX Handle IAC End of Record (EOF, SLC_EOF).
        """
        self.log.debug('IAC EOF: End of File (unhandled).')

    def handle_susp(self, byte):
        """ XXX Handle IAC Suspend Process (SUSP, SLC_SUSP).

            Suspends the execution of the current process attached to the NVT
            in such a way that another process will take over control of the
            NVT, and the suspended process can be resumed at a later time.

            If the receiving system does not support this functionality, it
            should be ignored.
        """
        self.log.debug('IAC SUSP: Suspend (unhandled).')

    def handle_brk(self, byte):
        """ XXX Handle IAC Break (BRK, SLC_BRK).

            Sent by clients to indicate BREAK keypress. This is not the same
            as IP (^c), but a means to map sysystem-dependent break key such
            as found on an IBM Systems.
        """
        self.log.debug('IAC BRK: Break (unhandled).')

    def handle_ayt(self, byte):
        """ XXX Handle IAC Are You There (AYT, SLC_AYT).

            Provides the user with some visible (e.g., printable) evidence
            that the system is still up and running.
        """
        self.log.debug('IAC AYT: Are You There? (unhandled).')

    def handle_ip(self, byte):
        """ XXX Handle IAC Interrupt Process (IP, SLC_IP).
        """
        self.log.debug('IAC IP: Interrupt Process (unhandled).')

    def handle_ao(self, byte):
        """ XXX Handle IAC Abort Output (AO) or SLC_AO.

            Discards any remaining output on the transport buffer.

            " [...] a reasonable implementation would be to suppress the
              remainder of the text string, but transmit the prompt character
              and the preceding <CR><LF>. "
        """
        self.log.debug('IAC AO: Abort Output, '
                       '{} bytes discarded.'.format(len(self._write_buffer)))
        self._write_buffer.clear()

    def handle_ec(self, byte):
        """ XXX Handle IAC Erase Character (EC, SLC_EC).

            Provides a function which deletes the last preceding undeleted
            character from data ready on current line of input.
        """
        self.log.debug('IAC EC: Erase Character (unhandled).')

    def handle_tm(self, cmd):
        """ XXX Handle IAC (WILL, WONT, DO, DONT) Timing Mark (TM).

            TM is essentially a NOP that any IAC interpreter must answer, if at
            least it answers WONT to unknown options (required), it may still
            be used as a means to accurately measure the "ping" time.
        """
        self.log.debug('IAC TM: Received {} TM (Timing Mark).'
                       .format(name_command(cmd)))

# public Special Line Mode (SLC) callbacks
#
    def set_slc_callback(self, slc_byte, func):
        """ Register ``func`` as callbable for receipt of SLC character
            negotiated for the SLC command ``slc`` in  ``_slc_callback``,
            keyed by ``slc`` and valued by its handling function.

            SLC_SYNCH, SLC_BRK, SLC_IP, SLC_AO, SLC_AYT, SLC_EOR, SLC_ABORT,
            SLC_EOF, SLC_SUSP, SLC_EC, SLC_EL, SLC_EW, SLC_RP, SLC_XON,
            SLC_XOFF, (...)

            These callbacks receive a single argument: the SLC function
            byte that fired it. Some SLC and IAC functions are intermixed;
            which signalling mechanism used by client can be tested by
            evaulating this argument.
            """
        assert callable(func), ('Argument func must be callable')
        assert (type(slc_byte) == bytes and
                0 < ord(slc_byte) < slc.NSLC + 1
                ), ('Uknown SLC byte: {!r}'.format(slc_byte))
        self._slc_callback[slc_byte] = func

    def handle_ew(self, slc):
        """ XXX Handle SLC_EW (Erase Word).

            Provides a function which deletes the last preceding undeleted
            character, and any subsequent bytes until next whitespace character
            from data ready on current line of input.
        """
        self.log.debug('SLC EC: Erase Word (unhandled).')

    def handle_rp(self, slc):
        """ XXX Handle SLC Repaint.
        """
        self.log.debug('SLC RP: Repaint (unhandled).')

    def handle_lnext(self, slc):
        """ XXX Handle SLC Literal Next (Next character(s) received raw)
        """
        self.log.debug('SLC LNEXT: Literal Next (unhandled)')

    def handle_xon(self, byte):
        """ XXX Handle SLC XON (Transmit-On).

            The default implementation enables transmission and
            writes any data buffered during XOFF.
        """
        self.log.debug('SLC XON: Transmit On.')
        self.writing = True
        self.write(b''.join(self._write_buffer), oob=True)
        self._write_buffer.clear()

    def handle_xoff(self, byte):
        """ XXX Handle SLC XOFF (Transmit-Off)

            The default implementation disables transmission of
            in-band data until XON is received.
        """
        self.log.debug('SLC XOFF: Transmit Off.')
        self.writing = False

# public Telnet extension callbacks
#
    def set_ext_send_callback(self, cmd, func):
        """ Register ``func`` as a callback for inquries of subnegotiation
        response values of ``cmd``. These callbacks must return any number
        of arguments:

          * SNDLOC: for clients, returing one argument: the string
            describing client location, such as b'ROOM 641-A' (rfc 779).

          * NAWS: for clients, returning two integer arguments (width,
            height), such as (80, 24) (rfc 1073).

          * TSPEED: for clients, returning two integer arguments (rx, tx)
            such as (57600, 57600) (rfc 1079).

          * TTYPE: for clients, returning one string, usually the terminfo(5)
            database capability name, such as 'xterm' (rfc 1091).

          * XDISPLOC: for clients, returning one string, the DISPLAY host
            value, in form of <host>:<dispnum>[.<screennum>] (rfc 1096).

          * NEW_ENVIRON: for clients, returning a dictionary of (key, val)
            pairs of environment item values (rfc 1408).

          * CHARSET: for clients, receiving iterable of strings of character
            sets requested by server. Must returns one of those strings.
            chose one !negotiated by client (rfc 2066).
        """
        assert cmd in (SNDLOC, NAWS, TSPEED, TTYPE, XDISPLOC,
                       NEW_ENVIRON, CHARSET), cmd
        assert callable(func), ('Argument func must be callable')
        self._ext_send_callback[cmd] = func

    def set_ext_callback(self, cmd, func):
        """ Register ``func`` as callback for receipt of ``cmd`` negotiation:

          * LOGOUT: for servers and clients, receiving one argument. Server
            end may receive DO or DONT as argument ``cmd``, indicating
            client's wish to disconnect, or a response to WILL, LOGOUT,
            indicating it's wish not to be automatically disconnected.
            Client end may receive WILL or WONT, indicating server's wish
            to disconnect, or acknowledgement that the client will not be
            disconnected.

          * SNDLOC: for servers, receiving one argument: the string
            describing the client location, such as b'ROOM 641-A' (rfc 779).

          * NAWS: for servers, receiving two integer arguments (width,
            height), such as (80, 24) (rfc 1073).

          * TSPEED: for servers, receiving two integer arguments (rx, tx)
            such as (57600, 57600) (rfc 1079).

          * TTYPE: for servers, receiving one string, usually the terminfo(5)
            database capability name, such as 'xterm' (rfc 1091).

          * XDISPLOC: for servers, receiving one string, the DISPLAY host
            value, in form of <host>:<dispnum>[.<screennum>] (rfc 1096).

          * NEW_ENVIRON: for servers, receiving a dictionary of (key, val)
            pairs of remote client environment item values (rfc 1408).

          * CHARSET: for servers, receiving one string, the character set
            negotiated by client (rfc 2066).
        """
        assert cmd in (LOGOUT, SNDLOC, NAWS, TSPEED, TTYPE,
                       XDISPLOC, NEW_ENVIRON, CHARSET), cmd
        assert callable(func), ('Argument func must be callable')
        self._ext_callback[cmd] = func

    def handle_xdisploc(self, xdisploc):
        """ XXX Receive XDISPLAY value ``xdisploc``, rfc1096.
        """
        #   xdisploc string format is '<host>:<dispnum>[.<screennum>]'.
        self.log.debug('X Display is {}'.format(xdisploc))

    def handle_send_xdisploc(self):
        """ XXX Send XDISPLAY value ``xdisploc``, rfc1096.
        """
        #   xdisploc string format is '<host>:<dispnum>[.<screennum>]'.
        self.log.warn('X Display requested, returning empty response.')
        return b''

    def handle_sndloc(self, location):
        """ XXX Receive LOCATION value ``location``, rfc779.
        """
        self.log.debug('Location is {}'.format(location))

    def handle_send_sndloc(self):
        """ XXX Send LOCATION value ``location``, rfc779.
        """
        self.log.warn('Location requested, returning empty response.')
        return b''

    def handle_ttype(self, ttype):
        """ XXX Receive TTYPE value ``ttype``, rfc1091.
        """
        #   Often value of TERM, or analogous to client's emulation capability,
        #   common values for non-posix client replies are 'VT100', 'VT102',
        #   'ANSI', 'ANSI-BBS', or even a mud client identifier. RFC allows
        #   subsequent requests, the server may solicit multiple times, and
        #   the client indicates 'end of list' by cycling the return value.
        #
        #   Some example values: VT220, VT100, IBM-3278-(2 through 5),
        #       ANSITERM, ANSI, TTY, and 5250.
        self.log.debug('Terminal type is {!r}'.format(ttype))

    def handle_send_ttype(self):
        """ XXX Send TTYPE value ``ttype``, rfc1091.
        """
        self.log.warn('Terminal type requested, returning empty response.')
        return b''

    def handle_naws(self, width, height):
        """ XXX Receive window size ``width`` and ``height``, rfc1073.
        """
        self.log.debug('Terminal cols={}, rows={}'.format(width, height))

    def handle_send_naws(self):
        """ XXX Send window size ``width`` and ``height``, rfc1073.
        """
        self.log.warn('Terminal type requested, returning 80x24.')
        return 80, 24

    def handle_env(self, env):
        """ XXX Receive environment variables as dict, rfc1572.
        """
        self.log.debug('Environment values are {!r}'.format(env))

    def handle_send_env(self, keys=None):
        """ XXX Send environment variables as dict, rfc1572.
        If argument ``keys`` is None, then all available values should be
        sent. Otherwise, ``keys`` is a set of environment keys explicitly
        requested.
        """
        self.log.warn('Environment values requested, returning none.')
        return dict()

    def handle_tspeed(self, rx, tx):
        """ XXX Receive terminal speed from TSPEED as int, rfc1079.
        """
        self.log.debug('Terminal Speed rx:{}, tx:{}'.format(rx, tx))

    def handle_send_tspeed(self):
        """ XXX Send terminal speed from TSPEED as int, rfc1079.
        """
        self.log.warn('Terminal Speed requested, returning 9600,9600.')
        return 9600, 9600

    def handle_charset(self, charset):
        """ XXX Receive character set as string, rfc2066.
        """
        self.log.debug('Character set: {}'.format(charset))

    def handle_send_charset(self, charsets):
        """ XXX Send character set as string, rfc2066.
        """
        self.log.warn('Character Set requested, returning "ascii".')
        return b'ascii'

    def handle_logout(self, cmd):
        """ XXX Handle (IAC, (DO | DONT | WILL | WONT), LOGOUT), RFC 727.

            Only the server end may receive (DO, DONT).
            Only the client end may receive (WILL, WONT).
            """
        # Close the transport on receipt of DO, Reply DONT on receipt
        # of WILL.  Nothing is done on receipt of DONT or WONT LOGOFF.
        if cmd == DO:
            assert self.is_server, (cmd, LOGOUT)
            self.log.debug('client requests DO LOGOUT')
            self.transport.close()
        elif cmd == DONT:
            assert self.is_server, (cmd, LOGOUT)
            self.log.debug('client requests DONT LOGOUT')
        elif cmd == WILL:
            assert self.is_client, (cmd, LOGOUT)
            self.log.debug('recv WILL TIMEOUT (timeout warning)')
            self.log.debug('send IAC DONT LOGOUT')
            self.iac(DONT, LOGOUT)
        elif cmd == WONT:
            assert self.is_client, (cmd, LOGOUT)
            self.log.debug('recv IAC WONT LOGOUT (server refuses logout')

# public derivable methods DO, DONT, WILL, and WONT negotiation
#
    def handle_do(self, opt):
        """ XXX Process byte 3 of series (IAC, DO, opt) received by remote end.

        This method can be derived to change or extend protocol capabilities,
        for most cases, simply returning True if supported, False otherwise.

        In special cases of various RFC statutes, state is stored and
        answered in willing affirmitive, with the exception of:
            - DO TM is *always* answered WILL TM, even if it was already
              replied to.  No state is stored ("Timing Mark"), and the IAC
              callback registered by ``set_ext_callback`` for cmd TM
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
        if opt == ECHO and self.is_client:
            # What do we have here? A Telnet Server attempting to
            # fingerprint us as a broken 4.4BSD Telnet Client, which
            # would respond 'WILL ECHO'.
            self.log.debug('cannot recv DO ECHO on client end.')
            if self.local_option.get(opt, None) is None:
                self.iac(WONT, opt)
        elif opt == LINEMODE and self.is_server:
            self.log.warn('cannot recv DO LINEMODE on server end.')
        elif opt == LOGOUT and self.is_client:
            self.log.warn('cannot recv DO LOGOUT on client end.')
        elif opt == TTYPE and self.is_server:
            self.log.warn('cannot recv DO TTYPE on server end.')
        elif opt == NAWS and self.is_server:
            self.log.warn('cannot recv DO NAWS on server end.')
        elif opt == NEW_ENVIRON and self.is_server:
            self.log.warn('cannot recv DO NEW_ENVIRON on server end.')
        elif opt == XDISPLOC and self.is_server:
            self.log.warn('cannot recv DO XDISPLOC on server end.')
        elif opt == LFLOW and self.is_server:
            self.log.warn('cannot recv DO LFLOW on server end.')
        elif opt == TM:
            # timing mark is special: simply by replying, the effect
            # is accomplished ('will' or 'wont' is non-consequential):
            # the distant end is able to "time" our response. More
            # importantly, ensure that the IAC interpreter is, in fact,
            # interpreting, and, that all iac commands up to this point
            # have been processed.
            self.iac(WILL, TM)
            self._iac_callback[TM](DO)
        elif opt == LOGOUT:
            self._ext_callback[LOGOUT](DO)
        elif opt in (ECHO, LINEMODE, BINARY, SGA, LFLOW, CMD_EOR, TTYPE,
                     NEW_ENVIRON, XDISPLOC, TSPEED, CHARSET):
            if not self.local_option.enabled(opt):
                self.iac(WILL, opt)
            if opt in (LFLOW, TTYPE, NEW_ENVIRON,
                       XDISPLOC, TSPEED, CHARSET, LINEMODE):
                # expect follow-up subnegotation
                self.pending_option[SB + opt] = True
            return True
        elif opt == NAWS:
            if not self.local_option.enabled(opt):
                self.iac(WILL, opt)
            # on first receipt of DO NAWS, or any repeated requests,
            # just go ahead and send our window size.
            self._send_naws()
            return True
        elif opt == STATUS:
            if not self.local_option.enabled(opt):
                self.iac(WILL, STATUS)
            self._send_status()
            return True
        else:
            if self.local_option.get(opt, None) is None:
                self.iac(WONT, opt)
            self.log.warn('Unhandled: DO {}.'.format(name_command(opt),))
            # option value of -1 toggles opt.unsupported()
            self.local_option[opt] = -1
            if self.pending_option.enabled(WILL + opt):
                self.pending_option[WILL + opt] = False
        return False

    def handle_dont(self, opt):
        """ Process byte 3 of series (IAC, DONT, opt) received by remote end.

        This only results in ``self.local_option[opt]`` set to ``False``, with
        the exception of (IAC, DONT, LOGOUT), which only signals a callback
        to ``handle_logout(DONT)``.
        """
        self.log.debug('handle_dont({})'.format(name_command(opt)))
        if opt == LOGOUT:
            assert self.is_server, ('cannot recv DONT LOGOUT on server end')
            self._ext_callback[LOGOUT](DONT)
        # many implementations (wrongly!) sent a WONT in reply to DONT. It
        # sounds reasonable, but it can and will cause telnet loops. (ruby?)
        # Correctly, a DONT can not be declined, so there is no need to
        # affirm in the negative.

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
        ``self.default_env_request``. All others are replied with DONT.

        The result of a supported capability is a response of (IAC, DO, opt)
        and the setting of ``self.remote_option[opt]`` of ``True``. For
        unsupported capabilities, RFC specifies a response of (IAC, DONT, opt).
        Similarly, set ``self.remote_option[opt]`` to ``False``.  """
        self.log.debug('handle_will({})'.format(name_command(opt)))
        if opt in (BINARY, SGA, ECHO, NAWS, LINEMODE, CMD_EOR, SNDLOC):
            if opt == ECHO and self.is_server:
                raise ValueError('cannot recv WILL ECHO on server end')
            elif opt in (NAWS, LINEMODE, SNDLOC) and self.is_client:
                raise ValueError('cannot recv WILL {} on client end'.format(
                    name_command(opt),))
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
            if self.is_client:
                raise ValueError('cannot recv WILL LOGOUT on server end')
            self._ext_callback[LOGOUT](WILL)
        elif opt == STATUS:
            self.remote_option[opt] = True
            # This isn't really necessary, but if the server says they support
            # it, we'd like to test them on that, so go ahead, prove it ... !
            self.request_status()
        elif opt == LFLOW:
            if self.is_client:
                raise ValueError('cannot recv WILL LFLOW on client end')
            self.remote_option[opt] = True
            self.send_lineflow_mode()
        elif opt == NEW_ENVIRON:
            assert self.is_server, ('cannot recv WILL NEW_ENVIRON '
                                    'on client end.')
            self.remote_option[opt] = True
            self.request_env()
        elif opt == CHARSET:
            # charset is bi-directional: "WILL CHARSET indicates the sender
            # REQUESTS permission to, or AGREES to, use CHARSET option
            # subnegotiation to choose a character set."; however, the selected
            # encoding is, regarding SB CHARSET REQUEST, "The sender requests
            # that all text sent to and by it be encoded in one of the
            # specified character sets. "
            self.remote_option[opt] = True
            self.request_charset()
        elif opt == XDISPLOC:
            assert self.is_server, 'cannot recv WILL XDISPLOC on client end.'
            self.remote_option[opt] = True
            self.request_xdisploc()
        elif opt == TTYPE:
            assert self.is_server, 'cannot recv WILL TTYPE on client end.'
            self.remote_option[opt] = True
            self.request_ttype()
        elif opt == TSPEED:
            assert self.is_server, 'cannot recv WILL TSPEED on client end.'
            self.remote_option[opt] = True
            self.request_tspeed()
        else:
            # option value of -1 toggles opt.unsupported()
            self.iac(DONT, opt)
            self.remote_option[opt] = -1
            self.log.warn('Unhandled: WILL {}.'.format(name_command(opt),))
            self.local_option[opt] = -1
            if self.pending_option.enabled(DO + opt):
                self.pending_option[DO + opt] = False

    def handle_wont(self, opt):
        """ Process byte 3 of series (IAC, WONT, opt) received by remote end.

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
            assert not (self.is_server), (
                'cannot recv WONT LOGOUT on server end')
            if not self.pending_option.enabled(DO + LOGOUT):
                self.log.warn('Server sent WONT LOGOUT unsolicited')
            self._ext_callback[LOGOUT](WONT)
        else:
            self.remote_option[opt] = False

# public derivable Sub-Negotation parsing
#
    def handle_subnegotiation(self, buf):
        """ Callback for end of sub-negotiation buffer.

            SB options handled here are TTYPE, XDISPLOC, NEW_ENVIRON,
            NAWS, and STATUS, and are delegated to their ``handle_``
            equivalent methods. Implementors of additional SB options
            should extend this method.
        """
        assert buf, ('SE: buffer empty')
        assert buf[0] != theNULL, ('SE: buffer is NUL')
        assert len(buf) > 1, ('SE: buffer too short: {!r}'.format(buf))
        cmd = buf[0]
        assert cmd in (LINEMODE, LFLOW, NAWS, SNDLOC, NEW_ENVIRON,
                       CHARSET, TTYPE, TSPEED, XDISPLOC, STATUS,
                       ), ('SB {}: not supported'.format(name_command(cmd)))
        if self.pending_option.enabled(SB + cmd):
            self.pending_option[SB + cmd] = False
        else:
            self.log.debug('[SB + {}] unsolicited'.format(name_command(cmd)))
        if cmd == LINEMODE:
            self._handle_sb_linemode(buf)
        elif cmd == LFLOW:
            self._handle_sb_lflow(buf)
        elif cmd == NAWS:
            self._handle_sb_naws(buf)
        elif cmd == SNDLOC:
            self._handle_sb_sndloc(buf)
        elif cmd == NEW_ENVIRON:
            self._handle_sb_env(buf)
        elif cmd == CHARSET:
            self._handle_sb_charset(buf)
        elif cmd == TTYPE:
            self._handle_sb_ttype(buf)
        elif cmd == TSPEED:
            self._handle_sb_tspeed(buf)
        elif cmd == XDISPLOC:
            self._handle_sb_xdisploc(buf)
        elif cmd == STATUS:
            self._handle_sb_status(buf)
        else:
            raise ValueError('SE: unhandled: cmd={}, buf={!r}'
                             .format(name_command(cmd), buf))

# Private sub-negotiation (SB) routines
#
    def _handle_sb_charset(self, buf):
        cmd = buf.popleft()
        opt = buf.popleft()
        assert cmd == CHARSET, name_command(cmd)
        assert opt in (REQUEST, ACCEPTED, REJECTED, TTABLE_IS,
                       TTABLE_ACK, TTABLE_NAK, TTABLE_REJECTED)
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
            self.log.warn('recv IAC SB CHARSET REJECTED IAC SE')
        elif opt in (TTABLE_IS, TTABLE_ACK, TTABLE_NAK, TTABLE_REJECTED):
            raise NotImplementedError

    def _handle_sb_tspeed(self, buf):
        """ Callback handles IAC-SB-TSPEED-<buf>-SE.
        """
        cmd = buf.popleft()
        opt = buf.popleft()
        assert cmd == TSPEED, name_command(cmd)
        assert opt in (IS, SEND), opt
        opt_kind = 'IS' if opt == IS else 'INFO' if opt == INFO else 'SEND'
        if opt == IS:
            assert self.is_server, ('SE: cannot recv from server: {} {}'
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
            assert self.is_client, ('SE: cannot recv from client: {} {}'
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
        """ Callback handles IAC-SB-XIDISPLOC-<buf>-SE.
        """
        cmd = buf.popleft()
        opt = buf.popleft()
        assert cmd == XDISPLOC, name_command(cmd)
        assert opt in (IS, SEND), opt
        if opt == IS:
            xdisploc_str = b''.join(buf).decode('ascii')
            self.log.debug('recv IAC SB XDISPLOC IS {0!r} IAC SE'
                           .format(xdisploc_str))
            self._ext_callback[XDISPLOC](xdisploc_str)
        elif opt == SEND:
            xdisploc_str = self._ext_send_callback[XDISPLOC]().encode('ascii')
            response = [IAC, SB, XDISPLOC, IS, xdisploc_str, IAC, SE]
            self.log.debug('send IAC SB XDISPLOC IS {0!r} IAC SE'
                           .format(xdisploc_str))
            self.send_iac(b''.join(response))
            if self.pending_option.enabled(WILL + XDISPLOC):
                self.pending_option[WILL + XDISPLOC] = False

    def _handle_sb_ttype(self, buf):
        """ Callback handles IAC-SB-TTYPE-<buf>-SE.
        """
        cmd = buf.popleft()
        opt = buf.popleft()
        assert cmd == TTYPE, name_command(cmd)
        assert opt in (IS, SEND), opt
        if opt == IS:
            ttype_str = b''.join(buf).decode('ascii')
            self.log.debug('recv IAC SB TTYPE IS {0!r}'
                           .format(ttype_str))
            self._ext_callback[TTYPE](ttype_str)
        elif opt == SEND:
            ttype_str = self._ext_send_callback[TTYPE]().encode('ascii')
            response = [IAC, SB, TTYPE, IS, ttype_str, IAC, SE]
            self.log.debug('send IAC SB TTYPE IS {0!r} IAC SE'
                           .format(ttype_str))
            self.send_iac(b''.join(response))
            if self.pending_option.enabled(WILL + TTYPE):
                self.pending_option[WILL + TTYPE] = False

    def _handle_sb_env(self, buf):
        """ Callback handles (IAC, SB, NEW_ENVIRON, <buf>, SE), rfc1572.

            For requests beginning with IS, or subsequent requests beginning
            with INFO, any callback registered by ``set_ext_callback`` of
            cmd NEW_ENVIRON is passed a dictionary of (key, value) replied-to
            by client.

            For requests beginning with SEND, the callback registered by
            ``set_ext_send_callback`` is provided with a list of keys
            requested from the server; or None if only VAR and/or USERVAR
            is requested, indicating to "send them all".
        """
        cmd = buf.popleft()
        opt = buf.popleft()
        assert cmd == NEW_ENVIRON, name_command(cmd)
        assert opt in (IS, SEND, INFO), opt
        env = _decode_env_buf(b''.join(buf))
        opt_kind = 'IS' if opt == IS else 'INFO' if opt == INFO else 'SEND'
        self.log.debug('recv {} {}: {!r}'
                       .format(name_command(cmd), opt_kind, env))
        if opt in (IS, INFO):
            assert self.is_server, ('SE: cannot recv from server: {} {}'
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
                self.log.warn('{} IS already recv; expected INFO.'
                              .format(name_command(cmd)))
            if env:
                self._ext_callback[cmd](env)
        elif opt == SEND:
            assert self.is_client, ('SE: cannot recv from client: {} {}'
                                    .format(name_command(cmd), opt_kind))
            # We do _not_ honor the 'send all VAR' or 'send all USERVAR'
            # requests -- it is a small bit of a security issue.
            send_env = _encode_env_buf(
                self._ext_send_callback[NEW_ENVIRON](env.keys() or None))
            response = [IAC, SB, NEW_ENVIRON, IS, send_env, IAC, SE]
            self.log.debug('env send: {!r}'.format(response))
            self.send_iac(b''.join(response))
            if self.pending_option.enabled(WILL + TTYPE):
                self.pending_option[WILL + TTYPE] = False

    def _handle_sb_sndloc(self, buf):
        """ Fire callback for IAC-SB-SNDLOC-<buf>-SE (rfc779).
        """
        assert buf.popleft() == SNDLOC
        location_str = b''.join(buf).decode('ascii')
        self._ext_callback[SNDLOC](location_str)

    def _send_naws(self):
        """ XXX Fire callback for IAC-DO-NAWS and another one ??
            can a server send IAC-DO-NAWS anytime it wants to know,
            or an SB? TODO
        """
        rows, cols = self._ext_send_callback[NAWS]()

        # NAWS limits columns and rows to a size of 0-65535 (unsigned short)
        # >>> struct.unpack('!HH', b'\xff\xff\xff\xff')
        # (65535, 65535)
        rows, cols = max(min(65535, rows), 0), max(min(65535, cols), 0)

        #
        response = [IAC, SB, NAWS, struct.pack('!HH', cols, rows), IAC, SE]
        self.log.debug('send IAC SB NAWS (cols={0}, rows={1}) IAC SE'
                       .format(cols, rows))
        self.send_iac(b''.join(response))

    def _handle_sb_naws(self, buf):
        """ Fire callback for IAC-SB-NAWS-<buf>-SE (rfc1073).
        """
        cmd = buf.popleft()
        assert cmd == NAWS, name_command(cmd)
        assert len(buf) is 4, (
            'bad NAWS length {}: {!r}'.format(len(buf), buf)
        )
        assert self.remote_option.enabled(NAWS), (
            'received IAC SB NAWS without receipt of IAC WILL NAWS')
        cols, rows = struct.unpack('!HH', b''.join(buf))
        self.log.debug('recv IAC SB NAWS (cols={0}, rows={1}) IAC SE'
                       .format(cols, rows))
        self._ext_callback[NAWS](cols, rows)

    def _handle_sb_lflow(self, buf):
        """ Fire callback for IAC-SB-LFLOW-<buf>
        """ # XXX
        cmd = buf.popleft()
        assert cmd == LFLOW, name_command(cmd)
        assert self.local_option.enabled(LFLOW), (
            'received IAC SB LFLOW without IAC DO LFLOW')
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
            raise NotImplementedError(
                'Unknown IAC SB LFLOW option recieved: {!r}'.format(buf))

    def _handle_sb_status(self, buf):
        """ Fire callback for IAC-SB-STATUS-<buf>
        """
        cmd = buf.popleft()
        assert cmd == STATUS, name_command(cmd)
        sb = buf.popleft()
        if sb == SEND:
            self._send_status()
        elif sb == IS:
            self._receive_status(buf)

    def _receive_status(self, buf):
        for pos in range(len(buf) // 2):
            cmd = buf.popleft()
            try:
                opt = buf.popleft()
            except IndexError:
                # a remainder in division step-by-two,
                # presumed nonsense occurred.
                self.log.error('STATUS incomplete at pos {}, cmd: {}'
                               .format(pos, name_command(cmd)))
                break
            matching = False
            if cmd in (DO, DONT):
                enabled = self.local_option.enabled(opt)
                matching = ((cmd == DO and enabled) or
                            (cmd == DONT and not enabled))
                side = 'local'
            elif cmd in (WILL, WONT):
                enabled = self.remote_option.enabled(opt)
                matching = ((cmd == WILL and enabled) or
                            (cmd == WONT and not enabled))
                side = 'remote'
            else:
                self.log.error('STATUS invalid cmd at pos {}: {}.'
                               .format(pos, name_command(cmd)))
                break
            mode = 'enabled' if enabled else 'not enabled'
            if matching:
                self.log.debug('STATUS {} {} (agreed).'
                               .format(name_command(cmd), name_command(opt),))
            else:
                self.log.error('STATUS {cmd} {opt}: disagreed, '
                               '{side} option is {mode}.'.format(
                                   cmd=name_command(cmd),
                                   opt=name_command(opt),
                                   side=side, mode=mode))
                self.log.error('r {!r}|{}'.format(
                    [(name_command(_opt), _val)
                     for _opt, _val in self.remote_option.items()],
                    self.remote_option.enabled(_opt)))
                self.log.error('l {!r}|{}'.format(
                    [(name_command(_opt), _val)
                     for _opt, _val in self.local_option.items()],
                    self.local_option.enabled(_opt)))

    def _send_status(self):
        """ Fire callback for IAC-SB-STATUS-SEND (rfc859).
        """
        assert (self.pending_option.enabled(WILL + STATUS)
                or self.local_option.enabled(STATUS)
                ), ('Only sender of IAC WILL STATUS '
                    'may send IAC SB STATUS IS.')
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
        """ Callback handles IAC-SB-LINEMODE-<buf>.
        """
        cmd = buf.popleft()
        opt = buf.popleft()
        assert cmd == LINEMODE
        assert opt in (slc.LMODE_MODE, slc.LMODE_SLC, DO, DONT, WILL, WONT), (
            'Illegal IAC SB LINEMODE command, {!r}'.format(name_command(opt)))
        if opt == slc.LMODE_MODE:
            self._handle_sb_linemode_mode(buf)
        elif opt == slc.LMODE_SLC:
            self._handle_sb_linemode_slc(buf)
        elif opt in (DO, DONT, WILL, WONT):
            sb_opt = buf.popleft()
            assert sb_opt == slc.LMODE_FORWARDMASK, (
                'Illegal byte follows IAC SB LINEMODE {}: {!r}, '
                ' expected LMODE_FORWARDMASK.'
                .format(name_command(opt), sb_opt))

            self.log.debug('recv IAC SB LINEMODE {} LMODE_FORWARDMASK,'
                           .format(name_command(opt)))
            self._handle_sb_forwardmask(cmd, buf)

    def _handle_sb_linemode_mode(self, mode):
        """ Callback handles IAC-SB-LINEMODE-LINEMODE_MODE-<mode>.
        """
        assert len(mode) == 1

        suggest_mode = slc.Linemode(mode[0])

        self.log.debug('recv IAC SB LINEMODE LINEMODE-MODE {0!r} IAC SE'
                       .format(suggest_mode))

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

        if self.is_client:
            # " When a MODE command is received with the MODE_ACK bit set,
            #   and the mode is different that what the current mode is,
            #   the client will ignore the new mode"
            if self._linemode != suggest_mode:
                self.log.warn('server mode differs from local mode, '
                              'though ACK bit is set. Local mode will '
                              'remain.')
                self.log.warn('-remote: {0!r}'.format(suggest_mode))
                self.log.warn('+ local: {0!r}'.format(self._linemode))
            else:
                self.log.debug('Linemode matches, acknowledged by server.')
                self._linemode = suggest_mode
            return

        # as a server, we simply honor whatever is given.  This is also
        # problematic in some designers may wish to implement shells
        # that specifically do not honor some parts of the bitmask, we
        # must provide them an any/force-on/force-off mode table interface.
        if self._linemode != suggest_mode:
            self.log.debug('Client choses + {0!r}'.format(suggest_mode))
            self.log.debug('We suggested, - {0!r}'.format(self._linemode))
        else:
            self.log.debug('Linemode agreed by client: {0!r}'
                           .format(self._linemode))

        self._linemode = self._linemode


    def _handle_sb_linemode_slc(self, buf):
        """ Callback handles IAC-SB-LINEMODE-SLC-<buf>.

            Processes SLC command function triplets and replies accordingly.
        """
        assert 0 == len(buf) % 3, ('SLC buffer must be byte triplets')
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
        """ Send any pending SLC changes stored in _slc_buffer """
        if len(self._slc_buffer):

            self.log.debug('slc_end: {!r}'
                           .format(b''.join(self._slc_buffer)))
            self.transport.write(_escape_iac(b''.join(self._slc_buffer)))
            self._slc_buffer.clear()

        self.log.debug('slc_end: [..] IAC SE')
        self.send_iac(IAC + SE)

    def _slc_start(self):
        """ Send IAC SB LINEMODE SLC header """
        self.log.debug('slc_start: IAC SB LINEMODE SLC [..]')
        self.send_iac(IAC + SB + LINEMODE + slc.LMODE_SLC)

    def _slc_send(self, slctab=None):
        """
        Send supported SLC characters of current tabset,
        or tabset specified by argument slctab.
        """
        send_count = 0
        slctab = slctab or self.slctab
        for func in range(slc.NSLC + 1):
            if func is 0 and self.is_client:
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
        buffer slc triplet response as (function, flag, value),
        for the given SLC_func byte and slc_def instance providing
        byte attributes ``flag`` and ``val``. If no slc_def is provided,
        the slc definition of ``slctab`` is used by key ``func``.
        """
        assert len(self._slc_buffer) < _MAXSIZE_SLC, ('SLC: buffer full')
        if slc_def is None:
            slc_def = self.slctab[func]
        self.log.debug('_slc_add ({:<10} {})'.format(
            slc.name_slc_command(func) + ',', slc_def))
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
            self.log.warn('SLC not supported (out of range): ({!r})'
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
                self.log.warn('func(0) flag expected, got {}.'.format(slc_def))
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
        """ Update SLC tabset with SLC definition provided by remote end.

            Modify prviate attribute ``slctab`` appropriately for the level
            and value indicated, except for slc tab functions of SLC_NOSUPPORT.

            Reply as appropriate ..
        """
        hislevel, hisvalue = slc_def.level, slc_def.val
        mylevel, myvalue = self.slctab[func].level, self.slctab[func].val
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
        elif (slc_def.level == slc.SLC_CANTCHANGE
                and mylevel == slc.SLC_CANTCHANGE):
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
        """ Callback handles IAC-SB-LINEMODE-<cmd>-FORWARDMASK-<buf>.
        """
        # set and report about pending options by 2-byte opt,
        # not well tested, no known implementations exist !
        if self.is_server:
            assert self.remote_option.enabled(LINEMODE), (
                'cannot recv LMODE_FORWARDMASK {} ({!r}) '
                'without first sending DO LINEMODE.'
                .format(cmd, buf,))
            assert cmd not in (DO, DONT,), (
                'cannot recv {} LMODE_FORWARDMASK on server end'
                .format(name_command(cmd)))
        if self.is_client:
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
        """ Callback handles IAC-SB-LINEMODE-DO-FORWARDMASK-<buf>.
        """
        raise NotImplementedError  # TODO


class Option(dict):
    def __init__(self, name, log=logging):
        """ .. class:: Option(name : str, log: logging.logger)

            Initialize a Telnet Option database for capturing option
            negotation changes to ``log`` if enabled for debug logging.
        """
        self.name, self.log = name, log
        dict.__init__(self)

    def enabled(self, key):
        """ Returns True of option is enabled. """
        return bool(self.get(key, None) is True)

    def unhandled(self, key):
        """ Returns True of option is requested but unhandled by stream. """
        return bool(self.get(key, None) == -1)

    def __setitem__(self, key, value):
        if value != dict.get(self, key, None):
            descr = ' + '.join([name_command(bytes([byte]))
                                for byte in key[:2]
                                ] + [repr(byte) for byte in key[2:]])
            self.log.debug('{}[{}] = {}'.format(self.name, descr, value))
        dict.__setitem__(self, key, value)
    __setitem__.__doc__ = dict.__setitem__.__doc__

#: List of globals that may match an iac command option bytes
_DEBUG_OPTS = dict([(value, key)
                    for key, value in globals().items() if key in
                    ('LINEMODE', 'LMODE_FORWARDMASK', 'NAWS', 'NEW_ENVIRON',
                     'ENCRYPT', 'AUTHENTICATION', 'BINARY', 'SGA', 'ECHO',
                     'STATUS', 'TTYPE', 'TSPEED', 'LFLOW', 'XDISPLOC', 'IAC',
                     'DONT', 'DO', 'WONT', 'WILL', 'SE', 'NOP', 'DM', 'TM',
                     'BRK', 'IP', 'ABORT', 'AO', 'AYT', 'EC', 'EL', 'EOR',
                     'GA', 'SB', 'EOF', 'SUSP', 'ABORT', 'CMD_EOR', 'LOGOUT',
                     'CHARSET', 'SNDLOC', 'MCCP_COMPRESS', 'MCCP2_COMPRESS',
                     'ENCRYPT', 'AUTHENTICATION', 'TN3270E', 'XAUTH', 'RSP',
                     'COM_PORT_OPTION', 'SUPPRESS_LOCAL_ECHO', 'TLS',
                     'KERMIT', 'SEND_URL', 'FORWARD_X', 'PRAGMA_LOGON',
                     'SSPI_LOGON', 'PRAGMA_HEARTBEAT', 'EXOPL', 'X3PAD',
                     'VT3270REGIME', 'TTYLOC', 'SUPDUPOUTPUT', 'SUPDUP',
                     'DET', 'BM', 'XASCII', 'RCP', 'NAMS', 'RCTE', 'NAOL',
                     'NAOP', 'NAOCRD', 'NAOHTS', 'NAOHTD', 'NAOFFD', 'NAOVTS',
                     'NAOVTD', 'NAOLFD', )])


def name_command(byte):
    """ Given an IAC byte, return a mnemonic global constant, if any.
    """
    return (repr(byte) if byte not in _DEBUG_OPTS else _DEBUG_OPTS[byte])


def name_commands(cmds, sep=' '):
    """ Given array of telnet command bytes, return mnemonic global constants.
    """
    return sep.join([name_command(bytes([byte])) for byte in cmds])


def describe_stream_option_states(stream):
    local = stream.local_option
    remote = stream.remote_option
    pending = stream.pending_option
    status = collections.defaultdict(list)
    local_is = 'server' if stream.is_server else 'client'
    remote_is = 'server' if stream.is_client else 'client'
    if any(pending.values()):
        status.update({'failed-reply': [
            name_commands(opt) for (opt, val) in pending.items() if val]})
    if len(local):
        status.update({'local-{}'.format(local_is): [
            name_commands(opt) for (opt, val) in local.items()
            if local.enabled(opt)]})
    if len(remote):
        status.update({'remote-{}'.format(remote_is): [
            name_commands(opt) for (opt, val) in remote.items()
            if remote.enabled(opt)]})
    return dict(status)


def _escape_iac(buf):
    """ .. function:: _escape_iac(buf : bytes) -> type(bytes)

        Return byte array ``buf`` with IAC (\\xff)
        escaped as IAC IAC (\\xff\\xff).
    """
    assert isinstance(buf, (bytes, bytearray)), buf
    return buf.replace(IAC, IAC + IAC)


def _escape_env(buf):
    """ .. function:: escape_var(buf : bytes) -> type(bytes)

        Return byte array ``buf`` with VAR (\\x00) and USERVAR (\\x03)
        escaped as ESC VAR (\\x02\\x00) or ESC USERVAR (\\x02\\x03).
    """
    assert isinstance(buf, (bytes, bytearray)), buf
    return buf.replace(VAR, ESC + VAR).replace(USERVAR, ESC + USERVAR)


def _unescape_env(buf):
    """ .. function:: escape_var(buf : bytes) -> type(bytes)

        Return byte array ``buf`` with VAR (\\x00) and USERVAR (\\x03)
        escaped as ESC VAR (\\x02\\x00) or ESC USERVAR (\\x02\\x03).
    """
    assert isinstance(buf, (bytes, bytearray)), buf
    return buf.replace(ESC + VAR, VAR).replace(ESC + USERVAR, USERVAR)


def _decode_env_buf(buf):
    """ Returns dictionary of environment values contained in bytes array
        ``buf``, set forth in rfc1572, following sequence (IAC, SB,
        NEW_ENVIRON, SEND or IS) up to (IAC, SE).

        Values may have type of either USERVAR or VAR; This implementation
        does not distinguish between the two kinds.

        Environment values, oddly enough, may contain control characters,
        and those containing values USERVAR(\\x03) or VAR(\\x00), must have
        been escaped by ESC(\\x02), such as \\x02\\x03 or \\x02\\x00.
    """
    env = {}
    breaks = [idx for (idx, byte) in enumerate(buf)
              if bytes([byte]) in (VAR, USERVAR,) and
              (idx == 0 or bytes([buf[idx - 1]]) != ESC)]
    for start, end in zip(breaks, breaks[1:]):
        kind = bytes([buf[start]])
        pair = buf[start + 1:end].split(VALUE, 1)
        assert kind in (VAR, USERVAR), (kind, pair)
        key = _unescape_env(pair[0]).decode('ascii', 'ignore')
        if len(pair) == 1:
            value = ''
        else:
            value = _unescape_env(pair[1]).decode('ascii', 'ignore')
        env[key] = value
    return env


def _encode_env_buf(env):
    """ Returns bytes array ``buf`` for use in sequence (IAC, SB,
        NEW_ENVIRON, IS, <buf>, IAC, SE) as set forth in rfc1572.
    """
    buf = collections.deque()
    for key, value in env.items():
        buf.append(VAR)
        buf.extend([_escape_env(key.encode('ascii'))])
        buf.append(VALUE)
        buf.extend([_escape_env('{}'.format(value).encode('ascii'))])
    return b''.join(buf)
