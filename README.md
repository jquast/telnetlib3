About
=====

telnetlib3.py is an ISC-licensed Telnet Server library.

Implemented using the "tulip" module of PEP 3156, the proposed Asynchronous I/O framework for Python 3.4.

Development is currently in progress, telnet server is near completion.

Status
------

TODO: Server 100% RFC-compliant
TODO: TelnetClient
TODO: nosetests
TODO: example MUD server
TODO: example wunderground.com client

Synch
-----

RFC 1123 Requirements for Internet Hosts, states::

      3.2.4  Telnet "Synch" Signal: RFC-854, pp. 8-10

         When it receives "urgent" TCP data, a User or Server Telnet
         MUST discard all data except Telnet commands until the DM (and
         end of urgent) is reached.

     With protocols that support out-of-band data, the SO_OOBINLINE option
     requests that out-of-band data be placed in the normal data input queue
     as received; it will then be accessible with recv or read calls without
     the MSG_OOB flag.  Some protocols always behave as if this option is set.


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
a client replying "UTF-8" is presumed utf-8 encoded.

The default preferred encoding for clients that negotiate BINARY but not
CHARSET, such as the bsd client, is defined by the TelnetServer keyword
argument *default_encoding*, which is 'utf-8' by default.

Carriage Return
---------------

There are five signaling mechanisms for "end of line" supported.

_CR LF_  The Telnet protocol defines the sequence CR LF to mean "end-of-line".
the input argument to callback `line_received()` will contain the line input buffered up to, but not including, CR LF. the bsd telnet client sends CR LF by default.
_CR NUL_ An interpretation of rfc854 may be that CR NUL should be sent when only a single CR is intended on a client and server host capable of distinguishing between CR and CR LF ('return' vs 'enter' key). The input argument to callback ``line_received()`` makes no distinction, which contains neither. The bsd telnet client may send CR NUL by toggling the ``crlf`` option.
_CR_ If CR is not followed by LF or NUL, this byte is received as part of the next line. A client sending a bare CR is not RFC compliant.
_LF_ If LF is not prefixes by CR, it is treated as though CR LF was received.
_IAC EOR_ In addition to line-oriented or character-oriented terminals, IAC EOR is used to delimit logical recrds (e.g., "screens") on Data Entry Terminals (DETs).

Notes
-----

Many standard and extended telnet RFC protocols are implemented with one
deviation: Telnet byte (IAC, DM) is described as having the TCP Urgent bit
set. This is not supported by tulip (argument ``errorfds`` to select.select).

Listed is a summary of applicable telnet RFC's, x=implemented

[x] RFC 854  Telnet Protocol Specification                        May 1983
[x] RFC 855  Telnet Option Specification                          May 1983
[x] RFC 856  Telnet Binary Transmission                           May 1983
[x] RFC 857  Telnet Echo Option                                   May 1983
[x] RFC 858  Telnet Supress Go Ahead Option                       May 1983
[x] RFC 859  Telnet Status Option                                 May 1983
[x] RFC 860  Telnet Timing mark Option                            May 1983
[ ] RFC 861  Telnet Extended Options List                         May 1983
[x] RFC 885  Telnet End of Record Option                          Dec 1983
[x] RFC 1073 Telnet Window Size Option                            Oct 1988
[x] RFC 1079 Telnet Terminal Speed Option                         Dec 1988
[x] RFC 1091 Telnet Terminal-Type Option                          Feb 1989
[x] RFC 1096 Telnet X Display Location Option                     Mar 1989
[x] RFC 1184 Telnet Linemode Option (extended options)            Oct 1990
[x] RFC 1123 Requirements for Internet Hosts                      Oct 1989
[ ] RFC 1143 The Q Method of Implementing .. Option Negotiation   Feb 1990
[x] RFC 1372 Telnet Remote Flow Control Option                    Oct 1992
[x] RFC 1572 Telnet Environment Option                            Jan 1994
[ ] RFC 2066 Telnet Charset Option                                Jan 1997

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


      Number   Name                                RFC  NIC  ITP APH USE
      ------   ---------------------------------   --- ----- --- --- ---
         0     Binary Transmission                 856 ----- yes obs yes
         1     Echo                                857 ----- yes obs yes
         2     Reconnection                        ... 15391  no yes  no
         3     Suppress Go Ahead                   858 ----- yes obs yes
         4     Approx Message Size Negotiation     ... 15393  no yes  no
         5     Status                              859 ----- yes obs yes
         6     Timing Mark                         860 ----- yes obs yes
         7     Remote Controlled Trans and Echo    726 39237  no yes  no
         8     Output Line Width                   ... 20196  no yes  no
         9     Output Page Size                    ... 20197  no yes  no
        10     Output Carriage-Return Disposition  652 31155  no yes  no
        11     Output Horizontal Tabstops          653 31156  no yes  no
        12     Output Horizontal Tab Disposition   654 31157  no yes  no
        13     Output Formfeed Disposition         655 31158  no yes  no
        14     Output Vertical Tabstops            656 31159  no yes  no
        15     Output Vertical Tab Disposition     657 31160  no yes  no
        16     Output Linefeed Disposition         658 31161  no yes  no
        17     Extended ASCII                      698 32964  no yes  no
        18     Logout                              727 40025  no yes  no
        19     Byte Macro                          735 42083  no yes  no
        20     Data Entry Terminal                 732 41762  no yes  no
        21     SUPDUP                          734 736 42213  no yes  no
        22     SUPDUP Output                       749 45449  no  no  no
        23     Send Location                       779 -----  no  no  no
       255     Extended-Options-List               861 ----- yes obs yes


      if @options["Binmode"]
        self.write(string)
      else
        if @telnet_option["BINARY"] and @telnet_option["SGA"]
          # IAC WILL SGA IAC DO BIN send EOL --> CR
          self.write(string.gsub(/\n/n, CR))
        elsif @telnet_option["SGA"]
          # IAC WILL SGA send EOL --> CR+NULL
          self.write(string.gsub(/\n/n, CR + NULL))
        else
          # NONE send EOL --> CR+LF
          self.write(string.gsub(/\n/n, EOL))
        end
      end


   flush in/flush out flag handling of SLC characters

   description of, and handling of, local vs. remote line editing; it seems
   if linemode is 'edit', that 'aa^Hcd' is received as 'abcd',
   when linemode is not 'edit', then something like readline callbacks
   should be supplied for SLC characters; but otherwise is line oriented.

   Linemode.trapsig may be asserted by server; we shouldn't get any ^C when
   set, but, instead, get IAC IP only. When unset, we can get ^C raw, or,
   if an SLC function is requested, call that callback.

   Assert EOL with BINARY and LINEMODE EDIT option behavior.

   A series of callbacks for LINEMODE and standard EC, EL, etc; this should
   allow a readline-line interface to negotiate correct behavior, regardless
   of mode. Withholding on implementation: reaching for clarity without
   brevity.

   A simple telnet client .. with stdin as tulip  ..?

 [ ] Input & output all 7-bit characters
 [ ] Bypass local op sys interpretation
 [ ] Escape character
 [ ]    User-settable escape character
 [ ] Escape to enter 8-bit values
 [ ] Can input IP, AO, AYT
 [ ] Can input EC, EL, Break
 [ ] Report TCP connection errors to user
 [ ] Optional non-default contact port
 [ ] Can spec: output flushed when IP sent
 [ ] Can manually restore output mode
 [ ] Must send official name in Term-Type option
 [ ] User can enable/disable init negotiations
 [ ] User Telnet May ignore GA's
 [ ] User Telnet should send "Synch" after IP, AO, AYT
 [ ] User Telnet should flush output when send IP
 [ ] User Telnet able to send CR LF, CR NUL, or LF
 [ ] ASCII user able to select CR LF/CR NUL
 [ ] User Telnet default mode is CR LF
 [ ] Non-interactive uses CR LF for EOL

local line editing  - means that all normal command line character
 processing, like "Erase Character" and "Erase Line", happen on the
 local system, and only when "CR LF" (or some other special character)
 is encountered is the edited data sent to the remote system.

ignal trapping  - means, for example, that if the user types the
 character associated with the IP function, then the "IAC IP" function
 is sent to the remote side instead of the character typed.  Remote
 signal trapping means, for example, that if the user types the
 character associated with the IP function, then the "IAC IP" function
 is not sent to the remote side, but rather the actual character typed
 is sent to the remote side.

 rfc1184 one character left/right (SLC_MCL/SLC_MCR), move cursor one word left/right (SLC_MCWL/SLC_MCWR), move cursor to begining/end of line (SLC_MCBOL/SLC_MCEOL), enter insert/overstrike mode (SLC_INSRT/SLC_OVER), erase one character/word to the right (SLC_ECR/SLC_EWR), and erase to the beginning/end of the line (SLC_EBOL/SLC_EEOL).


Option negotiation rule compliance:
    [x] Must avoid negotiation loops
    [x] Must refuse unsupported options
    [x] negotiation Should be OK anytime on connection
    [x] Must default to NVT
    [x] Accept any name in Term-Type option
    [x] Implement Binary, Suppress-GA options
    [ ] Should support Echo, Status, EOL, Ext-Opt-List(*) options
    [x] Should Implement Window-Size option if appropriate
    [x] Server should initiate mode negotiations
    [x] Non-GA server negotiate SUPPRESS-GA option
    [x] User or Server Must accept SUPPRESS-GA option
    [x] Must Support SE NOP DM IP AO AYT SB
    [x] May Support EOR EC EL Break
    [x] Must Ignore unsupported control functions
    [ ] Must User, Server discard urgent data up to DM
    [ ] Server May Telnet reply Synch to IP
    [ ] Server Telnet must reply Synch to AO
    [x] Must not Send high-order bit in NVT mode
    [x] Must not Send high-order bit as parity bit
    [x] Should Negot. BINARY if pass high-ord. bit to applic
    [x] Must Always double IAC data byte
    [x] Must Double IAC data byte in binary mode
    [x] Must Obey Telnet cmds in binary mode
    [ ] Must not send End-of-line, CR NUL in binary mode
    [ ] EOL at Server same as local end-of-line
    [x] ASCII Server Must accept CR LF or CR NUL for EOL

[x] Where RFC 854 implies that the other side may reject a request to
    enable an option, it means that you must accept such a rejection.
[x] It MUST therefore remember that it is negotiating a WILL/DO, and this
    negotiation state MUST be separate from the enabled state and from
    the disabled state.  During the negotiation state, any effects of
    having the option enabled MUST NOT be used.
[x] Rule: Remember DONT/WONT requests
[x] Rule: Prohibit new requests before completing old negotiation
[ ] Rule: When LINEMODE is turned on, and when in EDIT mode, when any normal
    line terminator on the client side operating system is typed, the
    line should be transmitted with "CR LF" as the line terminator.  When
    EDIT mode is turned off, a carriage return should be sent as "CR
    NUL", a line feed should be sent as LF, and any other key that cannot
    be mapped into an ASCII character, but means the line is complete
    (like a DOIT or ENTER key), should be sent as "CR LF".
[x] Rule: At no time should "DO LINEMODE" be negotiated in both directions of
    the Telnet connection.  The side that is the "DO LINEMODE" is considered
    to be the server side, and the side that is "WILL LINEMODE" is the client
    side.
[x] Rule: At no time should "SB LINEMODE DO/DONT FORWARDMASK", be sent unless
    "DO LINEMODE" has been previously negotiated.  At no time should "SB
    LINEMODE WILL/WONT FORWARDMASK", be sent unless "WILL LINEMODE" has
    been previously negotiated.

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


