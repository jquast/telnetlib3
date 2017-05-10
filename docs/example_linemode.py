#!/usr/bin/env python3
"""
A very simple linemode server shell.
"""
# std
import asyncio
import sys
import pkg_resources

# local
import telnetlib3

@asyncio.coroutine
def shell(reader, writer):
    from telnetlib3 import WONT, ECHO
    writer.iac(WONT, ECHO)

    while True:
        writer.write('> ')

        recv = yield from reader.readline()

        # eof
        if not recv:
            return

        writer.write('\r\n')

        if recv.rstrip() == 'bye':
            writer.write('goodbye.\r\n')
            yield from writer.drain()
            writer.close()

        writer.write(''.join(reversed(recv)) + '\r\n')

if __name__ == '__main__':
    kwargs = telnetlib3.parse_server_args()
    kwargs['shell'] = shell
    telnetlib3.run_server(**kwargs)
    #sys.argv.append('--shell={
    sys.exit(
        pkg_resources.load_entry_point(
            'telnetlib3', 'console_scripts', 'telnetlib3-server')()
    )
