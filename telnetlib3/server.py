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

from __future__ import annotations

# std imports
import ssl as ssl_module
import sys
import zlib
import codecs
import signal
import socket
import asyncio
import logging
import argparse
from typing import Any, Dict, List, Type, Tuple, Union, Callable, Optional, Sequence, NamedTuple

# local
from . import accessories, server_base
from ._types import ShellCallback
from .telopt import SB, SE, IAC, MCCP2_COMPRESS, name_commands
from .stream_reader import TelnetReader, TelnetReaderUnicode
from .stream_writer import TelnetWriter, TelnetWriterUnicode

# Check if PTY support is available (Unix-only modules: pty, termios, fcntl)
try:
    import pty  # noqa: F401 pylint:disable=unused-import
    import fcntl  # noqa: F401 pylint:disable=unused-import
    import termios  # noqa: F401 pylint:disable=unused-import

    PTY_SUPPORT = True
except ImportError:
    PTY_SUPPORT = False

__all__ = (
    "TelnetServer",
    "LinemodeServer",
    "Server",
    "create_server",
    "run_server",
    "parse_server_args",
)


class CONFIG(NamedTuple):
    """Default configuration for the telnet server."""

    host: str = "localhost"
    port: int = 6023
    loglevel: str = "info"
    logfile: Optional[str] = None
    logfmt: str = accessories._DEFAULT_LOGFMT
    shell: Callable[..., Any] = accessories.function_lookup("telnetlib3.telnet_server_shell")
    encoding: str = "utf8"
    force_binary: bool = False
    timeout: int = 300
    connect_maxwait: float = 1.5
    pty_exec: Optional[str] = None
    pty_args: Optional[List[str]] = None
    pty_raw: bool = True
    robot_check: bool = False
    pty_fork_limit: int = 0
    status_interval: int = 20
    never_send_ga: bool = False
    line_mode: bool = False


# Default config instance - use this to access default values
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
        self,
        term: str = "unknown",
        cols: int = 80,
        rows: int = 25,
        timeout: int = 300,
        shell: Optional[ShellCallback] = None,
        _waiter_connected: Optional[asyncio.Future[None]] = None,
        encoding: Union[str, bool] = "utf8",
        encoding_errors: str = "strict",
        force_binary: bool = False,
        never_send_ga: bool = False,
        line_mode: bool = False,
        connect_maxwait: float = 4.0,
        compression: Optional[bool] = None,
        limit: Optional[int] = None,
        reader_factory: type = TelnetReader,
        reader_factory_encoding: type = TelnetReaderUnicode,
        writer_factory: type = TelnetWriter,
        writer_factory_encoding: type = TelnetWriterUnicode,
    ) -> None:
        """Initialize TelnetServer with terminal parameters."""
        super().__init__(
            shell=shell,
            _waiter_connected=_waiter_connected,
            encoding=encoding,
            encoding_errors=encoding_errors,
            force_binary=force_binary,
            never_send_ga=never_send_ga,
            line_mode=line_mode,
            connect_maxwait=connect_maxwait,
            limit=limit,
            reader_factory=reader_factory,
            reader_factory_encoding=reader_factory_encoding,
            writer_factory=writer_factory,
            writer_factory_encoding=writer_factory_encoding,
        )
        self._environ_requested = False
        self._echo_negotiated = False
        self._mccp2_compressor: Optional[Any] = None
        self._mccp2_pending: bool = False
        self._compression: Optional[bool] = compression
        self._mccp2_enabled: bool = compression is True
        self._mccp2_orig_write: Optional[Any] = None
        self.waiter_encoding: asyncio.Future[bool] = asyncio.Future()
        self._tasks.append(self.waiter_encoding)
        self._ttype_count = 1
        self._timer: Optional[asyncio.TimerHandle] = None
        self._extra.update(
            {
                "term": term,
                "charset": encoding or "",
                "cols": cols,
                "rows": rows,
                "timeout": timeout,
            }
        )

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        """Handle new connection and wire up telnet option callbacks."""
        from .telopt import NAWS, TTYPE, TSPEED, CHARSET, XDISPLOC, NEW_ENVIRON

        super().connection_made(transport)

        # Set compression policy on writer
        self.writer.compression = self._compression

        # begin timeout timer
        self.set_timeout()

        # Wire extended rfc callbacks for responses to
        # requests of terminal attributes, environment values, etc.
        _ext_callbacks: List[Tuple[bytes, Callable[..., Any]]] = [
            (NAWS, self.on_naws),
            (NEW_ENVIRON, self.on_environ),
            (TSPEED, self.on_tspeed),
            (TTYPE, self.on_ttype),
            (XDISPLOC, self.on_xdisploc),
            (CHARSET, self.on_charset),
        ]
        for tel_opt, callback_fn in _ext_callbacks:
            self.writer.set_ext_callback(tel_opt, callback_fn)

        # Wire up offer callbacks that return definitions for outgoing requests.
        for tel_opt, callback_fn in [
            (NEW_ENVIRON, self.on_request_environ),
            (CHARSET, self.on_request_charset),
        ]:
            self.writer.set_ext_offer_callback(tel_opt, callback_fn)

    _tls_checked = False

    def data_received(self, data: bytes) -> None:
        """Process received data and reset timeout timer."""
        if not self._tls_checked:
            self._tls_checked = True
            if data and data[0] == 0x16:
                peername = self._transport.get_extra_info("peername", ("-", 0))
                logger.warning(
                    "TLS ClientHello from %s:%s but server has no SSL"
                    " context -- closing connection",
                    peername[0], peername[1],
                )
                self._transport.close()
                return
        self.set_timeout()
        super().data_received(data)
        # MCCP2: start compression once client confirms DO MCCP2
        if (
            self._mccp2_enabled
            and not self._mccp2_pending
            and self._mccp2_compressor is None
            and self.writer.local_option.enabled(MCCP2_COMPRESS)
        ):
            self._mccp2_start()

    def _mccp2_start(self) -> None:
        """Send SB MCCP2 SE and start compressing server→client output."""
        self._mccp2_pending = True
        # All bytes after this SE are compressed.
        self.writer.send_iac(IAC + SB + MCCP2_COMPRESS + IAC + SE)

        self._mccp2_compressor = zlib.compressobj(
            zlib.Z_BEST_COMPRESSION, zlib.DEFLATED, 12, 5, zlib.Z_DEFAULT_STRATEGY
        )
        # Wrap transport.write so all subsequent output is compressed
        transport = self.writer._transport
        orig_write = transport.write

        def compressed_write(data: bytes) -> None:
            if self._mccp2_compressor is not None:
                compressed = self._mccp2_compressor.compress(data)
                compressed += self._mccp2_compressor.flush(zlib.Z_SYNC_FLUSH)
                orig_write(compressed)
            else:
                orig_write(data)

        transport.write = compressed_write  # type: ignore[method-assign]
        self._mccp2_orig_write = orig_write
        self.writer.mccp2_active = True
        logger.debug("MCCP2 compression started (server→client)")

    def _mccp2_end(self) -> None:
        """Stop MCCP2 compression, flush Z_FINISH."""
        if self._mccp2_compressor is not None:
            try:
                assert self._mccp2_orig_write is not None
                self._mccp2_orig_write(self._mccp2_compressor.flush(zlib.Z_FINISH))
            except zlib.error as exc:
                logger.debug("MCCP2 Z_FINISH flush error: %s", exc)
            self._mccp2_compressor = None
            self.writer._transport.write = self._mccp2_orig_write  # type: ignore[method-assign]
        self._mccp2_pending = False
        self.writer.mccp2_active = False
        logger.debug("MCCP2 compression ended (server→client)")

    def begin_negotiation(self) -> None:
        """Begin telnet negotiation by requesting terminal type."""
        from .telopt import DO, TTYPE

        super().begin_negotiation()
        self.writer.iac(DO, TTYPE)

    def begin_advanced_negotiation(self) -> None:
        """
        Request advanced telnet options from client.

        ``DO NEW_ENVIRON`` is deferred until the TTYPE cycle completes
        so that Microsoft telnet (ANSI + VT100) can be detected first.
        See ``_negotiate_environ()`` and GitHub issue #24.

        ``WILL ECHO`` is deferred until TTYPE reveals the client identity.
        MUD clients (Mudlet, TinTin++, etc.) interpret ``WILL ECHO`` as
        "password mode" and mask input.  See ``_negotiate_echo()``.
        """
        from .telopt import DO, SGA, NAWS, WILL, BINARY, CHARSET, MCCP2_COMPRESS, MCCP3_COMPRESS

        super().begin_advanced_negotiation()
        if not self.line_mode:
            self.writer.iac(WILL, SGA)
        # WILL ECHO is deferred -- see _negotiate_echo()
        self.writer.iac(WILL, BINARY)
        # DO NEW_ENVIRON is deferred -- see _negotiate_environ()
        self.writer.iac(DO, NAWS)
        if self.default_encoding:
            self.writer.iac(DO, CHARSET)
        # MCCP2/MCCP3: opt-in via compression=True, disabled over TLS
        # (compress-then-encrypt is vulnerable to CRIME/BREACH attacks).
        if self._mccp2_enabled:
            ssl_obj = self.writer.get_extra_info("ssl_object")
            if ssl_obj is None:
                self.writer.iac(WILL, MCCP2_COMPRESS)
                self.writer.iac(WILL, MCCP3_COMPRESS)
            else:
                logger.debug("MCCP disabled: TLS active (CRIME/BREACH mitigation)")

    def check_negotiation(self, final: bool = False) -> bool:
        """Check if negotiation is complete including encoding."""
        from .telopt import DO, SB, TTYPE, CHARSET, NEW_ENVIRON

        # If TTYPE cycle stalled or client refused TTYPE, trigger
        # deferred ECHO and NEW_ENVIRON negotiation now.  Only when
        # advanced negotiation is active -- a raw TCP client that
        # WONTs TTYPE should not be sent DO NEW_ENVIRON.
        if not self._echo_negotiated and self._advanced:
            ttype_refused = self.writer.remote_option.get(TTYPE) is False
            if ttype_refused or final:
                self._negotiate_echo()

        if not self._environ_requested and self._advanced:
            ttype_refused = self.writer.remote_option.get(TTYPE) is False
            ttype_do_pending = self.writer.pending_option.get(DO + TTYPE)
            ttype_sb_pending = self.writer.pending_option.get(SB + TTYPE)
            if ttype_refused or final:
                self._negotiate_environ()
            elif not ttype_do_pending and not ttype_sb_pending:
                # TTYPE fully resolved but on_ttype never called
                # _negotiate_environ (shouldn't happen, but be safe)
                self._negotiate_environ()

        # Debug log to see which options are still pending
        pending = [
            (name_commands(opt), val) for opt, val in self.writer.pending_option.items() if val
        ]
        if pending:
            logger.debug("Pending options: %r", pending)

        # Check if we're waiting for important subnegotiations
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

    def encoding(
        self, outgoing: Optional[bool] = None, incoming: Optional[bool] = None
    ) -> Union[str, bool]:
        """
        Return encoding for the given stream direction.

        :param outgoing: Whether the return value is suitable for
            encoding bytes for transmission to client end.
        :param incoming: Whether the return value is suitable for
            decoding bytes received from the client.
        :raises TypeError: when a direction argument, either ``outgoing``
            or ``incoming``, was not set ``True``.
        :returns: ``'US-ASCII'`` for the directions indicated, unless
            ``BINARY`` :rfc:`856` has been negotiated for the direction
            indicated or ``force_binary`` is set ``True``.
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

    def set_timeout(self, duration: int = -1) -> None:
        """
        Restart or unset timeout for client.

        :param duration: When specified as a positive integer,
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

    def on_timeout(self) -> None:
        """
        Callback received on session timeout.

        Default implementation writes "Timeout." bound by CRLF and closes.

        This can be disabled by calling :meth:`set_timeout` with
        ``duration`` value of ``0``.
        """
        logger.debug("Timeout after %1.2fs", self.idle)
        if isinstance(self.writer, TelnetWriterUnicode):
            self.writer.write("\r\nTimeout.\r\n")
        else:
            self.writer.write(b"\r\nTimeout.\r\n")
        self.timeout_connection()

    def on_naws(self, rows: int, cols: int) -> None:
        """
        Callback receives NAWS response, :rfc:`1073`.

        :param rows: screen size, by number of cells in height.
        :param cols: screen size, by number of cells in width.
        """
        self._extra.update({"rows": rows, "cols": cols})

    def on_request_environ(self) -> List[Union[str, bytes]]:
        """
        Definition for NEW_ENVIRON request of client, :rfc:`1572`.

        This method is a callback from :meth:`~.TelnetWriter.request_environ`,
        first entered on receipt of (WILL, NEW_ENVIRON) by server.  The return
        value *defines the request made to the client* for environment values.

        :returns: A list of US-ASCII character strings indicating the
            environment keys the server requests of the client.  If this list
            contains the special byte constants, ``USERVAR`` or ``VAR``, the
            client is allowed to volunteer any other additional user or system
            values.  An empty return value indicates that no request should be
            made.

        The default return value requests only common variables needed for
        session setup.  Override this method or see
        :data:`~.fingerprinting.ENVIRON_EXTENDED` for a larger set used
        during client fingerprinting.

        .. note::

            ``USER`` is excluded when the client is Microsoft telnet
            (ttype1=ANSI, ttype2=VT100) because requesting it crashes
            ``telnet.exe``.  See GitHub issue #24.
        """
        from .telopt import VAR, USERVAR

        ttype1 = self.get_extra_info("ttype1") or ""
        ttype2 = self.get_extra_info("ttype2") or ""
        is_ms_telnet = ttype1 == "ANSI" and ttype2 == "VT100"

        result: List[Union[str, bytes]] = []
        if not is_ms_telnet:
            result.append("USER")
        result.extend(
            [
                "LOGNAME",
                "DISPLAY",
                "LANG",
                "TERM",
                "TERM_PROGRAM",
                "COLUMNS",
                "LINES",
                "COLORTERM",
                "EDITOR",
                "IPADDRESS",
                # Request any other VAR/USERVAR the client wants to send
                VAR,
                USERVAR,
            ]
        )
        return result

    def on_environ(self, mapping: Dict[str, str]) -> None:
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

        # When the client provides LANG (with encoding suffix) or CHARSET,
        # presume BINARY capability even without explicit BINARY negotiation.
        has_charset = bool(u_mapping.get("CHARSET"))
        lang_val = u_mapping.get("LANG", "")
        has_lang_encoding = "." in lang_val and lang_val != "C"
        if (has_charset or has_lang_encoding) and self.writer is not None:
            self.writer._force_binary_on_protocol()

    def on_request_charset(self) -> List[str]:
        """
        Definition for CHARSET request by client, :rfc:`2066`.

        This method is a callback from :meth:`~.TelnetWriter.request_charset`,
        first entered on receipt of (WILL, CHARSET) by server.  The return
        value *defines the request made to the client* for encodings.

        :returns: A list of US-ASCII character strings indicating the
            encodings offered by the server in its preferred order.  An empty
            return value indicates that no encodings are offered.

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

    def on_charset(self, charset: str) -> None:
        """Callback for CHARSET response, :rfc:`2066`."""
        self._extra["charset"] = charset

    def on_tspeed(self, rx: str, tx: str) -> None:
        """Callback for TSPEED response, :rfc:`1079`."""
        self._extra["tspeed"] = f"{rx},{tx}"

    def on_ttype(self, ttype: str) -> None:
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

        # After first TTYPE, negotiate ECHO -- MUD clients are detected
        # by ttype1 and never receive WILL ECHO (avoids password mode).
        self._negotiate_echo()

        # After ttype1: send DO NEW_ENVIRON now unless ttype1 is "ANSI",
        # in which case we defer until ttype2 to detect Microsoft telnet
        # (ANSI + VT100) which crashes on NEW_ENVIRON (issue #24).
        if key == "ttype1" and ttype != "ANSI":
            self._negotiate_environ()
        elif key == "ttype2" and not self._environ_requested:
            self._negotiate_environ()

        if key != "ttype1" and ttype == self.get_extra_info("ttype1", None):
            # cycle has looped, stop
            logger.debug("ttype cycle stop at %s: %s, looped.", key, ttype)
            self._negotiate_environ()

        elif not ttype or self._ttype_count > self.TTYPE_LOOPMAX:
            # empty reply string or too many responses!
            logger.warning("ttype cycle stop at %s: %s.", key, ttype)
            self._negotiate_environ()

        elif self._ttype_count == 3 and ttype.upper().startswith("MTTS "):
            val = self.get_extra_info("ttype2")
            logger.debug("ttype cycle stop at %s: %s, using %s from ttype2.", key, ttype, val)
            self._extra["TERM"] = val
            self._negotiate_environ()

        elif ttype == _lastval:
            logger.debug("ttype cycle stop at %s: %s, repeated.", key, ttype)
            self._negotiate_environ()

        else:
            logger.debug("ttype cycle cont at %s: %s.", key, ttype)
            self._ttype_count += 1
            self.writer.request_ttype()

    def on_xdisploc(self, xdisploc: str) -> None:
        """Callback for XDISPLOC response, :rfc:`1096`."""
        self._extra["xdisploc"] = xdisploc

    # private methods

    def _negotiate_environ(self) -> None:
        """
        Send ``DO NEW_ENVIRON``.

        Called from :meth:`on_ttype` as soon as we have enough information:

        - After ``ttype1`` when it is not ``"ANSI"``.
        - After ``ttype2`` when ``ttype1`` *is* ``"ANSI"`` -- this gives
          :meth:`on_request_environ` enough context to detect Microsoft
          telnet and exclude ``USER`` (GitHub issue #24).
        - From :meth:`check_negotiation` when TTYPE stalls or is refused.
        """
        if self._environ_requested:
            return
        self._environ_requested = True

        from .telopt import DO, NEW_ENVIRON

        self.writer.iac(DO, NEW_ENVIRON)

    def _negotiate_echo(self) -> None:
        """
        Send ``WILL ECHO`` unless the client is a MUD client or line mode.

        MUD clients (Mudlet, TinTin++, etc.) interpret ``WILL ECHO`` as
        "password mode" and mask the input bar.  We defer ECHO negotiation
        until TTYPE arrives so MUD clients are detected first.

        When :attr:`line_mode` is ``True``, ECHO is never sent so the
        client stays in NVT local (line) mode.

        Called from :meth:`on_ttype` on each TTYPE response, and from
        :meth:`check_negotiation` when TTYPE stalls or is refused.
        """
        if self._echo_negotiated:
            return
        self._echo_negotiated = True

        if self.line_mode:
            return

        from .telopt import ECHO, WILL
        from .fingerprinting import _is_maybe_mud

        assert self.writer is not None
        if _is_maybe_mud(self.writer):
            logger.info("skipping WILL ECHO for MUD client")
            return
        self.writer.iac(WILL, ECHO)

    def _check_encoding(self) -> bool:
        # Periodically check for completion of ``waiter_encoding``.
        from .telopt import DO, SB, BINARY, CHARSET

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


class _TLSAutoDetectProtocol(asyncio.Protocol):
    """
    Protocol wrapper that auto-detects TLS vs plain telnet connections.

    Reading is paused on connect, then a non-blocking ``MSG_PEEK`` on a
    duplicated socket checks the first byte without consuming it.  A TLS
    ClientHello always begins with ``0x16`` (22); anything else (telnet IAC
    ``0xFF``, printable ASCII, etc.) is plain telnet.

    Plain telnet clients typically wait for the server to speak first, so a
    timeout (*detect_timeout* seconds) assumes plain telnet when no data
    arrives promptly.  TLS clients always send ClientHello immediately.

    When TLS is detected, :meth:`loop.start_tls` upgrades the transport.
    Plain connections resume reading and hand off directly.
    """

    _PEEK_RETRY_SECS = 0.01

    def __init__(
        self,
        ssl_context: ssl_module.SSLContext,
        real_factory: Callable[[], asyncio.Protocol],
        detect_timeout: float = 1.0,
    ) -> None:
        self._ssl_context = ssl_context
        self._detect_timeout_secs = detect_timeout
        self._real_factory = real_factory
        self._transport: Optional[asyncio.Transport] = None
        self._detect_timer: Optional[asyncio.TimerHandle] = None
        self._decided = False

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        """Pause reading and begin non-blocking peek loop."""
        self._transport = transport  # type: ignore[assignment]
        transport.pause_reading()  # type: ignore[attr-defined]
        loop = asyncio.get_event_loop()
        self._detect_timer = loop.call_later(
            self._detect_timeout_secs, self._on_detect_timeout
        )
        loop.call_soon(self._try_peek)

    def _cancel_timer(self) -> None:
        """Cancel the pending detect timeout."""
        if self._detect_timer is not None:
            self._detect_timer.cancel()
            self._detect_timer = None

    def _try_peek(self) -> None:
        """Non-blocking peek at the first byte via a duplicated socket."""
        if self._decided:
            return
        if self._transport is None:
            logger.debug("tls-auto: no transport in _try_peek (client disconnected?)")
            return
        tsock = self._transport.get_extra_info("socket")
        if tsock is None:
            logger.debug("tls-auto: no socket in _try_peek (client disconnected?)")
            return
        peek_sock = socket.fromfd(tsock.fileno(), tsock.family, socket.SOCK_STREAM)
        peek_sock.setblocking(False)
        try:
            data = peek_sock.recv(1, socket.MSG_PEEK)
        except BlockingIOError:
            asyncio.get_event_loop().call_later(
                self._PEEK_RETRY_SECS, self._try_peek
            )
            return
        except OSError:
            data = b""
        finally:
            peek_sock.close()

        self._decided = True
        self._cancel_timer()
        if not data:
            self._transport.close()
            return
        if data[0] == 0x16:
            logger.debug("tls-auto: TLS ClientHello detected")
            asyncio.ensure_future(self._upgrade_to_tls())
        else:
            logger.debug("tls-auto: non-TLS byte 0x%02x, plain telnet",
                         data[0])
            self._handoff_plain()

    def _on_detect_timeout(self) -> None:
        """No data arrived -- assume plain telnet."""
        self._detect_timer = None
        if self._decided:
            return
        self._decided = True
        logger.debug("tls-auto: no data in %.1fs, assuming plain telnet",
                      self._detect_timeout_secs)
        self._handoff_plain()

    async def _upgrade_to_tls(self) -> None:
        """
        Upgrade the plain transport to TLS, then hand off.

        .. note::

            On Python < 3.11, ``loop.start_tls(server_side=True)`` may hang
            due to a bug in the ``_SSLPipe``-based ``SSLProtocol``
            (rewritten in 3.11).  See
            https://github.com/python/cpython/issues/79156
        """
        loop = asyncio.get_running_loop()
        assert self._transport is not None
        protocol = self._real_factory()
        try:
            # start_tls uses call_connection_made=False, so we must call
            # connection_made ourselves with the returned SSL transport.
            ssl_transport = await loop.start_tls(
                self._transport, protocol, self._ssl_context, server_side=True
            )
        except (ssl_module.SSLError, OSError) as exc:
            logger.debug("TLS handshake failed: %s", exc)
            if not self._transport.is_closing():
                self._transport.close()
            return
        assert ssl_transport is not None
        protocol.connection_made(ssl_transport)

    def _handoff_plain(self) -> None:
        """Hand off to the real protocol as a plain telnet connection."""
        assert self._transport is not None
        protocol = self._real_factory()
        self._transport.set_protocol(protocol)
        protocol.connection_made(self._transport)
        self._transport.resume_reading()

    def data_received(self, data: bytes) -> None:  # pragma: no cover
        """Not expected -- reading is paused during detection."""

    def connection_lost(self, exc: Optional[Exception]) -> None:
        """Connection dropped before detection completed."""
        self._decided = True
        self._cancel_timer()


class LinemodeServer(TelnetServer):
    """
    :class:`TelnetServer` subclass that negotiates LINEMODE EDIT.

    In addition to the standard options negotiated by :class:`TelnetServer`,
    this server sends ``DO LINEMODE`` during advanced negotiation, proposes
    LINEMODE EDIT (local line editing by the client), and suppresses
    ``WILL ECHO`` so the client performs local echoing via its LINEMODE buffer.

    Use with :func:`create_server` to enable RFC 1184 LINEMODE EDIT on a
    :func:`~.telnet_server_shell` session or any custom shell.
    """

    from . import slc as _slc_module

    #: Propose LINEMODE EDIT (local line editing) instead of remote mode.
    default_linemode = _slc_module.Linemode(_slc_module.LMODE_MODE_LOCAL)

    def begin_advanced_negotiation(self) -> None:
        """Negotiate standard options plus ``DO LINEMODE``."""
        from .telopt import DO, LINEMODE

        super().begin_advanced_negotiation()
        # Propagate the protocol-level default_linemode to the writer so that
        # TelnetWriter.handle_will(LINEMODE) proposes the correct mode (LOCAL/EDIT)
        # rather than the TelnetWriter class default (REMOTE).
        self.writer.default_linemode = self.default_linemode
        self.writer.iac(DO, LINEMODE)

    def _negotiate_echo(self) -> None:
        """Skip ``WILL ECHO`` — LINEMODE EDIT client handles local echo."""
        if self._echo_negotiated:
            return
        self._echo_negotiated = True


class Server:
    """
    Telnet server that tracks connected clients.

    Wraps asyncio.Server with protocol tracking and connection waiting.
    Returned by :func:`create_server`.
    """

    def __init__(self, server: Optional[asyncio.Server]) -> None:
        """Initialize wrapper around asyncio.Server."""
        self._server: Optional[asyncio.Server] = server
        self._protocols: List[server_base.BaseServer] = []
        self._new_client: asyncio.Queue[server_base.BaseServer] = asyncio.Queue()

    def close(self) -> None:
        """Close the server, stop accepting new connections, and close all clients."""
        self._server.close()
        # Close all connected client transports
        for protocol in list(self._protocols):
            if hasattr(protocol, "_transport") and protocol._transport is not None:
                protocol._transport.close()

    async def wait_closed(self) -> None:
        """Wait until the server and all client connections are closed."""
        await self._server.wait_closed()
        # Yield to event loop for pending close callbacks
        await asyncio.sleep(0)
        # Clear protocol list now that server is closed
        self._protocols.clear()

    @property
    def sockets(self) -> Optional[Tuple["socket.socket", ...]]:
        """Return list of socket objects the server is listening on."""
        return self._server.sockets

    def is_serving(self) -> bool:
        """Return True if the server is accepting new connections."""
        return self._server.is_serving()

    @property
    def clients(self) -> List[server_base.BaseServer]:
        """
        List of connected client protocol instances.

        :returns: List of protocol instances for all connected clients.
        """
        # Filter out closed protocols (lazy cleanup)
        self._protocols = [p for p in self._protocols if not getattr(p, "_closing", False)]
        return list(self._protocols)

    async def wait_for_client(self) -> server_base.BaseServer:
        r"""
        Wait for a client to connect and complete negotiation.

        :returns: The protocol instance for the connected client.

        Example::

            server = await telnetlib3.create_server(port=6023)
            client = await server.wait_for_client()
            client.writer.write("Welcome!\r\n")
        """
        return await self._new_client.get()

    def _register_protocol(self, protocol: asyncio.Protocol) -> None:
        """Register a new protocol instance (called by factory)."""
        self._protocols.append(protocol)  # type: ignore[arg-type]
        # Only register callbacks if protocol has the required waiters
        # (custom protocols like plain asyncio.Protocol won't have these)
        if hasattr(protocol, "_waiter_connected"):
            protocol._waiter_connected.add_done_callback(
                lambda f, p=protocol: self._new_client.put_nowait(p) if not f.cancelled() else None
            )


class StatusLogger:
    """Periodic status logger for connected clients."""

    def __init__(self, server: Server, interval: int) -> None:
        """
        Initialize status logger.

        :param server: Server instance to monitor.
        :param interval: Logging interval in seconds.
        """
        self._server = server
        self._interval = interval
        self._task: Optional["asyncio.Task[None]"] = None
        self._last_status: Optional[Dict[str, Any]] = None

    def _get_status(self) -> Dict[str, Any]:
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
                    "tls": client.get_extra_info("ssl_object") is not None,
                }
            )
        client_data.sort(key=lambda x: (x["ip"], x["port"]))
        return {"count": len(clients), "clients": client_data}

    def _status_changed(self, current: Dict[str, Any]) -> bool:
        """Check if status differs from last logged."""
        if self._last_status is None:
            return bool(current["count"] > 0)
        return current != self._last_status

    def _format_status(self, status: Dict[str, Any]) -> str:
        """Format status for logging."""
        if status["count"] == 0:
            return "0 clients connected"
        def _fmt_client(c: Dict[str, Any]) -> str:
            tls = " tls" if c["tls"] else ""
            return f"{c['ip']}:{c['port']} (rx={c['rx']}, tx={c['tx']}, idle={c['idle']}{tls})"

        client_info = ", ".join(_fmt_client(c) for c in status["clients"])
        return f"{status['count']} client(s): {client_info}"

    async def _run(self) -> None:
        """Run periodic status logging."""
        while True:
            await asyncio.sleep(self._interval)
            status = self._get_status()
            if self._status_changed(status):
                logger.info("Status: %s", self._format_status(status))
                self._last_status = status

    def start(self) -> None:
        """Start the status logging task."""
        if self._interval > 0:
            self._task = asyncio.create_task(self._run())

    def stop(self) -> None:
        """Stop the status logging task."""
        if self._task:
            self._task.cancel()


async def create_server(
    host: Optional[Union[str, Sequence[str]]] = None,
    port: int = 23,
    protocol_factory: Optional[Type[asyncio.Protocol]] = TelnetServer,
    shell: Optional[ShellCallback] = None,
    encoding: Union[str, bool] = "utf8",
    encoding_errors: str = "strict",
    force_binary: bool = False,
    never_send_ga: bool = False,
    line_mode: bool = False,
    connect_maxwait: float = 4.0,
    compression: Optional[bool] = None,
    limit: Optional[int] = None,
    term: str = "unknown",
    cols: int = 80,
    rows: int = 25,
    timeout: int = 300,
    ssl: Optional[ssl_module.SSLContext] = None,
    tls_auto: Union[bool, float] = False,
) -> Server:
    """
    Create a TCP Telnet server.

    :param host: The host parameter can be a string, in that case the TCP
        server is bound to host and port. The host parameter can also be a
        sequence of strings, and in that case the TCP server is bound to all
        hosts of the sequence.
    :param port: Listen port for TCP server.
    :param protocol_factory: An alternate protocol factory for the server.
        When unspecified, :class:`TelnetServer` is used.
    :param shell: An async function that is called after negotiation
        completes, receiving arguments ``(reader, writer)``.
        Default is :func:`~.telnet_server_shell`.  The reader is a
        :class:`~.TelnetReader` instance, the writer is a
        :class:`~.TelnetWriter` instance.
    :param encoding: The default assumed encoding, or ``False`` to disable
        unicode support.  Encoding may be negotiated to another value by
        the client through NEW_ENVIRON :rfc:`1572` by sending environment value
        of ``LANG``, or by any legal value for CHARSET :rfc:`2066` negotiation.

        The server's attached ``reader, writer`` streams accept and return
        unicode, or natural strings, "hello world", unless this value is
        explicitly set to ``False``.  In that case, the attached stream
        interfaces are bytes-only, b"hello world".
    :param encoding_errors: Same meaning as :meth:`codecs.Codec.encode`.
        Default value is ``strict``.
    :param force_binary: When ``True``, the encoding specified is
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
    :param line_mode: When ``True``, the server does not send ``WILL SGA``
        or ``WILL ECHO`` during negotiation.  This keeps the client in NVT
        local (line) mode, where the client performs its own line editing
        and sends complete lines.  Default is ``False`` (kludge mode).
    :param term: Value returned for ``writer.get_extra_info('term')``
        until negotiated by TTYPE :rfc:`930`, or NAWS :rfc:`1572`.  Default value
        is ``'unknown'``.
    :param cols: Value returned for ``writer.get_extra_info('cols')``
        until negotiated by NAWS :rfc:`1572`. Default value is 80 columns.
    :param rows: Value returned for ``writer.get_extra_info('rows')``
        until negotiated by NAWS :rfc:`1572`. Default value is 25 rows.
    :param timeout: Causes clients to disconnect if idle for this duration,
        in seconds.  This ensures resources are freed on busy servers.  When
        explicitly set to ``False``, clients will not be disconnected for
        timeout. Default value is 300 seconds (5 minutes).
    :param connect_maxwait: If the remote end is not compliant, or
        otherwise confused by our demands, the shell continues anyway after the
        greater of this value has elapsed.  A client that is not answering
        option negotiation will delay the start of the shell by this amount.
    :param compression: MCCP compression policy.  ``None`` (default)
        passively accepts compression if requested by the client.  ``True``
        advertises MCCP2/MCCP3 during advanced negotiation.  ``False``
        rejects all compression offers.
    :param limit: The buffer limit for the reader stream.
    :param ssl: An :class:`ssl.SSLContext` for TLS-encrypted connections
        (TELNETS, :rfc:`855` over TLS).  When provided, the server performs a
        TLS handshake before any telnet data is exchanged.  ``None`` (default)
        creates a plain TCP server.
    :param tls_auto: When truthy and *ssl* is provided, the server accepts
        both TLS and plain telnet clients on the same port.  A ``float``
        value sets the number of seconds to wait for a TLS ClientHello
        (``0x16``) before assuming a plain telnet connection; ``True`` uses
        a default of 1.0 second.  TLS clients send ClientHello immediately;
        plain telnet clients typically wait for the server to speak first,
        so the timeout distinguishes the two.  ``False`` or ``0`` (default)
        disables auto-detection.  Requires *ssl* to be an
        :class:`ssl.SSLContext`.

    :return: A :class:`Server` instance that wraps the asyncio.Server
        and provides access to connected client protocols via
        :meth:`Server.wait_for_client` and :attr:`Server.clients`.
    """
    if tls_auto and ssl is None:
        raise ValueError("tls_auto requires an ssl SSLContext")
    # normalize True → 1.0
    if tls_auto is True:
        tls_auto = 1.0

    protocol_factory = protocol_factory or TelnetServer

    telnet_server = Server(None)

    def _make_telnet_protocol() -> asyncio.Protocol:
        protocol: asyncio.Protocol
        if issubclass(protocol_factory, TelnetServer):
            protocol = protocol_factory(
                shell=shell,
                encoding=encoding,
                encoding_errors=encoding_errors,
                force_binary=force_binary,
                never_send_ga=never_send_ga,
                line_mode=line_mode,
                connect_maxwait=connect_maxwait,
                compression=compression,
                limit=limit,
                term=term,
                cols=cols,
                rows=rows,
                timeout=timeout,
            )
        elif issubclass(protocol_factory, server_base.BaseServer):
            protocol = protocol_factory(
                shell=shell,
                encoding=encoding,
                encoding_errors=encoding_errors,
                force_binary=force_binary,
                never_send_ga=never_send_ga,
                line_mode=line_mode,
                connect_maxwait=connect_maxwait,
                limit=limit,
            )
        else:
            protocol = protocol_factory()
        telnet_server._register_protocol(protocol)
        return protocol

    if tls_auto:
        assert ssl is not None

        def factory() -> asyncio.Protocol:
            return _TLSAutoDetectProtocol(ssl, _make_telnet_protocol, tls_auto)

        telnet_server._server = await asyncio.get_running_loop().create_server(factory, host, port)
    else:

        def factory() -> asyncio.Protocol:
            return _make_telnet_protocol()

        telnet_server._server = await asyncio.get_running_loop().create_server(
            factory, host, port, ssl=ssl
        )

    return telnet_server


async def _sigterm_handler(server: Server, _log: logging.Logger) -> None:
    logger.info("SIGTERM received, closing server.")

    # This signals the completion of the server.wait_closed() Future,
    # allowing the main() function to complete.
    server.close()


def parse_server_args(
    extra_args_fn: Optional[Callable[[argparse.ArgumentParser], None]] = None,
) -> Dict[str, Any]:
    """
    Parse command-line arguments for telnet server.

    :param extra_args_fn: Optional callback to add extra arguments to the parser
        before parsing.  Used by ``telnetlib3-fingerprint-server`` to inject
        ``--data-dir``.
    """
    # Extract arguments after '--' for PTY program before argparse sees them
    argv = sys.argv[1:]
    pty_args = []
    if PTY_SUPPORT and "--" in argv:
        idx = argv.index("--")
        pty_args = argv[idx + 1 :]
        argv = argv[:idx]

    parser = argparse.ArgumentParser(
        description="Telnet protocol server", formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument("host", nargs="?", default=_config.host, help="bind address")
    parser.add_argument("port", nargs="?", type=int, default=_config.port, help="bind port")
    parser.add_argument(
        "--compression",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="MCCP compression: --compression to advertise, --no-compression to reject, "
        "omit to passively accept (default)",
    )
    parser.add_argument(
        "--connect-maxwait",
        type=float,
        default=_config.connect_maxwait,
        help="timeout for pending negotiation",
    )
    parser.add_argument("--encoding", default=_config.encoding, help="encoding name")
    parser.add_argument(
        "--force-binary",
        action="store_true",
        default=_config.force_binary,
        help="force binary transmission",
    )
    parser.add_argument(
        "--line-mode",
        action="store_true",
        default=_config.line_mode,
        help="keep clients in NVT line mode by not sending WILL SGA or "
        "WILL ECHO during negotiation.  Clients perform their own line "
        "editing and send complete lines.  Also sets cooked PTY mode "
        "when combined with --pty-exec.",
    )
    parser.add_argument("--logfile", default=_config.logfile, help="filepath")
    parser.add_argument("--logfmt", default=_config.logfmt, help="log format")
    parser.add_argument("--loglevel", default=_config.loglevel, help="level name")
    parser.add_argument(
        "--never-send-ga",
        action="store_true",
        default=_config.never_send_ga,
        help="never send IAC GA (Go-Ahead). Default sends GA when SGA is "
        "not negotiated, which is correct for MUD clients but may "
        "confuse some other clients.",
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
        # Hidden backwards-compat: --pty-raw was the default since 2.5,
        # keep it as a silent no-op so existing scripts don't break.
        parser.add_argument("--pty-raw", action="store_true", default=False, help=argparse.SUPPRESS)
    parser.add_argument(
        "--robot-check",
        action="store_true",
        default=_config.robot_check,
        help="check if client can render wide unicode (rejects bots)",
    )
    parser.add_argument(
        "--shell",
        default=_config.shell,
        type=accessories.function_lookup,
        help="module.function_name",
    )
    parser.add_argument(
        "--ssl-certfile",
        default=None,
        metavar="PATH",
        help="path to PEM certificate file for TLS (enables TELNETS)",
    )
    parser.add_argument(
        "--ssl-keyfile", default=None, metavar="PATH", help="path to PEM private key file for TLS"
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
    parser.add_argument("--timeout", default=_config.timeout, help="idle disconnect (0 disables)")
    parser.add_argument(
        "--tls-auto",
        type=float,
        nargs="?",
        const=1.0,
        default=0,
        metavar="SECONDS",
        help="accept both TLS and plain telnet on the same port;"
        " value is seconds to wait for TLS ClientHello before"
        " assuming plain telnet (default: 1.0, requires --ssl-certfile)",
    )
    if extra_args_fn is not None:
        extra_args_fn(parser)
    result = vars(parser.parse_args(argv))
    result["pty_args"] = pty_args if PTY_SUPPORT else None
    # --pty-raw is a hidden no-op (raw is now the default);
    # --line-mode opts out of raw mode and suppresses WILL SGA/ECHO.
    result.pop("pty_raw", None)
    result["pty_raw"] = not result.get("line_mode", False)
    if not PTY_SUPPORT:
        result["pty_exec"] = None
        result["pty_fork_limit"] = 0
        result["pty_raw"] = False

    # Auto-enable force_binary for any non-ASCII encoding that uses high-bit bytes.
    enc_key = result["encoding"].lower().replace("-", "_")
    if enc_key not in ("us_ascii", "ascii"):
        result["force_binary"] = True

    # Build SSLContext from --ssl-certfile / --ssl-keyfile
    ssl_certfile = result.pop("ssl_certfile", None)
    ssl_keyfile = result.pop("ssl_keyfile", None)
    tls_auto = result.pop("tls_auto", False)
    if ssl_certfile:
        ctx = ssl_module.SSLContext(ssl_module.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(ssl_certfile, keyfile=ssl_keyfile)
        result["ssl"] = ctx
    else:
        result["ssl"] = None
    result["tls_auto"] = tls_auto

    return result


async def run_server(
    host: str = _config.host,
    port: int = _config.port,
    loglevel: str = _config.loglevel,
    logfile: Optional[str] = _config.logfile,
    logfmt: str = _config.logfmt,
    shell: Callable[..., Any] = _config.shell,
    encoding: Union[str, bool] = _config.encoding,
    force_binary: bool = _config.force_binary,
    timeout: int = _config.timeout,
    connect_maxwait: float = _config.connect_maxwait,
    pty_exec: Optional[str] = _config.pty_exec,
    pty_args: Optional[List[str]] = _config.pty_args,
    pty_raw: bool = _config.pty_raw,
    robot_check: bool = _config.robot_check,
    pty_fork_limit: int = _config.pty_fork_limit,
    status_interval: int = _config.status_interval,
    never_send_ga: bool = _config.never_send_ga,
    line_mode: bool = _config.line_mode,
    compression: Optional[bool] = None,
    protocol_factory: Optional[Type[asyncio.Protocol]] = None,
    ssl: Optional[ssl_module.SSLContext] = None,
    tls_auto: Union[bool, float] = False,
) -> None:
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
        from .server_pty_shell import make_pty_shell

        shell = make_pty_shell(pty_exec, pty_args, raw_mode=pty_raw)

    # Wrap shell with guards if enabled
    if robot_check or pty_fork_limit:
        from .guard_shells import ConnectionCounter, busy_shell
        from .guard_shells import robot_check as do_robot_check
        from .guard_shells import robot_shell

        counter = ConnectionCounter(pty_fork_limit) if pty_fork_limit else None
        inner_shell = shell

        async def guarded_shell(
            reader: Union[TelnetReader, TelnetReaderUnicode],
            writer: Union[TelnetWriter, TelnetWriterUnicode],
        ) -> None:
            try:
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
            except (ConnectionResetError, BrokenPipeError, EOFError):
                logger.debug(
                    "Connection lost in guarded_shell: %s",
                    writer.get_extra_info("peername", "unknown"),
                )

        shell = guarded_shell

    # log all function arguments.
    _locals = locals()
    _cfg_mapping = ", ".join((f"{field}={{{field}}}" for field in CONFIG._fields)).format(**_locals)
    logger.debug("Server configuration: %s", _cfg_mapping)

    loop = asyncio.get_running_loop()

    # bind
    server = await create_server(
        host,
        port,
        shell=shell,
        protocol_factory=protocol_factory,
        encoding=encoding,
        force_binary=force_binary,
        never_send_ga=never_send_ga,
        line_mode=line_mode,
        connect_maxwait=connect_maxwait,
        compression=compression,
        timeout=timeout,
        ssl=ssl,
        tls_auto=tls_auto,
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


def main() -> None:
    """Entry point for telnetlib3-server command."""
    asyncio.run(run_server(**parse_server_args()))


if __name__ == "__main__":  # pragma: no cover
    main()
