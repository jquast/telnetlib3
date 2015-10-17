TelnetStream
------------

feed_byte called by telnet server should be a coroutine
receiving data by send. It should yield 'is_oob', or 'slc_received',
etc.?  We're still considering ... the state still requires tracking,
but this would turn multiple function calls into a single call as a
generator, probably preferred for bandwidth.

in feed_byte, Our OOB implementation is not RFC-correct, is 'oob'
even the correct keyword, here?  we're having a very difficult
time implementing these old MSG_OOB-flagged tcp socket writes,
no longer an OS feature.

complete and test pause/resume_writing throughout API

On Encoding
-----------

currently we determine 'CHARSET': 'utf8' because we cannot correctly
determine the first part of LANG (en_us.UTF-8, for example).  It should
be possible, in a derived (demo) application, to determine the region
code by geoip (maxmind database, etc).

On FLUSHIN, FLUSHOUT
--------------------

Its really hard to introspect where a mid-stream SLC is. We need to
investigate existing code about how flushin/out works. Ours is
unimplemented.
