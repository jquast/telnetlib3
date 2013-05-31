import collections
import logging
from telnetlib import LINEMODE, NAWS, NEW_ENVIRON, BINARY, SGA, ECHO, STATUS
from telnetlib import TTYPE, TSPEED, LFLOW, XDISPLOC, IAC, DONT, DO, WONT
from telnetlib import WILL, SE, NOP, TM, DM, BRK, IP, AO, AYT, EC, EL, EOR
from telnetlib import GA, SB, LOGOUT, EXOPL, CHARSET, SNDLOC, theNULL

import slc

__all__ = ['TelnetStreamReader', 'escape_iac', 'name_command', 'name_commands']

(EOF, SUSP, ABORT, EOR_CMD) = (
        bytes([const]) for const in range(236, 240))
(IS, SEND, INFO) = (bytes([const]) for const in range(3))
(LFLOW_OFF, LFLOW_ON, LFLOW_RESTART_ANY, LFLOW_RESTART_XON) = (
        bytes([const]) for const in range(4))
(REQUEST, ACCEPTED, REJECTED, TTABLE_IS, TTABLE_REJECTED,
    TTABLE_ACK, TTABLE_NAK) = (bytes([const]) for const in range(1, 8))

_MAXSIZE_SB = 2048
_MAXSIZE_SLC = slc.NSLC * 6

def escape_iac(buf):
    """ .. function:: escape_iac(buf : bytes) -> type(bytes)
        :noindex:

        Return byte buffer with IAC (\xff) escaped.
    """
    assert isinstance(buf, (bytes, bytearray)), buf
    return buf.replace(IAC, IAC + IAC)

class TelnetStreamReader:
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
    _default_env_request = (
            "USER HOSTNAME UID TERM COLUMNS LINES DISPLAY LANG SYSTEMTYPE "
            "ACCT JOB PRINTER SFUTLNTVER SFUTLNTMODE LC_ALL VISUAL EDITOR "
            "LC_COLLATE LC_CTYPE LC_MESSAGES LC_MONETARY LC_NUMERIC LC_TIME"
            ).split()
    default_slc_tab = slc.BSD_SLC_TAB
    default_codepages = ('UTF-8', 'UTF-16', 'US-ASCII', 'LATIN1', 'BIG5',
            'GBK', 'SHIFTJIS', 'GB18030', 'KOI8-R', 'KOI8-U',) + tuple(
                    'ISO8859-{}'.format(iso) for iso in range(16)) + tuple(
                          'CP{}'.format(cp) for cp in (
                              154, 437, 500, 737, 775, 850, 852, 855, 856, 857,
                             860, 861, 862, 863, 864, 865, 866, 869, 874,
                             875, 932, 949, 950, 1006, 1026, 1140, 1250,
                             1251, 1252, 1253, 1254, 1255, 1257, 1257, 1258,
                             1361, ))

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
        return bool(self._server)

    @property
    def is_client(self):
        """ Returns True if stream is used for client-end.  """
        return bool(not self._server)

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
        assert not client == False or not server == False, (
            "Arguments 'client' and 'server' are mutually exclusive")
        self.log = log
        self.transport = transport
        self._server = (client in (None, False) or server in (None, True))

        #: Total bytes sent to ``feed_byte()``
        self.byte_count = 0

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

        #: True when Flow Control (XON) has been recv, until receipt of XOFF.
        self.writing = True

        #: Sub-negotiation buffer
        self._sb_buffer = collections.deque()

        #: SLC buffer
        self._slc_buffer = collections.deque()

        #: SLC Tab (SLC Functions and their support level, and ascii value)
        self.slctab = slc.generate_slctab(self.default_slc_tab)

        #: Represents LINEMODE MODE neogtiated or requested by client.
        #  attribute ``ack`` returns True if it is in use.
        self._linemode = slc.Linemode()

        #: Initial line mode requested by server if client supports LINEMODE
        #  negotiation (remote line editing and literal echo of control chars)
        self.default_linemode = slc.Linemode(bytes([
            ord(slc.LMODE_MODE_REMOTE) | ord(slc.LMODE_MODE_LIT_ECHO)]))

        #: True if client sends IAC-SB-LINEMODE-DO-FORWARDMASK
        self._forwardmask_enabled = False

        # Set default callback handlers to local methods.
        self._iac_callback = {}
        for iac_cmd, key in (
                (BRK, 'brk'), (IP, 'ip'), (AO, 'ao'), (AYT, 'ayt'), (EC, 'ec'),
                (EL, 'el'), (EOF, 'eof'), (SUSP, 'susp'), (ABORT, 'abort'),
                (NOP, 'nop'), (DM, 'dm'), (GA, 'ga'), (EOR_CMD, 'eor'), ):
            self.set_iac_callback(iac_cmd,
                    getattr(self, 'handle_{}'.format(key)))

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
            self.set_slc_callback(slc_cmd,
                    getattr(self, 'handle_{}'.format(key)))

        self._ext_callback = {}
        for ext_cmd, key in (
                (TTYPE, 'ttype'), (TSPEED, 'tspeed'), (XDISPLOC, 'xdisploc'),
                (NEW_ENVIRON, 'env'), (NAWS, 'naws'), (LOGOUT, 'logout'),
                (SNDLOC, 'sndloc'), (CHARSET, 'charset'), ):
            self.set_ext_callback(ext_cmd,
                    getattr(self, 'handle_{}'.format(key)))

    def feed_byte(self, byte):
        """ .. method:: feed_byte(byte : bytes)

            Feed a single byte into Telnet option state machine.
        """
        assert isinstance(byte, (bytes, bytearray)), byte
        assert len(byte) == 1, byte
        self.byte_count += 1
        self._dm_recv = False
        self.slc_received = False
        # list of IAC commands needing 3+ bytes
        iac_mbs = (DO, DONT, WILL, WONT, SB)
        # cmd received is toggled false, unless its a msb.
        self.cmd_received = self.cmd_received in iac_mbs and self.cmd_received

        if byte == IAC:
            self.iac_received = (not self.iac_received)
            if not self.iac_received and self.cmd_received == SB:
                # SB buffer recvs escaped IAC values
                self._sb_buffer.append(IAC)

        elif self.iac_received and not self.cmd_received:
            # parse 2nd byte of IAC, even if recv under SB
            self.cmd_received = cmd = byte
            if cmd not in iac_mbs:
                # DO, DONT, WILL, WONT are 3-byte commands and
                # SB can be of any length. Otherwise, this 2nd byte
                # is the final iac sequence command byte.
                assert cmd in self._iac_callback, name_command(cmd)
                self._iac_callback[cmd](cmd)
            self.iac_received = False

        elif self.iac_received and self.cmd_received == SB:
            # parse 2nd byte of IAC while while already within
            # IAC SB sub-negotiation buffer, assert command is SE.
            self.cmd_received = cmd = byte
            if cmd != SE:
                self.log.warn('SB buffer interrupted by IAC {}'.format(
                    name_command(cmd)))
                self._sb_buffer.clear()
            else:
                self.log.debug('recv IAC SE')
                # sub-negotiation end (SE), fire handle_subnegotiation
                try:
                    self.handle_subnegotiation(self._sb_buffer)
                finally:
                    self._sb_buffer.clear()
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
            if cmd == DO:
                if self.handle_do(opt):
                    self.local_option[opt] = True
                    if self.pending_option.enabled(WILL + opt):
                        self.pending_option[WILL + opt] = False
            elif cmd == DONT:
                self.handle_dont(opt)
                if self.pending_option.enabled(WILL + opt):
                    self.pending_option[WILL + opt] = False
                self.local_option[opt] = False
            elif cmd == WILL:
                if not self.pending_option.enabled(DO + opt) and opt != TM:
                    self.log.debug('WILL {} unsolicited'.format(
                        name_command(opt)))
                self.handle_will(opt)
                if self.pending_option.enabled(DO + opt):
                    self.pending_option[DO + opt] = False
                if self.pending_option.enabled(DONT + opt):
                    self.pending_option[DONT + opt] = False
            elif cmd == WONT:
                self.handle_wont(opt)
                self.pending_option[DO + opt] = False
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
                        callback.__name__ if callback is not None else None))
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
            # any key after XOFF enables XON
            self._slc_callback[slc.SLC_XON]()

    def write(self, data, oob=False):
        """ .. method:: feed_byte(byte : bytes)

            Write data bytes to transport end connected to stream reader.
            Bytes matching IAC (\xff) is escabed by IAC IAC, unless oob=True.
        """
        assert isinstance(data, (bytes, bytearray)), repr(data)
        if not oob and not self.local_option.enabled(BINARY):
            for pos, byte in enumerate(data):
                assert byte < 128, (
                        'character value {} at pos {} not valid, send '
                        'IAC WILL BINARY first: {}'.format(byte, pos, data))
        self.transport.write(escape_iac(data))

    def send_iac(self, data):
        """ .. method: send_iac(self, data : bytes)

            No transformations of bytes are performed, Only complete
            IAC commands are legal.
        """
        assert isinstance(data, (bytes, bytearray)), data
        assert data and data.startswith(IAC), data
        self.transport.write(data)

    def iac(self, cmd, opt=None):
        """ .. method: iac(self, cmd : bytes, opt : bytes)

            Send Is-A-Command (IAC) 2 or 3-byte command option.

            Returns True if the command was actually sent. Not all commands
            are legal in the context of client, server, or negotiation state,
            emitting a relevant debug warning to the log handler.
        """
        short_iacs = (DM, SE)
        assert (cmd in (DO, DONT, WILL, WONT)
                or cmd in short_iacs and opt is None), (
                        'Uknown IAC {}.'.format(name_command(cmd)))
        if opt == LINEMODE:
            if cmd == DO and not self.is_server:
                raise ValueError('DO LINEMODE may only be sent by server.')
            if cmd == WILL and self.is_server:
                raise ValueError('WILL LINEMODE may only be sent by client.')
        if cmd == DO: # XXX any exclusions ?
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
        if cmd == WILL and opt != TM:
            if self.local_option.enabled(opt):
                self.log.debug('skip {} {}; local_option = True'.format(
                    name_command(cmd), name_command(opt)))
                self.pending_option[cmd + opt] = False
                return False
        if cmd == DONT and opt not in (LOGOUT,): # XXX any other exclusions?
            if self.remote_option.enabled(opt):
                # warning: some implementations incorrectly reply (DONT, opt),
                # for an option we already said we WONT. This would cause
                # telnet loops for implementations not doing state tracking!
                self.log.debug('skip {} {}; remote_option = True'.format(
                    name_command(cmd), name_command(opt)))
            self.remote_option[opt] = False
        elif cmd == WONT:
            self.local_option[opt] = False
        if cmd in short_iacs:
            self.log.debug('send IAC {}'.format(name_command(cmd)))
            self.send_iac(IAC + cmd)
        else:
            self.send_iac(IAC + cmd + opt)
            self.log.debug('send IAC {} {}'.format(
                name_command(cmd), name_command(opt)))

# Public methods for notifying about, or soliciting state options.
#
    def send_ga(self):
        """ .. method:: send_ga() -> bool

            Send IAC-GA (go ahead) only if IAC-DONT-SGA was received.
            Returns True if GA was sent.
        """
        if not self.local_option.enabled(SGA):
            self.send_iac(IAC + GA)
            return True

    def request_status(self):
        """ .. method:: request_status() -> bool

            Send IAC-SB-STATUS-SEND sub-negotiation (rfc859) only if
            IAC-WILL-STATUS has been received. Returns True if status
            request was sent.
        """
        if (self.remote_option.enabled(STATUS) and
                not self.pending_option.enabled(SB + STATUS)):
            self.send_iac(b''.join([IAC, SB, STATUS, SEND, IAC, SE]))
            self.pending_option[SB + STATUS] = True
            return True

    def request_tspeed(self):
        """ .. method:: request_tspeed() -> bool

            Send IAC-SB-TSPEED-SEND sub-negotiation (rfc1079) only if
            IAC-WILL-TSPEED has been received. Returns True if tspeed
            request was sent.
        """
        #   Does nothing if (WILL, TSPEED) has not yet been received.
        #   or an existing SB TSPEED SEND request is already pending. """
        if not self.remote_option.enabled(TSPEED):
            pass
        if not self.pending_option.enabled(SB + TSPEED):
            self.pending_option[SB + TSPEED] = True
            response = [IAC, SB, TSPEED, SEND, IAC, SE]
            self.log.debug('send: IAC SB TSPEED SEND IAC SE')
            self.send_iac(b''.join(response))
            return True

    def request_charset(self, codepages=None, sep=' '):
        """ .. method:: request_charset(codepages : list, sep : string) -> bool

            Request sub-negotiation CHARSET, rfc 2066. Returns True if request
            is valid for telnet state, and was sent.

            The sender requests that all text sent to and by it be encoded in
            one of character sets specifed by string list ``codepages``.
        """
        codepages = self.default_codepages if codepages is None else codepages
        if (self.remote_option.enabled(CHARSET) and
                not self.pending_option.enabled(SB + CHARSET)):
            self.pending_option[SB + CHARSET] = True

            response = collections.deque()
            response.extend([IAC, SB, CHARSET, REQUEST])
            response.extend([bytes(sep, 'ascii')])
            response.extend([bytes(sep.join(codepages), 'ascii')])
            response.extend([IAC, SE])
            self.log.debug('send: IAC SB CHARSET REQUEST {} IAC SE'.format(
                sep.join(codepages)))
            self.send_iac(b''.join(response))
            return True

    def request_env(self, env=None):
        """ .. method:: request_env(env : list) -> bool

            Request sub-negotiation NEW_ENVIRON, rfc 1572.
            Returns True if request is valid for telnet state, and was sent.

            ``env`` is list ascii uppercase keys of values requested. Default
            value is when unset is instance attribute ``_default_env_request``.
            Returns True if request is valid for telnet state, and was sent.
        """
        # May only be requested by the server end. Sends IAC SB ``kind``
        # SEND IS sub-negotiation, rfc1086, using list of ascii string
        # values ``self._default_env_request``, which is mostly variables
        # for impl.-specific extensions, such as TERM type, or USER for auth.
        request_ENV = self._default_env_request if env is None else env
        assert self.is_server
        kind = NEW_ENVIRON
        if not self.remote_option.enabled(kind):
            self.log.debug('cannot send SB {} SEND IS '
                'without receipt of WILL {}'.format(
                    name_command(kind), name_command(kind)))
            return False
        if self.pending_option.enabled(SB + kind + SEND + IS):
            self.log.debug('cannot send SB {} SEND IS, '
                'request pending.'.format(name_command(kind)))
            return False
        self.pending_option[SB + kind + SEND + IS] = True
        response = collections.deque()
        response.extend([IAC, SB, kind, SEND, IS])
        for idx, env in enumerate(request_ENV):
            response.extend([bytes(char, 'ascii') for char in env])
            if idx < len(request_ENV) - 1:
                response.append(theNULL)
        response.extend([b'\x03', IAC, SE])
        self.log.debug('send: {!r}'.format(b''.join(response)))
        self.send_iac(b''.join(response))
        return True

    def request_xdisploc(self):
        """ .. method:: request_xdisploc() -> bool

            Send XDISPLOC, SEND sub-negotiation, rfc1086.
            Returns True if request is valid for telnet state, and was sent.
        """
        if not self.remote_option.enabled(XDISPLOC):
            pass
        if not self.pending_option.enabled(SB + XDISPLOC):
            self.pending_option[SB + XDISPLOC] = True
            response = [IAC, SB, XDISPLOC, SEND, IAC, SE]
            self.log.debug('send: IAC SB XDISPLOC SEND IAC SE')
            self.send_iac(b''.join(response))
            return True

    def request_ttype(self):
        """ .. method:: request_ttype() -> bool

            Send TTYPE SEND sub-negotiation, rfc930.
            Returns True if request is valid for telnet state, and was sent.
        """
        if not self.remote_option.enabled(TTYPE):
            pass
        if not self.pending_option.enabled(SB + TTYPE):
            self.pending_option[SB + TTYPE] = True
            response = [IAC, SB, TTYPE, SEND, IAC, SE]
            self.log.debug('send: IAC SB TTYPE SEND IAC SE')
            self.send_iac(b''.join(response))
            return True

    def request_forwardmask(self, fmask=None):
        """ Request the client forward the control characters indicated
            in the Forwardmask class instance ``fmask``. When fmask is
            None, a forwardmask is generated for the SLC characters
            registered by ``slctab``.
        """
        assert self.is_server, (
                'DO FORWARDMASK may only be sent by server end')
        assert self.remote_option.enabled(LINEMODE), (
                'cannot send DO FORWARDMASK without receipt of WILL LINEMODE.')
        if fmask is None:
            fmask = slc.generate_forwardmask(
                    binary_mode=self.local_option.enabled(BINARY),
                    tabset=self.slctab, ack=self._forwardmask_enabled)

        assert isinstance(fmask, slc.Forwardmask), fmask
        self.pending_option[SB + LINEMODE] = True
        self.send_iac(IAC + SB + LINEMODE + DO + slc.LMODE_FORWARDMASK)
        self.write(fmask.value)  # escape IAC+IAC
        self.iac(SE)

        self.log.debug('send IAC SB LINEMODE DO LMODE_FORWARDMASK::')
        for maskbit_descr in fmask.__repr__():
            self.log.debug('  %s', maskbit_descr)

    def send_eor(self):
        """ .. method:: request_eor() -> bool

            Send IAC EOR_CMD (End-of-Record) only if IAC DO EOR was received.
            Returns True if request is valid for telnet state, and was sent.
        """
        if not self.local_option.enabled(EOR):
            self.send_iac(IAC + EOR_CMD)

    def send_lineflow_mode(self):
        """ .. method send_lineflow_mod() -> bool

        Send LFLOW mode sub-negotiation, rfc1372.
        """
        if not self.remote_option.enabled(LFLOW):
            return
        mode = LFLOW_RESTART_ANY if self.xon_any else LFLOW_RESTART_XON
        desc = 'LFLOW_RESTART_ANY' if self.xon_any else 'LFLOW_RESTART_XON'
        self.send_iac(b''.join([IAC, SB, LFLOW, mode, IAC, SE]))
        self.log.debug('send: IAC SB LFLOW %s IAC SE', desc)

    def send_linemode(self, linemode=None):
        """ Request the client switch to linemode ``linemode``, an
        of the Linemode class, or self._linemode by default.
        """
        assert self.is_server, (
                'SB LINEMODE LMODE_MODE cannot be sent by client')
        assert self.remote_option.enabled(LINEMODE), (
                'SB LINEMODE LMODE_MODE cannot be sent; '
                'WILL LINEMODE not received.')
        if linemode is not None:
            self.log.debug('Linemode is %s', linemode)
            self._linemode = linemode
        self.send_iac(IAC + SB + LINEMODE + slc.LMODE_MODE)
        self.write(self._linemode.mask)
        self.iac(SE)
        self.log.debug('sent IAC SB LINEMODE MODE %s IAC SE', self._linemode)

# Public is-a-command (IAC) callbacks
#
    def set_iac_callback(self, cmd, func):
        """ Register callable ``func`` as callback for IAC ``cmd``.

            BRK, IP, AO, AYT, EC, EL, EOR_CMD, EOF, SUSP, ABORT, and NOP.

            These callbacks receive a single argument, the IAC ``cmd`` which
            triggered it.
        """
        assert callable(func), ('Argument func must be callable')
        assert cmd in (BRK, IP, AO, AYT, EC, EL, EOR_CMD, EOF, SUSP,
                       ABORT, NOP, DM, GA), cmd
        self._iac_callback[cmd] = func

    def handle_nop(self, cmd):
        """ XXX Handle IAC No-Operation (NOP)
        """
        self.log.debug('IAC NOP: Null Operation')

    def handle_ga(self, cmd):
        """ XXX Handle IAC Go-Ahead (GA)
        """
        self.log.debug('IAC GA: Go-Ahead')

    def handle_dm(self, cmd):
        """ XXX Handle IAC Data-Mark (DM)

            Callback sets ``self._dm_recv``.  when IAC + DM is received.
            The TCP transport is not tested for OOB/TCP Urgent, so an old
            teletype half-duplex terminal may inadvertantly send unintended
            control sequences up until now,

            Oh well.  """
        self.log.debug('IAC DM: received')
        #: ``True`` if the last byte sent to ``feed_byte()`` was the end
        #  of an *IAC DM* has been received. MSG_OOB not implemented, so
        #  this mechanism _should not be implmeneted_.
        self._dm_recv = True
        #self.iac(DM)

# Public mixed-mode SLC and IAC callbacks
#
    def handle_el(self, byte):
        """ XXX Handle IAC Erase Line (EL) or SLC_EL.

            Provides a function which discards all the data ready on current
            line of input. The prompt should be re-displayed.
        """
        self.log.debug('IAC EL: Erase Line')

    def handle_eor(self, byte):
        """ XXX Handle IAC End of Record (EOR_CMD) or SLC_EOR.
        """
        self.log.debug('IAC EOR_CMD: End of Record')

    def handle_abort(self, byte):
        """ XXX Handle IAC Abort (ABORT) rfc1184, or SLC_ABORT.

            Similar to Interrupt Process (IP), but means only to abort or
            terminate the process to which the NVT is connected.
        """
        self.log.debug('IAC ABORT: Abort')

    def handle_eof(self, byte):
        """ XXX Handle End of Record (IAC, EOF), rfc1184 or SLC_EOF.
        """
        self.log.debug('IAC EOF: End of File')

    def handle_susp(self, byte):
        """ XXX Handle Suspend Process (SUSP), rfc1184 or SLC_SUSP.

            Suspends the execution of the current process attached to the NVT
            in such a way that another process will take over control of the
            NVT, and the suspended process can be resumed at a later time.
        """
        # If the receiving system does not support this functionality, it
        # should be ignored.
        self.log.debug('IAC SUSP: Suspend')

    def handle_brk(self, byte):
        """ XXX Handle IAC Break (BRK) or SLC_BRK (Break).

            Sent by clients to indicate BREAK keypress. This is not the same
            as IP (^c), but a means to map sysystem-dependent break key such
            as found on an IBM Systems.
        """
        self.log.debug('IAC BRK: Break')

    def handle_ayt(self, byte):
        """ XXX Handle IAC Are You There (AYT) or SLC_AYT.

            Provides the user with some visible (e.g., printable) evidence
            that the system is still up and running.
        """
        #   Terminal servers that respond to AYT usually print the status
        #   of the client terminal session, its speed, type, and options.
        self.log.debug('IAC AYT: Are You There?')

    def handle_ip(self, byte):
        """ XXX Handle IAC Interrupt Process (IP) or SLC_IP
        """
        self.log.debug('IAC IP: Interrupt Process')

    def handle_ao(self, byte):
        """ XXX Handle IAC Abort Output (AO) or SLC_AO.

            Discards any remaining output on the transport buffer.
        """
        #   "If the AO were received [...] a reasonable implementation would
        #   be to suppress the remainder of the text string, *but transmit the
        #   prompt character and the preceding <CR><LF>*."
        # XXX TODO: Must netsend()
        self.log.debug('IAC AO: Abort Output')
        pass
        #self.stream.discard_output()

    def handle_xon(self, byte):
        """ XXX handle Transmit-On (IAC, XON) or SLC_XON.

            Pauses writing to the transport.
        """
        self.log.debug('IAC XON: Transmit On')
        self.writing = True
        self.transport.resume_writing()

    def handle_ec(self, byte):
        """ XXX Handle IAC + SLC or SLC_EC (Erase Character).

            Provides a function which deletes the last preceding undeleted
            character from data ready on current line of input.
        """
        self.log.debug('IAC EC: Erase Character')

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
                0 < ord(slc_byte) < slc.NSLC + 1), (
                        'Uknown SLC byte: {!r}'.format(slc_byte))
        self._slc_callback[slc_byte] = func

    def handle_ew(self, slc):
        """ XXX Handle SLC_EW (Erase Word).

            Provides a function which deletes the last preceding undeleted
            character, and any subsequent bytes until next whitespace character
            from data ready on current line of input.
        """
        self.log.debug('IAC EC: Erase Word')

    def handle_rp(self, slc):
        """ Handle SLC Repaint.
        """ # XXX
        self.log.debug('SLC RP: Repaint')

    def handle_lnext(self, slc):
        """ Handle SLC LINE NEXT?
        """ # XXX
        self.log.debug('IAC LNEXT: Line Next')

    def handle_xoff(self, slc):
        """ Called when SLC_XOFF is received.
        """
        self.log.debug('IAC XOFF: Transmit Off')
        self.writing = False
        self.transport.pause_writing()

# public Telnet extension callbacks
#
    def set_ext_callback(self, cmd, func):
        """ Register ``func`` as callback for subnegotiation result of ``cmd``.

        cmd is one of: TTYPE, TSPEED, XDISPLOC, NEW_ENVIRON, NAWS, or CHARSET.

        These callbacks may receive a number of arguments.

        Callbacks for ``TTYPE``, ``XDISPLOC``, and ``CHARSET`` receive a
        single argument as a string. ``NEW_ENVIRON`` receives a single
        argument as dictionary. ``NAWS`` receives two integer arguments
        (width, height), and ``TSPEED`` receives two integer arguments
        (rx, tx).
        """
        assert cmd in (TTYPE, TSPEED, XDISPLOC,
                NEW_ENVIRON, NAWS, LOGOUT, CHARSET, SNDLOC), cmd
        assert callable(func), ('Argument func must be callable')
        self._ext_callback[cmd] = func

    def handle_xdisploc(self, xdisploc):
        """ XXX Receive XDISPLAY value ``xdisploc``, rfc1096.
        """
        #   xdisploc string format is '<host>:<dispnum>[.<screennum>]'.
        self.log.debug('X Display is {}'.format(xdisploc))

    def handle_sndloc(self, location):
        """ XXX Receive LOCATION value ``location``, rfc779.
        """
        self.log.debug('Location is {}'.format(location))

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
        self.log.debug('Terminal type is %r', ttype)

    def handle_naws(self, width, height):
        """ XXX Receive window size ``width`` and ``height``, rfc1073
        """
        self.log.debug('Terminal cols=%d, rows=%d', width, height)

    def handle_env(self, env):
        """ XXX Receive environment variables as dict, rfc1572.
        """
        self.log.debug('env=%r', env)

    def handle_tspeed(self, rx, tx):
        """ XXX Receive terminal speed from TSPEED as int, rfc1079
        """
        self.log.debug('Terminal Speed rx:{}, tx:{}'.format(rx, tx))

    def handle_location(self, location):
        """ XXX Receive terminal location from SNDLOC as string, rfc779.
        """
        self.log.debug('Terminal Location: {}'.format(location))

    def handle_charset(self, charset):
        """ XXX Receive character set from CHARSET as string, rfc2066
        """
        self.log.debug('Character set: {}'.format(charset))

    def handle_logout(self, cmd):
        """ XXX Handle (IAC, (DO | DONT | WILL | WONT), LOGOUT), RFC 727.

            Only the server end may receive (DO, DONT).
            Only the client end may receive (WILL, WONT).
            """
        # Close the transport on receipt of DO, Reply DONT on receipt
        # of WILL.  Nothing is done on receipt of DONT or WONT LOGOFF.
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

# public derivable methods DO, DONT, WILL, and WONT negotiation
#
    def handle_do(self, opt):
        """ XXX Process byte 3 of series (IAC, DO, opt) received by remote end.

        This method can be derived to change or extend protocol capabilities.
        The result of a supported capability is a response of (IAC, WILL, opt)
        and the setting of ``self.local_option[opt]`` of ``True``.
        """
        # For unsupported capabilities, RFC specifies a response of
        # (IAC, WONT, opt).  Similarly, set ``self.local_option[opt]``
        # to ``False``.
        #
        # This method returns True if the opt enables the willingness of the
        # remote end to accept a telnet capability, such as NAWS. It returns
        # False for unsupported option, or an option invalid in that context,
        # such as LOGOUT.
        self.log.debug('handle_do(%s)' % (name_command(opt)))
        if opt == ECHO and not self.is_server:
            self.log.warn('cannot recv DO ECHO on client end.')
        elif opt == LINEMODE and self.is_server:
            self.log.warn('cannot recv DO LINEMODE on server end.')
        elif opt == LOGOUT and self.is_server:
            self.log.warn('cannot recv DO LOGOUT on client end')
        elif opt == TM:
            self.iac(WILL, TM)
        elif opt == LOGOUT:
            self._ext_callback[LOGOUT](DO)
        elif opt in (ECHO, LINEMODE, BINARY, SGA, LFLOW, EXOPL, EOR):
            if not self.local_option.enabled(opt):
                self.iac(WILL, opt)
            return True
        elif opt == STATUS:
            if not self.local_option.enabled(opt):
                self.iac(WILL, STATUS)
            self._send_status()
            return True
        else:
            if self.local_option.get(opt, None) is None:
                self.iac(WONT, opt)
            self.log.warn('Unhandled: DO %s.' % (name_command(opt),))
        return False

    def handle_dont(self, opt):
        """ Process byte 3 of series (IAC, DONT, opt) received by remote end.

        This only results in ``self.local_option[opt]`` set to ``False``, with
        the exception of (IAC, DONT, LOGOUT), which only signals a callback
        to ``handle_logout(DONT)``.
        """
        self.log.debug('handle_dont(%s)' % (name_command(opt)))
        if opt == LOGOUT:
            assert self.is_server, ('cannot recv DONT LOGOUT on server end')
            self._ext_callback[LOGOUT](DONT)
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
        ``self._default_env_request``. All others are replied with DONT.

        The result of a supported capability is a response of (IAC, DO, opt)
        and the setting of ``self.remote_option[opt]`` of ``True``. For
        unsupported capabilities, RFC specifies a response of (IAC, DONT, opt).
        Similarly, set ``self.remote_option[opt]`` to ``False``.  """
        self.log.debug('handle_will(%s)' % (name_command(opt)))
        if opt in (BINARY, SGA, ECHO, NAWS, LINEMODE, EOR, SNDLOC):
            if opt == ECHO and self.is_server:
                raise ValueError('cannot recv WILL ECHO on server end')
            if opt in (NAWS, LINEMODE, SNDLOC) and not self.is_server:
                raise ValueError('cannot recv WILL %s on client end' % (
                    name_command(opt),))
            if not self.remote_option.enabled(opt):
                self.remote_option[opt] = True
                self.iac(DO, opt)
            if opt in (NAWS, LINEMODE, SNDLOC):
                self.pending_option[SB + opt] = True
                if opt == LINEMODE:
                    # server sets the initial mode and sends forwardmask,
                    self.send_linemode(self.default_linemode)
        elif opt == TM:
            if opt == TM and not self.pending_option.enabled(DO + TM):
                raise ValueError('cannot recv WILL TM, must first send DO TM.')
            self.log.debug('WILL TIMING-MARK')
            self.pending_option[DO + TM] = False
        elif opt == LOGOUT:
            if opt == LOGOUT and not self.is_server:
                raise ValueError('cannot recv WILL LOGOUT on server end')
            self._ext_callback[LOGOUT](WILL)
        elif opt == STATUS:
            self.remote_option[opt] = True
            self.request_status()
        elif opt == LFLOW:
            if opt == LFLOW and not self.is_server:
                raise ValueError('WILL LFLOW not supported on client end')
            self.remote_option[opt] = True
            self.send_lineflow_mode()
        elif opt == NEW_ENVIRON:
            self.remote_option[opt] = True
            self.request_env()
        elif opt == CHARSET:
            self.remote_option[opt] = True
            self.request_charset()
        elif opt == XDISPLOC:
            if opt == XDISPLOC and not self.is_server:
                raise ValueError('cannot recv WILL XDISPLOC on client end')
            self.remote_option[opt] = True
            self.request_xdisploc()
        elif opt == TTYPE:
            if opt == TTYPE and not self.is_server:
                raise ValueError('cannot recv WILL TTYPE on client end')
            self.remote_option[opt] = True
            self.request_ttype()
        elif opt == TSPEED:
            self.remote_option[opt] = True
            self.request_tspeed()
        else:
            self.remote_option[opt] = False
            self.iac(DONT, opt)
            raise ValueError('Unhandled: WILL %s.' % (name_command(opt),))

    def handle_wont(self, opt):
        """ Process byte 3 of series (IAC, WONT, opt) received by remote end.

        (IAC, WONT, opt) is a negative acknolwedgement of (IAC, DO, opt) sent.

        The remote end requests we do not perform a telnet capability.

        It is not possible to decline a WONT. ``T.remote_option[opt]`` is set
        False to indicate the remote end's refusal to perform ``opt``.
        """
        self.log.debug('handle_wont(%s)' % (name_command(opt)))
        if opt == TM and not self.pending_option.enabled(DO + TM):
            raise ValueError('WONT TM received but DO TM was not sent')
        elif opt == TM:
            self.log.debug('WONT TIMING-MARK')
            self.pending_option[DO + TM] = False
        elif opt == LOGOUT:
            assert not (self.is_server), (
                'cannot recv WONT LOGOUT on server end')
            if not self.pending_option(DO + LOGOUT):
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
        #   Changes to the default responses should replace the
        #   default callbacks ``handle_ttype``, ``handle_xdisploc``,
        #   ``handle_env``, and ``handle_naws``, by using
        #   ``set_ext_callback(opt_byte, func)``.
        #
        assert buf, ('SE: buffer empty')
        assert buf[0] != theNULL, ('SE: buffer is NUL')
        assert len(buf) > 1, ('SE: buffer too short: %r' % (buf,))
        cmd = buf[0]
        if self.is_server:
            assert cmd in (LINEMODE, LFLOW, NAWS, SNDLOC,
                NEW_ENVIRON, TTYPE, TSPEED, XDISPLOC, STATUS, CHARSET
                ), ('SB {}: not supported'.format(name_command(cmd)))
        if self.pending_option.enabled(SB + cmd):
            self.pending_option[SB + cmd] = False
        else:
            self.log.debug('[SB + %s] unsolicited', name_command(cmd))
        if cmd == LINEMODE: self._handle_sb_linemode(buf)
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
        elif (cmd, buf[1]) == (TTYPE, IS):
            self._handle_sb_ttype(buf)
        elif (cmd, buf[1]) == (TSPEED, IS):
            self._handle_sb_tspeed(buf)
        elif (cmd, buf[1]) == (XDISPLOC, IS):
            self._handle_sb_xdisploc(buf)
        elif (cmd, buf[1]) == (STATUS, SEND):
            self._send_status()
        else:
            raise ValueError('SE: unhandled: %r' % (buf,))


# Private sub-negotiation (SB) routines
#
    def _handle_sb_charset(self, buf):
        assert buf.popleft() == CHARSET
        cmd = buf.popleft()
        if cmd == REQUEST:
            #sep = buf.popleft()
            # client decodes 'buf', split by 'sep', and choses a charset
            raise NotImplementedError
        elif cmd == ACCEPTED:
            charset = b''.join(buf).decode('ascii')
            self._ext_callback[CHARSET](charset)
        elif cmd == REJECTED:
            self.log.info('Client rejects codepages')
        elif cmd in (TTABLE_IS, TTABLE_ACK, TTABLE_NAK, TTABLE_REJECTED):
            raise NotImplementedError
        else:
            raise ValueError("SB: unknown CHARSET command: {!r}".format(cmd))

    def _handle_sb_tspeed(self, buf):
        """ Callback handles IAC-SB-TSPEED-<buf>-SE.
        """
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
        self._ext_callback[TSPEED](int(rx), int(tx))

    def _handle_sb_xdisploc(self, buf):
        """ Callback handles IAC-SB-XIDISPLOC-<buf>-SE.
        """
        assert buf.popleft() == XDISPLOC
        assert buf.popleft() == IS
        xdisploc_str = b''.join(buf).decode('ascii')
        self.log.debug('sb_xdisploc: %s', xdisploc_str)
        self._ext_callback[XDISPLOC](xdisploc_str)

    def _handle_sb_ttype(self, buf):
        """ Callback handles IAC-SB-TTYPE-<buf>-SE.
        """
        assert buf.popleft() == TTYPE
        assert buf.popleft() == IS
        ttype_str = b''.join(buf).decode('ascii')
        self.log.debug('sb_ttype: %s', ttype_str)
        self._ext_callback[TTYPE](ttype_str)

    def _handle_sb_env(self, buf):
        """ Callback handles IAC-SB-NEWENVIRON-<buf>-SE (rfc1073).
        """
        assert len(buf) > 1, ('SE: buffer too short: %r' % (buf,))
        kind = buf.popleft()
        opt = buf.popleft()
        assert opt in (IS, INFO, SEND), opt
        assert kind == NEW_ENVIRON
        if opt == SEND:
            raise NotImplementedError # client
        if opt in (IS, INFO):
            assert self.is_server, ('SE: cannot recv from server: %s %s' % (
                name_command(kind), 'IS' if opt == IS else 'INFO',))
            if opt == IS:
                if not self.pending_option.enabled(SB + kind + SEND + IS):
                    self.log.debug('%s IS unsolicited', name_command(opt))
                self.pending_option[SB + kind + SEND + IS] = False
            if self.pending_option.get(SB + kind + SEND + IS, None) is False:
                # a pending option of value of 'False' means it previously
                # completed, subsequent environment values should have been
                # send as INFO ..
                self.log.debug('%s IS already recv; expected INFO.',
                        name_command(kind))
            breaks = list([idx for (idx, byte) in enumerate(buf)
                           if byte in (theNULL, b'\x03')])
            env = {}
            for start, end in zip(breaks, breaks[1:]):
                # not the best looking code, how do we splice & split bytes ..?
                decoded = bytes([ord(byte) for byte in buf]).decode('ascii')
                pair = decoded[start + 1:end].split('\x01', 1)
                if 2 == len(pair):
                    key, value = pair
                    env[key] = value
            self.log.debug('sb_env %s: %r', name_command(opt), env)
            if env:
                self._ext_callback[kind](env)
            return

    def _handle_sb_sndloc(self, buf):
        """ Callback handles IAC-SB-SNDLOC-<buf>-SE (rfc779).
        """
        location_str = b''.join(buf).decode('ascii')
        self._ext_callback[SNDLOC](location_str)

    def _handle_sb_naws(self, buf):
        """ Callback handles IAC-SB-NAWS-<buf>-SE (rfc1073).
        """
        assert buf.popleft() == NAWS
        columns = str((256 * ord(buf[0])) + ord(buf[1]))
        rows = str((256 * ord(buf[2])) + ord(buf[3]))
        self.log.debug('sb_naws: %s, %s', int(columns), int(rows))
        self._ext_callback[NAWS](int(columns), int(rows))

    def _handle_sb_lflow(self, buf):
        """ Callback handles IAC-SB-LFOW-<buf>
        """ # XXX
        assert buf.popleft() == LFLOW
        assert self.local_option.enabled(LFLOW), (
            'received IAC SB LFLOW wihout IAC DO LFLOW')
        raise NotImplementedError

    def _send_status(self):
        """ Callback handles IAC-SB-STATUS-SEND (rfc859).
        """
        assert (self.pending_option.enabled(WILL + STATUS)
                or self.local_option.enabled(STATUS)), (u'Only the sender '
                'of IAC WILL STATUS may send IAC SB STATUS IS.')
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
            name_command(byte) for byte in response]))
        self.send_iac(bytes([ord(byte) for byte in response]))
        if self.pending_option.enabled(WILL + STATUS):
            self.pending_option[WILL + STATUS] = False

# Special Line Character and other LINEMODE functions
#
    def _handle_sb_linemode(self, buf):
        """ Callback handles IAC-SB-LINEMODE-<buf>.
        """
        assert buf.popleft() == LINEMODE
        cmd = buf.popleft()
        if cmd == slc.LMODE_MODE:
            self._handle_sb_linemode_mode(buf)
        elif cmd == slc.LMODE_SLC:
            self._handle_sb_linemode_slc(buf)
        elif cmd in (DO, DONT, WILL, WONT):
            opt = buf.popleft()
            self.log.debug('recv SB LINEMODE %s FORWARDMASK%s.',
                    name_command(cmd), '(...)' if len(buf) else '')
            assert opt == slc.LMODE_FORWARDMASK, (
                    'Illegal byte follows IAC SB LINEMODE %s: %r, '
                    ' expected LMODE_FORWARDMASK.' (name_command(cmd), opt))
            self._handle_sb_forwardmask(cmd, buf)
        else:
            raise ValueError('Illegal IAC SB LINEMODE command, %r' % (
                name_command(cmd),))

    def _handle_sb_linemode_mode(self, mode):
        """ Callback handles IAC-SB-LINEMODE-MODE-<mode>.
        """
        assert len(mode) == 1
        self._linemode = slc.Linemode(mode[0])
        self.log.debug('Linemode MODE is %s.' % (self.mode,))

    def _handle_sb_linemode_slc(self, buf):
        """ Callback handles IAC-SB-LINEMODE-SLC-<buf>.

            Processes slc command function triplets and replies accordingly.
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
        """ Send any SLC pending SLC changes sotred in _slc_buffer """
        if 0 == len(self._slc_buffer):
            self.log.debug('slc_end: IAC SE')
        else:
            self.write(b''.join(self._slc_buffer), oob=True)
            self.log.debug('slc_end: (%r) IAC SE', b''.join(self._slc_buffer))
        self.send_iac(IAC + SE)
        self._slc_buffer.clear()

    def _slc_start(self):
        """ Send IAC SB LINEMODE SLC header """
        self.send_iac(IAC + SB + LINEMODE + slc.LMODE_SLC)
        self.log.debug('slc_start: IAC + SB + LINEMODE + SLC')

    def _slc_send(self):
        """ Send all special characters that are supported """
        send_count = 0
        for func in range(slc.NSLC + 1):
            if self.slctab[bytes([func])].nosupport:
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
            the slc definition of ``slctab`` is used by key ``func``.
        """
        assert len(self._slc_buffer) < _MAXSIZE_SLC, ('SLC: buffer full')
        if slc_def is None:
            slc_def = self.slctab[func]
        self.log.debug('_slc_add (%s, %s)',
            slc.name_slc_command(func), slc_def)
        self._slc_buffer.extend([func, slc_def.mask, slc_def.val])

    def _slc_process(self, func, slc_def):
        """ Process an SLC definition provided by remote end.

            Ensure the function definition is in-bounds and an SLC option
            we support. Store SLC_VARIABLE changes to self.slctab, keyed
            by SLC byte function ``func``.

            The special definition (0, SLC_DEFAULT|SLC_VARIABLE, 0) has the
            side-effect of replying with a full slc tabset, resetting to
            the default tabset, if indicated.  """
        self.log.debug('_slc_process {:<9} mine={}, his={}'.format(
                slc.name_slc_command(func), self.slctab[func], slc_def))

        # out of bounds checking
        if ord(func) > slc.NSLC:
            self.log.warn('SLC not supported (out of range): (%r)', func)
            self._slc_add(func, slc.SLC_nosupport())
            return

        # process special request
        if func == theNULL:
            if slc_def.level == slc.SLC_DEFAULT:
                # client requests we send our default tab,
                self.log.info('SLC_DEFAULT')
                self._default_slc(self._default_tabset)
                self._slc_send()
            elif slc_def.level == slc.SLC_VARIABLE:
                # client requests we send our current tab,
                self.log.info('SLC_VARIABLE')
                self._slc_send()
            else:
                self.log.warn('func(0) flag expected, got %s.', slc_def)
            return

        # evaluate slc
        mylevel, myvalue = (self.slctab[func].level, self.slctab[func].val)
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
        if self.is_server:
            assert self.remote_option.enabled(LINEMODE), (
                    'cannot recv LMODE_FORWARDMASK %s (%r) '
                    'without first sending DO LINEMODE.' % (cmd, buf,))
            assert cmd not in (DO, DONT), (
                    'cannot recv %s LMODE_FORWARDMASK on server end',
                    name_command(cmd,))
        if self.is_client:
            assert self.local_option.enabled(LINEMODE), (
                    'cannot recv %s LMODE_FORWARDMASK without first '
                    ' sending WILL LINEMODE.')
            assert cmd not in (WILL, WONT), (
                    'cannot recv %s LMODE_FORWARDMASK on client end',
                    name_command(cmd,))
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
        """ Callback handles IAC-SB-LINEMODE-DO-FORWARDMASK-<buf>.
        """ # XXX
        raise NotImplementedError

class Option(dict):
    def __init__(self, name, log=logging):
        """ .. class:: Option(name : str, log: logging.logger)

            Initialize a Telnet Option database for capturing option
            negotation changes to ``log`` if enabled for debug logging.
        """
        self.name, self.log = name, log
        dict.__init__(self)

    def enabled(self, key):
        """ Returns True of option is enabled."""
        return bool(self.get(key, None) is True)

    def __setitem__(self, key, value):
        if value != dict.get(self, key, None):
            descr = ' + '.join([name_command(bytes([byte]))
                for byte in key[:2]] + [repr(byte)
                    for byte in key[2:]])
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
                      'GA', 'SB', 'EOF', 'SUSP', 'ABORT', 'LOGOUT',
                      'CHARSET', 'SNDLOC')])

def name_command(byte):
    """ Given an IAC byte, return its mnumonic global constant. """
    return (repr(byte) if byte not in _DEBUG_OPTS
            else _DEBUG_OPTS[byte])

def name_commands(cmds, sep=' '):
    return ' '.join([
        name_command(bytes([byte])) for byte in cmds])

