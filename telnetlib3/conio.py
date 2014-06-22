import logging
import termios
import codecs
import struct
import fcntl
import sys
import os
import io


__all__ = ('ConsoleShell', 'TerminalShell')


class ConsoleShell():
    """ A shell appropriate for use with the TelnetClient protocol
        argument 'shell'. stream_out recieves bare uncode values.
        callbacks for window size, xdisploc, and speed return
        static default values.
    """
    def __init__(self, client, log=logging, stream_out=None):
        #: TelnetClient instance associated with console
        self.client = client
        self.log = log
        self.stream_out = stream_out or io.StringIO()

        #: default encoding 'errors' argument
        self.encoding_errors = 'replace'

        #: codecs.IncrementalDecoder for current CHARSET
        self.decoder = None

    def write(self, string=u''):
        """ Write string to console output stream.
        """
        self.stream_out.write(string)

    @property
    def terminal_type(self):
        """ Always returns 'unknown'.
        """
        return 'unknown'

    @property
    def xdisploc(self):
        """ Always returns ''
        """
        return ''

    @property
    def terminal_speed(self):
        """ Returns (38400, 38400).
        """
        return 38400, 38400

    @property
    def terminal_width(self):
        """ Returns 80.
        """
        return 80

    @property
    def terminal_height(self):
        """ Returns 24.
        """
        return 24

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
        """ Receive byte from telnet server, decodes to client encoding
            and writes to output stream as unicode.
        """
        ucs = self.decode(byte)
        if ucs:
            self.write(ucs)

    def encode(self, buf, errors=None):
        """ Encode byte buffer ``buf`` using client-preferred encoding.

            If ``outbinary`` is not negotiated, ucs must be made of strictly
            7-bit ascii characters (valued less than 128), and any values
            outside of this range will be replaced with a python-like
            representation.
        """
        errors = errors if errors is not None else self.encoding_errors
        return bytes(buf, self.client.encoding(outgoing=True), errors)

    @property
    def will_echo(self):
        """ Returns wether to expect the server to display our input; if
            False, it is our own responsibility to write a copy to screen.
        """
        from .telopt import ECHO
        return self.client.stream.remote_option.enabled(ECHO)

    def __str__(self):
        """ Returns string describing state of stream encoding.
        """
        enc_in = self.client.encoding(incoming=True)
        enc_out = self.client.encoding(outgoing=True)
        return (enc_in if enc_in == enc_out else
                '{} in, {} out'.format(enc_in, enc_out))


class TerminalShell(ConsoleShell):
    """ A shell appropriate for use with the TelnetClient protocol
        argument 'shell' and a stream_out connected to a tty, by
        default, output stream is sys.stdout, and callbacks registered
        for send terminal window size, and speed query the
        connected terminal and environment values.
    """
    def __init__(self, client, log=logging, stream_out=None):
        self.stream_out = stream_out or sys.__stdout__
        super().__init__(client, log, self.stream_out)

    def write(self, string=u''):
        """ Write string to console output stream.
        """
        # XXX probably an out-of-order timing condition, here
        try:
            if string:
                self.stream_out.write(string)
        except BlockingIOError:
            # output is blocking, defer *write* for another 50ms,
            self.client._loop.call_later(0.05, self.write, string)
        try:
            self.stream_out.flush()
        except BlockingIOError:
            # output cannot flush, defer *flush* for another 50ms,
            self.client._loop.call_later(0.05, self.write)

    @property
    def terminal_type(self):
        """ The terminfo(5) terminal type name: the value found in the
        ``TERM`` environment variable, if exists; otherwise 'unknown'.
        """
        return (os.environ.get('TERM', '') or super().terminal_type)

    @property
    def xdisploc(self):
        """ The XDISPLAY value: the value found as os environ key ``DISPLAY``.
        """
        return (os.environ.get('DISPLAY', '') or super().xdisploc)

    @property
    def terminal_speed(self):
        """ The terminal speed is a legacy application of determining
        the bandwidth (bits per second) of the connecting terminal, esp.
        when that terminal is serial attached. This method retuns a tuple
        of receive and send speed (rx, tx).

        If the connecting output stream's terminal speed cannot be
        determined, a default value of (38400, 38400) is returned.
        """
        if (hasattr(self.stream_out, 'fileno')
                and os.isatty(self.stream_out.fileno())):
            return self._query_term_speed(self.stream_out)
        return super().terminal_speed

    @property
    def terminal_width(self):
        """ The terminal width in printable character columns as integer:
        if the stream_out file descriptor is a terminal, the terminal is
        queried for its size; otherwise the value found in the ``COLUMNS``
        environment variable is returned.
        """
        try:
            if (hasattr(self.stream_out, 'fileno')
                    and os.isatty(self.stream_out.fileno())):
                rows, cols, xpixels, ypixels = (
                    self._query_term_winsize(self.stream_out))
                return cols
        except io.UnsupportedOperation:
            self.log.warn('{}: stream_out {} does not support fileno()'.format(
                self.__class__.__name__, self.stream_out))
            pass
        try:
            cols = int(os.environ.get('COLUMNS', str(super().terminal_width)))
        except ValueError:
            cols = super().terminal_width
        return cols

    @property
    def terminal_height(self):
        """ The terminal height printable character columns as integer:
        if the stream_out file descriptor is a terminal, the terminal is
        queried for its size; otherwise the value found in the ``LINES``
        environment variable is returned.  """
        try:
            if (hasattr(self.stream_out, 'fileno')
                    and os.isatty(self.stream_out.fileno())):
                rows, cols, xpixels, ypixels = (
                    self._query_term_winsize(self.stream_out))
                return rows
        except io.UnsupportedOperation:
            self.log.warn('{}: stream_out {} does not support fileno()'.format(
                self.__class__.__name__, self.stream_out))
            pass
        try:
            rows = int(os.environ.get('LINES', str(super().terminal_height)))
        except ValueError:
            rows = super().terminal_height
        return rows

    @staticmethod
    def _query_term_speed(tty_fd):
        """ .. function:: _query_term_speed(int) -> type((int, int,))

            Returns the input and output speed of the terminal specified
            by argument ``tty_fd`` as two integers: (rx, tx).
        """
        iflag, oflag, cflag, lflag, ispeed, ospeed, cc = (
            termios.tcgetattr(tty_fd))
        return ispeed, ospeed

    @staticmethod
    def _query_term_winsize(tty_fd):
        """ .. function:: _query_term_winsize(int) -> type((int, int, int, int,))

            Returns the value of the `winsize' struct returned by ioctl of
            TIOCGWINSZ for the terminal specified by argument ``tty_fd``
            as its natural 4 unsigned short integers:
                (ws_rows, ws_cols, ws_xpixels, ws_ypixels).
        """
        #  struct winsize {
        #      unsigned short  ws_row;         /* rows, in characters */
        #      unsigned short  ws_col;         /* columns, in characters */
        #      unsigned short  ws_xpixel;      /* horizontal size, pixels */
        #      unsigned short  ws_ypixel;      /* vertical size, pixels */
        #  };
        val = fcntl.ioctl(tty_fd, termios.TIOCGWINSZ, b'\x00' * 8)
        return struct.unpack('hhhh', val)

#    def can_write(self, ucs):
#        """ Returns True if transport can receive ``ucs`` as a single-cell,
#            carriage-forwarding character, such as 'x' or ' '. Values outside
#            of 7-bit NVT ASCII range may only be written if server option
#            ``outbinary`` is True.
#
#            Otherwise, a return value of False indicates that a write of this
#            unicode character would be an encoding error on the transport
#            (may crash or corrupt client screen).
#        """
#        return ord(ucs) > 31 and (ord(ucs) < 127 or self.client.outbinary)
#

