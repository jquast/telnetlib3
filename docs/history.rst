0.2.5
  * bugfix: cannot bind to IPv6 address :ghissue:`5`.
  * enhancement: Futures waiter_connected, waiter_telopt, waiter_encoding,
    waiter_closed added to server.
  * change: TelnetServer.connected renamed .waiter_connected

0.2.4
  * bugfix: pip installation issue :ghissue:`8`.
  * new: TelnetServer.connected property is a Future set when first connected.

0.2
  * enhancement: various example programs were included in this release.

0.1
  * Initial release.
