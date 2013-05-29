#!/usr/bin/env python3
import collections
import traceback
import datetime
import argparse
import logging
import codecs
import shlex
import socket
import time
import sys
import re

import tulip
import telopt
import teldisp
#import editing
from slc import name_slc_command
    # XXX
    # TODO: EditingStreamReader.character_received()
     # _will_timeout use future instead

def wrap_future_result(future, result):
    future = tulip.Future()
    future.set_result(result)
    return future

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
    default_env = {'COLUMNS': '80',
                   'LINES': '24',
                   'USER': 'unknown',
                   'TERM': 'unknown',
                   'CHARSET': 'ascii',
                   'PS1': '[%u@%h] %# ',
                   'PS2': '> ',
                   'TIMEOUT': '120',
                   }

    readonly_env = ['USER', 'HOSTNAME', 'UID']
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
    #: regular expression pattern that matches 1 or more characters following
    #  the prompt escape character (``prompt_esc_char``), which should be
    #  handled by prompt_esc().
    #: character used to prefix special prompt escapes, ``prompt_escapes``
    prompt_esc_char = '%'
    #: character used for %# substituion in PS1 or PS2 evaluation
    prompt_char = '%'

    def __init__(self, log=logging, default_encoding='utf8'):
        self.log = log
        #: cient_env holds client session variables
        self._client_env = collections.defaultdict(str, **self.default_env)
        self._client_env['CHARSET'] = default_encoding
        self._will_timeout = (
                self._client_env['TIMEOUT'] and int(self._client_env['TIMEOUT']))
        #: Show client full traceback on error
        self.show_traceback = True
        #: if set, characters are stripped around ``line_received``
        self.strip_eol = '\r\n\00'
        #: default encoding 'errors' argument
        self.encoding_errors = 'replace'
        #: Whether ``tab_received()`` performs tab completion
        self.tab_completion = True
        #: server-preferred encoding
        self._default_encoding = default_encoding
        #: buffer for line input
        self._lastline = collections.deque()
        #: toggled if transport is shutting down
        self._closing = False
        #: codecs.IncrementalDecoder for current CHARSET
        self._decoder = None
        #: time since last byte received
        self._last_received = None
        #: time connection was made
        self._connected = None
        #: toggled on client WILL TTYPE, remote end is not a 'dumb' client
        self._advanced = False
        #: toggled on SLC_LNEXT (^v) for keycode input
        self._literal = False
        #: limit number of digits using counter _lit_recv
        self._lit_recv = False
        #: strip CR[+LF|+NUL] in character_received() by tracking last recv
        self._last_char = None
        #: Whether to send simple video attributes
        self._does_styling = False
        #: sends GA if DO SGA not received (legacy)
        self._send_ga = True
        #: send ASCII BELL on line editing error
        self._send_bell = True
        #: currently in multiline (shell quote not escaped, or \\)
        self._multiline = False
        #: prompt evaulation re for ``prompt_eval()``
        self._re_prompt = re.compile('{}{}'.format(
            self.prompt_esc_char, self.prompt_escapes), flags=re.DOTALL)
        #: variable evaluation re for ``echo_eval()``
        self._re_variable = re.compile('\$([a-zA-Z_]+|\{[a-zA-Z_]+\})')
        #: prompt escape %h is socket.gethostname()
        self._server_name = tulip.get_event_loop().run_in_executor(None,
                socket.gethostname)
        #: prompt escape %H is socket.get_fqdn(self._server_name) Future
        self._server_fqdn = tulip.Future()
        #: name of shell %s in prompt escape
        self._shell_name = 'telsh'
        #: version of shell %v in prompt escape
        self._shell_ver = '0.1'

    def banner(self):
        """ XXX Display login banner and solicit initial telnet options.
        """
        #   The default initially sets 'kludge' mode, which does not warrant
        #   any reply and is always compatible with any client NVT.
        #
        #   Notably, a request to negotiate TTYPE is made. If sucessful,
        #   the callback ``request_advanced_opts()`` is fired.
        self.echo ('Welcome to {}! '.format(__file__,))
        self.stream.iac(telopt.WILL, telopt.SGA)
        self.stream.iac(telopt.WILL, telopt.ECHO)
        self.stream.iac(telopt.DO, telopt.TTYPE)

    def first_prompt(self, call_after=None):
        """ XXX First time prompt fire
        """
        call_after = self.display_prompt if call_after is None else call_after
        assert callable(call_after), call_after

        self.log.info(self.about_connection())
        # conceivably, you could use various callback mechanisms to
        # relate to authenticating or other multi-state login process.
        loop = tulip.get_event_loop()
        loop.call_soon(call_after)

    def display_prompt(self, redraw=False, input=None):
        """ XXX Prompts client end for input. """
        input = self.lastline if input is None else input
        parts = (('\r\x1b[K') if redraw else ('\r\n'), self.prompt, input,)
        self.echo(''.join(parts))
        if self._send_ga:
            self.stream.send_ga()

    # TODO: pipe blessings curses in Multiprocessing for initialization
    # and parameterizing
    def dim(self, string):
        """ XXX Return ``string`` as dim color for smart terms
        """
        if self._does_styling:
            term = self.env['TERM']
            if (term.startswith('xterm') or term.startswith('rxvt')
                    or term.startswith('urxvt') or term.startswith('ansi')
                    or term == 'screen'):
                # smart terminals know that a bold black wouldn't be very
                # visible, and instead use it to great effect as 'dim'
                return u'\x1b[1m\x1b[30m' + string + '\x1b[0m'
            # use red instead
            return '\x1b[31m' + string + '\x1b[0m'
        return string

    def bold(self, string):
        """ XXX Return ``string`` decorated using 'bold' for smart terms
        """
        if self._does_styling:
            return '\x1b[0;1m' + string + '\x1b[0m'
        return string


    def standout(self, string):
        """ XXX Return ``string`` decorated using 'standout' for smart terms
        """
        if self._does_styling:
            return '\x1b[31;1m' + string + '\x1b[0m'
        return string

    def character_received(self, char):
        """ XXX Callback receives a single Unicode character as it is received.

            The default takes a 'most-compatible' implementation, providing
            'kludge' mode with simulated remote editing for inadvanced clients.
        """
        CR, LF, NUL = '\r\n\x00'
        char_disp = char
        #self.log.debug('character_received: {!r}'.format(char))
        if not self.can_write(char) or not char.isprintable():
            # ASCII representation of unprtintables for display editing
            char_disp = self.standout(teldisp.name_unicode(char))
        if self.is_literal:
            # Within a ^v loop of ``literal_received()``, insert raw
            self._lastline.append(char)
            self.local_echo(char_disp)
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
        elif self.tab_completion and char == '\t':  # ^I tab auto-completion
            try:
                if not self.tab_received(self.lastline):
                    self.bell()
            except ValueError as err:
                self.log.debug(err)
                self.bell()
            except Exception:
                self._display_tb(*sys.exc_info(), level=logging.INFO)
            finally:
                self.display_prompt(redraw=True)
        elif not char.isprintable() and char not in (CR, LF, NUL,):
            self.bell()
        elif char.isprintable() and char not in ('\r', '\n'):
            self._lastline.append(char)
            self.local_echo(char_disp)
        self._last_char = char

    def line_received(self, input, eor=False):
        """ XXX Callback for each telnet input line received.
        """
        self.log.debug('line_received: {!r}'.format(input))
        if self.strip_eol:
            input = input.rstrip(self.strip_eol)
        self._multiline = False
        try:
            self._retval = self.process_cmd(input)
        except Exception:
            self._display_tb(*sys.exc_info(), level=logging.INFO)
            self.bell()
            self._retval = -1
        finally:
            # when _retval is None, we are multi-line
            if self._retval is not None:
                # command was processed, clear line buffer and prompt
                self._lastline.clear()
                self.display_prompt()
            else:
                # we are in a line continuate
                self._multiline = True
                self.display_prompt(input='')

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

    def literal_received(self, ucs):
        """ Receives literal character(s) SLC_LNEXT (^v) and all subsequent
            characters until the boolean toggle ``_literal`` is set False.
        """
        self.log.debug('literal_received: {!r}'.format(ucs))
        literval = 0 if self._literal is '' else int(self._literal)
        new_lval = 0
        if self._literal is False:  # ^V or SLC_VLNEXT
            self.echo(self.standout('^\b'))
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

    def tab_received(self, input, table=None):
        """ .. method:: tab_received(input : string)

            XXX Callback for receipt of TAB key ('\t'), provides tab
            auto-compeltion, using default ``table`` of format OrderedDict
            ``self.cmdset_autocomplete``.
        """
        self.log.debug('tab_received: {!r}'.format(input))
        if not self.tab_completion:
            return

        def autocomplete(table, buf, cmd, *args):
            """
            .. function::autocomplete(table, buf, cmd, *args) -> (buf, bool)

            Returns autocompletion from command point after prefixing
            line, "buf", based on nested OrderedDict table, with current
            command as "cmd", and remaining shell quoting args *args,
            recursively completing as command-args match, returning tuple
            (buffer, bool), where bool indicates that a command or
            argument was successfully auto-completed, and buffer is the
            new input line.
            """
            auto_cmds = tuple(table.keys())
            self.log.debug('autocomplete: {!r}, {!r}, {!r}; {}'.format(
                buf, cmd, args, auto_cmds))
            # empty commands cycle at first argument,
            if not cmd:
                has_args = table[auto_cmds[0]] is not None
                buf = ''.join((
                    teldisp.postfix(buf),
                    teldisp.postfix(auto_cmds[0], using=' '
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
                                    teldisp.postfix(buf),
                                    teldisp.postfix(auto_cmd),
                                    teldisp.escape_quote(args))
                            return (buf, False)
                        # first-time exact match,
                        if self._last_char != '\t':
                            return (buf, True)
                        # cycle next match
                        ptr = 0 if ptr + 1 == len(auto_cmds) - 1 else ptr + 1
                        buf = ''.join((teldisp.postfix(buf), auto_cmds[ptr],))
                        return (buf, True)
                    else:
                        # match at this step, have/will args, recruse;
                        buf = ''.join((teldisp.postfix(buf), auto_cmd,))
                        _cmd = args[0] if args else ''
                        return autocomplete(  # recurse
                                table[auto_cmd], buf, _cmd, *args[1:])
                elif auto_cmd.lower().startswith(cmd.lower()):
                    # partial match, error if arguments not valid,
                    args_ok = bool(not args or args and has_args)
                    buf = ''.join((teldisp.postfix(buf), auto_cmd))
                    if args:
                        buf = ''.join((teldisp.postfix(buf),
                            teldisp.escape_quote(args)))
                    return (buf, args_ok)
            # no matches
            buf = '{}{}{}'.format(teldisp.postfix(buf),
                    cmd, teldisp.escape_quote(args))
            return (buf, False)
        # dynamic injection of variables for set command,
        cmd, args = input.rstrip(), []
        table = self.cmdset_autocomplete if table is None else table
        # inject session variables for set command,
        if 'set' in table:
            table['set'] = collections.OrderedDict([
                ('{}='.format(key), None)
                for key in sorted(self.env.keys())
                if key not in self.readonly_env])
        if ' ' in cmd:
            cmd, *args = shlex.split(cmd)
        buf, match = autocomplete(table, '', cmd, *args)
        self._last_char = '\t'
        self._lastline = collections.deque(buf)
        return match

    def editing_received(self, char, slc=None):
        self.log.debug('editing_received: {!r}{}.'.format(
            char, ', {}'.format(name_slc_command(slc),) if slc is not None
                else ''))
        char_disp = teldisp.name_unicode(char.decode('iso8859-1'))
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
        elif slc == telopt.SLC_EW:  # erase word (^w), rubout .(\w+)
            if not self._lastline:
                self.bell()
            else:
                while self._lastline and self._lastline[-1].isspace():
                    self._lastline.pop()
                while self._lastline and not self._lastline[-1].isspace():
                    self._lastline.pop()
                self.display_prompt(redraw=True)
        elif slc == telopt.SLC_EL:  # erase line (^L)
            self._lastline.clear()
            self.display_prompt(redraw=True)
        elif slc == telopt.SLC_EOF:  # end of file (^D)
            if not self._lastline:
                self.echo(char_disp)
                self.logout(telopt.DO)
            else:
                self.bell()
        elif slc in (telopt.SLC_IP, telopt.SLC_ABORT):
            # interrupt process (^C), abort process (^\)
            self._lastline.clear()
            self.echo(char_disp)
            self.display_prompt()
        elif slc in (telopt.SLC_XON, telopt.SLC_XOFF,
                telopt.SLC_AYT, telopt.SLC_SUSP):
            # handled by callbacks or not really an editing cmd
            pass
        elif slc in (telopt.SLC_AO, telopt.SLC_SYNCH, telopt.SLC_EOR):
            # all others (unhandled)
            self.log.debug('recv {}'.format(name_slc_command(slc)))
            self.echo(char_disp)
            self.bell()
            self.display_prompt()
        else:
            raise NotImplementedError(char, slc)


    def data_received(self, data):
        """ Process each byte as received by transport.

            Derived impl. should instead extend or override the
            ``line_received()`` and ``char_received()`` methods.
        """
        #self.log.debug('data_received: {!r}'.format(data))
        self._last_received = datetime.datetime.now()
        if self._will_timeout:
            self._timeout.cancel()
            self._start_timeout()
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

    def echo(self, ucs, errors=None):
        """ Write unicode string to transport using preferred encoding.
        """
        errors = errors if errors is not None else self.encoding_errors
        try:
            self.stream.write(self.encode(ucs, errors))
        except LookupError as err:
            assert self.encoding(outgoing=True) != self._default_encoding
            self._env_update({'CHARSET': self._default_encoding})
            self.log.debug(err)
            self._display_charset_err(err)
            return self.echo(ucs, errors)

    def about_connection(self):
        """ Returns string suitable for status of server session.
        """
        return '{}{}{}{}'.format(
                # user [' using <terminal> ']
                '{}{} '.format(self.env['USER'],
                    ' using' if self.env['TERM'] != 'unknown' else ''),
                '{} '.format(self.env['TERM'])
                if self.env['TERM'] != 'unknown' else '',
                # state,
                '{}connected from '.format(
                    'dis' if self._closing else ''),
                # ip, dns
                '{}{}'.format(
                    self.client_ip, ' ({}{})'.format(
                        self.client_hostname.result(),
                        (', dns-ok' if self.client_ip
                            == self.client_reverse_ip.result()
                            else self.standout('!= {}, revdns-fail'.format(
                                self.client_reverse_ip.result()))
                            ) if self.client_reverse_ip.done() else '')
                        if self.client_hostname.done() else ''),
                ' after {:0.3f}s'.format(self.duration))

    @property
    def client_ip(self):
        """ .. client_ip() -> string

            Returns Client IP address as string.
        """
        return self._client_ip

    @property
    def client_hostname(self):
        """ .. client_hostname() -> Future()

            Returns DNS name of client as String as Future.
        """
        if self._client_hostname.done():
            val = self._client_hostname.result()[0]
            return wrap_future_result(self._client_hostname, val)
        return self._client_hostname

    @property
    def client_fqdn(self):
        """ .. client_fqdn() -> Future()

            Returns FQDN dns name of client as Future.
        """
        if self._client_hostname.done():
            val = self._client_hostname.result()[1][0]
            return wrap_future_result(self._client_hostname, val)
        return self._client_hostname

    @property
    def client_reverse_ip(self):
        """ .. client_fqdn() -> Future()

            Returns reverse DNS lookup IP address of client as Future.
        """
        if self._client_hostname.done():
            val = self._client_hostname.result()[2][0]
            return wrap_future_result(self._client_hostname, val)
        return self._client_hostname

    @property
    def server_name(self):
        """ .. server_name() -> Future()

            Returns name of server as string as Future.
        """
        return self._server_name

    @property
    def server_fqdn(self):
        """ .. server_fqdn() -> Future()

            Returns fqdn string of server as Future.
        """
        if self._server_fqdn.done():
            # future is complete,
            return self._server_fqdn
        if not self._server_fqdn.running() and self._server_name.done():
            # first DNS lookup,
            self._server_fqdn = tulip.get_event_loop().run_in_executor(
                        None, socket.getfqdn, self._server_name.result())
        return self._server_fqdn

    @property
    def env(self):
        """ Returns hash of session environment values
        """
        return self._client_env

    @property
    def lastline(self):
        """ Returns client command line as unicode string.
        """
        return u''.join(self._lastline)

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

    prompt_escapes = r'(\d{3}|x[0-9a-fA-F]{2}|[\$e#\?huH])'

    def prompt_esc(self, input, esc_char=None):
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
            return self.env['USER']
        if input == 'h':
            return '{}'.format(self.server_name.result())
        if input == 'H':
            return '{}'.format(self.server_fqdn.result())
        if input.startswith('$'):
            return self.env[input[1:]]
        if input == '?':
            return '{}'.format(self.retval)
        if input.isdigit():
            return chr(int(input, 8))
        if input.startswith('x'):
            return chr(int('0x{}'.format(input[1:]), 16))
        if input == 's':
            return self._shell_name
        if input == 'v':
            return self._shell_ver
        return input

    def echo_eval(self, input, literal_escape=True):
        """ Evalutes ``input`` for variable substituion
        """
        output = []
        start_next = 0
        for n in range(len(input)):
            if n >= start_next:
                match = self._re_variable.match(input[n:])
                if match:
                    key = match.group(1).strip('}{')
                    val = self.env[key]
                    output.append(val)
                    start_next = n + match.end()
                elif (input[n] == '\\' and n < len(input) - 1
                        and literal_escape):
                    val = teldisp.resolve_literal(input[n:n+2])
                    if val is None:
                        output.append('\\')
                        start_next = 0
                        continue
                    output.append(val)
                    start_next = n + 2
                else:
                    output.append(input[n])
        return ''.join(output)


    def prompt_eval(self, input, escape_literals=True):
        """ Evaluates ``input`` as a prompt containing escape characters
        """
        output = []
        start_next = 0
        for n in range(len(input)):
            if n >= start_next:
                match = self._re_prompt.match(input[n:])
                if match:
                    val = self.prompt_esc(match.group(1))
                    output.append(val)
                    start_next = n + match.end()
                elif (input[n] == '\\' and n < len(input) - 1
                        and escape_literals):
                    val = teldisp.resolve_literal(input[n:n+2])
                    if val is None:
                        output.append('\\')
                        start_next = 0
                        continue
                    output.append(val)
                    start_next = n + 2
                else:
                    output.append(input[n])
        return ''.join(output)

    @property
    def prompt(self):
        """ Returns string suitable for display_prompt().

            This implementation just returns the PROMPT client env
            value, or '% ' if unset.
        """
        return self.prompt_eval(
                self.env['PS2'] if self.is_multiline else self.env['PS1'])

    def encoding(self, outgoing=False, incoming=False):
        """ Returns the session's preferred input or output encoding.

            Always 'ascii' for the direction(s) indicated unless ``inbinary``
            or ``outbinary`` has been negotiated. Then, the session value
            CHARSET is used, or ``default_encoding``, if CHARSET is not
            negotiated.
        """
        #   It possible to negotiate UTF-8 input with ascii output using
        #   command ``toggle outbinary`` on the bsd client.
        assert outgoing or incoming
        return (self.env.get('CHARSET', self._default_encoding)
                if (outgoing and not incoming and self.outbinary or
                    not outgoing and incoming and self.inbinary or
                    outgoing and incoming and self.outbinary and self.inbinary)
                else 'ascii')

    @property
    def inbinary(self):
        """ Returns True if server status ``inbinary`` is True.
        """
        # character values above 127 should not be expected to be read
        # inband from the transport unless inbinary is set True.
        return self.stream.remote_option.enabled(telopt.BINARY)

    @property
    def outbinary(self):
        """ Returns True if server status ``outbinary`` is True.
        """
        # character values above 127 should not be written to the transport
        # unless outbinary is set True.
        return self.stream.local_option.enabled(telopt.BINARY)

    @property
    def retval(self):
        """ Returns exit status of last command processed by ``line_received``
        """
        return self._retval if self._retval is not None else ''


    @property
    def is_literal(self):
        """ Returns True if the SLC_LNEXT character (^v) was recieved, and
            any subsequent character should be received as-is; this is for
            inserting raw sequences into a command line that may otherwise
            interpret them not printable, or a special line editing character.
        """
        return not self._literal is False

    @property
    def is_multiline(self):
        """ Returns True if currently within a multi-line prompt, that is,
            a shell quote was used (" or ') and carriage return was pressed,
            a PS2-prompt like should be implemented in display_prompt(True)
        """
        return self._multiline

    @is_literal.setter
    def is_literal(self, value):
        assert isinstance(value, (str, bool)), value
        self._literal = value

    def bell(self):
        """ Callback when inband data is not valid during remote line editing.

            Default impl. writes ASCII BEL to unless ``_send_bell`` is False.
        """
        if self._send_bell:
            self.local_echo('\a')

    def local_echo(self, ucs, errors=None):
        """ Calls ``echo(ucs, errors`` only of local option ECHO is True.
        """
        if self.stream.local_option.enabled(telopt.ECHO):
            self.echo(ucs, errors)

    def process_cmd(self, input):
        """ .. method:: process_cmd(input : string) -> int
            XXX Callback from ``line_received()`` for input line processing..

            The default handler returns shell-like exit/success value as
            integer, 0 meaning success, non-zero failure, and provides a
            minimal set of diagnostic commands.
        """
        cmd, args = input.rstrip(), []
        if ' ' in cmd:
            try:
                cmd, *args = shlex.split(cmd)
            except ValueError as err:
                self.log.debug(err)
                if err.args == ('No closing quotation',):
                    self._lastline.append('\r') # use '\r' ..
                    return None
                elif (err.args == ('No escaped character',)
                        and cmd.endswith('\\')):
                    # multiline without escaping
                    return None
                raise err
        self.log.debug('process_cmd {!r}{!r}'.format(cmd, args))
        if cmd in ('help', '?',):
            return self.cmdset_help(*args)
        elif cmd == 'echo':
            self.cmdset_echo(*args)
        elif cmd in ('quit', 'exit', 'logoff', 'logout', 'bye'):
            self.logout()
        elif cmd == 'status':
            self.display_status()
        elif cmd == 'whoami':
            self.echo('\r\n{}.'.format(self.about_connection()))
        elif cmd == 'whereami':
            self.echo('\r\n{}'.format(
                (self.server_fqdn.result()
                    if self.server_fqdn.done()
                    else self.server_name.result()
                    if self.server_name.done()
                    else self.server_name.__repr__())))
        elif cmd == 'set':
            return self.cmdset_set(*args)
        elif cmd == 'toggle':
            return self.cmdset_toggle(*args)
        elif '=' in cmd:
            return self.cmdset_assign(*([cmd] + args))
        elif cmd:
            self.echo('\r\n{!s}: command not found.'.format(cmd))
            return 1
        return 0

    def can_write(self, ucs):
        """ .. method::can_display(string) -> bool

            True if client end can receive character as a simple cell
            glyph: if character is 7-bit ascii and not a control character,
            or has 8th bit set but outbinary is true.
        """
        return ord(ucs) > 31 and (ord(ucs) < 127 or self.outbinary)

    def encode(self, buf, errors=None):
        """ Encode byte buffer using client-preferred encoding.

            If ``outbinary`` is not negotiated, ucs must be made of strictly
            7-bit ascii characters (valued less than 128), and any values
            outside of this range will be replaced with a python-like
            representation.
        """
        errors = errors if errors is not None else self.encoding_errors
        return bytes(buf, self.encoding(outgoing=True), errors)

    def decode(self, input, final=False):
        """ Decode bytes received from client using preferred encoding.
        """
        if (self._decoder is None or
                self._decoder._encoding
                != self.encoding(incoming=True)):
            try:
                self._decoder = codecs.getincrementaldecoder(
                        self.encoding(incoming=True))(
                        errors=self.encoding_errors)
                self._decoder._encoding = self.encoding(incoming=True)
            except LookupError as err:
                assert (self.encoding(incoming=True)
                        != self._default_encoding), err
                self.log.info(err)
                self._env_update({'CHARSET': self._default_encoding})
                self._decoder = codecs.getincrementaldecoder(
                        self.encoding(incoming=True))(
                        errors=self.encoding_errors)
                self._decoder._encoding = self.encoding(incoming=True)
                # interupt client session to notify change of encoding,
                self._display_charset_err(err)
                self.display_prompt()
        return self._decoder.decode(input, final)

    def _display_charset_err(self, err):
        self.stream.write(b'\r\n')
        self.stream.write(bytes(
            err.args[0].encode(
                self.encoding(outgoing=True), )))
        self.stream.write(b', CHARSET is ')
        self.stream.write(bytes(self.env['CHARSET'].encode(
            self.encoding(outgoing=True))))
        self.stream.write(b'.\r\n')

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
        self._retval = None
        self.set_callbacks()
        self.server_fqdn  # spawn Future for server_fqdn
        self.banner()
        self._negotiate()
        self._start_timeout()
        #: start DNS lookup of client
        self._client_ip = transport.get_extra_info('addr')[0]
        self._client_hostname = tulip.get_event_loop().run_in_executor(None,
                socket.gethostbyaddr, self._client_ip)
        self._client_hostname.add_done_callback(
                self._completed_client_lookup)

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
        if ttype and self.stream.remote_option.enabled(telopt.TTYPE):
            # we've already accepted their ttype, but see what else they have!
            self.stream.request_ttype()

    def handle_ayt(self, *args):
        """ XXX Callback when AYT or SLC_AYT is received.

            Outputs status of connection and re-displays prompt.
        """
        self.echo('\r\n{}.'.format(self.about_connection()))
        self.display_prompt()

    def display_status(self):
        """ Output the status of telnet session.
        """
        encoding = '{}{}'.format(
                self.encoding(incoming=True), '' if
                self.encoding(outgoing=True)
                == self.encoding(incoming=True) else ' in, {} out'
                .format(self.encoding(outgoing=True)))
        origin = '{0}:{1}'.format(
                *self.transport.get_extra_info('addr', ('unknown', -1,)))
        self.echo('\r\nConnected {}s ago from {}.'
            '\r\nLinemode is {}.'
            '\r\nFlow control is {}.'
            '\r\nEncoding is {}.'
            '\r\n{} rows; {} cols.'.format(
                self.bold('{:0.3f}'.format(self.duration)),
                (origin
                    if not origin.startswith('127.0.0.1:')
                    else self.bold(origin)),
                (self.standout(self.stream.linemode.__str__().rstrip('|ack'))
                    if self.stream.is_linemode
                    else self.bold('kludge')),
                (self.bold('xon-any') if self.stream.xon_any
                    else 'xon'),
                (encoding if encoding == 'ascii'
                    else self.standout(encoding)),
                (self.bold(self.env['COLUMNS'])
                    if self.env['COLUMNS']
                        != self.default_env['COLUMNS']
                    else self.env['COLUMNS']),
                (self.bold(self.env['LINES'])
                    if self.env['LINES']
                        != self.default_env['LINES']
                    else self.env['LINES']),
                ))

    def timeout(self):
        self.echo('\r\nTimeout after {}s.\r\n'.format(int(self.idle)))
        self.log.debug('Timeout after {}s.'.format(self.idle))
        self.transport.close()

    def logout(self, opt=telopt.DO):
        if opt != telopt.DO:
            return self.stream.handle_logout(opt)
        self.log.debug('Logout by client.')
        msgs = ('The black thing inside rejoices at your departure',
                'The very earth groans at your depature',
                'The very trees seem to moan as you leave',
                'Echoing screams fill the wastelands as you close your eyes',
                'Your very soul aches as you wake up from your favorite dream')
        self.echo('\r\n{}.\r\n'.format(msgs[int(time.time()/84) % len(msgs)]))
        self.transport.close()

    def eof_received(self):
        self._closing = True

    def connection_lost(self, exc):
        self._closing = True
        self.log.info('{}{}'.format(self.about_connection(),
            ': {}'.format(exc) if exc is not None else ''))
        if self._will_timeout:
            self._timeout.cancel()
            self.log.debug('cancelled {!r}'.format(self._timeout))
        for task in (self._server_name, self._server_fqdn,
                self._client_hostname):
            if task.running():
                task.cancel()
                self.log.debug('cancelled {!r}'.format(task))

    def set_callbacks(self):
        """ XXX Register callbacks with TelnetStreamReader

        The default implementation wires several IAC, SLC, and extended
        RFC negotiation options to local handling functions. This indicates
        our desire to be notified by callbacks for additional signals than
        just ``line_received``.  """
        # wire AYT and SLC_AYT (^T) to callback ``status()``
        self.stream.set_iac_callback(telopt.AYT, self.handle_ayt)
        self.stream.set_slc_callback(telopt.SLC_AYT, self.handle_ayt)

        # wire various 'interrupts', such as AO, IP to ``interrupt_received``
        self.stream.set_iac_callback(telopt.AO, self.interrupt_received)
        self.stream.set_iac_callback(telopt.IP, self.interrupt_received)
        self.stream.set_iac_callback(telopt.BRK, self.interrupt_received)
        self.stream.set_iac_callback(telopt.SUSP, self.interrupt_received)
        self.stream.set_iac_callback(telopt.ABORT, self.interrupt_received)

        # wire extended rfc callbacks for terminal type, dimensions
        self.stream.set_ext_callback(telopt.NEW_ENVIRON, self._env_update)
        self.stream.set_ext_callback(telopt.TTYPE, self.ttype_received)
        self.stream.set_ext_callback(telopt.NAWS, self._naws_update)

    def cmdset_help(self, *args):
        if not len(args):
            self.echo('\r\nAvailable commands:\r\n')
            self.echo(', '.join(self.cmdset_autocomplete.keys()))
            return 0
        cmd = args[0].lower()
        if cmd == 'help':
            self.echo('\r\nDON\'T PANIC.')
            return -42
        elif cmd == 'logoff':
            self.echo('\r\nTerminate connection.')
        elif cmd == 'status':
            self.echo('\r\nDisplay operating parameters.')
        elif cmd == 'whoami':
            self.echo('\r\nDisplay session identifier.')
        elif cmd == 'set':
            self.echo('\r\nSet or display session values.'
                      '\r\nset[ option[=value]]')
        elif cmd == 'whereami':
            self.echo('\r\nDisplay server name')
        elif cmd == 'toggle':
            self.echo('\r\nToggle operating parameters:')
        elif cmd == 'echo':
            self.echo('\r\nDisplay arguments.')
        else:
            return 1
        if (cmd and cmd in self.cmdset_autocomplete
                and self.cmdset_autocomplete[cmd] is not None):
            self.echo('\r\n{}'.format(', '.join(
                self.cmdset_autocomplete[cmd].keys())))
        return 0

    def cmdset_echo(self, *args):
        self.echo('\r\n{}'.format(' '.join(
            self.echo_eval(arg) if '$' in arg else arg for arg in args)))
        return 0

    def cmdset_toggle(self, *args):
        lopt = self.stream.local_option
        tbl_opt = dict([
            ('echo', lopt.enabled(telopt.ECHO)),
            ('outbinary', self.outbinary),
            ('inbinary', self.inbinary),
            ('goahead', not lopt.enabled(telopt.SGA) and not self._send_ga),
            ('color', self._does_styling),
            ('xon-any', self.stream.xon_any),
            ('bell', self._send_bell)])
        if len(args) is 0:
            self.echo(', '.join(
                '{}{} [{}]'.format('\r\n' if num % 4 == 0 else '',
                    opt, self.standout('ON') if enabled
                    else self.dim('off'))
                for num, (opt, enabled) in enumerate(sorted(tbl_opt.items()))))
            return 0
        if len(args) > 1:
            self.echo('\r\ntoggle: too many arguments.')
            return 1
        elif args[0] not in tbl_opt:
            self.echo('\r\ntoggle: not option.')
            return 1
        opt = args[0].lower()
        if opt == 'echo':
            cmd = (telopt.WONT if tbl_opt[opt] else telopt.WILL)
            self.stream.iac(cmd, telopt.ECHO)
            self.echo('\r\n{} echo.'.format(
                telopt._name_command(cmd).lower()))
        elif opt == 'outbinary':
            cmd = (telopt.WONT if tbl_opt[opt] else telopt.WILL)
            self.stream.iac(cmd, telopt.BINARY)
            self.echo('\r\n{} binary.'.format(
                telopt._name_command(cmd).lower()))
        elif opt == 'inbinary':
            cmd = (telopt.DONT if tbl_opt[opt] else telopt.DO)
            self.stream.iac(cmd, telopt.BINARY)
            self.echo('\r\n{} binary.'.format(
                telopt._name_command(cmd).lower()))
        elif opt == 'goahead':
            cmd = (telopt.WONT if tbl_opt[opt] else telopt.WILL)
            self._send_ga = cmd is telopt.WILL
            self.stream.iac(cmd, telopt.SGA)
            self.echo('\r\n{} supress go-ahead.'.format(
                telopt._name_command(cmd).lower()))
        elif opt == 'bell':
            self._send_bell = not tbl_opt[opt]
            self.echo('\r\nbell {}abled.'.format(
                'en' if self._send_bell else 'dis'))
        elif opt == 'xon-any':
            self.stream.xon_any = not tbl_opt[opt]
            self.echo('\r\nxon-any {}abled.'.format(
                'en' if self.stream.xon_any else 'dis'))
        elif opt == 'color':
            self._does_styling = not self._does_styling
            self.echo('\r\ncolor {}.'.format('on'
                if self._does_styling else 'off'))
        else:
            return 1
        return 0

    def cmdset_set(self, *args):
        def disp_kv(key, val):
            return (shlex.quote(val)
                    if key not in self.readonly_env
                    else self.standout(shlex.quote(val)))
        retval = 0
        if args:
            if '=' in args[0]:
                retval = self.cmdset_assign(*args)
                return 0 if not retval else retval # cycle down errors
            # no '=' must mean form of 'set a', displays 'a=value'
            key = args[0].strip()
            if key in self.env:
                self.echo('\r\n{}{}{}'.format(
                    key, '=', disp_kv(key, self.env[key])))
                return 0
            return -1  # variable not found, -1
        # display all values
        self.echo('\r\n')
        self.echo('\r\n'.join(['{}{}{}'.format(
            _key, '=', disp_kv(_key, _val))
            for (_key, _val) in sorted(self.env.items())]))
        return 0

    def cmdset_assign(self, *args):
        """ remote command: set [ option[=value]]: read or set session values.
        """
        if len(args) > 1:
            # x=1 y=2; evaluates right-left recursively
            self.cmdset_set(*args[1:])
        key, val = args[0].split('=', 1)
        if key in self.readonly_env:
            # value is read-only
            return -2
        if not val:
            if not key in self.env:
                # key not found
                return -3
            self._env_update({key: ''})
            return 0
        self._env_update({key: val})
        return 0

    def ttype_received(self, ttype):
        """ Callback for TTYPE response.

        The first firing of this callback signals an advanced client and
        is awarded with additional opts by ``request_advanced_opts()``.

        Otherwise the session variable TERM is set to the value of ``ttype``.
        """
        if self._advanced is False:
            if not len(self.env['TERM']):
                self._env_update({'TERM': ttype})
            # track TTYPE seperately from the NEW_ENVIRON 'TERM' value to
            # avoid telnet loops in TTYPE cycling
            self._env_update({'TTYPE0': ttype})
            # windows-98 era telnet ('ansi'), or terminals replying as
            # such won't have anything more interesting to say. windows
            # socket transport locks up if a second TTYPE is requested.
            self.request_advanced_opts(ttype=(ttype != 'ansi'))
            self._advanced = 1
            return

        self._env_update({'TTYPE{}'.format(self._advanced): ttype})
        lastval = self.env['TTYPE{}'.format(self._advanced)]
        if ttype == self.env['TTYPE0']:
            self._env_update({'TERM': ttype})
            self.log.debug('end on TTYPE{}: {}, using {env[TERM]}.'
                    .format(self._advanced, ttype, env=self.env))
            return
        elif (self._advanced == self.TTYPE_LOOPMAX
                or not ttype or ttype.lower() == 'unknown'):
            ttype = self.env['TERM'].lower()
            self._env_update({'TERM': ttype})
            self.log.warn('TTYPE stop on {}, using {env[TERM]}.'.format(
                self._advanced, env=self.env))
            return
        elif (self._advanced == 2 and ttype.upper().startswith('MTTS ')):
            # Mud Terminal type started, previous value is most termcap-like
            ttype = self.env['TTYPE{}'.format(self._advanced)]
            self._env_update({'TERM': ttype})
            self.log.warn('TTYPE is {}, using {env[TERM]}.'.format(
                self._advanced, env=self.env))
        elif (ttype.lower() == lastval):
            # End of list (looping). Chose first value
            self.log.warn('TTYPE repeated at {}, using {env[TERM]}.'.format(
                self._advanced, env=self.env))
            return
        ttype = ttype.lower()
        self.stream.request_ttype()
        self._advanced += 1

    def _display_tb(self, *exc_info, level=logging.DEBUG):
        """ Dispaly exception to client when ``show_traceback`` is True,
            forward copy server log at debug and info levels.
        """
        tbl_exception = (
                traceback.format_tb(exc_info[2]) +
                traceback.format_exception_only(exc_info[0], exc_info[1]))
        for num, tb in enumerate(tbl_exception):
            tb_msg = tb.splitlines()
            if self.show_traceback:
                self.echo('\r\n' + '\r\n>> '.join(
                    self.standout(row.rstrip())
                    if num == len(tbl_exception) - 1
                    else row.rstrip() for row in tb_msg))
            tbl_srv = [row.rstrip() for row in tb_msg]
            for line in tbl_srv:
                logging.log(level, line)

    def _env_update(self, env):
        " Callback receives no environment variables "
        if 'HOSTNAME' in env:
            env['REMOTEHOST'] = env.pop('HOSTNAME')
        if 'TERM' in env:
            if env['TERM'].lower() != self.env['TERM'].lower():
                ttype = env['TERM'].lower()
                if not ttype:
                    ttype = 'unknown'
                self.log.debug('{!r} -> {!r}'.format(self.env['TERM'], ttype))
                self._client_env['TERM'] = ttype
                self._does_styling = ( ttype.startswith('vt') or
                        ttype.startswith('xterm') or
                        ttype.startswith('dtterm') or
                        ttype.startswith('rxvt') or
                        ttype.startswith('urxvt') or
                        ttype.startswith('ansi') or
                        ttype == 'linux' or ttype == 'screen')
            del env['TERM']
        if 'TIMEOUT' in env and env['TIMEOUT'] != self.env['TIMEOUT']:
            timeout = env['TIMEOUT']
            if not timeout:
                will_timeout = False
            else:
                try:
                    will_timeout = (timeout and int(timeout))
                except ValueError as err:
                    self.log.info('cannot set timeout {!r}: {}'
                            .format(env['TIMEOUT'], err))
                    return
            if self._will_timeout:
                self._timeout.cancel()
            self._client_env['TIMEOUT'] = timeout
            self._will_timeout = will_timeout
            self._start_timeout()
        else:
            self._client_env.update(env)
            self.log.debug('env_update: %r', env)

    def _start_timeout(self):
        if self._will_timeout:
            self._timeout = tulip.get_event_loop().call_later(
                int(self.env['TIMEOUT']) * 60, self.timeout)

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

    def _completed_client_lookup(self, arg):
        """
        Called when dns resolution of client IP address completed.
        """
        if self.client_ip != self.client_reverse_ip.result():
            # OpenSSH will log 'POSSIBLE BREAK-IN ATTEMPT!' but we dont care ..
            self.log.warn('reverse mapping failed: {}'.format(
                self.arg.result()))

    def _negotiate(self, call_after=None):
        """
        Negotiate options before prompting for input, this method calls itself
        every CONNECT_DEFERED up to the greater of the value CONNECT_MAXWAIT.

        Negotiation completes when all ``pending_options`` of the
        TelnetStreamReader have completed. Any options not negotiated
        are displayed to the client as a warning, and ``display_prompt()``
        is called for the first time, unless ``call_after`` specifies another
        callback.
        """
        if call_after is None:
            call_after = self.first_prompt
        assert callable(call_after), call_after
        if self._closing:
            return
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

    socks = loop.run_until_complete(func)
    logging.info('Listening on %s', socks[0].getsockname())
    loop.run_forever()

if __name__ == '__main__':
    main()

