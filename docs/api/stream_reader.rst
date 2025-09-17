stream_reader
-------------

Closing a connection
~~~~~~~~~~~~~~~~~~~~

- Application code should not call ``reader.close()``. To gracefully close a connection, call ``writer.close()`` and, if needed, ``await writer.wait_closed()``. The protocol will signal end-of-input to the reader.
- The protocol layer calls ``reader.feed_eof()`` when the underlying transport indicates EOF (for example in ``connection_lost()``). This marks the reader as EOF and wakes any pending read coroutines.
- After ``feed_eof()``, subsequent ``read()`` calls will drain any buffered bytes and then return ``b""``; ``readline()``/iteration will stop at EOF. Use ``reader.at_eof()`` to test EOF state.

Example (application code):
::
  
  async def app(reader, writer):
      # ... use reader/readline/readuntil ...
      writer.close()
      await writer.wait_closed()
      # reader will eventually see EOF; reads return b"" once buffer drains

Example (protocol integration):
::
  
  class MyProtocol(asyncio.Protocol):
      def __init__(self, reader):
          self.reader = reader
      def connection_lost(self, exc):
          if exc:
              self.reader.set_exception(exc)
          self.reader.feed_eof()

Deprecation notes:
- ``TelnetReader.close()`` is deprecated; use ``feed_eof()`` (protocol) and ``writer.close()``/``wait_closed()`` (application).
- ``TelnetReader.connection_closed`` property is deprecated; use ``reader.at_eof()``.

.. automodule:: telnetlib3.stream_reader
   :members:
