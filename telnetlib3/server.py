# std imports
import asyncio

# local
from . import server_base
from . import accessories

__all__ = ('TelnetServer', 'create_server')


class TelnetServer(server_base.BaseServer):
    """Telnet Server protocol performing common negotiation."""
    #: Maximum number of cycles to seek for all terminal types.  We are seeking
    #: the repeat or cycle of a terminal table, choosing the first -- but when
    #: negotiated by MUD clients, we chose the must Unix TERM appropriate,
    TTYPE_LOOPMAX = 8

    # Derived methods from base class

    def __init__(self, term='unknown', cols=80, rows=25, timeout=300,
                 *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.waiter_encoding = asyncio.Future()
        self._tasks.append(self.waiter_encoding)
        self._ttype_count = 1
        self._timer = None
        self._extra.update({
            'term': term,
            'charset': kwargs['encoding'] or '',
            'cols': cols,
            'rows': rows,
            'timeout': timeout
        })

    def connection_made(self, transport):
        from .telopt import NAWS, NEW_ENVIRON, TSPEED, TTYPE, XDISPLOC, CHARSET
        super().connection_made(transport)

        # begin timeout timer
        self.set_timeout()

        for tel_opt, callback_fn in [
            (NAWS, self.on_naws),
            (NEW_ENVIRON, self.on_environ),
            (TSPEED, self.on_tspeed),
            (TTYPE, self.on_ttype),
            (XDISPLOC, self.on_xdisploc),
            (CHARSET, self.on_charset),
        ]:
            self.writer.set_ext_callback(tel_opt, callback_fn)

    def data_received(self, data):
        self.set_timeout()
        super().data_received(data)

    def begin_negotiation(self):
        from .telopt import DO, TTYPE
        super().begin_negotiation()
        self.writer.iac(DO, TTYPE)

    def begin_advanced_negotiation(self):
        from .telopt import (DO, WILL, SGA, ECHO, BINARY,
                             NEW_ENVIRON, NAWS, CHARSET)
        super().begin_advanced_negotiation()
        self.writer.iac(WILL, SGA)
        self.writer.iac(WILL, ECHO)
        self.writer.iac(WILL, BINARY)
        self.writer.iac(DO, NEW_ENVIRON)
        self.writer.iac(DO, NAWS)
        if self.default_encoding:
            self.writer.iac(DO, CHARSET)

    def check_negotiation(self, final=False):
        from .telopt import TTYPE
        parent = super().check_negotiation()

        # in addition to the base class negotiation check, periodically check
        # for completion of bidirectional encoding negotiation.
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
            return parent

        elif not self.waiter_encoding.done() and final:
            self.log.debug('encoding failed after {0:1.2f}s: {1}'
                           .format(self.duration, encoding))
            self.waiter_encoding.set_result(self)
            return parent

        return parent and result

    # new methods

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

            # otherwise, uncommon CHARSET value if negotiated
            return self.get_extra_info('charset', self.default_encoding)
        return 'US-ASCII'

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

    # Callback methods

    def on_timeout(self):
        """
        Callback received on session timeout.

        Default implementation writes "Timeout." bound by CRLF and closes.

        This can be disabled by calling :meth:`set_timeout` with
        :paramref:`~.set_timeout.duration` value of ``0`` or value of
        the same for keyword argument ``timeout``.
        """
        self.writer.write('\r\nTimeout.\r\n')
        eof_msg = 'timeout after {self.idle:1.2f}s'.format(self=self)
        self.connection_lost(EOFError(eof_msg))

    def on_naws(self, rows, cols):
        """
        Callback receives NAWS response, rfc-1073_.

        :param int rows: screen size, by number of cells in height.
        :param int cols: screen size, by number of cells in width.
        """
        self._extra.update({'rows': rows, 'cols': cols})

    def on_environ(self, mapping):
        """Callback receives NEW_ENVIRON response, rfc-1572_."""
        # A well-formed client responds with empty values for variables to
        # mean "no value".  They might have it, they just may not wish to
        # divulge that information.  We pop these keys as a side effect in
        # the result statement of the following list comprehension.
        no_value = [mapping.pop(key) or key
                    for key, val in list(mapping.items())
                    if not val]

        # because we are working with "untrusted input", we make one fair
        # distinction: all keys received by NEW_ENVIRON are in uppercase.
        # this ensures a client may not override trusted values such as
        # 'peer'.
        u_mapping = {key.upper(): val for key, val in list(mapping.items())}

        self.log.debug('on_environ received: {0!r}'.format(u_mapping))
        self._extra.update(u_mapping)

    def on_tspeed(self, rx, tx):
        """Callback for TSPEED response, rfc-1079_."""
        self._extra['tspeed'] = '{0},{1}'.format(rx, tx)

    def on_ttype(self, ttype):
        """Callback for TTYPE response, rfc-930_."""
        # TTYPE may be requested multiple times, we honor this system and
        # attempt to cause the client to cycle, as their first response may
        # not be their most significant. All responses held as 'ttype{n}',
        # where {n} is their serial response order number.
        #
        # The most recently received terminal type by the server is
        # assumed TERM by this implementation, even when unsolicited.
        key = 'ttype{}'.format(self._ttype_count)
        self._extra[key] = ttype
        if ttype:
            self._extra['TERM'] = ttype

        _lastval = self.get_extra_info('ttype{0}'.format(
            self._ttype_count - 1))

        if key != 'ttype1' and ttype == self.get_extra_info('ttype1', None):
            # cycle has looped, stop
            self.log.debug('ttype cycle stop at {0}: {1}, looped.'
                           .format(key, ttype))

        elif (not ttype or self._ttype_count > self.TTYPE_LOOPMAX):
            # empty reply string or too many responses!
            self.log.warn('ttype cycle stop at {0}: {1}.'.format(key, ttype))

        elif (self._ttype_count == 3 and ttype.upper().startswith('MTTS ')):
            val = self.get_extra_info('ttype2')
            self.log.debug(
                'ttype cycle stop at {0}: {1}, using {2} from ttype2.'
                .format(key, ttype, val))
            self._extra['TERM'] = val

        elif (ttype == _lastval):
            self.log.debug('ttype cycle stop at {0}: {1}, repeated.'
                           .format(key, ttype))

        else:
            self.log.debug('ttype cycle cont at {0}: {1}.'
                           .format(key, ttype))
            self._ttype_count += 1
            self.writer.request_ttype()

    def on_xdisploc(self, xdisploc):
        """Callback for XDISPLOC response, rfc-1096_."""
        self._extra['xdisploc'] = xdisploc

    def on_charset(self, charset):
        """Callback for CHARSET response, rfc-2066_."""
        self._extra['charset'] = charset

    # private methods

    def _check_encoding(self):
        # Periodically check for completion of ``waiter_encoding``.
        from .telopt import DO, BINARY
        if (self.writer.outbinary and not self.writer.inbinary and
                not DO + BINARY in self.writer.pending_option):
            self.log.debug('BINARY in: direction request.')
            self.writer.iac(DO, BINARY)
            return False

        # are we able to negotiation BINARY bidirectionally?
        return self.writer.outbinary and self.writer.inbinary


@asyncio.coroutine
def create_server(
    protocol_factory=None, host=None, port=23, *, loop=None, log=None,
    encoding='utf8', encoding_errors='strict', force_binary=False,
    term='unknown', cols=80, rows=25, timeout=300, shell=None,
    waiter_closed=None, waiter_connected=None
):
    """
    Create a Telnet Server

    :param str encoding: The default assumed encoding, may be negotiation by
        client using NEW_ENVIRON value for LANG, or by CHARSET negotiation.
        The server's attached ``reader, writer`` streams accept and return
        When explicitly set ``False``, the attached streams interfaces become
        bytes-only.
    :param str encoding_errors: Same meaning as :class:`codecs.Codec`.
    :param bool force_binary: When ``True``, the encoding specified is
        used for both directions without ``BINARY`` negotiation, rfc-856_.
        This parameter has no effect when ``encoding=False``.
    :param str term: Default assumed value of writer.get_extra_info('term')
        until otherwise negotiated by TTYPE.
    :param int cols: Default assumed value of writer.get_extra_info('cols')
        until otherwise negotiated by NAWS.
    :param int rows: Default assumed value of writer.get_extra_info('rows')
        until otherwise negotiated by NAWS.
    :param int timeout: Causes clients to disconnect if idle for this duration,
        ensuring resources are freed on busy servers.  When explicitly set to
        ``False``, clients will not be disconnected for timeout.
    """

    protocol_factory = protocol_factory or TelnetServer
    loop = loop or asyncio.get_event_loop()

    def on_connect():
        return protocol_factory(
            loop=loop, log=log, encoding=encoding,
            encoding_errors=encoding_errors, force_binary=force_binary,
            term=term, cols=cols, rows=rows, timeout=timeout, shell=shell,
            waiter_closed=waiter_closed, waiter_connected=waiter_connected)

    return (yield from loop.create_server(on_connect, host, port))


def _get_argument_parser():
    import argparse
    parser = argparse.ArgumentParser(
        description="Telnet protocol server",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument('host', nargs='?', default='localhost',
                        help='bind address')
    parser.add_argument('port', nargs='?', default=6023, type=int,
                        help='bind port')
    parser.add_argument('--loglevel', default='info',
                        help='level name')
    parser.add_argument('--logfile',
                        help='filepath')
    parser.add_argument('--shell', default='telnetlib3.telnet_server_shell',
                        help='module.function_name')
    parser.add_argument('--encoding', default='utf8',
                        help='encoding name')
    parser.add_argument('--force-binary', action='store_true',
                        help='force binary transmission')
    parser.add_argument('--timeout', default=300, type=int,
                        help='idle disconnect (0 disables)')
    return parser


def _transform_args(args):

    # parse --shell='module.function' into function target
    module_name, func_name = args.shell.rsplit('.', 1)
    module = __import__(module_name)
    shell_function = getattr(module, func_name)
    assert callable(shell_function), shell_function

    return {
        'host': args.host,
        'port': args.port,
        'encoding': args.encoding,
        'force_binary': args.force_binary,
        'timeout': args.timeout,
        'loglevel': args.loglevel,
        'logfile': args.logfile,
        'shell': shell_function
    }


@asyncio.coroutine
def start_server(host, port, log, **kwds):
    server = yield from create_server(host=host, port=port, log=log, **kwds)
    return server

@asyncio.coroutine
def _sigterm_handler(server, log):
    log.info('SIGTERM received, closing server.')
    server.close()
    yield from server.wait_closed()


def main():
    import signal
    kwargs = _transform_args(_get_argument_parser().parse_args())
    config_msg = 'Server configuration: ' + accessories.repr_mapping(kwargs)

    loglevel = kwargs.pop('loglevel')
    logfile = kwargs.pop('logfile')
    host = kwargs.pop('host')
    port = kwargs.pop('port')

    log = accessories.make_logger(loglevel=loglevel, logfile=logfile)
    log.debug(config_msg)

    loop = asyncio.get_event_loop()

    # bind
    server = loop.run_until_complete(start_server(host, port, log, **kwargs))

    loop.add_signal_handler(signal.SIGTERM, asyncio.async,
                            _sigterm_handler(server, log))
    try:
        loop.run_until_complete(server.wait_closed())
    finally:
        loop.remove_sigal_handler(signal.SIGTERM)
