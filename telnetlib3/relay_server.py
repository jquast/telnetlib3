CR, LF, NUL = '\r\n\x00'

import logging
import asyncio
from .server_shell import readline
from .client import open_connection
from .accessories import make_reader_task

async def relay_shell(client_reader, client_writer):
    """
    An example 'telnet relay shell', appropriate for use with
    telnetlib3.create_server, run command::

        telnetlib3 --shell telnetlib3.relay_server.relay_shell

    This function is a :func:`~asyncio.coroutine`.

    This relay service is very basic, it still needs to somehow forward the TERM
    type and environment variable of value COLORTERM
    """
    log = logging.getLogger('relay_server')

    password_prompt = readline(client_reader, client_writer)
    password_prompt.send(None)

    client_writer.write("Telnet Relay shell ready." + CR + LF + CR + LF)

    client_passcode = '867-5309'
    num_tries = 3
    next_host, next_port = '1984.ws', 23
    passcode = None
    for _ in range(num_tries):
        client_writer.write('Passcode: ')
        while passcode is None:
            inp = await client_reader.read(1)
            if not inp:
                log.info('EOF from client')
                return
            passcode = password_prompt.send(inp)
        await asyncio.sleep(1)
        client_writer.write(CR + LF)
        if passcode == client_passcode:
            log.info('passcode accepted')
            break
        passcode = None

    # wrong passcode after 3 tires
    if passcode is None:
        log.info('passcode failed after %s tries', num_tries)
        client_writer.close()
        return

    # connect to another telnet server (next_host, next_port)
    loop = asyncio.get_event_loop()
    client_writer.write('Connecting to {}:{} ... '.format(
        next_host, next_port))
    server_reader, server_writer = await open_connection(
        next_host, next_port,
        cols=client_writer.get_extra_info('cols'),
        rows=client_writer.get_extra_info('rows'))
    client_writer.write('connected!' + CR + LF)
 
    done = []
    client_stdin = make_reader_task(client_reader)
    server_stdout = make_reader_task(server_reader)
    wait_for = {client_stdin, server_stdout}
    while wait_for:
        done, remaining = await asyncio.wait(
            wait_for, return_when=asyncio.FIRST_COMPLETED)
        while done:
            task = done.pop()
            wait_for.remove(task)
            if task == client_stdin:
                inp = task.result()
                if inp:
                    server_writer.write(inp)
                    client_stdin = make_reader_task(client_reader)
                    wait_for.add(client_stdin)
                else:
                    log.info('EOF from client')
                    server_writer.close()
            elif task == server_stdout:
                out = task.result()
                if out:
                    client_writer.write(out)
                    server_stdout = make_reader_task(server_reader)
                    wait_for.add(server_stdout)
                else:
                    log.info('EOF from server')
                    client_writer.close()
    log.info('No more tasks: relay server complete')
