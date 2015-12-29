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

        inp = yield from reader.read(1)
        if inp:
            if writer.will_echo:
                writer.write(inp)
            writer.write('\r\nThey say the only way to win '
                         'is to not play at all.\r\n')

    
    @asyncio.coroutine
    def start_server():
        yield from telnetlib3.create_server(shell=shell, port=6023)
    
    loop = asyncio.get_event_loop()
    loop.run_until_complete(start_server())

Basic Telnet Client::

    import asyncio, telnetlib3

    @asyncio.coroutine
    def start_client():
        reader, writer = yield from telnetlib3.connect('localhost', port=6023)
        while True:
            buf = yield from reader.readexactly(1)
            if not buf:
                # EOF
                break

            print(buf, end='', flush=True)

            if '?' in buf:
                # reply all questions with 'y'.
                writer.write('y')

    loop = asyncio.get_event_loop()
    loop.run_until_complete(start_client())

Scripts
=======

The bundled binary programs ``telnetlib3-client`` and ``telnetlib3-server``
demonstrate full protocol functionality.  The default telnet server or client
shell function may be specified as a command line parameter in form of
``--shell=my_module.my_shell``.

* telnetlib3-client: Small demonstrating terminal telnet client.
* telnetlib3-server: Telnet server providing the default debugging shell.

.. _asyncio: http://docs.python.org/3.4/library/asyncio.html
