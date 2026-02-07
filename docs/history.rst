History
=======
2.3.0 *unreleased*
  * new: ``connect_timeout`` arguments for client and ``--connect-timeout`` Client CLI argument.
  * bugfix: missing LICENSE.txt in sdist file.

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
  * new: ``Server`` class returned by ``create_server()`` with
    ``wait_for_client()`` method and ``clients`` property for tracking
    connected clients.
  * new: ``TelnetWriter.wait_for()`` and ``wait_for_condition()``
    methods for waiting on telnet option negotiation state.
  * new: ``telnetlib3.sync`` module with blocking (non-asyncio) APIs:
    ``TelnetConnection`` for clients, ``BlockingTelnetServer`` for servers.
  * new: ``pty_shell`` module and demonstrating ``telnetlib3-server --pty-exec`` CLI argument
    and related ``--pty-raw`` server CLI option for raw PTY mode, used by most
    programs that handle their own terminal I/O.
  * new: ``guard_shells`` module with ``--robot-check`` and ``--pty-fork-limit``
    CLI arguments for connection limiting and bot detection.
  * new: ``fingerprinting`` module for telnet client identification and
    capability probing.
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
 * feature: Add `TelnetReader.readuntil_pattern` :ghissue:`92` by
   :ghuser:`agicy`
 * feature: Add `TelnetWriter.wait_closed` async method in response to
   :ghissue:`82`.
 * bugfix: README Examples do not work :ghissue:`81`
 * bugfix: `TypeError: buf expected bytes, got <class 'str'>` on client timeout
   in `TelnetServer`, :ghissue:`87`
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
 * bugfix: about fn_encoding using repr() on TelnetReaderUnicode
 * bugfix: TelnetReader.is_closing() raises AttributeError
 * deprecation: `TelnetReader.close` and `TelnetReader.connection_closed` emit
   warning, use `at_eof()` and `feed_eof()` instead.
 * deprecation: the ``loop`` argument are is no longer accepted by TelnetReader.
 * enhancement: Add Generic Mud Communication Protocol support :ghissue:`63` by
   :ghuser:`gtaylor`!
 * change: TelnetReader and TelnetWriter no longer derive from
   `asyncio.StreamReader` and `asyncio.StreamWriter`, this fixes some TypeError
   in signatures and runtime

2.0.0
 * change: Support Python 3.9, 3.10, 3.11. Drop Python 3.6 and earlier, All code
   and examples have been updated to the new-style PEP-492 syntax.
 * change: the ``loop``, ``event_loop``, and ``log`` arguments are no longer accepted to
   any class initializers.
 * note: This release has a known memory leak when using the ``_waiter_connected`` and
   ``_waiter_closed`` arguments to Client or Shell class initializers, please do
   not use them, A replacement "wait_for_negotiation" awaitable is planned for a
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
