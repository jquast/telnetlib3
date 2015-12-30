# std imports
import logging
import asyncio
import codecs
#import traceback
#import sys


class TelnetReader(asyncio.StreamReader):
    """
    A reader interface from telnet protocol.

    This API requires the attached ``protocol.encoding(incoming=True)`` method
    call return either a string that should be used for encoding, or ``False``
    to indicate that this interface should not perform encoding conversion.
    When ``False``, this streams interface is bytes-only, otherwise unicode.

    returning a string, this API is a unicode interface, over the pairing telnet protocol's
    preferred encoding, unless such instance encoding is explicitly set
    ``False`` for class initializer argument ``encoding``.

    Protocol byte data that is **not** ``IAC`` (Is A Command) escape data
    is sent to :meth:`feed_byte`.  Consumers may then yield from the various
    ``read``-family of coroutines derived from :class:`asyncio.StreamReader`.

    The :meth:`readline` interface handles the four variants of newlines
    sent by telnet clients:

        - ``CR LF``, received as ``CR``.
        - ``CR \x00``, received as ``CR``.
        - ``CR``
        - ``LF``
    """
    #: Late-binding instance of :class:`codecs.IncrementalDecoder`, some
    #: bytes may be lost if the protocol's encoding is changed after
    #: previously receiving a partial multibyte.  This isn't common in
    #: practice, however.
    _decoder = None

    def __init__(self, protocol, limit=asyncio.streams._DEFAULT_LIMIT,
                 loop=None, log=None, encoding_errors='replace',
                 server=False, client=False):
        if not any((client, server)) or all((client, server)):
            raise TypeError("keyword arguments `client', and `server' "
                            "are mutually exclusive.")
        self._server = server
        self.log = log or logging.getLogger(__name__)
        self._protocol = protocol
        if loop is None:
            loop = asyncio.get_event_loop()

        super().__init__(limit=limit, loop=loop)

        #: same as meaning as ``errors`` in :class:`codecs.Codec`.
        self.encoding_errors = encoding_errors

    def decode(self, buf, final=False):
        """Decode bytes ``buf`` using preferred encoding."""
        encoding = self._protocol.encoding(incoming=True)

        # late-binding,
        if (self._decoder is None or encoding != self._decoder._encoding):
            self._decoder = codecs.getincrementaldecoder(encoding)(
                errors=self.encoding_errors)
            self._decoder._encoding = encoding

        return self._decoder.decode(buf, final)

# TODO: handle \r\n, \r\x00, \r, \n.
    @asyncio.coroutine
    def readline(self):
        r"""
        Read one line.

        Where "line" is a sequence of characters ending with ``\n``.

        If EOF is received, and ``\n`` was not found, the method will
        return the partial read string.

        If the EOF was received and the internal buffer is empty,
        return an empty string.

        This method is a :func:`asyncio.coroutine`.
        """
        buf_line = yield from super().readline()
        if self._protocol.default_encoding:
            return self.decode(buf_line, final=True)
        return buf_line

    @asyncio.coroutine
    def read(self, n=-1):
        """
        Read up to *n* bytes.

        If the EOF was received and the internal buffer is empty, return an
        empty string.

        :param int n:  If *n* is not provided, or set to -1, read until EOF
            and return all characters as one large string.
        :rtype: str

        This method is a :func:`asyncio.coroutine`.
        """
        buf = yield from super().read(n)

        if not self._protocol.default_encoding:
            return buf

        if n < 0:
            return self.decode(buf, final=True)

        if buf == b'':
            return ''

        ucs = self.decode(buf)
        while not ucs:
            # we have received an incomplete multibyte encoding which so far
            # has not decoded to a completed unicode point.  We must continue
            # to read from super() until completed.  We do this *one byte at
            # a time*, expecting very few bytes remaining to complete.
            buf = yield from super().read(1)
            if not buf:
                # an incomplete multibyte followed by EOF, discard the bytes
                # which have so far failed to decode and signal EOF to caller.
                break
            ucs += self.decode(buf)
        return ucs

    @asyncio.coroutine
    def readexactly(self, n):
        """
        Read exactly *n* bytes.

        :raises: asyncio.IncompleteReadError: if the end of the stream is
            reached before *n* can be read. the
            :attr:`asyncio.IncompleteReadError.partial` attribute of the
            exception contains the partial read characters.

        This method is a :func:`asyncio.coroutine`.

        :rtype: str
        """
        buf = yield from super().read(n)
        if not self._protocol.default_encoding:
            return buf
        return self.decode(buf)

    def __repr__(self):
        """Description of stream encoding state."""
        encoding = (self._protocol.default_encoding and
                    self._protocol.encoding(incoming=True))
        # may be encoding=False or encoding='utf8'
        return '<TelnetReader encoding={0!r}>'.format(encoding)
