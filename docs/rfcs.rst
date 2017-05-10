RFCs
====

Implemented
-----------

* :rfc:`727`, "Telnet Logout Option," Apr 1977.
* :rfc:`779`, "Telnet Send-Location Option", Apr 1981.
* :rfc:`854`, "Telnet Protocol Specification", May 1983.
* :rfc:`855`, "Telnet Option Specifications", May 1983.
* :rfc:`856`, "Telnet Binary Transmission", May 1983.
* :rfc:`857`, "Telnet Echo Option", May 1983.
* :rfc:`858`, "Telnet Suppress Go Ahead Option", May 1983.
* :rfc:`859`, "Telnet Status Option", May 1983.
* :rfc:`860`, "Telnet Timing mark Option", May 1983.
* :rfc:`885`, "Telnet End of Record Option", Dec 1983.
* :rfc:`1073`, "Telnet Window Size Option", Oct 1988.
* :rfc:`1079`, "Telnet Terminal Speed Option", Dec 1988.
* :rfc:`1091`, "Telnet Terminal-Type Option", Feb 1989.
* :rfc:`1096`, "Telnet X Display Location Option", Mar 1989.
* :rfc:`1123`, "Requirements for Internet Hosts", Oct 1989.
* :rfc:`1184`, "Telnet Linemode Option (extended options)", Oct 1990.
* :rfc:`1372`, "Telnet Remote Flow Control Option", Oct 1992.
* :rfc:`1408`, "Telnet Environment Option", Jan 1993.
* :rfc:`1571`, "Telnet Environment Option Interoperability Issues", Jan 1994.
* :rfc:`1572`, "Telnet Environment Option", Jan 1994.
* :rfc:`2066`, "Telnet Charset Option", Jan 1997.

Not Implemented
---------------

* :rfc:`861`, "Telnet Extended Options List", May 1983. describes a method of
  negotiating options after all possible 255 option bytes are exhausted by
  future implementations. This never happened (about 100 remain), it was
  perhaps, ambitious in thinking more protocols would incorporate Telnet (such
  as FTP did).
* :rfc:`927`, "TACACS_ User Identification Telnet Option", describes a method
  of identifying terminal clients by a 32-bit UUID, providing a form of
  'rlogin'.  This system, published in 1984, was designed for MILNET_ by BBN_,
  and the actual TACACS_ implementation is undocumented, though partially
  re-imagined by Cisco in :rfc:`1492`. Essentially, the user's credentials are
  forwarded to a TACACS_ daemon to verify that the client does in fact have
  access. The UUID is a form of an early Kerberos_ token.
* :rfc:`933`, "Output Marking Telnet Option", describes a method of sending
  banners, such as displayed on login, with an associated ID to be stored by
  the client. The server may then indicate at which time during the session
  the banner is relevant. This was implemented by Mitre_ for DOD installations
  that might, for example, display various levels of "TOP SECRET" messages
  each time a record is opened -- preferably on the top, bottom, left or right
  of the screen.
* :rfc:`946`, "Telnet Terminal Location Number Option", only known to be
  implemented at Carnegie Mellon University in the mid-1980's, this was a
  mechanism to identify a Terminal by ID, which would then be read and
  forwarded by gatewaying hosts. So that user traveling from host A -> B -> C
  appears as though his "from" address is host A in the system "who" and
  "finger" services.  There exists more appropriate solutions, such as the
  "Report Terminal ID" sequences ``CSI + c`` and ``CSI + 0c`` for vt102, and
  ``ESC + z`` (vt52), which sends a terminal ID in-band as ASCII.
* :rfc:`1041`, "Telnet 3270 Regime Option", Jan 1988
* :rfc:`1043`, "Telnet Data Entry Terminal Option", Feb 1988
* :rfc:`1097`, "Telnet Subliminal-Message Option", Apr 1989
* :rfc:`1143`, "The Q Method of Implementing .. Option Negotiation", Feb 1990
* :rfc:`1205`, "5250 Telnet Interface", Feb 1991
* :rfc:`1411`, "Telnet Authentication: Kerberos_ Version 4", Jan 1993
* :rfc:`1412`, "Telnet Authentication: SPX"
* :rfc:`1416`, "Telnet Authentication Option"
* :rfc:`2217`, "Telnet Com Port Control Option", Oct 1997

Additional Resources
--------------------

These RFCs predate, or are superseded by, :rfc:`854`, but may be relevant for
study of the telnet protocol.

* :rfc:`97` A First Cut at a Proposed Telnet Protocol
* :rfc:`137` Telnet Protocol.
* :rfc:`139` Discussion of Telnet Protocol.
* :rfc:`318` Telnet Protocol.
* :rfc:`328` Suggested Telnet Protocol Changes.
* :rfc:`340` Proposed Telnet Changes.
* :rfc:`393` Comments on TELNET Protocol Changes.
* :rfc:`435` Telnet Issues.
* :rfc:`513` Comments on the new Telnet Specifications.
* :rfc:`529` A Note on Protocol Synch Sequences.
* :rfc:`559` Comments on the new Telnet Protocol and its Implementation.
* :rfc:`563` Comments on the RCTE Telnet Option.
* :rfc:`593` Telnet and FTP Implementation Schedule Change.
* :rfc:`595` Some Thoughts in Defense of the Telnet Go-Ahead.
* :rfc:`596` Second Thoughts on Telnet Go-Ahead.
* :rfc:`652` Telnet Output Carriage-Return Disposition Option.
* :rfc:`653` Telnet Output Horizontal Tabstops Option.
* :rfc:`654` Telnet Output Horizontal Tab Disposition Option.
* :rfc:`655` Telnet Output Formfeed Disposition Option.
* :rfc:`656` Telnet Output Vertical Tabstops Option.
* :rfc:`657` Telnet Output Vertical Tab Disposition Option.
* :rfc:`658` Telnet Output Linefeed Disposition.
* :rfc:`659` Announcing Additional Telnet Options.
* :rfc:`698` Telnet Extended ASCII Option.
* :rfc:`701` August, 1974, Survey of New-Protocol Telnet Servers.
* :rfc:`702` September, 1974, Survey of New-Protocol Telnet Servers.
* :rfc:`703` July, 1975, Survey of New-Protocol Telnet Servers.
* :rfc:`718` Comments on RCTE from the TENEX Implementation Experience.
* :rfc:`719` Discussion on RCTE.
* :rfc:`726` Remote Controlled Transmission and Echoing Telnet Option.
* :rfc:`728` A Minor Pitfall in the Telnet Protocol.
* :rfc:`732` Telnet Data Entry Terminal Option (Obsoletes: :rfc:`731`)
* :rfc:`734` SUPDUP Protocol.
* :rfc:`735` Revised Telnet Byte Macro Option (Obsoletes: :rfc:`729`,
  :rfc:`736`)
* :rfc:`749` Telnet SUPDUP-Output Option.
* :rfc:`818` The Remote User Telnet Service.

The following further describe the telnet protocol and various extensions of
related interest:

* "Telnet Protocol," MIL-STD-1782_, U.S. Department of Defense, May 1984.
* "Mud Terminal Type Standard," http://tintin.sourceforge.net/mtts/
* "Mud Client Protocol, Version 2.1," http://www.moo.mud.org/mcp/mcp2.html
* "Telnet Protocol in C-Kermit 8.0 and Kermit 95 2.0," http://www.columbia.edu/kermit/telnet80.html
* "Telnet Negotiation Concepts," http://lpc.psyc.eu/doc/concepts/negotiation
* "Telnet RFCs," http://www.omnifarious.org/~hopper/telnet-rfc.html"
* "Telnet Options", http://www.iana.org/assignments/telnet-options/telnet-options.xml

.. _MIL-STD-1782: http://www.everyspec.com/MIL-STD/MIL-STD-1700-1799/MIL-STD-1782_6678/
.. _Mitre: https://mitre.org
.. _MILNET: https://en.wikipedia.org/wiki/MILNET
.. _BBN: https://en.wikipedia.org/wiki/BBN_Technologies
.. _Kerberos: https://en.wikipedia.org/wiki/Kerberos_%28protocol%29
.. _TACACS: https://en.wikipedia.org/wiki/TACACS
