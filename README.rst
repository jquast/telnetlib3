.. image:: https://img.shields.io/travis/jquast/telnetlib3.svg
    :alt: Travis Continuous Integration
    :target: https://travis-ci.org/jquast/telnetlib3/

.. image:: https://img.shields.io/teamcity/https/teamcity-master.pexpect.org/s/Telnetlib3_FullBuild.svg
    :alt: TeamCity Build status
    :target: https://teamcity-master.pexpect.org/viewType.html?buildTypeId=Telnetlib3_FullBuild&branch_Telnetlib3=%3Cdefault%3E&tab=buildTypeStatusDiv

.. image:: https://coveralls.io/repos/jquast/telnetlib3/badge.svg?branch=master&service=github
    :alt: Coveralls Code Coverage
    :target: https://coveralls.io/github/jquast/telnetlib3?branch=master

.. image:: https://img.shields.io/pypi/v/telnetlib3.svg
    :alt: Latest Version
    :target: https://pypi.python.org/pypi/telnetlib3

.. image:: https://img.shields.io/pypi/dm/telnetlib3.svg
    :alt: Downloads
    :target: https://pypi.python.org/pypi/telnetlib3

.. image:: https://badges.gitter.im/Join%20Chat.svg
    :alt: Join Chat
    :target: https://gitter.im/jquast/telnetlib3


About
=====

telnetlib3 is a Telnet Client and Server Protocol library for python.

This project requires the asyncio_ module, available as a module in python
3.3, and as a standard module for all versions of made python 3.4 and later.

Usage
=====

Basic Telnet Server using Streams interface::

    import asyncio, telnetlib3

    @asyncio.coroutine
    def shell(reader, writer):
        writer.write('Would you like to play a game? ')
        inp = yield from reader.read(1)
        if inp:
            writer.echo(inp)
            writer.write('\r\nThey say the only way to win '
                         'is to not play at all.\r\n')
            yield from writer.drain()
        writer.close()

    loop = asyncio.get_event_loop()
    coro = telnetlib3.start_server(port=6023, shell=shell)
    server = loop.run_until_complete(coro)
    loop.run_until_complete(server.wait_closed())

Basic Telnet Client using Streams interface::

    import asyncio, telnetlib3

    @asyncio.coroutine
    def shell(reader, writer):
        buf = ''
        while True:
            outp = yield from reader.read(1024)
            if not outp:
                break
            print(outp, end='', flush=True)
            if '?' in outp:
                # reply all questions with 'y'.
                writer.write('y')
        print()
              
    loop = asyncio.get_event_loop()
    coro = telnetlib3.start_client('localhost', 6023, shell=shell)
    reader, _ = loop.run_until_complete(coro)
    loop.run_until_complete(reader.protocol.waiter_closed)

Please note that using the ``print()`` function from a coroutine may raise
a ``BlockingIOError`` when a large amount of data is printed -- for this
demonstration shell, it behaves fine.

Scripts
=======

``telnetlib3-client``
  Small demonstrating terminal telnet client.  This opens *stdin* and *stdout*
  for asynchronous I/O, forwarding input to the writer interface, and printing
  output received from the reader interface.

``telnetlib3-server``
  Telnet server providing the default debugging shell.  This provides a simple
  shell server that allows introspection of the session's values.

The default telnet server or client shell function may be specified as
command line parameter in form of ``--shell=my_module.my_shell`` This is
coroutine defined as demonstrated in the above examples.

.. _asyncio: http://docs.python.org/3.4/library/asyncio.html
