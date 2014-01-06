import collections
import traceback
import logging
import codecs
import shlex
import time
import sys
import re

from . import slc
from . import telopt
from . import wcwidth

__all__ = ('TelnetShellStream', 'Telsh')


class _EDIT():
    """ Enums for value of ``cmd`` in ``Telsh.editing_received()``.
    """
    (RP, EC, EW, EL, IP, AO, AYT, BRK, EOF, EOR, XON, XOFF, ABORT, SUSP, LNEXT
     ) = range(15)

    def __init__(self):
        self.constants = dict([(getattr(self, key), key)
                               for key in dir(self) if key.isupper()])

    def name(self, const):
        return self.constants.get(const, str(const))

EDIT = _EDIT()

SLC_EDIT_TRANSTABLE = dict((
    (slc.SLC_RP, EDIT.RP),
    (slc.SLC_EC, EDIT.EC),
    (slc.SLC_EW, EDIT.EW),
    (slc.SLC_EL, EDIT.EL),
    (slc.SLC_IP, EDIT.IP),
    (slc.SLC_AO, EDIT.AO),
    (slc.SLC_AYT, EDIT.AYT),
    (slc.SLC_BRK, EDIT.BRK),
    (slc.SLC_EOF, EDIT.EOF),
    (slc.SLC_EOR, EDIT.EOR),
    (slc.SLC_XON, EDIT.XON),
    (slc.SLC_XOFF, EDIT.XOFF),
    (slc.SLC_SUSP, EDIT.SUSP),
    (slc.SLC_ABORT, EDIT.ABORT),
    (slc.SLC_LNEXT, EDIT.LNEXT),
))  # not mapped: SLC_SYNCH


class TelnetShellStream():
    def __init__(self, server, log=logging):
        self.server = server
        self.log = log

        #: codecs.IncrementalDecoder for current CHARSET
        self.decoder = None

        #: default encoding 'errors' argument
        self.encoding_errors = 'replace'

        #: boolean toggle: call ``TelnetServer.stream.send_ga()`` if client
        #  refuses supress goahead (WONT SGA) ?
        self.send_go_ahead = True

    def display_charset_err(self, err):
        """ XXX Carefully notify client of encoding error. """
        encoding = self.server.encoding(outgoing=True)
        err_bytes = bytes(err.args[0].encode(encoding))
        charset = bytes(self.server.env['CHARSET'].encode(encoding))
        msg = b''.join((b'\r\n', err_bytes, b', CHARSET is ', charset, b'.'))
        self.server.stream.write(msg)

    def send_ga(self):
        if self.send_go_ahead:
            self.server.stream.send_ga()

    def write(self, string, errors=None):
        """ Write string to output using preferred encoding.
        """
        errors = errors if errors is not None else self.encoding_errors
        assert isinstance(string, str), string
        try:
            bytestring = self.encode(string, errors)
            self.log.debug('write: {!r}'.format(bytestring))
            self.server.stream.write(bytestring)
        except LookupError as err:
            assert (self.server.encoding(outgoing=True)
                    != self.server._default_encoding)
            self.server.env_update(
                {'CHARSET': self.server._default_encoding})
            self.log.debug(err)
            self.display_charset_err(err)
            return self.write(string, errors)

    @property
    def will_echo(self):
        """ Returns wether the shell should display keyboard input to the
            client: True if (DO, ECHO) received by client or (WILL, ECHO)
            sent by server.
        """
        return self.server.stream.local_option.enabled(telopt.ECHO)

    def echo(self, string, errors=None):
        """ Write string to output only if "remote echo" enabled, for
            Telnet Servers that have sent (WILL, ECHO) or have
            received (DO, ECHO). Otherwise, nothing is done.
        """
        if self.will_echo:
            self.write(string, errors)

    def decode(self, input, final=False):
        """ Decode input string using preferred encoding.
        """
        enc = self.server.encoding(incoming=True)
        if (self.decoder is None or enc != self.decoder._encoding):
            try:
                self.decoder = codecs.getincrementaldecoder(enc)(
                    errors=self.encoding_errors)
                self.decoder._encoding = enc
            except LookupError as err:
                assert (enc != self.server._default_encoding), err
                self.log.info(err)
                # notify server of change to _default_encoding, try again,
                self.server.env_update(
                    {'CHARSET': self.server._default_encoding})
                self.decoder = codecs.getincrementaldecoder(enc)(
                    errors=self.encoding_errors)
                self.decoder._encoding = enc
                self.display_charset_err(err)
                self.shell.display_prompt()
        return self.decoder.decode(input, final)

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
        enc_in = self.server.encoding(incoming=True)
        enc_out = self.server.encoding(outgoing=True)
        if enc_in == enc_out:
            return enc_in
        else:
            return '{} in, {} out'.format(enc_in, enc_out)

class Telsh():
    """ A remote line editing shell for host command processing.
    """
    #: character used to prefix special prompt escapes, ``prompt_escapes``
    prompt_esc_char = '%'

    #: character used for %# substituion in PS1 or PS2 evaluation
    prompt_char = '%'

    #: regular expression pattern string for prompt escape sequences
    re_prompt = (r'(?P<val>\d{3}'
                 r'|x[0-9a-fA-F]{2}'
                 r'|\$([a-zA-Z_]+)'
                 r'|[Ee#\?hHusvtTpPdDwWyYzZ])')

    #: regular expression pattern for echo variable matches, $var or ${var}
    re_echo = r'\${?(\?|[a-zA-Z_]+)}?'

    #: name of shell %s in prompt escape
    shell_name = 'telsh'

    #: version of shell %v in prompt escape
    shell_ver = '0.1'

    #: A cyclical collections.OrderedDict of command names and nestable
    #  arguments, or None for end-of-command, used by ``tab_received()``
    #  to provide autocomplete and argument cycling.
    autocomplete_cmdset = collections.OrderedDict(sorted([
        ('help', collections.OrderedDict(sorted([
            ('status', None),
            ('whoami', None),
            ('toggle', None),
            ('quit', None),
            ('whereami', None),
            ('set', None),
            ('echo', None),
            ])), ),
        ('echo', None),
        ('status', None),
        ('set', None),  # args injected during tab_received()
        ('slc', None),
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
        ('quit', None),
    ]))  # TODO: auto-generated

    #: display full traceback to output stream ``display_exception()``
    show_traceback = True

    #: Whether to send video attributes; toggled by ``set_term()``, updated
    #  to 'True' in certain conditions in callback ``term_received()``.
    does_styling = False

    #: if set, input character fires ``autocomplete()``
    autocomplete_char = '\t'

    #: if set, these chars stripped from end of line in ``line_received()``
    strip_eol = '\r\n\00'

    #: boolean toggle: write ASCII BELL on error?
    send_bell = False

    #: if set, maximum base10 digit that may be entered using ^V[0-9]+
    _max_litval = 65535

    #: maximum length of in-memory input buffer, (default, 8K)
    _max_lastline = 8192

    def __init__(self, server, stream=TelnetShellStream, log=logging):
        #: TelnetServer instance associated with shell
        self.server = server

        #: TelnetShellStream provides encoding for Telnet NVT
        self.stream = stream(server=server, log=log)

        self.log = log

        #: boolean toggle: Current state is multiline? PS2 is displayed.
        self.multiline = False

        #: if set, last character received by ``character_received()``.
        self.last_char = None

        #: if set, exit status of last command; accessed by property ``retval``
        self._retval = None

        #: boolean toggle: set on editing_received of EDIT.LNEXT (^v)
        self._literal = False

        #: buffer of line input until command process.
        self._lastline = collections.deque(maxlen=self._max_lastline)

        #: compiled expression for prompt evaluation in ``resolve_prompt()``
        self._re_prompt = re.compile('{esc}{pattern}'.format(
            esc=self.prompt_esc_char, pattern=self.re_prompt), re.DOTALL)

        #: compiled expression for variable expanson in ``echo_eval()``
        self._re_echo = re.compile(self.re_echo, re.DOTALL)

#
# properties
#
    @property
    def retval(self):
        """ Returns exit status of last command executed. ``
        """
        return self._retval if self._retval is not None else ''

    @property
    def is_multiline(self):
        """ Returns True if current prompt is a continuation
            of a multi-line prompt.
        """
        return self.multiline

    @property
    def lastline(self):
        """ Returns current input line.
        """
        return u''.join(self._lastline)

#
# internal methods
#
    def term_received(self, term):
        """ .. method:: term_received(string)

            callback fired by telnet iac to set or update TERM
        """
        self.term = term
        self.log.debug('term_received: {}'.format(term))
        self.does_styling = (
            term.startswith('vt') or
            term.startswith('xterm') or
            term.startswith('dtterm') or
            term.startswith('rxvt') or
            term.startswith('urxvt') or
            term.startswith('ansi') or
            term == 'linux' or term == 'screen')

    def winsize_received(self, lines, columns):
        """ .. method:: winsize_received(lines : int, columns : int)

            callback fired by telnet iac to set or update window size
        """
        pass

    def bell(self):
        """ ..method:: bell()

            writes ASCII bell (\a) unless ``send_bell`` is set False.
        """
        if self.send_bell:
            self.stream.write('\a')

    def erase(self, string, keypress=chr(127)):
        """ .. method:: erase(string, keypress=chr(127)) -> string

            Returns sequence for erasing ``string`` preceeding cursor given
            the erase character ``keypressed`` (one of chr(127) or 8) has
            been used to perform deletion, assisting predicted cursor
            movement of sessions using remote line editing with echo off.
        """
        assert keypress in (chr(127), chr(8)), chr
        string_disp = ''.join(((_char
                                if self.stream.can_write(_char)
                                and _char.isprintable()
                                else name_unicode(_char))
                               for _char in string))
        vtlen = wcwidth.wcswidth_cjk(string_disp)
        assert vtlen >= 0, string

        # non-BSD clients will echo
        if self.stream.will_echo:
            return ('\b' * vtlen) + '\x1b[K'

        # BSD has strange behavior for line editing with local echo:
        if keypress == chr(127):
            # (1) '^?' actually advances the cursor right one cell,
            return '\b' + ('\b' * vtlen) + '\x1b[K'
        else:
            # (2) whereas '^h' moves the cursor left one (displayable) cell !
            return '\b' * (vtlen - 1) + '\x1b[K'

#
# public callbacks
#
    def display_prompt(self, redraw=False):
        """ .. method::display_prompt(redraw=False)

            XXX Display or redraw prompt.
        """
        disp_char = lambda char: (
            self.standout(name_unicode(char))
            if not self.stream.can_write(char)
            or not char.isprintable()
            else char)
        if self.is_multiline:
            text = self.lastline.split('\r')[-1]
        else:
            text = self.lastline
        text = ''.join([disp_char(char) for char in text])
        # when 'redraw' is true, perform a 'carriage return'
        # followed by 'clear_eol' sequence, otherwise CR+LF is fine.
        prefix = '\r\x1b[K' if redraw else '\r\n'
        output = ''.join((prefix, self.prompt, text,))
        self.stream.write(output)
        self.stream.send_ga()

    def display_status(self):
        """ .. method::display_status()

            XXX Output status of telnet session, respoding to 'are you there?'
            (AYT) requests, or command 'status'.
        """
        self.stream.write(
            '\r\nConnected {:0.3f}s ago from {}.'
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
                self.server.env['LINES'],
            ))

    def dim(self, string):
        """ .. method:: dim(string) -> string

            XXX Returns ``string`` decorated using preferred terminal sequence.
        """
        _dim, _normal = '\x1b[31m', '\x1b[0m'
        return (string if not self.does_styling
                else (_dim + string + _normal))

    def standout(self, string):
        """ .. method:: standout(string) -> string

            XXX Returns ``string`` decorated using preferred terminal sequence.
        """
        _standout, _normal = '\x1b[31;1m', '\x1b[0m'
        return (string if not self.does_styling
                else (_standout + string + _normal))

    def underline(self, string):
        """ .. method:: standout(string) -> string

            XXX Returns ``string`` decorated using preferred terminal sequence.
        """
        _underline, _normal = '\x1b[4m', '\x1b[0m'
        return (string if not self.does_styling
                else (_underline + string + _normal))

    def autocomplete(self, input, table=None):
        """ .. method:: autocomplete(input : string, table=None) -> bool

            XXX Callback for receipt of autocompletion key (default \t),
                providing command or argument completion, using default
                ``table`` of type ``OrderedDict``. If unspecified, the
                instance attribute ``autocomplete_cmdset`` is used.
        """
        self.log.debug('tab_received: {!r}'.format(input))
        # dynamic injection of variables for set command,
        cmd, args = input.rstrip(), []
        table = self.autocomplete_cmdset if table is None else table
        # inject session variables for set command,
        if 'set' in table:
            table['set'] = collections.OrderedDict([
                ('{}='.format(key), None)
                for key in sorted(self.server.env.keys())
                if key not in self.server.readonly_env])
        if ' ' in cmd:
            cmd, *args = shlex.split(cmd)
        do_cycle = bool(self.last_char == '\t')
        buf, match = _autocomplete(table, do_cycle, '', cmd, *args)
        self.last_char = '\t'
        self._lastline = collections.deque(buf)
        return match

    def editing_received(self, char, cmd):
        """ ..method::editing_received(char, cmd)

            XXX Callback receives unicode character and editing ``cmd``,
            matching class attributes of global instance ``EDIT``. The
            default implementation provides a readline-like interface,
            though, without any insertion.
        """
        # it could be said the default SLC function values are emacs-like.
        # it is not suprising, given rms' involvement with late 70's telnet
        # line editing and video attribute negotiation protocols ..
        def _name(cmd):
            for key in dir(EDIT):
                if key.isupper() and getattr(EDIT, key) == cmd:
                    return key
        self.log.debug('editing_received: {!r}({}).'.format(
            name_unicode(char), _name(cmd)))
        # a printable ASCII representation of unprintables,
        char_disp = name_unicode(char)
        if self.is_literal is not False:
            # continue literal input for matching editing characters
            self.literal_received(char)
        elif cmd == EDIT.LNEXT:
            # begin literal input (^v)
            self.literal_received(char)
        elif cmd == EDIT.RP:
            # repaint (^r)
            self.display_prompt(redraw=True)
        elif cmd == EDIT.EC:
            # erase character chr(127)
            if 0 == len(self._lastline):
                self.bell()
                self.display_prompt(redraw=True)
            else:
                self.stream.write(self.erase(self._lastline.pop(), char))
        elif cmd == EDIT.EW:
            # erase word (^w), rubout .(\w+)
            if not self._lastline:
                self.bell()
            else:
                ucs = ''
                while self._lastline and self._lastline[-1].isspace():
                    ucs += self._lastline.pop()
                while self._lastline and not self._lastline[-1].isspace():
                    ucs += self._lastline.pop()
                self.display_prompt(redraw=True)
        elif cmd == EDIT.EL:
            # erase line (^L)
            self._lastline.clear()
            self.display_prompt(redraw=True)
        elif cmd == EDIT.EOF:
            # end of file (^D)
            if not self._lastline:
                self.stream.write(char_disp)
                self.server.logout()
            else:
                self.bell()
        elif cmd == EDIT.AYT:
            # are-you-there? (^T)
            self._lastline.clear()
            self.stream.write(char_disp)
            self.display_status()
            self.display_prompt()
        elif cmd in (EDIT.IP, EDIT.ABORT,):
            # interrupt process (^C), abort process (^\)
            self._lastline.clear()
            self.stream.write(char_disp)
            self.display_prompt()
        elif cmd in (EDIT.XOFF, EDIT.XON,):
            # transmit-off (^S), transmit-on (^Q)
            pass
        elif cmd in (EDIT.AO,):
            self.display_prompt()
        else:
            # not handled or implemented
            self.log.debug('{} unhandled.'.format(EDIT.name(cmd)))
            self._lastline.append(char)
            self.stream.write(char_disp)

    @property
    def is_literal(self):
        """ Returns True if the EDIT.LNEXT character (^v) was recieved, and
            any subsequent character should be received as-is; this is for
            inserting raw sequences into a command line that may otherwise
            interpret them not printable, or a special line editing character.
        """
        return self._literal is not False

    def feed_byte(self, byte):
        ucs = self.stream.decode(byte, final=False)
        if ucs:
            self.character_received(ucs)

    def feed_slc(self, byte, func):
        ucs = self.stream.decode(byte, final=True)
        self.editing_received(ucs, SLC_EDIT_TRANSTABLE[func])

    def literal_received(self, ucs):
        """ Receives literal character(s) EDIT.LNEXT (^v) and all subsequent
            characters until the boolean toggle ``_literal`` is set False.
        """
        CR, LF, NUL = '\r\n\x00'
        self.log.debug('literal_received: {!r}'.format(ucs))
        if self.is_literal is False:
            self.log.debug('begin marker {!r}'.format(name_unicode(ucs)))
            self.stream.write(self.standout('^\b'))
            self._literal = -1
            return
        val = 0 if self._literal == -1 else int(self._literal)
        self.log.debug('continuation, {!r}, {!r}'.format(val, ucs))
        if ord(ucs) < 32:
            if ucs in (CR, LF) and self._literal != -1:
                # when CR or LF is received, send as-is & cancel,
                self.character_received(chr(val), literal=True)
                self._literal = False
                return
            if self._literal != -1:
                # before sending control character, send current value,
                self.character_received(chr(val), literal=True)
            # send raw control character to ``character_received``
            self.character_received(ucs, literal=True)
            self._literal = False
            return
        if self._max_litval and ord('0') <= ord(ucs) <= ord('9'):
            # base10 digit
            self._literal = (val*10) + int(ucs)
            if (self._literal >= self._max_litval or
                    len('{}'.format(self._literal))
                    == len('{}'.format(self._max_litval))):
                ucs = chr(min(self._literal, self._max_litval))
                self.character_received(ucs, literal=True)
                self._literal = False
            return
        self._lastline.append(ucs)
        if self.stream.will_echo:
            self.stream.echo(self.standout(name_unicode(ucs)))
        else:
            self.display_prompt(redraw=True)
        self._literal = False

    def character_received(self, char, literal=False):
        """ Receive a single (non-editing) Unicode character.
        """
        CR, LF, NUL = '\r\n\x00'
#        self.log.debug('character_received: {!r} literal={}'.format(
#            char, literal))

        # a printable ASCII representation of unprintables,
        char_disp = (char
                     if self.stream.can_write(char) and char.isprintable()
                     else self.standout(name_unicode(char)))

        if literal:
            self._lastline.append(char)
            self.display_prompt(redraw=True)

        elif self.is_literal and not literal:
            self.literal_received(char)

        elif self.last_char == CR and char in (LF, NUL):
            # ``strip_eol`` is True, pass on '\n' or '\x00' following CR,
            if self.strip_eol:
                pass
            # ``strip_eol`` is False, preserve '\n' or '\x00'
            else:
                self._lastline.append(char)

        # callback ``line_received()`` always on CR
        elif char == CR:
            if not self.strip_eol:
                self.lastline._append(CR)
            self.line_received(self.lastline)

        # callback ``line_received()`` on single LF without CR
        elif char == LF:
            if not self.strip_eol:
                self.lastline._append(LF)
            self.line_received(self.lastline)

        # callback ``editing_received(char, EDIT.EC)`` for backspace/delete
        elif char in ('\b', chr(127)) and not literal:
            self.editing_received(char, EDIT.EC)

        elif char == '\x0c': # ^L, refresh
            self.display_prompt(redraw=True)

        # perform tab completion for kludge or remote line editing.
        elif (char == self.autocomplete_char
                and self.autocomplete_char
                and self.server.stream.mode != 'local'):
            try:
                if not self.autocomplete(self.lastline):
                    self.bell()
            # shlex parsing error
            except ValueError as err:
                self.log.debug(err)
                self.bell()
            except Exception:
                self.display_exception(*sys.exc_info())
            finally:
                self.display_prompt(redraw=True)

        else:
            self._lastline.append(char)
            self.stream.echo(char_disp)
            if not char.isprintable():
                self.log.debug('unprintable recv, {!r}'.format(char))

        self.last_char = char

    def line_received(self, input):
        """ Callback for each line received, processing command(s) at EOL.
        """
        self.log.debug('line_received: {!r}'.format(input))
        if self.strip_eol:
            input = input.rstrip(self.strip_eol)
        if self.multiline:
            input = ' '.join(input.split('\r'))
        self.multiline = False
        retval = None
        try:
            retval = self.process_cmd(input)
        except Exception:
            self.display_exception(*sys.exc_info())
            self.bell()
            retval = -1
        finally:
            # when _retval is None, we are multi-line
            if retval == '':
                # we are in a line continuate
                self.multiline = True
                self.display_prompt(redraw=True)
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
        ps = (self.server.env['PS2'] if self.is_multiline
              else self.server.env['PS1'])
        return ('{}'.format(prompt_eval(self, ps)))

    def display_exception(self, *exc_info):
        """ Dispaly exception to client when ``show_traceback`` is True,
            forward copy server log at debug and info levels.
        """
        tbl_exception = (
            traceback.format_tb(exc_info[2]) +
            traceback.format_exception_only(exc_info[0], exc_info[1]))
        for num, tb in enumerate(tbl_exception):
            tb_msg = tb.splitlines()
            if self.show_traceback:
                self.stream.write('\r\n' + '\r\n'.join(
                    self.standout(row.rstrip())
                    if num == len(tbl_exception) - 1
                    else row.rstrip() for row in tb_msg))
            tbl_srv = [row.rstrip() for row in tb_msg]
            for line in tbl_srv:
                self.log.log(logging.ERROR, line)

    def process_cmd(self, input):
        """ .. method:: process_cmd(input : string) -> int
            XXX Callback from ``line_received()`` for input line processing.

            The default handler returns shell-like exit/success value as
            integer, 0 meaning success, non-zero failure. None indicates
            no command was processed, and '' indicates a continuation of
            multi-line.
        """
        self.stream.write('\r\n')
        commands = []
        for cmd_args in input.split(';'):
            cmd, args = cmd_args.rstrip(), []
            if ' ' in cmd:
                try:
                    cmd, *args = shlex.split(cmd)
                except ValueError as err:
                    self.log.debug(err)
                    if err.args == ('No closing quotation',):
                        self._lastline.append('\r')  # use '\r' ..
                        return ''
                    elif (err.args == ('No escaped character',)
                            and cmd.endswith('\\')):
                        return ''
                    raise err
            commands.append((cmd, args))
        for cmd, args in commands:
            retval = self.cmdset_command(cmd, *args)
        return retval

    def cmdset_command(self, cmd, *args):
        self.log.debug('command {!r} {!r}'.format(cmd, args))
        if not len(cmd) and not len(args):
            return None
        cmd_funcname = 'cmdset_{}'.format(cmd)
        if hasattr(self, cmd_funcname):
            func = getattr(self, cmd_funcname)
            return func(*args)
        elif '=' in cmd:
            return self.cmdset_assign(*((cmd,) + args))
        elif cmd:
            disp_cmd = u''.join([name_unicode(char) for char in cmd])
            self.stream.write('{!s}: command not found.'.format(disp_cmd))
            return 1
        return 0

    def cmdset_help(self, *args):
        if not len(args):
            self.stream.write('Available commands:\r\n')
            self.stream.write(', '.join(self.autocomplete_cmdset.keys()))
            return 0
        cmd = args[0].lower()
        if cmd == 'help':
            self.stream.write("DON'T PANIC.")
            return -42

        method_name = 'cmdset_{}'.format(cmd)
        if not hasattr(self, method_name):
            self.stream.write('Command not found.')
            return 1
        else:
            method = getattr(self, method_name)
            docstr = method.__doc__
            docstr = 'No help available.' if docstr is None else docstr
            self.stream.write('{}: {}'.format(cmd, docstr.strip()))
            # display command arguments
            if (cmd in self.autocomplete_cmdset
                    and self.autocomplete_cmdset[cmd] is not None):
                self.stream.write('\r\n{}'.format(', '.join(
                    self.autocomplete_cmdset[cmd].keys())))
        return 0

    def cmdset_status(self, *args):
        " Display session status. "
        return self.display_status()

    def cmdset_quit(self, *args):
        " Disconnect from server. "
        return self.server.logout()

    def cmdset_whoami(self, *args):
        " Display session identifier. "
        self.stream.write('{}.'.format(self.server.__str__()))
        return 0

    def cmdset_echo(self, *args):
        " Display arguments. "
        def echo_eval(input, literal_escape=True):
            def _getter(match):
                if match.group(1) == '?':
                    return ('{}'.format(self._retval)
                            if self._retval is not None else '')
                return self.server.env[match.group(1)]
            return self._eval(input, self._re_echo, _getter, literal_escape)
        output = ' '.join(echo_eval(arg) for arg in args)
        self.stream.write('{}'.format(output))
        return 0

    def cmdset_whereami(self, *args):
        " Display server name. "
        self.stream.write('{}'.format(
            (self.server.server_fqdn.result()
                if self.server.server_fqdn.done()
                else self.server.server_name.result()
                if self.server.server_name.done()
                else self.server.server_name.__repr__())))
        return 0

    def cmdset_debug(self, *args):
        " Display telnet option negotiation information. "
        self.stream.write('server: DO')
        for cmd, enabled in self.server.stream.remote_option.items():
            if enabled:
                self.stream.write(' {}'.format(telopt.name_command(cmd)))
        self.stream.write('.\r\nclient: WILL')
        for cmd, enabled in self.server.stream.local_option.items():
            if enabled:
                self.stream.write(' {}'.format(telopt.name_command(cmd)))
        self.stream.write('.\r\nunreplied:')
        for cmd, pending in self.server.stream.pending_option.items():
            if pending:
                self.stream.write(', {}'.format(telopt.name_commands(cmd)))
        self.stream.write('.')
        return 0

    def cmdset_slc(self, *args):
        " Display special line editing characters. "
        # TODO: support re-assignment
        from .slc import name_slc_command, theNULL
        self.stream.write('Special Line Characters:\r\n{}'.format(
            '\r\n'.join(['{:>10}: {}'.format(
                name_slc_command(slc_func), slc_def)
                for (slc_func, slc_def) in sorted(
                    self.server.stream.slctab.items())
                if not (slc_def.nosupport or slc_def.val == slc.theNULL)])))
        self.stream.write('\r\n\r\nUnset by client: {}'.format(
            ', '.join([name_slc_command(slc_func)
                       for (slc_func, slc_def) in sorted(
                           self.server.stream.slctab.items())
                       if slc_def.val == slc.theNULL])))
        self.stream.write('\r\n\r\nNot supported by server: {}'.format(
            ', '.join([name_slc_command(slc_func)
                       for (slc_func, slc_def) in sorted(
                           self.server.stream.slctab.items())
                       if slc_def.nosupport])))
        self.stream.write('\r\n')
        return 0

    def cmdset_toggle(self, *args):
        " Display, set, or unset session options. "
        lopt = self.server.stream.local_option
        tbl_opt = dict([
            ('echo', lopt.enabled(telopt.ECHO)),
            ('outbinary', self.server.outbinary),
            ('inbinary', self.server.inbinary),
            ('binary', self.server.outbinary + self.server.inbinary),
            ('goahead', (
                not lopt.enabled(telopt.SGA)) and self.stream.send_go_ahead),
            ('color', self.does_styling),
            ('xon-any', self.server.stream.xon_any),
            ('bell', self.send_bell)])
        if len(args) is 0:
            self.stream.write(', '.join(
                '{}{} [{}]'.format('\r\n' if num % 4 == 0 else '',
                                   opt, self.standout('ON') if enabled
                                   else self.dim('off'))
                for num, (opt, enabled) in enumerate(
                    sorted(tbl_opt.items()))))
            return 0
        opt = args[0].lower()
        if len(args) > 1:
            self.stream.write('toggle: too many arguments.')
            return 1
        elif args[0] not in tbl_opt and opt != '_all':
            self.stream.write('toggle: not option.')
            return 1
        if opt in ('echo', '_all'):
            cmd = (telopt.WONT if tbl_opt['echo'] else telopt.WILL)
            self.server.stream.iac(cmd, telopt.ECHO)
            self.stream.write('\r\n{} echo.'.format(
                telopt.name_command(cmd).lower()))
        if opt in ('outbinary', 'binary', '_all'):
            cmd = (telopt.WONT if tbl_opt['outbinary'] else telopt.WILL)
            self.server.stream.iac(cmd, telopt.BINARY)
            self.stream.write('\r\n{} binary.'.format(
                telopt.name_command(cmd).lower()))
        if opt in ('inbinary', 'binary', '_all'):
            cmd = (telopt.DONT if tbl_opt['inbinary'] else telopt.DO)
            self.server.stream.iac(cmd, telopt.BINARY)
            self.stream.write('\r\n{} binary.'.format(
                telopt.name_command(cmd).lower()))
        if opt in ('goahead', '_all'):
            cmd = (telopt.WILL if tbl_opt['goahead'] else telopt.WONT)
            self.stream.send_go_ahead = cmd is telopt.WONT
            self.server.stream.iac(cmd, telopt.SGA)
            self.stream.write('\r\n{} supress go-ahead.'.format(
                telopt.name_command(cmd).lower()))
        if opt in ('bell', '_all'):
            self.send_bell = not tbl_opt['bell']
            self.stream.write('\r\nbell {}abled.'.format(
                'en' if self.send_bell else 'dis'))
        if opt in ('xon-any', '_all'):
            self.server.stream.xon_any = not tbl_opt['xon-any']
            self.server.stream.send_lineflow_mode()
            self.stream.write('\r\nxon-any {}abled.'.format(
                'en' if self.server.stream.xon_any else 'dis'))
        if opt in ('color', '_all'):
            self.does_styling = not tbl_opt['color']
            self.stream.write('\r\ncolor {}.'.format(
                'on' if self.does_styling else 'off'))
        return 0

    def cmdset_set(self, *args):
        " Display or set operating parameters. "
        def disp_kv(key, val):
            """ display a shell-escaped version of value ``val`` of ``key``,
                using terminal 'dim' attribute for read-only variables.
            """
            return (self.dim(shlex.quote(val))
                    if key in self.server.readonly_env
                    else shlex.quote(val))
        retval = 0
        for arg in args:
            if '=' in arg:
                retval = self.cmdset_assign(arg)  # assigned
            elif arg in self.server.env:
                val = self.server.env[arg]
                self.stream.write('\r\n{key}={val}'.format(
                    key=arg, val=disp_kv(arg, val)))
                retval = 0  # displayed query
            else:
                retval = -1  # query unmatched
        if not args:
            # display all values
            kv = [(_key, _val) for (_key, _val)
                  in sorted(self.server.env.items())]
            self.stream.write('\r\n'.join(['{key}={val}'.format(
                key=_key, val=disp_kv(_key, _val)) for _key, _val in kv]))
        return retval

    def cmdset_assign(self, *args):
        " remote command: x=[val] set or unset session values. "
        if not len(args):
            return -1
        elif len(args) > 1:
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
            # transtable changed 2.x -> 3.x,
            # still worth it? XXX
            if char == 'e':
                return '\x1b'
            elif char == 'f':
                return '\f'
            elif char == 'n':
                return '\n'
            elif char == 'r':
                return '\r'
            elif char == 't':
                return '\t'
            elif char == 'v':
                return '\v'
            else:
                return '\\{}'.format(char)

        assert callable(getter), getter
        output = []
        start_next = 0
        for n in range(len(input)):
            if n >= start_next:
                match = pattern.match(input[n:])
                if match:
                    output.append(getter(match))
                    start_next = n + match.end()
                elif (literal_escape and input[n] == '\\'
                        and n < len(input) - 1):
                    val = _resolve_literal(input[n+1])
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
            postfix(auto_cmds[0], using=(' ' if has_args else '')),))
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
                buf = ''.join((postfix(buf), auto_cmds[ptr]))
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
            if args or has_args:
                buf = ''.join((postfix(buf), escape_quote(args)))
            return (buf, args_ok)
    # no matches
    buf = '{}{}{}'.format(
        postfix(buf), cmd, escape_quote(args))
    return (buf, False)


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


def name_unicode(ucs):
    """ Return 7-bit ascii printable of any unprintable string.
        8-bit printable unicodes are left as-is.
    """
    if ord(ucs) < ord(' ') or ord(ucs) == 127:
        ucs = r'^{}'.format(chr(ord(ucs) ^ ord('@')))
    elif not ucs.isprintable():
        ucs = r'\x{:02x}'.format(ord(ucs))
    return ucs


def _resolve_prompt(shell, input, esc_char=None):
    """ Escape prompt characters and return value, using escape value
        ``prompt_esc_char`` of matching regular expression values for
        ``prompt_escapes``, and the following value lookup table::

          '%%'     a single '%'.
          '%#'     prompt character.
          '%u'     username.
          '%h'     hostname.
          '%H'     full hostname.
          '%$'     value of session parameter following $.
          '%?'     Return code last command processed.
          '%000'   8-bit character for octal '077'.
          '%x00'   8-bit character for 16-bit hexidecimal pair.
          '%E'     Encoding of session.
          '%s'     name of shell.
          '%v'     version of shell.
          '%t'     time of day in 12-hour AM/PM format.
          '%T'     time of day in 24-hour format.
          '%p'     time of day in 12-hour format with seconds, AM/PM format.
          '%P'     time of day in 24-hour format with seconds.
          '%d      The weekday in `Day' format.
          '%D'     The day in `dd' format.
          '%w'     The month in `Mon' format.
          '%W'     The month in `mm' format.
          '%y'     The year in `yy' format.
          '%Y'     The year in `yyyy' format.
          '%z'     The timezone in `[-+]NNNN' format.
          '%Z'     The timezone name in `TZNAME' format.
      """
    esc_char = shell.prompt_esc_char if esc_char is None else esc_char
    if input == esc_char:
        return esc_char
    if input == '#':
        return shell.prompt_char
    if input == 'u':
        return shell.server.env['USER']
    if input == 'h':
        return '{}'.format(
            shell.server.server_name.result().split('.')[0]
            if shell.server.server_name.done()
            else '')
    if input == 'H':
        return '{}'.format(shell.server.server_fqdn.result()
                           if shell.server.server_fqdn.done() else
                           shell.server.server_name.result()
                           if shell.server.server_name.done()
                           else '')
    if input[0] == '$':
        return shell.server.env[input[1:]]
    if input == '?':
        if shell.retval or shell.retval == 0:
            return '{}'.format(shell.retval & 255)
        return ''
    if input.isdigit():
        return chr(int(input, 8))
    if input.startswith('x'):
        return chr(int('0x{}'.format(input[1:]), 16))
    if input == 's':
        return shell.shell_name
    if input == 'v':
        return shell.shell_ver
    if input == 'E':
        return shell.stream.__str__()
    if input == 't':
        return time.strftime('%I:%M%p')
    if input == 'T':
        return time.strftime('%H:%M')
    if input == 'p':
        return time.strftime('%I:%M:%S %p')
    if input == 'P':
        return time.strftime('%H:%M:%S')
    if input == 'd':
        return time.strftime('%a')
    if input == 'D':
        return time.strftime('%D')
    if input == 'w':
        return time.strftime('%b')
    if input == 'W':
        return time.strftime('%d')
    if input == 'y':
        return time.strftime('%y')
    if input == 'Y':
        return time.strftime('%Y')
    if input == 'z':
        return time.strftime('%z')
    if input == 'Z':
        return time.strftime('%Z')
    return input


def prompt_eval(shell, input, literal_escape=True):
    def _getter(match):
        return _resolve_prompt(shell, match.group('val'))
    return shell._eval(input, shell._re_prompt, _getter, literal_escape)
