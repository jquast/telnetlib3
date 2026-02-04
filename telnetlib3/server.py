"""
Telnet server implementation with command-line interface.

The ``main`` function here is wired to the command line tool by name
telnetlib3-server. If this server's PID receives the SIGTERM signal,
it attempts to shutdown gracefully.

The :class:`TelnetServer` class negotiates a character-at-a-time (WILL-SGA,
WILL-ECHO) session with support for negotiation about window size, environment
variables, terminal type name, and to automatically close connections clients
after an idle period.
"""

# std imports
import codecs
import os
import signal
import asyncio
import logging
import argparse
from typing import Callable, Optional, NamedTuple

# local
from . import accessories, server_base
from .telopt import name_commands

# Check if PTY support is available (Unix-only modules: pty, termios, fcntl)
try:
    # std imports
    import pty  # noqa: F401 pylint:disable=unused-import
    import fcntl  # noqa: F401 pylint:disable=unused-import
    import termios  # noqa: F401 pylint:disable=unused-import

    PTY_SUPPORT = True
except ImportError:
    PTY_SUPPORT = False

__all__ = ("TelnetServer", "Server", "create_server", "run_server", "parse_server_args")


class CONFIG(NamedTuple):
    """Default configuration for the telnet server."""

    host: str = "localhost"
    port: int = 6023
    loglevel: str = "info"
    logfile: Optional[str] = None
    logfmt: str = accessories._DEFAULT_LOGFMT  # pylint: disable=protected-access
    shell: Callable = accessories.function_lookup("telnetlib3.telnet_server_shell")
    encoding: str = "utf8"
    force_binary: bool = False
    timeout: int = 300
    connect_maxwait: float = 1.5
    pty_exec: Optional[str] = None
    pty_args: Optional[str] = None
    pty_raw: bool = False
    robot_check: bool = False
    pty_fork_limit: int = 0
    status_interval: int = 20


# Default config instance - use this to access default values
# (accessing CONFIG.field directly returns _tuplegetter in Python 3.8)
_config = CONFIG()

logger = logging.getLogger("telnetlib3.server")


class TelnetServer(server_base.BaseServer):
    """Telnet Server protocol performing common negotiation."""

    #: Maximum number of cycles to seek for all terminal types.  We are seeking
    #: the repeat or cycle of a terminal table, choosing the first -- but when
    #: negotiated by MUD clients, we chose the must Unix TERM appropriate,
    TTYPE_LOOPMAX = 8

    # Derived methods from base class

    def __init__(
        self, term="unknown", cols=80, rows=25, timeout=300, *args, **kwargs
    ):  # pylint: disable=keyword-arg-before-vararg
        """Initialize TelnetServer with terminal parameters."""
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
        """Handle new connection and wire up telnet option callbacks."""
        # local
        from .telopt import (  # pylint: disable=import-outside-toplevel
            NAWS,
            TTYPE,
            TSPEED,
            CHARSET,
            XDISPLOC,
            NEW_ENVIRON,
        )

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
        """Process received data and reset timeout timer."""
        self.set_timeout()
        super().data_received(data)

    def begin_negotiation(self):
        """Begin telnet negotiation by requesting terminal type."""
        # local
        from .telopt import DO, TTYPE  # pylint: disable=import-outside-toplevel

        super().begin_negotiation()
        self.writer.iac(DO, TTYPE)

    def begin_advanced_negotiation(self):
        """Request advanced telnet options from client."""
        # local
        from .telopt import (  # pylint: disable=import-outside-toplevel
            DO,
            SGA,
            ECHO,
            NAWS,
            WILL,
            BINARY,
            CHARSET,
            NEW_ENVIRON,
        )

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
        """Check if negotiation is complete including encoding."""
        # local
        from .telopt import (  # pylint: disable=import-outside-toplevel
            SB,
            TTYPE,
            CHARSET,
            NEW_ENVIRON,
        )

        # Debug log to see which options are still pending
        pending = [
            (name_commands(opt), val) for opt, val in self.writer.pending_option.items() if val
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
            SB + CHARSET in self.writer.pending_option and self.writer.pending_option[SB + CHARSET]
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
            logger.debug("encoding complete: %r", encoding)
            self.waiter_encoding.set_result(result)

        elif not self.waiter_encoding.done() and self.writer.remote_option.get(TTYPE) is False:
            # if the remote end doesn't support TTYPE, which is agreed upon
            # to continue towards advanced negotiation of CHARSET, we assume
            # the distant end would not support it, declaring encoding failed.
            logger.debug(
                "encoding failed after %1.2fs: %s, remote_option[TTYPE]=%s, result=%s",
                self.duration,
                encoding,
                self.writer.remote_option.get(TTYPE),
                result,
            )
            self.waiter_encoding.set_result(result)  # False
            return parent

        elif not self.waiter_encoding.done() and final:
            logger.debug("encoding failed after %1.2fs: %s", self.duration, encoding)
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
                "encoding arguments 'outgoing' and 'incoming' are required: toggle at least one."
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
                candidate = accessories.encoding_from_lang(_lang)
                if candidate:
                    try:
                        codecs.lookup(candidate)
                        return candidate
                    except LookupError:
                        pass  # fall through to charset or default

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
        logger.debug("Timeout after %1.2fs", self.idle)
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
        # local
        from .telopt import VAR, USERVAR  # pylint: disable=import-outside-toplevel

        # Parse additional keys from environment variable (comma-delimited)
        additional = os.environ.get("TELNETLIB3_FINGERPRINT_ENVIRON_ADDITIONAL", "")
        additional_keys = [k.strip() for k in additional.split(",") if k.strip()]

        return [
            # Well-known VAR (RFC 1572)
            "USER",
            "DISPLAY",
            # USERVAR - common environment variables
            "LANG",
            "TERM",
            "COLUMNS",
            "LINES",
            "COLORTERM",
            "HOME",
            "SHELL",
            # SSH/remote connection info
            "SSH_CLIENT",
            "SSH_TTY",
            # System info
            "LOGNAME",
            "HOSTNAME",
            "HOSTTYPE",
            "OSTYPE",
            "PWD",
            # Editor preferences
            "EDITOR",
            "VISUAL",
            # Terminal multiplexers
            "TMUX",
            "STY",
            # Locale settings
            "LC_ALL",
            "LC_CTYPE",
            "LC_MESSAGES",
            "LC_COLLATE",
            "LC_TIME",
            # Container/remote
            "DOCKER_HOST",
            # Shell history
            "HISTFILE",
            # Cloud
            "AWS_PROFILE",
            "AWS_REGION",
            # Additional keys from TELNETLIB3_FINGERPRINT_ENVIRON_ADDITIONAL
            *additional_keys,
            # Request any other VAR/USERVAR the client wants to send
            VAR,
            USERVAR,
        ]

    def on_environ(self, mapping):
        """Callback receives NEW_ENVIRON response, :rfc:`1572`."""
        # A well-formed client responds with empty values for variables to
        # mean "no value".  They might have it, they just may not wish to
        # divulge that information.  We pop these keys as a side effect.
        for key, val in list(mapping.items()):
            if not val:
                mapping.pop(key)

        # because we are working with "untrusted input", we make one fair
        # distinction: all keys received by NEW_ENVIRON are in uppercase.
        # this ensures a client may not override trusted values such as
        # 'peer'.
        u_mapping = {key.upper(): val for key, val in list(mapping.items())}

        logger.debug("on_environ received: %r", u_mapping)

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
        self._extra["tspeed"] = f"{rx},{tx}"

    def on_ttype(self, ttype):
        """Callback for TTYPE response, :rfc:`930`."""
        # TTYPE may be requested multiple times, we honor this system and
        # attempt to cause the client to cycle, as their first response may
        # not be their most significant. All responses held as 'ttype{n}',
        # where {n} is their serial response order number.
        #
        # The most recently received terminal type by the server is
        # assumed TERM by this implementation, even when unsolicited.
        key = f"ttype{self._ttype_count}"
        self._extra[key] = ttype
        if ttype:
            self._extra["TERM"] = ttype

        _lastval = self.get_extra_info(f"ttype{self._ttype_count - 1}")

        if key != "ttype1" and ttype == self.get_extra_info("ttype1", None):
            # cycle has looped, stop
            logger.debug("ttype cycle stop at %s: %s, looped.", key, ttype)

        elif not ttype or self._ttype_count > self.TTYPE_LOOPMAX:
            # empty reply string or too many responses!
            logger.warning("ttype cycle stop at %s: %s.", key, ttype)

        elif self._ttype_count == 3 and ttype.upper().startswith("MTTS "):
            val = self.get_extra_info("ttype2")
            logger.debug("ttype cycle stop at %s: %s, using %s from ttype2.", key, ttype, val)
            self._extra["TERM"] = val

        elif ttype == _lastval:
            logger.debug("ttype cycle stop at %s: %s, repeated.", key, ttype)

        else:
            logger.debug("ttype cycle cont at %s: %s.", key, ttype)
            self._ttype_count += 1
            self.writer.request_ttype()

    def on_xdisploc(self, xdisploc):
        """Callback for XDISPLOC response, :rfc:`1096`."""
        self._extra["xdisploc"] = xdisploc

    # private methods

    def _check_encoding(self):
        # Periodically check for completion of ``waiter_encoding``.
        # local
        from .telopt import DO, SB, BINARY, CHARSET  # pylint: disable=import-outside-toplevel

        # Check if we need to request client to use BINARY mode for client-to-server communication
        if (
            self.writer.outbinary
            and not self.writer.inbinary
            and (DO + BINARY) not in self.writer.pending_option
        ):
            logger.debug("BINARY in: direction request.")
            self.writer.iac(DO, BINARY)
            return False

        # Check if CHARSET is enabled but no REQUEST has been sent yet
        if (
            self.writer.remote_option.enabled(CHARSET)
            and self.writer.local_option.enabled(CHARSET)
            and (SB + CHARSET) not in self.writer.pending_option
        ):
            logger.debug("Initiating CHARSET REQUEST after capabilities negotiation")
            self.writer.request_charset()

        # are we able to negotiate BINARY bidirectionally?
        # or, is force_binary=True ?
        return (self.writer.outbinary and self.writer.inbinary) or self.force_binary


class Server:
    """
    Telnet server that tracks connected clients.

    Wraps asyncio.Server with protocol tracking and connection waiting.
    Returned by :func:`create_server`.
    """

    def __init__(self, server):
        """Initialize wrapper around asyncio.Server."""
        self._server = server
        self._protocols = []
        self._new_client = asyncio.Queue()

    def close(self):
        """Close the server, stop accepting new connections, and close all clients."""
        self._server.close()
        # Close all connected client transports
        for protocol in list(self._protocols):
            # pylint: disable=protected-access
            if hasattr(protocol, "_transport") and protocol._transport is not None:
                protocol._transport.close()

    async def wait_closed(self):
        """Wait until the server and all client connections are closed."""
        await self._server.wait_closed()
        # Yield to event loop for pending close callbacks
        await asyncio.sleep(0)
        # Clear protocol list now that server is closed
        self._protocols.clear()

    @property
    def sockets(self):
        """Return list of socket objects the server is listening on."""
        return self._server.sockets

    def is_serving(self):
        """Return True if the server is accepting new connections."""
        return self._server.is_serving()

    @property
    def clients(self):
        """
        List of connected client protocol instances.

        :returns: List of protocol instances for all connected clients.
        """
        # Filter out closed protocols (lazy cleanup)
        self._protocols = [p for p in self._protocols if not getattr(p, "_closing", False)]
        return list(self._protocols)

    async def wait_for_client(self):
        r"""
        Wait for a client to connect and complete negotiation.

        :returns: The protocol instance for the connected client.

        Example::

            server = await telnetlib3.create_server(port=6023)
            client = await server.wait_for_client()
            client.writer.write("Welcome!\r\n")
        """
        return await self._new_client.get()

    def _register_protocol(self, protocol):
        """Register a new protocol instance (called by factory)."""
        # pylint: disable=protected-access
        self._protocols.append(protocol)
        # Only register callbacks if protocol has the required waiters
        # (custom protocols like plain asyncio.Protocol won't have these)
        if hasattr(protocol, "_waiter_connected"):
            protocol._waiter_connected.add_done_callback(
                lambda f, p=protocol: self._new_client.put_nowait(p) if not f.cancelled() else None
            )


class StatusLogger:
    """Periodic status logger for connected clients."""

    def __init__(self, server, interval):
        """
        Initialize status logger.

        :param server: Server instance to monitor.
        :param interval: Logging interval in seconds.
        """
        self._server = server
        self._interval = interval
        self._task = None
        self._last_status = None

    def _get_status(self):
        """Get current status snapshot using IP:port pairs for change detection."""
        clients = self._server.clients
        client_data = []
        for client in clients:
            peername = client.get_extra_info("peername", ("-", 0))
            client_data.append(
                {
                    "ip": peername[0],
                    "port": peername[1],
                    "rx": getattr(client, "rx_bytes", 0),
                    "tx": getattr(client, "tx_bytes", 0),
                    "idle": int(getattr(client, "idle", 0)),
                }
            )
        client_data.sort(key=lambda x: (x["ip"], x["port"]))
        return {"count": len(clients), "clients": client_data}

    def _status_changed(self, current):
        """Check if status differs from last logged."""
        if self._last_status is None:
            return current["count"] > 0
        return current != self._last_status

    def _format_status(self, status):
        """Format status for logging."""
        if status["count"] == 0:
            return "0 clients connected"
        client_info = ", ".join(
            f"{c['ip']}:{c['port']} (rx={c['rx']}, tx={c['tx']}, idle={c['idle']})"
            for c in status["clients"]
        )
        return f"{status['count']} client(s): {client_info}"

    async def _run(self):
        """Run periodic status logging."""
        while True:
            await asyncio.sleep(self._interval)
            status = self._get_status()
            if self._status_changed(status):
                logger.info("Status: %s", self._format_status(status))
                self._last_status = status

    def start(self):
        """Start the status logging task."""
        if self._interval > 0:
            self._task = asyncio.create_task(self._run())

    def stop(self):
        """Stop the status logging task."""
        if self._task:
            self._task.cancel()


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
    :param shell: An async function that is called after negotiation
        completes, receiving arguments ``(reader, writer)``.
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
    :param kwds: Additional keyword arguments passed to the protocol factory.

    :return Server: A :class:`Server` instance that wraps the asyncio.Server
        and provides access to connected client protocols via
        :meth:`Server.wait_for_client` and :attr:`Server.clients`.
    """
    protocol_factory = protocol_factory or TelnetServer
    loop = asyncio.get_event_loop()

    telnet_server = Server(None)

    def factory():
        protocol = protocol_factory(**kwds)
        telnet_server._register_protocol(protocol)  # pylint: disable=protected-access
        return protocol

    server = await loop.create_server(factory, host, port)
    telnet_server._server = server  # pylint: disable=protected-access

    return telnet_server


async def _sigterm_handler(server, _log):
    logger.info("SIGTERM received, closing server.")

    # This signals the completion of the server.wait_closed() Future,
    # allowing the main() function to complete.
    server.close()


def parse_server_args():
    """Parse command-line arguments for telnet server."""
    # std imports
    import sys  # pylint: disable=import-outside-toplevel

    # Extract arguments after '--' for PTY program before argparse sees them
    argv = sys.argv[1:]
    pty_args = []
    if PTY_SUPPORT and "--" in argv:
        idx = argv.index("--")
        pty_args = argv[idx + 1 :]
        argv = argv[:idx]

    parser = argparse.ArgumentParser(
        description="Telnet protocol server",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("host", nargs="?", default=_config.host, help="bind address")
    parser.add_argument("port", nargs="?", type=int, default=_config.port, help="bind port")
    parser.add_argument("--loglevel", default=_config.loglevel, help="level name")
    parser.add_argument("--logfile", default=_config.logfile, help="filepath")
    parser.add_argument("--logfmt", default=_config.logfmt, help="log format")
    parser.add_argument(
        "--shell",
        default=_config.shell,
        type=accessories.function_lookup,
        help="module.function_name",
    )
    parser.add_argument("--encoding", default=_config.encoding, help="encoding name")
    parser.add_argument(
        "--force-binary",
        action="store_true",
        default=_config.force_binary,
        help="force binary transmission",
    )
    parser.add_argument("--timeout", default=_config.timeout, help="idle disconnect (0 disables)")
    parser.add_argument(
        "--connect-maxwait",
        type=float,
        default=_config.connect_maxwait,
        help="timeout for pending negotiation",
    )
    if PTY_SUPPORT:
        parser.add_argument(
            "--pty-exec",
            metavar="PROGRAM",
            default=_config.pty_exec,
            help="execute PROGRAM in a PTY for each connection (use -- to pass args)",
        )
        parser.add_argument(
            "--pty-fork-limit",
            type=int,
            metavar="N",
            default=_config.pty_fork_limit,
            help="limit concurrent PTY connections (0 disables)",
        )
        parser.add_argument(
            "--pty-raw",
            action="store_true",
            default=_config.pty_raw,
            help="raw mode for --pty-exec: disable PTY echo for programs that "
                 "handle their own terminal I/O (curses, blessed, ucs-detect)",
        )
    parser.add_argument(
        "--robot-check",
        action="store_true",
        default=_config.robot_check,
        help="check if client can render wide unicode (rejects bots)",
    )
    parser.add_argument(
        "--status-interval",
        type=int,
        metavar="SECONDS",
        default=_config.status_interval,
        help=(
            "periodic status log interval in seconds (0 to disable). "
            "status only logged when connected clients has changed."
        ),
    )
    result = vars(parser.parse_args(argv))
    result["pty_args"] = pty_args if PTY_SUPPORT else None
    if not PTY_SUPPORT:
        result["pty_exec"] = None
        result["pty_fork_limit"] = 0
        result["pty_raw"] = False
    return result


async def run_server(  # pylint: disable=too-many-positional-arguments,too-many-locals
    host=_config.host,
    port=_config.port,
    loglevel=_config.loglevel,
    logfile=_config.logfile,
    logfmt=_config.logfmt,
    shell=_config.shell,
    encoding=_config.encoding,
    force_binary=_config.force_binary,
    timeout=_config.timeout,
    connect_maxwait=_config.connect_maxwait,
    pty_exec=_config.pty_exec,
    pty_args=_config.pty_args,
    pty_raw=_config.pty_raw,
    robot_check=_config.robot_check,
    pty_fork_limit=_config.pty_fork_limit,
    status_interval=_config.status_interval,
):
    """
    Program entry point for server daemon.

    This function configures a logger and creates a telnet server for the given keyword arguments,
    serving forever, completing only upon receipt of SIGTERM.
    """
    log = accessories.make_logger(
        name="telnetlib3.server", loglevel=loglevel, logfile=logfile, logfmt=logfmt
    )

    if pty_exec:
        if not PTY_SUPPORT:
            raise NotImplementedError("PTY support is not available on this platform (Windows?)")
        # local
        from .server_pty_shell import make_pty_shell  # pylint: disable=import-outside-toplevel

        shell = make_pty_shell(pty_exec, pty_args, raw_mode=pty_raw)

    # Wrap shell with guards if enabled
    if robot_check or pty_fork_limit:
        # local
        # pylint: disable=import-outside-toplevel
        from .guard_shells import robot_shell  # pylint: disable=import-outside-toplevel
        from .guard_shells import ConnectionCounter, busy_shell
        from .guard_shells import robot_check as do_robot_check

        counter = ConnectionCounter(pty_fork_limit) if pty_fork_limit else None
        inner_shell = shell

        async def guarded_shell(reader, writer):
            # Check connection limit first
            if counter and not counter.try_acquire():
                try:
                    await busy_shell(reader, writer)
                finally:
                    if not writer.is_closing():
                        writer.close()
                return

            try:
                # Check robot if enabled
                if robot_check:
                    passed = await do_robot_check(reader, writer)
                    if not passed:
                        await robot_shell(reader, writer)
                        if not writer.is_closing():
                            writer.close()
                        return

                # Run actual shell
                await inner_shell(reader, writer)
            finally:
                if counter:
                    counter.release()

        shell = guarded_shell

    # log all function arguments.
    _locals = locals()
    _cfg_mapping = ", ".join((f"{field}={{{field}}}" for field in CONFIG._fields)).format(**_locals)
    logger.debug("Server configuration: %s", _cfg_mapping)

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
    loop.add_signal_handler(signal.SIGTERM, asyncio.ensure_future, _sigterm_handler(server, log))

    # Start periodic status logger if enabled
    status_logger = None
    if status_interval > 0:
        status_logger = StatusLogger(server, status_interval)
        status_logger.start()

    logger.info("Server ready on %s:%s", host, port)

    # await completion of server stop
    try:
        await server.wait_closed()
    finally:
        # stop status logger
        if status_logger:
            status_logger.stop()
        # remove signal handler on stop
        loop.remove_signal_handler(signal.SIGTERM)

    logger.info("Server stop.")


def main():
    """Entry point for telnetlib3-server command."""
    asyncio.run(run_server(**parse_server_args()))


if __name__ == "__main__":
    main()
