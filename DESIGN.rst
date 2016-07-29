Design/TODO
===========

Design items


reduce
------

outer telnetlib3-server and telnetlib3-client and examples should connect
as exit(main(\*\*parse_args(sys.argv))), the _transform_args() function is
rather shoe-horned, main() should declare keywords


wait_for?
---------

We need a way to wish to wait for a state. For example, our shell might await
until local_echo is False, or remote_option[ECHO] is True. A function wait_for,
receiving a function that returns True when state is met, will be called back
continuously after each block of data received containing an IAC command byte,
but the boiler code simply returns the waiter.

This should allow us to spray the client with feature requests, and await the
completion of their negotiation, especially for things like LINEMODE that might
have many state changes, this allows asyncio to solve the complex "awaiting
many future states in parallel" event loop easily

BaseTelnetProtocol
------------------

base_client.py and base_server.py actually share the same ABC
base_protocol.py, they are almost mirror images of one another,
which is pretty great, actually, so they can be reduced to
BaseTelnetProtocol.


On Linemode
-----------

How do we write a server which suggests a matrix of preferred linemode
negotiated with client, or client negotiated towards server?  As a server, we
simply honor whatever is requested, which may be wrong for the server shell
interface designed.

- LINEMODE compliance needs a lot of work.
  - possibly, we remove LINEMODE support entirely. I only know of one client,
    BSD telnet, that is capable of negotiating -- this is the C code from which
    our implementation was derived!
  - callbacks on TelnetServer needed for requesting/replying to mode settings
  - the SLC abstractions and 'slc_simul' mode is difficult for the API.
  - There are many edge cases of SLC negotiation outlined in the RFC, how
    comprehensive are our tests, and how well is our SLC working?
  - IAC-SB-LINEMODE-DO-FORWARDMASK is unhandled, raises NotImplementedError

TelnetWriter and TelnetServer
-----------------------------

feed_byte called by telnet server should be a coroutine
receiving data by send. It should yield out-of-bound values, None otherwise?
'is_oob', or 'slc_received', etc.?  We're still considering ... the state still
requires tracking, but this would turn multiple function calls into a .send()
into generator, better for state loops or bandwidth, maybe?

handle_xon resumes writing in a way that is not obvious -- we should
be using the true 'pause_writing' and 'resume_writing' methods of our
base protocol.  The given code was written before these methods became
available in asyncio (then, tulip).  We need to accommodate the new
availabilities.

On STATUS rfc
-------------
We've seen everything negotiate fine, but what exactly are we expected to do
when the distant end's concept of our negotiation STATUS disagrees with our
own? Match theirs, should we re-negotiate or re-affirm misunderstood values?
The RFC is not very clear.

- _receive_status(self, buf) response to STATUS does not *honor* given state
   values. only a non-compliant distant end would cause such a condition. so
   it is decided to leave it as "conflict report only, no action always"

SLC flush
---------

- SLC flushin/flushout attributes are not honored.  Not entirely sure
  how to handle these two values with asyncio yet.



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

Others
------

It should be said as historical source code, BSD 2.11's telnet source of UCLA
and `NCSA Telnet`_ client of Univ. of IL for MacOS is most notable. There are also
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

.. _Atlantis: http://www.riverdark.net/atlantis/
.. _NCSA Telnet: https://en.wikipedia.org/wiki/NCSA_Telnet
.. _SyncTerm: http://syncterm.bbsdev.net/
.. _`TinTin++`: http://tintin.sourceforge.net/
.. _examples: https://github.com/jquast/telnetlib3/tree/master/examples
.. _github: https://github.com/jquast/telnetlib3
.. _iTerm2: http://www.iterm2.com/
.. _mtelnet: http://mt32.bbses.info/
.. _mystic: http://www.mysticbbs.com/about.html
.. _netrunner: http://www.mysticbbs.com/downloads.html
.. _pip: http://www.pip-installer.org/en/latest/installing.html
.. _python: https://www.python.org
.. _rxvt: http://rxvt.sourceforge.net/
.. _sixteencolors.net: http://www.sixteencolors.net
.. _sockatmark(3): http://netbsd.gw.com/cgi-bin/man-cgi?sockatmark+3
.. _synchronet: http://www.synchro.net/ 
.. _tehmaze: https://github.com/tehmaze
.. _xterm: http://invisible-island.net/xterm/


  for communicating with any telnet server and the keyboard & screen. Most
  notably, it provides a ``--cp437`` argument that allows connecting to
  telnet BBS systems from any posix shell, that otherwise would require
  a DOS Emulating program SyncTerm_, mtelnet_, netrunner_. Instead, these
  systems may be used with a standard terminal emulator, such as xterm_,
  rxvt_, or iTerm2_.

  Some telnet destinations:

  * htc.zapto.org: Supports UTF8 or CP437 encoding (enthral).
  * 1984.ws: Supports UTF8 or CP437 encoding (`x/84`_).
  * nethack.alt.org: Supports latin1, CP437, or UTF8 encoding (dgamelaunch).
  * blackflag.acid.org: CP437 encoding only, requires 80x24 window (mystic_).
  * bbs.pharcyde.org: CP437 encoding only, requires 80x24 window (synchronet_).



It is hosted on github_.  Currently in development stage, feedback is
encouraged. Feel free to make use of fork, pull and Issues services to
report any bugs, grievances, or enhancements.


.. _x/84: http://pypi.python.org/pypi/x84 
