.. image:: https://img.shields.io/pypi/v/telnetlib3.svg
    :alt: Latest Version
    :target: https://pypi.python.org/pypi/telnetlib3

.. image:: https://img.shields.io/pypi/dm/telnetlib3.svg?logo=pypi
    :alt: Downloads
    :target: https://pypi.python.org/pypi/telnetlib3

.. image:: https://codecov.io/gh/jquast/telnetlib3/branch/master/graph/badge.svg
    :alt: codecov.io Code Coverage
    :target: https://codecov.io/gh/jquast/telnetlib3/

.. image:: https://img.shields.io/badge/Linux-yes-success?logo=linux
    :alt: Linux supported
    :target: https://telnetlib3.readthedocs.io/

.. image:: https://img.shields.io/badge/Windows-yes-success?logo=windows
    :alt: Windows supported
    :target: https://telnetlib3.readthedocs.io/

.. image:: https://img.shields.io/badge/MacOS-yes-success?logo=apple
    :alt: MacOS supported
    :target: https://telnetlib3.readthedocs.io/

.. image:: https://img.shields.io/badge/BSD-yes-success?logo=freebsd
    :alt: BSD supported
    :target: https://telnetlib3.readthedocs.io/

Introduction
============

``telnetlib3`` is a feature-rich Telnet Server, Client, and Protocol library
for Python 3.9 and newer.

This library supports both modern asyncio_ *and* legacy `Blocking API`_.

The python telnetlib.py_ module removed by Python 3.13 is also re-distributed as-is, as a backport.
In Python 3.13+, ``pip install telnetlib3`` provides a drop-in ``import telnetlib`` shim, so
existing code using the legacy standard telnet client API continues to work without modification.

See the `Guidebook`_ for examples and the `API documentation`_.

Command-line Utilities
----------------------

The CLI utility ``telnetlib3-client`` is provided for connecting to servers and
``telnetlib3-server`` for hosting a server.

Both tools accept the argument ``--shell=my_module.fn_shell`` describing a python module path to a
function of signature ``async def shell(reader, writer)``.  The server also provides a
``--pty-exec`` argument allowing it to act as a telnet server for any CLI/TUI programs.

::

    # telnet to utf8 roguelike server
    telnetlib3-client nethack.alt.org

    # or bbs,
    telnetlib3-client xibalba.l33t.codes 44510

    # automatic script communicates with a server
    telnetlib3-client --shell bin.client_wargame.shell 1984.ws 666

    # run a default shell server bound to 127.0.0.1 6023
    telnetlib3-server

    # or custom ip, port and shell
    telnetlib3-server 0.0.0.0 1984 --shell=bin.server_wargame.shell

    # host an external program with a pseudo-terminal (raw mode is default)
    telnetlib3-server --pty-exec /bin/bash -- --login

    # or host a program in linemode,
    telnetlib3-server --pty-exec /bin/bc --line-mode

There are also two fingerprinting CLIs, ``telnetlib3-fingerprint`` and
``telnetlib3-fingerprint-server``::

    # host a server, wait for clients to connect and fingerprint them,
    telnetlib3-fingerprint-server

    # report fingerprint of the telnet server on 1984.ws
    telnetlib3-fingerprint 1984.ws

Encoding
~~~~~~~~

The default encoding of telnetlib3-client and server is set by the `locale
<https://man7.org/linux/man-pages/man1/locale.1.html>`_.

Without negotiation of BINARY transmission, all Telnet protocol text *should* be limited to ASCII
text, by strict compliance of Telnet.  Further, the encoding used *should* be negotiated by CHARSET
:rfc:`2066` or by ``LANG`` using ``NEW_ENVIRON`` :rfc:`1572`.  Otherwise, a compliant telnet client
should be limited to ASCII.

When these conditions are true, telnetlib3-server and telnetlib3-client allow *automatic
negotiation* of any encoding in either direction supported by the python language, or any
custom ``ATASCII``, ``PETSCII``, and ``big5bbs`` provided with telnetlib3.

**However**, from a February 2026 `census of MUDs <https://muds.modem.xyz>`_ and `BBSs servers
<https://bbs.modem.xyz>`_:

- 2.8% of MUDs and 0.5% of BBSs support bi-directional CHARSET
- 18.4% of BBSs and 3.2% of MUDs support BINARY.

This means that connecting to *large majority* of BBSs or MUDs that transmit non-ascii, it will
require *manually specifying an encoding*, eg.::

    telnetlib3-client --encoding=cp437 20forbeers.com 1337

    telnetlib3-client --encoding=big5bbs bbs.ccns.ncku.edu.tw 3456

Raw Mode
~~~~~~~~

Some telnet servers, especially "retro" BBS systems or those designed for serial transmission but
are connected to a TCP socket without any telnet negotiation may require the "raw" mode argument::

    telnetlib3-client --raw-mode area52.tk 5200 --encoding=atascii

Go-Ahead (GA)
~~~~~~~~~~~~~

When a client does not negotiate Suppress Go-Ahead (SGA), the server sends
``IAC GA`` after output to signal that the client may transmit. This is
correct behavior for MUD clients like Mudlet that expect prompt detection
via GA.

If GA causes unwanted output for your use case, disable it::

    telnetlib3-server --never-send-ga

For PTY shells, GA is sent after 500ms of output idle time to avoid
injecting GA in the middle of streaming output.

Compression (MCCP)
~~~~~~~~~~~~~~~~~~

MCCP2 (server-to-client) and MCCP3 (client-to-server) zlib compression are
supported, widely used by MUD servers to reduce bandwidth::

    # connect to a MUD that offers MCCP compression
    telnetlib3-client dunemud.net 6789

    # actively request compression from a server
    telnetlib3-client --compression dunemud.net 6789

    # reject compression even if the server offers it
    telnetlib3-client --no-compression dunemud.net 6789

    # host a MUD server that advertises MCCP2/MCCP3
    telnetlib3-server --compression --shell=my_mud.shell

By default (without ``--compression`` or ``--no-compression``), the telnetlib3-client passively
accepts compression when offered by the server, and the telnetlib3-server does not advertise
compression. Compression is automatically disabled over TLS connections.

Asyncio Protocol
----------------

The core protocol and CLI utilities are written using an `Asyncio Interface`_.

Blocking API
------------

A Synchronous interface, modeled after telnetlib.py_ (client) and miniboa_ (server), with various
enhancements in protocol negotiation is also provided.  See `sync API documentation`_ for more.

Quick Example
=============

A simple telnet server:

.. code-block:: python

    import asyncio
    import telnetlib3

    async def shell(reader, writer):
        writer.write('\r\nWould you like to play a game? ')
        inp = await reader.readline()
        if inp:
            writer.echo(inp)
            writer.write('\r\nThey say the only way to win '
                         'is to not play at all.\r\n')
            await writer.drain()
        writer.close()

    async def main():
        server = await telnetlib3.create_server(port=6023, shell=shell)
        await server.wait_closed()

    asyncio.run(main())

A client that connects and plays the game:

.. code-block:: python

    import asyncio
    import telnetlib3

    async def shell(reader, writer):
        while True:
            output = await reader.read(1024)
            if not output:
                break
            if '?' in output:
                writer.write('y\r\n')
            print(output, end='', flush=True)
        print()

    async def main():
        reader, writer = await telnetlib3.open_connection('localhost', 6023)
        await shell(reader, writer)

    asyncio.run(main())

More examples are available in the `Guidebook`_ and the `bin/`_ directory of the repository.

Features
--------

Matching specifications:

* Telnet protocol: Network Virtual Terminal data, IAC command escaping, and
  option negotiation with pending and refused states (`rfc-854`_,
  `rfc-855`_).
* Commands: NOP, AYT, BRK, IP, AO, EC, EL, ABORT, EOF, SUSP, DM, and GA
  (`rfc-854`_, `rfc-858`_).
* 8-bit binary transmission (`rfc-856`_) and End of Record (`rfc-885`_).
* Echo (`rfc-857`_) and Suppress Go-Ahead (`rfc-858`_).
* Status (`rfc-859`_), Timing Mark (`rfc-860`_), and Logout (`rfc-727`_).
* Terminal type (`rfc-930`_, `rfc-1091`_), terminal speed (`rfc-1079`_),
  window size (`rfc-1073`_), X display location (`rfc-1096`_), and client
  location (`rfc-779`_).
* Environment negotiation (`rfc-1408`_, `rfc-1571`_, `rfc-1572`_) including
  automatic encoding detection from ``LANG``.
* Character set negotiation (`rfc-2066`_) with bidirectional code page lists.
* Linemode (`rfc-1184`_) with local editing, special line characters (SLC),
  and forwardmask.
* Remote flow control (`rfc-1372`_).
* MUD standards: GMCP_ JSON packages (option 201), MSDP_ structured tables
  and arrays (69), MSSP_ server metadata (70), ZMP_ NUL-delimited messages
  (93), and MCCP2_/MCCP3_ zlib compression (86/87).
* Public MUD codec helpers in ``telnetlib3.mud`` for GMCP, MSDP, MSSP, ZMP,
* MCCP format auto-detection for zlib, gzip, and raw deflate streams.

See Specifications_ for the complete list of RFCs and MUD standards.

Features somewhat unique to this library:

* Client and server fingerprinting: ``telnetlib3-fingerprint`` and ``telnetlib3-fingerprint-server``
  probe core, legacy, and MUD telnet options, capture the TTYPE cycle, environment, SLC table, and
  banner, and save a deterministic JSON fingerprint, detection of telnet loops and wrong-direction
  negotiation.
* Legacy BBS codecs: ATASCII, PETSCII, ``big5bbs``, and Atari ST, including encoding-native keyboard
  and end-of-line translation for ATASCII and PETSCII.
* TLS (TELNETS) in both client and server, including ``--tls-auto`` to accept TLS and plain telnet
  on the same port by detecting the TLS ClientHello.
* PTY hosting with ``--pty-exec`` on POSIX systems: run any CLI or TUI program per connection,
  forwarding NAWS and TTYPE and switching between raw and cooked line mode.
* Guard shells: ``--robot-check`` identifies a real terminal emulator by requesting a cursor
  position report, ``--pty-fork-limit`` queues excess connections with a busy shell, and
  ``ConnectionCounter`` enforces limits.
  ATCP, and AARDWOLF.

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
.. _rfc-930: https://www.rfc-editor.org/rfc/rfc930.txt
.. _rfc-1073: https://www.rfc-editor.org/rfc/rfc1073.txt
.. _rfc-1079: https://www.rfc-editor.org/rfc/rfc1079.txt
.. _rfc-1091: https://www.rfc-editor.org/rfc/rfc1091.txt
.. _rfc-1096: https://www.rfc-editor.org/rfc/rfc1096.txt
.. _rfc-1184: https://www.rfc-editor.org/rfc/rfc1184.txt
.. _rfc-1372: https://www.rfc-editor.org/rfc/rfc1372.txt
.. _rfc-1408: https://www.rfc-editor.org/rfc/rfc1408.txt
.. _rfc-1571: https://www.rfc-editor.org/rfc/rfc1571.txt
.. _rfc-1572: https://www.rfc-editor.org/rfc/rfc1572.txt
.. _rfc-2066: https://www.rfc-editor.org/rfc/rfc2066.txt
.. _GMCP: https://www.gammon.com.au/gmcp
.. _MSDP: https://tintin.mudhalla.net/protocols/msdp/
.. _MSSP: https://tintin.mudhalla.net/protocols/mssp/
.. _MCCP2: https://tintin.mudhalla.net/protocols/mccp/
.. _MCCP3: https://tintin.mudhalla.net/protocols/mccp/
.. _ZMP: https://discworld.starturtle.net/external/protocols/zmp.html
.. _Specifications: https://telnetlib3.readthedocs.io/en/latest/rfcs.html
.. _`bin/`: https://github.com/jquast/telnetlib3/tree/master/bin
.. _telnetlib.py: https://docs.python.org/3.12/library/telnetlib.html
.. _Asyncio Interface: https://telnetlib3.readthedocs.io/en/latest/guidebook.html#asyncio-interface
.. _Blocking API: https://telnetlib3.readthedocs.io/en/latest/guidebook.html#blocking-interface
.. _Guidebook: https://telnetlib3.readthedocs.io/en/latest/guidebook.html
.. _API documentation: https://telnetlib3.readthedocs.io/en/latest/api.html
.. _sync API documentation: https://telnetlib3.readthedocs.io/en/latest/api/sync.html
.. _miniboa: https://github.com/shmup/miniboa
.. _asyncio: https://docs.python.org/3/library/asyncio.html

Further Reading
---------------

Further documentation available at https://telnetlib3.readthedocs.io/
