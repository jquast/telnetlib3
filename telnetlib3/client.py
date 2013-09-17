#!/usr/bin/env python3
import collections
import datetime
import argparse
import logging
import codecs
import sys

from telnetlib3 import tulip
from telnetlib3.telopt import TelnetStream

__all__ = ('TelnetClient',)

class ConsoleStream():
    def __init__(self, client, log=logging, stream_out=None, stream_in=None):
        #: TelnetClient instance associated with console
        self.client = client
        self.log = log
        self.stream_out = (stream_out if stream_out is not None
                            else sys.__stdout__)
        self.stream_in = (stream_in if stream_in is not None
                            else sys.__stdin__)

        #: codecs.IncrementalDecoder for current CHARSET
        self.decoder = None

        #: default encoding 'errors' argument
        self.encoding_errors = 'replace'

    def write(self, string, errors=None):
        """ Write string to output using preferred encoding.
        """
        errors = errors if errors is not None else self.encoding_errors
        assert isinstance(string, str), string
        self.stream_out.write(self.encode(string, errors))

    @property
    def will_echo(self):
        """ Returns wether to expect the server to display our input; if
            False, it is our own responsibility to write a copy to screen.
        """
        from telopt import ECHO
        return self.client.stream.remote_option.enabled(ECHO)

    def decode(self, input, final=False):
        """ Decode input string using preferred encoding.
        """
        enc = self.client.encoding(incoming=True)
        if (self.decoder is None or enc != self.decoder._encoding):
                self.decoder = codecs.getincrementaldecoder(enc)(
                        errors=self.encoding_errors)
                self.decoder._encoding = enc
        return self.decoder.decode(input, final)

    def feed_byte(self, byte):
        """ Receive byte from telnet server, display to console """
        ucs = self.decode(byte)
        if ucs is not None:
            self.stream_out.write(ucs)


    def can_write(self, ucs):
        """ Returns True if transport can receive ``ucs`` as a single-cell,
            carriage-forwarding character, such as 'x' or ' '. Values outside
            of 7-bit NVT ASCII range may only be written if server option
            ``outbinary`` is True.

            Otherwise False indicates that a write of this unicode character
            would be an encoding error on the transport (may crash or corrupt
            client screen).
        """
        return ord(ucs) > 31 and (ord(ucs) < 127 or self.client.outbinary)

    def encode(self, buf, errors=None):
        """ Encode byte buffer using client-preferred encoding.

            If ``outbinary`` is not negotiated, ucs must be made of strictly
            7-bit ascii characters (valued less than 128), and any values
            outside of this range will be replaced with a python-like
            representation.
        """
        errors = errors if errors is not None else self.encoding_errors
        return bytes(buf, self.client.encoding(outgoing=True), errors)

    def __str__(self):
        """ Returns string describing state of stream encoding.
        """
        encoding = '{}{}'.format(
                self.client.encoding(incoming=True), '' if
                self.client.encoding(outgoing=True)
                == self.client.encoding(incoming=True) else ' in, {} out'
                .format(self.client.encoding(outgoing=True)))
        return encoding



class TelnetClient(tulip.protocols.Protocol):
    #: mininum on-connect time to wait for server-initiated negotiation options
    CONNECT_MINWAIT = 2.00
    #: maximum on-connect time to wait for server-initiated negotiation options
    #  before negotiation is considered 'final'.
    CONNECT_MAXWAIT = 6.00
    #: timer length for check_negotiation re-scheduling
    CONNECT_DEFERED = 0.2

    #: default client environment variables,
    default_env = {
            'COLUMNS': '80',
            'LINES': '24',
            'USER': 'unknown',
            'TERM': 'unknown',
            'CHARSET': 'ascii',
            }

    def __init__(self, shell=ConsoleStream, stream=TelnetStream,
            encoding='utf8', log=logging):
        self.log = log
        self._shell_factory = shell
        self._stream_factory = stream
        self._default_encoding = encoding

        #: session environment as S.env['key'], defaults empty string value
        self._client_env = collections.defaultdict(str, **self.default_env)

        #: toggled when transport is shutting down
        self._closing = False

        #: datetime of last byte received
        self._last_received = None

        #: datetime of connection made
        self._connected = None

        self._negotiation = tulip.Future()
        self._negotiation.add_done_callback(self.after_negotiation)

    def __str__(self):
        """ XXX Returns string suitable for status of server session.
        """
        return describe_connection(self)

    def connection_made(self, transport):
        """ Begin a new telnet client connection.

            A ``TelnetStream`` instance is created for reading on
            the transport as ``stream``, and various IAC, SLC.

            ``begin_negotiation()`` is fired after connection
            is registered.
        """
        self.transport = transport
        self.stream = self._stream_factory(
                transport=transport, client=True, log=self.log)
        self.shell = self._shell_factory(client=self, log=self.log)
        self.set_environment()
        self._last_received = datetime.datetime.now()
        self._connected = datetime.datetime.now()

        loop = tulip.get_event_loop()

        # begin connect-time negotiation
        loop.call_soon(self.begin_negotiation)

    @property
    def env(self):
        """ Returns hash of session environment values
        """
        return self._client_env

    @property
    def connected(self):
        """ Returns datetime connection was made.
        """
        return self._connected

    @property
    def inbinary(self):
        """ Returns True if server status ``inbinary`` is True.
        """
        from telnetlib3.telopt import BINARY
        # character values above 127 should not be expected to be read
        # inband from the transport unless inbinary is set True.
        return self.stream.remote_option.enabled(BINARY)

    @property
    def outbinary(self):
        """ Returns True if server status ``outbinary`` is True.
        """
        from telnetlib3.telopt import BINARY
        # character values above 127 should not be written to the transport
        # unless outbinary is set True.
        return self.stream.local_option.enabled(BINARY)


    def encoding(self, outgoing=False, incoming=False):
        """ Returns the session's preferred input or output encoding.

            Always 'ascii' for the direction(s) indicated unless ``inbinary``
            or ``outbinary`` has been negotiated. Then, the session value
            CHARSET is used, or the constructor kwarg ``encoding`` if CHARSET
            is not negotiated.
        """
        # of note: UTF-8 input with ascii output or vice-versa is possible.
        assert outgoing or incoming
        return (self.env.get('CHARSET', self._default_encoding)
                if (outgoing and not incoming and self.outbinary) or (
                    not outgoing and incoming and self.inbinary) or (
                    outgoing and incoming and self.outbinary and self.inbinary
                    ) else 'ascii')


    def set_environment(self):
        """ XXX This method must initialize the class attribute, ``env``
            with at least the value of 'TERM' for proper terminal handling,
            and may optionally set COLUMNS, LINES, or any other values
            wish to be explicitly exported from the client's environment
            values. Otherwise, the values of ``default_env`` are used.
        """
        import os
        term = os.environ.get('TERM', '')
        if term:
            self.env['TERM'] = term

        cols = os.environ.get('COLUMNS', '')
        if cols:
            self.env['COLUMNS'] = cols

        lines = os.environ.get('LINES', '')
        if lines:
            self.env['LINES'] = lines

    def begin_negotiation(self):
        """ XXX begin on-connect negotiation.

            A Telnet Server is expected to assert the preferred session
            options immediately after connection.
        """
        if self._closing:
            self._negotiation.cancel()
            return

        tulip.get_event_loop().call_soon(self.check_negotiation)

    def check_negotiation(self):
        """ XXX negotiation check-loop, schedules itself for continual callback
            until negotiation is considered final, firing ``after_negotiation``
            callback when complete.
        """
        if self._closing:
            self._negotiation.cancel()
            return
        pots = self.stream.pending_option
        if not any(pots.values()):
            if self.duration > self.CONNECT_MINWAIT:
                self._negotiation.set_result(self.stream.__repr__())
                return
        elif self.duration > self.CONNECT_MAXWAIT:
            self._negotiation.set_result(self.stream.__repr__())
            return
        loop = tulip.get_event_loop()
        loop.call_later(self.CONNECT_DEFERED, self.check_negotiation)

    def after_negotiation(self, status):
        """ XXX telnet stream option negotiation completed
        """
        self.log.info('{}.'.format(self))
        self.log.info('stream status is {}.'.format(self.stream))

    @property
    def duration(self):
        """ Returns seconds elapsed since connected to server.
        """
        return (datetime.datetime.now() - self._connected).total_seconds()


    def data_received(self, data):
        """ Process each byte as received by transport.
        """
        self.log.debug('data_received: {!r}'.format(data))
        self._last_received = datetime.datetime.now()
        for byte in (bytes([value]) for value in data):

            try:
                self.stream.feed_byte(byte)
            except (ValueError, AssertionError) as err:
                self.log.warn(err)
                continue

            if self.stream.is_oob:
                continue

            self.shell.feed_byte(byte)
            #sys.stdout.write(byte.
            #print(byte)
            #if self.stream.slc_received:
            #    self.shell.feed_slc(byte, func=self.stream.slc_received)
            #    continue


    def interrupt_received(self, cmd):
        """ XXX Callback receives telnet IAC or SLC interrupt byte.

            This is suitable for the receipt of interrupt signals,
            such as iac(AO) and SLC_AO.
        """
        from telnetlib3.telopt import name_command
        self.log.debug('interrupt_received: {}'.format(name_command(cmd)))
        #self.shell.display_prompt()

    def eof_received(self):
        self._closing = True

    def connection_lost(self, exc):
        self._closing = True
        self.log.info('{}{}'.format(self.__str__(),
            ': {}'.format(exc) if exc is not None else ''))
#        for task in (self._server_name, self._server_fqdn,
#                self._client_host, self._timeout):
#            task.cancel()

def describe_connection(client):
    return '{}{}{}'.format(
            # user [' using <terminal> ']
            '{}{} '.format(client.env['USER'],
                ' using' if client.env['TERM'] != 'unknown' else ''),
            '{} '.format(client.env['TERM'])
            if client.env['TERM'] != 'unknown' else '',
            # state,
            '{}connected from '.format(
                'dis' if client._closing else ''),
            # ip, dns
#            '{}{}'.format(
#                client.client_ip, ' ({}{})'.format(
#                    client.client_hostname.result(),
#                    ('' if server.client_ip
#                        == server.client_reverse_ip.result()
#                        else server.standout('!= {}, revdns-fail'.format(
#                            server.client_reverse_ip.result()))
#                        ) if server.client_reverse_ip.done() else '')
#                    if server.client_hostname.done() else ''),
            ' after {:0.3f}s'.format(client.duration))


ARGS = argparse.ArgumentParser(description="Connect to telnet server.")
ARGS.add_argument(
    '--host', action="store", dest='host',
    default='127.0.0.1', help='Host name')
ARGS.add_argument(
    '--port', action="store", dest='port',
    default=6023, type=int, help='Port number')
ARGS.add_argument(
    '--loglevel', action="store", dest="loglevel",
    default='info', type=str, help='Loglevel (debug,info)')

def main():
    import locale
    args = ARGS.parse_args()
    if ':' in args.host:
        args.host, port = args.host.split(':', 1)
        args.port = int(port)
    locale.setlocale(locale.LC_ALL, '')
    enc = locale.getpreferredencoding()
    log = logging.getLogger()
    log_const = args.loglevel.upper()
    assert (log_const in dir(logging)
            and isinstance(getattr(logging, log_const), int)
            ), args.loglevel
    log.setLevel(getattr(logging, log_const))
    log.debug('default_encoding is {}'.format(enc))

    loop = tulip.get_event_loop()
    task = loop.create_connection(
            lambda: TelnetClient(encoding=enc, log=log), args.host, args.port)

    socks = loop.run_until_complete(task)
    logging.info('Connecting to %s', socks[0])
    loop.run_forever()

if __name__ == '__main__':
    main()
