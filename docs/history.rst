History
=======
1.0.4
 * bugfix a NoneType error on EOF/Timeout, introduced in previous
   version 1.0.3, :ghissue:`51` by :ghuser:`zofy`.

1.0.3
  * bugfix circular reference between transport and protocol, :ghissue:`43` by
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
