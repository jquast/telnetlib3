.. image:: https://img.shields.io/pypi/v/telnetlib3.svg
    :alt: Latest Version
    :target: https://pypi.python.org/pypi/telnetlib3

.. image:: https://img.shields.io/pypi/dm/telnetlib3.svg?logo=pypi
    :alt: Downloads
    :target: https://pypi.python.org/pypi/telnetlib3

.. image:: https://codecov.io/gh/jquast/telnetlib3/branch/master/graph/badge.svg
    :alt: codecov.io Code Coverage
    :target: https://codecov.io/gh/jquast/telnetlib3/

Introduction
============

telnetlib3 is a Telnet Client and Server library for python.  This project
requires python 3.7 and later, using the asyncio_ module.

.. _asyncio: http://docs.python.org/3.11/library/asyncio.html

Legacy 'telnetlib'
------------------

This library *also* contains a copy of telnetlib.py_ from the standard library of
Python 3.12 before it was removed in Python 3.13. asyncio_ is not required.

To migrate code from Python 3.11 and earlier, install this library and change
instances of `telnetlib` to `telnetlib3`:

.. code-block:: python

    # OLD imports:
    import telnetlib
    # - or -
    from telnetlib import Telnet, ECHO, BINARY

    # NEW imports:
    import telnetlib3.telnetlib as telnetlib
    # - or - 
    from telnetlib3 import Telnet, ECHO, BINARY
    from telnetlib3.telnetlib import Telnet, ECHO, BINARY

.. _telnetlib.py: https://docs.python.org/3.12/library/telnetlib.html


Quick Example
-------------

Writing a Telnet Server that offers a basic "war game":

.. code-block:: python

    import asyncio, telnetlib3

    async def shell(reader, writer):
        writer.write('\r\nWould you like to play a game? ')
        inp = await reader.read(1)
        if inp:
            writer.echo(inp)
            writer.write('\r\nThey say the only way to win '
                         'is to not play at all.\r\n')
            await writer.drain()
        writer.close()

    async def main():
        server = await telnetlib3.create_server('127.0.0.1', 6023, shell=shell)
        await server.wait_closed()

    asyncio.run(main())

Writing a Telnet Client that plays the "war game" against this server:

.. code-block:: python

    import asyncio, telnetlib3

    async def shell(reader, writer):
        while True:
            # read stream until '?' mark is found
            outp = await reader.read(1024)
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

    async def main():
        reader, writer = await telnetlib3.open_connection('localhost', 6023, shell=shell)
        await writer.protocol.waiter_closed

    asyncio.run(main())

Command-line
------------

Two command-line scripts are distributed with this package,
`telnetlib3-client` and `telnetlib3-server`.

Both command-line scripts accept argument ``--shell=my_module.fn_shell``
describing a python module path to an function of signature
``async def shell(reader, writer)``, as in the above examples.

These scripts also serve as more advanced server and client examples that
perform advanced telnet option negotation and may serve as a basis for
creating your own custom negotiation behaviors.

Find their filepaths using command::

     python -c 'import telnetlib3.server;print(telnetlib3.server.__file__, telnetlib3.client.__file__)'

telnetlib3-client
~~~~~~~~~~~~~~~~~

This is an entry point for command ``python -m telnetlib3.client``

Small terminal telnet client.  Some example destinations and options::

    telnetlib3-client --loglevel warn 1984.ws
    telnetlib3-client --loglevel debug --logfile logfile.txt nethack.alt.org 
    telnetlib3-client --encoding=cp437 --force-binary blackflag.acid.org

See section Encoding_ about arguments, ``--encoding=cp437`` and ``--force-binary``.

::

    usage: telnetlib3-client [-h] [--term TERM] [--loglevel LOGLEVEL]
                             [--logfmt LOGFMT] [--logfile LOGFILE] [--shell SHELL]
                             [--encoding ENCODING] [--speed SPEED]
                             [--encoding-errors {replace,ignore,strict}]
                             [--force-binary] [--connect-minwait CONNECT_MINWAIT]
                             [--connect-maxwait CONNECT_MAXWAIT]
                             host [port]
    
    Telnet protocol client
    
    positional arguments:
      host                  hostname
      port                  port number (default: 23)
    
    optional arguments:
      -h, --help            show this help message and exit
      --term TERM           terminal type (default: xterm-256color)
      --loglevel LOGLEVEL   log level (default: warn)
      --logfmt LOGFMT       log format (default: %(asctime)s %(levelname)s
                            %(filename)s:%(lineno)d %(message)s)
      --logfile LOGFILE     filepath (default: None)
      --shell SHELL         module.function_name (default:
                            telnetlib3.telnet_client_shell)
      --encoding ENCODING   encoding name (default: utf8)
      --speed SPEED         connection speed (default: 38400)
      --encoding-errors {replace,ignore,strict}
                            handler for encoding errors (default: replace)
      --force-binary        force encoding (default: True)
      --connect-minwait CONNECT_MINWAIT
                            shell delay for negotiation (default: 1.0)
      --connect-maxwait CONNECT_MAXWAIT
                            timeout for pending negotiation (default: 4.0)

telnetlib3-server
~~~~~~~~~~~~~~~~~

This is an entry point for command ``python -m telnetlib3.server``

Telnet server providing the default debugging shell.  This provides a simple
shell server that allows introspection of the session's values.

Example session::

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

::

    usage: telnetlib3-server [-h] [--loglevel LOGLEVEL] [--logfile LOGFILE]
                             [--logfmt LOGFMT] [--shell SHELL]
                             [--encoding ENCODING] [--force-binary]
                             [--timeout TIMEOUT]
                             [--connect-maxwait CONNECT_MAXWAIT]
                             [--pty-exec PROGRAM]
                             [host] [port] [-- ARG ...]

    Telnet protocol server

    positional arguments:
      host                  bind address (default: localhost)
      port                  bind port (default: 6023)

    optional arguments:
      -h, --help            show this help message and exit
      --loglevel LOGLEVEL   level name (default: info)
      --logfile LOGFILE     filepath (default: None)
      --logfmt LOGFMT       log format (default: %(asctime)s %(levelname)s
                            %(filename)s:%(lineno)d %(message)s)
      --shell SHELL         module.function_name (default: telnet_server_shell)
      --encoding ENCODING   encoding name (default: utf8)
      --force-binary        force binary transmission (default: False)
      --timeout TIMEOUT     idle disconnect (0 disables) (default: 300)
      --connect-maxwait CONNECT_MAXWAIT
                            timeout for pending negotiation (default: 4.0)
      --pty-exec PROGRAM    execute PROGRAM in a PTY for each connection
                            (use -- to pass args to PROGRAM)

PTY Execution
~~~~~~~~~~~~~

The server can spawn a PTY-connected program for each connection::

    telnetlib3-server --pty-exec /bin/bash -- --login

This spawns an interactive bash login shell. The ``--login`` flag (or ``-l``)
is recommended for proper shell initialization (readline, history, profile
sourcing).

For a minimal shell without these features::

    telnetlib3-server --pty-exec /bin/sh

Arguments after ``--`` are passed to the program, for example, to execute python
with argument of a script as a subprocess::

    telnetlib3-server --pty-exec $(which python) -- ../blessed/bin/cellestial.py

Encoding
--------

In this client connection example::

    telnetlib3-client --encoding=cp437 --force-binary blackflag.acid.org

Note the use of `--encoding=cp437` to translate input and output characters of
the remote end. This example legacy telnet BBS is unable to negotiate about
or present characters in any other encoding but CP437. Without these arguments,
Telnet protocol would dictate our session to be US-ASCII.

Argument `--force-binary` is *also* required in many cases, with both
``telnetlib3-client`` and ``telnetlib3-server``. In the original Telnet protocol
specifications, the Network Virtual Terminal (NVT) is defined as 7-bit US-ASCII,
and this is the default state for both ends until negotiated otherwise by RFC-856_
by negotiation of BINARY TRANSMISSION.

However, **many common telnet clients and servers fail to negotiate for BINARY**
correctly or at all. Using ``--force-binary`` allows non-ASCII encodings to be
used with those kinds of clients.

A Telnet Server that prefers "utf8" encoding, and, transmits it even in the case
of failed BINARY negotiation, to support a "dumb" telnet client like netcat::

    telnetlib3-server --encoding=utf8 --force-binary

Connecting with "dumb" client::

    nc -t localhost 6023

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

Further documentation available at https://telnetlib3.readthedocs.io/
