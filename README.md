About
=====

telnetlib3.py is an ISC-licensed Telnet Server library.

Implemented using the "tulip" module of PEP 3156, the proposed Asynchronous I/O framework for Python 3.4.

Development is currently in progress, telnet server is near completion.

RFCs supported
--------------

RFC 854 "Telnet Protocol Specification", May 1983
RFC 855 "Telnet Option Specifications", May 1983
RFC 856 "Telnet Binary Transmission", May 1983
RFC 857 "Telnet Echo Option", May 1983
RFC 858 "Telnet Supress Go Ahead Option", May 1983
RFC 859 "Telnet Status Option", May 1983
RFC 860 "Telnet Timing mark Option", May 1983
RFC 885 "Telnet End of Record Option", Dec 1983
RFC 1073, "Telnet Window Size Option", Oct 1988
RFC 1079, "Telnet Terminal Speed Option", Dec 1988
RFC 1091, "Telnet Terminal-Type Option", Feb 1989
RFC 1096, "Telnet X Display Location Option", Mar 1989
RFC 1184, "Telnet Linemode Option (extended options)", Oct 1990
RFC 1123, "Requirements for Internet Hosts", Oct 1989
RFC 2066, "Telnet Charset Option", Jan 1997
RFC 1372, "Telnet Remote Flow Control Option", Oct 1992
RFC 1408, "Telnet Environment Option", Jan 1993
RFC 1571, "Telnet Environment Option Interoperability Issues", Jan 1994
RFC 1572, "Telnet Environment Option", Jan 1994

RFCs not supported
------------------

RFC 861 "Telnet Extended Options List", May 1983
RFC 927, "TACACS User Identification Telnet Option", Dec 1984
RFC 933, "Output Marking Telnet Option", Jan 1985
RFC 1041, "Telnet 3270 Regime Option", Jan 1988
RFC 1143, "The Q Method of Implementing .. Option Negotiation", Feb 1990
RFC 1097, "Telnet Subliminal-Message Option", Apr 1989
RFC 1205, "5250 Telnet Interface", Feb 1991
RFC 1411, "Telnet Authentication: Kerberos Version 4", Jan 1993
RFC 2217, "Telnet Com Port Control Option", Oct 1997

Status
------

TODO: Server 100% RFC-compliant
TODO: TelnetClient
TODO: nosetests
TODO: example MUD server
TODO: example wunderground.com client

Synch
-----

This is refering to the TCP Urgent flag, which is received using socket
option SO_OOBINLINE_


The Telnet Synch mechanism, much must sent with the TCP Urgent flag, is not
supported. This capability appears to be legacy and is not found in "the wild",
it can be sent with the bsd telnet client command, "send synch".

UTF-8
-----

CHARSET (rfc 2066) specifies a codepage, not an encoding. It is unimplemented
in bsd client, and generally found implemented only in recent MUD client and
servers, and possibly some vendor implementations. Where implemented, the
a client replying "UTF-8" has been found, and is presumed utf-8 encoded.

The default preferred encoding for clients that negotiate BINARY but not
CHARSET, such as the bsd client, is defined by the TelnetServer keyword
argument *default_encoding*, which is 'utf-8' by default.

Carriage Return
---------------

There are five supported signalling mechanisms for "end of line"

_CR LF_  The Telnet protocol defines the sequence CR LF to mean "end-of-line".
the input argument to callback `line_received()` will contain the line input buffered up to, but not including, CR LF. the bsd telnet client sends CR LF by default.
_CR NUL_ An interpretation of rfc854 may be that CR NUL should be sent when only a single CR is intended on a client and server host capable of distinguishing between CR and CR LF ('return' vs 'enter' key). The input argument to callback ``line_received()`` makes no distinction, which contains neither. The bsd telnet client may send CR NUL by toggling the ``crlf`` option.
_CR_ If CR is not followed by LF or NUL, this byte is received as part of the next line. A client sending a bare CR is not RFC compliant.
_LF_ If LF is not prefixes by CR, it is treated as though CR LF was received.
_IAC EOR_ In addition to line-oriented or character-oriented terminals, IAC EOR is used to delimit logical recrds (e.g., "screens") on Data Entry Terminals (DETs).

Notes
-----

Additional Resources,
   "COMMENTS ON THE NEW TELNET SPECIFICATIONS" RFC 513
   "A Note on Protocol Synch Sequences", RFC 529
   "Comments on the new TELNET Protocol and its Implementation," RFC 559
   "A Minor Pitfall in the Telnet Protocol," RFC 728
   "Telnet Protocol," MIL-STD-1782, U.S. Department of Defense, May 1984.
   "Mud Terminal Type Standard," http://tintin.sourceforge.net/mtts/
   "Telnet Protocol in C-Kermit 8.0 and Kermit 95 2.0,"
       http://www.columbia.edu/kermit/telnet80.html
   "Telnet Negotiation Concepts," http://lpc.psyc.eu/doc/concepts/negotiation

License
-------
telnetlib3 is (c) 2013 Jeffrey Quast <contact@jeffquast.com>.

Permission to use, copy, modify, and/or distribute this software for any purpose with or without fee is hereby granted, provided that the above copyright notice and this permission notice appear in all copies.

THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES WITH REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY SPECIAL, DIRECT, INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN AN ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF OR IN CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.

SLC functions were transcribed from NetBSD.

 Copyright (c) 1989, 1993
      The Regents of the University of California.  All rights reserved.

 Redistribution and use in source and binary forms, with or without
 modification, are permitted provided that the following conditions
 are met:
 1. Redistributions of source code must retain the above copyright
    notice, this list of conditions and the following disclaimer.
 2. Redistributions in binary form must reproduce the above copyright
    notice, this list of conditions and the following disclaimer in the
    documentation and/or other materials provided with the distribution.
 3. Neither the name of the University nor the names of its contributors
    may be used to endorse or promote products derived from this software
    without specific prior written permission.

 THIS SOFTWARE IS PROVIDED BY THE REGENTS AND CONTRIBUTORS ``AS IS'' AND
 ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
 IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
 ARE DISCLAIMED.  IN NO EVENT SHALL THE REGENTS OR CONTRIBUTORS BE LIABLE
 FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
 DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS
 OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION)
 HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT
 LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY
 OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF
 SUCH DAMAGE.


