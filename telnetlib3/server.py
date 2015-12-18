# std imports
import asyncio
import socket

# local
from .server_mixins import UnicodeMixin, TimeoutServerMixin

__all__ = ('TelnetServer', 'create_server')


class TelnetServer(UnicodeMixin, TimeoutServerMixin):
    """Telnet Server protocol performing common negotiation."""
    #: Maximum number of cycles to seek for all terminal types offered.
    TTYPE_LOOPMAX = 8

    # TODO: 'encoding=None' is bytes-only interface
    def __init__(self, term='unknown', cols=80, rows=25, *args, **kwargs):
        """
        :param str term: Default terminal type unless negotiated.
        :param int cols: Default terminal width.
        :param int rows: Default terminal height.
        """
        super().__init__(*args, **kwargs)
        self._extra.update({'term': term, 'cols': cols, 'rows': rows})
        self._ttype_count = 1


    def connection_made(self, transport):
        from .telopt import NAWS, NEW_ENVIRON, TSPEED, TTYPE, XDISPLOC
        super().connection_made(transport)

        for tel_opt, callback_fn in [
            (NAWS, self.on_naws),
            (NEW_ENVIRON, self.on_environ),
            (TSPEED, self.on_tspeed),
            (TTYPE, self.on_ttype),
            (XDISPLOC, self.on_xdisploc)
        ]:
            self.writer.set_ext_callback(tel_opt, callback_fn)

    def begin_negotiation(self):
        """
        Begin on-connect negotiation.

        Deriving implementations should always call
        ``super().begin_negotiation()``.
        """
        if self._closing:
            return
        super().begin_negotiation()

        from .telopt import DO, TTYPE
        self.writer.iac(DO, TTYPE)

    def begin_advanced_negotiation(self):
        """
        Begin advanced negotiation.

        Deriving implementations should always call
        ``super().begin_advanced_negotiation()``.
        """
        from .telopt import DO, NEW_ENVIRON, NAWS, WILL, SGA, ECHO
        super().begin_advanced_negotiation()
        self.writer.iac(DO, NEW_ENVIRON)
        self.writer.iac(DO, NAWS)
        self.writer.iac(WILL, SGA)
        self.writer.iac(WILL, ECHO)

    def on_naws(self, width, height):
        """
        Callback receives NAWS response, rfc-1073_.

        :param int width: screen size, by number of columns.
        :param int height: screen size, by number of rows.
        """
        self._extra.update({'cols': width, 'rows': height})

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


@asyncio.coroutine
def create_server(
    protocol_factory=None, host=None, port=23, *,
    loop=None, log=None, encoding='utf8', encoding_error='replace',
    force_binary=False, term='unknown', cols=80, rows=25, timeout=300,
    shell=None, reader_factory=None, writer_factory=None,
    waiter_closed=None, waiter_connected=None
):
    """
    Create a Telnet Server

    [...]
    """

    if protocol_factory is None:
        protocol_factory = TelnetServer

    if loop is None:
        loop = asyncio.get_event_loop()

    def on_connect():
        return protocol_factory(
            loop=loop, log=log, encoding='utf8', encoding_error='replace',
            force_binary=False, term='unknown', cols=80, rows=25, timeout=300,
            shell=shell, reader_factory=reader_factory,
            writer_factory=writer_factory, waiter_closed=waiter_closed,
            waiter_connected=waiter_connected)

    return (yield from loop.create_server(on_connect, host, port))
