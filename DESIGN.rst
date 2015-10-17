TelnetStream
------------

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

On Encoding
-----------

currently we determine 'CHARSET': 'utf8' because we cannot correctly
determine the first part of LANG (en_us.UTF-8, for example).  It should
be possible, in a derived (demo) application, to determine the region
code by geoip (maxmind database, etc).
