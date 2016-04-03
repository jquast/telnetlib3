Design/TODO
===========

Design items


reduce
------

outer telnetlib3-server and telnetlib3-client and examples should connect
as exit(main(\*\*parse_args(sys.argv))), the _transform_args() function is
rather shoe-horned, main() should declare keywords

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

