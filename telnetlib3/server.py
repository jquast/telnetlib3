"""
The ``main`` function here is wired to the command line tool by name
telnetlib3-server.  If this server's PID receives the SIGTERM signal, it
attempts to shutdown gracefully.

The :class:`TelnetServer` class negotiates a character-at-a-time (WILL-SGA,
WILL-ECHO) session with support for negotiation about window size, environment
variables, terminal type name, and to automatically close connections clients
after an idle period.
"""

# std imports
import collections
import argparse
import asyncio
import logging
import signal

# local
from . import server_base
from . import accessories
from .telopt import name_commands

__all__ = ("TelnetServer", "create_server", "run_server", "parse_server_args")

CONFIG = collections.namedtuple(
    "CONFIG",
    [
        "host",
        "port",
        "loglevel",
        "logfile",
        "logfmt",
        "shell",
        "encoding",
        "force_binary",
        "timeout",
        "connect_maxwait",
    ],
)(
    host="localhost",
    port=6023,
    loglevel="info",
    logfile=None,
    logfmt=accessories._DEFAULT_LOGFMT,
    shell=accessories.function_lookup("telnetlib3.telnet_server_shell"),
    encoding="utf8",
    force_binary=False,
    timeout=300,
    connect_maxwait=4.0,
)
logger = logging.getLogger("telnetlib3.server")


class TelnetServer(server_base.BaseServer):
    """Telnet Server protocol performing common negotiation."""

    #: Maximum number of cycles to seek for all terminal types.  We are seeking
    #: the repeat or cycle of a terminal table, choosing the first -- but when
    #: negotiated by MUD clients, we chose the must Unix TERM appropriate,
    TTYPE_LOOPMAX = 8

    # Derived methods from base class

    def __init__(self, term="unknown", cols=80, rows=25, timeout=300, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.waiter_encoding = asyncio.Future()
        self._tasks.append(self.waiter_encoding)
        self._ttype_count = 1
        self._timer = None
        self._extra.update(
            {
                "term": term,
                "charset": kwargs.get("encoding", ""),
                "cols": cols,
                "rows": rows,
                "timeout": timeout,
            }
        )

    def connection_made(self, transport):
        from .telopt import NAWS, NEW_ENVIRON, TSPEED, TTYPE, XDISPLOC, CHARSET

        super().connection_made(transport)

        # begin timeout timer
        self.set_timeout()

        # Wire extended rfc callbacks for responses to
        # requests of terminal attributes, environment values, etc.
        for tel_opt, callback_fn in [
            (NAWS, self.on_naws),
            (NEW_ENVIRON, self.on_environ),
            (TSPEED, self.on_tspeed),
            (TTYPE, self.on_ttype),
            (XDISPLOC, self.on_xdisploc),
            (CHARSET, self.on_charset),
        ]:
            self.writer.set_ext_callback(tel_opt, callback_fn)

        # Wire up a callbacks that return definitions for requests.
        for tel_opt, callback_fn in [
            (NEW_ENVIRON, self.on_request_environ),
            (CHARSET, self.on_request_charset),
        ]:
            self.writer.set_ext_send_callback(tel_opt, callback_fn)

    def data_received(self, data):
        self.set_timeout()
        super().data_received(data)

    def begin_negotiation(self):
        from .telopt import DO, TTYPE

        super().begin_negotiation()
        self.writer.iac(DO, TTYPE)

    def begin_advanced_negotiation(self):
        from .telopt import DO, WILL, SGA, ECHO, BINARY, NEW_ENVIRON, NAWS, CHARSET

        super().begin_advanced_negotiation()
        self.writer.iac(WILL, SGA)
        self.writer.iac(WILL, ECHO)
        self.writer.iac(WILL, BINARY)
        self.writer.iac(DO, NEW_ENVIRON)
        self.writer.iac(DO, NAWS)
        if self.default_encoding:
            # Request client capability to negotiate character set
            self.writer.iac(DO, CHARSET)

    def check_negotiation(self, final=False):
        from .telopt import TTYPE, NEW_ENVIRON, CHARSET, SB

        # Debug log to see which options are still pending
        pending = [
            (name_commands(opt), val)
            for opt, val in self.writer.pending_option.items()
            if val
        ]
        if pending:
            logger.debug("Pending options: %r", pending)

        # Check if we're waiting for important subnegotiations -- environment or charset information
        # These are critical for proper encoding determination
        waiting_for_environ = (
            SB + NEW_ENVIRON in self.writer.pending_option
            and self.writer.pending_option[SB + NEW_ENVIRON]
        )
        waiting_for_charset = (
            SB + CHARSET in self.writer.pending_option
            and self.writer.pending_option[SB + CHARSET]
        )

        if waiting_for_environ or waiting_for_charset:
            if final:
                logger.warning(
                    "Waiting for critical subnegotiation: environ=%s, charset=%s",
                    waiting_for_environ,
                    waiting_for_charset,
                )

        parent = super().check_negotiation()

        # In addition to the base class negotiation check, periodically check
        # for completion of bidirectional encoding negotiation.
        result = self._check_encoding()
        encoding = self.encoding(outgoing=True, incoming=True)

        if not self.waiter_encoding.done() and result:
            logger.debug("encoding complete: {0!r}".format(encoding))
            self.waiter_encoding.set_result(result)

        elif (
            not self.waiter_encoding.done()
            and self.writer.remote_option.get(TTYPE) is False
        ):
            # if the remote end doesn't support TTYPE, which is agreed upon
            # to continue towards advanced negotiation of CHARSET, we assume
            # the distant end would not support it, declaring encoding failed.
            logger.debug(
                "encoding failed after {0:1.2f}s: {1}, remote_option[TTYPE]={2}, result={3}".format(
                    self.duration,
                    encoding,
                    self.writer.remote_option.get(TTYPE),
                    result,
                )
            )
            self.waiter_encoding.set_result(result)  # False
            return parent

        elif not self.waiter_encoding.done() and final:
            logger.debug(
                "encoding failed after {0:1.2f}s: {1}".format(self.duration, encoding)
            )
            self.waiter_encoding.set_result(result)  # False
            return parent

        # Now consider the pending critical options for the final return value
        # This ensures we don't complete negotiation until env/charset are done
        if waiting_for_environ or waiting_for_charset:
            return False

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
            ``BINARY`` :rfc:`856` has been negotiated for the direction
            indicated or :attr`force_binary` is set ``True``.
        :rtype: str
        """
        if not (outgoing or incoming):
            raise TypeError(
                "encoding arguments 'outgoing' and 'incoming' "
                "are required: toggle at least one."
            )

        # may we encode in the direction indicated?
        _outgoing_only = outgoing and not incoming
        _incoming_only = not outgoing and incoming
        _bidirectional = outgoing and incoming
        may_encode = self.force_binary or (
            (_outgoing_only and self.writer.outbinary)
            or (_incoming_only and self.writer.inbinary)
            or (_bidirectional and self.writer.outbinary and self.writer.inbinary)
        )

        if may_encode:
            # prefer 'LANG' environment variable forwarded by client, if any.
            # for modern systems, this is the preferred method of encoding
            # negotiation.
            _lang = self.get_extra_info("LANG", "")
            if _lang and _lang != "C":
                return accessories.encoding_from_lang(_lang)

            # otherwise, less common CHARSET negotiation may be found in many
            # East-Asia BBS and Western MUD systems.
            return self.get_extra_info("charset") or self.default_encoding
        return "US-ASCII"

    def set_timeout(self, duration=-1):
        """
        Restart or unset timeout for client.

        :param int duration: When specified as a positive integer,
            schedules Future for callback of :meth:`on_timeout`.  When ``-1``,
            the value of ``self.get_extra_info('timeout')`` is used.  When
            non-True, it is canceled.
        """
        if duration == -1:
            duration = self.get_extra_info("timeout")
        if self._timer is not None:
            if self._timer in self._tasks:
                self._tasks.remove(self._timer)
            self._timer.cancel()
        if duration:
            loop = asyncio.get_event_loop()
            self._timer = loop.call_later(duration, self.on_timeout)
            self._tasks.append(self._timer)
        self._extra["timeout"] = duration

    # Callback methods

    def on_timeout(self):
        """
        Callback received on session timeout.

        Default implementation writes "Timeout." bound by CRLF and closes.

        This can be disabled by calling :meth:`set_timeout` with
        ``duration` value of ``0``.
        """
        logger.debug("Timeout after {self.idle:1.2f}s".format(self=self))
        # try to write timeout using encoding,
        try:
            self.writer.write("\r\nTimeout.\r\n")
        except TypeError:
            # unless server was started with encoding=False, we must send as binary!
            self.writer.write(b"\r\nTimeout.\r\n")
        self.timeout_connection()

    def on_naws(self, rows, cols):
        """
        Callback receives NAWS response, :rfc:`1073`.

        :param int rows: screen size, by number of cells in height.
        :param int cols: screen size, by number of cells in width.
        """
        self._extra.update({"rows": rows, "cols": cols})

    def on_request_environ(self):
        """
        Definition for NEW_ENVIRON request of client, :rfc:`1572`.

        This method is a callback from :meth:`~.TelnetWriter.request_environ`,
        first entered on receipt of (WILL, NEW_ENVIRON) by server.  The return
        value *defines the request made to the client* for environment values.

        :rtype list: a list of unicode character strings of US-ASCII
            characters, indicating the environment keys the server requests
            of the client.  If this list contains the special byte constants,
            ``USERVAR`` or ``VAR``, the client is allowed to volunteer any
            other additional user or system values.

            Any empty return value indicates that no request should be made.

        The default return value is::

            ['LANG', 'TERM', 'COLUMNS', 'LINES', 'DISPLAY', 'COLORTERM',
             VAR, USERVAR, 'COLORTERM']
        """
        from .telopt import VAR, USERVAR

        return [
            "LANG",
            "TERM",
            "COLUMNS",
            "LINES",
            "DISPLAY",
            "COLORTERM",
            VAR,
            USERVAR,
        ]

    def on_environ(self, mapping):
        """Callback receives NEW_ENVIRON response, :rfc:`1572`."""
        # A well-formed client responds with empty values for variables to
        # mean "no value".  They might have it, they just may not wish to
        # divulge that information.  We pop these keys as a side effect in
        # the result statement of the following list comprehension.
        no_value = [
            mapping.pop(key) or key for key, val in list(mapping.items()) if not val
        ]

        # because we are working with "untrusted input", we make one fair
        # distinction: all keys received by NEW_ENVIRON are in uppercase.
        # this ensures a client may not override trusted values such as
        # 'peer'.
        u_mapping = {key.upper(): val for key, val in list(mapping.items())}

        logger.debug("on_environ received: {0!r}".format(u_mapping))

        self._extra.update(u_mapping)

    def on_request_charset(self):
        """
        Definition for CHARSET request by client, :rfc:`2066`.

        This method is a callback from :meth:`~.TelnetWriter.request_charset`,
        first entered on receipt of (WILL, CHARSET) by server.  The return
        value *defines the request made to the client* for encodings.

        :rtype list: a list of unicode character strings of US-ASCII
            characters, indicating the encodings offered by the server in
            its preferred order.

            Any empty return value indicates that no encodings are offered.

        The default return value includes common encodings for both Western and Eastern scripts::

            ['UTF-8', 'UTF-16', 'LATIN1', 'US-ASCII', 'CP1252', 'ISO-8859-15', 'CP437',
             'SHIFT_JIS', 'CP932', 'BIG5', 'CP950', 'GBK', 'GB2312', 'CP936', 'EUC-KR', 'CP949']
        """
        return [
            "UTF-8",  # Most common modern encoding
            "UTF-16",  # Common Unicode encoding
            "LATIN1",  # ISO-8859-1, Western European
            "CP1252",  # Windows Western European
            "ISO-8859-15",  # Updated Western European (includes Euro symbol)
            "CP437",  # PC-DOS / US telnet BBS systems
            # Eastern encodings
            "SHIFT_JIS",  # Japan
            "CP932",  # Japan (Windows code page)
            "BIG5",  # Taiwan/Hong Kong
            "CP950",  # Taiwan/Hong Kong (Windows code page)
            "GBK",  # Mainland China
            "GB2312",  # Mainland China
            "CP936",  # Mainland China (Windows code page)
            "EUC-KR",  # Korea
            "CP949",  # Korea (Windows code page)
            "US-ASCII",  # Basic ASCII
        ]

    def on_charset(self, charset):
        """Callback for CHARSET response, :rfc:`2066`."""
        self._extra["charset"] = charset

    def on_tspeed(self, rx, tx):
        """Callback for TSPEED response, :rfc:`1079`."""
        self._extra["tspeed"] = "{0},{1}".format(rx, tx)

    def on_ttype(self, ttype):
        """Callback for TTYPE response, :rfc:`930`."""
        # TTYPE may be requested multiple times, we honor this system and
        # attempt to cause the client to cycle, as their first response may
        # not be their most significant. All responses held as 'ttype{n}',
        # where {n} is their serial response order number.
        #
        # The most recently received terminal type by the server is
        # assumed TERM by this implementation, even when unsolicited.
        key = "ttype{}".format(self._ttype_count)
        self._extra[key] = ttype
        if ttype:
            self._extra["TERM"] = ttype

        _lastval = self.get_extra_info("ttype{0}".format(self._ttype_count - 1))

        if key != "ttype1" and ttype == self.get_extra_info("ttype1", None):
            # cycle has looped, stop
            logger.debug("ttype cycle stop at {0}: {1}, looped.".format(key, ttype))

        elif not ttype or self._ttype_count > self.TTYPE_LOOPMAX:
            # empty reply string or too many responses!
            logger.warning("ttype cycle stop at {0}: {1}.".format(key, ttype))

        elif self._ttype_count == 3 and ttype.upper().startswith("MTTS "):
            val = self.get_extra_info("ttype2")
            logger.debug(
                "ttype cycle stop at {0}: {1}, using {2} from ttype2.".format(
                    key, ttype, val
                )
            )
            self._extra["TERM"] = val

        elif ttype == _lastval:
            logger.debug("ttype cycle stop at {0}: {1}, repeated.".format(key, ttype))

        else:
            logger.debug("ttype cycle cont at {0}: {1}.".format(key, ttype))
            self._ttype_count += 1
            self.writer.request_ttype()

    def on_xdisploc(self, xdisploc):
        """Callback for XDISPLOC response, :rfc:`1096`."""
        self._extra["xdisploc"] = xdisploc

    # private methods

    def _check_encoding(self):
        # Periodically check for completion of ``waiter_encoding``.
        from .telopt import DO, BINARY, CHARSET, SB

        # Check if we need to request client to use BINARY mode for client-to-server communication
        if (
            self.writer.outbinary
            and not self.writer.inbinary
            and not (DO + BINARY) in self.writer.pending_option
        ):
            logger.debug("BINARY in: direction request.")
            self.writer.iac(DO, BINARY)
            return False

        # Check if CHARSET is enabled but no REQUEST has been sent yet
        if (
            self.writer.remote_option.enabled(CHARSET)
            and self.writer.local_option.enabled(CHARSET)
            and not (SB + CHARSET) in self.writer.pending_option
        ):
            logger.debug("Initiating CHARSET REQUEST after capabilities negotiation")
            self.writer.request_charset()

        # are we able to negotiate BINARY bidirectionally?
        # or, is force_binary=True ?
        return (self.writer.outbinary and self.writer.inbinary) or self.force_binary


async def create_server(host=None, port=23, protocol_factory=TelnetServer, **kwds):
    """
    Create a TCP Telnet server.

    :param str host: The host parameter can be a string, in that case the TCP
        server is bound to host and port. The host parameter can also be a
        sequence of strings, and in that case the TCP server is bound to all
        hosts of the sequence.
    :param int port: listen port for TCP Server.
    :param server_base.BaseServer protocol_factory: An alternate protocol
        factory for the server, when unspecified, :class:`TelnetServer` is
        used.
    :param Callable shell: An async function that is called after
        negotiation completes, receiving arguments ``(reader, writer)``.
        Default is :func:`~.telnet_server_shell`.  The reader is a
        :class:`~.TelnetReader` instance, the writer is a
        :class:`~.TelnetWriter` instance.
    :param str encoding: The default assumed encoding, or ``False`` to disable
        unicode support.  Encoding may be negotiation to another value by
        the client through NEW_ENVIRON :rfc:`1572` by sending environment value
        of ``LANG``, or by any legal value for CHARSET :rfc:`2066` negotiation.

        The server's attached ``reader, writer`` streams accept and return
        unicode, or natural strings, "hello world", unless this value explicitly
        set ``False``.  In that case, the attached streams interfaces are
        bytes-only, b"hello world".
    :param str encoding_errors: Same meaning as :meth:`codecs.Codec.encode`.
        Default value is ``strict``.
    :param bool force_binary: When ``True``, the encoding specified is
        used for both directions even when BINARY mode, :rfc:`856`, is not
        negotiated for the direction specified.  This parameter has no effect
        when ``encoding=False``.

        Note that when combined with a default ``encoding``, use of this option
        may prematurely cause data transmitted in the default encoding immediately
        on-connect, before a "smart" telnet client or server can negotiate a
        different one.

        In most cases, so long as the initial login banner/etc is US-ASCII, this
        may be no problem at all. If an encoding is assumed, as in many MUD and
        BBS systems, the combination of ``force_binary`` with a default
        ``encoding`` is often preferred.
    :param str term: Value returned for ``writer.get_extra_info('term')``
        until negotiated by TTYPE :rfc:`930`, or NAWS :rfc:`1572`.  Default value
        is ``'unknown'``.
    :param int cols: Value returned for ``writer.get_extra_info('cols')``
        until negotiated by NAWS :rfc:`1572`. Default value is 80 columns.
    :param int rows: Value returned for ``writer.get_extra_info('rows')``
        until negotiated by NAWS :rfc:`1572`. Default value is 25 rows.
    :param int timeout: Causes clients to disconnect if idle for this duration,
        in seconds.  This ensures resources are freed on busy servers.  When
        explicitly set to ``False``, clients will not be disconnected for
        timeout. Default value is 300 seconds (5 minutes).
    :param float connect_maxwait: If the remote end is not complaint, or
        otherwise confused by our demands, the shell continues anyway after the
        greater of this value has elapsed.  A client that is not answering
        option negotiation will delay the start of the shell by this amount.
    :param int limit: The buffer limit for the reader stream.

    :return asyncio.Server: The return value is the same as
        :meth:`asyncio.loop.create_server`, An object which can be used
        to stop the service.
    """
    protocol_factory = protocol_factory or TelnetServer
    loop = asyncio.get_event_loop()
    return await loop.create_server(lambda: protocol_factory(**kwds), host, port)


async def _sigterm_handler(server, log):
    logger.info("SIGTERM received, closing server.")

    # This signals the completion of the server.wait_closed() Future,
    # allowing the main() function to complete.
    server.close()


def parse_server_args():
    parser = argparse.ArgumentParser(
        description="Telnet protocol server",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("host", nargs="?", default=CONFIG.host, help="bind address")
    parser.add_argument(
        "port", nargs="?", type=int, default=CONFIG.port, help="bind port"
    )
    parser.add_argument("--loglevel", default=CONFIG.loglevel, help="level name")
    parser.add_argument("--logfile", default=CONFIG.logfile, help="filepath")
    parser.add_argument("--logfmt", default=CONFIG.logfmt, help="log format")
    parser.add_argument(
        "--shell",
        default=CONFIG.shell,
        type=accessories.function_lookup,
        help="module.function_name",
    )
    parser.add_argument("--encoding", default=CONFIG.encoding, help="encoding name")
    parser.add_argument(
        "--force-binary",
        action="store_true",
        default=CONFIG.force_binary,
        help="force binary transmission",
    )
    parser.add_argument(
        "--timeout", default=CONFIG.timeout, help="idle disconnect (0 disables)"
    )
    parser.add_argument(
        "--connect-maxwait",
        type=float,
        default=CONFIG.connect_maxwait,
        help="timeout for pending negotiation",
    )
    return vars(parser.parse_args())


async def run_server(
    host=CONFIG.host,
    port=CONFIG.port,
    loglevel=CONFIG.loglevel,
    logfile=CONFIG.logfile,
    logfmt=CONFIG.logfmt,
    shell=CONFIG.shell,
    encoding=CONFIG.encoding,
    force_binary=CONFIG.force_binary,
    timeout=CONFIG.timeout,
    connect_maxwait=CONFIG.connect_maxwait,
):
    """
    Program entry point for server daemon.

    This function configures a logger and creates a telnet server for the
    given keyword arguments, serving forever, completing only upon receipt of
    SIGTERM.
    """
    log = accessories.make_logger(
        name="telnetlib3.server", loglevel=loglevel, logfile=logfile, logfmt=logfmt
    )

    # log all function arguments.
    _locals = locals()
    _cfg_mapping = ", ".join(
        ("{0}={{{0}}}".format(field) for field in CONFIG._fields)
    ).format(**_locals)
    logger.debug("Server configuration: {}".format(_cfg_mapping))

    loop = asyncio.get_event_loop()

    # bind
    server = await create_server(
        host,
        port,
        shell=shell,
        encoding=encoding,
        force_binary=force_binary,
        timeout=timeout,
        connect_maxwait=connect_maxwait,
    )

    # SIGTERM cases server to gracefully stop
    loop.add_signal_handler(
        signal.SIGTERM, asyncio.ensure_future, _sigterm_handler(server, log)
    )

    logger.info("Server ready on {0}:{1}".format(host, port))

    # await completion of server stop
    try:
        await server.wait_closed()
    finally:
        # remove signal handler on stop
        loop.remove_signal_handler(signal.SIGTERM)

    logger.info("Server stop.")


def main():
    asyncio.run(run_server(**parse_server_args()))


if __name__ == "__main__":
    main()
