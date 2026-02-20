=========
Guidebook
=========

This guide provides examples for using telnetlib3 to build telnet servers
and clients. All examples are available in the ``bin/`` directory of the
repository.

Most examples are **shell callbacks** -- async functions that receive a
``(reader, writer)`` pair. You do not need a standalone script to run them;
just point ``telnetlib3-server --shell=`` (or ``telnetlib3-client --shell=``)
at the callback::

    telnetlib3-server --shell=bin.server_wargame.shell

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
efficiently in a single process or thread.

Server Examples
---------------

server_wargame.py
~~~~~~~~~~~~~~~~~

https://github.com/jquast/telnetlib3/blob/master/bin/server_wargame.py

A minimal shell callback that asks a simple question and responds based on
user input.

.. literalinclude:: ../bin/server_wargame.py
   :language: python
   :lines: 23-31

Run with::

    telnetlib3-server --shell=bin.server_wargame.shell

Then connect with::

    telnet localhost 6023


server_wait_for_client.py
~~~~~~~~~~~~~~~~~~~~~~~~~

https://github.com/jquast/telnetlib3/blob/master/bin/server_wait_for_client.py

Demonstrates direct use of :func:`~telnetlib3.server.create_server` and the
:meth:`~telnetlib3.server.Server.wait_for_client` API for accessing client
protocols without using a shell callback. This is a standalone script because
it needs server-level control that cannot be expressed as a ``--shell=``
callback.

.. literalinclude:: ../bin/server_wait_for_client.py
   :language: python
   :lines: 21-44


server_broadcast.py
~~~~~~~~~~~~~~~~~~~

https://github.com/jquast/telnetlib3/blob/master/bin/server_broadcast.py

A chat-style server that broadcasts messages from one client to all others.
This is a standalone script because it uses
:meth:`~telnetlib3.server.Server.wait_for_client` and
:attr:`~telnetlib3.server.Server.clients` shared state. Demonstrates:

- Using :attr:`~telnetlib3.server.Server.clients` to access all connected protocols
- Handling multiple clients with asyncio tasks
- Using :meth:`~telnetlib3.stream_writer.TelnetWriter.wait_for` to check negotiation states

.. literalinclude:: ../bin/server_broadcast.py
   :language: python
   :lines: 18-43


server_wait_for_negotiation.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

https://github.com/jquast/telnetlib3/blob/master/bin/server_wait_for_negotiation.py

A shell callback demonstrating :meth:`~telnetlib3.stream_writer.TelnetWriter.wait_for` to
await specific telnet option negotiation states before proceeding. This is useful when your
application depends on certain terminal capabilities being negotiated.

The server waits for:

- NAWS (Negotiate About Window Size) - window dimensions
- TTYPE (Terminal Type) - terminal identification
- BINARY mode - 8-bit clean transmission

.. literalinclude:: ../bin/server_wait_for_negotiation.py
   :language: python
   :lines: 19-44

Run with::

    telnetlib3-server --shell=bin.server_wait_for_negotiation.shell


Client Examples
---------------

client_wargame.py
~~~~~~~~~~~~~~~~~

https://github.com/jquast/telnetlib3/blob/master/bin/client_wargame.py

A shell callback that connects to a server and automatically answers
questions. Demonstrates the client shell callback pattern.

.. literalinclude:: ../bin/client_wargame.py
   :language: python
   :lines: 17-28

Run with::

    telnetlib3-client --shell=bin.client_wargame.shell localhost 6023

Server API Reference
--------------------

The :func:`~telnetlib3.server.create_server` function returns a
:class:`~telnetlib3.server.Server` instance with these key methods and
properties:

wait_for_client()
~~~~~~~~~~~~~~~~~

:meth:`~telnetlib3.server.Server.wait_for_client` waits for a client to
connect and complete initial negotiation::

    server = await telnetlib3.create_server(port=6023)
    client = await server.wait_for_client()
    client.writer.write("Welcome!\r\n")

clients
~~~~~~~

The :attr:`~telnetlib3.server.Server.clients` property provides access to all
currently connected client protocols::

    # Broadcast to all clients
    for client in server.clients:
        client.writer.write("Server announcement\r\n")

wait_for()
~~~~~~~~~~

:meth:`~telnetlib3.stream_writer.TelnetWriter.wait_for` waits for specific
telnet option negotiation states::

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

The :meth:`~telnetlib3.stream_writer.TelnetWriter.wait_for_condition` method
waits for a custom condition::

    from telnetlib3.telopt import ECHO

    await client.writer.wait_for_condition(
        lambda w: w.mode == "kludge" and w.remote_option.enabled(ECHO)
    )

Encoding and Binary Mode
------------------------

By default, telnetlib3 uses ``encoding="utf8"``, which means the shell
callback receives :class:`~telnetlib3.stream_reader.TelnetReaderUnicode` and
:class:`~telnetlib3.stream_writer.TelnetWriterUnicode`.
These work with Python ``str`` -- you read and write strings::

    async def shell(reader, writer):
        writer.write("Hello, world!\r\n")  # str
        data = await reader.read(1)        # returns str

To work with raw bytes instead, pass ``encoding=False`` to
:func:`~telnetlib3.server.create_server` or :func:`~telnetlib3.client.open_connection`.
The shell then receives :class:`~telnetlib3.stream_reader.TelnetReader` and
:class:`~telnetlib3.stream_writer.TelnetWriter`, which work with ``bytes``::

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

The same applies to clients --
:func:`open_connection(..., encoding=False) <telnetlib3.client.open_connection>`
returns a (:class:`~telnetlib3.stream_reader.TelnetReader`,
:class:`~telnetlib3.stream_writer.TelnetWriter`) pair that works with
``bytes``.

Retro BBS Encodings
~~~~~~~~~~~~~~~~~~~

telnetlib3 includes custom codecs for retro computing platforms commonly
found on telnet BBS systems:

- **ATASCII** (``--encoding=atascii``) -- Atari 8-bit computers (400, 800,
  XL, XE).  Graphics characters at 0x00-0x1F, card suits, box drawing, and
  an inverse-video range at 0x80-0xFF.  The ATASCII end-of-line character
  (0x9B) maps to newline.  Aliases: ``atari8bit``, ``atari_8bit``.
- **PETSCII** (``--encoding=petscii``) -- Commodore 64/128 shifted
  (lowercase) mode.  Lowercase a-z at 0x41-0x5A, uppercase A-Z at
  0xC1-0xDA.  Aliases: ``cbm``, ``commodore``, ``c64``, ``c128``.
- **Atari ST** (``--encoding=atarist``) -- Atari ST character set with
  extended Latin, Greek, and math symbols.  Alias: ``atari``.

These encodings use bytes 0x80-0xFF for standard glyphs, which conflicts
with the telnet protocol's default 7-bit NVT mode.  When any of these
encodings is selected, ``--force-binary`` is automatically enabled so that
high-bit bytes are transmitted without requiring BINARY option negotiation.

PETSCII inline color codes are translated to ANSI 24-bit RGB using the
VIC-II C64 palette, and cursor control codes (up/down/left/right, HOME,
CLR, DEL) are translated to ANSI sequences.  ATASCII control character
glyphs (cursor movement, backspace, clear screen) are similarly translated.

Keyboard input is also mapped: arrow keys, backspace, delete, and enter
produce the correct raw bytes for each encoding::

    telnetlib3-client --encoding=atascii area52.tk 5200
    telnetlib3-client --encoding=petscii bbs.example.com 6400

``telnetlib3-fingerprint`` decodes and translates banners with these
encodings, including PETSCII colors.

SyncTERM Font Detection
^^^^^^^^^^^^^^^^^^^^^^^^

When a server sends a SyncTERM/CTerm font selection sequence
(``CSI Ps1 ; Ps2 SP D``), both ``telnetlib3-client`` and
``telnetlib3-fingerprint`` automatically switch the session encoding
to match the font (e.g. font 36 = ATASCII, 32-35 = PETSCII, 0 = CP437).
An explicit ``--encoding`` flag takes precedence over font detection.

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

Raw Mode and Line Mode
~~~~~~~~~~~~~~~~~~~~~~

By default ``telnetlib3-client`` matches the terminal's mode by the
server's stated telnet negotiation.  It starts in line mode (local echo,
line buffering) and switches dynamically depending on server:

- Nothing: line mode with local echo
- ``WILL ECHO`` + ``WILL SGA``: kludge mode (raw, no local echo)
- ``WILL ECHO``: raw mode, server echoes
- ``WILL SGA``: character-at-a-time with local echo

Use ``--raw-mode`` to force raw mode (no line buffering, no local echo),
which is needed for some legacy BBS systems that don't negotiate ``WILL
ECHO``.  This is set true when ``--encoding=petscii`` or ``atascii``.

Conversely, Use ``--line-mode`` to force line-buffered input with local echo.

Similarly, ``telnetlib3-server --pty-exec`` defaults to raw PTY mode
(disabling PTY echo), which is correct for programs that handle their own
terminal I/O (bash, curses, etc.).  Use ``--line-mode`` for programs
that expect cooked/canonical PTY mode::

    # Default: raw PTY (correct for curses programs)
    telnetlib3-server --pty-exec /bin/bash -- --login

    # Line mode: cooked PTY with echo (for simple programs like bc)
    telnetlib3-server --pty-exec /bin/bc --line-mode

Debugging
~~~~~~~~~

Use ``--loglevel=trace`` to see hexdump-style output of all bytes sent
and received on the wire::

    telnetlib3-client --loglevel=trace --logfile=debug.log bbs.example.com

TLS / SSL
~~~~~~~~~

Telnet over TLS (TELNETS, IANA port 992) secures the connection using
standard TLS encryption.  The TLS handshake is handled at the transport
layer — the telnet protocol sees plaintext exactly as it would over plain
TCP.  This is *not* STARTTLS (upgrade-in-place); the connection is
encrypted from the start.

**Server-side**

Generate a self-signed certificate for testing::

    openssl req -x509 -newkey rsa:2048 -keyout key.pem -out cert.pem \
        -days 365 -nodes -subj '/CN=localhost'

Run a TLS server from the CLI::

    telnetlib3-server --ssl-certfile cert.pem --ssl-keyfile key.pem 0.0.0.0 6023

Or programmatically::

    import ssl
    import telnetlib3

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain("cert.pem", keyfile="key.pem")

    server = await telnetlib3.create_server(host="0.0.0.0", port=6023, shell=shell, ssl=ctx)

For production, use certificates from Let's Encrypt or another trusted CA.

**Client-side**

Connect to a server with a CA-signed certificate (e.g. ``dunemud.net``)::

    telnetlib3-client --ssl dunemud.net 6788

The system CA store is used automatically, just like ``curl`` or a browser.

Connect to a server with a self-signed certificate::

    telnetlib3-client --ssl --ssl-cafile cert.pem localhost 6023

Or programmatically with full control::

    import ssl
    import telnetlib3

    # CA-signed server — just pass ssl=True
    reader, writer = await telnetlib3.open_connection("dunemud.net", 6788, ssl=True)

    # Self-signed — load the server's cert explicitly
    ctx = ssl.create_default_context(cafile="cert.pem")
    reader, writer = await telnetlib3.open_connection("localhost", 6023, ssl=ctx)

**Fingerprinting TLS servers**::

    telnetlib3-fingerprint --ssl dunemud.net 6788
    telnetlib3-fingerprint --ssl --ssl-cafile cert.pem localhost 6023

To skip certificate verification (e.g. for servers with self-signed or expired
certificates)::

    telnetlib3-client --ssl-no-verify example.com 6023
    telnetlib3-fingerprint --ssl-no-verify example.com 6023

.. warning::

   ``--ssl-no-verify`` is **insecure**.  The connection is encrypted, but the
   server's identity is not verified — a man-in-the-middle could intercept
   traffic.  Only use this for testing or when you trust the network path.

server_tls.py
~~~~~~~~~~~~~

https://github.com/jquast/telnetlib3/blob/master/bin/server_tls.py

A TLS-encrypted echo shell callback.  Demonstrates the ``ssl=`` parameter on
:func:`~telnetlib3.server.create_server`.

.. literalinclude:: ../bin/server_tls.py
   :language: python
   :lines: 24-36

Run with::

    telnetlib3-server --ssl-certfile cert.pem --ssl-keyfile key.pem \
        --shell=bin.server_tls.shell

server_binary.py
~~~~~~~~~~~~~~~~

https://github.com/jquast/telnetlib3/blob/master/bin/server_binary.py

A shell callback that echoes client input as hex bytes.
Demonstrates using ``encoding=False`` on :func:`~telnetlib3.server.create_server`
for raw byte I/O.

.. literalinclude:: ../bin/server_binary.py
   :language: python
   :lines: 22-32

Run with::

    telnetlib3-server --encoding=false --shell=bin.server_binary.shell

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

   The :meth:`~telnetlib3.sync.ServerConnection.send` method normalizes
   newlines to ``\r\n`` for miniboa
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

Go-Ahead (GA)
--------------

When a client does not negotiate Suppress Go-Ahead (SGA), the server sends
``IAC GA`` after output to signal that the client may transmit. This is
correct behavior for MUD clients like Mudlet that expect prompt detection
via GA.

If GA causes unwanted output for your use case, disable it::

    telnetlib3-server --never-send-ga

For PTY shells, GA is sent after 500ms of output idle time -- Go ahead (GA) isn't typically used
with interactive programs, it is probably best to disable it.

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

    pip install telnetlib3[with_tui]

A dedicated CLI entry point is provided::

    telnetlib3-fingerprint-server --data-dir data

This uses :class:`~telnetlib3.fingerprinting.FingerprintingServer` as the
protocol factory and :func:`~telnetlib3.fingerprinting.fingerprinting_server_shell`
as the default shell. All ``telnetlib3-server`` options (``--host``, ``--port``,
etc.) are accepted.

Storage
-------

Results are saved as JSON files organized by fingerprint hash::

    <data-dir>/client/<telnet-hash>/<terminal-hash>/

Moderating
----------

The ``bin/moderate_fingerprints.py`` script provides an interactive CLI for
reviewing client-submitted name suggestions and assigning names to hashes::

    export TELNETLIB3_DATA_DIR=./data
    python bin/moderate_fingerprints.py


Fingerprinting Client
=====================

The ``telnetlib3-fingerprint`` CLI connects to a remote telnet server,
probes its supported telnet options, captures the login banner, and saves a
structured JSON fingerprint.  This is the reverse of the fingerprinting
server -- it fingerprints *servers* instead of clients.

Running
-------

::

    telnetlib3-fingerprint example.com 23

Options:

- ``--data-dir <path>`` -- directory for fingerprint data
  (default: ``$TELNETLIB3_DATA_DIR``).
- ``--save-json <path>`` -- write the JSON result to a specific file instead
  of ``<data-dir>/server/<hash>/``.
- ``--connect-timeout <secs>`` -- TCP connection timeout (default 10).
- ``--silent`` -- suppress fingerprint output to stdout.

The fingerprint JSON records which options the server offered (WILL) and
requested (DO), which it refused, the pre-login banner text, and optional
DNS resolution results.  Files are stored under::

    <data-dir>/server/<protocol-hash>/<session-hash>.json

The ``bin/moderate_fingerprints.py`` script handles both client and server
fingerprints.


MUD Server
==========

The public telnetlib3 demonstration MUD Server is::

    telnet 1984.ws 6063

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
:meth:`~telnetlib3.stream_writer.TelnetWriter.send_gmcp`,
:meth:`~telnetlib3.stream_writer.TelnetWriter.send_msdp`, and
:meth:`~telnetlib3.stream_writer.TelnetWriter.send_mssp`.

Running
-------

The repository includes a "mini-MUD" example at `bin/server_mud.py
<https://github.com/jquast/telnetlib3/blob/master/bin/server_mud.py>`_ with
rooms, combat, weapons, GMCP/MSDP/MSSP support, and basic persistence.  MUD
servers usually run in "line mode"::

    telnetlib3-server --line-mode --shell bin.server_mud.shell

Connect with any telnet or MUD client::

    telnetlib3-client localhost 6023

Legacy telnetlib Compatibility
==============================

Python's ``telnetlib`` was removed in Python 3.13 (`PEP 594
<https://peps.python.org/pep-0594/>`_). telnetlib3 includes a verbatim copy from Python 3.12 with its original test
suite::

    # OLD:
    from telnetlib import Telnet

    # NEW:
    from telnetlib3.telnetlib import Telnet

The legacy module has limited negotiation support and is maintained for
compatibility only.

Modern Alternative
------------------

:mod:`telnetlib3.sync` provides a modern blocking interface:

======================  ================================================
Old telnetlib           :mod:`telnetlib3.sync`
======================  ================================================
``Telnet(host)``        :class:`~telnetlib3.sync.TelnetConnection`
``tn.read_until()``     :meth:`~telnetlib3.sync.TelnetConnection.read_until`
``tn.read_some()``      :meth:`~telnetlib3.sync.TelnetConnection.read_some`
``tn.write()``          :meth:`~telnetlib3.sync.TelnetConnection.write`
``tn.close()``          :meth:`~telnetlib3.sync.TelnetConnection.close`
======================  ================================================

Enhancements over legacy telnetlib:

- Full RFC 854 protocol negotiation (NAWS, TTYPE, BINARY, ECHO, SGA)
- :meth:`~telnetlib3.sync.TelnetConnection.wait_for` to await negotiation states
- :meth:`~telnetlib3.sync.TelnetConnection.get_extra_info` for terminal type, size
  and other metadata
- :attr:`~telnetlib3.sync.TelnetConnection.writer` property for protocol state inspection
- Server support via :class:`~telnetlib3.sync.BlockingTelnetServer`
