import logging
import codecs
import sys


def _query_term_speed(tty_fd):
    """ .. function:: _query_term_speed(int) -> type((int, int,))

        Returns the input and output speed of the terminal specified
        by argument ``tty_fd`` as two integers: (rx, tx).
    """
    import termios
    iflag, oflag, cflag, lflag, ispeed, ospeed, cc = termios.tcgetattr(tty_fd)
    return ispeed, ospeed


def _query_term_winsize(tty_fd):
    """ .. function:: _query_term_winsize(int) -> type((int, int, int, int,))

        Returns the value of the `winsize' struct returned by ioctl of
        TIOCGWINSZ for the terminal specified by argument ``tty_fd``
        as its natural four integers: (rows, cols, xheight, yheight).
    """
    import fcntl
    import struct
    import termios
    val = fcntl.ioctl(tty_fd, termios.TIOCGWINSZ, b'\x00' * 8)
    return struct.unpack('hhhh', val)


class ConsoleShell():
    def __init__(self, client, log=logging,
                 stream_out=None, stream_in=None):
        #: TelnetClient instance associated with console
        self.client = client
        self.log = log
        self.stream_out = (stream_out if stream_out is not None
                           else sys.__stdout__)
        self.stream_in = (stream_in if stream_in is not None
                          else sys.__stdin__)

        #: codecs.IncrementalDecoder for current CHARSET
        self.decoder = None

        #: default encoding 'errors' argument
        self.encoding_errors = 'replace'

    def write(self, string, errors=None):
        """ Write string to output using preferred encoding.
        """
        errors = errors if errors is not None else self.encoding_errors
        assert isinstance(string, str), string
        self.stream_out.write(self.encode(string, errors))
        self.stream_out.flush()

    @property
    def will_echo(self):
        """ Returns wether to expect the server to display our input; if
            False, it is our own responsibility to write a copy to screen.
        """
        from telopt import ECHO
        return self.client.stream.remote_option.enabled(ECHO)

    @property
    def terminal_type(self):
        """ The terminfo(5) terminal type name: the value found in the
        ``TERM`` environment variable, if exists; otherwise 'unknown'.
        """
        import os
        return os.environ.get('TERM', 'unknown')

    @property
    def xdisploc(self):
        """ The XDISPLAY value: the value found as os environ key ``DISPLAY``.
        """
        import os
        return os.environ.get('DISPLAY', '')

    @property
    def terminal_speed(self):
        """ The terminal speed is a legacy application of determining
        the bandwidth (bits per second) of the connecting terminal, esp.
        when that terminal is serial attached. This method retuns a tuple
        of receive and send speed (rx, tx).

        If the connecting output stream's terminal speed cannot be
        determined, a default value of (38400, 38400) is returned.
        """
        import os
        if (hasattr(self.stream_out, 'fileno')
                and os.isatty(self.stream_out.fileno())):
            return _query_term_speed(self.stream_out)
        return 38400, 38400

    @property
    def terminal_width(self):
        """ The terminal width in printable character columns as integer:
        if the stream_out file descriptor is a terminal, the terminal is
        queried for its size; otherwise the value found in the ``COLUMNS``
        environment variable is returned.
        """
        import os
        if (hasattr(self.stream_out, 'fileno')
                and os.isatty(self.stream_out.fileno())):
            cols, rows, xpixels, ypixels = _query_term_winsize(self.stream_out)
            return cols
        try:
            cols = int(os.environ.get('COLUMNS', '80'))
        except ValueError:
            cols = 80
        return cols

    @property
    def terminal_height(self):
        """ The terminal height printable character columns as integer:
        if the stream_out file descriptor is a terminal, the terminal is
        queried for its size; otherwise the value found in the ``LINES``
        environment variable is returned.  """
        import os
        if (hasattr(self.stream_out, 'fileno')
                and os.isatty(self.stream_out.fileno())):
            cols, rows, xpixels, ypixels = _query_term_winsize(self.stream_out)
            return rows
        try:
            rows = int(os.environ.get('LINES', '24'))
        except ValueError:
            rows = 24
        return rows

    def decode(self, input, final=False):
        """ Decode input string using preferred encoding.
        """
        enc = self.client.encoding(incoming=True)
        if (self.decoder is None or enc != self.decoder._encoding):
                self.decoder = codecs.getincrementaldecoder(enc)(
                    errors=self.encoding_errors)
                self.decoder._encoding = enc
        return self.decoder.decode(input, final)

    def feed_byte(self, byte):
        """ Receive byte from telnet server, display to console """
        ucs = self.decode(byte)
        if ucs is not None:
            self.stream_out.write(ucs)

    def can_write(self, ucs):
        """ Returns True if transport can receive ``ucs`` as a single-cell,
            carriage-forwarding character, such as 'x' or ' '. Values outside
            of 7-bit NVT ASCII range may only be written if server option
            ``outbinary`` is True.

            Otherwise False indicates that a write of this unicode character
            would be an encoding error on the transport (may crash or corrupt
            client screen).
        """
        return ord(ucs) > 31 and (ord(ucs) < 127 or self.client.outbinary)

    def encode(self, buf, errors=None):
        """ Encode byte buffer using client-preferred encoding.

            If ``outbinary`` is not negotiated, ucs must be made of strictly
            7-bit ascii characters (valued less than 128), and any values
            outside of this range will be replaced with a python-like
            representation.
        """
        errors = errors if errors is not None else self.encoding_errors
        return bytes(buf, self.client.encoding(outgoing=True), errors)

    def __str__(self):
        """ Returns string describing state of stream encoding.
        """
        enc_in = self.client.encoding(incoming=True)
        enc_out = self.client.encoding(outgoing=True)
        if enc_in == enc_out:
            return enc_in
        else:
            return '{} in, {} out'.format(enc_in, enc_out)
