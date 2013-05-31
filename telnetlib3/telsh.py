import collections
import traceback
import logging
import codecs
import shlex
import re
import sys

import telopt

__all__ = ['TelnetShellStream', 'Telsh']

class TelnetShellStream():
    def __init__(self, server):
        self.server = server
        #: codecs.IncrementalDecoder for current CHARSET
        self._decoder = None
        #: default encoding 'errors' argument
        self.encoding_errors = 'replace'

    def _display_charset_err(self, err):
        errmsg = bytes(err.args[0].encode(self.server.encoding(outgoing=True)))
        charset = bytes(self.server.env['CHARSET'].encode(
            self.server.encoding(outgoing=True)))
        self.server.stream.write(
                b''.join((b'\r\n', errmsg, ', CHARSET is ', charset, '.\r\n')))

    def send_ga(self):
        self.server.stream.send_ga()

    def write(self, string, errors=None):
        """ Write string to output using preferred encoding.
        """
        errors = errors if errors is not None else self.encoding_errors
        assert isinstance(string, str), string
        try:
            self.server.stream.write(self.encode(string, errors))
        except LookupError as err:
            assert (self.server.encoding(outgoing=True)
                    != self.server._default_encoding)
            self.server.env_update({'CHARSET': self.server._default_encoding})
            self.log.debug(err)
            self._display_charset_err(err)
            return self.write(string, errors)

    def echo(self, string, errors=None):
        """ Write string to output only if "remote echo" enabled, for
            Telnet Servers that have sent (WILL, ECHO) or have
            received (DO, ECHO). Otherwise, nothing is done.
        """
        if self.server.stream.local_option.enabled(telopt.ECHO):
            self.write(string, errors)

    def decode(self, input, final=False):
        """ Decode input string using preferred encoding.
        """
        enc = self.server.encoding(incoming=True)
        if (self._decoder is None or enc != self._decoder._encoding):
            try:
                self._decoder = codecs.getincrementaldecoder(enc)(
                        errors=self.encoding_errors)
                self._decoder._encoding = enc
            except LookupError as err:
                assert (enc != self._default_encoding), err
                self.log.info(err)
                # notify server of change to _default_encoding, try again,
                self.server.env_update(
                        {'CHARSET': self.server._default_encoding})
                self._decoder = codecs.getincrementaldecoder(enc)(
                        errors=self.encoding_errors)
                self._decoder._encoding = enc
                # notify client of change to CHARSET,
                self._display_charset_err(err)
                # re-display shell prompt on input decode error
                self.shell.display_prompt()
        return self._decoder.decode(input, final)

    def can_write(self, ucs):
        """ Returns True if transport can receive ``ucs`` as a single-cell,
            carriage-forwarding character, such as 'x' or ' '. Values outside
            of 7-bit NVT ASCII range may only be written if server option
            ``outbinary`` is True.

            Otherwise False indicates that a write of this unicode character
            would be an encoding error on the transport (may crash or corrupt
            client screen).
        """
        return ord(ucs) > 31 and (ord(ucs) < 127 or self.server.outbinary)

    def encode(self, buf, errors=None):
        """ Encode byte buffer using client-preferred encoding.

            If ``outbinary`` is not negotiated, ucs must be made of strictly
            7-bit ascii characters (valued less than 128), and any values
            outside of this range will be replaced with a python-like
            representation.
        """
        errors = errors if errors is not None else self.encoding_errors
        return bytes(buf, self.server.encoding(outgoing=True), errors)

    def __str__(self):
        """ Returns string describing state of stream encoding.
        """
        encoding = '{}{}'.format(
                self.server.encoding(incoming=True), '' if
                self.server.encoding(outgoing=True)
                == self.server.encoding(incoming=True) else ' in, {} out'
                .format(self.server.encoding(outgoing=True)))
        return encoding

class Telsh():
    """ A remote line editing shell for host command processing.
    """
    #: character used to prefix special prompt escapes, ``prompt_escapes``
    prompt_esc_char = '%'

    #: character used for %# substituion in PS1 or PS2 evaluation
    prompt_char = '%'

    #: name of shell %s in prompt escape
    shell_name = 'telsh'

    #: version of shell %v in prompt escape
    shell_ver = '0.1'

    #: A cyclical collections.OrderedDict of command names and nestable
    #  arguments, or None for end-of-command, used by ``tab_received()``
    #  to provide autocomplete and argument cycling.
    cmdset_autocomplete = collections.OrderedDict([
        ('help', collections.OrderedDict([
            ('status', None),
            ('whoami', None),
            ('whereami', None),
            ('toggle', None),
            ('logoff', None),
            ]), ),
        ('echo', None),
        ('status', None),
        ('set', None),  # args injected during tab_received()
        ('whoami', None),
        ('whereami', None),
        ('toggle', collections.OrderedDict([
            ('echo', None),
            ('outbinary', None),
            ('inbinary', None),
            ('goahead', None),
            ('color', None),
            ('xon-any', None),
            ('bell', None),
            ]), ),
        ('logoff', None),
        ])

    def __init__(self, server, log=logging):
        #: TelnetServer instance associated with shell
        self.server = server
        self.stream = TelnetShellStream(server)
        self.log = log

        #: display full traceback to output stream ``display_exception()``
        self.show_traceback = True

        #: Whether to send video attributes
        self.does_styling = False

        #: input character fires ``autocomplete()``, None disables
        self.autocomplete_char = '\t'

        #: if set, characters are stripped around ``line_received``
        self.strip_eol = '\r\n\00'

        #: buffer of line input until command process.
        self._lastline = collections.deque()

        #: prompt evaluation re for ``resolve_prompt()``
        self._re_prompt = re.compile('{}{}'.format( self.prompt_esc_char,
            r'(?P<val>\d{3}|x[0-9a-fA-F]{2}|\$([a-zA-Z_]+)|[Ee#\?huH])'),
            flags=re.DOTALL)

        #: variable evaluation re for ``echo_eval()``
        self._re_var = re.compile(r'\$({(?P<eval>[^}]+)}|(?P<val>[a-zA-Z_]+))')

        #: Current state is multiline (PS2 is displayed)
        self._multiline = False

        #: write ASCII BELL on error
        self.send_bell = True

        #: write video attributes to output stream
        self._does_styling = False

        #: toggled on SLC_LNEXT (^v) for keycode input
        self._literal = False

        #: limit number of digits using counter _lit_recv
        self._lit_recv = False

        #: strip CR[+LF|+NUL] in character_received() by tracking last recv
        self._last_char = None

        #: Return value of last command, or None if none yet processed.
        self._retval = None

        #: Whether to call ``send_ga`` on server stream on WONT SGA (legacy)
        self._send_ga = True

    def set_term(self, term):
        """ Set termcap TERM value
        """ #  currently only _does_styling is flipped True,
            #  sometime in the future a multiprocessing.Process() should
            #  drive the 'blessings' module
        self.term = term
        self._does_styling = (
                term.startswith('vt') or
                term.startswith('xterm') or
                term.startswith('dtterm') or
                term.startswith('rxvt') or
                term.startswith('urxvt') or
                term.startswith('ansi') or
                term == 'linux' or term == 'screen')

    def display_prompt(self, redraw=False, input=None):
        """ Display or redraw prompt and current command line input.
        """
        input = self.lastline
        if self.is_multiline:
            input = input.split('\r')[-1]
        self.stream.write(''.join((
            '\r\x1b[K' if redraw else '\r\n',
            self.prompt,
            input,)))
        if self._send_ga:
            self.stream.send_ga()

    def display_status(self):
        """ Output the status of telnet session.
        """
        self.stream.write('\r\nConnected {:0.3f}s ago from {}.'
            '\r\nLinemode is {}.'
            '\r\nFlow control is {}.'
            '\r\nEncoding is {}.'
            '\r\n{} rows; {} cols.'.format(
                self.server.duration,
                (self.server.client_fqdn.result()
                    if self.server.client_fqdn.done()
                    else self.server.client_ip),
                self.server.stream.mode,
                'xon-any' if self.server.stream.xon_any else 'xon',
                self.stream,
                self.server.env['COLUMNS'],
                self.server.env['LINES'],))

    def bell(self):
        """ writes ASCII BEL unless ``send_bell`` is toggled False.
        """
        if self.send_bell:
            self.stream.write('\a')

    @property
    def retval(self):
        """ Returns exit status of last command processed by ``line_received``
        """
        return self._retval if self._retval is not None else ''

    @property
    def is_multiline(self):
        """ Returns True if at continuation of multi-line prompt.
        """
        return self._multiline

    @property
    def lastline(self):
        """ Returns current input line.
        """
        return u''.join(self._lastline)

    def write(self, ucs):
        """ Write unicode string using TelnetServer transport
        """
        self.stream.write(ucs)

    def dim(self, string):
        """ XXX Return ``string`` decurated using 'dim'
        """
        return (string if not self._does_styling
                else '\x1b[31m' + string + '\x1b[0m')

    def bold(self, string):
        """ XXX Return ``string`` decorated using 'bold'
        """
        return (string if not self._does_styling
                else '\x1b[0;1m' + string + '\x1b[0m')

    def standout(self, string):
        """ XXX Return ``string`` decorated using 'standout'
        """
        return (string if not self._does_styling
                else '\x1b[31;1m' + string + '\x1b[0m')

    def autocomplete(self, input, table=None):
        """ .. method:: autocomplete(input : string, table=None) -> bool

            XXX Callback for receipt of autocompletion key (default \t),
                providing command or argument completion, using default
                ``table`` of type ``OrderedDict``. If unspecified, the
                instance attribute ``cmdset_autocomplete`` is used.
        """
        self.log.debug('tab_received: {!r}'.format(input))
        # dynamic injection of variables for set command,
        cmd, args = input.rstrip(), []
        table = self.cmdset_autocomplete if table is None else table
        # inject session variables for set command,
        if 'set' in table:
            table['set'] = collections.OrderedDict([
                ('{}='.format(key), None)
                for key in sorted(self.server.env.keys())
                if key not in self.server.readonly_env])
        if ' ' in cmd:
            cmd, *args = shlex.split(cmd)
        do_cycle = bool(self._last_char == '\t')
        buf, match = _autocomplete(table, do_cycle, '', cmd, *args)
        self._last_char = '\t'
        self._lastline = collections.deque(buf)
        return match

    def editing_received(self, char, slc_byte=None):
        import slc  # todo: abstract away slc
        self.log.debug('editing_received: {!r}{}.'.format(
            char, ', {}'.format(
                (slc.name_slc_command(slc_byte),)
                if slc_byte is not None else '')))
        char_disp = name_unicode(char)
        if self.is_literal is not False:
            # continue literal input
            self.literal_received(char)
        elif slc_byte == slc.SLC_LNEXT:
            # begin literal input (^v)
            self.literal_received(char)
        elif slc_byte == slc.SLC_RP:
            # repaint (^r)
            self.display_prompt(redraw=True)
        elif slc_byte == slc.SLC_EC:
            # erase character chr(127)
            if 0 == len(self._lastline):
                self.bell()
            else:
                self._lastline.pop()
            self.display_prompt(redraw=True)
        elif slc_byte == slc.SLC_EW:  # erase word (^w), rubout .(\w+)
            if not self._lastline:
                self.bell()
            else:
                while self._lastline and self._lastline[-1].isspace():
                    self._lastline.pop()
                while self._lastline and not self._lastline[-1].isspace():
                    self._lastline.pop()
                self.display_prompt(redraw=True)
        elif slc_byte == slc.SLC_EL:  # erase line (^L)
            self._lastline.clear()
            self.display_prompt(redraw=True)
        elif slc_byte == slc.SLC_EOF:  # end of file (^D)
            if not self._lastline:
                self.stream.write(char_disp)
                self.server.logout(telopt.DO)
            else:
                self.bell()
        elif slc_byte in (slc.SLC_IP, slc.SLC_ABORT):
            # interrupt process (^C), abort process (^\)
            self._lastline.clear()
            self.stream.write(char_disp)
            self.display_prompt()
        elif slc_byte in (slc.SLC_XON, slc.SLC_XOFF,
                slc.SLC_AYT, slc.SLC_SUSP):
            # handled by callbacks or not really an editing cmd
            pass
        elif slc_byte in (slc.SLC_AO, slc.SLC_SYNCH, slc.SLC_EOR):
            # all others (unhandled)
            self.log.debug('recv {}'.format(slc.name_slc_command(slc)))
            self.stream.write(char_disp)
            self.bell()
            self.display_prompt()
        else:
            raise NotImplementedError(char, slc)

    @property
    def is_literal(self):
        """ Returns True if the SLC_LNEXT character (^v) was recieved, and
            any subsequent character should be received as-is; this is for
            inserting raw sequences into a command line that may otherwise
            interpret them not printable, or a special line editing character.
        """
        return not self._literal is False

    def literal_received(self, ucs):
        """ Receives literal character(s) SLC_LNEXT (^v) and all subsequent
            characters until the boolean toggle ``_literal`` is set False.
        """
        self.log.debug('literal_received: {!r}'.format(ucs))
        literval = 0 if self._literal is '' else int(self._literal)
        new_lval = 0
        if self._literal is False:  # ^V or SLC_VLNEXT
            self.stream.write(self.standout('^\b'))
            self._literal = ''
            return
        elif ord(ucs) < 32 and (
                not self._lit_recv
                and ucs not in ('\r', '\n')):
            # Control character
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
        elif self._lit_recv and ucs in ('\r', '\n'):
            self.character_received(chr(literval))
        else:
            self.character_received(ucs)
        self._lit_recv, self._literal = 0, False
        self._last_char = ucs

    def feed_byte(self, byte):
        ucs = self.stream.decode(byte, final=False)
        if ucs:
            self.character_received(ucs)

    def feed_slc(self, byte, slc):
        ucs = self.stream.decode(byte, final=False)
        self.editing_received(ucs, slc)

    def character_received(self, char):
        """ XXX Callback receives a single Unicode character as it is received.

            The default takes a 'most-compatible' implementation, providing
            'kludge' mode with simulated remote editing for inadvanced clients.
        """
        CR, LF, NUL = '\r\n\x00'
        char_disp = char
        self.log.debug('character_received: {!r}'.format(char))
        if not self.stream.can_write(char) or not char.isprintable():
            # ASCII representation of unprtintables for display editing
            char_disp = self.standout(name_unicode(char))
        if self.is_literal:
            # Within a ^v loop of ``literal_received()``, insert raw
            self._lastline.append(char)
            self.stream.echo(char_disp)
        elif (self._last_char == CR and char in (LF, NUL) and self.strip_eol):
            # ``strip_eol`` is True, pass on '\n' or '\x00' following CR,
            pass
        elif self._last_char == CR and char in (LF, NUL):
            # ``strip_eol`` is False, preserve '\n' or '\x00'
            self._lastline.append(char)
        elif char == CR:
            # callback ``line_received()`` always on CR
            if not self.strip_eol:
                self.lastline._append(CR)
            self.line_received(self.lastline)
        elif char == LF:
            # callback ``line_received()`` on single LF without CR
            if not self.strip_eol:
                self.lastline._append(LF)
            self.line_received(self.lastline)
        elif (char == self.autocomplete_char
                and self.autocomplete_char
                and self.server.stream.mode != 'local'):
            try:
                if not self.autocomplete(self.lastline):
                    self.bell()
            except ValueError as err:
                self.log.debug(err)  # shlex parsing error
                self.bell()
            except Exception:
                self.display_exception(*sys.exc_info(), level=logging.INFO)
            finally:
                self.display_prompt(redraw=True)
        elif not char.isprintable() and char not in (CR, LF, NUL,):
            self.bell()
        elif char.isprintable() and char not in ('\r', '\n'):
            self._lastline.append(char)
            self.stream.echo(char_disp)
        self._last_char = char

    def line_received(self, input, eor=False):
        """ XXX Callback for each telnet input line received.
        """
        self.log.debug('line_received: {!r}'.format(input))
        if self.strip_eol:
            input = input.rstrip(self.strip_eol)
        if self._multiline:
            input = ' '.join(input.split('\r'))
        self._multiline = False
        retval = None
        try:
            retval = self.process_cmd(input)
        except Exception:
            self.display_exception(*sys.exc_info(), level=logging.INFO)
            self.bell()
            retval = -1
        finally:
            # when _retval is None, we are multi-line
            if retval == '':
                # we are in a line continuate
                self._multiline = True
                self.display_prompt(input='')
            else:
                if retval is not None:
                    # a command was processed
                    self._retval = retval
                # clear line buffer and prompt
                self._lastline.clear()
                self.display_prompt()

    @property
    def prompt(self):
        """ Returns PS1 or PS2 prompt depending on current multiline context,
            with prompt escape `%' resolved for special values.
        """
        def _resolve_prompt(input, esc_char=None):
            """ Escape prompt characters and return value, using escape value
                ``prompt_esc_char`` of matching regular expression values for
                ``prompt_escapes``, and the following value lookup table:

              '%%'     a single '%'
              '%#'     prompt character
              '%u'     username
              '%h'     hostname
              '%H'     full hostname
              '%$'     value of session parameter following $
              '%?'     Return code last command processed
              '%000'   8-bit character for octal '077'
              '%x00'   8-bit character for 16-bit hexidecimal pair
              '%E'     Encoding of session
              '%s'     name of shell
              '%v'     version of shell
              """
            # TODO:
            # '%t'     time of day in 12-hour AM/PM format
            # '%T'     time of day in 24-hour format
            # '%p'     time of day in 12-hour format with seconds
            # '%P'     time of day in 24-hour format with seconds
            # '%d      The weekday in `Day' format.
            # '%D'     The day in `dd' format.
            # '%w'     The month in `Mon' format.
            # '%W'     The month in `mm' format.
            # '%y'     The year in `yy' format.
            # '%Y'     The year in `yyyy' format.
            esc_char = self.prompt_esc_char if esc_char is None else esc_char
            if input == esc_char:
                return esc_char
            if input == '#':
                return self.prompt_char
            if input == 'u':
                return self.server.env['USER']
            if input == 'h':
                return '{}'.format(self.server.server_name.result())
            if input == 'H':
                return '{}'.format(self.server.server_fqdn.result()
                        if self.server.server_fqdn.done() else
                        self.server.server_name.result()
                        if self.server.server_name.done()
                        else '')
            if input[0] == '$':
                return self.server.env[input[1:]]
            if input == '?':
                if self.retval or self.retval == 0:
                    return '{}'.format(self.retval & 255)
                return ''
            if input.isdigit():
                return chr(int(input, 8))
            if input.startswith('x'):
                return chr(int('0x{}'.format(input[1:]), 16))
            if input == 's':
                return self.shell_name
            if input == 'v':
                return self.shell_ver
            if input == 'E':
                return '{}'.format(self.stream)
            return input
        def prompt_eval(input, literal_escape=True):
            def _getter(match):
                return _resolve_prompt(match.group('val'))
            return self._eval(input, self._re_prompt, _getter, literal_escape)
        return ('{}'.format(prompt_eval(
            self.server.env['PS2'] if self.is_multiline
            else self.server.env['PS1'])))

    def display_exception(self, *exc_info, level=logging.DEBUG):
        """ Dispaly exception to client when ``show_traceback`` is True,
            forward copy server log at debug and info levels.
        """
        tbl_exception = (
                traceback.format_tb(exc_info[2]) +
                traceback.format_exception_only(exc_info[0], exc_info[1]))
        for num, tb in enumerate(tbl_exception):
            tb_msg = tb.splitlines()
            if self.show_traceback:
                self.stream.write('\r\n' + '\r\n>> '.join(
                    self.standout(row.rstrip())
                    if num == len(tbl_exception) - 1
                    else row.rstrip() for row in tb_msg))
            tbl_srv = [row.rstrip() for row in tb_msg]
            for line in tbl_srv:
                logging.log(level, line)

    def process_cmd(self, input):
        """ .. method:: process_cmd(input : string) -> int
            XXX Callback from ``line_received()`` for input line processing..

            The default handler returns shell-like exit/success value as
            integer, 0 meaning success, non-zero failure, and provides a
            minimal set of diagnostic commands.
        """
        commands = []
        for cmd_args in input.split(';'):
            cmd, args = cmd_args.rstrip(), []
            if ' ' in cmd:
                try:
                    cmd, *args = shlex.split(cmd)
                except ValueError as err:
                    self.log.debug(err)
                    if err.args == ('No closing quotation',):
                        self._lastline.append('\r') # use '\r' ..
                        return ''
                    elif (err.args == ('No escaped character',)
                            and cmd.endswith('\\')):
                        # multiline without escaping
                        return None
                    raise err
            commands.append((cmd, args))
        for cmd, args in commands:
            self.cmdset_command(cmd, *args)

    def cmdset_command(self, cmd, *args):
        self.log.debug('command {!r}{!r}'.format(cmd, args))
        if not len(cmd) and not len(args):
            return None
        if cmd in ('help', '?',):
            return self.cmdset_help(*args)
        elif cmd == 'echo':
            self.cmdset_echo(*args)
        elif cmd in ('quit', 'exit', 'logoff', 'logout', 'bye'):
            self.logout()
        elif cmd == 'status':
            self.display_status()
        elif cmd == 'whoami':
            self.stream.write('\r\n{}.'.format(self.about_connection()))
        elif cmd == 'whereami':
            return self.cmdset_whereami(*args)
        elif cmd == 'set':
            return self.cmdset_set(*args)
        elif cmd == 'toggle':
            return self.cmdset_toggle(*args)
        elif '=' in cmd:
            return self.cmdset_assign(*([cmd] + args))
        elif cmd:
            self.stream.write('\r\n{!s}: command not found.'.format(cmd))
            return 1
        return 0

    def cmdset_echo(self, *args):
        def echo_eval(input, literal_escape=True):
            def _getter(match):
                key = match.group('eval') or match.group('val')
                return self.server.env[key]
            return self._eval(input, self._re_var, _getter, literal_escape)
        self.stream.write('\r\n{}'.format(' '.join(
            echo_eval(arg) if '$' in arg else arg for arg in args)))
        return 0

    def cmdset_help(self, *args):
        if not len(args):
            self.stream.write('\r\nAvailable commands:\r\n')
            self.stream.write(', '.join(self.cmdset_autocomplete.keys()))
            return 0
        cmd = args[0].lower()
        if cmd == 'help':
            self.stream.write('\r\nDON\'T PANIC.')
            return -42
        elif cmd == 'logoff':
            self.stream.write('\r\nTerminate connection.')
        elif cmd == 'status':
            self.stream.write('\r\nDisplay operating parameters.')
        elif cmd == 'whoami':
            self.stream.write('\r\nDisplay session identifier.')
        elif cmd == 'set':
            self.stream.write('\r\nSet or display session values.')
        elif cmd == 'whereami':
            self.stream.write('\r\nDisplay server name')
        elif cmd == 'toggle':
            self.stream.write('\r\nToggle operating parameters:')
        elif cmd == 'echo':
            self.stream.write('\r\nDisplay arguments.')
        else:
            return 1
        if (cmd and cmd in self.cmdset_autocomplete
                and self.cmdset_autocomplete[cmd] is not None):
            self.stream.write('\r\n{}'.format(', '.join(
                self.cmdset_autocomplete[cmd].keys())))
        return 0

    def cmdset_whereami(self, *args):
        self.stream.write('\r\n{}'.format(
            (self.server_fqdn.result()
                if self.server.server_fqdn.done()
                else self.server.server_name.result()
                if self.server.server_name.done()
                else self.server.server_name.__repr__())))
        return 0

    def cmdset_toggle(self, *args):
        import telopt
        lopt = self.server.stream.local_option
        tbl_opt = dict([
            ('echo', lopt.enabled(telopt.ECHO)),
            ('outbinary', self.server.outbinary),
            ('inbinary', self.server.inbinary),
            ('binary', self.server.outbinary + self.server.inbinary),
            ('goahead', not lopt.enabled(telopt.SGA) and not self._send_ga),
            ('color', self._does_styling),
            ('xon-any', self.server.stream.xon_any),
            ('bell', self.send_bell)])
        if len(args) is 0:
            self.stream.write(', '.join(
                '{}{} [{}]'.format('\r\n' if num % 4 == 0 else '',
                    opt, self.standout('ON') if enabled
                    else self.dim('off'))
                for num, (opt, enabled) in enumerate(sorted(tbl_opt.items()))))
            return 0
        opt = args[0].lower()
        if len(args) > 1:
            self.stream.write('\r\ntoggle: too many arguments.')
            return 1
        elif args[0] not in tbl_opt and opt != '_all':
            self.stream.write('\r\ntoggle: not option.')
            return 1
        if opt in ('echo', '_all'):
            _opt = 'echo' if opt == '_all' else opt
            cmd = (telopt.WONT if tbl_opt[_opt] else telopt.WILL)
            self.server.stream.iac(cmd, telopt.ECHO)
            self.stream.write('\r\n{} echo.'.format(
                telopt.name_command(cmd).lower()))
        if opt in ('outbinary', 'binary', '_all'):
            _opt = 'outbinary' if opt in ('_all', 'binary') else opt
            cmd = (telopt.WONT if tbl_opt[_opt] else telopt.WILL)
            self.server.stream.iac(cmd, telopt.BINARY)
            self.stream.write('\r\n{} binary.'.format(
                telopt.name_command(cmd).lower()))
        if opt in ('inbinary', 'binary', '_all'):
            _opt = 'inbinary' if opt in ('_all', 'binary') else opt
            cmd = (telopt.DONT if tbl_opt[_opt] else telopt.DO)
            self.server.stream.iac(cmd, telopt.BINARY)
            self.stream.write('\r\n{} binary.'.format(
                telopt.name_command(cmd).lower()))
        if opt in ('goahead', '_all'):
            _opt = 'goahead' if opt == '_all' else opt
            cmd = (telopt.WONT if tbl_opt[_opt] else telopt.WILL)
            self._send_ga = cmd is telopt.WILL
            self.server.stream.iac(cmd, telopt.SGA)
            self.stream.write('\r\n{} supress go-ahead.'.format(
                telopt.name_command(cmd).lower()))
        if opt in ('bell', '_all'):
            _opt = 'bell' if opt == '_all' else opt
            self.send_bell = not tbl_opt[_opt]
            self.stream.write('\r\nbell {}abled.'.format(
                'en' if self.send_bell else 'dis'))
        if opt in ('xon-any', '_all'):
            _opt = 'xon-any' if opt == '_all' else opt
            self.server.stream.xon_any = not tbl_opt[_opt]
            self.stream.write('\r\nxon-any {}abled.'.format(
                'en' if self.server.stream.xon_any else 'dis'))
        if opt in ('color', '_all'):
            _opt = 'color' if opt == '_all' else opt
            self._does_styling = not self._does_styling
            self.stream.write('\r\ncolor {}.'.format('on'
                if self._does_styling else 'off'))
        return 0

    def cmdset_set(self, *args):
        def disp_kv(key, val):
            return (shlex.quote(val)
                    if key not in self.server.readonly_env
                    else self.standout(shlex.quote(val)))
        retval = 0
        if args:
            if '=' in args[0]:
                retval = self.cmdset_assign(*args)
                return 0 if not retval else retval # cycle down errors
            # no '=' must mean form of 'set a', displays 'a=value'
            key = args[0].strip()
            if key in self.server.env:
                self.stream.write('\r\n{}{}{}'.format(
                    key, '=', disp_kv(key, self.server.env[key])))
                return 0
            return -1  # variable not found, -1
        # display all values
        self.stream.write('\r\n')
        self.stream.write('\r\n'.join(['{}{}{}'.format(
            _key, '=', disp_kv(_key, _val))
            for (_key, _val) in sorted(self.server.env.items())]))
        return 0

    def cmdset_assign(self, *args):
        """ remote command: x=[val] set or unset session values.
        """
        if len(args) > 1:
            # x=1 y=2; evaluates right-left recursively
            self.cmdset_set(*args[1:])
        key, val = args[0].split('=', 1)
        if key in self.server.readonly_env:
            # value is read-only
            return -2
        if not val:
            if not key in self.server.env:
                # key not found
                return -3
            self.server.env_update({key: ''})
            return 0
        self.server.env_update({key: val})
        return 0

    def _eval(self, input, pattern, getter, literal_escape=True):
        """ Evalutes ``input`` for variable substituion using ``pattern``,
            replacing matches with return value of ``getter(match)``.
        """
        def _resolve_literal(char):
            if char == 'e': return '\x1b'  # transtable changed 2.x -> 3.x,
            elif char == 'f': return '\f'  # still worth it? Xxx
            elif char == 'n': return '\n'
            elif char == 'r': return '\r'
            elif char == 't': return '\t'
            elif char == 'v': return '\v'
            else:
                return '\\'
            return char

        assert callable(getter), getter
        output = []
        start_next = 0
        for n in range(len(input)):
            if n >= start_next:
                match = pattern.match(input[n:])
                if match:
                    output.append(getter(match))
                    start_next = n + match.end()
                elif literal_escape and (
                        input[n] == '\\' and n < len(input) - 1
                        and literal_escape):
                    val = _resolve_literal(input[n:n+2])
                    if val is None:
                        output.append('\\')
                        start_next = 0
                        continue
                    output.append(val)
                    start_next = n + 2
                else:
                    output.append(input[n])
        return ''.join(output)


def _autocomplete(table, cycle, buf, cmd, *args):
    """
    .. function::fnc_autocomplete(
            table : collections.OrderedDict, cycle : bool,
            buf : string, cmd : string, *args) -> (buf, bool)

    Recursive autocompletion function. This provides no "found last match"
    state tracking, but rather simply cycles 'next match' if cycle is True,
    meaning 's'<tab> -> 'set', then subsequent <tab> -> 'status'. """

    def postfix(buf, using=' '):
        return '{}{}'.format(buf, using) if buf else ''

    auto_cmds = tuple(table.keys())
    # empty commands cycle at first argument,
    if not cmd:
        has_args = table[auto_cmds[0]] is not None
        buf = ''.join((
            postfix(buf),
            postfix(auto_cmds[0], using=' '
                if has_args else ''),))
        return (buf, True)
    # scan for partial/complete matches, recurse if applicable
    for ptr, auto_cmd in enumerate(auto_cmds):
        has_args = table[auto_cmd] is not None
        if cmd.lower() == auto_cmd.lower():
            if table[cmd] is None:
                # match, but arguments not valid for command,
                if len(args):
                    buf = '{}{}'.format(
                            postfix(buf),
                            postfix(auto_cmd),
                            escape_quote(args))
                    return (buf, False)
                # first-time exact match,
                if not cycle:
                    return (buf, True)
                # cycle next match
                ptr = 0 if ptr + 1 == len(auto_cmds) - 1 else ptr + 1
                buf = ''.join((postfix(buf), auto_cmds[ptr],))
                return (buf, True)
            else:
                # match at this step, have/will args, recruse;
                buf = ''.join((postfix(buf), auto_cmd,))
                _cmd = args[0] if args else ''
                return _autocomplete(
                        table[auto_cmd], cycle, buf, _cmd, *args[1:])
        elif auto_cmd.lower().startswith(cmd.lower()):
            # partial match, error if arguments not valid,
            args_ok = bool(not args or args and has_args)
            buf = ''.join((postfix(buf), auto_cmd))
            if args:
                buf = ''.join((postfix(buf),
                    escape_quote(args)))
            return (buf, args_ok)
    # no matches
    buf = '{}{}{}'.format(postfix(buf),
            cmd, escape_quote(args))
    return (buf, False)

def name_unicode(ucs):
    """ Return 7-bit ascii printable of any string. """
    if ord(ucs) < ord(' ') or ord(ucs) == 127:
        ucs = r'^{}'.format(chr(ord(ucs) ^ ord('@')))
    elif ord(ucs) > 127 or not ucs.isprintable():
        ucs = r'\x{:02x}'.format(ord(ucs))
    return ucs

def escape_quote(args, quote_char="'", join_char=' '):
    """ .. function::quote(args : list, quote_char="'") -> string

        Supplement shlex.quote, returning list of strings ``args``
        joined by ``join_char`` and quoted by ``quote_char`` if
        ``join_char`` is used within that argument. For example:

        >>> print(escape_quote(['x', 'y', 'zz y']))
        "x y 'zz y'"
    """
    def quoted(arg):
        return (''.join(quote_char, arg, quote_char)
                if join_char in arg else arg)
    return join_char.join([quoted(arg) for arg in args] if args else [])


