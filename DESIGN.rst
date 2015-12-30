TelnetStream
============

feed_byte called by telnet server should be a coroutine
receiving data by send. It should yield 'is_oob', or 'slc_received',
etc.?  We're still considering ... the state still requires tracking,
but this would turn multiple function calls into a single call as a
generator, probably preferred for bandwidth.

handle_xon resumes writing in a way that is not obvious -- we should
be using the true 'pause_writing' and 'resume_writing' methods of our
base protocol.  The given code was written before these methods became
available in asyncio (then, tulip).  We need to accommodate the new
availabilities.

On STATUS rfc
-------------
We do everything fine because we negotiate fine so far, but what exactly are
we expected to do when the distant end's concept of our negotiation STATUS
disagrees with our own? Match theirs, re-negotiate .. ?

On Linemode
-----------
Now we seem to completion of linemode acknowledgement state loop for both
client and server, however, what do we do in the case of a server making a
suggestion (no ack bit set) that a "user" downstream of our API would
suggest to refuse? We would rather supply a table of 3-divider choice,
(allow-any/force-on/force-off), where force-on and force-off would cause
such bits to be set back on again before acknowledging return.

currently, we just choose whatever they suggest, always, in
_handle_sb_linemode_mode.

as a server, we simply honor whatever is given.  This is also
problematic in some designers may wish to implement shells
that specifically do not honor some parts of the bitmask, we
must provide them an any/force-on/force-off mode table interface.


On Encoding
-----------

currently we determine 'CHARSET': 'utf8' because we cannot correctly
determine the first part of LANG (en_us.UTF-8, for example).  It should
be possible, in a derived (demo) application, to determine the region
code by geoip (maxmind database, etc).

On Constants, debug logging
---------------------------

Like our C counterparts, we should use something more advanced than
a #define / enumeration, so that when printed, print their NAME --
when sent as bytes, send their raw value.


On Encoding
-----------
doing good so far, make sure tox.ini tests LANG=C, LANG=en_US.UTF-8 matrix,
or push travis-ci to do that for us?
# TODO: LANG=C, LANG=en_US.UTF-8 matrix ?

On Versioning
-------------

version.json should contain "shell": "0.1" currently, 0.2 next.
