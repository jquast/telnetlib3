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

Server Usage
============

Basic Telnet Server::

   import asyncio, telnetlib3
   
   @asyncio.coroutine
   def shell(reader, writer):

        writer.write('Would you like to play a game? ')

        resp = yield from reader.read(1)
        if writer.will_echo:
            writer.write(resp)

        msg = 'They say the only way to win is to not play at all.'
        writer.write('\r\n{msg}\r\n'.format(resp))
        writer.close()
    
    @asyncio.coroutine
    def start_server():
        yield from telnetlib3.create_server(shell=shell, port=6023)
    
    loop = asyncio.get_event_loop()
    loop.run_until_complete(start_server())
    loop.run_forever()

Basic Telnet Client::

    transport, protocol = yield from loop.create_connection(
        lambda: telnetlib3.Client(**kwargs), host, port)

    reader, writer = yield from telnetlib3.open_connection(
        host, port, **kwargs)

    protocol = yield from telnetlib3.start_client(
        host, port, **kwargs)

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
