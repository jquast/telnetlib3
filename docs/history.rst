0.5.0
  * change: TelnetStream.writing boolean now replaced by methods
    ``pause_writing`` and ``resume_writing``, matching basic
    transports.
  * change: IAC GA is no longer sent by the default shell,
    :class:`telnetlib3.telsh.TelSh`.  This is intentionally not RFC
    compliant: such feature coerces apple's netcat.c into an illegal
    IAC response state (responding ``IAC BINARY``).

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
