# std imports
import traceback
import logging
import asyncio
import codecs
import sys


class StreamReader(asyncio.StreamReader):
    """
    A reader interface to the client from TelnetServer perspective.

    The API's responsibility is to feed standard protocol byte
    data to :meth:`feed_byte`, and for the API-using developer
    to yield from the various ``read``-family of calls.

    - ``protocol.default_encoding`` attribute.
    - ``protocol.set_encoding`` method.
    - ``protocol.encoding`` method.
    """

    #: Late-binding instance of :class:`codecs.IncrementalDecoder`, some
    #: bytes may be lost when ``final=False`` is used as an argument to
    #: :meth:`decode` after ``protocol.set_encoding`` has been called with
    #: a new encoding.
    _decoder = None

    def __init__(self, protocol, limit=asyncio.streams._DEFAULT_LIMIT,
                 loop=None, log=None, encoding_error='replace'):
        self.log = log or logging.getLogger(__name__)
        self._protocol = protocol
        if loop is None:
            loop = asyncio.get_event_loop()

        super().__init__(limit=limit, loop=loop)

        #: same as meaning as ``error`` in :class:`codecs.Codec`.
        self.encoding_error = encoding_error

    def decode(self, buf, final=False):
        """Decode bytes ``buf`` using preferred encoding."""
        encoding = self._protocol.encoding(incoming=True)
        self.log.debug('decode: {!r}'.format(encoding))

        # late-binding,
        if (self._decoder is None or encoding != self._decoder._encoding):
            try:
                self._decoder = codecs.getincrementaldecoder(encoding)(
                    errors=self.encoding_error)
                self._decoder._encoding = encoding

            except LookupError:
                default_encoding = self._protocol.default_encoding
                if encoding == default_encoding:
                    raise

                # notify server log of encoding error before retrying using
                # server protocol-preferred ``default_encoding``.
                exc_msg = traceback.format_exception_only(*sys.exc_info[:2])
                self.log.debug(exc_msg)

                # change to default encoding and try once more
                self._protocol.set_encoding(default_encoding)

                self._decoder = codecs.getincrementaldecoder(default_encoding)(
                    errors=self.encoding_error)
                self._decoder._encoding = default_encoding

        return self._decoder.decode(buf, final)

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
        return self.decode(buf_line, final=True)

    @asyncio.coroutine
    def read(self, n=-1):
        """
        Read up to *n* characters. If *n* is not provided, or set to -1,
        read until EOF and return all characters as one large string.

        If the EOF was received and the internal buffer is empty, return
        an empty bytes object.

        This method is a :func:`asyncio.coroutine`.
        """
        if n < 0:
            buf = yield from super().read(n)
            return self.decode(buf, final=True)

        # we interpret 'n' not as number of bytes from the transport,
        # but rather the number of completed unicode characters, which
        # may require many more bytes than 'n' to satisfy.
        string = u''

        while n > len(string):
            readsize = n
            if n > 0:
                readsize = n - len(string)

            buf = yield from super().read(readsize)

            string += self.decode(buf, final=False)

        return string

    @asyncio.coroutine
    def readexactly(self, n):
        """
        Read exactly *n* bytes.

        :raises: asyncio.IncompleteReadError: if the end of the stream is
            reached before *n* can be read. the
            :attr:`asyncio.IncompleteReadError.partial` attribute of the
            exception contains the partial read characters.

        This method is a :func:`asyncio.coroutine`.
        """
        # mirrors exactly what we derive, exception that it returns
        # unicode, and not bytes.
        if self._exception is not None:
            raise self._exception

        blocks = []
        while n > 0:
            block = yield from self.read(n)
            if not block:
                partial = ''.join(blocks)
                raise asyncio.IncompleteReadError(partial, len(partial) + n)
            blocks.append(block)
            n -= len(block)

        return ''.join(blocks)

    def __repr__(self):
        """Description of stream encoding state."""
        encoding = self._protocol.encoding(outgoing=True)
        return '<StreamReader encoding={0}>'.format(encoding)
