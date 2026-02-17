"""
Shell callback: TLS-encrypted echo server (TELNETS).

Generate a self-signed certificate for testing::

    openssl req -x509 -newkey rsa:2048 -keyout key.pem -out cert.pem \
        -days 365 -nodes -subj '/CN=localhost'

Usage::

    telnetlib3-server --ssl-certfile cert.pem --ssl-keyfile key.pem \
        --shell=bin.server_tls.shell

Connect with the telnetlib3 client::

    telnetlib3-client --ssl --ssl-cafile cert.pem localhost 6023

Or with openssl::

    openssl s_client -connect localhost:6023 -quiet
"""


async def shell(reader, writer):
    """Simple echo shell over TLS."""
    writer.write("Welcome to the TLS echo server!\r\n")
    await writer.drain()

    while True:
        data = await reader.read(256)
        if not data:
            break
        writer.write(f"echo: {data}\r\n")
        await writer.drain()

    writer.close()
