#!/usr/bin/env python3
"""
Telnet Client API for the 'telnetlib3' python package.
"""
# std imports
import argparse
import asyncio
import codecs
import struct
import sys
import os

# local imports
from telnetlib3 import accessories
from telnetlib3 import client_base

__all__ = ("TelnetClient", "TelnetTerminalClient", "open_connection")


class TelnetClient(client_base.BaseClient):
    """
    Telnet client that supports all common options.

    This class is useful for automation, it appears to be a virtual terminal to
    the remote end, but does not require an interactive terminal to run.
    """

    #: On :meth:`send_env`, the value of 'LANG' will be 'C' for binary
    #: transmission.  When encoding is specified (utf8 by default), the LANG
    #: variable must also contain a locale, this value is used, providing a
    #: full default LANG value of 'en_US.utf8'
    DEFAULT_LOCALE = "en_US"

    def __init__(
        self,
        term="unknown",
        cols=80,
        rows=25,
        tspeed=(38400, 38400),
        xdisploc="",
        *args,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self._extra.update(
            {
                "charset": kwargs["encoding"] or "",
                # for our purposes, we only send the second part (encoding) of our
                # 'lang' variable, CHARSET negotiation does not provide locale
                # negotiation; this is better left to the real LANG variable
                # negotiated as-is by send_env().
                #
                # So which locale should we represent? Rather than using the
                # locale.getpreferredencoding() method, we provide a deterministic
                # class value DEFAULT_LOCALE (en_US), derive and modify as needed.
                "lang": (
                    "C"
                    if not kwargs["encoding"]
                    else self.DEFAULT_LOCALE + "." + kwargs["encoding"]
                ),
                "cols": cols,
                "rows": rows,
                "term": term,
                "tspeed": "{},{}".format(*tspeed),
                "xdisploc": xdisploc,
            }
        )

    def connection_made(self, transport):
        """Callback for connection made to server."""
        from telnetlib3.telopt import TTYPE, TSPEED, XDISPLOC, NEW_ENVIRON
        from telnetlib3.telopt import CHARSET, NAWS

        super().connection_made(transport)

        # Wire extended rfc callbacks for requests of
        # terminal attributes, environment values, etc.
        for opt, func in (
            (TTYPE, self.send_ttype),
            (TSPEED, self.send_tspeed),
            (XDISPLOC, self.send_xdisploc),
            (NEW_ENVIRON, self.send_env),
            (NAWS, self.send_naws),
            (CHARSET, self.send_charset),
        ):
            self.writer.set_ext_send_callback(opt, func)

        # Override the default handle_will method to detect when both sides support CHARSET
        original_handle_will = self.writer.handle_will

        def enhanced_handle_will(opt):
            result = original_handle_will(opt)

            # If this was a WILL CHARSET from the server, and we also have WILL CHARSET enabled,
            # log that both sides support CHARSET. The server should initiate the actual REQUEST.
            if (
                opt == CHARSET
                and self.writer.remote_option.enabled(CHARSET)
                and self.writer.local_option.enabled(CHARSET)
            ):
                self.log.debug(
                    "Both sides support CHARSET, ready for server to initiate REQUEST"
                )

            return result

        self.writer.handle_will = enhanced_handle_will

    def send_ttype(self):
        """Callback for responding to TTYPE requests."""
        return self._extra["term"]

    def send_tspeed(self):
        """Callback for responding to TSPEED requests."""
        return tuple(map(int, self._extra["tspeed"].split(",")))

    def send_xdisploc(self):
        """Callback for responding to XDISPLOC requests."""
        return self._extra["xdisploc"]

    def send_env(self, keys):
        """
        Callback for responding to NEW_ENVIRON requests.

        :param dict keys: Values are requested for the keys specified.
           When empty, all environment values that wish to be volunteered
           should be returned.
        :returns: dictionary of environment values requested, or an
            empty string for keys not available. A return value must be
            given for each key requested.
        :rtype: dict
        """
        env = {
            "LANG": self._extra["lang"],
            "TERM": self._extra["term"],
            "DISPLAY": self._extra["xdisploc"],
            "LINES": self._extra["rows"],
            "COLUMNS": self._extra["cols"],
        }
        return {key: env.get(key, "") for key in keys} or env

    def send_charset(self, offered):
        """
        Callback for responding to CHARSET requests.

        Simplified policy:

        - If client has explicit encoding that matches an offered charset, use it

        - If client has explicit encoding that isn't offered,

           - For Latin-1 (weak default), accept first viable offered encoding

           - For other explicit encodings, reject (keep client's choice)

        - If no explicit encoding preference, accept first viable offered encoding

        - If no viable encodings found, reject

        :param list offered: list of CHARSET options offered by server.
        :returns: character encoding agreed to be used, or "" to reject.
        :rtype: str
        """
        # Get client's desired encoding canonical name
        desired_name = None
        if self.default_encoding:
            try:
                desired_name = codecs.lookup(self.default_encoding).name
            except LookupError:
                # Unknown encoding, treat as no explicit preference
                pass

        # Find first viable offered encoding and check for exact match
        first_viable = None
        matched_offer = None

        for offer in offered:
            try:
                canon = codecs.lookup(offer).name

                # Record first viable encoding
                if first_viable is None:
                    first_viable = (offer, canon)

                # Check for exact match with desired encoding
                if desired_name and canon == desired_name:
                    matched_offer = (offer, canon)
                    break

            except LookupError:
                self.log.info(f"LookupError: encoding {offer} not available")
                continue

        # Decision logic:

        # Case 1: Found exact match for desired encoding
        if matched_offer:
            offer, canon = matched_offer
            self._extra["charset"] = canon
            self._extra["lang"] = self.DEFAULT_LOCALE + "." + canon
            self.log.debug(f"encoding negotiated: {offer}")
            return offer

        # Case 2: Has explicit encoding but not offered
        if desired_name:
            # Special case: Latin-1 is a weak default, accept first viable instead
            is_latin1 = desired_name in ("latin-1", "latin1", "iso8859-1", "iso-8859-1")
            if is_latin1 and first_viable:
                offer, canon = first_viable
                self._extra["charset"] = canon
                self._extra["lang"] = self.DEFAULT_LOCALE + "." + canon
                self.log.debug(f"encoding negotiated: {offer}")
                return offer

            # Otherwise reject - keep client's explicit encoding
            self.log.debug(
                f"Declining offered charsets {offered}; prefer {desired_name}"
            )
            return ""

        # Case 3: No explicit preference, use first viable
        if first_viable:
            offer, canon = first_viable
            self._extra["charset"] = canon
            self._extra["lang"] = self.DEFAULT_LOCALE + "." + canon
            self.log.debug(f"encoding negotiated: {offer}")
            return offer

        # Case 4: No viable encodings found
        self.log.warning(f"No suitable encoding offered by server: {offered}")
        return ""

    def send_naws(self):
        """
        Callback for responding to NAWS requests.

        :rtype: (int, int)
        :returns: client window size as (rows, columns).
        """
        return (self._extra["rows"], self._extra["cols"])

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
        may_encode = (
            (_outgoing_only and self.writer.outbinary)
            or (_incoming_only and self.writer.inbinary)
            or (_bidirectional and self.writer.outbinary and self.writer.inbinary)
        )

        if self.force_binary or may_encode:
            # The 'charset' value, initialized using keyword argument
            # default_encoding, may be re-negotiated later.  Only the CHARSET
            # negotiation method allows the server to select an encoding, so
            # this value is reflected here by a single return statement.
            return self._extra["charset"]
        return "US-ASCII"


class TelnetTerminalClient(TelnetClient):
    """Telnet client for sessions with a network virtual terminal (NVT)."""

    def send_naws(self):
        """
        Callback replies to request for window size, NAWS :rfc:`1073`.

        :rtype: (int, int)
        :returns: window dimensions by lines and columns
        """
        return self._winsize()

    def send_env(self, keys):
        """
        Callback replies to request for env values, NEW_ENVIRON :rfc:`1572`.

        :rtype: dict
        :returns: super class value updated with window LINES and COLUMNS.
        """
        env = super().send_env(keys)
        env["LINES"], env["COLUMNS"] = self._winsize()
        return env

    @staticmethod
    def _winsize():
        try:
            import fcntl
            import termios

            fmt = "hhhh"
            buf = "\x00" * struct.calcsize(fmt)
            val = fcntl.ioctl(sys.stdin.fileno(), termios.TIOCGWINSZ, buf)
            rows, cols, _, _ = struct.unpack(fmt, val)
            return rows, cols
        except (ImportError, IOError):
            # TODO: mock import error, or test on windows or other non-posix.
            return (
                int(os.environ.get("LINES", 25)),
                int(os.environ.get("COLUMNS", 80)),
            )


async def open_connection(
    host=None,
    port=23,
    *,
    client_factory=None,
    family=0,
    flags=0,
    local_addr=None,
    encoding="utf8",
    encoding_errors="replace",
    force_binary=False,
    term="unknown",
    cols=80,
    rows=25,
    tspeed=(38400, 38400),
    xdisploc="",
    shell=None,
    connect_minwait=2.0,
    connect_maxwait=3.0,
    waiter_closed=None,
    _waiter_connected=None,
    limit=None,
):
    """
    Connect to a TCP Telnet server as a Telnet client.

    :param str host: Remote Internet TCP Server host.
    :param int port: Remote Internet host TCP port.
    :param client_base.BaseClient client_factory: Client connection class
        factory.  When ``None``, :class:`TelnetTerminalClient` is used when
        *stdin* is attached to a terminal, :class:`TelnetClient` otherwise.
    :param int family: Same meaning as
        :meth:`asyncio.loop.create_connection`.
    :param int flags: Same meaning as
        :meth:`asyncio.loop.create_connection`.
    :param tuple local_addr: Same meaning as
        :meth:`asyncio.loop.create_connection`.
    :param str encoding: The default assumed encoding, or ``False`` to disable
        unicode support.  This value is used for decoding bytes received by and
        encoding bytes transmitted to the Server.  These values are preferred
        in response to NEW_ENVIRON :rfc:`1572` as environment value ``LANG``,
        and by CHARSET :rfc:`2066` negotiation.

        The server's attached ``reader, writer`` streams accept and return
        unicode, unless this value explicitly set ``False``.  In that case, the
        attached streams interfaces are bytes-only.
    :param str encoding_errors: Same meaning as :meth:`codecs.Codec.encode`.

    :param str term: Terminal type sent for requests of TTYPE, :rfc:`930` or as
        Environment value TERM by NEW_ENVIRON negotiation, :rfc:`1672`.
    :param int cols: Client window dimension sent as Environment value COLUMNS
        by NEW_ENVIRON negotiation, :rfc:`1672` or NAWS :rfc:`1073`.
    :param int rows: Client window dimension sent as Environment value LINES by
        NEW_ENVIRON negotiation, :rfc:`1672` or NAWS :rfc:`1073`.
    :param tuple tspeed: Tuple of client BPS line speed in form ``(rx, tx``)
        for receive and transmit, respectively.  Sent when requested by TSPEED,
        :rfc:`1079`.
    :param str xdisploc: String transmitted in response for request of
        XDISPLOC, :rfc:`1086` by server (X11).
    :param shell: A async function that is called after negotiation completes,
        receiving arguments ``(reader, writer)``.  The reader is a
        :class:`~.TelnetReader` instance, the writer is a
        :class:`~.TelnetWriter` instance.
    :param float connect_minwait: The client allows any additional telnet
        negotiations to be demanded by the server within this period of time
        before launching the shell.  Servers should assert desired negotiation
        on-connect and in response to 1 or 2 round trips.

        A server that does not make any telnet demands, such as a TCP server
        that is not a telnet server will delay the execution of ``shell`` for
        exactly this amount of time.
    :param float connect_maxwait: If the remote end is not complaint, or
        otherwise confused by our demands, the shell continues anyway after the
        greater of this value has elapsed.  A client that is not answering
        option negotiation will delay the start of the shell by this amount.

    :param int limit: The buffer limit for reader stream.
    :return (reader, writer): The reader is a :class:`~.TelnetReader`
        instance, the writer is a :class:`~.TelnetWriter` instance.
    """
    if client_factory is None:
        client_factory = TelnetClient
        if sys.platform != "win32" and sys.stdin.isatty():
            client_factory = TelnetTerminalClient

    def connection_factory():
        return client_factory(
            encoding=encoding,
            encoding_errors=encoding_errors,
            force_binary=force_binary,
            term=term,
            cols=cols,
            rows=rows,
            tspeed=tspeed,
            xdisploc=xdisploc,
            shell=shell,
            connect_minwait=connect_minwait,
            connect_maxwait=connect_maxwait,
            waiter_closed=waiter_closed,
            _waiter_connected=_waiter_connected,
            limit=limit,
        )

    transport, protocol = await asyncio.get_event_loop().create_connection(
        connection_factory,
        host,
        port,
        family=family,
        flags=flags,
        local_addr=local_addr,
    )

    await protocol._waiter_connected

    return protocol.reader, protocol.writer


async def run_client():
    """Command-line 'telnetlib3-client' entry point, via setuptools."""
    kwargs = _transform_args(_get_argument_parser().parse_args())
    config_msg = "Client configuration: {key_values}".format(
        key_values=accessories.repr_mapping(kwargs)
    )
    host = kwargs.pop("host")
    port = kwargs.pop("port")

    log = accessories.make_logger(
        name=__name__,
        loglevel=kwargs.pop("loglevel"),
        logfile=kwargs.pop("logfile"),
        logfmt=kwargs.pop("logfmt"),
    )
    log.debug(config_msg)

    # connect
    reader, writer = await open_connection(host, port, **kwargs)

    # repl loop
    await writer.protocol.waiter_closed


def _get_argument_parser():
    parser = argparse.ArgumentParser(
        description="Telnet protocol client",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("host", action="store", help="hostname")
    parser.add_argument("port", nargs="?", default=23, type=int, help="port number")
    parser.add_argument(
        "--term", default=os.environ.get("TERM", "unknown"), help="terminal type"
    )
    parser.add_argument("--loglevel", default="warn", help="log level")
    parser.add_argument(
        "--logfmt", default=accessories._DEFAULT_LOGFMT, help="log format"
    )
    parser.add_argument("--logfile", help="filepath")
    parser.add_argument(
        "--shell", default="telnetlib3.telnet_client_shell", help="module.function_name"
    )
    parser.add_argument("--encoding", default="utf8", help="encoding name")
    parser.add_argument("--speed", default=38400, type=int, help="connection speed")
    parser.add_argument(
        "--encoding-errors",
        default="replace",
        help="handler for encoding errors",
        choices=("replace", "ignore", "strict"),
    )

    parser.add_argument(
        "--force-binary", action="store_true", help="force encoding", default=True
    )
    parser.add_argument(
        "--connect-minwait", default=1.0, type=float, help="shell delay for negotiation"
    )
    parser.add_argument(
        "--connect-maxwait",
        default=4.0,
        type=float,
        help="timeout for pending negotiation",
    )
    return parser


def _transform_args(args):
    return {
        "host": args.host,
        "port": args.port,
        "loglevel": args.loglevel,
        "logfile": args.logfile,
        "logfmt": args.logfmt,
        "encoding": args.encoding,
        "tspeed": (args.speed, args.speed),
        "shell": accessories.function_lookup(args.shell),
        "term": args.term,
        "force_binary": args.force_binary,
        "encoding_errors": args.encoding_errors,
        "connect_minwait": args.connect_minwait,
    }


def main():
    asyncio.run(run_client())


if __name__ == "__main__":
    main()
