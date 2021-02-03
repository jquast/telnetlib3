"""Module provides class TelnetReader and TelnetReaderUnicode."""
# std imports
import codecs
import asyncio
import logging

__all__ = ('TelnetReader', 'TelnetReaderUnicode', )


class TelnetReader(asyncio.StreamReader):
    """A reader interface for the telnet protocol."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._connection_closed = False
        self.log = logging.getLogger(__name__)

    @property
    def connection_closed(self):
        return self._connection_closed

    def _cancel_task(self):
        if self._waiter is None:
            return
        try:
            # cancel the ongoing task when connection is closed
            # raises asyncio.CancelledError
            self._waiter.cancel()
        except asyncio.CancelledError:
            self.log.debug('Connection closed, _waiter cancelled.')

    def close(self):
        self._connection_closed = True
        self._cancel_task()

    @asyncio.coroutine
    def readline(self):
        r"""
        Read one line.

        Where "line" is a sequence of characters ending with CR LF, LF,
        or CR NUL. This readline function is a strict interpretation of
        Telnet Protocol :rfc:`854`.

          The sequence "CR LF" must be treated as a single "new line" character
          and used whenever their combined action is intended; The sequence "CR
          NUL" must be used where a carriage return alone is actually desired;
          and the CR character must be avoided in other contexts.

        And therefor, a line does not yield for a stream containing a
        CR if it is not succeeded by NUL or LF.

        ================= =====================
        Given stream      readline() yields
        ================= =====================
        ``--\r\x00---``   ``--\r``, ``---`` *...*
        ``--\r\n---``     ``--\r\n``, ``---`` *...*
        ``--\n---``       ``--\n``, ``---`` *...*
        ``--\r---``       ``--\r``, ``---`` *...*
        ================= =====================

        If EOF is received before the termination of a line, the method will
        yield the partially read string.

        This method is a :func:`~asyncio.coroutine`.
        """
        if self._exception is not None:
            raise self._exception

        line = bytearray()
        not_enough = True

        while not_enough:
            while self._buffer and not_enough:
                search = [
                    (self._buffer.find(b'\r\n'), b'\r\n'),
                    (self._buffer.find(b'\r\x00'), b'\r\x00'),
                    (self._buffer.find(b'\r'), b'\r'),
                    (self._buffer.find(b'\n'), b'\n'),
                ]

                # sort by (position, length * -1), so that the
                # smallest sorted value is the longest-match,
                # preferring '\r\n' over '\r', for example.
                matches = [(_pos, len(_kind) * -1, _kind)
                           for _pos, _kind in search
                           if _pos != -1]

                if not matches:
                    line.extend(self._buffer)
                    self._buffer.clear()
                    continue

                # position is nearest match,
                pos, _, kind = min(matches)
                if kind == b'\r\x00':
                    # trim out '\x00'
                    begin, end = pos + 1, pos + 2
                elif kind == b'\r\n':
                    begin = end = pos + 2
                else:
                    # '\r' or '\n'
                    begin = end = pos + 1
                line.extend(self._buffer[:begin])
                del self._buffer[:end]
                not_enough = False

            if self._eof:
                break

            if not_enough:
                yield from self._wait_for_data('readline')

        self._maybe_resume_transport()
        buf = bytes(line)
        return buf

    def __repr__(self):
        """Description of stream encoding state."""
        return ('<TelnetReader encoding=False limit={self._limit} buflen={0} '
                'eof={self._eof}>'.format(len(self._buffer), self=self))


class TelnetReaderUnicode(TelnetReader):
    #: Late-binding instance of :class:`codecs.IncrementalDecoder`, some
    #: bytes may be lost if the protocol's encoding is changed after
    #: previously receiving a partial multibyte.  This isn't common in
    #: practice, however.
    _decoder = None

    def __init__(self, fn_encoding, *, limit=asyncio.streams._DEFAULT_LIMIT,
                 loop=None, encoding_errors='replace'):
        """
        A Unicode StreamReader interface for Telnet protocol.

        :param Callable fn_encoding: function callback, receiving boolean
            keyword argument, ``incoming=True``, which is used by the callback
            to determine what encoding should be used to decode the value in
            the direction specified.
        """
        loop = loop or asyncio.get_event_loop()
        super().__init__(limit=limit, loop=loop)

        assert callable(fn_encoding), fn_encoding
        self.fn_encoding = fn_encoding
        self.encoding_errors = encoding_errors

    def decode(self, buf, final=False):
        """Decode bytes ``buf`` using preferred encoding."""
        if buf == b'':
            return ''  # EOF

        encoding = self.fn_encoding(incoming=True)

        # late-binding,
        if (self._decoder is None or encoding != self._decoder._encoding):
            self._decoder = codecs.getincrementaldecoder(encoding)(
                errors=self.encoding_errors)
            self._decoder._encoding = encoding

        return self._decoder.decode(buf, final)

    @asyncio.coroutine
    def readline(self):
        """
        Read one line.

        See ancestor method, :func:`~TelnetReader.readline` for details.

        This method is a :func:`~asyncio.coroutine`.
        """
        buf = yield from super().readline()
        return self.decode(buf)

    @asyncio.coroutine
    def read(self, n=-1):
        """
        Read up to *n* bytes.

        If the EOF was received and the internal buffer is empty, return an
        empty string.

        :param int n:  If *n* is not provided, or set to -1, read until EOF
            and return all characters as one large string.
        :rtype: str

        This method is a :func:`~asyncio.coroutine`.
        """
        if self._exception is not None:
            raise self._exception

        if not n:
            return u''

        if n < 0:
            # This used to just loop creating a new waiter hoping to
            # collect everything in self._buffer, but that would
            # deadlock if the subprocess sends more than self.limit
            # bytes.  So just call self.read(self._limit) until EOF.
            blocks = []
            while True:
                block = yield from self.read(self._limit)
                if not block:
                    # eof
                    break
                blocks.append(block)
            return u''.join(blocks)

        else:
            if not self._buffer and not self._eof:
                yield from self._wait_for_data('read')

        buf = self.decode(bytes(self._buffer))
        if n < 0 or len(buf) <= n:
            u_data = buf
            self._buffer.clear()
        else:
            u_data = u''
            while n > len(u_data):
                u_data += self.decode(bytes([self._buffer.pop(0)]))

        self._maybe_resume_transport()
        return u_data

    @asyncio.coroutine
    def readexactly(self, n):
        """
        Read exactly *n* unicode characters.

        :raises asyncio.IncompleteReadError: if the end of the stream is
            reached before *n* can be read. the
            :attr:`asyncio.IncompleteReadError.partial` attribute of the
            exception contains the partial read characters.
        :rtype: str

        This method is a :func:`~asyncio.coroutine`.
        """
        if self._exception is not None:
            raise self._exception

        blocks = []
        while n > 0:
            block = yield from self.read(n)
            if not block:
                partial = u''.join(blocks)
                raise asyncio.IncompleteReadError(partial, len(partial) + n)
            blocks.append(block)
            n -= len(block)

        return u''.join(blocks)

    def __repr__(self):
        """Description of stream encoding state."""
        if callable(self.fn_encoding):
            encoding = self.fn_encoding(incoming=True)
        return ('<TelnetReaderUnicode encoding={0!r} limit={self._limit} '
                'buflen={1} eof={self._eof}>'.format(
                    encoding, len(self._buffer), self=self))
