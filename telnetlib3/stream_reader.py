"""Module provides class TelnetReader and TelnetReaderUnicode."""
# std imports
import sys
import codecs
import asyncio
import logging
import warnings
import asyncio

from asyncio import events
from asyncio import format_helpers

__all__ = (
    "TelnetReader",
    "TelnetReaderUnicode",
)

_DEFAULT_LIMIT = 2 ** 16  # 64 KiB


class TelnetReader:
    """
    This is a copy of :class:`asyncio.StreamReader`, with a little
    care for telnet-like readline(), and something about _waiter which I don't
    really
    """

    _source_traceback = None

    def __init__(self, limit=_DEFAULT_LIMIT):
        self.log = logging.getLogger(__name__)
        # The line length limit is  a security feature;
        # it also doubles as half the buffer limit.

        if limit <= 0:
            raise ValueError("Limit cannot be <= 0")

        self._limit = limit
        self._loop = asyncio.get_event_loop_policy().get_event_loop()
        self._buffer = bytearray()
        self._eof = False  # Whether we're done.
        self._waiter = None  # A future used by _wait_for_data()
        self._exception = None
        self._transport = None
        self._paused = False
        if self._loop.get_debug():
            self._source_traceback = format_helpers.extract_stack(sys._getframe(1))

    def __repr__(self):
        """Description of stream encoding state."""
        info = [type(self).__name__]
        if self._buffer:
            info.append(f"{len(self._buffer)} bytes")
        if self._eof:
            info.append("eof")
        if self._limit != _DEFAULT_LIMIT:
            info.append(f"limit={self._limit}")
        if self._waiter:
            info.append(f"waiter={self._waiter!r}")
        if self._exception:
            info.append(f"exception={self._exception!r}")
        if self._transport:
            info.append(f"transport={self._transport!r}")
        if self._paused:
            info.append("paused")
        info.append("encoding=False")
        return "<{}>".format(" ".join(info))

    def exception(self):
        return self._exception

    def set_exception(self, exc):
        self._exception = exc

        waiter = self._waiter
        if waiter is not None:
            self._waiter = None
            if not waiter.cancelled():
                waiter.set_exception(exc)

    def _wakeup_waiter(self):
        """Wakeup read*() functions waiting for data or EOF."""
        waiter = self._waiter
        if waiter is not None:
            self._waiter = None
            if not waiter.cancelled():
                waiter.set_result(None)

    def set_transport(self, transport):
        assert self._transport is None, "Transport already set"
        self._transport = transport

    def _maybe_resume_transport(self):
        if self._paused and len(self._buffer) <= self._limit:
            self._paused = False
            self._transport.resume_reading()

    def feed_eof(self):
        self._eof = True
        self._wakeup_waiter()

    def at_eof(self):
        """Return True if the buffer is empty and 'feed_eof' was called."""
        return self._eof and not self._buffer

    def feed_data(self, data):
        assert not self._eof, "feed_data after feed_eof"

        if not data:
            return

        self._buffer.extend(data)
        self._wakeup_waiter()

        if (
            self._transport is not None
            and not self._paused
            and len(self._buffer) > 2 * self._limit
        ):
            try:
                self._transport.pause_reading()
            except NotImplementedError:
                # The transport can't be paused.
                # We'll just have to buffer all data.
                # Forget the transport so we don't keep trying.
                self._transport = None
            else:
                self._paused = True

    async def _wait_for_data(self, func_name):
        """Wait until feed_data() or feed_eof() is called.

        If stream was paused, automatically resume it.
        """
        # StreamReader uses a future to link the protocol feed_data() method
        # to a read coroutine. Running two read coroutines at the same time
        # would have an unexpected behaviour. It would not possible to know
        # which coroutine would get the next data.
        if self._waiter is not None:
            raise RuntimeError(
                f"{func_name}() called while another coroutine is "
                f"already waiting for incoming data"
            )

        assert not self._eof, "_wait_for_data after EOF"

        # Waiting for data while paused will make deadlock, so prevent it.
        # This is essential for readexactly(n) for case when n > self._limit.
        if self._paused:
            self._paused = False
            self._transport.resume_reading()

        self._waiter = self._loop.create_future()
        try:
            await self._waiter
        finally:
            self._waiter = None

    async def readline(self):
        """Read chunk of data from the stream until newline (b'\n') is found.

        On success, return chunk that ends with newline. If only partial
        line can be read due to EOF, return incomplete line without
        terminating newline. When EOF was reached while no bytes read, empty
        bytes object is returned.

        If limit is reached, ValueError will be raised. In that case, if
        newline was found, complete line including newline will be removed
        from internal buffer. Else, internal buffer will be cleared. Limit is
        compared against part of the line without newline.

        If stream was paused, this function will automatically resume it if
        needed.
        """
        sep = b"\n"
        seplen = len(sep)
        try:
            line = await self.readuntil(sep)
        except asyncio.IncompleteReadError as e:
            return e.partial
        except asyncio.LimitOverrunError as e:
            if self._buffer.startswith(sep, e.consumed):
                del self._buffer[: e.consumed + seplen]
            else:
                self._buffer.clear()
            self._maybe_resume_transport()
            raise ValueError(e.args[0])
        return line

    async def readuntil(self, separator=b"\n"):
        """Read data from the stream until ``separator`` is found.

        On success, the data and separator will be removed from the
        internal buffer (consumed). Returned data will include the
        separator at the end.

        Configured stream limit is used to check result. Limit sets the
        maximal length of data that can be returned, not counting the
        separator.

        If an EOF occurs and the complete separator is still not found,
        an IncompleteReadError exception will be raised, and the internal
        buffer will be reset.  The IncompleteReadError.partial attribute
        may contain the separator partially.

        If the data cannot be read because of over limit, a
        LimitOverrunError exception  will be raised, and the data
        will be left in the internal buffer, so it can be read again.
        """
        seplen = len(separator)
        if seplen == 0:
            raise ValueError("Separator should be at least one-byte string")

        if self._exception is not None:
            raise self._exception

        # Consume whole buffer except last bytes, which length is
        # one less than seplen. Let's check corner cases with
        # separator='SEPARATOR':
        # * we have received almost complete separator (without last
        #   byte). i.e buffer='some textSEPARATO'. In this case we
        #   can safely consume len(separator) - 1 bytes.
        # * last byte of buffer is first byte of separator, i.e.
        #   buffer='abcdefghijklmnopqrS'. We may safely consume
        #   everything except that last byte, but this require to
        #   analyze bytes of buffer that match partial separator.
        #   This is slow and/or require FSM. For this case our
        #   implementation is not optimal, since require rescanning
        #   of data that is known to not belong to separator. In
        #   real world, separator will not be so long to notice
        #   performance problems. Even when reading MIME-encoded
        #   messages :)

        # `offset` is the number of bytes from the beginning of the buffer
        # where there is no occurrence of `separator`.
        offset = 0

        # Loop until we find `separator` in the buffer, exceed the buffer size,
        # or an EOF has happened.
        while True:
            buflen = len(self._buffer)

            # Check if we now have enough data in the buffer for `separator` to
            # fit.
            if buflen - offset >= seplen:
                isep = self._buffer.find(separator, offset)

                if isep != -1:
                    # `separator` is in the buffer. `isep` will be used later
                    # to retrieve the data.
                    break

                # see upper comment for explanation.
                offset = buflen + 1 - seplen
                if offset > self._limit:
                    raise asyncio.LimitOverrunError(
                        "Separator is not found, and chunk exceed the limit", offset
                    )

            # Complete message (with full separator) may be present in buffer
            # even when EOF flag is set. This may happen when the last chunk
            # adds data which makes separator be found. That's why we check for
            # EOF *ater* inspecting the buffer.
            if self._eof:
                chunk = bytes(self._buffer)
                self._buffer.clear()
                raise asyncio.IncompleteReadError(chunk, None)

            # _wait_for_data() will resume reading if stream was paused.
            await self._wait_for_data("readuntil")

        if isep > self._limit:
            raise asyncio.LimitOverrunError(
                "Separator is found, but chunk is longer than limit", isep
            )

        chunk = self._buffer[: isep + seplen]
        del self._buffer[: isep + seplen]
        self._maybe_resume_transport()
        return bytes(chunk)

    async def read(self, n=-1):
        """Read up to `n` bytes from the stream.

        If n is not provided, or set to -1, read until EOF and return all read
        bytes. If the EOF was received and the internal buffer is empty, return
        an empty bytes object.

        If n is zero, return empty bytes object immediately.

        If n is positive, this function try to read `n` bytes, and may return
        less or equal bytes than requested, but at least one byte. If EOF was
        received before any byte is read, this function returns empty byte
        object.

        Returned value is not limited with limit, configured at stream
        creation.

        If stream was paused, this function will automatically resume it if
        needed.
        """

        if self._exception is not None:
            raise self._exception

        if n == 0:
            return b""

        if n < 0:
            # This used to just loop creating a new waiter hoping to
            # collect everything in self._buffer, but that would
            # deadlock if the subprocess sends more than self.limit
            # bytes.  So just call self.read(self._limit) until EOF.
            blocks = []
            while True:
                block = await self.read(self._limit)
                if not block:
                    break
                blocks.append(block)
            return b"".join(blocks)

        if not self._buffer and not self._eof:
            await self._wait_for_data("read")

        # This will work right even if buffer is less than n bytes
        data = bytes(self._buffer[:n])
        del self._buffer[:n]

        self._maybe_resume_transport()
        return data

    async def readexactly(self, n):
        """Read exactly `n` bytes.

        Raise an IncompleteReadError if EOF is reached before `n` bytes can be
        read. The IncompleteReadError.partial attribute of the exception will
        contain the partial read bytes.

        if n is zero, return empty bytes object.

        Returned value is not limited with limit, configured at stream
        creation.

        If stream was paused, this function will automatically resume it if
        needed.
        """
        if n < 0:
            raise ValueError("readexactly size can not be less than zero")

        if self._exception is not None:
            raise self._exception

        if n == 0:
            return b""

        while len(self._buffer) < n:
            if self._eof:
                incomplete = bytes(self._buffer)
                self._buffer.clear()
                raise asyncio.IncompleteReadError(incomplete, n)

            await self._wait_for_data("readexactly")

        if len(self._buffer) == n:
            data = bytes(self._buffer)
            self._buffer.clear()
        else:
            data = bytes(self._buffer[:n])
            del self._buffer[:n]
        self._maybe_resume_transport()
        return data

    def __aiter__(self):
        return self

    async def __anext__(self):
        val = await self.readline()
        if val == b"":
            raise StopAsyncIteration
        return val

    # these next two are deprecated in 2.0.1, feed_eof should just be called,
    # instead of the commit 260dd63a that introduced a close() method on a
    # reader.
    @property
    def connection_closed(self):
        warnings.warn(
            "connection_closed property removed, use at_eof() instead",
            DeprecationWarning,
        )
        return self._eof

    def close(self):
        warnings.warn(
            "connection_closed deprecated, use feed_eof() instead", DeprecationWarning
        )
        self.feed_eof()

    async def readline(self):
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
        """
        if self._exception is not None:
            raise self._exception

        line = bytearray()
        not_enough = True

        while not_enough:
            while self._buffer and not_enough:
                search_results_pos_kind = (
                    (self._buffer.find(b"\r\n"), b"\r\n"),
                    (self._buffer.find(b"\r\x00"), b"\r\x00"),
                    (self._buffer.find(b"\r"), b"\r"),
                    (self._buffer.find(b"\n"), b"\n"),
                )

                # sort by (position, length * -1), so that the
                # smallest sorted value is the longest-match,
                # preferring '\r\n' over '\r', for example.
                matches = [
                    (_pos, len(_kind) * -1, _kind)
                    for _pos, _kind in search_results_pos_kind
                    if _pos != -1
                ]

                if not matches:
                    line.extend(self._buffer)
                    self._buffer.clear()
                    continue

                # position is nearest match,
                pos, _, kind = min(matches)
                if kind == b"\r\x00":
                    # trim out '\x00'
                    begin, end = pos + 1, pos + 2
                elif kind == b"\r\n":
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
                await self._wait_for_data("readline")

        self._maybe_resume_transport()
        buf = bytes(line)
        return buf


class TelnetReaderUnicode(TelnetReader):
    #: Late-binding instance of :class:`codecs.IncrementalDecoder`, some
    #: bytes may be lost if the protocol's encoding is changed after
    #: previously receiving a partial multibyte.  This isn't common in
    #: practice, however.
    _decoder = None

    def __init__(self, fn_encoding, *, limit=_DEFAULT_LIMIT, encoding_errors="replace"):
        """
        A Unicode StreamReader interface for Telnet protocol.

        :param Callable fn_encoding: function callback, receiving boolean
            keyword argument, ``incoming=True``, which is used by the callback
            to determine what encoding should be used to decode the value in
            the direction specified.
        """
        super().__init__(limit=limit)

        assert callable(fn_encoding), fn_encoding
        self.fn_encoding = fn_encoding
        self.encoding_errors = encoding_errors

    def decode(self, buf, final=False):
        """Decode bytes ``buf`` using preferred encoding."""
        if buf == b"":
            return ""  # EOF

        encoding = self.fn_encoding(incoming=True)

        # late-binding,
        if self._decoder is None or encoding != self._decoder._encoding:
            self._decoder = codecs.getincrementaldecoder(encoding)(
                errors=self.encoding_errors
            )
            self._decoder._encoding = encoding

        return self._decoder.decode(buf, final)

    async def readline(self):
        """
        Read one line.

        See ancestor method, :func:`~TelnetReader.readline` for details.
        """
        buf = await super().readline()
        return self.decode(buf)

    async def read(self, n=-1):
        """
        Read up to *n* bytes.

        If the EOF was received and the internal buffer is empty, return an
        empty string.

        :param int n:  If *n* is not provided, or set to -1, read until EOF
            and return all characters as one large string.
        :rtype: str
        """
        if self._exception is not None:
            raise self._exception

        if not n:
            return ""

        if n < 0:
            # This used to just loop creating a new waiter hoping to
            # collect everything in self._buffer, but that would
            # deadlock if the subprocess sends more than self.limit
            # bytes.  So just call self.read(self._limit) until EOF.
            blocks = []
            while True:
                block = await self.read(self._limit)
                if not block:
                    # eof
                    break
                blocks.append(block)
            return "".join(blocks)

        else:
            if not self._buffer and not self._eof:
                await self._wait_for_data("read")

        buf = self.decode(bytes(self._buffer))
        if n < 0 or len(buf) <= n:
            u_data = buf
            self._buffer.clear()
        else:
            u_data = ""
            while n > len(u_data):
                u_data += self.decode(bytes([self._buffer.pop(0)]))

        self._maybe_resume_transport()
        return u_data

    async def readexactly(self, n):
        """
        Read exactly *n* unicode characters.

        :raises asyncio.IncompleteReadError: if the end of the stream is
            reached before *n* can be read. the
            :attr:`asyncio.IncompleteReadError.partial` attribute of the
            exception contains the partial read characters.
        :rtype: str
        """
        if self._exception is not None:
            raise self._exception

        blocks = []
        while n > 0:
            block = await self.read(n)
            if not block:
                partial = "".join(blocks)
                raise asyncio.IncompleteReadError(partial, len(partial) + n)
            blocks.append(block)
            n -= len(block)

        return "".join(blocks)

    def __repr__(self):
        """Description of stream encoding state."""
        encoding = None
        if callable(self.fn_encoding):
            encoding = self.fn_encoding(incoming=True)
        return (
            "<TelnetReaderUnicode encoding={encoding!r} limit={self._limit!r} "
            "buflen={buflen} eof={self._eof}>".format(
                encoding=encoding, buflen=len(self._buffer), self=self
            )
        )
