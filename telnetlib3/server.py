#!/usr/bin/env python3
import collections
import traceback
import datetime
import argparse
import logging
import codecs
import shlex
import sys

import tulip
import telopt

__all__ = ['TelnetServer']

class TelnetServer(tulip.protocols.Protocol):
    """
        The banner() method is called on-connect, displaying the login banner,
        and indicates the desired telnet options. The default implementations
        sends only: iac(WILL, SGA), iac(WILL, ECHO), and iac(DO, TTYPE).

        The "magic sequence" WILL-SGA, WILL-ECHO enables 'kludge' mode,
        the most frequent 'simple' client implementation, and most compatible
        with cananical (line-seperated) processing, while still providing
        remote line editing for dumb clients. a client is still able to
        perform local line editing if it really is a line-oriented terminal.

        The negotiation DO-TTYPE is twofold: provide at least one option to
        negotiate to test the remote iac interpreter, (if any!). If the remote
        end replies in the affirmitive, then ``request_advanced_opts()`` is
        called.

        The reason all capabilities are not immediately announced is that
        the remote end may be too dumb to advance any further, and these
        additional negotiations can only serve to confuse the remote end
        or erroneously display garbage output if remote end is not equipped
        with an iac interpreter.
    """

    CONNECT_MINWAIT = 0.50
    CONNECT_MAXWAIT = 4.00
    CONNECT_DEFERED = 0.15
    TTYPE_LOOPMAX = 8

    def __init__(self, log=logging, default_encoding='utf8'):
        self.log = log
        self.client_env = {}
        self.show_traceback = True  # client sees full traceback
        self.strip_eol = '\r\n\00'

        self._default_encoding = default_encoding
        self._lastline = collections.deque()
        self._closing = False
        self._decoder = None
        self._last_received = None  # datetime timers,
        self._connected = None
        # toggled on fire of client WILL TTYPE
        self._advanced = False
        # toggled on ^v for raw input (SLC_LNEXT), '' until end of digit,
        self._literal = False
        self._lit_recv = False
        # track and strip CR[+LF|+NUL] in ``character_received``
        self._last_char = None
        self._encoding_errors = 'strict'
        self._on_encoding_err = 'replace'
    def banner(self):
        """ XXX Display login banner and solicit initial telnet options.
        """
        #   The default initially sets 'kludge' mode, which does not warrant
        #   any reply and is always compatible with any client NVT.
        #
        #   Notably, a request to negotiate TTYPE is made. If sucessful,
        #   the callback ``request_advanced_opts()`` is fired.
        self.echo ('Welcome to {}!\r\n'.format(__file__,))
        self.stream.iac(telopt.WILL, telopt.SGA)
        self.stream.iac(telopt.WILL, telopt.ECHO)
        self.stream.iac(telopt.DO, telopt.TTYPE)

    def echo(self, ucs, errors=None):
        """ Write unicode string to transport using preferred encoding.
        """
        #   If the stream is not in BINARY mode, the string must be made of
        #   strictly 7-bit ascii characters (valued less than 128). Otherwise,
        #   the session's preferred encoding is used (negotiated by CHARSET),
        #   or if unnegotiated, the server's default_encoding (utf-8).
        assert isinstance(ucs, str), ucs
        errors = self._encoding_errors if errors is None else errors
        try:
            self.stream.write(bytes(ucs, self.encoding, errors))
        except UnicodeDecodeError as err:
            # This could occur for instance if a non-compatible client is
            # transfering in binary when *DO BINARY* was not correctly
            # negotiated on their side. Send original string with
            # errors='replace', and only bell + TB if show_tracebacks is True.
            self._display_exception(*sys.exc_info(), level=logging.INFO)
            if self._encoding_errors != self._on_encoding_err:
                # warn each side once, brief to clientclient, terse to host.
                self.log.info('{}. encoding_errors is {!r}, was {!r}.'.format(
                    err, self._on_encoding_err, self._encoding_errors))
                self.echo('\r\n{} bad charset, translation now {!r}, was {!r}.'
                        .format(self.encoding, self._on_encoding_err,
                            self._encoding_errors))
                self._encoding_errors = self._on_encoding_err
            assert errors != 'replace', errors
            self.echo(ucs, errors='replace')

    def display_prompt(self, redraw=False):
        """ Prompts client end for input.  When ``redraw`` is ``True``, the
            prompt is re-displayed at the user's current screen row. GA
            (go-ahead) is signalled if SGA (supress go-ahead) is declined.
        """
        # display CRLF before prompt, or, when redraw only carriage return
        # without linefeed, then 'clear_eol' vt102 before prompt.
        parts = (('\r\x1b[K') if redraw else ('\r\n'),
                         self.prompt,
                         self.lastline,)
        self.echo(''.join(parts))
        self.stream.send_ga()


    def standout(self, string):
        """ XXX Return ``string`` decorated using 'standout' terminal sequence
            appropriate for the client end, if any. The default returns
            *CSI 0;1m* + string + *CSI 0m* when _does_styling is True
            (auto-toggled during TTYPE negotiation).
        """
        # A best-solution would be to spawn subprocesses, (as curses termcap
        # lookups are not thread-safe) to handle the client session,
        # initializing a termcap database using the value of 'TERM'. I highly
        # recommend the 'blessings' module, for ala Terminal('xterm').red('x')
        if self._does_styling:
            return '\x1b[0;1m' + string + '\x1b[0m'
        return string

    @property
    def lastline(self):
        """ Returns client command line as unicode string. """
        return u''.join([chr if chr.isprintable()
            else telopt._name_char(chr)
            for chr in self._lastline])


    @property
    def connected(self):
        """ Returns datetime connection was made. """
        return self._connected

    @property
    def duration(self):
        """ Returns seconds elapsed since client connected. """
        return (datetime.datetime.now() - self._connected).total_seconds()

    @property
    def idle(self):
        """ Returns seconds elapsed since last received any data.
        """
        return (datetime.datetime.now() - self._last_received).total_seconds()

    @property
    def input_idle(self):
        """ Returns seconds elapsed since last received inband data.
        """
        return (datetime.datetime.now() - self._last_received).total_seconds()


    @property
    def prompt(self):
        """ Returns string suitable for display_prompt(). This implementation
            evaluates PS1 to a completed string, otherwise returns '$ '.
        """
        return u'% '

    @property
    def encoding(self):
        """ Returns the session's preferred encoding.

            Always 'ascii' unless BINARY has been negotiated, then the
            session value CHARSET is used, or constructor keyword
            argument ``default_encoding`` if undefined.
        """
        return (self.client_env.get('CHARSET', self._default_encoding)
                if self.stream.local_option.get(telopt.BINARY, None)
                else 'ascii')

    @property
    def retval(self):
        """ Returns exit status of last command processed by ``line_received``
        """
        return self._retval


    @property
    def is_literal(self):
        """ Returns True if the SLC_LNEXT character (^v) was recieved, and
            any subsequent character should be received as-is; this is for
            inserting raw sequences into a command line that may otherwise
            interpret them not printable, or a special line editing character.
        """
        return not self._literal is False

    @is_literal.setter
    def is_literal(self, value):
        assert isinstance(value, (str, bool)), value
        self._literal = value

    def bell(self):
        """ Callback when inband data is not valid during remote line editing.

            Default impl. writes ASCII BEL to transport if stream if it is
            in kludge mode or remote editing is enabled with flag 'lit_echo'.
        """
        if not self.stream.is_linemode or (
                not self.stream.linemode.local
                and self.stream.linemode.lit_echo):
            self.echo('\x07')

    def interrupt_received(self, cmd):
        """ This method aborts any output waiting on transport, then calls
            ``prompt()`` to solicit a new command, retaining the existing
            command buffer, if any.

            This is suitable for the receipt of interrupt signals, or for
            iac(AO) and SLC_AO.
        """
        self.transport.discard_output()
        self.log.debug(telopt._name_command(cmd))
        self.echo('\r\n ** {}'.format(telopt._name_command(cmd)))
        self.display_prompt()

    def character_received(self, char):
        """ XXX Callback receives a single Unicode character as it is received.

            The default takes a 'most-compatible' implementation, providing
            'kludge' mode with simulated remote editing for inadvanced clients.
        """
        # This impl Optionally allows input of raw characters when
        #   ``next_is_literal`` is toggled True by ``literal_received``.
        # Fires callback ``line_received(self.lastline)`` on carriage
        #   return (CR), or linefeed (LF) not preceeded by CR.
        # Compatible with all 4 "send" keys, bsd client may toggle in and
        #   back out of binary mode, and toggle 'crlf' out of binary mode,
        #   and ^J for LF for testing all 4; a poorly implemented client may
        #   not be able to 'switch' CR kind, or agree to use the correct one,
        #   so we act as forgiving as possible.
        # Caveat: no distinction between CR, LF, CR LF, or CR NUL may be
        #   done by the callback ``line_received``, esp. as it is fired upon
        #   receipt of CR with remaining LF or NUL unreceived.
        CR, LF, NUL = '\r\n\x00'
        if self.is_literal:
            self._lastline.append(char)
            if not char.isprintable():
                self.echo(self.standout(telopt._name_char(char)))
            else:
                self.echo(char)
            return
        if self._last_char == CR and char in (LF, NUL):
            if not self.strip_eol:
                self._lastline.append(char)
            else:
                return
        if char in (CR, LF,):
            if not self.strip_eol:
                self._lastline.append(char)
            if char == CR or self.strip_eol:
                self.line_received(self.lastline)
            return
        if not char.isprintable():
            self.bell()
        else:
            self._lastline.append(char)
            if self.stream.local_option.get(telopt.ECHO, None) == True:
                self.echo(char)

        self._last_char = char

    def line_received(self, input, eor=False):
        """ XXX Callback for each telnet input line received.
        """
        #   The default implementation splits ``input`` using shell-like
        #   syntax, and passed as (cmd, *args) to ``process_cmd``, storing
        #   the success value as ``retval``.
        self.log.debug('line_received: {!r}'.format(input))
        if self.strip_eol:
            input = input.rstrip(self.strip_eol)
        try:
            self._retval = self.process_cmd(input)
        except Exception as err:
            self._retval = -1
            self.bell()
            self._display_exception(*sys.exc_info(), level=logging.INFO)
            err #  pyflakes
        finally:
            self._lastline.clear()
            self.display_prompt()

    def _display_exception(self, *exc_info, level=logging.DEBUG):
        """ Dispaly exception to client when ``show_traceback`` is True,
            forward copy server log at debug and info levels.
        """
        tbl_exception = traceback.format_exception(*exc_info)
        for num, tb in enumerate(tbl_exception):
            tb_msg = tb.splitlines()
            if self.show_traceback:
                self.echo('\r\n' + '\r\n\t'.join(
                    self.standout(row.rstrip())
                    if num == len(tbl_exception) - 1
                    else row.rstrip() for row in tb_msg))
            tbl_srv = [row.rstrip() for row in tb_msg]
            for line in tbl_srv:
                logging.log(level, line)

    def data_received(self, data):
        """ Process each byte as received by transport.

            Derived impl. should instead extend or override the
            ``line_received()`` and ``char_received()`` methods.
        """
        # Raw transport bytes received are sent to the ``feed_byte()``
        # method of the session's TelnetStreamReader instance. Callbacks
        # registered in ``set_callbacks()`` are fired upon completion of
        # iac sequences.
        #
        # If a carriage return is received on input, the ``line_received``
        # callback is fired. When special linemode characters (SLCs) are
        # received, the callback ``editing_received`` is fired with the
        # SLC function byte. Other inband data is decoded using the
        # session-preferred encoding. Callback ``char_received`` receives
        # a decoded string of length 1 upon completion of any possiblly
        # multibyte input sequence.
        self.log.debug('data_received: {!r}'.format(data))
        self._last_received = datetime.datetime.now()
        for byte in (bytes([value]) for value in data):
            self.stream.feed_byte(byte)
            if self.stream.is_oob:
                continue  # stream processed an IAC command,
            elif self.stream.slc_received:
                self.editing_received(byte, self.stream.slc_received)
            else:
                ucs = self.decode(byte, final=False)
                if ucs is not None and ucs != '':
                    if self.is_literal is not False:
                        self.literal_received(ucs)
                    else:
                        self.character_received(ucs)

    def literal_received(self, ucs):
        """ Receives literal character(s) SLC_LNEXT (^v) and all subsequent
            characters until the boolean toggle ``_literal`` is set False.
        """
        self.log.debug('literal_received: {}'.format(telopt._name_char(ucs)
            if not ucs.isprintable() else ucs))
        literval = 0 if self._literal is '' else int(self._literal)
        new_lval = 0
        if self._literal is False:  # ^V or SLC_VLNEXT
            self.echo('^\b')
            self._literal = ''
            return
        elif ord(ucs) < 32:  # Control character
            if self._lit_recv:
                self.character_received(chr(literval))
            self.character_received(ucs)
            self._lit_recv, self._literal = 0, False
            return
        elif ord('0') <= ord(ucs) <= ord('9'):  # base10 digit
            self._literal += ucs
            self._lit_recv += 1
            new_lval = int(self._literal)
            if new_lval >= 255 or self._lit_recv == len('255'):
                self.character_received(chr(min(new_lval, 255)))
                self._lit_recv, self._literal = 0, False
            return
        # printable character
        if self._lit_recv:
            self.character_received(chr(literval))
        if ucs not in ('\r', '\n'):
            # newline after digits are ignored,
            self.character_received(ucs)
        self._lit_recv, self._literal = 0, False

    def editing_received(self, char, slc):
        self.log.debug('editing_received: {}, {}.'.format(
            telopt._name_char(char), telopt._name_slc_command(slc),))
        if self.is_literal is not False:  # continue literal
            ucs = self.decode(char)
            if ucs is not None:
                self.literal_received(ucs)
        elif slc == telopt.SLC_LNEXT:  # literal input (^v)
            ucs = self.decode(char)
            if ucs is not None:
                self.literal_received(ucs)
        elif slc == telopt.SLC_RP:  # repaint (^r)
            self.display_prompt(redraw=True)
        elif slc == telopt.SLC_EC:  # erase character chr(127)
            if 0 == len(self._lastline):
                self.bell()
            else:
                self._lastline.pop()
            self.display_prompt(redraw=True)
        elif slc == telopt.SLC_EW:  # erase word (^w)
            # erase over .(\w+)
            removed = 0
            while (self.lastline) and not (removed
                    or not self._lastline[-1].isspace()):
                self._lastline.pop()
                removed += 1
            if not removed:
                self.bell()
            else:
                self.display_prompt(redraw=True)
        elif slc == telopt.SLC_EL:
            # erase line (^L)
            self._lastline.clear()
            self.display_prompt(redraw=True)
        else:
            self.echo('\r\n ** {} **'.format(
                telopt._name_slc_command(slc).split('_')[-1]))
            self._lastline.clear()
            self.display_prompt()

    def process_cmd(self, input):
        """ Simple shell-like command processing interface.

            This is used with the default ``line_received`` callback to
            provide commands and command help. Returns exit/success
            value as integer, 0 is success, non-zero is failure.

            If ``show_traceback`` is enabled, exceptions that occur during
            command line processing are displayed to the user.
        """
        cmd, args = input.rstrip(), []
        if ' ' in cmd:
            cmd, *args = shlex.split(cmd)
        self.log.debug('process_cmd {!r}{!r}'.format(cmd, args))
        if cmd == 'help':
            self.echo('\r\nAvailable commands, command -h for help:\r\n')
            self.echo('quit, echo, set, toggle, status')
            return 0 if not args or args[0] in ('-h', '--help',) else 1
        elif cmd == 'quit':
            if len(args):
                self.echo('\r\nquit: close session.')
                return 0 if args[0] in ('-h', '--help',) else 1
            return self.logout()
        elif cmd == 'status':
            if args:
                self.echo('\r\nstatus: displays session parameters')
                return 0 if args[0] in ('-h', '--help',) else 1
            self.display_status()
            return 0
        else:
            self.echo('\r\nCommand {!r} not understood.'.format(cmd))
            return 1

    def decode(self, input, final=False):
        """ Decode bytes sent by client using preferred encoding.

            Wraps the ``decode()`` method of a ``codecs.IncrementalDecoder``
            instance using the session's preferred ``encoding``.

            If the preferred encoding is not valid, the class constructor
            keyword ``default_encoding`` is used, the 'CHARSET' environment
            value is reverted, and the client
        """
        if self._decoder is None or self._decoder._encoding != self.encoding:
            try:
                self._decoder = codecs.getincrementaldecoder(self.encoding)()
            except LookupError as err:
                assert self.encoding != self._default_encoding, (
                        self._default_encoding, err)
                self.log.warn(err)
                self._env_update({'CHARSET': self._default_encoding})
                self._decoder = codecs.getincrementaldecoder(self.encoding)()
                # interupt client session to notify change of encoding,
                self.echo('{}, CHARSET is {}.'.format(err, self.encoding))
                self.display_prompt()
            self._decoder._encoding = self.encoding
        return self._decoder.decode(input, final)

    def connection_made(self, transport):
        """ Receive a new telnet client connection.

            A new TelnetStreamReader is instantiated for the transport,
            and various IAC, SLC, and extended callbacks are registered.
            Then, ``banner()`` is fired.

            An authenticating server should override the ``banner()``
            method to initialize auth state tracking for the
            ``line_received`` callback.
        """
        self.transport = transport
        self.stream = telopt.TelnetStreamReader(transport, server=True)
        self._last_received = datetime.datetime.now()
        self._connected = datetime.datetime.now()
        self._retval = 0
        self.set_callbacks()
        self.banner()
        self._negotiate()

    def request_advanced_opts(self, ttype=True):
        """ XXX Request advanced telnet options.

        Once the remote end has been identified as capable of at least TTYPE,
        this callback is fired a single time. This is the preferred method
        of delaying advanced negotiation attempts only for those clients deemed
        intelligent enough to attempt, as some non-compliant clients may crash
        or close connection.

        Request additional TTYPE responses from clients who have replied
        already, allowing a 'looping' mechanism by which MUD clients may be
        identified, or at least all possible (Kermit claims 30) ttypes are
        logged.
        """
        self.stream.iac(telopt.DO, telopt.LINEMODE)
        self.stream.iac(telopt.WILL, telopt.STATUS)
        self.stream.iac(telopt.WILL, telopt.LFLOW)
        self.stream.iac(telopt.DO, telopt.NEW_ENVIRON)
        self.stream.iac(telopt.DO, telopt.NAWS)
        self.stream.iac(telopt.DO, telopt.CHARSET)
        self.stream.iac(telopt.DO, telopt.TTYPE)
        if ttype and self.stream.remote_option.get('TTYPE', None):
            # we've already accepted their ttype, but see what else they have!
            self.stream.request_ttype()

    def display_status_then_prompt(self, *args):
        self.display_status()
        self.display_prompt()

    def display_status(self):
        """ Output the status of telnet session.
        """
        self.echo('\r\nConnected {}s ago from {} (latency is {:0.3f}s.),'
            '\r\nLinemode is {} ({}).\r\nFlow control is {}.'
            '\r\nEncoding is {}.'.format(
                self.duration,
                self.transport.get_extra_info('addr', 'unknown'),
                'ENABLED' if self.stream.is_linemode else 'DISABLED',
                self.stream.linemode if self.stream.is_linemode else 'kludge',
                'xon-any' if self.stream.xon_any else 'xon',
                self.encoding))

        if not self.stream.is_linemode:
            self.echo('\r\nInput is full duplex (kludge) mode.')
        else:
            self.echo('\r\nLinemode is {0}.'.format(self.stream.linemode))

    def set_callbacks(self):
        """ XXX Register callbacks with TelnetStreamReader

        The default implementation wires several IAC, SLC, and extended
        RFC negotiation options to local handling functions. This indicates
        our desire to be notified by callbacks for additional signals than
        just ``line_received``.  """
        # wire AYT and SLC_AYT (^T) to callback ``status()``
        self.stream.set_iac_callback(telopt.AYT,
                self.display_status_then_prompt)
        self.stream.set_slc_callback(telopt.SLC_AYT,
                self.display_status_then_prompt)

        # wire various 'interrupts', such as AO, IP to ``abort_output``
        self.stream.set_iac_callback(telopt.AO, self.interrupt_received)
        self.stream.set_iac_callback(telopt.IP, self.interrupt_received)
        self.stream.set_iac_callback(telopt.BRK, self.interrupt_received)
        self.stream.set_iac_callback(telopt.SUSP, self.interrupt_received)
        self.stream.set_iac_callback(telopt.ABORT, self.interrupt_received)

        # wire extended rfc callbacks for terminal type, dimensions
        self.stream.set_ext_callback(telopt.NEW_ENVIRON, self._env_update)
        self.stream.set_ext_callback(telopt.TTYPE, self._ttype_received)
        self.stream.set_ext_callback(telopt.NAWS, self._naws_update)

    def _env_update(self, env):
        " Callback receives no environment variables "
        if 'TERM' in env and env['TERM'] != env['TERM'].lower():
            self.log.debug('{!r} -> {!r}'.format(env['TERM'],
                env['TERM'].lower()))
            env['TERM'] = env['TERM'].lower()
        self.client_env.update(env)
        self.log.debug('env_update: %r', env)

    def _charset_received(self, charset):
        " Callback receives CHARSET value, rfc2066 "
        self._env_update({'CHARSET': charset.lower()})

    def _naws_update(self, width, height):
        " Callback receives NAWS values, rfc1073 "
        self._env_update({'COLUMNS': str(width), 'LINES': str(height)})

    def _xdisploc_received(self, xdisploc):
        " Callback receives XDISPLOC value, rfc1096 "
        self._env_update({'DISPLAY': xdisploc})

    def _tspeed_received(self, rx, tx):
        " Callback receives TSPEED values, rfc1079 "
        self._env_update({'TSPEED': '%s,%s' % (rx, tx)})

    def _negotiate(self, call_after=None):
        """
        Negotiate options before prompting for input, this method calls itself
        every CONNECT_DEFERED up to the greater of the value CONNECT_MAXWAIT.

        Negotiation completes when all ``pending_options`` of the
        TelnetStreamReade have completed. Any options not negotiated
        are displayed to the client as a warning, and ``display_prompt()``
        is called for the first time, unless ``call_after`` specifies another
        callback.
        """
        if call_after is None:
            call_after = self.display_prompt
        assert callable(call_after), call_after

        loop = tulip.get_event_loop()
        pending = [telopt._name_commands(opt)
                for (opt, val) in self.stream.pending_option.items()
                if val]

        if self.duration < self.CONNECT_MINWAIT or (
                pending and self.duration < self.CONNECT_MAXWAIT):
            loop.call_later(self.CONNECT_DEFERED, self._negotiate, call_after)
            return
        elif pending:
            self.log.warn('negotiate failed for {}.'.format(pending))
            self.echo('\r\nnegotiate failed for {}.'.format(pending))
        loop.call_soon(call_after)

    def _ttype_received(self, ttype):
        """ Callback for TTYPE response.

        The first firing of this callback signals an advanced client and
        is awarded with additional opts by ``request_advanced_opts()``.

        Otherwise the session variable TERM is set to the value of ``ttype``.
        """
        if not self._advanced:
            self.log.info('TTYPE is {}, latency {:0.3f}s.'.format(
                ttype, self.duration))
            if not 'TERM' in self.client_env:
                self._env_update({'TERM': ttype})
            # track TTYPE seperately from the NEW_ENVIRON 'TERM' value to
            # avoid telnet loops in TTYPE cycling
            self._env_update({'TTYPE0': ttype})
            self.request_advanced_opts()
            self._advanced = 1
            return

        # Soliciting additional TTYPE responses, so that a termcap-compatible
        # TERM value can be determined from a greater variaty of telnet
        # clients, rotating available TERM until it is repeated.
        #
        # This retrieves 'xterm256-color' from MUD or real xterms, regardless
        # of wether they're fully implementing. But this is the closest we'll
        # get to an appropriate definition of terminal capabilities we would
        # be most interested in.
        self._env_update({'TTYPE{}'.format(self._advanced): ttype})
        if ttype == self.client_env['TTYPE0']:
            ttype = ttype.lower()
            self._env_update({'TERM': ttype})
            self.logger.debug('end on TTYPE{}: {}.'.format(
                self._advanced, ttype))
        elif self._advanced > self.TTYPE_LOOPMAX:
            ttype = self.client_env['TERM'].lower()
            self._env_update({'TERM': ttype})
            self.log.warn('TTYPE stop on {}, using {}.'.format(
                self._advanced, ttype))
        ttype = ttype.lower()
        self.stream.request_ttype()
        self._does_styling = (
                ttype.startswith('vt') or ttype.startswith('xterm')
                or ttype.startswith('dtterm') or ttype.startswith('rxvt')
                or ttype.startswith('shell') or ttype.startswith('ansi'))
        self._advanced += 1

    def logout(self, opt=telopt.DO):
        if opt != telopt.DO:
            return self.stream.handle_logout(opt)
        self.log.debug('Logout by client.')
        self.echo('\r\nLogout by client.\r\n')
        self.close()

    def eof_received(self):
        self._eof = True
        self.log.info('Connection closed by client, {}.'.format(
                self.transport.get_extra_info('addr', None)))

    def close(self):
        if not self._eof:
            self.log.info('Connection closed by server, {}.'.format(
                    self.transport.get_extra_info('addr', None)))
        self.transport.close ()
        self._closing = True

ARGS = argparse.ArgumentParser(description="Run simple telnet server.")
ARGS.add_argument(
    '--host', action="store", dest='host',
    default='127.0.0.1', help='Host name')
ARGS.add_argument(
    '--port', action="store", dest='port',
    default=6023, type=int, help='Port number')
ARGS.add_argument(
    '--loglevel', action="store", dest="loglevel",
    default='info', type=str, help='Loglevel (debug,info)')

def main():
    import logging
    import locale
    args = ARGS.parse_args()
    if ':' in args.host:
        args.host, port = args.host.split(':', 1)
        args.port = int(port)
    locale.setlocale(locale.LC_ALL, '')
    enc = locale.getpreferredencoding()
    log = logging.getLogger()
    log_const = args.loglevel.upper()
    assert (log_const in dir(logging)
            and isinstance(getattr(logging, log_const), int)
            ), args.loglevel
    log.setLevel(getattr(logging, log_const))
    log.debug('default_encoding is {}'.format(enc))

    loop = tulip.get_event_loop()
    func = loop.start_serving(lambda: TelnetServer(default_encoding=enc),
            args.host, args.port)

    for sock in loop.run_until_complete(func):
        # XXX --- we could set our socket OOBINLINE, and recieve IAC+DM as
        # a normally interpreted signal, and find some way to go about
        # ignoring it; but the only impl. I've found is BSD Client, which
        # appears to lock up after 'send synch' --- XXX
        #sock.setsockopt(socket.SOL_SOCKET, socket.SO_OOBINLINE, 1)
        logging.info('Listening on %s', sock.getsockname())
    loop.run_forever()

if __name__ == '__main__':
    main()

