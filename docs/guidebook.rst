=========
Guidebook
=========

This guide provides examples for using telnetlib3 to build telnet servers
and clients. All examples are available as standalone scripts in the
``bin/`` directory of the repository.

These examples are not distributed with the package -- they are only available
in the github repository. You can retrieve them by cloning the repository, or
downloading the "raw" file link.

.. contents:: Contents
   :local:
   :depth: 2

Asyncio Interface
=================

The primary interface for telnetlib3 uses Python's asyncio library for
asynchronous I/O. This allows handling many concurrent connections
efficiently in a single thread.

Server Examples
---------------

server_wargame.py
~~~~~~~~~~~~~~~~~

https://github.com/jquast/telnetlib3/blob/master/bin/server_wargame.py

A minimal telnet server that demonstrates the basic shell callback pattern.
The server asks a simple question and responds based on user input.

.. literalinclude:: ../bin/server_wargame.py
   :language: python
   :lines: 17-35

Run the server::

    python bin/server_wargame.py

Then connect with::

    telnet localhost 6023


server_wait_for_client.py
~~~~~~~~~~~~~~~~~~~~~~~~~

https://github.com/jquast/telnetlib3/blob/master/bin/server_wait_for_client.py

Demonstrates the ``Server.wait_for_client()`` API for accessing
client protocols without using a shell callback. This pattern is useful when
you need direct control over client handling.

.. literalinclude:: ../bin/server_wait_for_client.py
   :language: python
   :lines: 21-44


server_broadcast.py
~~~~~~~~~~~~~~~~~~~

https://github.com/jquast/telnetlib3/blob/master/bin/server_broadcast.py

A chat-style server that broadcasts messages from one client to all others.
Demonstrates:

- Using ``server.clients`` to access all connected protocols
- Handling multiple clients with asyncio tasks
- Using ``wait_for()`` to check negotiation states

.. literalinclude:: ../bin/server_broadcast.py
   :language: python
   :lines: 18-43


server_wait_for_negotiation.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

https://github.com/jquast/telnetlib3/blob/master/bin/server_wait_for_negotiation.py

Demonstrates using ``writer.wait_for()`` to await specific
telnet option negotiation states before proceeding. This is useful when your
application depends on certain terminal capabilities being negotiated.

The server waits for:

- NAWS (Negotiate About Window Size) - window dimensions
- TTYPE (Terminal Type) - terminal identification
- BINARY mode - 8-bit clean transmission

.. literalinclude:: ../bin/server_wait_for_negotiation.py
   :language: python
   :lines: 19-54


Client Examples
---------------

client_wargame.py
~~~~~~~~~~~~~~~~~

https://github.com/jquast/telnetlib3/blob/master/bin/client_wargame.py

A telnet client that connects to a server and automatically answers
questions. Demonstrates the client shell callback pattern.

.. literalinclude:: ../bin/client_wargame.py
   :language: python
   :lines: 18-41

Server API Reference
--------------------

The ``create_server()`` function returns a ``Server`` instance with these
key methods and properties:

wait_for_client()
~~~~~~~~~~~~~~~~~

``Server.wait_for_client()`` waits for a client to connect and complete
initial negotiation::

    server = await telnetlib3.create_server(port=6023)
    client = await server.wait_for_client()
    client.writer.write("Welcome!\r\n")

clients
~~~~~~~

The ``Server.clients`` property provides access to all currently connected
client protocols::

    # Broadcast to all clients
    for client in server.clients:
        client.writer.write("Server announcement\r\n")

wait_for()
~~~~~~~~~~

``TelnetWriter.wait_for()`` waits for specific telnet option negotiation
states::

    # Wait for BINARY mode
    await asyncio.wait_for(
        client.writer.wait_for(remote={"BINARY": True}),
        timeout=5.0
    )

    # Wait for terminal type negotiation to complete
    await asyncio.wait_for(
        client.writer.wait_for(pending={"TTYPE": False}),
        timeout=5.0
    )

The method accepts these keyword arguments:

- ``remote``: Dict of options to wait for in ``remote_option`` (client WILL)
- ``local``: Dict of options to wait for in ``local_option`` (client DO)
- ``pending``: Dict of options to wait for in ``pending_option``

Option names are strings: ``"BINARY"``, ``"ECHO"``, ``"NAWS"``, ``"TTYPE"``, etc.

wait_for_condition()
~~~~~~~~~~~~~~~~~~~~

The ``wait_for_condition()`` method waits for a custom condition::

    from telnetlib3.telopt import ECHO

    await client.writer.wait_for_condition(
        lambda w: w.mode == "kludge" and w.remote_option.enabled(ECHO)
    )

Encoding and Binary Mode
------------------------

By default, telnetlib3 uses ``encoding="utf8"``, which means the shell
callback receives ``TelnetReaderUnicode`` and ``TelnetWriterUnicode``.
These work with Python ``str`` -- you read and write strings::

    async def shell(reader, writer):
        writer.write("Hello, world!\r\n")  # str
        data = await reader.read(1)        # returns str

To work with raw bytes instead, pass ``encoding=False`` to
``create_server()`` or ``open_connection()``. The shell then receives
``TelnetReader`` and ``TelnetWriter``, which work with ``bytes``::

    async def binary_shell(reader, writer):
        writer.write(b"Hello, world!\r\n")  # bytes
        data = await reader.read(1)         # returns bytes

    await telnetlib3.create_server(
        host="127.0.0.1", port=6023,
        shell=binary_shell, encoding=False
    )

Binary mode is useful for specific low-level conditions, like performing
xmodem transfers, or working with legacy systems that predate unicode
and utf-8 support.

The same applies to clients -- ``open_connection(..., encoding=False)``
returns a ``(TelnetReader, TelnetWriter)`` pair that works with ``bytes``.

Line Endings
~~~~~~~~~~~~

The telnet protocol (RFC 854) requires ``\r\n`` (CR LF) as the line ending
for all NVT (Network Virtual Terminal) output. This applies in all standard
modes:

- **NVT ASCII mode** (default): ``\r\n`` is required.
- **Kludge mode** (SGA negotiated, no LINEMODE): input is character-at-a-time,
  but server output is still NVT -- ``\r\n`` is expected.
- **Binary mode** (TRANSMIT-BINARY): raw bytes, no NVT transformation --
  ``\n`` is acceptable if both sides agree.

The ``write()`` method on both the asyncio and blocking interfaces sends data
as-is -- it does **not** convert ``\n`` to ``\r\n``::

    # Correct:
    writer.write("Hello!\r\n")

    # Wrong -- most clients will not display a proper line break:
    writer.write("Hello!\n")

For maximum compatibility with MUD clients, legacy terminals, and standard
telnet implementations, always use ``\r\n`` with ``write()``.

server_binary.py
~~~~~~~~~~~~~~~~

https://github.com/jquast/telnetlib3/blob/master/bin/server_binary.py

A telnet server in binary mode that echoes client input as hex bytes.
Demonstrates using ``encoding=False`` for raw byte I/O.

.. literalinclude:: ../bin/server_binary.py
   :language: python
   :lines: 34-51

Blocking Interface
==================

Asyncio can be complex or unnecessary for many applications. For these cases,
telnetlib3 provides a blocking (synchronous) interface via :mod:`telnetlib3.sync`.
The asyncio event loop runs in a background thread, exposing familiar blocking
methods.

Client Usage
------------

The :class:`~telnetlib3.sync.TelnetConnection` class provides a blocking client
interface::

    from telnetlib3.sync import TelnetConnection

    # Using context manager (recommended)
    with TelnetConnection('localhost', 6023) as conn:
        conn.write('hello\r\n')
        response = conn.readline()
        print(response)

    # Manual lifecycle
    conn = TelnetConnection('localhost', 6023, encoding='utf8')
    conn.connect()
    try:
        conn.write('command\r\n')
        data = conn.read_until(b'>>> ')
        print(data)
    finally:
        conn.close()

Server Usage
------------

The :class:`~telnetlib3.sync.BlockingTelnetServer` class provides a blocking
server interface with thread-per-connection handling::

    from telnetlib3.sync import BlockingTelnetServer

    def handle_client(conn):
        """Called in a new thread for each client."""
        conn.write('Welcome!\r\n')
        while True:
            line = conn.readline(timeout=60)
            if not line or line.strip() in ('quit', b'quit'):
                break
            conn.write(f'Echo: {line}')
        conn.close()

    # Simple: auto-spawns thread per client
    server = BlockingTelnetServer('localhost', 6023, handler=handle_client)
    server.serve_forever()

Or with a manual accept loop for custom threading strategies::

    import threading

    server = BlockingTelnetServer('localhost', 6023)
    server.start()
    while True:
        conn = server.accept()
        threading.Thread(target=handle_client, args=(conn,)).start()


Blocking Server Example
-----------------------

blocking_echo_server.py
~~~~~~~~~~~~~~~~~~~~~~~

https://github.com/jquast/telnetlib3/blob/master/bin/blocking_echo_server.py

A traditional threaded echo server using :class:`~telnetlib3.sync.BlockingTelnetServer`.
Each client connection runs in its own thread.

.. literalinclude:: ../bin/blocking_echo_server.py
   :language: python
   :lines: 21-52

Run the server::

    python bin/blocking_echo_server.py


Blocking Client Example
-----------------------

blocking_client.py
~~~~~~~~~~~~~~~~~~

https://github.com/jquast/telnetlib3/blob/master/bin/blocking_client.py

A traditional blocking telnet client using :class:`~telnetlib3.sync.TelnetConnection`.

.. literalinclude:: ../bin/blocking_client.py
   :language: python
   :lines: 18-54

Usage::

    python bin/blocking_client.py localhost 6023


Miniboa Compatibility
---------------------

The :class:`~telnetlib3.sync.ServerConnection` class (received in handler
callbacks) provides miniboa-compatible properties and methods for easier
migration::

    from telnetlib3.sync import BlockingTelnetServer

    def handler(client):
        # Miniboa-compatible properties
        print(f"Connected: {client.addrport()}")
        print(f"Terminal: {client.terminal_type}")
        print(f"Size: {client.columns}x{client.rows}")

        # Miniboa-compatible send (converts \n to \r\n)
        client.send("Welcome!\n")

        while client.active:
            if client.idle() > 300:
                client.send("Timeout.\n")
                client.deactivate()
                break

            try:
                line = client.readline(timeout=1)
            except TimeoutError:
                continue
            if line:
                client.send(f"Echo: {line}")

    server = BlockingTelnetServer('0.0.0.0', 6023, handler=handler)
    server.serve_forever()

Properties and methods with equal mapping:

:attr:`~telnetlib3.sync.ServerConnection.active`,
:attr:`~telnetlib3.sync.ServerConnection.address`,
:attr:`~telnetlib3.sync.ServerConnection.port`,
:attr:`~telnetlib3.sync.ServerConnection.terminal_type`,
:attr:`~telnetlib3.sync.ServerConnection.columns`,
:attr:`~telnetlib3.sync.ServerConnection.rows`,
:meth:`~telnetlib3.sync.ServerConnection.send`,
:meth:`~telnetlib3.sync.ServerConnection.addrport`,
:meth:`~telnetlib3.sync.ServerConnection.idle`,
:meth:`~telnetlib3.sync.ServerConnection.duration`,
:meth:`~telnetlib3.sync.ServerConnection.deactivate`

Key differences from miniboa:

- telnetlib3 uses a thread-per-connection model instead of miniboa's
  poll-based ``server.poll()`` loop
- miniboa's ``get_command()`` and ``cmd_ready`` are replaced by blocking
  :meth:`~telnetlib3.sync.ServerConnection.readline` and
  :meth:`~telnetlib3.sync.ServerConnection.read`

.. note::

   The ``send()`` method normalizes newlines to ``\r\n`` for miniboa
   compatibility.  Both ``\n`` and ``\r\n`` in the input produce a single
   ``\r\n`` on the wire::

       conn.send("Hello!\n")        # OK -- sends \r\n on the wire
       conn.send("Hello!\r\n")      # OK -- also sends \r\n on the wire
       conn.write("Hello!\r\n")     # OK -- write() sends as-is


Advanced Negotiation
--------------------

Use :meth:`~telnetlib3.sync.TelnetConnection.wait_for` to block until telnet
options are negotiated::

    conn.wait_for(remote={'NAWS': True, 'TTYPE': True}, timeout=5.0)
    term = conn.get_extra_info('TERM')
    cols = conn.get_extra_info('cols')
    rows = conn.get_extra_info('rows')

The :meth:`~telnetlib3.sync.TelnetConnection.wait_for` method accepts ``remote``,
``local``, and ``pending`` dicts. Option names are strings: ``"BINARY"``,
``"ECHO"``, ``"NAWS"``, ``"TTYPE"``, etc.

For protocol state inspection, use the :attr:`~telnetlib3.sync.TelnetConnection.writer`
property::

    writer = conn.writer
    print(f"Mode: {writer.mode}")  # 'local', 'remote', or 'kludge'
    print(f"ECHO enabled: {writer.remote_option.enabled(ECHO)}")


Fingerprinting Server
=====================

The public telnetlib3 demonstration Fingerprinting Server is::

    telnet 1984.ws 555


The fingerprinting shell
(:func:`telnetlib3.fingerprinting.fingerprinting_server_shell`) probes each
connecting client's telnet capabilities, terminal emulator features, and unicode
support. This useful for uniquely identify clients across sessions by the
capabilities of the software used.  The fingerprinting shell runs in two phases:

1. **Telnet probe** -- negotiates all standard telnet options (TTYPE, NAWS,
   BINARY, SGA, ECHO, NEW_ENVIRON, CHARSET, LINEMODE, SLC) and records which
   options the client supports, the TTYPE cycle, environment variables, and SLC
   table. A deterministic hash is computed from the protocol-level fingerprint.

2. **Terminal probe** -- if `ucs-detect <https://pypi.org/project/ucs-detect/>`_
   is installed, the shell spawns it through a PTY to probe the terminal
   emulator's software and version, color depth, graphics protocols (Kitty,
   iTerm2, Sixel), device attributes, DEC private modes, unicode version
   support, and emoji rendering. A second hash is computed from the terminal
   fingerprint.

Running
-------

Install with optional dependencies for full fingerprinting support
(`prettytable <https://pypi.org/project/prettytable/>`_ and
`ucs-detect <https://pypi.org/project/ucs-detect/>`_)::

    pip install telnetlib3[extras]


::

    TELNETLIB3_DATA_DIR=data telnetlib3-server --shell telnetlib3.fingerprinting_server_shell

Storage
-------

Results are saved as JSON files organized by fingerprint hash::

    $TELNETLIB3_DATA_DIR/client/<telnet-hash>/<terminal-hash>/

Moderating
----------

The ``bin/moderate_fingerprints.py`` script provides an interactive CLI for
reviewing client-submitted name suggestions and assigning names to hashes::

    export TELNETLIB3_DATA_DIR=./data
    python bin/moderate_fingerprints.py


MUD Server
==========

The public telnetlib3 demonstration MUD Server is::

    telnet 1984.ws 6066

telnetlib3 supports the common MUD (Multi-User Dungeon) protocols used by
MUD clients like Mudlet, TinTin++, and BlowTorch:

- **GMCP** (Generic MUD Communication Protocol) -- JSON-based structured data
  for room info, character vitals, inventory, and more.
- **MSDP** (MUD Server Data Protocol) -- binary-encoded variable/value pairs for
  real-time game state.
- **MSSP** (MUD Server Status Protocol) -- server metadata for MUD
  crawlers and directories.

The :mod:`telnetlib3.mud` module provides encode/decode functions for all three
protocols using :class:`~telnetlib3.stream_writer.TelnetWriter` methods
``send_gmcp()``, ``send_msdp()``, and ``send_mssp()``.

Running
-------

The repository includes a "mini-MUD" example at `bin/server_mud.py
<https://github.com/jquast/telnetlib3/blob/master/bin/server_mud.py>`_ with
rooms, combat, weapons, GMCP/MSDP/MSSP support, and basic persistence.

::

    telnetlib3-server --shell bin.server_mud.shell

Then, connect with any telnet or MUD client::

    telnet localhost 6023

Legacy telnetlib Compatibility
==============================

Python's ``telnetlib`` was removed in Python 3.13 (`PEP 594
<https://peps.python.org/pep-0594/>`_). telnetlib3 includes a verbatim copy
from Python 3.12 with its original test suite::

    # OLD:
    from telnetlib import Telnet

    # NEW:
    from telnetlib3.telnetlib import Telnet

The legacy module has limited negotiation support and is maintained for
compatibility only.

Modern Alternative
------------------

:mod:`telnetlib3.sync` provides a modern blocking interface:

======================  ==============================
Old telnetlib           :mod:`telnetlib3.sync`
======================  ==============================
``Telnet(host)``        ``TelnetConnection(host)``
``tn.read_until()``     ``conn.read_until()``
``tn.read_some()``      ``conn.read_some()``
``tn.write()``          ``conn.write()``
``tn.close()``          ``conn.close()``
======================  ==============================

Enhancements over legacy telnetlib:

- Full RFC 854 protocol negotiation (NAWS, TTYPE, BINARY, ECHO, SGA)
- ``wait_for()`` to await negotiation states
- ``get_extra_info()`` for terminal type, size, and other metadata
- ``writer`` property for protocol state inspection
- Server support via ``BlockingTelnetServer``
