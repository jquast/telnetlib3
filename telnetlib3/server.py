# std imports
import logging

# local
from .server_base import BaseServer
from .server_mixins import UnicodeMixin, TimeoutServerMixin

__all__ = ('Server', 'TelnetServer')


class Server(BaseServer, UnicodeMixin, TimeoutServerMixin):
    """Telnet Server protocol performing common negotiation."""
    #: Maximum number of cycles to seek for all terminal types offered.
    TTYPE_LOOPMAX = 8

    def __init__(self, term='unknown', cols=80, rows=25,
                 reader_factory=None, writer_factory=None,
                 encoding=None, log=logging, loop=None):
        """
        :param str term: Default terminal type unless negotiated.
        :param int cols: Default terminal width.
        :param int rows: Default terminal height.
        """
        super().__init__(self,
                         reader_factory=reader_factory,
                         writer_factory=writer_factory,
                         encoding=encoding, log=log, loop=loop)
        self._extra.update({'term': term, 'cols': cols, 'rows': rows})

    def connection_made(self, transport):
        from .iac import NAWS, NEW_ENVIRON, TSPEED, TTYPE, XDISPLOC
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
        """Send ``DO TTYPE``."""
        if self._closing:
            return
        super().begin_negotiation()

        from .telopt import DO, TTYPE
        self._stream.iac(DO, TTYPE)

    def begin_advanced_negotiation(self):
        """
        Begin advanced negotiation.

        Callback method further requests advanced telnet options.  Called
        once on receipt of any ``DO`` or ``WILL`` acknowledgments received,
        indicating that the remote end is capable of negotiating further.

        Only called if sub-classing ``begin_negotiation`` method causes
        at least one negotiation option to be affirmatively acknowledged.
        """
        from .iac import DO, NEW_ENVIRON, NAWS, WILL, SGA, ECHO
        self._stream.iac(DO, NEW_ENVIRON)
        self._stream.iac(DO, NAWS)
        self._stream.iac(WILL, SGA)
        self._stream.iac(WILL, ECHO)

    def on_naws(self, width, height):
        """Callback receives NAWS response, rfc-1073_."""
        self._extra.update({'cols': str(width), 'rows': str(height)})

    def on_environ(self, mapping):
        """Callback receives NEW_ENVIRON response, rfc-1572_."""
        # A well-formed client responds with empty values for variables to
        # mean "no value".  They might have it, they just may not wish to
        # divulge that information.  We pop these keys as a side effect in
        # the result statement of the following list comprehension.
        no_value = [mapping.pop(key) or key
                    for key, val in list(mapping.items())
                    if not val]

        if no_value:
            self.log.debug('on_environ responses without value: {0}'
                           .format(' '.join(no_value)))

        # because we are working with "untrusted input", we make one fair
        # distinction: all keys received by NEW_ENVIRON are in uppercase.
        # this ensures a client may not override trusted values such as
        # 'peer'.
        u_mapping = {key.upper(): val for key, val in list(mapping.items())}
        self._extra.update(u_mapping)

        self.log.debug('on_environ received: {0!r}'.format(u_mapping))

    def on_tspeed(self, rx, tx):
        """Callback for TSPEED response, rfc-1079_."""
        self._extra['tspeed'] = '{0},{1}'.format(rx, tx)

    def on_ttype(self, ttype):
        """Callback for TTYPE response, rfc-930_."""
        # TTYPE may be requested multiple times, we honor this system and
        # attempt to cause the client to cycle, as their first response may
        # not be their most significant. All responses held as 'ttype{n}',
        # where {n} is their serial response order number.

        key = 'ttype{}'.format(self._advanced)
        self._extra[key] = ttype

        lastval = self.env['ttype{}'.format(self._advanced - 1)]

        if ttype == self.env.get('ttype0', None):
            # cycle has looped
            self.log.debug('ttype cycle {0}: {1}.'
                           .format(key, ttype))
            self._extra['TERM'] = self.get_extra_info('ttype0')

        elif (not ttype or self._advanced == self.TTYPE_LOOPMAX):
            # empty reply string, too many responses!
            self.log.warn('ttype cycle stop at {0}: {1}.'
                          .format(key, ttype))
            self._extra['TERM'] = self.get_extra_info('ttype0')

        elif (self._advanced == 2 and ttype.upper().startswith('MTTS ')):
            self.log.debug('ttype mud at {0}: {1}'
                           .format(key, ttype))
            self._extra['TERM'] = self.get_extra_info('ttype1')

        elif (ttype == lastval):
            self.log.debug('ttype repeated {0}: {1}'
                           .format(key, ttype))
            self._extra['TERM'] = ttype

        else:
            self.log.debug('ttype{} is {}, requesting another.'
                           .format(self._advanced, ttype))
            self._extra['TERM'] = ttype
            self._stream.request_ttype()
            self._advanced += 1

    def on_xdisploc(self, xdisploc):
        """Callback for XDISPLOC response, rfc-1096_."""
        self._extra['xdisploc'] = xdisploc


# TODO(1.0): mark by deprecation warning
TelnetServer = Server
