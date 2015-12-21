# std imports
import asyncio

# local
from . import server_base


class UnicodeMixin(server_base.BaseServer):
    """Provides unicode streams by negotiating encoding."""

    def __init__(self, encoding='utf8', encoding_error='replace',
                 force_binary=False, **kwargs):
        """
        :param str encoding: The default encoding preferred by the server
            if not otherwise negotiated.
        :param str encoding_error: Same meaning as :class:`codecs.Codec`.
        :param bool force_binary: When ``True``, the encoding specified is
            used for both directions without ``BINARY`` negotiation, rfc-856_.
        """
        self.default_encoding = encoding
        self.encoding_error = encoding_error
        self.force_binary = force_binary

        #: Future receives ``self`` as result after completion
        #: of encoding negotiation considered complete.
        self.waiter_encoding = asyncio.Future()

        super().__init__(**kwargs)

        self._tasks.append(self.waiter_encoding)

    def begin_advanced_negotiation(self):
        """Request ``IAC WILL BINARY`` and ``IAC DO CHARSET``."""
        from .telopt import WILL, BINARY, DO, CHARSET
        super().begin_advanced_negotiation()

        self.writer.iac(WILL, BINARY)
        self.writer.iac(DO, CHARSET)

    def check_negotiation(self, final=False):
        """Periodically check for completion of :attr:`waiter_encoding`."""
        from .telopt import TTYPE
        parent = super().check_negotiation()

        result = self._check_encoding()
        encoding = self.encoding(outgoing=True, incoming=True)
        if not self.waiter_encoding.done() and result:
            self.log.debug('encoding complete: {0!r}'.format(encoding))
            self.waiter_encoding.set_result(self)

        elif (not self.waiter_encoding.done() and
              self.writer.remote_option.get(TTYPE) is False):
            # if the remote end doesn't support TTYPE, which is agreed upon
            # to continue towards advanced negotiation of CHARSET, we assume
            # the distant end would not support it, declaring encoding failed.
            self.log.debug('encoding failed after {0:1.2f}s: {1}'
                           .format(self.duration, encoding))
            self.waiter_encoding.set_result(self)
            result = True

        elif not self.waiter_encoding.done() and final:
            self.log.debug('encoding failed after {0:1.2f}s: {1}'
                           .format(self.duration, encoding))
            self.waiter_encoding.set_result(self)
            result = True

        return parent and result

    def encoding(self, outgoing=None, incoming=None):
        """
        Return encoding for the given stream direction.

        :param bool outgoing: Whether the return value is suitable for
            encoding bytes for transmission to client end.
        :param bool incoming: Whether the return value is suitable for
            decoding bytes received from the client.
        :raises TypeError: when a direction argument, either ``outgoing``
            or ``incoming``, was not set ``True``.
        :returns: ``'US-ASCII'`` for the directions indicated, unless
            ``BINARY`` rfc-856_ has been negotiated for the direction
            indicated or :attr`force_binary` is set ``True``.
        :rtype: str

        Value resolution order (first-matching):

        - value set by :meth:`set_encoding`.
        - value of :meth:`get_extra_info` using key argument, ``LANG``.
        - value of :attr:`default_encoding`.
        - ``US-ASCII`` when binary transmission not allowed.
        """
        if not (outgoing or incoming):
            raise TypeError("encoding arguments 'outgoing' and 'incoming' "
                            "are required: toggle at least one.")

        # may we encode in the direction indicated?
        _outgoing_only = outgoing and not incoming
        _incoming_only = not outgoing and incoming
        _bidirectional = outgoing and incoming
        may_encode = ((_outgoing_only and self.writer.outbinary) or
                      (_incoming_only and self.writer.inbinary) or
                      (_bidirectional and
                       self.writer.outbinary and self.writer.inbinary))

        if self.force_binary or may_encode:
            # prefer 'LANG' environment variable, if sent
            _lang = self.get_extra_info('LANG', None)
            if _lang and '.' in _lang:
                _, encoding = _lang.split('.', 1)
                return encoding
            # then our less common CHARSET negotiation,
            return self.get_extra_info('encoding', self.default_encoding)
        return 'US-ASCII'

    def _check_encoding(self):
        # Periodically check for completion of ``waiter_encoding``.
        from .telopt import DO, BINARY
        if (self.writer.outbinary and not self.writer.inbinary and
                not DO + BINARY in self.writer.pending_option):
            self.log.debug('BINARY in: direction request.')
            self.writer.iac(DO, BINARY)
            return False

        return self.writer.outbinary and self.writer.inbinary


class TimeoutServerMixin(server_base.BaseServer):
    """BaseServer Mix-in closes peer after timeout."""

    def __init__(self, timeout=300, **kwargs):
        """
        :param int timeout: Forcefully disconnect client in callback
            method :meth:`on_timeout` after given seconds have elapsed
            without client input.
        """
        super().__init__(**kwargs)
        self._extra['timeout'] = timeout
        self._timer = None

    def connection_made(self, transport):
        super().connection_made(transport)
        self.set_timeout()

    def data_received(self, data):
        # Derive and cause timer reset.
        self.set_timeout()
        super().data_received(data)

    def set_timeout(self, duration=-1):
        """
        Restart or unset timeout for client.

        :param int duration: When specified as a positive integer,
            schedules Future :attr:`self.waiter_timeout` with attached
            instance callback :meth:`timeout`.  When ``-1``, the value
            of :meth:`get_extra_info` for keyword ``timeout`` is used.
            When non-True, :attr:`waiter_timeout` is cancelled.
        """
        if duration == -1:
            duration = self.get_extra_info('timeout')
        if self._timer is not None:
            if self._timer in self._tasks:
                self._tasks.remove(self._timer)
            self._timer.cancel()
        if duration:
            self._timer = self._loop.call_later(duration, self.on_timeout)
            self._tasks.append(self._timer)
        self._extra['timeout'] = duration

    def on_timeout(self):
        """
        Callback received on session timeout.

        Default implementation writes "Timeout." bound by CRLF and closes.

        This can be disabled by calling :meth:`set_timeout` with
        :paramref:`~.set_timeout.duration` value of ``0`` or value of
        the same for keyword argument ``timeout``.
        """
        msg = 'timeout after {self.idle:1.2f}s'.format(self=self)
        self.writer.write('\r\nTimeout.\r\n')
        self.connection_lost(EOFError(msg))
