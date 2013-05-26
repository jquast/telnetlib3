    
http://lpc.psyc.eu/doc/concepts/mccp
    DEFAULT_PS1 = '\e[31m[\e[m\h\e]m \s-\v$'

#PS1.finditer(r'\\('
#            r'\d{3}|x[0-9a-fA-F]{2}|\$|e|h|H|n|r
#
#          \\     a backslash
#          \077   8-bit character for octal '077'
#          \x7f   8-bit character for hexidecimal 'x7f'
#          \$     display "$" for normal uid, "#" for super user
#          \e     equivalent to \033 (escape)
#          \h     hostname
#          \H     full hostname
#          \r     carriage return
#          \n     newline
#          \s     name of shell
#          \v     version of shell
#          \v     version of shell



    def PS1(self):
        import re
        ps1 = self.env.get('PS1', self.DEFAULT_PS1)
        result = []
        for escape in re.finditer(self.env.get('PS1', self.DEFAULT_PS1)):
            print(escape, escape.group(0))
        return self.env.get('PS1', self.DEFAULT_PS1)

#PS1.finditer(r'\\('
#            r'\d{3}|x[0-9a-fA-F]{2}|\$|e|h|H|n|r
#
#          \\     a backslash
#          \077   8-bit character for octal '077'
#          \x7f   8-bit character for hexidecimal 'x7f'
#          \$     display "$" for normal uid, "#" for super user
#          \e     equivalent to \033 (escape)
#          \h     hostname
#          \H     full hostname
#          \r     carriage return
#          \n     newline
#          \s     name of shell
#          \v     version of shell
#          \v     version of shell


class MTTS(object):
    """ Support the MUD Terminal Type Standard """
    BITFLAGS = {
            "ANSI": 1,
            "VT100": 2,
            "UTF-8": 4,
            "256 COLORS": 8,
            "MOUSE TRACKING": 16,
            "OSC COLOR PALETTE": 32,
            "SCREEN READER": 64,
            "PROXY": 128, }

    def __init__(self, bitvector):
        assert type(bitvector) == bytes
        self.bitvector = bitvector

    def supports(self, option):
        """ Tests if MTTS terminal supports one of the following options:

            "ANSI": Client supports all ANSI color codes.
            "VT100": Client supports most VT100 codes.
            "UTF-8": Client is using UTF-8 character encoding.
            "256 COLORS": Client supports all xterm 256 color codes.
            "MOUSE TRACKING": Client supports xterm mouse tracking.
            "OSC COLOR PALETTE": Client supports the OSC color palette.
            "SCREEN READER": Client is using a screen reader.
            "PROXY": Client is a proxy. """
        assert option in self.BITFLAGS, (
                "option argument was '%s', must be one of: %s" % (
                    ', '.join(self.BITFLAGS.keys())))
        return bool(ord(self.bitvector) & self.BITFLAGS[option])

    def __str__(self):
        opts = [option for option in self.BITFLAGS if self.supports(option)]
        if not opts:
            return 'NO SUPPORT'
        return ', '.join(opts)

class MudTelnetServer(LinemodeTelnetServer):
    _ttype_level = 1
    _start_1mb = None
    def __init__(self, log=logging, debug=False):
        LinemodeTelnetServer.__init__(self, log, debug)

    def connection_made(self, transport):
        LinemodeTelnetServer.connection_made(self, transport)
        self.term = 'DUMB'
        self.mtts = MTTS(b'\x00')
        self.env = {}
        self.stream.set_ext_callback(TTYPE, self.handle_ttype)
        self.stream.set_ext_callback(NEW_ENVIRON, self.handle_env)
        self.stream.set_ext_callback(NAWS, self.handle_naws)

    def banner(self):
        LinemodeTelnetServer.banner(self)
        self.stream.iac(DO, TTYPE)
        self.stream.iac(DO, NEW_ENVIRON)
        self.stream.iac(DO, NAWS)

    def handle_env(self, env):
        self.log.debug('env: %s', env)
        self.env.update(env)

    def display_help(self):
        # derive standard help to add additional commands
        LinemodeTelnetServer.display_help(self)
        self.stream.write(b', set, 1mb')

    def process_cmd(self, cmd):
        """ Process a full line of input after carriage return """
        cmd = cmd.rstrip()
        # When linemode LMODE_MODE_SOFT_TAB is used, horizontal tab chr(9)
        # should always be translated to X±XX

        try:
            cmd, *args = shlex.split(cmd)
        except ValueError:
            args = []
        if cmd == 'set':
            if 0 == len(args):
                # no arguments display all values
                for num, (key, value) in enumerate(self.env.items()):
                    if num:
                        self.stream.write(b'\r\n')
                    self.stream.write(bytes('%s=%s' % (key, value), 'ascii'))
            elif 1 == len(args) and args[0] != '-h':
                if '=' in args[0]:
                    # 'set a=1' for value assignment
                    variable_name, value = args[0].split('=', 1)
                    variable_name, value = variable_name.strip(), value.strip()
                    self.env[variable_name] = value
                else:
                    variable_name = args[0].strip()
                    # 'set a' to display single value
                    if variable_name in self.env:
                        self.stream.write(bytes(
                            '%s=%s' % (variable_name,
                                self.env[variable_name]), 'ascii'))
            else:
                self.stream.write(b'Usage:\r\nset [option[=value]]\r\n')
        elif cmd == '1mb':
            # perform test of writing 1MB of ascii data, for testing flow
            self.test_1mb()
        else:
            LinemodeTelnetServer.process_cmd(self, cmd)

    def test_1mb(self):
        #self.stream.write(b'x' * 1024 * 1024)
        if self._start_1mb is None:
            self._start_1mb = self.stream.byte_count
        loop = tulip.get_event_loop()
        self.stream.write(b'\r\n' + (b'x' * self.width))
        if self.stream.byte_count - self._start_1mb < (1024): #*1024):
            loop.call_soon(self.test_1mb)
        else:
            self._start_1mb = None
        #wait_min = time.time() - self.connect_time <= self.CONNECT_MINWAIT
        #wait_max = time.time() - self.connect_time <= self.CONNECT_MAXWAIT
        #if wait_min or any(self.stream.pending_option.values()) and wait_max:
        #    loop.call_later(self.CONNECT_DEFERED, self._negotiate, call_after)
        #    return

        #self.log.debug(self.transport.get_extra_info('addr', None))
        #for option, pending in self.stream.pending_option.items():
        #    if pending:
        #        cmd = ' + '.join([
        #            _name_command(bytes([byte])) for byte in option])
        #        self.log.warn('telnet reply not received for "%s"', cmd)
        #        self.stream.write(bytes('\r\nwarning: no reply received '
        #            'for "%s"' % (cmd,), 'ascii'))


    @property
    def height(self):
        """ Returns the client terminal height, returns 24 if unknown.
        """
        return int(self.env.get('LINES', '24'))

    @property
    def width(self):
        """ Returns the client terminal width, returns 80 if unknown.
        """
        return int(self.env.get('COLUMNS', '80'))

    def handle_naws(self, width, height):
        self.log.debug('naws: %d, %d', width, height)
        self.env['COLUMNS'] = str(width)
        self.env['LINES'] = str(height)

    def handle_ttype(self, ttype):
        """ "Mud Terminal Type Standard", http://tintin.sourceforge.net/mtts/

        "On the first TTYPE SEND request the client should return its name,
        preferably without a version number and in all caps."

        "On the second TTYPE SEND request the client should return a terminal
        type, preferably in all caps."

        "On the third TTYPE SEND request the client should return MTTS
        followed by a bitvector."
        """
        if self._ttype_level == 1:
            self.log.debug('MTTS level 1 CLIENTINFO=%s', ttype)
            self.env['CLIENTINFO'] = ttype
            self.stream.request_ttype()
            self._ttype_level += 1
            return
        if self._ttype_level == 2:
            if self.env['CLIENTINFO'] == ttype:
                # if the first and second TTYPE response is equal,
                # end negotiation; this is not a mud client.
                self.env['TERM'] = self.env['CLIENTINFO']
                del self.env['CLIENTINFO']
                if self.env['TERM'] != ttype:
                    self.log.warn('using first ttype response after level 2 '
                            'reply, %s', self.env['TERM'], ttype)
                self.log.debug('non-MTTS TERM=%s', self.env['TERM'])
            else:
                self.log.debug('MTTS level 2 TERM=%s', ttype)
                self.env['TERM'] = ttype
                self.stream.request_ttype()
                self._ttype_level += 1
            return
        if self._ttype_level == 3:
            self._ttype_level += 1
            if not ttype.startswith('MTTS '):
                self.log.debug('ttype level 3, not MTTS: %s', ttype)
                return
            try:
                value = int(ttype[len('MTTS '):])
            except ValueError as err:
                self.log.info ('bad MTTS value %r: %s', ttype, err)
                value = 0
            self.mtts = MTTS(bytes([value]))
            self.log.debug('ttype level 3 (MTTS): %s', self.mtts)
            return
        self.log.info('ttype level %d ignored: %s', ttype, self._ttype_level)
        self._ttype_level += 1



# TODO: split into MUD server and honeypot server
# # use DEFAULT_SLC_TAB for honeypot (solicit)
#    def standout(self, ucs):
#        if self._advanced:
#            ttype = self.client_env.get('TERM')
#            if (ttype.startswith('vt') or ttype.startswith('xterm')
#                    or ttype.startswith('dtterm') or ttype.startswith('rxvt')
#                    or ttype.startswith('shell') or ttype.startswith('ansi')):
#                return '\033[1m{}\033[m'.format(ucs)
#            else:
#                self.log.debug('too dumb? {}'.format(ttype))
#        return ucs
#
#    @property
#    def prompt(self):
#        """ TODO: PS1
#        """
#        return u'% '
#
#
#    def request_advanced_opts(self):
#        TelnetServer.request_advanced_opts(self)
#        self.stream.iac(WILL, BINARY)
#        self.stream.iac(DO, BINARY)
#        self.stream.iac(DO, TSPEED)
#        self.stream.iac(DO, XDISPLOC)
#        self.stream.iac(DO, EOR)
#        self.stream.iac(DO, SNDLOC)
#
#
#        elif cmd == 'set':
#            return self.cmdset_set(*args)
#        elif cmd == 'toggle':
#            return self.cmdset_toggle(*args)
#        elif cmd == 'echo':
#            return self.cmdset_echo(*args)
#        else:
#            self.echo('\r\nCommand {!r} not understood.'.format(cmd))
#            return 1
#
#    def cmdset_echo(self, *args):
#        """ remote command: echo [ arg ... ]
#        """
#        self.echo('\r\n{}'.format(' '.join(args)))
#        return 0
#
#    def cmdset_toggle(self, *args):
#        """ remote command: toggle <parameter>
#        """
#        if 0 == len(args) or args[0] in ('-h', '--help'):
#            self.echo('\r\necho [{}] {}'.format(
#                'on' if self.stream.local_option.get(ECHO, None) else 'off',
#                'enable remote echo of input received.'))
#            self.echo('\r\nxon_any [{}] {}'.format(
#                'on' if self.xon_any else 'off',
#                'any input after XOFF resumes XON.'))
#            self.echo('\r\nbinary [{}] {}'.format(
#                'on' if self.local_option.get(BINARY, None) and
#                        self.remote_option.get(BINARY, None) else 'off',
#                'enable bi-directional binary transmission.'))
#            # XXX todo ..
#            self.echo('\r\ninbinary    '
#                'enable server receipt of client binary input.')
#            self.echo('\r\noutbinary    '
#                'enable binary transmission by server.')
#        elif args == ['echo']:
#            if self.stream.local_option.get(ECHO, None):
#                self.stream.iac(WONT, ECHO)
#            else:
#                self.stream.iac(WILL, ECHO)
#
#    def cmdset_set(self, *args):
#        """ remote command: set [ option[=value]]: read or set session values.
#        """
#        def usage():
#            self.echo('\r\nset[ option[=value]]: read or set session values.')
#        if not args:  # display all values
#            self.echo('\r\n\t')
#            self.echo('\r\n\t'.join(
#                '%s=%r' % (key, value,)
#                    for (key, value) in sorted(self.client_env.items())))
#        elif len(args) != 1 or args[0].startswith('-'):
#            usage()
#            return 0 if args[0] in ('-h', '--help',) else 1
#        elif '=' in args[0]:
#            # 'set a=1' for value assignment, 'set a=' to clear
#            var, value = args[0].split('=', 1)
#            value = value.rstrip()
#            if value:
#                self.client_env[var] = value
#            elif var in self.client_env:
#                del self.client_env[var]
#            else:
#                return -1
#        else:
#            # no '=' must mean form of 'set a', displays 'a=value'
#            variable_name = args[0].strip()
#            if variable_name in self.client_env:
#               value = self.client_env[variable_name]
#               self.echo('{}={}'.format(variable_name, value))
#            else:
#                return -1
#        return 0
#
#    def display_slc(self):
#        """ Output special line characters used in session.
#        """
#        self.echo('\r\nSpecial Line Characters:')
#        slc_tbl = ['{:<8} [{}]'.format(
#            telopt._name_slc_command(slc).split('_', 1)[-1].lower(),
#                _name_char(slc_def.val.decode('iso8859-1')))
#                for slc, slc_def in self.stream._slctab.items()
#                    if not slc_def.nosupport and slc_def.val != theNULL]
#        for row, slc_row in enumerate(slc_tbl):
#            self.echo('{}{}'.format(
#                '\r\n\t' if row % 2 == 0 else '\t',
#                slc_row))
#
#    def display_options(self):
#        """ Output status of server options to client end """
#
#        local_opts = self.stream.local_option.items()
#        remote_opts = self.stream.remote_option.items()
#        pending_opts = self.stream.pending_option.items()
#        list_do = [opt for opt, val in local_opts if val]
#        list_dont = [opt for opt, val in local_opts if not val]
#        list_will = [opt for opt, val in remote_opts if val]
#        list_wont = [opt for opt, val in remote_opts if not val]
#        pending = [opt for (opt, val) in pending_opts if val]
#        opt_tbl = []
#        opt_tbl.append('\r\nRemote options:')
#        sep = ', '
#        if list_do:
#            opt_tbl.append('\r\n\tDO {0}.'.format(
#                sep.join([_name_commands(opt) for opt in list_do])))
#        if list_dont:
#            opt_tbl.append('\r\n\tDONT {0}.'.format(
#                ', '.join([_name_commands(opt) for opt in list_dont])))
#        if not list_do and not list_dont:
#            opt_tbl.append('\r\n\tNone.')
#
#        opt_tbl.append('\r\nLocal options:')
#        if list_will:
#            opt_tbl.append('\r\n\tWILL {0}.'.format(
#                ', '.join([_name_commands(opt) for opt in list_will])))
#        if list_dont:
#            opt_tbl.append('\r\n\tWONT {0}.'.format(
#                ', '.join([_name_commands(opt) for opt in list_wont])))
#        if not list_will and not list_wont:
#            opt_tbl.append('\r\n\tNone.')
#
#        if pending:
#            opt_tbl.append('\r\nTelnet options pending reply:')
#            opt_tbl.append('\r\n\t'.join([
#                self.standout(_name_commands(opt)) for opt in pending]))
#
#    def set_callbacks(self):
#        """ This impl. registers callbacks for EOR, LOGOUT, TSPEED,
#            XDISPLOC, CHARSET ...
#        """
#        TelnetServer.set_callbacks(self)
#        # wire IAC + EOR
#        self.stream.set_iac_callback(EOR_CMD, self.eor_received)
#        # wire IAC + cmd + LOGOUT + opt + to callback ``logout(cmd)``
#        self.stream.set_ext_callback(LOGOUT, self.logout)
#        self.stream.set_ext_callback(TSPEED, self._tspeed_received)
#        self.stream.set_ext_callback(XDISPLOC, self._xdisploc_received)
#        self.stream.set_ext_callback(CHARSET, self._charset_received)
#
#    def eor_received(self):
#        """ This impl. fires ``line_received`` with the optional boolean
#            value ``eor`` set ``True` on receipt of IAC + EOR.
#        """
#        self.line_received(self.lastline, eor=True)
#
#    def display_prompt(self, redraw=False):
#        TelnetServer.display_prompt(self, redraw)
#        if self.local_option.get(SGA, True):
#            self.stream.send_eor()
#
#    def standout(self, string):
#        """ XXX Derive to return ``string`` wrapped with sequence
#            for 'standout', if any. Default returns as-is.
#        """
#        return '\x1b[1m' + string + '\x1b[m'
#
        'The black thing inside rejoices at your departure.'
        'The very earth groans at your depature.'
        'The very trees seem to moan as you leave.'
        'Echoing screams fill the wastelands as you close your eyes.'
        'Your very soul aches as you wake up from your favorite dream.'

LC_COLLATE="en_US.UTF-8"
LC_CTYPE="en_US.UTF-8"
LC_MESSAGES="en_US.UTF-8"
LC_MONETARY="en_US.UTF-8"
LC_NUMERIC="en_US.UTF-8"
LC_TIME="en_US.UTF-8"
    def decode(self, input, final=False):
        """ Decode bytes sent by client using preferred encoding.

            Wraps the ``decode()`` method of a ``codecs.IncrementalDecoder``
            instance using the session's preferred ``encoding``.

            If the preferred encoding is not valid, the class constructor
            keyword ``default_encoding`` is used, the 'CHARSET' environment
            value is reverted, and the client
        """
        encoding = self.encoding(outgoing=False)
        if self._decoder is None or self._decoder._encoding != encoding:
            try:
                self._decoder = codecs.getincrementaldecoder(encoding)(
                        errors=self._encoding_errors)
            except LookupError as err:
                assert encoding != self._default_encoding, (
                        self._default_encoding, err)
                self.log.warn(err)
                self._env_update({'CHARSET': self._default_encoding})
                self._decoder = codecs.getincrementaldecoder(encoding)(
                        errors=self._encoding_errors)
                # interupt client session to notify change of encoding,
                self.echo('{}, CHARSET is {}.'.format(err, encoding))
                self.display_prompt()
            self._decoder._encoding = encoding
        try:
            return self._decoder.decode(input, final)
        except UnicodeDecodeError:
            self._decoder = codecs.getincrementaldecoder(
                    encoding)(errors=self._on_encoding_err)
            self._decoder._encoding = encoding
            return self._decoder.decode(input, final)
        self._encoding_errors = 'strict'
        self._on_encoding_err = 'replace'


    # popular term types:
    330 'XTERM-256COLOR'
     56 'XTERM'
     31 'ANSI'
     40 'rxvt-unicode-256color'
     24 'linux'
     15 'xterm'
     13 'VT100'
     12 'xterm-256color'
      9 'TINTIN++'
      3 'screen'
      3 'vt100'
      2 'XTERM-COLOR'
