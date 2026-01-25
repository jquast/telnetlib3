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

``telnetlib3`` is a full-featured Telnet Client and Server library for python3.8 and newer.

Modern asyncio_ and legacy blocking API's are provided.

The python telnetlib.py_ module removed by Python 3.13 is also re-distributed as a backport.

Overview
========

telnetlib3 provides multiple interfaces for working with the Telnet protocol:

Asyncio Protocol
----------------

Modern async/await interface for both client and server, supporting concurrent
connections. See the `Guidebook`_ for examples and the `API documentation`_.

Blocking API
------------

A traditional synchronous interface modeled after telnetlib.py_ (client) and miniboa_ (server),
with various enhancements in protocol negotiation is provided. Blocking API calls for complex
arrangements of clients and servers typically require threads.

See `sync API documentation`_ for more.

Command-line Utilities
----------------------

Two CLI tools are included: ``telnetlib3-client`` for connecting to servers
and ``telnetlib3-server`` for hosting a server.

Both tools argument ``--shell=my_module.fn_shell`` describing a python
module path to a function of signature ``async def shell(reader, writer)``.
The server also provides ``--pty-exec`` argument to host a stand-alone
program.

::

    telnetlib3-client nethack.alt.org
    telnetlib3-client xibalba.l33t.codes 44510
    telnetlib3-client --shell bin.client_wargame.shell 1984.ws 666
    telnetlib3-server 0.0.0.0 1984 --shell=bin.server_wargame.shell
    telnetlib3-server --pty-exec /bin/bash -- --login

Legacy telnetlib
----------------

This library contains an unadulterated copy of Python 3.12's telnetlib.py_,
from the standard library before it was removed in Python 3.13.

To migrate code, change import statements:

.. code-block:: python

    # OLD imports:
    import telnetlib

    # NEW imports:
    import telnetlib3

``telnetlib3`` did not provide server support, while this library also provides
both client and server support through a similar Blocking API interface.

See `sync API documentation`_ for details.

Encoding
--------

Often required, ``--encoding`` and ``--force-binary``::

    telnetlib3-client --encoding=cp437 --force-binary 20forbeers.com 1337

The default encoding is the system locale, usually UTF-8, but all Telnet
protocol text *should* be limited to ASCII until BINARY mode is agreed by
compliance of their respective RFCs.

However, many clients and servers that are capable of non-ascii encodings like
UTF-8 or CP437 may not be capable of negotiating about BINARY, NEW_ENVIRON,
or CHARSET to negotiate about it.

In this case, use ``--force-binary`` and ``--encoding`` when the encoding of
the remote end is known.

Quick Example
=============

A simple telnet server:

.. code-block:: python

    import asyncio
    import telnetlib3

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
        server = await telnetlib3.create_server(port=6023, shell=shell)
        await server.wait_closed()

    asyncio.run(main())

More examples are available in the `Guidebook`_ and the `bin/`_ directory of the repository.

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
.. _`bin/`: https://github.com/jquast/telnetlib3/tree/master/bin
.. _telnetlib.py: https://docs.python.org/3.12/library/telnetlib.html
.. _Guidebook: https://telnetlib3.readthedocs.io/en/latest/guidebook.html
.. _API documentation: https://telnetlib3.readthedocs.io/en/latest/api.html
.. _sync API documentation: https://telnetlib3.readthedocs.io/en/latest/api/sync.html
.. _miniboa: https://github.com/shmup/miniboa
.. _asyncio: https://docs.python.org/3/library/asyncio.html
.. _wait_for(): https://telnetlib3.readthedocs.io/en/latest/api/sync.html#telnetlib3.sync.TelnetConnection.wait_for
.. _get_extra_info(): https://telnetlib3.readthedocs.io/en/latest/api/sync.html#telnetlib3.sync.TelnetConnection.get_extra_info
.. _readline(): https://telnetlib3.readthedocs.io/en/latest/api/sync.html#telnetlib3.sync.TelnetConnection.readline
.. _read_until(): https://telnetlib3.readthedocs.io/en/latest/api/sync.html#telnetlib3.sync.TelnetConnection.read_until
.. _active: https://telnetlib3.readthedocs.io/en/latest/api/sync.html#telnetlib3.sync.ServerConnection.active
.. _address: https://telnetlib3.readthedocs.io/en/latest/api/sync.html#telnetlib3.sync.ServerConnection.address
.. _terminal_type: https://telnetlib3.readthedocs.io/en/latest/api/sync.html#telnetlib3.sync.ServerConnection.terminal_type
.. _columns: https://telnetlib3.readthedocs.io/en/latest/api/sync.html#telnetlib3.sync.ServerConnection.columns
.. _rows: https://telnetlib3.readthedocs.io/en/latest/api/sync.html#telnetlib3.sync.ServerConnection.rows
.. _idle(): https://telnetlib3.readthedocs.io/en/latest/api/sync.html#telnetlib3.sync.ServerConnection.idle
.. _duration(): https://telnetlib3.readthedocs.io/en/latest/api/sync.html#telnetlib3.sync.ServerConnection.duration
.. _deactivate(): https://telnetlib3.readthedocs.io/en/latest/api/sync.html#telnetlib3.sync.ServerConnection.deactivate

Further Reading
---------------

Further documentation available at https://telnetlib3.readthedocs.io/
