About
=====

telnetlib3 is a Telnet Client and Server Protocol library for python.

It is hosted on github_.  Currently in development stage, feedback is
encouraged. Feel free to make use of fork, pull and Issues services to
report any bugs, grievances, or enhancements.

This project uses the asyncio_ module of python 3.4, available
on pypi for python 3.3, for which this is currently targeted.

Example scripts are provided that make use of the protocol.

Installing
==========

1. Install python_ 3

2. Install pip_

3. Ensure pip is up-to-date::

    pip install --upgrade pip

4. Install telnetlib3::

    pip install telnetlib3

Scripts
=======

* telnet-client_: This provides a simple interactive shell for terminals
  for communicating with any telnet server and the keyboard & screen. Most
  notably, it provides a ``--cp437`` argument that allows connecting to
  telnet BBS systems from any posix shell, that otherwise would require
  a DOS Emulating program SyncTerm_, mtelnet_, netrunner_. Instead, these
  systems may be used with a standard terminal emulator, such as xterm_,
  rxvt_, or iTerm2_. Some example telnet destinations,

  * htc.zapto.org: Supports UTF8 or CP437 encoding (enthral).
  * 1984.ws: Supports UTF8 or CP437 encoding (`x/84`_).
  * nethack.alt.org: Supports latin1, CP437, or UTF8 encoding (dgamelaunch).
  * blackflag.acid.org: DOS-based CP437 bbs, requires a 80x24 window (mystic_).
  * bbs.pharcyde.org: DOS-based CP437 bbs, requires a 80x24 window (synchronet_).

* telnet-server_: This provides a simple cmd-line interface (telsh_) for
  interactively toggling and displaying telnet session parameters. This serves
  as an example of a basic prompting server with which commands may be issued.
* telnet-talker_: An example multi-user telnet server shell that is basically
  a simple chat system, sometimes called a talker_.

telsh
=====

In addition to remote line editing as described below, a pure-python shell,
*telsh* is provided to allow toggling of server options and session parameters.
In this way, it provides a suitable interface for testing telnet client
capabilities.

It is only in the interest of this project to provide enough shell-like
capabilities to demonstrate remote line editing and an extensible environment
for session introspection. An example of this is assigning a new value to
CHARSET, toggling in and outbinary, thereby enabling UTF8 input/output, etc.

UTF8
====

CHARSET (`RFC 2066`_) specifies a codepage, not an encoding. At the time, this
was more or less limited to specifying the codepage used to display bytes of the
range 127 through 255.  Unimplemented in BSD client, and generally found
implemented only in recent MUD client (Atlantis_) and servers. Most common
values are: ASCII, UTF8, BIG5, and LATIN1.

The default preferred encoding for clients that negotiate BINARY but not
CHARSET, such as the BSD client, is defined by the TelnetServer keyword
argument ``default_encoding`` ('UTF8' by default).

The example shell *telsh* allows changing encoding on the fly by setting the
'CHARSET' session environment value at the *telsh* command prompt by issuing
command::

    set CHARSET=UTF8

Setting binary for only a single direction ('outbinary' or 'inbinary') is
supported. Client support of one does not immediately toggle the other, it
must be negotiated both ways for full UTF8 input and output.

Some clients (`TinTin++`_) incorrectly negotiation either directions (WILL,
DO/WONT, DONT) as a single option, causing only one reply for a request of
either 'outbinary' or 'inbinary' for which it always declines, only once, for
either request (Even when configured for UTF8).

CP437
=====

Additionally, a contrib.cp437 module is included (authored by tehmaze_) which
translates output meant to be translated by DOS Emulating programs to their
comparable UTF-8 font. This is used by argument *--cp437* of the telnet-client_
program.

Some bulletin-board systems will send extended ascii characters (such as those
used by 

Telnet
======

The Telnet protocol is over 40 years old and still in use today. Telnet predates
TCP, and was used over a wide array of transports, especially on academic and
military systems. Nearly all computer networking that interacted with human
interfaces was done using the Telnet protocol prior to the mass-adoption of
the World Wide Web in the mid 1990's, when SSH became more commonplace.

Naturally, Telnet as a code project inevitably must handle a wide variety of
connecting clients and hosts, due to limitations of their networking Transport
, Terminals, their drivers, and host operating systems.

This implementation aims to implement only those capabilities "found in the
wild", and includes, or does not include, mechanisms that are suitable only
for legacy or vendor-implemented options. It even makes one of its own: the
encoding' used in binary mode is the value replied by the CHARSET negotation
(`RFC 2066`_).



Remote LineMode
---------------

This project is the only known Server-side implementation of *Special Linemode
Character* (SLC) negotiation and *Remote line editing* (`RFC 1184`_), other than
BSD telnet, which was used as a guide for the bulk of this python implementation.

Remote line editing is a comprehensive approach to providing responsive,
low-latency output of characters received over slow network links, allowing
incomplete lines to be buffered, while still providing remote editing
facilities, such as backspace, kill line, etc.

The Server and Client agree on a series of Special Linemode Character (SLC)
function values, to agree on the keyboard characters used for Backspace,
Interrupt Process (``^C``), Repaint (``^R``), Erase Word (``^W``), etc.

Kludge Mode
-----------

In kludge mode, SLC characters are simulated for remote editing, provide an
almost readline-like experience for all telnet clients, except those that
perform only local editing, which are unaffected.

The sequence sent by server, ``WILL-SGA``, ``WILL-ECHO`` enables "kludge
mode", a form of line mode editing that is compatible with all minimally
implemented telnet clients. This is the most frequent implementation used by
Windows 98 telnet, SyncTerm_, netrunner_, or `TinTin++`_ to provide
character-at-a-time editing.

Consider that kludge mode provides no way to determine which bytes, received at
any indeterminate time, of any indeterminate length, or none at all, are
received as the result of which input characters sent.

Accordingly, with Suppress Go-Ahead (``SGA``) enabled, there can be any
indeterminable state: (1) the remote program is hung, (2) receiving and/or
processing, (3) has responded with output but not yet received by transport,
and (4) has received some, but not yet all output by transport.

This is detrimental to a user experience with character-at-a-time processing,
as a user cannot know whether the input was legal, ignored, or not yet replied
to, causing some frustration over high latency links.

Go-Ahead
--------

The ``IAC-GA`` signal would seemingly be of little use over today's
bi-directional TCP protocol and virtual terminal emulators -- its original
purpose was to coordinate transmission on half-duplex protocols and terminals.

Only a few 1970-era hosts (``AMES-67``, ``UCLA-CON``) require the ``IAC-GA``
signal.  For this reason, this server takes the modern recommendation of
suppressing the ``IAC-GA`` signal (``IAC-WILL-SGA``) **by default**; those
clients wishing to make use of the ``IAC-GA`` signal must explicitly request
``IAC-DONT-SGA`` to enable the ``IAC-GA`` signal.

The ``IAC-GA`` signal has been recently restored for character-at-a-time servers,
such as the competition nethack server alt.nethack.org, targeted at client
scripts that play using AI decision-making routines.

Local Line Mode
---------------

Unless otherwise negotiated, the specification describes Telnet's default mode
as half-duplex, local line editing. This most basic "dummy" mode is modeled
after a Teletype 33, which runs in "half-duplex" mode.

A Telnet implementation attached to 7-bit ASCII teletype may implement the
Telnet protocol by hardware circuit, or by minimal changes to their terminal
line drivers: when the connecting CPU is without MMU or process control, an
IAC interpreter or hardware device could be "interrupted" when the 8th bit is
set high, "Out of band" in regards to 7-bit terminals, the receipt of value
255 indicates that the byte following it ``Is-A-Command`` (IAC).

Default Telnet Mode
^^^^^^^^^^^^^^^^^^^

  * Each end transmits only 7-bit ASCII, (except as used in the interpreter).
  * A server's prompt must be followed by the 'Go-Ahead' (``IAC-GA``) command.
  * Client signals end of input (send) by CR, LF (Carriage Return, Linefeed).

"Synch" Mechanism, not supported
--------------------------------

A supervisor connecting a (7-bit) teletype to a telnet (8-bit) data line would
simply pipe the streams together by the 7 bits; The teletypist may press
'BREAK' at any time to signal a control line: the supervisor then enters
Telnet Synch" mode by sending an "Urgent" mechanism, and ceases printing data
received on the transport.

A user could then instruct "Abort Output" (``IAC-AO``), "Interrupt Process"
(``IAC-IP``), or others, and then presumably return to normal processing.

Consider the description of a PDP-10 session in `RFC 139`_ (May 1971), presented
here as a simple unix session:

    1. Teletype sends command input::

          find /usr -name 'telop.c'<CR>

    2. Server begins output -- perhaps, after some minutes of pause,
       many rows of 'Permission Denied'. Meanwhile, the user has already
       filled his teletype's input buffer, and later deciding to abort the
       previous program::

          ed /usr/local/s^t/tel^t^c

At this point, however, the half-dupex Teletype cannot transmit any input.

The only way to signal the attention of the supervisor, which is currently
blocking the half-duplex transmission with output (having not yet received
``IAC-GA``), is by a special line signal wired separately from the teletype
keyboard.  This is the ``BREAK`` or ``ATTN`` key.

The terminal driver may then signal the 'supervisor', which then sends ``INS``
(`RFC 139`_). Although the teletype is capable of "flushing" its input buffer,
it does not flush control codes. Remaining control codes from the teletype
(``^t^t^c``) continues to the remote end, but is discarded by that end, until
the Data-Mark (``IAC-DM``) is sent by the supervisor.

This ensures the ``^t`` and ``^c`` characters are not received by the remote
program.

TCP Implementation
^^^^^^^^^^^^^^^^^^

In the TCP implementation of telnet, where presumably a half-duplex terminal
may still interconnect, the ``INS`` marker referenced in pre-TCP documents is,
instead, marked by sending the TCP Urgent option::

    socket.send(IAC, socket.MSG_OOB).

The value of the byte does not seem to matter, can be of any length, and can
continue sending ``socket.MSG_OOB`` (presumably, along with the remaining
``^t^t^c`` described previously). The BSD server sends only a single byte::

    /*
     * In 4.2 (and 4.3) systems, there is some question about
     * what byte in a sendOOB operation is the "OOB" data.
     * To make ourselves compatible, we only send ONE byte
     * out of band, the one WE THINK should be OOB
     (...)

All input is discarded by the ``IAC`` interpreter until ``IAC-DM`` is received;
including IAC or 8-bit commands. This was used to some abuse to "piggyback"
telnet by breaking out of IAC and into another "protocol" all together, and is
grieved about in `RFC 529`_::

      The Telnet SYNCH mechanism is being misused by attempting to give
      it meaning at two different levels of protocol.

The BSD client may be instructed to send this legacy mechanism by escaping and
using the command ``send synch``::

    telnet> send synch

This sends ``IAC`` marked ``MSG_OOB``, followed by ``DM``, not marked
``MSG_OOB``. The BSD server at this point would continue testing whether the
last received byte is still marked urgent, by continuing to test ``errorfds``
(third argument to select select, a modern implementation might rather use
`sockatmark(3)`_).

Abort Output
------------

BSD Telnet Server sets "Packet mode" with the pty driver::

        (void) ioctl(p, TIOCPKT, (char *)&on);

And when *TIOCPKT_FLUSHWRITE* is signaled by the pty driver::

        #define         TIOCPKT_FLUSHWRITE      0x02    /* flush packet */

Awaiting data buffered on the write transport is cleared; taking care to
ensure all IAC commands were sent in the *netclear()* algorithm, which also
sets the *neturgent* pointer.

Carriage Return
---------------

There are five supported signaling mechanisms for "send" or "end of line"
received by clients.  The default implementation supplies remote line editing
and callback of ``line_received`` with all client-supported carriage returns,
but may cause loss of data for implementors wishing to distinguish among them.

Namely, the difference between 'return' and 'enter' or raw file transfers.
Those implementors should directly override ``data_received``, or carefully
deriving their own implementations of ``editing_received`` and ``character_received``.

An overview of the primary callbacks and their interaction with carriage
returns are described below for those wishing to extend the basic remote line
editing or 'character-at-a-time' capabilities.

* ``CR LF`` (Carriage Return, Linefeed): The Telnet protocol defines the sequence
  ``CR LF`` to mean "end-of-line".  The default implementation strips *CL LF*,
  and fires ``line_received`` on receipt of ``CR`` byte.

* ``CR NUL`` (Carriage Return, Null): An interpretation of `RFC 854`_ may be that
  ``CR NUL`` should be sent when only a single ``CR`` is intended on a client and
  server host capable of distinguishing between ``CR`` and ``CR LF`` (return key
  vs enter key).  The default implementation strips ``CL NUL``, and fires
  ``line_received`` on receipt of ``CR`` byte.

* ``CR`` (Carriage Return): ``CR`` alone may be received, though a client is not
  RFC-complaint to do so.  The default implementation strips ``CR``, and fires
  ``line_received``.

* ``LF`` (Linefeed): ``LF`` alone may be received, though a client is not
  RFC-complaint to do so.  The default implementation strips ``LF``, and
  fires ``line_received``.

* ``IAC EOR`` (``Is-A-Command``, ``End-Of-Record``): In addition to
  line-oriented or character-oriented terminals, ``IAC EOR`` is used to delimit
  logical records (e.g., "screens") on Data Entry Terminals (DETs), or end of
  multi-line input on vendor-implemented and some MUD clients, or, together with
  BINARY, a mechanism to signal vendor-implemented newline outside of ``CR LF``
  during file transfers. MUD clients may read ``IAC EOR`` as meaning 'Go Ahead',
  marking the current line to be displayed as a "prompt", optionally not
  included in the client "history buffer". To register receipt of ``IAC EOR``,
  a client must call ``set_iac_callback(telopt.EOR, func)``.

RFCs Implemented
================

* `RFC 727`_ "Telnet Logout Option," Apr 1977. **(1)**
* `RFC 779`_ "Telnet Send-Location Option", Apr 1981. **(1)**
* `RFC 854`_ "Telnet Protocol Specification", May 1983. **(2)**
* `RFC 855`_ "Telnet Option Specifications", May 1983. **(2)**
* `RFC 856`_ "Telnet Binary Transmission", May 1983.
* `RFC 857`_ "Telnet Echo Option", May 1983. **(2)**
* `RFC 858`_ "Telnet Suppress Go Ahead Option", May 1983. **(2)**
* `RFC 859`_ "Telnet Status Option", May 1983.
* `RFC 860`_ "Telnet Timing mark Option", May 1983. **(2)**
* `RFC 885`_ "Telnet End of Record Option", Dec 1983. **(1)**
* `RFC 1073`_, "Telnet Window Size Option", Oct 1988.
* `RFC 1079`_, "Telnet Terminal Speed Option", Dec 1988.
* `RFC 1091`_, "Telnet Terminal-Type Option", Feb 1989. **(2)**
* `RFC 1123`_, "Requirements for Internet Hosts", Oct 1989. **(2)**
* `RFC 1184`_, "Telnet Linemode Option (extended options)", Oct 1990.
* `RFC 1096`_, "Telnet X Display Location Option", Mar 1989.
* `RFC 1372`_, "Telnet Remote Flow Control Option", Oct 1992.
* `RFC 1408`_, "Telnet Environment Option", Jan 1993.
* `RFC 1571`_, "Telnet Environment Option Interoperability Issues", Jan 1994.
* `RFC 1572`_, "Telnet Environment Option", Jan 1994.
* `RFC 2066`_, "Telnet Charset Option", Jan 1997. **(1)**

**(1)**: Not implemented in BSD telnet (rare!)

**(2)**: Required by specification (complies!)

RFCs Not Implemented
====================

* `RFC 861`_, "Telnet Extended Options List", May 1983. describes a method of
  negotiating options after all possible 255 option bytes are exhausted by
  future implementations. This never happened (about 100 remain), it was
  perhaps, ambitious in thinking more protocols would incorporate Telnet (such
  as FTP did).
* `RFC 927`_, "TACACS_ User Identification Telnet Option", describes a method of
  identifying terminal clients by a 32-bit UUID, providing a form of 'rlogin'.
  This system, published in 1984, was designed for MILNET_ by BBN_, and the
  actual TACACS_ implementation is undocumented, though partially re-imagined
  by Cisco in `RFC 1492`_. Essentially, the user's credentials are forwarded to a
  TACACS_ daemon to verify that the client does in fact have access. The UUID is
  a form of an early Kerberos_ token.
* `RFC 933`_, "Output Marking Telnet Option", describes a method of sending
  banners", such as displayed on login, with an associated ID to be stored by
  the client. The server may then indicate at which time during the session
  the banner is relevant. This was implemented by Mitre_ for DOD installations
  that might, for example, display various levels of "TOP SECRET" messages
  each time a record is opened -- preferably on the top, bottom, left or right
  of the screen.
* `RFC 946`_, "Telnet Terminal Location Number Option", only known to be
  implemented at Carnegie Mellon University in the mid-1980's, this was a
  mechanism to identify a Terminal by ID, which would then be read and forwarded
  by gatewaying hosts. So that user traveling from host A -> B -> C appears as
  though his "from" address is host A in the system "who" and "finger" services.
  There exists more appropriate solutions, such as the "Report Terminal ID"
  sequences ``CSI + c`` and ``CSI + 0c`` for vt102, and ``ESC + z`` (vt52),
  which sends a terminal ID in-band as ASCII.
* `RFC 1041`_, "Telnet 3270 Regime Option", Jan 1988
* `RFC 1043`_, "Telnet Data Entry Terminal Option", Feb 1988
* `RFC 1097`_, "Telnet Subliminal-Message Option", Apr 1989
* `RFC 1143`_, "The Q Method of Implementing .. Option Negotiation", Feb 1990
* `RFC 1205`_, "5250 Telnet Interface", Feb 1991
* `RFC 1411`_, "Telnet Authentication: Kerberos_ Version 4", Jan 1993
* `RFC 1412`_, "Telnet Authentication: SPX"
* `RFC 1416`_, "Telnet Authentication Option"
* `RFC 2217`_, "Telnet Com Port Control Option", Oct 1997

Additional Resources
====================

These RFCs predate, or are superseded by, `RFC 854`_, but may be relevant.

* `RFC 97`_ A First Cut at a Proposed Telnet Protocol
* `RFC 137`_ Telnet Protocol.
* `RFC 139`_ Discussion of Telnet Protocol.
* `RFC 318`_ Telnet Protocol.
* `RFC 328`_ Suggested Telnet Protocol Changes.
* `RFC 340`_ Proposed Telnet Changes.
* `RFC 393`_ Comments on TELNET Protocol Changes.
* `RFC 435`_ Telnet Issues.
* `RFC 513`_ Comments on the new Telnet Specifications.
* `RFC 529`_ A Note on Protocol Synch Sequences.
* `RFC 559`_ Comments on the new Telnet Protocol and its Implementation.
* `RFC 563`_ Comments on the RCTE Telnet Option.
* `RFC 593`_ Telnet and FTP Implementation Schedule Change.
* `RFC 595`_ Some Thoughts in Defense of the Telnet Go-Ahead.
* `RFC 596`_ Second Thoughts on Telnet Go-Ahead.
* `RFC 652`_ Telnet Output Carriage-Return Disposition Option.
* `RFC 653`_ Telnet Output Horizontal Tabstops Option.
* `RFC 654`_ Telnet Output Horizontal Tab Disposition Option.
* `RFC 655`_ Telnet Output Formfeed Disposition Option.
* `RFC 656`_ Telnet Output Vertical Tabstops Option.
* `RFC 657`_ Telnet Output Vertical Tab Disposition Option.
* `RFC 658`_ Telnet Output Linefeed Disposition.
* `RFC 659`_ Announcing Additional Telnet Options.
* `RFC 698`_ Telnet Extended ASCII Option.
* `RFC 701`_ August, 1974, Survey of New-Protocol Telnet Servers.
* `RFC 702`_ September, 1974, Survey of New-Protocol Telnet Servers.
* `RFC 703`_ July, 1975, Survey of New-Protocol Telnet Servers.
* `RFC 718`_ Comments on RCTE from the TENEX Implementation Experience.
* `RFC 719`_ Discussion on RCTE.
* `RFC 726`_ Remote Controlled Transmission and Echoing Telnet Option.
* `RFC 728`_ A Minor Pitfall in the Telnet Protocol.
* `RFC 732`_ Telnet Data Entry Terminal Option (Obsoletes: `RFC 731`_)
* `RFC 734`_ SUPDUP Protocol.
* `RFC 735`_ Revised Telnet Byte Macro Option (Obsoletes: `RFC 729`_, `RFC 736`_)
* `RFC 749`_ Telnet SUPDUP-Output Option.
* `RFC 818`_ The Remote User Telnet Service.
* "Telnet Protocol," MIL-STD-1782_, U.S. Department of Defense, May 1984.
* "Mud Terminal Type Standard," http://tintin.sourceforge.net/mtts/
* "Mud Client Protocol, Version 2.1," http://www.moo.mud.org/mcp/mcp2.html
* "Telnet Protocol in C-Kermit 8.0 and Kermit 95 2.0," http://www.columbia.edu/kermit/telnet80.html
* "Telnet Negotiation Concepts," http://lpc.psyc.eu/doc/concepts/negotiation
* "Telnet RFCs," http://www.omnifarious.org/~hopper/telnet-rfc.html"
* "Telnet Options", http://www.iana.org/assignments/telnet-options/telnet-options.xml


Others
------

It should be said as historical source code, BSD 2.11's telnet source of UCLA
and NCSA_Telnet_ client of Univ. of IL for MacOS is most notable. There are also
a few modern Telnet servers. Some modern Telnet clients support only kludge mode,
with the exception of MUD clients, which are often Linemode only. `TinTin++`_ is the
only known client to support both modes.

Finding RFC 495
---------------

`RFC 495`_, NIC #15371 "TELNET Protocol Specification." 1 May 1973,
A. McKenzie, lists the following attached documents, which are not available::

    [...] specifications for TELNET options which allow negotiation of:

            o binary transmission
            o echoing
            o reconnection
            o suppression of "Go Ahead"
            o approximate message size
            o use of a "timing mark"
            o discussion of status
            o extension of option code set

    These specifications have been prepared by Dave Walden (BBN-NET) with
    the help of Bernie Cosell, Ray Tomlinson (BBN-TENEX) and Bob Thomas;
    by Jerry Burchfiel (BBN-TENEX); and by David Crocker (ULCA-NMC).

If anybody can locate these documents, please forward them along.

.. _python: https://www.python.org
.. _pip: http://www.pip-installer.org/en/latest/installing.html
.. _github: https://github.com/jquast/telnetlib3
.. _asyncio: http://docs.python.org/3.4/library/asyncio.html
.. _examples: https://github.com/jquast/telnetlib3/tree/master/examples
.. _telnet-client: https://github.com/jquast/telnetlib3/tree/master/bin/telnet-client
.. _telnet-server: https://github.com/jquast/telnetlib3/tree/master/bin/telnet-server
.. _telnet-talker: https://github.com/jquast/telnetlib3/tree/master/bin/telnet-talker
.. _talker: https://en.wikipedia.org/wiki/Talker
.. _xterm: http://invisible-island.net/xterm/
.. _rxvt: http://rxvt.sourceforge.net/
.. _iTerm2: http://www.iterm2.com/
.. _SyncTerm: http://syncterm.bbsdev.net/
.. _mtelnet: http://mt32.bbses.info/
.. _`TinTin++`: http://tintin.sourceforge.net/
.. _Atlantis: http://www.riverdark.net/atlantis/
.. _netrunner: http://www.mysticbbs.com/downloads.html
.. _sixteencolors.net: http://www.sixteencolors.net
.. _tehmaze: https://github.com/tehmaze
.. _NCSA_Telnet: https://en.wikipedia.org/wiki/NCSA_Telnet
.. _MIL-STD-1782: http://www.everyspec.com/MIL-STD/MIL-STD-1700-1799/MIL-STD-1782_6678/
.. _RFC 97: https://www.rfc-editor.org/rfc/rfc97.txt
.. _RFC 137: https://www.rfc-editor.org/rfc/rfc137.txt
.. _RFC 139: https://www.rfc-editor.org/rfc/rfc139.txt
.. _RFC 318: https://www.rfc-editor.org/rfc/rfc318.txt
.. _RFC 328: https://www.rfc-editor.org/rfc/rfc328.txt
.. _RFC 340: https://www.rfc-editor.org/rfc/rfc340.txt
.. _RFC 393: https://www.rfc-editor.org/rfc/rfc393.txt
.. _RFC 435: https://www.rfc-editor.org/rfc/rfc435.txt
.. _RFC 495: https://www.rfc-editor.org/rfc/rfc495.txt
.. _RFC 513: https://www.rfc-editor.org/rfc/rfc513.txt
.. _RFC 529: https://www.rfc-editor.org/rfc/rfc529.txt
.. _RFC 559: https://www.rfc-editor.org/rfc/rfc559.txt
.. _RFC 563: https://www.rfc-editor.org/rfc/rfc563.txt
.. _RFC 593: https://www.rfc-editor.org/rfc/rfc593.txt
.. _RFC 595: https://www.rfc-editor.org/rfc/rfc595.txt
.. _RFC 596: https://www.rfc-editor.org/rfc/rfc596.txt
.. _RFC 652: https://www.rfc-editor.org/rfc/rfc652.txt
.. _RFC 653: https://www.rfc-editor.org/rfc/rfc653.txt
.. _RFC 654: https://www.rfc-editor.org/rfc/rfc654.txt
.. _RFC 655: https://www.rfc-editor.org/rfc/rfc655.txt
.. _RFC 656: https://www.rfc-editor.org/rfc/rfc656.txt
.. _RFC 657: https://www.rfc-editor.org/rfc/rfc657.txt
.. _RFC 658: https://www.rfc-editor.org/rfc/rfc658.txt
.. _RFC 659: https://www.rfc-editor.org/rfc/rfc659.txt
.. _RFC 698: https://www.rfc-editor.org/rfc/rfc698.txt
.. _RFC 701: https://www.rfc-editor.org/rfc/rfc701.txt
.. _RFC 702: https://www.rfc-editor.org/rfc/rfc702.txt
.. _RFC 703: https://www.rfc-editor.org/rfc/rfc703.txt
.. _RFC 718: https://www.rfc-editor.org/rfc/rfc718.txt
.. _RFC 719: https://www.rfc-editor.org/rfc/rfc719.txt
.. _RFC 726: https://www.rfc-editor.org/rfc/rfc726.txt
.. _RFC 727: https://www.rfc-editor.org/rfc/rfc727.txt
.. _RFC 728: https://www.rfc-editor.org/rfc/rfc728.txt
.. _RFC 729: https://www.rfc-editor.org/rfc/rfc729.txt
.. _RFC 731: https://www.rfc-editor.org/rfc/rfc731.txt
.. _RFC 732: https://www.rfc-editor.org/rfc/rfc732.txt
.. _RFC 734: https://www.rfc-editor.org/rfc/rfc734.txt
.. _RFC 735: https://www.rfc-editor.org/rfc/rfc735.txt
.. _RFC 736: https://www.rfc-editor.org/rfc/rfc736.txt
.. _RFC 749: https://www.rfc-editor.org/rfc/rfc749.txt
.. _RFC 779: https://www.rfc-editor.org/rfc/rfc779.txt
.. _RFC 818: https://www.rfc-editor.org/rfc/rfc818.txt
.. _RFC 854: https://www.rfc-editor.org/rfc/rfc854.txt
.. _RFC 855: https://www.rfc-editor.org/rfc/rfc855.txt
.. _RFC 856: https://www.rfc-editor.org/rfc/rfc856.txt
.. _RFC 857: https://www.rfc-editor.org/rfc/rfc857.txt
.. _RFC 858: https://www.rfc-editor.org/rfc/rfc858.txt
.. _RFC 859: https://www.rfc-editor.org/rfc/rfc859.txt
.. _RFC 860: https://www.rfc-editor.org/rfc/rfc860.txt
.. _RFC 861: https://www.rfc-editor.org/rfc/rfc861.txt
.. _RFC 885: https://www.rfc-editor.org/rfc/rfc885.txt
.. _RFC 927: https://www.rfc-editor.org/rfc/rfc927.txt
.. _RFC 933: https://www.rfc-editor.org/rfc/rfc933.txt
.. _RFC 946: https://www.rfc-editor.org/rfc/rfc946.txt
.. _RFC 1041: https://www.rfc-editor.org/rfc/rfc1041.txt
.. _RFC 1043: https://www.rfc-editor.org/rfc/rfc1043.txt
.. _RFC 1073: https://www.rfc-editor.org/rfc/rfc1073.txt
.. _RFC 1079: https://www.rfc-editor.org/rfc/rfc1079.txt
.. _RFC 1097: https://www.rfc-editor.org/rfc/rfc1097.txt
.. _RFC 1091: https://www.rfc-editor.org/rfc/rfc1091.txt
.. _RFC 1096: https://www.rfc-editor.org/rfc/rfc1096.txt
.. _RFC 1123: https://www.rfc-editor.org/rfc/rfc1123.txt
.. _RFC 1143: https://www.rfc-editor.org/rfc/rfc1143.txt
.. _RFC 1184: https://www.rfc-editor.org/rfc/rfc1184.txt
.. _RFC 1205: https://www.rfc-editor.org/rfc/rfc1205.txt
.. _RFC 1372: https://www.rfc-editor.org/rfc/rfc1372.txt
.. _RFC 1408: https://www.rfc-editor.org/rfc/rfc1408.txt
.. _RFC 1411: https://www.rfc-editor.org/rfc/rfc1411.txt
.. _RFC 1412: https://www.rfc-editor.org/rfc/rfc1412.txt
.. _RFC 1416: https://www.rfc-editor.org/rfc/rfc1416.txt
.. _RFC 1492: https://www.rfc-editor.org/rfc/rfc1492.txt
.. _RFC 1571: https://www.rfc-editor.org/rfc/rfc1571.txt
.. _RFC 1572: https://www.rfc-editor.org/rfc/rfc1572.txt
.. _RFC 2066: https://www.rfc-editor.org/rfc/rfc2066.txt
.. _RFC 2217: https://www.rfc-editor.org/rfc/rfc2217.txt
.. _Mitre: https://mitre.org
.. _MILNET: https://en.wikipedia.org/wiki/MILNET
.. _BBN: https://en.wikipedia.org/wiki/BBN_Technologies
.. _TACACS: https://en.wikipedia.org/wiki/TACACS
.. _Kerberos: https://en.wikipedia.org/wiki/Kerberos_%28protocol%29
.. _sockatmark(3): http://netbsd.gw.com/cgi-bin/man-cgi?sockatmark+3
.. _x/84: http://pypi.python.org/pypi/x84 
.. _mystic: http://www.mysticbbs.com/about.html
.. _synchronet: http://www.synchro.net/ 
