# std imports
import logging
import asyncio
import codecs

__all__ = ('TelnetReader',)


class TelnetReader(asyncio.StreamReader):
    """
    A reader interface from telnet protocol.
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
        encoding = self.protocol.encoding(incoming=True)

        # late-binding,
        if (self._decoder is None or encoding != self._decoder._encoding):
            self._decoder = codecs.getincrementaldecoder(encoding)(
                errors=self.encoding_errors)
            self._decoder._encoding = encoding

        return self._decoder.decode(buf, final)

    @property
    def protocol(self):
        """The protocol attached to this stream."""
        return self._protocol

    @asyncio.coroutine
    def readline(self, auto_decode=True):
        r"""
        Read one line.

        Where "line" is a sequence of characters ending with ``\r``.

        If EOF is received, and ``\r`` was not found, the method will
        return the partial read string.

        This method is a :func:`asyncio.coroutine`.
        """
        ## TODO: handle \r\n, \r\x00, \r, \n.
        if self._exception is not None:
            raise self._exception

        line = bytearray()
        not_enough = True

        while not_enough:
            while self._buffer and not_enough:
                ichar = self._buffer.find(b'\r')
                if ichar < 0:
                    line.extend(self._buffer)
                    self._buffer.clear()
                else:
                    ichar += 1
                    line.extend(self._buffer[:ichar])
                    del self._buffer[:ichar]
                    not_enough = False

                if len(line) > self._limit:
                    self._maybe_resume_transport()
                    raise ValueError('Line is too long')

            if self._eof:
                break

            if not_enough:
                yield from self._wait_for_data('readline')

        self._maybe_resume_transport()
        buf = bytes(line)
        if auto_decode and self.protocol.default_encoding:
            return self.decode(buf)
        return buf

    @asyncio.coroutine
    def read(self, n=-1, auto_decode=True):
        """
        Read up to *n* bytes.

        If the EOF was received and the internal buffer is empty, return an
        empty string.

        :param int n:  If *n* is not provided, or set to -1, read until EOF
            and return all characters as one large string.
        :rtype: str

        This method is a :func:`asyncio.coroutine`.
        """
        if self._exception is not None:
            raise self._exception

        if not n:
            if auto_decode and self.protocol.default_encoding:
                return ''
            return b''

        if n < 0:
            # This used to just loop creating a new waiter hoping to
            # collect everything in self._buffer, but that would
            # deadlock if the subprocess sends more than self.limit
            # bytes.  So just call self.read(self._limit) until EOF.
            blocks = []
            while True:
                block = yield from self.read(self._limit, auto_decode=False)
                if not block:
                    break
                blocks.append(block)
            buf = b''.join(blocks)
            if auto_decode and self.protocol.default_encoding:
                return self.decode(buf)
            return buf
        else:
            if not self._buffer and not self._eof:
                yield from self._wait_for_data('read')

        if n < 0 or len(self._buffer) <= n:
            data = bytes(self._buffer)
            self._buffer.clear()
        else:
            # n > 0 and len(self._buffer) > n
            data = bytes(self._buffer[:n])
            del self._buffer[:n]

        self._maybe_resume_transport()
        if auto_decode and self.protocol.default_encoding:
            return self.decode(data)
        return data

    @asyncio.coroutine
    def readexactly(self, n, auto_decode=True):
        """
        Read exactly *n* bytes.

        :raises: asyncio.IncompleteReadError: if the end of the stream is
            reached before *n* can be read. the
            :attr:`asyncio.IncompleteReadError.partial` attribute of the
            exception contains the partial read characters.
        :rtype: str

        This method is a :func:`asyncio.coroutine`.
        """
        if self._exception is not None:
            raise self._exception

        blocks = []
        while n > 0:
            block = yield from self.read(n, auto_decode=False)
            if not block:
                partial = b''.join(blocks)
                raise asyncio.IncompleteReadError(partial, len(partial) + n)
            blocks.append(block)
            n -= len(block)

        if auto_decode and self.protocol.default_encoding:
            return self.decode(b''.join(blocks))
        return b''.join(blocks)

    def __repr__(self):
        """Description of stream encoding state."""
        encoding = (self.protocol.default_encoding and
                    self.protocol.encoding(incoming=True))

        # may be encoding=False or encoding='utf8'
        return '<TelnetReader encoding={0!r}>'.format(encoding)
