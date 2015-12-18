# std imports
import collections
import traceback
import logging
import asyncio
import shlex
import time
import sys
import re

# local
from . import slc
from . import telopt
from .stream_reader import StreamReader
from .accessories import name_unicode

# 3rd party
import wcwidth

__all__ = ('TelnetShell', 'telnet_shell')

class Logout(Exception):
    """User-requested logout by shell."""

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

# because this shell provides editing support irregardless of LINEMODE
# negotiation, we must provide an edit command map abstraction that
# augments base SLC Editing support.
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

CR, LF, NUL = '\r\n\x00'


class TelnetShell(StreamReader):
    """
    A remote line editing shell for host command processing.

    .. warning: This is still a work in a progress, a Base class is still
        not yet defined, please do not derive!
    """
    #: Character to prefix special prompt escapes, ``prompt_escapes``
    prompt_esc_char = '%'

    #: Character for %# substitution in PS1 or PS2 evaluation
    prompt_char = '%'

    #: Character at end of line is interpreted as continuation line,
    #: joining the following line without newline when evaluated.
    line_continuation = '\\'

    #: regular expression pattern string for prompt escape sequences
    re_prompt = (r'(\d{3}'
                 r'|x[0-9a-fA-F]{2}'
                 r'|\$([a-zA-Z_]+)'
                 r'|[Ee#\?hHusvtTpPdDwWyYzZ])')

    #: regular expression pattern for echo variable matches,
    #: ``$var_33`` legal, as is ``${var:default}``.
    re_variable = (r'\$(\?|{?[a-zA-Z_0-9_:]+}?)')

    #: escape character table, '\e' becomes '\x1b', for example.
    re_literal = r'\\([abefnrtv])'

    #: Name of shell %s in prompt escape
    shell_name = 'telsh'

    #: Version of shell %v in prompt escape
    shell_ver = '0.2'  # TODO, parse version.json

    #: Display full traceback to output stream ``display_exception()``?
    show_traceback = True

    #: Initial terminal type of remote end. Callback :meth:`term_received`
    #: may suggest a new terminal type through protocol negotiation.
    _term = 'vt52'

    #: Whether to send screen addressing and video attributes.  Toggled by
    #: 'color' attribute in command shell, and updated by callback
    #: :meth:`term_received`.
    does_styling = False

    #: In callback :meth:`term_received`, If the given string starts with
    #: any of the following, set :attr:`does_styling` ``True``.
    term_does_styling = ('vt220 xterm rxvt urxvt ansi screen linux '
                         'dtterm screen'.split())

    #: If set, input character fires ``autocomplete()``
    autocomplete_char = '\t'

    #: Discover commands for this shell by matching against methods whose
    #: names begin with this value.
    cmd_prefix = 'cmdset_'

    #: If set, these chars stripped from end of line in ``line_received()``
    strip_eol = '\r\n\00'

    #: Write ASCII BELL (Ctrl - G) on error?
    send_bell = False

    #: Maximum base-10 digit that may be entered using {Ctrl -V}[0-9]+
    max_litval = 65535

    #: Maximum length of in-memory input buffer, (default is 4K)
    max_lastline = 4192

    #: Last character received by ``character_received()``.  Used internally
    #: to handle many combinations of return key that may be received.
    last_char = None

    # See class property, 'retval'.
    _retval = None

    # See class property, 'literal'.
    _literal = False

    # See class property, 'multiline'.
    _multiline = False

    def __init__(self, protocol, limit=asyncio.streams._DEFAULT_LIMIT,
                 loop=None, log=None, **kwds):
        super().__init__(protocol=protocol, limit=limit, loop=loop)
        self.protocol = protocol
        self.log = log or logging.getLogger(__name__)

        #: buffer of line input until command process.  Accessed by
        #: @property ``lastline``.
        self._lastline = collections.deque(maxlen=self.max_lastline)

        #: compiled expression for pairing by :meth:`expand_prompt`
        self._re_prompt = re.compile(
            '{esc}{pattern}'.format(
                esc=re.escape(self.prompt_esc_char),
                pattern=self.re_prompt),
            re.DOTALL)

        #: compiled expression for pairing by :meth:`expand_variable`
        self._re_variable = re.compile(self.re_variable, re.DOTALL)

        #: compiled expression for pairing by :meth:`expand_literal`
        self._re_literal = re.compile(self.re_literal, re.DOTALL)

    @property
    def writer(self):
        return self.protocol.writer

    def feed_data(self, data):
        text = self.decode(data, final=False)
        for char in text:
            self.character_received(char)

#        for byte in data:
#            self.feed_byte(bytes([byte]))
#        char = self.reader.decode(byte, final=False)
#        if char:
#            if slc_function:
#                self.editing_received(char=char,
#                                      cmd=SLC_EDIT_TRANSTABLE[slc_function])
#                return
#
#            self.character_received(char)
#
#    def feed_byte(self, byte, slc_function=None):
#        """
#        Send byte input data to command shell.
#
#        :param byte: raw byte received as input, to be negotiated by
#            shell-preferred encoding (as defined by 'CHARSET').
#        :param byte slc_function: a special line character function
#            constant defined by :mod:`telnetlib3.slc` was received,
#            as indicated by the given function byte.  Its natural
#            character is received as ``byte``.
#        """
#        if isinstance(byte, int):
#            byte = bytes([byte])
#

    def editing_received(self, char, cmd):
        """
        Callback receives unicode character with editing command, ``cmd``.

        The default implementation provides a readline-like interface
        supporting at least a set of SLC characters and various traditional
        telnet transmission reporting sequences.

        Backspace is destructive.

        Insert mode is not possible.

        :param str char: The given line editing character.
        :param int cmd: ``cmd`` matches class attributes of global instance
            :obj:`EDIT`.  This is an abstraction over SLC characters
            negotiated, or their defaults otherwise.
        """
        # mccabe: MC0001 / Telsh.editing_received is too complex (21)
        # pylint: disable=too-many-statements
        #         Too many statements (51/50)
        def _name(cmd):
            for key in dir(EDIT):
                if key.isupper() and getattr(EDIT, key) == cmd:
                    return key
        self.log.debug('editing_received: {!r}({}).'.format(
            name_unicode(char), _name(cmd)))

        # a printable ASCII representation the given character
        char_disp = name_unicode(char)

        if self.is_literal is not False:
            # continue literal input for matching editing characters
            self.literal_received(char)

        elif cmd == EDIT.LNEXT:
            # begin literal input (Ctrl - V)
            self.literal_received(char)

        elif cmd == EDIT.RP:
            # repaint (Ctrl - R)
            self.display_prompt(redraw=True)

        elif cmd == EDIT.EC:
            # erase character chr(127)
            if 0 == len(self._lastline):
                self.bell()
                self.display_prompt(redraw=True)
                return

            self.writer.write(self.erase(self._lastline.pop(), char))

        elif cmd == EDIT.EW:
            # erase word (^w), rubout .(\w+)
            if not self._lastline:
                self.bell()
                return

            string = ''

            # erase any space,
            while self._lastline and self._lastline[-1].isspace():
                string += self._lastline.pop()

            # then, erase any non-space
            while self._lastline and not self._lastline[-1].isspace():
                string += self._lastline.pop()

            if not string:
                # bell if erase-word at beginning of line
                self.bell()

            else:
                # or re-draw prompt when anything erased,
                self.display_prompt(redraw=True)

        elif cmd == EDIT.EL:
            # erase line (Ctrl - L)
            self._lastline.clear()
            self.display_prompt(redraw=True)

        elif cmd == EDIT.EOF:
            # end of file (Ctrl - D)
            if self._lastline:
                # error when input line is non-empty
                self.bell()
                return

            # logout when input line is empty and EOF received
            self.writer.write(char_disp)
            self.protocol.connection_lost(exc=Logout('logout by EOF'))

        elif cmd == EDIT.AYT:
            # are-you-there? (Ctrl - T)
            self._lastline.clear()
            self.writer.write(char_disp)
            self.display_status()
            self.display_prompt()

        elif cmd in (EDIT.IP, EDIT.ABORT,):
            # interrupt process (Ctrl - C), abort process (Ctrl - \)
            self._lastline.clear()
            self.writer.write(char_disp)
            self.display_prompt()

        elif cmd in (EDIT.XOFF, EDIT.XON,):
            # transmit-off (Ctrl - S), transmit-on (Ctrl - Q)
            raise NotImplementedError("Still working on it.")

        elif cmd in (EDIT.AO,):
            self.display_prompt()

        else:
            # not handled or implemented
            self.log.debug('{} unhandled.'.format(EDIT.name(cmd)))
            self._lastline.append(char)
            self.writer.write(char_disp)

    def literal_received(self, char):
        """
        Callback on receipt of *literal-next* and subsequent characters.

        :param str char: character received.
        :rtype: None

        Receives literal character ``EDIT.LNEXT``, normally ``Ctrl - V``,
        and all subsequent characters until the literal character sequence
        is completed.

        This allows the insertion of arbitrary characters into the shell
        that may conflict with an editing command character, or the user
        is otherwise incapable of generating.
        """
        self.log.debug('literal_received: {!r}'.format(char))
        if self.is_literal is False:
            self.log.debug('begin marker {!r}'.format(name_unicode(char)))
            self.writer.write(self._standout('^\b'))
            self._literal = -1
            return

        val = 0
        if self._literal != -1:
            val = int(self._literal)

        self.log.debug('literal continued: {!r}, {!r}'.format(val, char))

        if ord(char) < 32:
            # entering a raw control character,
            if char in (CR, LF) and self._literal != -1:
                # when CR or LF is received, send as-is & cancel,
                self.character_received(chr(val), literal=True)
                self._literal = False
                return

            if self._literal != -1:
                # before sending control character, send current value,
                self.character_received(chr(val), literal=True)

            # send raw control character to ``character_received``
            self.character_received(char, literal=True)
            self._literal = False
            return

        if self.max_litval and ord('0') <= ord(char) <= ord('9'):
            # entering a base-10 digit
            self._literal = (val * 10) + int(char)

            # If our maximum value has been reached or exceeded, or,
            # our string literal length is equal to the maximum value's
            # length, terminate the base 10 input literal input sequence.
            str_val = '{}'.format(self._literal)
            str_max = '{}'.format(self.max_litval)
            if (self._literal >= self.max_litval or
                    len(str_val) == len(str_max)):
                char = chr(min(self._literal, self.max_litval))
                self.character_received(char, literal=True)
                self._literal = False
            return

        self._lastline.append(char)

        if self.writer.will_echo:
            self.writer.echo(self._standout(name_unicode(char)))
        else:
            self.display_prompt(redraw=True)

        self._literal = False

    @property
    def is_literal(self):
        """
        Whether the shell is currently in "literal next" input mode.

        ``True`` if the ``EDIT.LNEXT`` character, *Ctrl - V* was received,
        and any subsequent character should be delegated to
        :meth:`literal_received` for continuation.
        """
        return self._literal is not False

    def character_received(self, char, literal=False):
        """
        Receive a single (non-editing) Unicode character.

        :param str char: Character to be received by shell prompt.
        :param bool literal: The given character is a continuation
            of a literal, induced by Control - V by default.
        """
        # mccabe: MC0001 / Telsh.character_received is too complex (13)
        # a printable ASCII representation of unprintables,
        if literal:
            self._lastline.append(char)
            self.display_prompt(redraw=True)

        elif self.is_literal and not literal:
            self.literal_received(char)

        elif self.last_char == CR and char in (LF, NUL):
            if not self.strip_eol:
                # ``strip_eol`` is False, preserve '\n' or '\x00'
                self._lastline.append(char)
            # otherwise, ``strip_eol`` is True, pass on '\n' or '\x00'
            # following CR

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

        elif char == '\x0c':
            # Ctrl - L, refresh
            self.display_prompt(redraw=True)

        elif (self.writer.mode != 'local' and
              self.autocomplete_char and
              char == self.autocomplete_char):

            # perform tab completion for non-local line editing modes
            completed = self.autocomplete(self.lastline)

            if completed is None:
                # no match
                self.bell()

            else:
                # command completed
                self._lastline.clear()
                self._lastline.extend(completed)
                self.display_prompt(redraw=True)

        else:
            # add new input character.
            self._lastline.append(char)

            # echo as output.
            char_disp = char
            if self.writer.can_write(char) and char.isprintable():
                char_disp = self._standout(name_unicode(char))
            self.writer.echo(char_disp)

        # it is necessary on receipt of subsequent characters to backtrack
        # the previous, most especially for carriage return.
        self.last_char = char

    def line_received(self, string):
        """
        Callback for each line received.

        :param str string: line received from remote client.
        """
        self.log.debug('line_received: {!r}'.format(string))
        if self.strip_eol:
            string = string.rstrip(self.strip_eol)
        if self.multiline:
            string = ' '.join(string.split('\r'))

        # pylint: disable=broad-except
        #         Catching too general exception
        try:
            retval = self.process_cmd(string)
        except Exception:
            # display traceback and exception to client and
            # forward-copy the same exception message to DEBUG log.
            self.display_exception(*sys.exc_info(), tee=self.log.debug)
            self.bell()
            retval = -1

        if retval == '':
            # incomplete quoted character, we continue
            # a line continuation, no command processed.
            self._multiline = True
            self.display_prompt(redraw=True)

        else:
            # store return value
            self._retval = retval

            # clear line buffer and display prompt
            self._lastline.clear()
            self.display_prompt()

    def erase(self, string, keypress='\x08'):
        """
        Return sequence for erasing ``string`` leading to the cursor position.

        :param str string: the string requiring erasure.
        :param str keypress: The raw unicode sequence issued, some
            implementations make perform a line editing between
            ``(chr(8), chr(127))`` as functions ``(BACKSPACE, DELETE)``.
        :returns: result string should be written to perform screen erasure.
        :rtype: str

        This method is a callback from :meth:`editing_received`, and is
        necessary to determine the printable width of some unicode characters,
        and the display width our shell uses for special characters, such as
        erasing over raw control characters.

        This implementation always performs the backspace function, but
        makes a distinction in determined carriage location of remote end
        and always terminates the sequence with ``clear_eol``.
        """
        if keypress not in (chr(127), chr(8)):
            raise TypeError("Only backspace and delete characters supported, "
                            "given keypress={0}".format(keypress))

        def disp_char(char):
            # this differs from the function of the same name
            # in `~.display_prompt` and `~.character_received`
            # by using self._standout.
            if self.writer.can_write(char) and char.isprintable():
                return char
            return name_unicode(char)

        disp_text = ''.join([disp_char(char) for char in string])
        vtlen = wcwidth.wcswidth(disp_text)
        assert vtlen >= 0, (string, disp_text, vtlen)

        # non-BSD clients are generally will echo,
        if self.will_echo:
            return ('\b' * vtlen) + '\x1b[K'

        # BSD has strange behavior for line editing with local echo,
        #
        # (1) '^?' actually advances the cursor right one cell,
        if keypress == chr(127):
            # it is a strange mode to send 127 in -- this sort of mode
            # should be used in half-duplex, but our shell makes no
            # distinction, and erases an extra character

            return '\b' + ('\b' * vtlen) + '\x1b[K'

        # (2) whereas '^h' moves the cursor left one (displayable) cell !
        return '\b' * (vtlen - 1) + '\x1b[K'

    def term_received(self, term):
        """
        Callback on receipt of new terminal type, by negotiation.

        :param str term: Terminal type.

        The deault implementation sets class attributes :attr:`term`
        and :attr:`does_styling`, and logs a debug statement.
        """
        self.does_styling = any(map(term.startswith, self.term_does_styling))

        # pylint: disable=redundant-keyword-arg
        #         Argument 'self' passed by position and keyword in method call
        self.log.debug('term={self.term}, does_styling={self.does_styling}.'
                       .format(self=self))

    def winsize_received(self, rows, cols):
        """
        Callback on receipt of new window size dimensions, by negotiation.

        :param int rows: new screen height.
        :param int cols: new screen width.

        The default implementation only logs a debug statement.
        """
        self.log.debug('new size: rows={0}, cols={1}'.format(rows, cols))

    def bell(self):
        r"""
        Convenience function, writes ASCII bell alert issued by shell.

        When :attr:`send_bell` is False, nothing happens!
        """
        if self.send_bell:
            self.writer.write('\a')

    @property
    def prompt(self):
        """
        Command prompt string for current multi-line context.

        The result of environment value of ``PS1`` or ``PS2`` when
        :attr:`multiline`, expanded for special prompt characters, variables,
        and literal characters.
        """
        # prompt character expansion
        # XXX
        #_ps1 = self.server.env.get('PS1', '%s-%v %# ')
        #_ps2 = self.server.env.get('PS2', '> ')
        _ps1, _ps2 = '%s-%v %# ', '> '
        result = self.expansion(
            string=_ps2 if self.multiline else _ps1,
            pattern=self._re_prompt,
            getter=lambda match: self.expand_prompt(match.group(1))
        )
        # variable expansion
        result = self.expansion(
            string=result,
            pattern=self._re_variable,
            getter=lambda match: self.expand_variable(match.group(1))
        )
        # literals
        result = self.expansion(
            string=result,
            pattern=self._re_literal,
            getter=lambda match: self.expand_literal(match.group(1))
        )
        return result

    def display_prompt(self, redraw=False):
        """
        Display prompt to client end.

        :param bool redraw: When ``True``, display a carriage return,
            followed by VT52 ``clear_eol`` sequence.  This is used to
            accompany EDIT.RP (repaint, Ctrl - V).  Otherwise, only
            ``CR + LF`` (default) is used before displaying prompt.
        :rtype: None
        """
        def disp_char(char):
            # this differs from the function of the same name
            # in `~.erase` by using self._standout.
            if self.writer.can_write(char) and char.isprintable():
                return char
            return self._standout(name_unicode(char))

        text = self.lastline
        if self.multiline:
            text = self.lastline.splitlines()[-1]

        prefix = '\r\n'
        if redraw:
            prefix = '\r\x1b[K'

        disp_text = ''.join([disp_char(char) for char in text])
        output = ''.join((prefix, self.prompt, disp_text,))
        self.writer.write(output)
        #self.stream.send_ga()

    def display_status_details(self, tee=None):
        """
        Display server stream negotiation status to client end.

        :param callable tee: Callback receives each line of parsed
            traceback.  When ``None``, defaults to ``self.log.debug``.
            Set to ``False`` to disable.
        """
        if tee is None:
            tee = self.log.debug

        # what the server has demanded by IAC 'DO'.
        server_do = [cmd for cmd, enabled in
                     self.writer.remote_option.items()
                     if enabled]

        # what the client has promised by IAC 'WILL'.
        client_will = [cmd for cmd, enabled in
                       self.writer.local_option.items()
                       if enabled]

        # unacknowledged option negotiation, often failed replies.
        pending = [cmd_args for cmd_args, _pending in
                   self.writer.pending_option.items()
                   if _pending]

        details = [
            'server: DO {0}'.format(
                ' '.join(telopt.name_command(cmd)
                         for cmd in server_do)),
            'client: WILL {0}'.format(
                ' '.join(telopt.name_command(cmd)
                         for cmd in client_will)),
            'failed-reply: {0}'.format(
                ' '.join(telopt.name_commands(cmd_args)
                         for cmd_args in pending)),
        ]

        self.writer.write('\r\n' + '\r\n'.join(details))
        if tee:
            map(tee, details)

    def display_status(self, tee=None):
        """
        Display session status to client end.

        :param callable tee: Callback receives each line of parsed
            traceback.  When ``None``, defaults to ``self.log.debug``.
            Set to ``False`` to disable.
        """
        if tee is None:
            tee = self.log.debug

#        origin = self.server.client_ip
#        if self.server.client_fqdn.done():
#            origin = self.server.client_fqdn.result()

        xon = 'xon'
        if self.writer.xon_any:
            xon = 'xon-any'

        # TODO this all ..

        #kwds = {'origin': origin, 'xon': xon, 'self': self}
        kwds = {'xon': xon, 'self': self}
        summary = [
            'connected: {self.protocol.duration:0.1f}s ago'.format(**kwds),
#            'from: {origin}'.format(**kwds),
            'linemode: {self.writer.mode}'.format(**kwds),
#            'writer: {self.writer}'.format(**kwds),
#            'flow control is {xon}'.format(**kwds),
#            'rows: {self.server.env[LINES]}'.format(**kwds),
#            'cols: {self.server.env[COLUMNS]}'.format(**kwds),
        ]

        self.writer.write('\r\n' + '\r\n'.join(summary))
        if tee:
            map(tee, summary)

    @property
    def multiline(self):
        r"""
        Whether current prompt is a continuation-line mode (PS2).

        When True, the property :attr:`lastline` contains ``'\r'``
        marking each continuation record.
        """
        return self._multiline

    @property
    def lastline(self):
        """Current input line."""
        return ''.join(self._lastline)

    @property
    def retval(self):
        """Exit status of last command executed."""
        return self._retval if self._retval is not None else ''

    @property
    def term(self):
        """
        Return telnet-negotiated terminal type.

        Note that the value ``self.server.env[TERM]`` might be changed
        by shell, but this property only returns the telnet-negotiated
        value as received by callback :meth:`term_received`
        """
        return self._term

    @property
    def autocomplete_cmdset(self):
        """List of commands instance method ``cmdset_{pattern}`` provides."""
        fn_cmds = filter(self.is_a_command, sorted(dir(self)))

        return [fn_name[len(self.cmd_prefix):]
                for fn_name in fn_cmds]

    def display_exception(self, *exc_info, tee=None):
        """
        Display exception to client when :attr:`show_traceback` is ``True``.

        :param exc_info: Return value of :func:`sys.exc_info`.
        :param callable tee: Callback receives each line of parsed
            traceback.  When ``None``, defaults to ``self.log.debug``.
            Set to ``False`` to disable.
        :rtype: None
        """
        tbl_exception = (
            traceback.format_tb(exc_info[2]) +
            traceback.format_exception_only(exc_info[0], exc_info[1]))

        if tee is None:
            tee = self.log.debug

        for num, tb_string in enumerate(tbl_exception):
            if self.show_traceback:
                self.writer.write('\r\n'.join((
                    '',
                    '\r\n'.join(
                        self._standout(line.rstrip())
                        if num == len(tbl_exception) - 1
                        else line.rstrip()
                        for line in tb_string.splitlines()),
                )))

            if tee:
                map(tee, tb_string.splitlines())

    def process_cmd(self, string):
        """
        Callback from :meth:`line_received` for input line processing.

        The default implementation supports multiple commands by
        semicolon (``';'``) executed in left to right order.
        :func:`shlex.split` is used to parse quoted arguments, and
        allow entering of continuation line.

        :rtype: int or str.
        :returns: ``u''`` when the given command enters a continuation
            line (a quoted argument is not yet completed), otherwise the
            command exit value: 0 meaning success, non-zero meaning failure.
        """
        self.writer.write('\r\n')
        commands = []
        for cmd_args in string.split(';'):
            cmd, args = cmd_args.rstrip(), []
            if ' ' in cmd:
                try:
                    cmd, *args = shlex.split(cmd)
                except ValueError as err:
                    self.log.debug(err)
                    if err.args == ('No closing quotation',):
                        self._lastline.append('\r')  # use '\r' ..
                        return ''
                    elif (err.args == ('No escaped character',) and
                          cmd.endswith(self.line_continuation)):
                        return ''
                    raise err
            commands.append((cmd, args))

        if not commands:
            return 0

        for cmd, args in commands:
            retval = self.command(cmd, *args)

        return retval

    def command(self, cmd, *args):
        """
        Callback executes the given command after argument evaluation.

        :param str cmd: command to issue: Where command ``echo`` is given,
            the pairing instance method ``cmdset_echo`` is called with the
            given positional arguments.
        :returns: exit value as integer, 0 on success, 1 if not found.
        """
        self.log.debug('command {!r} {!r}'.format(cmd, args))
        if not len(cmd) and not len(args):
            return None

        if not cmd:
            return 0  # nothing happens!

        method_name = '{0}{1}'.format(self.cmd_prefix, cmd)
        if self.is_a_command(method_name):
            return getattr(self, method_name)(*args)

        # safely display user input back with 'command not found.'
        disp_cmd = ''.join([name_unicode(char) for char in cmd])
        self.writer.write('{!s}: command not found.'.format(disp_cmd))
        return 1

    def is_a_command(self, f_name):
        """
        Whether the class method pointed to by ``f_name`` is a shell command.

        :param str f_name: name of method of this class instance.
        :rtype: bool
        """
        return bool(f_name.startswith(self.cmd_prefix) and
                    hasattr(self, f_name) and
                    callable(getattr(self, f_name)))

    def cmdset_help(self, *args):
        """
        Display Help, or help for command in given argument.

        :rtype: int
        :returns: shell return code, 0 is success (help displayed).
            1 is returned if help is requested for a command that is
            not available.
        """
        if not len(args):
            self.writer.write('Available commands:\r\n')
            self.writer.write(', '.join(self.autocomplete_cmdset))
            return 0
        cmd = args[0].lower()

        method_name = '{0}{1}'.format(self.cmd_prefix, cmd)
        if not self.is_a_command(method_name):
            self.writer.write('help: not a command, {0}.'.format(cmd))
            return 1

        # get doc string
        method = getattr(self, method_name)
        docstr = method.__doc__
        if docstr is None:
            docstr = 'help: No help available for {0}.'.format(cmd)
            return 0

        # we avoid leaking into inner sphinx abstractions, and just
        # use the pep257-compliant 1-line sentence summary.
        help_summary = (docstr.splitlines() or [''])[0]
        self.writer.write('{0}: {1}'.format(cmd, help_summary))
        return 0

    def assign(self, *args):
        """
        Method callback for :meth:`cmdset_set` of variable assignment.

        :param args: each argument in string of form, ``'{key}={value}'``.
        :rtype: int
        :returns: shell return code, 0 is success (assigned).
        """
        if not len(args):
            return -1
        elif len(args) > 1:
            # x=1 y=2; evaluates right-left recursively
            return self.assign(*args[1:])   # RECURSE

        # allow ValueError to unpack naturally and report to client.
        key, val = args[0].split('=', 1)

# XXX
        return 0
#ifdef
#        if key in self.server.readonly_env:
#            # variable is read-only
#            return -2
#
#        if not val:
#            # unset variable (set blank)
#            self.server.env_update({key: ''})
#            return 0
#
#        if not key:
#            # cannot assign empty string
#            return -1
#
#        self.server.env_update({key: val})
#        return 0
#endif

    def cmdset_status(self, *args):
        """
        Display session status.

        :rtype: int
        :returns: shell return code, 0 is success (displayed).
        """
        if len(args):
            self.writer.write('status: too many arguments.')
            return 1
        self.display_status(tee=self.log.debug)
        self.display_status_details(tee=self.log.debug)
        return 0

    def cmdset_quit(self, *args):
        """
        Disconnect from server.

        :rtype: int
        :returns: shell return code, 0 is success (logged off).
        """
        retval = 0
        if len(args):
            # even with too many arguments, logoff anyway
            self.writer.write('quit: too much arguing.')
            retval = 1
        self.protocol.connection_lost(exc=Logout('logout by command'))
        return retval

    def cmdset_raise(self, *args):
        """
        Raise RuntimeError with given exception message string.

        :raises RuntimeError: always.
        """
        # pylint: disable=no-self-use
        raise RuntimeError('{0}'.format(' '.join(args)))

    def cmdset_whoami(self, *args):
        """
        Display session information.

        :rtype: int
        :returns: shell return code, 0 is success (displayed).
        """
        if len(args):
            self.writer.write('whoami: too many arguments.')
            return 1
        self.writer.write('{}.'.format(self.protocol))
        return 0

    def cmdset_echo(self, *args):
        """
        Display arguments, performing literal and variable expansion.

        :params args: each argument result is joined by space(' ') in output.
        :rtype: int
        :returns: shell return code, 0 is success (displayed).
        """
        def echo_eval(string):
            # variable expansion,
            result = self.expansion(
                string=string,
                pattern=self._re_variable,
                getter=lambda match: self.expand_variable(match.group(1))
            )

            # literals, note: by this order, variables expand their literals.
            result = self.expansion(
                string=result,
                pattern=self._re_literal,
                getter=lambda match: self.expand_literal(match.group(1))
            )
            return result

        output = ' '.join(echo_eval(arg) for arg in args)
        self.writer.write('{}'.format(output))
        return 0

    def cmdset_slc(self, *args):
        """
        Display Special Line Editing (SLC) characters.

        :rtype: int
        :returns: shell return code, 0 is success (toggled or displayed).
        """
        if len(args):
            self.writer.write('slc: too many arguments.')
            return 1
        self.writer.write('Special Line Characters:\r\n{}'.format(
            '\r\n'.join(['{:>10}: {}'.format(
                slc.name_slc_command(slc_func), slc_def)
                for (slc_func, slc_def) in sorted(
                    self.writer.slctab.items())
                if not (slc_def.nosupport or slc_def.val == slc.theNULL)])))
        self.writer.write('\r\n\r\nUnset by client: {}'.format(
            ', '.join([slc.name_slc_command(slc_func)
                       for (slc_func, slc_def) in sorted(
                           self.writer.slctab.items())
                       if slc_def.val == slc.theNULL])))
        self.writer.write('\r\n\r\nNot supported by server: {}'.format(
            ', '.join([slc.name_slc_command(slc_func)
                       for (slc_func, slc_def) in sorted(
                           self.writer.slctab.items())
                       if slc_def.nosupport])))
        self.writer.write('\r\n')
        return 0

    def get_toggle_options(self):
        """
        Return options that may be toggled, valued by state.

        Callback for method :meth:`cmdset_toggle`.

        :rtype: dict
        :returns: mapping of options that may be toggled using the
            shell ``toggle`` command, valued by their boolean state.
        """
        return {
            'echo':
                self.writer.local_option.enabled(telopt.ECHO),
            'outbinary':
                self.protocol.outbinary,
            'inbinary':
                self.protocol.inbinary,
            'binary':
                self.protocol.outbinary and self.protocol.inbinary,
            'color':
                self.does_styling,
            'xon-any':
                self.writer.xon_any,
            'lflow':
                self.writer.lflow,
            'bell':
                self.send_bell,
            'goahead': (
                not self.writer.local_option.enabled(telopt.SGA) and
                self.protocol.send_go_ahead),
        }

    def cmdset_toggle(self, *args):
        """
        Display or toggle telnet session parameters.

        :rtype: int
        :returns: shell return code, 0 is success (toggled or displayed).
        """
        # mccabe: MC0001 / Telsh.cmdset_toggle is too complex (12)
        tbl_opt = self.get_toggle_options()

        # display all toggle options, 4 per line.
        if len(args) is 0:
            self.writer.write(', '.join(
                '{}{} [{}]'.format('\r\n' if num and num % 4 == 0 else '',
                                   opt, self._standout('ON') if enabled
                                   else self._dim('off'))
                for num, (opt, enabled) in enumerate(
                    sorted(tbl_opt.items()))))
            return 0

        opt = args[0].lower()
        if len(args) > 1:
            self.writer.write('toggle: too many arguments.')
            return 1

        elif args[0] not in tbl_opt and opt != 'all':
            self.writer.write('toggle: not option.')
            return 1

        # this can't get a lot more simple than a long case sequence,
        # the 'all' toggle certainly ensures code coverage.  some toggles
        # warrant modifying the negotiation state for telnet options,
        # and others the shell.
        if opt in ('echo', 'all'):
            cmd = (telopt.WONT if tbl_opt['echo'] else telopt.WILL)
            self.writer.iac(cmd, telopt.ECHO)
            self.writer.write('{} echo.{}'.format(
                telopt.name_command(cmd).lower(),
                opt == 'all' and '\r\n' or ''))
        if opt in ('outbinary', 'binary', 'all'):
            cmd = (telopt.WONT if tbl_opt['outbinary'] else telopt.WILL)
            self.writer.iac(cmd, telopt.BINARY)
            self.writer.write('{} outbinary.{}'.format(
                telopt.name_command(cmd).lower(),
                opt == 'all' and '\r\n' or ''))
        if opt in ('inbinary', 'binary', 'all'):
            cmd = (telopt.DONT if tbl_opt['inbinary'] else telopt.DO)
            self.writer.iac(cmd, telopt.BINARY)
            self.writer.write('{} inbinary.{}'.format(
                telopt.name_command(cmd).lower(),
                opt == 'all' and '\r\n' or ''))
        if opt in ('goahead', 'all'):
            cmd = (telopt.WILL if tbl_opt['goahead'] else telopt.WONT)
            self.protocol.send_go_ahead = cmd is telopt.WONT
            self.writer.iac(cmd, telopt.SGA)
            self.writer.write('{} suppress go-ahead.{}'.format(
                telopt.name_command(cmd).lower(),
                opt == 'all' and '\r\n' or ''))
        if opt in ('bell', 'all'):
            self.send_bell = not tbl_opt['bell']
            self.writer.write('bell {}abled.{}'.format(
                'en' if self.send_bell else 'dis',
                opt == 'all' and '\r\n' or ''))
        if opt in ('xon-any', 'all'):
            self.writer.xon_any = not tbl_opt['xon-any']
            self.writer.send_lineflow_mode()
            self.writer.write('xon-any {}abled.{}'.format(
                'en' if self.writer.xon_any else 'dis',
                opt == 'all' and '\r\n' or ''))
        if opt in ('lflow', 'all'):
            self.writer.lflow = not tbl_opt['lflow']
            self.writer.send_lineflow_mode()
            self.writer.write('lineflow {}abled.{}'.format(
                'en' if self.writer.lflow else 'dis',
                opt == 'all' and '\r\n' or ''))
        if opt in ('color', 'all'):
            self.does_styling = not tbl_opt['color']
            self.writer.write('color {}.'.format(
                'on' if self.does_styling else 'off',))
        return 0

    def cmdset_set(self, *args):
        """
        Set or display variable expression.

        :param args: Of each argument, for string pattern ``key``,
            display value.  For pattern ``key=value``, set value.
            For pattern ``key=``, unset value.
        :rtype: int
        :returns: shell return code, 0 is success (assigned).
        """
        def disp_kv(key, val):
            return shlex.quote(val)
            # Display shell-escaped version of value ``val`` of ``key``.
            # Uses terminal 'dim' attribute for read-only variables.
#            return (
#                self._dim(shlex.quote(val))
#                if key in self.server.readonly_env
#                else shlex.quote(val))

        retval = 0
#        for arg in args:
#            if '=' in arg:
#                retval = self.assign(arg)
#            elif arg in self.server.env:
#                key = arg
#                val = self.server.env[arg]
#                self.writer.write('{key}={val}'.format(
#                    key=key,
#                    val=disp_kv(key, val)))
#                retval = 0  # displayed query
#            else:
#                retval = -1  # query unmatched
#
#        if not args:
#            # display all values, in sorted, filtered order
#            sorted_keyvalues = [
#                (_key, _val) for (_key, _val)
#                in sorted(self.server.env.items())
#                if _key]
#            self.writer.write('\r\n'.join(['{key}={val}'.format(
#                key=_key,
#                val=disp_kv(_key, _val))
#                for _key, _val in sorted_keyvalues]))
        return retval

    def expand_variable(self, string):
        """
        Expand variable by name ``string`` to value.

        :param str var_name: A Server environment variable name.
        :rtype: str
        :returns: variable name expanded to its value.  Returns
            empty string (``''``) when no such variable is found.

        This method called when matched by :attr:`~.re_variable` pattern.

        The default expanding value can be set using phrase
        ``{var_name:default}``.  For example, the server ``PS1`` variable
        could contain phrase ``${USER:unknown}``, conditionally displaying
        the value of variable ``USER``, defaulting to word ``unknown`` when
        undefined.
        """
        if string == '?':
            # return code
            if self._retval is not None:
                return '{}'.format(self._retval)

            # no previous return code
            return ''

        default = u''
        if string.startswith('{') and string.endswith('}'):
            # remove braces,
            string = string.strip('{}')

            # allow ${val:default},
            if ':' in string:
                string, default = string.split(':', 1)

        return default
#        return self.server.env.get(string, default)

    def expand_literal(self, string):
        r"""
        Return literal for strings matching literal escapes.

        :param str string: An escape literal, such as ``\n``
        :rtype: str
        :returns: literal expanded, ``\x0a`` in this case.
                  When unmatched, string is returned as-is.

        This method called when matched by :attr:`~.re_literal` pattern.
        """
        return {
            'a': '\a',    # bell
            'b': '\b',    # backspace
            'e': '\x1b',  # escape
            'f': '\f',    # linefeed
            'n': '\n',    # newline
            'r': '\r',    # carriage return
            't': '\t',    # horizontal tab
            'v': '\v',    # vertical tab
        }.get(string, string)

    @staticmethod
    def expansion(string, pattern, getter):
        """
        Return string, pattern substituted by getter.

        :param str string: given input string for expansion.
        :param pattern: :func:`re.compile` result for pattern matching.
        :param callable getter: given the non-None match result of
            callable :paramref:`~.expansion.pattern` as first argument,
            a callable returning string that the given pattern should
            expand to.
        """
        assert callable(getter), getter
        output = collections.deque()
        start_next = 0
        for pos in range(len(string)):
            if pos >= start_next:
                match = pattern.match(string[pos:])
                if match:
                    output.extend(getter(match))
                    start_next = pos + match.end()
                    continue

                output.append(string[pos])
        return ''.join(output)

    def expand_prompt(self, char, timevalue=None):
        """
        Resolve the prompt (PS1, PS2) values for special characters and return.

        :param str char: pattern contents without leading
            :attr:`prompt_esc_char`.

        Class attribute :attr:`prompt_esc_char` is used in the building from
        :attr:`re_prompt` for in this function to perform the following pattern
        expansion:

        =========  ==========
        Pattern    Expands to
        =========  ==========
        ``'%%'``   A single '%'.
        ``'%#'``   Prompt character.
        ``'%u'``   Username.
        ``'%h'``   Hostname.
#        ``'%H'``   Full hostname.
        ``'%$'``   Value of session parameter following $.
        ``'%?'``   Return code last command processed.
        ``'%00'``  8-bit character string for octal '077'.
        ``'%x0'``  8-bit character string for 16-bit hexadecimal pair.
        ``'%E'``   Encoding of session.
        ``'%s'``   Name of shell.
        ``'%v'``   Version of shell.
        ``'%t'``   Time of day in 12-hour AM/PM format.
        ``'%T'``   Time of day in 24-hour format.
        ``'%p'``   Time of day in 12-hour format with seconds,
                   AM/PM format.
        ``'%P'``   Time of day in 24-hour format with seconds.
        ``'%d ``   The weekday in 'Day' format.
        ``'%D'``   The day in 'dd' format.
        ``'%w'``   The month in 'Mon' format.
        ``'%W'``   The month in 'mm' format.
        ``'%y'``   The year in 'yy' format.
        ``'%Y'``   The year in 'yyyy' format.
        ``'%z'``   The timezone in '[-+]NNNN' format.
        ``'%Z'``   The timezone name in 'TZNAME' format.
        =========  ==========
        """
        if timevalue is None:
            timevalue = time.localtime()

        # although this allows some form of chaining (multiple 'subscriptions'
        # to the same pattern), highly suggest against it, what do you think?
        return self._expand_prompt_simple(
            char, self._expand_prompt_complex(
                char, self._expand_prompt_other(
                    char, char, timevalue=timevalue)))

    def autocomplete(self, cmd_input):
        """
        Callback for receipt of :attr:`autocomplete_char`.

        :param str cmd_input: given command prompt
        :returns: completed command, or ``None`` if not completed.
        """
        result = None
        if cmd_input.strip():
            for tgt_cmd in self.autocomplete_cmdset:
                if tgt_cmd.startswith(cmd_input):
                    result = tgt_cmd
                    break
        self.log.debug('autocomplete: {0!r} -> {1!r}'
                       .format(cmd_input, result))
        return result

    def _expand_prompt_simple(self, char, default):
        """
        Resolve and return simple value expansions of prompt string character.

        :param str char: characters for the given expansion pattern, such
            as ``'t'`` of phrase ``'%#'``.  This method resolves characters
            ``#``, ``u``, ``$``, ``s``, ``v``, and ``E``.
        :param str default: the default value to resolve to when unmatched.
        :rtype: str
        :returns: resolved expansion or ``default`` when unexpanded.
        """
        return {
            '#': self.prompt_char,
            'u': '', #self.server.env['USER'],
            '$': '', #self.server.env[char[1:]],
            's': self.shell_name,
            'v': self.shell_ver,
            'E': self.writer.__str__(),
        }.get(char, default)

    def _expand_prompt_complex(self, char, default):
        """
        Resolve and return complex value expansions of prompt string character.

        :param str char: characters for the given expansion pattern, such
            as ``'h'`` of phrase ``'%h'``.  This method resolves characters
            ``h``, ``H``, and ``?``.
        :param str default: the default value to resolve to when unmatched.
        :rtype: str
        :returns: resolved expansion or ``default`` when unexpanded.
        """
        val = None
        if char == '?':
            val = ''
            if self.retval or self.retval == 0:
                val = '{}'.format(self.retval & 255)
        if val is not None:
            return val
        return default

    @staticmethod
    def _expand_prompt_other(char, default, timevalue):
        """
        Resolve and return digit and timevalue expansions of prompt character.

        :param str char: characters for the given expansion pattern.  This
            method resolves ``t``, ``T``, ``p``, ``P``, ``d``, ``D``,
            ``w``, ``W``, ``y``, ``Y``, ``z``, ``Z`` for :func:`time.strftime`
            values, as well as octal and hexadecimal, in form ``033`` or
            ``x1b`` patterns as example.
        :param str default: the default value to resolve to when unmatched.
        :param time.struct_time timevalue: timestruct to represent by
            :func:`time.strftime` translations.
        :rtype: str
        :returns: resolved expansion or ``default`` when unexpanded.
        """
        strftime_mapping = {
            't': '%I:%M%p',
            'T': '%H:%M',
            'p': '%I:%M:%S %p',
            'P': '%H:%M:%S',
            'd': '%a',
            'D': '%D',
            'w': '%b',
            'W': '%d',
            'y': '%y',
            'Y': '%Y',
            'z': '%z',
            'Z': '%Z',
        }
        if char.isdigit():
            return chr(int(char, 8))
        if char.startswith('x'):
            return chr(int('0x{}'.format(char[1:]), 16))
        if char in strftime_mapping:
            return time.strftime(strftime_mapping[char], timevalue)
        return default

    def _dim(self, string):
        """Return ``string`` decorated using preferred terminal sequence."""
        _dim, _normal = '\x1b[31m', '\x1b[0m'
        return (string if not self.does_styling
                else (_dim + string + _normal))

    def _standout(self, string):
        """Return ``string`` decorated using preferred terminal sequence."""
        _standout, _normal = '\x1b[31;1m', '\x1b[0m'
        return (string if not self.does_styling
                else (_standout + string + _normal))

    @property
    def will_echo(self):
       return self.writer.local_option.enabled(telopt.ECHO)

@asyncio.coroutine
def telnet_shell(reader, writer):
    writer.write('Would you like to play a game? ')
    resp = yield from reader.readline()
    writer.write('\r\nThe only way to win is to not play at all.\r\n')
    writer.close()
