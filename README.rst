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

This project requires the asyncio_ module, first made available in python 3.4.

Usage
=====

Basic Server::

   import asyncio, telnetlib3

   loop = asyncio.get_event_loop()
   server = loop.run_until_complete(telnetlib3.create_server(port=6023))
   print('Server Listening %s %s' % server.sockets[0].getsockname()[:2])
   loop.run_until_complete(server.wait_closed())

Basic Server, using streams interface::

   import asyncio, telnetlib3

   @asyncio.coroutine
   def shell(reader, writer):
        writer.write('Would you like to play a game? ')
        echo = yield from reader.read(1)
        writer.write('{0}\r\nThey say the only way to win is '
                     'to not play at all.\r\n'.format(echo))
        writer.close()

   loop = asyncio.get_event_loop()
   coro = telnetlib3.create_server(port=6023, shell=shell)
   server = loop.run_until_complete(coro)
   print('Server Listening %s %s' % server.sockets[0].getsockname()[:2])
   loop.run_until_complete(server.wait_closed())

# WIP
#    transport, protocol = yield from loop.create_connection(
#        lambda: telnetlib3.Client(**kwargs), host, port)
#
#    reader, writer = yield from telnetlib3.open_connection(
#        host, port, **kwargs)
#
#    protocol = yield from telnetlib3.start_client(
#        host, port, **kwargs)

Scripts
=======

These example binary programs demonstrate protocol functionality.

* telnet-client_: Small demonstrating terminal telnet client.
* telnet-server_: Telnet server providing debugging shell.
* telnet-talker_: Multi-user server shell, sometimes called a talker_.

.. _asyncio: http://docs.python.org/3.4/library/asyncio.html
.. _talker: https://en.wikipedia.org/wiki/Talker
.. _telnet-client: https://github.com/jquast/telnetlib3/tree/master/bin/telnet-client
.. _telnet-server: https://github.com/jquast/telnetlib3/tree/master/bin/telnet-server
.. _telnet-talker: https://github.com/jquast/telnetlib3/tree/master/bin/telnet-talker
