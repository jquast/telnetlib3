RFCs Implemented
================

* `rfc-727`_ "Telnet Logout Option," Apr 1977. **(1)**
* `rfc-779`_ "Telnet Send-Location Option", Apr 1981. **(1)**
* `rfc-854`_ "Telnet Protocol Specification", May 1983. **(2)**
* `rfc-855`_ "Telnet Option Specifications", May 1983. **(2)**
* `rfc-856`_ "Telnet Binary Transmission", May 1983.
* `rfc-857`_ "Telnet Echo Option", May 1983. **(2)**
* `rfc-858`_ "Telnet Suppress Go Ahead Option", May 1983. **(2)**
* `rfc-859`_ "Telnet Status Option", May 1983.
* `rfc-860`_ "Telnet Timing mark Option", May 1983. **(2)**
* `rfc-885`_ "Telnet End of Record Option", Dec 1983. **(1)**
* `rfc-1073`_, "Telnet Window Size Option", Oct 1988.
* `rfc-1079`_, "Telnet Terminal Speed Option", Dec 1988.
* `rfc-1091`_, "Telnet Terminal-Type Option", Feb 1989. **(2)**
* `rfc-1123`_, "Requirements for Internet Hosts", Oct 1989. **(2)**
* `rfc-1184`_, "Telnet Linemode Option (extended options)", Oct 1990.
* `rfc-1096`_, "Telnet X Display Location Option", Mar 1989.
* `rfc-1372`_, "Telnet Remote Flow Control Option", Oct 1992.
* `rfc-1408`_, "Telnet Environment Option", Jan 1993.
* `rfc-1571`_, "Telnet Environment Option Interoperability Issues", Jan 1994.
* `rfc-1572`_, "Telnet Environment Option", Jan 1994.
* `rfc-2066`_, "Telnet Charset Option", Jan 1997. **(1)**

**(1)**: Not implemented in BSD telnet (rare!)

**(2)**: Required by specification (complies!)

RFCs Not Implemented
====================

* `rfc-861`_, "Telnet Extended Options List", May 1983. describes a method of
  negotiating options after all possible 255 option bytes are exhausted by
  future implementations. This never happened (about 100 remain), it was
  perhaps, ambitious in thinking more protocols would incorporate Telnet (such
  as FTP did).
* `rfc-927`_, "TACACS_ User Identification Telnet Option", describes a method
  of identifying terminal clients by a 32-bit UUID, providing a form of
  'rlogin'.  This system, published in 1984, was designed for MILNET_ by BBN_,
  and the actual TACACS_ implementation is undocumented, though partially
  re-imagined by Cisco in `rfc-1492`_. Essentially, the user's credentials are
  forwarded to a TACACS_ daemon to verify that the client does in fact have
  access. The UUID is a form of an early Kerberos_ token.
* `rfc-933`_, "Output Marking Telnet Option", describes a method of sending
  banners, such as displayed on login, with an associated ID to be stored by
  the client. The server may then indicate at which time during the session
  the banner is relevant. This was implemented by Mitre_ for DOD installations
  that might, for example, display various levels of "TOP SECRET" messages
  each time a record is opened -- preferably on the top, bottom, left or right
  of the screen.
* `rfc-946`_, "Telnet Terminal Location Number Option", only known to be
  implemented at Carnegie Mellon University in the mid-1980's, this was a
  mechanism to identify a Terminal by ID, which would then be read and forwarded
  by gatewaying hosts. So that user traveling from host A -> B -> C appears as
  though his "from" address is host A in the system "who" and "finger" services.
  There exists more appropriate solutions, such as the "Report Terminal ID"
  sequences ``CSI + c`` and ``CSI + 0c`` for vt102, and ``ESC + z`` (vt52),
  which sends a terminal ID in-band as ASCII.
* `rfc-1041`_, "Telnet 3270 Regime Option", Jan 1988
* `rfc-1043`_, "Telnet Data Entry Terminal Option", Feb 1988
* `rfc-1097`_, "Telnet Subliminal-Message Option", Apr 1989
* `rfc-1143`_, "The Q Method of Implementing .. Option Negotiation", Feb 1990
* `rfc-1205`_, "5250 Telnet Interface", Feb 1991
* `rfc-1411`_, "Telnet Authentication: Kerberos_ Version 4", Jan 1993
* `rfc-1412`_, "Telnet Authentication: SPX"
* `rfc-1416`_, "Telnet Authentication Option"
* `rfc-2217`_, "Telnet Com Port Control Option", Oct 1997

Additional Resources
====================

These RFCs predate, or are superseded by, `rfc-854`_, but may be relevant for
study of the telnet protocol.

* `rfc-97`_ A First Cut at a Proposed Telnet Protocol
* `rfc-137`_ Telnet Protocol.
* `rfc-139`_ Discussion of Telnet Protocol.
* `rfc-318`_ Telnet Protocol.
* `rfc-328`_ Suggested Telnet Protocol Changes.
* `rfc-340`_ Proposed Telnet Changes.
* `rfc-393`_ Comments on TELNET Protocol Changes.
* `rfc-435`_ Telnet Issues.
* `rfc-513`_ Comments on the new Telnet Specifications.
* `rfc-529`_ A Note on Protocol Synch Sequences.
* `rfc-559`_ Comments on the new Telnet Protocol and its Implementation.
* `rfc-563`_ Comments on the RCTE Telnet Option.
* `rfc-593`_ Telnet and FTP Implementation Schedule Change.
* `rfc-595`_ Some Thoughts in Defense of the Telnet Go-Ahead.
* `rfc-596`_ Second Thoughts on Telnet Go-Ahead.
* `rfc-652`_ Telnet Output Carriage-Return Disposition Option.
* `rfc-653`_ Telnet Output Horizontal Tabstops Option.
* `rfc-654`_ Telnet Output Horizontal Tab Disposition Option.
* `rfc-655`_ Telnet Output Formfeed Disposition Option.
* `rfc-656`_ Telnet Output Vertical Tabstops Option.
* `rfc-657`_ Telnet Output Vertical Tab Disposition Option.
* `rfc-658`_ Telnet Output Linefeed Disposition.
* `rfc-659`_ Announcing Additional Telnet Options.
* `rfc-698`_ Telnet Extended ASCII Option.
* `rfc-701`_ August, 1974, Survey of New-Protocol Telnet Servers.
* `rfc-702`_ September, 1974, Survey of New-Protocol Telnet Servers.
* `rfc-703`_ July, 1975, Survey of New-Protocol Telnet Servers.
* `rfc-718`_ Comments on RCTE from the TENEX Implementation Experience.
* `rfc-719`_ Discussion on RCTE.
* `rfc-726`_ Remote Controlled Transmission and Echoing Telnet Option.
* `rfc-728`_ A Minor Pitfall in the Telnet Protocol.
* `rfc-732`_ Telnet Data Entry Terminal Option (Obsoletes: `rfc-731`_)
* `rfc-734`_ SUPDUP Protocol.
* `rfc-735`_ Revised Telnet Byte Macro Option (Obsoletes: `rfc-729`_, `rfc-736`_)
* `rfc-749`_ Telnet SUPDUP-Output Option.
* `rfc-818`_ The Remote User Telnet Service.

The following further describe the telnet protocol and various extensions of
related interest:

* "Telnet Protocol," MIL-STD-1782_, U.S. Department of Defense, May 1984.
* "Mud Terminal Type Standard," http://tintin.sourceforge.net/mtts/
* "Mud Client Protocol, Version 2.1," http://www.moo.mud.org/mcp/mcp2.html
* "Telnet Protocol in C-Kermit 8.0 and Kermit 95 2.0," http://www.columbia.edu/kermit/telnet80.html
* "Telnet Negotiation Concepts," http://lpc.psyc.eu/doc/concepts/negotiation
* "Telnet RFCs," http://www.omnifarious.org/~hopper/telnet-rfc.html"
* "Telnet Options", http://www.iana.org/assignments/telnet-options/telnet-options.xml

.. _rfc-97: https://www.rfc-editor.org/rfc/rfc97.txt
.. _rfc-137: https://www.rfc-editor.org/rfc/rfc137.txt
.. _rfc-139: https://www.rfc-editor.org/rfc/rfc139.txt
.. _rfc-318: https://www.rfc-editor.org/rfc/rfc318.txt
.. _rfc-328: https://www.rfc-editor.org/rfc/rfc328.txt
.. _rfc-340: https://www.rfc-editor.org/rfc/rfc340.txt
.. _rfc-393: https://www.rfc-editor.org/rfc/rfc393.txt
.. _rfc-435: https://www.rfc-editor.org/rfc/rfc435.txt
.. _rfc-495: https://www.rfc-editor.org/rfc/rfc495.txt
.. _rfc-513: https://www.rfc-editor.org/rfc/rfc513.txt
.. _rfc-529: https://www.rfc-editor.org/rfc/rfc529.txt
.. _rfc-559: https://www.rfc-editor.org/rfc/rfc559.txt
.. _rfc-563: https://www.rfc-editor.org/rfc/rfc563.txt
.. _rfc-593: https://www.rfc-editor.org/rfc/rfc593.txt
.. _rfc-595: https://www.rfc-editor.org/rfc/rfc595.txt
.. _rfc-596: https://www.rfc-editor.org/rfc/rfc596.txt
.. _rfc-652: https://www.rfc-editor.org/rfc/rfc652.txt
.. _rfc-653: https://www.rfc-editor.org/rfc/rfc653.txt
.. _rfc-654: https://www.rfc-editor.org/rfc/rfc654.txt
.. _rfc-655: https://www.rfc-editor.org/rfc/rfc655.txt
.. _rfc-656: https://www.rfc-editor.org/rfc/rfc656.txt
.. _rfc-657: https://www.rfc-editor.org/rfc/rfc657.txt
.. _rfc-658: https://www.rfc-editor.org/rfc/rfc658.txt
.. _rfc-659: https://www.rfc-editor.org/rfc/rfc659.txt
.. _rfc-698: https://www.rfc-editor.org/rfc/rfc698.txt
.. _rfc-701: https://www.rfc-editor.org/rfc/rfc701.txt
.. _rfc-702: https://www.rfc-editor.org/rfc/rfc702.txt
.. _rfc-703: https://www.rfc-editor.org/rfc/rfc703.txt
.. _rfc-718: https://www.rfc-editor.org/rfc/rfc718.txt
.. _rfc-719: https://www.rfc-editor.org/rfc/rfc719.txt
.. _rfc-726: https://www.rfc-editor.org/rfc/rfc726.txt
.. _rfc-727: https://www.rfc-editor.org/rfc/rfc727.txt
.. _rfc-728: https://www.rfc-editor.org/rfc/rfc728.txt
.. _rfc-729: https://www.rfc-editor.org/rfc/rfc729.txt
.. _rfc-731: https://www.rfc-editor.org/rfc/rfc731.txt
.. _rfc-732: https://www.rfc-editor.org/rfc/rfc732.txt
.. _rfc-734: https://www.rfc-editor.org/rfc/rfc734.txt
.. _rfc-735: https://www.rfc-editor.org/rfc/rfc735.txt
.. _rfc-736: https://www.rfc-editor.org/rfc/rfc736.txt
.. _rfc-749: https://www.rfc-editor.org/rfc/rfc749.txt
.. _rfc-779: https://www.rfc-editor.org/rfc/rfc779.txt
.. _rfc-818: https://www.rfc-editor.org/rfc/rfc818.txt
.. _rfc-854: https://www.rfc-editor.org/rfc/rfc854.txt
.. _rfc-855: https://www.rfc-editor.org/rfc/rfc855.txt
.. _rfc-856: https://www.rfc-editor.org/rfc/rfc856.txt
.. _rfc-857: https://www.rfc-editor.org/rfc/rfc857.txt
.. _rfc-858: https://www.rfc-editor.org/rfc/rfc858.txt
.. _rfc-859: https://www.rfc-editor.org/rfc/rfc859.txt
.. _rfc-860: https://www.rfc-editor.org/rfc/rfc860.txt
.. _rfc-861: https://www.rfc-editor.org/rfc/rfc861.txt
.. _rfc-885: https://www.rfc-editor.org/rfc/rfc885.txt
.. _rfc-927: https://www.rfc-editor.org/rfc/rfc927.txt
.. _rfc-933: https://www.rfc-editor.org/rfc/rfc933.txt
.. _rfc-946: https://www.rfc-editor.org/rfc/rfc946.txt
.. _rfc-1041: https://www.rfc-editor.org/rfc/rfc1041.txt
.. _rfc-1043: https://www.rfc-editor.org/rfc/rfc1043.txt
.. _rfc-1073: https://www.rfc-editor.org/rfc/rfc1073.txt
.. _rfc-1079: https://www.rfc-editor.org/rfc/rfc1079.txt
.. _rfc-1091: https://www.rfc-editor.org/rfc/rfc1091.txt
.. _rfc-1096: https://www.rfc-editor.org/rfc/rfc1096.txt
.. _rfc-1097: https://www.rfc-editor.org/rfc/rfc1097.txt
.. _rfc-1123: https://www.rfc-editor.org/rfc/rfc1123.txt
.. _rfc-1143: https://www.rfc-editor.org/rfc/rfc1143.txt
.. _rfc-1184: https://www.rfc-editor.org/rfc/rfc1184.txt
.. _rfc-1205: https://www.rfc-editor.org/rfc/rfc1205.txt
.. _rfc-1372: https://www.rfc-editor.org/rfc/rfc1372.txt
.. _rfc-1408: https://www.rfc-editor.org/rfc/rfc1408.txt
.. _rfc-1411: https://www.rfc-editor.org/rfc/rfc1411.txt
.. _rfc-1412: https://www.rfc-editor.org/rfc/rfc1412.txt
.. _rfc-1416: https://www.rfc-editor.org/rfc/rfc1416.txt
.. _rfc-1492: https://www.rfc-editor.org/rfc/rfc1492.txt
.. _rfc-1571: https://www.rfc-editor.org/rfc/rfc1571.txt
.. _rfc-1572: https://www.rfc-editor.org/rfc/rfc1572.txt
.. _rfc-2066: https://www.rfc-editor.org/rfc/rfc2066.txt
.. _rfc-2217: https://www.rfc-editor.org/rfc/rfc2217.txt
.. _MIL-STD-1782: http://www.everyspec.com/MIL-STD/MIL-STD-1700-1799/MIL-STD-1782_6678/
.. _Mitre: https://mitre.org
.. _MILNET: https://en.wikipedia.org/wiki/MILNET
.. _BBN: https://en.wikipedia.org/wiki/BBN_Technologies
.. _Kerberos: https://en.wikipedia.org/wiki/Kerberos_%28protocol%29
.. _TACACS: https://en.wikipedia.org/wiki/TACACS
