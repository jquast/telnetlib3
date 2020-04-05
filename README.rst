.. image:: https://img.shields.io/travis/jquast/telnetlib3.svg
    :alt: Travis Continuous Integration
    :target: https://travis-ci.org/jquast/telnetlib3/

.. image:: https://coveralls.io/repos/jquast/telnetlib3/badge.svg?branch=master&service=github
    :alt: Coveralls Code Coverage
    :target: https://coveralls.io/github/jquast/telnetlib3?branch=master

.. image:: https://img.shields.io/pypi/v/telnetlib3.svg
    :alt: Latest Version
    :target: https://pypi.python.org/pypi/telnetlib3

.. image:: https://img.shields.io/pypi/dm/telnetlib3.svg
    :alt: Downloads
    :target: https://pypi.python.org/pypi/telnetlib3


Introduction
============

telnetlib3 is a Telnet Client and Server library for python.  This project
requires python 3.3 and later, using the asyncio_ module.

.. _asyncio: http://docs.python.org/3.4/library/asyncio.html

Quick Example
-------------

Authoring a Telnet Server using Streams interface that offers a basic war game:

.. code-block:: python

    import asyncio, telnetlib3

    @asyncio.coroutine
    def shell(reader, writer):
        writer.write('\r\nWould you like to play a game? ')
        inp = yield from reader.read(1)
        if inp:
            writer.echo(inp)
            writer.write('\r\nThey say the only way to win '
                         'is to not play at all.\r\n')
            yield from writer.drain()
        writer.close()

    loop = asyncio.get_event_loop()
    coro = telnetlib3.create_server(port=6023, shell=shell)
    server = loop.run_until_complete(coro)
    loop.run_until_complete(server.wait_closed())

Authoring a Telnet Client that plays the war game with this server:

.. code-block:: python

    import asyncio, telnetlib3

    @asyncio.coroutine
    def shell(reader, writer):
        while True:
            # read stream until '?' mark is found
            outp = yield from reader.read(1024)
            if not outp:
                # End of File
                break
            elif '?' in outp:
                # reply all questions with 'y'.
                writer.write('y')
 
            # display all server output
            print(outp, flush=True)
 
        # EOF
        print()

    loop = asyncio.get_event_loop()
    coro = telnetlib3.open_connection('localhost', 6023, shell=shell)
    reader, writer = loop.run_until_complete(coro)
    loop.run_until_complete(writer.protocol.waiter_closed)

Command-line
------------

Two command-line scripts are distributed with this package.

``telnetlib3-client``

  Small terminal telnet client.  Some example destinations and options::

    telnetlib3-client nethack.alt.org
    telnetlib3-client --encoding=cp437 --force-binary blackflag.acid.org
    telnetlib3-client htc.zapto.org


``telnetlib3-server``

  Telnet server providing the default debugging shell.  This provides a simple
  shell server that allows introspection of the session's values, for example::

     tel:sh> help
     quit, writer, slc, toggle [option|all], reader, proto

     tel:sh> writer
     <TelnetWriter server mode:kludge +lineflow -xon_any +slc_sim server-will:BINARY,ECHO,SGA client-will:BINARY,NAWS,NEW_ENVIRON,TTYPE>

     tel:sh> reader
     <TelnetReaderUnicode encoding='utf8' limit=65536 buflen=0 eof=False>

     tel:sh> toggle all
     wont echo.
     wont suppress go-ahead.
     wont outbinary.
     dont inbinary.
     xon-any enabled.
     lineflow disabled.

     tel:sh> reader
     <TelnetReaderUnicode encoding='US-ASCII' limit=65536 buflen=1 eof=False>

     tel:sh> writer
     <TelnetWriter server mode:local -lineflow +xon_any +slc_sim client-will:NAWS,NEW_ENVIRON,TTYPE>


Both command-line scripts accept argument ``--shell=my_module.fn_shell``
describing a python module path to a coroutine of signature
``shell(reader, writer)``, just as the above examples.

Features
--------

The following RFC specifications are implemented:

* `rfc-727`_, "Telnet Logout Option," Apr 1977.
* `rfc-779`_, "Telnet Send-Location Option", Apr 1981.
* `rfc-854`_, "Telnet Protocol Specification", May 1983.
* `rfc-855`_, "Telnet Option Specifications", May 1983.
* `rfc-856`_, "Telnet Binary Transmission", May 1983.
* `rfc-857`_, "Telnet Echo Option", May 1983.
* `rfc-858`_, "Telnet Suppress Go Ahead Option", May 1983.
* `rfc-859`_, "Telnet Status Option", May 1983.
* `rfc-860`_, "Telnet Timing mark Option", May 1983.
* `rfc-885`_, "Telnet End of Record Option", Dec 1983.
* `rfc-1073`_, "Telnet Window Size Option", Oct 1988.
* `rfc-1079`_, "Telnet Terminal Speed Option", Dec 1988.
* `rfc-1091`_, "Telnet Terminal-Type Option", Feb 1989.
* `rfc-1096`_, "Telnet X Display Location Option", Mar 1989.
* `rfc-1123`_, "Requirements for Internet Hosts", Oct 1989.
* `rfc-1184`_, "Telnet Linemode Option (extended options)", Oct 1990.
* `rfc-1372`_, "Telnet Remote Flow Control Option", Oct 1992.
* `rfc-1408`_, "Telnet Environment Option", Jan 1993.
* `rfc-1571`_, "Telnet Environment Option Interoperability Issues", Jan 1994.
* `rfc-1572`_, "Telnet Environment Option", Jan 1994.
* `rfc-2066`_, "Telnet Charset Option", Jan 1997.

.. _rfc-727: https://www.rfc-editor.org/rfc/rfc727.txt
.. _rfc-779: https://www.rfc-editor.org/rfc/rfc779.txt
.. _rfc-854: https://www.rfc-editor.org/rfc/rfc854.txt
.. _rfc-855: https://www.rfc-editor.org/rfc/rfc855.txt
.. _rfc-856: https://www.rfc-editor.org/rfc/rfc856.txt
.. _rfc-857: https://www.rfc-editor.org/rfc/rfc857.txt
.. _rfc-858: https://www.rfc-editor.org/rfc/rfc858.txt
.. _rfc-859: https://www.rfc-editor.org/rfc/rfc859.txt
.. _rfc-860: https://www.rfc-editor.org/rfc/rfc860.txt
.. _rfc-885: https://www.rfc-editor.org/rfc/rfc885.txt
.. _rfc-1073: https://www.rfc-editor.org/rfc/rfc1073.txt
.. _rfc-1079: https://www.rfc-editor.org/rfc/rfc1079.txt
.. _rfc-1091: https://www.rfc-editor.org/rfc/rfc1091.txt
.. _rfc-1096: https://www.rfc-editor.org/rfc/rfc1096.txt
.. _rfc-1123: https://www.rfc-editor.org/rfc/rfc1123.txt
.. _rfc-1184: https://www.rfc-editor.org/rfc/rfc1184.txt
.. _rfc-1372: https://www.rfc-editor.org/rfc/rfc1372.txt
.. _rfc-1408: https://www.rfc-editor.org/rfc/rfc1408.txt
.. _rfc-1571: https://www.rfc-editor.org/rfc/rfc1571.txt
.. _rfc-1572: https://www.rfc-editor.org/rfc/rfc1572.txt
.. _rfc-2066: https://www.rfc-editor.org/rfc/rfc2066.txt

Further Reading
---------------

Further documentation available at https://telnetlib3.readthedocs.org/
