#!/usr/bin/env python3
"""
Telnet Client for the 'telnetlib3' python package.
"""
# std imports
import asyncio

# std imports
import argparse
import logging
import asyncio
import locale
import codecs

# local
import telnetlib3

__all__ = ('TelnetClient', 'connect')


# std imports
from . import client_base


class TelnetClient(client_base.BaseClient):
    pass


@asyncio.coroutine
def connect(host=None, port=23, *,
            client_factory=TelnetClient,
            loop=None, family=0, flags=0, local_addr=None,
            log=None, encoding='utf8', encoding_errors='strict',
            force_binary=False,
            # term='unknown', cols=80, rows=25,
            shell=None, waiter_closed=None, waiter_connected=None):

    def connection_factory():
        """Return an SSH client connection handler"""

        return TelnetClient(
            log=log, encoding=encoding, encoding_errors=encoding_errors,
            force_binary=force_binary,
            # term=term, cols=cols, rows=rows,
            shell=shell, waiter_closed=waiter_closed,
            waiter_connected=waiter_connected)

    client_factory = client_factory or TelnetClient
    loop = loop or asyncio.get_event_loop()

    transport, protocol = yield from loop.create_connection(
        connection_factory, host, port,
        family=family, flags=flags, local_addr=local_addr)

    yield from protocol.waiter_connected

    return protocol.reader, protocol.writer



#@asyncio.coroutine
#def open_connection(
#    host, port, *, loop=None, protocol_factory=None,
#):
#    """ XXX """
#    loop = loop or asyncio.get_event_loop()
#    protocol_factory = protocol_factory or TelnetClient
#    protocol, transport = yield from loop.create_connection(
#        protocol_factory, host, port)
#    return protocol.reader, protocol.writer
#
##    protocol_factory = protocol_factory or TelnetClient
##    transport, protocol = yield from loop.create_connection(
#        protocol_factory=lambda: protocol, host, port, )
#    #reader = StreamReader(limit=limit, loop=loop)
#    #protocol = StreamReaderProtocol(reader, loop=loop)
#    #transport, _ = yield from loop.create_connection(
#    #    lambda: protocol, host, port, **kwds)
#    #writer = StreamWriter(transport, protocol, reader, loop)
#    #return reader, writer
#
#
#
#
#    def set_stream_callbacks(self):
#        """
#        Initialize callbacks for Telnet negotiation responses.
#
#        Sets callbacks for methods class :py:method:`self.send_ttype`,
#        :py:method:`self.send_ttype`, :py:method:`self.send_tspeed`,
#        :py:method:`self.send_xdisploc`, :py:method:`self.send_env`,
#        :py:method:`self.send_naws`, and :py:method:`self.send_charset`,
#        to the appropriate Telnet Option Negotiation byte values.
#        """
#        from telnetlib3.telopt import TTYPE, TSPEED, XDISPLOC, NEW_ENVIRON
#        from telnetlib3.telopt import CHARSET, NAWS
#
#        # wire extended rfc callbacks for terminal atributes, etc.
#        for (opt, func) in (
#                (TTYPE, self.send_ttype),
#                (TSPEED, self.send_tspeed),
#                (XDISPLOC, self.send_xdisploc),
#                (NEW_ENVIRON, self.send_env),
#                (NAWS, self.send_naws),
#                (CHARSET, self.send_charset),
#                ):
#            self.writer.set_ext_send_callback(opt, func)
#
#    def encoding(self, outgoing=False, incoming=False):
#        """ Client-preferred input or output encoding of BINARY data.
#
#        Always returns 'ascii' for the direction(s) indicated unless
#        :py:attr:`self.inbinary` or :py:attr:`self.outbinary` is True,
#        Returnning the session-negotiated value of CHARSET(rfc2066)
#        or encoding indicated by :py:attr:`self.encoding`.
#
#        As BINARY(rfc856) must be negotiated bi-directionally, both or
#        at least one direction should always be indicated, which may
#        return different values -- it is entirely possible to receive
#        only 'ascii'-encoded data but negotiate the allowance to transmit
#        'utf8'.
#        """
#        assert outgoing or incoming
#        return (self.env.get('CHARSET', self.default_encoding)
#                if (outgoing and not incoming and self.outbinary) or (
#                    not outgoing and incoming and self.inbinary) or (
#                    outgoing and incoming and self.outbinary and self.inbinary
#                    ) else 'ascii')
#
#    def check_encoding_negotiation(self):
#        """ Callback to check on-connect option negotiation for encoding.
#
#        Schedules itself for continual callback until encoding negotiation
#        with server is considered final, firing
#        :py:meth:`after_encoding_negotiation` when complete.  Encoding
#        negotiation is considered final when BINARY mode has been negotiated
#        bi-directionally.
#        """
#        from .telopt import DO, BINARY
#        if self._closing:
#            return
#
#        # encoding negotiation is complete
#        if self.outbinary and self.inbinary:
#            self.log.debug('negotiated outbinary and inbinary with client.')
#
#        # if (WILL, BINARY) requested by begin_negotiation() is answered in
#        # the affirmitive, then request (DO, BINARY) to ensure bi-directional
#        # transfer of non-ascii characters.
#        elif self.outbinary and not self.inbinary and (
#                not (DO, BINARY,) in self.writer.pending_option):
#            self.log.debug('outbinary=True, requesting inbinary.')
#            self.writer.iac(DO, BINARY)
#            self._loop.call_later(self.CONNECT_DEFERRED,
#                                  self.check_encoding_negotiation)
#
#        elif self.duration > self.CONNECT_MAXWAIT:
#            # Perhaps some IAC interpreting servers do not differentiate
#            # 'local' from 'remote' options -- they are treated equivalently.
#            self.log.debug('failed to negotiate both outbinary and inbinary.')
#
#        else:
#            self._loop.call_later(self.CONNECT_DEFERRED,
#                                  self.check_encoding_negotiation)
#
#
