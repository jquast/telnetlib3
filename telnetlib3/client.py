#!/usr/bin/env python3
"""
Telnet Client API for the 'telnetlib3' python package.
"""
# std imports
import argparse
import asyncio
import logging
import codecs
import struct
import sys
import os

# local imports
from . import accessories
from . import client_base

__all__ = ('TelnetClient', 'open_connection', 'start_client')


class TelnetClient(client_base.BaseClient):
    #: On :meth:`send_env`, the value of 'LANG' will be 'C' for binary
    #: transmission.  When encoding is specified (utf8 by default), the LANG
    #: variable must also contain a locale, this value is used, providing a
    #: full default LANG value of 'en_US.utf8'
    DEFAULT_LOCALE = 'en_US'

    def __init__(self, term='unknown', cols=80, rows=25,
                 tspeed=(38400, 38400), xdisploc='',
                 *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._extra.update({
            'charset': kwargs['encoding'] or '',
            # for our purposes, we only send the second part (encoding) of our
            # 'lang' variable, CHARSET negotiation does not provide locale
            # negotiation; this is better left to the real LANG variable
            # negotiated as-is by send_env().
            #
            # So which locale should we represent? Rather than using the
            # locale.getpreferredencoding() method, we provide a deterministic
            # class value DEFAULT_LOCALE (en_US), derive and modify as needed.
            'lang': ('C' if not kwargs['encoding'] else
                     self.DEFAULT_LOCALE + '.' + kwargs['encoding']),
            'cols': cols,
            'rows': rows,
            'term': term,
            'tspeed': '{},{}'.format(*tspeed),
            'xdisploc': xdisploc,
        })


    def connection_made(self, transport):
        from telnetlib3.telopt import TTYPE, TSPEED, XDISPLOC, NEW_ENVIRON
        from telnetlib3.telopt import CHARSET, NAWS
        super().connection_made(transport)

        # Wire extended rfc callbacks for requests of
        # terminal attributes, environment values, etc.
        for (opt, func) in (
                (TTYPE, self.send_ttype),
                (TSPEED, self.send_tspeed),
                (XDISPLOC, self.send_xdisploc),
                (NEW_ENVIRON, self.send_env),
                (NAWS, self.send_naws),
                (CHARSET, self.send_charset),
                ):
            self.writer.set_ext_send_callback(opt, func)

    def send_ttype(self):
        """Callback for responding to TTYPE requests."""
        return self._extra['term']

    def send_tspeed(self):
        """Callback for responding to TSPEED requests."""
        return tuple(map(int, self._extra['tspeed'].split(',')))

    def send_xdisploc(self):
        """Callback for responding to XDISPLOC requests."""
        return self._extra['xdisploc']

    def send_env(self, keys):
        """ Callback for responding to NEW_ENVIRON requests.

        :param keys: Values are requested for the keys specified. When empty,
           all environment values that wish to be volunteered should be
           returned.
        :returns: dictionary of environment values requested, or an
            empty string for keys not available. A return value must be
            given for each key requested.
        :rtype: dict[(key, value), ..]
        """
        env = {
            'LANG': self._extra['lang'],
            'TERM': self._extra['term'],
            'DISPLAY': self._extra['xdisploc'],
            'LINES': self._extra['rows'],
            'COLUMNS': self._extra['cols'],
        }
        return {key: env.get(key, '') for key in keys} or env

    def send_charset(self, offered):
        """ Callback for responding to CHARSET requests.

        Receives a list of character encodings offered by the server
        as ``offered`` such as ``('LATIN-1', 'UTF-8')``, for which the
        client may return a value agreed to use, or None to disagree to
        any available offers.  Server offerings may be encodings or
        codepages.

        The default implementation selects any matching encoding that
        python is capable of using, preferring any that matches
        :py:attr:`self.encoding` if matched in the offered list.

        :param offered: list of CHARSET options offered by server.
        :returns: character encoding agreed to be used.
        :rtype: str or None.
        """
        selected = ''
        for offer in offered:
                try:
                    codec = codecs.lookup(offer)
                except LookupError as err:
                    self.log.info('LookupError: {}'.format(err))
                else:
                    if (codec.name == self.default_encoding or not selected):
                        self._extra['charset'] = codec.name
                        self._extra['lang'] = (
                            self.DEFAULT_LOCALE + '.' + codec.name)
                        selected = offer
        if selected:
            self.log.debug('encoding negotiated: {0}'.format(selected))
        else:
            self.log.warn('No suitable encoding offered by server: {!r}.'
                          .format(offered))
        return selected

    def send_naws(self):
        """ Callback for responding to NAWS requests.

        :rtype: (int, int)
        :returns: client window size as (rows, columns).
        """
        return (self._extra['rows'], self._extra['cols'])

    def encoding(self, outgoing=None, incoming=None):
        """
        Return encoding for the given stream direction.

        :param bool outgoing: Whether the return value is suitable for
            encoding bytes for transmission to server.
        :param bool incoming: Whether the return value is suitable for
            decoding bytes received by the client.
        :raises TypeError: when a direction argument, either ``outgoing``
            or ``incoming``, was not set ``True``.
        :returns: ``'US-ASCII'`` for the directions indicated, unless
            ``BINARY`` rfc-856_ has been negotiated for the direction
            indicated or :attr`force_binary` is set ``True``.
        :rtype: str

        Value resolution order (first-matching):

        - value set by :meth:`set_encoding`.
        - value of :meth:`get_extra_info` using key argument, ``lang``.
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
            # The 'charset' value, initialized using keyword argument
            # default_encoding, may be re-negotiated later.  Only the CHARSET
            # negotiation method allows the server to select an encoding, so
            # this value is reflected here by a single return statement.
            return self._extra['charset']
        return 'US-ASCII'

class TelnetTerminalClient(TelnetClient):
    def send_naws(self):
        return self._winsize()

    @staticmethod
    def _winsize():
        try:
            import fcntl
            import termios
            fmt = 'hhhh'
            buf = '\x00' * struct.calcsize(fmt)
            val = fcntl.ioctl(sys.stdin.fileno(), termios.TIOCGWINSZ, buf)
            rows, cols, _, _ = struct.unpack(fmt, val)
            return rows, cols
        except (ImportError, IOError):
            return (int(os.environ.get('LINES', 25)),
                    int(os.environ.get('COLUMNS', 80)))

    def send_env(self, keys):
        env = super().send_env(keys)
        env['LINES'], env['COLUMNS'] = self._winsize()
        return env


@asyncio.coroutine
def open_connection(host=None, port=23, *, client_factory=None, loop=None,
                    family=0, flags=0, local_addr=None, log=None,
                    encoding='utf8', encoding_errors='strict',
                    force_binary=False, term='unknown', cols=80, rows=25,
                    tspeed=(38400, 38400), xdisploc='', shell=None,
                    connect_minwait=1.0, connect_maxwait=4.0,
                    waiter_closed=None, waiter_connected=None):
    """
    :param client_base.BaseClient client_factory: TelnetClient class instance,
        when ``None``, :class:`TelnetTerminalClient` is used when *stdin* is
        attached to a terminal, :class:`TelnetClient` otherwise.
    :param float connect_minwait: The client allows any telnet negotiations to
        be demanded by the server within this period of time before the shell
        begins.  These demands are usually made immediately on connection.
        A server that does not make any telnet demands, such as a non-telnet
        server, will delay the shell for this amount of time.
    :param float connect_maxwait: If the remote end is not complaint, or
        otherwise confused by our demands, the shell continues anyway after the
        greater of this value or ``connect_minwait``.
    """
    log = log or logging.getLogger(__name__)
    loop = loop or asyncio.get_event_loop()

    if client_factory is None:
        client_factory = TelnetClient
        if sys.platform != 'win32' and sys.stdin.isatty():
            client_factory = TelnetTerminalClient

    def connection_factory():
        return client_factory(
            log=log, encoding=encoding, encoding_errors=encoding_errors,
            force_binary=force_binary, term=term, cols=cols, rows=rows,
            tspeed=tspeed, xdisploc=xdisploc, shell=shell,
            connect_minwait=connect_minwait, connect_maxwait=connect_maxwait,
            waiter_closed=waiter_closed, waiter_connected=waiter_connected)

    transport, protocol = yield from loop.create_connection(
        connection_factory, host, port,
        family=family, flags=flags, local_addr=local_addr)

    yield from protocol.waiter_connected

    return protocol.reader, protocol.writer


@asyncio.coroutine
def start_client(host, port, log=None, **kwds):
    reader, writer = yield from open_connection(
        host=host, port=port, log=log, **kwds)
    return reader, writer


def main():
    """ Command-line tool telnetlib3-client entry point via setuptools."""
    kwargs = _transform_args(_get_argument_parser().parse_args())
    config_msg = 'Client configuration: ' + accessories.repr_mapping(kwargs)

    loglevel = kwargs.pop('loglevel')
    logfile = kwargs.pop('logfile')
    host = kwargs.pop('host')
    port = kwargs.pop('port')

    log = accessories.make_logger(loglevel=loglevel, logfile=logfile)
    log.debug(config_msg)

    loop = asyncio.get_event_loop()

    reader, writer = loop.run_until_complete(
        start_client(host, port, log, **kwargs))

    loop.run_until_complete(writer.protocol.waiter_closed)


def _get_argument_parser():
    parser = argparse.ArgumentParser(
        description="Telnet protocol client",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument('host', action='store',
                        help='hostname')
    parser.add_argument('port', nargs='?', default=23, type=int,
                        help='port number')
    parser.add_argument('--term', default=os.environ.get('TERM', 'unknown'),
                        help='terminal type')
    parser.add_argument('--loglevel', default='warn',
                        help='log level')
    parser.add_argument('--logfile',
                        help='filepath')
    parser.add_argument('--shell', default='telnetlib3.telnet_client_shell',
                        help='module.function_name')
    parser.add_argument('--encoding', default='utf8',
                        help='encoding name')
    parser.add_argument('--force-binary', action='store_true',
                        help='force encoding')
    parser.add_argument('--connect-minwait', default=1.0, type=float,
                        help='shell delay for negotiation')
    parser.add_argument('--connect-maxwait', default=4.0, type=float,
                        help='timeout for pending negotiation')
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
        'loglevel': args.loglevel,
        'logfile': args.logfile,
        'encoding': args.encoding,
        'shell': shell_function,
        'term': args.term,
        'force_binary': args.force_binary,
        'connect_minwait': args.connect_minwait,
    }

