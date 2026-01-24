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

Quick Example
-------------

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

More examples are available in the `Guidebook`_ and the ``bin/`` directory.

.. _Guidebook: https://telnetlib3.readthedocs.io/en/latest/guidebook.html

Legacy telnetlib
----------------

This library *also* contains a copy of telnetlib.py_ from the standard library of
Python 3.12 before it was removed in Python 3.13. asyncio_ is not required.

To migrate code from Python 3.11 and earlier:

.. code-block:: python

    # OLD imports:
    import telnetlib
    # - or -
    from telnetlib import Telnet, ECHO, BINARY

    # NEW imports:
    import telnetlib3.telnetlib as telnetlib
    # - or -
    from telnetlib3.telnetlib import Telnet, ECHO, BINARY

.. _telnetlib.py: https://docs.python.org/3.12/library/telnetlib.html

Command-line
------------

Two command-line scripts are distributed with this package,
``telnetlib3-client`` and ``telnetlib3-server``.

Both accept argument ``--shell=my_module.fn_shell`` describing a python
module path to a function of signature ``async def shell(reader, writer)``.

::

    telnetlib3-client nethack.alt.org
    telnetlib3-server --pty-exec /bin/bash -- --login

Encoding
--------

Use ``--encoding`` and ``--force-binary`` for non-ASCII terminals::

    telnetlib3-client --encoding=cp437 --force-binary blackflag.acid.org

The default encoding is UTF-8. Use ``--force-binary`` when the server
doesn't properly negotiate BINARY mode.

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
