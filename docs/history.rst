History
=======

2.3.0
  * bugfix: repeat "socket.send() raised exception." exceptions
  * bugfix: server incorrectly accepted ``DO TSPEED`` and ``DO SNDLOC``
    with ``WILL`` responses. These are client-only options per :rfc:`1079`
    and :rfc:`779`; the server now correctly rejects them.
  * bugfix: ``LINEMODE DO FORWARDMASK`` subnegotiation no longer raises
    ``NotImplementedError``; the mask is accepted (logged only).
  * bugfix: echo doubling in ``--pty-exec`` without ``--pty-raw`` (linemode).
  * bugfix: missing LICENSE.txt in sdist file.
  * bugfix: GMCP, MSDP, and MSSP decoding now uses ``--encoding`` when set,
    falling back to latin-1 for non-UTF-8 bytes instead of lossy replacement.
  * bugfix: ``NEW_ENVIRON SEND`` with empty payload now correctly
    interpreted as "send all" per :rfc:`1572`.
  * new: :mod:`telnetlib3.mud` module with encode/decode functions for
    GMCP (option 201), MSDP (option 69), and MSSP (option 70) MUD telnet
    protocols.
  * new: :meth:`~telnetlib3.stream_writer.TelnetWriter.send_gmcp`,
    :meth:`~telnetlib3.stream_writer.TelnetWriter.send_msdp`, and
    :meth:`~telnetlib3.stream_writer.TelnetWriter.send_mssp` methods for sending MUD protocol
    data, with corresponding :meth:`~telnetlib3.stream_writer.TelnetWriter.handle_gmcp`,
    :meth:`~telnetlib3.stream_writer.TelnetWriter.handle_msdp`, and
    :meth:`~telnetlib3.stream_writer.TelnetWriter.handle_mssp` callbacks.
  * new: ``connect_timeout`` arguments for client and ``--connect-timeout``
    Client CLI argument, :ghissue:`30`.
  * new: ``telnetlib3-fingerprint-server`` CLI with extended ``NEW_ENVIRON``
    for fingerprinting of connected clients.
  * new: ``telnetlib3-fingerprint`` CLI for fingerprinting the given remote
    server, probing telnet option support and capturing banners.
  * enhancement: reversed ``WILL``/``DO`` for directional options (e.g. ``WILL
    NAWS`` from server, ``DO TTYPE`` from client) now gracefully refused with
    ``DONT``/``WONT`` instead of raising ``ValueError``.
  * enhancement: ``NEW_ENVIRON SEND`` and response logging improved --
    ``SEND (all)`` / ``env send: (empty)`` instead of raw byte dumps.
  * enhancement: ``telnetlib3-fingerprint`` now probes MSDP and MSSP options
    and captures MSSP server status data in session output.
  * new: ``--always-will``, ``--always-do``, ``--scan-type``, ``--mssp-wait``,
    ``--banner-quiet-time``, ``--banner-max-wait`` options for ``telnetlib3-fingerprint``.

2.2.0
  * bugfix: workaround for Microsoft Telnet client crash on
    ``SB NEW_ENVIRON SEND``, :ghissue:`24`. Server now defers ``DO
    NEW_ENVIRON`` until TTYPE cycling identifies the client, skipping it
    entirely for MS Telnet (ANSI/VT100).
  * bugfix: in handling of LINEMODE FORWARDMASK command bytes.
  * bugfix: SLC fingerprinting byte handling.
  * bugfix: send IAC GA (Go-Ahead) after prompts when SGA is not negotiated.
    Fixes hanging for MUD clients like Mudlet. PTY shell uses a 500ms idle
    timer. Use ``--never-send-ga`` to suppress like old behavior.
  * performance: with 'smarter' negotiation, default ``connect_maxwait``
    reduced from 4.0s to 1.5s.
  * performance: both client and server protocol data_received methods
    have approximately ~50x throughput improvement in bulk data transfers.
  * new: :class:`~telnetlib3.server.Server` class returned by
    :func:`~telnetlib3.server.create_server` with
    :meth:`~telnetlib3.server.Server.wait_for_client` method and
    :attr:`~telnetlib3.server.Server.clients` property for tracking connected
    clients.
  * new: :meth:`~telnetlib3.stream_writer.TelnetWriter.wait_for` and
    :meth:`~telnetlib3.stream_writer.TelnetWriter.wait_for_condition` methods for waiting on
    telnet option negotiation state.
  * new: :mod:`telnetlib3.sync` module with blocking (non-asyncio) APIs:
    :class:`~telnetlib3.sync.TelnetConnection` for clients,
    :class:`~telnetlib3.sync.BlockingTelnetServer` for servers.
  * new: :mod:`~telnetlib3.server_pty_shell` module and demonstrating
    ``telnetlib3-server --pty-exec`` CLI argument and related ``--pty-raw``
    server CLI option for raw PTY mode, used by most programs that handle their
    own terminal I/O.
  * new: :mod:`~telnetlib3.guard_shells` module with ``--robot-check`` and
    ``--pty-fork-limit`` CLI arguments for connection limiting and bot
    detection.
  * new: :mod:`~telnetlib3.fingerprinting` module for telnet client
    identification and capability probing.
  * new: ``--send-environ`` client CLI option to control which environment
    variables are sent via NEW_ENVIRON. Default no longer includes HOME or
    SHELL.

2.0.8
 * bugfix: object has no attribute '_extra' :ghissue:`100`

2.0.7
 * bugfix: respond WILL CHARSET with DO CHARSET

2.0.6
 * bugfix: corrected CHARSET protocol client/server role behavior :ghissue:`59`
 * bugfix: allow ``--force-binary`` and ``--encoding`` to be combined to prevent
   long ``encoding failed after 4.00s`` delays in ``telnetlib3-server`` with
   non-compliant clients, :ghissue:`74`.
 * bugfix: reduce ``telnetlib3-client`` connection delay, session begins as
   soon as TTYPE and either NEW_ENVIRON or CHARSET negotiation is completed.
 * bugfix: remove `'NoneType' object has no attribute 'is_closing'` message
   on some types of closed connections
 * bugfix: further improve ``telnetlib3-client`` performance, capable of
   11.2 Mbit/s or more.
 * bugfix: more gracefully handle unsupported SB STATUS codes.
 * feature: ``telnetlib3-client`` now negotiates terminal resize events.

2.0.5
 * feature: legacy `telnetlib.py` from Python 3.11 now redistributed,
   note change to project `LICENSE.txt` file.
 * feature: Add :meth:`~telnetlib3.stream_reader.TelnetReader.readuntil_pattern` :ghissue:`92` by
   :ghuser:`agicy`
 * feature: Add :meth:`~telnetlib3.stream_writer.TelnetWriter.wait_closed`
   async method in response to :ghissue:`82`.
 * bugfix: README Examples do not work :ghissue:`81`
 * bugfix: `TypeError: buf expected bytes, got <class 'str'>` on client timeout
   in :class:`~telnetlib3.server.TelnetServer`, :ghissue:`87`
 * bugfix: Performance issues with client protocol under heavy load,
   demonstrating server `telnet://1984.ws` now documented in README.
 * bugfix: annoying `socket.send() raised exception` repeating warning,
   :ghissue:`89`.
 * bugfix: legacy use of get_event_loop, :ghissue:`85`.
 * document: about encoding and force_binary in response to :ghissue:`90`
 * feature: add tests to source distribution, :ghissue:`37`
 * test coverage increased by ~20%

2.0.4
 * change: stop using setuptools library to get current software version

2.0.3
 * bugfix: NameError: when debug=True is used with asyncio.run, :ghissue:`75`

2.0.2
 * bugfix: NameError: name 'sleep' is not defined in stream_writer.py

2.0.1
 * bugfix: "write after close" is disregarded, caused many errors logged in socket.send()
 * bugfix: in accessories.repr_mapping() about using shlex.quote on non-str,
   `TypeError: expected string or bytes-like object, got 'int'`
 * bugfix: about fn_encoding using repr() on :class:`~telnetlib3.stream_reader.TelnetReaderUnicode`
 * bugfix: TelnetReader.is_closing() raises AttributeError
 * deprecation: ``TelnetReader.close`` and ``TelnetReader.connection_closed``
   emit warning, use :meth:`~telnetlib3.stream_reader.TelnetReader.at_eof` and
   :meth:`~telnetlib3.stream_reader.TelnetReader.feed_eof` instead.
 * deprecation: the ``loop`` argument is no longer accepted by
   :class:`~telnetlib3.stream_reader.TelnetReader`.
 * enhancement: Add Generic Mud Communication Protocol support :ghissue:`63` by
   :ghuser:`gtaylor`!
 * change: :class:`~telnetlib3.stream_reader.TelnetReader` and
   :class:`~telnetlib3.stream_writer.TelnetWriter` no longer derive
   from :class:`asyncio.StreamReader` and :class:`asyncio.StreamWriter`, this
   fixes some TypeError in signatures and runtime

2.0.0
 * change: Support Python 3.9, 3.10, 3.11. Drop Python 3.6 and earlier, All code
   and examples have been updated to the new-style PEP-492 syntax.
 * change: the ``loop``, ``event_loop``, and ``log`` arguments are no longer accepted by
   any class initializers.
 * note: This release has a known memory leak when using the ``_waiter_connected`` and
   ``_waiter_closed`` arguments to Client or Shell class initializers, please do
   not use them. A replacement "wait_for_negotiation" awaitable is planned for a
   future release.
 * enhancement: Add COM-PORT-OPTION subnegotiation support :ghissue:`57` by
   :ghuser:`albireox`

1.0.4
 * bugfix: NoneType error on EOF/Timeout, introduced in previous
   version 1.0.3, :ghissue:`51` by :ghuser:`zofy`.

1.0.3
  * bugfix: circular reference between transport and protocol, :ghissue:`43` by
    :ghuser:`fried`.

1.0.2
  * add --speed argument to telnet client :ghissue:`35` by :ghuser:`hughpyle`.

1.0.1
  * add python3.7 support, drop python 3.4 and earlier, :ghissue:`33` by
    :ghuser:`AndrewNelis`.

1.0.0
  * First general release for standard API: Instead of encouraging twisted-like
    override of protocol methods, we provide a "shell" callback interface,
    receiving argument pairs (reader, writer).

0.5.0
  * bugfix: linemode MODE is now acknowledged.
  * bugfix: default stream handler sends 80 x 24 in cols x rows, not 24 x 80.
  * bugfix: waiter_closed future on client defaulted to wrong type.
  * bugfix: telnet shell (TelSh) no longer paints over final exception line.

0.4.0
  * bugfix: cannot connect to IPv6 address as client.
  * change: TelnetClient.CONNECT_DEFERED class attribute renamed DEFERRED.
    Default value changed to 50ms from 100ms.
  * change: TelnetClient.waiter renamed to TelnetClient.waiter_closed.
  * enhancement: TelnetClient.waiter_connected future added.

0.3.0
  * bugfix: cannot bind to IPv6 address :ghissue:`5`.
  * enhancement: Futures waiter_connected, and waiter_closed added to server.
  * change: TelSh.feed_slc merged into TelSh.feed_byte as slc_function keyword.
  * change: TelnetServer.CONNECT_DEFERED class attribute renamed DEFERRED.
    Default value changed to 50ms from 100ms.
  * enhancement: Default TelnetServer.PROMPT_IMMEDIATELY = False ensures prompt
    is not displayed until negotiation is considered final.  It is no longer
    "aggressive".
  * enhancement: TelnetServer.pause_writing and resume_writing callback wired.
  * enhancement: TelSh.pause_writing and resume_writing methods added.

0.2.4
  * bugfix: pip installation issue :ghissue:`8`.

0.2
  * enhancement: various example programs were included in this release.

0.1
  * Initial release.
