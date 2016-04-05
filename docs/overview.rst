Overview
========

This section provides an overview of asyncio design patterns using the
telnetlib3 API.  The first pattern, using the client and `server shell`_
is the most basic and preferred.  The second pattern, deriving
`TelnetServer`_ and `TelnetClient`_ may be used to alter the default
negotiation options, and requires tighter integration of the protocol API.

server shell
------------

Authoring a Telnet Server is only a matter of creating a python module, and
defining an :func:`asyncio.coroutine`_ function.  In this example, we'll write
a shell for the soviet Kosmos/300 lunar lander.

.. code-block:: python

    @asyncio.coroutine
    def shell(reader, writer):
        writer.write('космос/300 готов\r\n? ')
        while True:
            recv = reader.readline()
            if not recv:
                # eof
                return
            writer.write(recv + '\r\n')
            if recv.strip() in ('launch', 'запуск'):
                # The engines on the Block D upper stage failed, leaving the
                # spacecraft stranded in Earth orbit.
                writer.write('двигатель повреждение/п\r\n')
            elif recv.strip() in ('bye', 'Прощай'):
                writer.write('Прощай')
                yield from writer.drain()
                return

 
- reporting battery usage
- executing a self-test
- displaying shared log
