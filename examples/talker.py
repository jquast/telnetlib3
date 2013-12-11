#!/usr/bin/env python3
"""
An example 'Talker' implementation using the telnetlib3 library.

A talker is a chat system that people use to talk to each other.
Dating back to the 1980s, they were a predecessor of instant messaging.
People log into the talkers remotely (usually via telnet), and have
a basic text interface with which to communicate with each other.

https://en.wikipedia.org/wiki/Talker
"""
import collections
import argparse
import logging
import sys

import telnetlib3
from telnetlib3.telsh import name_unicode
import asyncio

ARGS = argparse.ArgumentParser(description="Run simple telnet server.")
ARGS.add_argument(
    '--host', action="store", dest='host',
    default='127.0.0.1', help='Host name')
ARGS.add_argument(
    '--port', action="store", dest='port',
    default=6023, type=int, help='Port number')
ARGS.add_argument(
    '--loglevel', action="store", dest="loglevel",
    default='info', type=str, help='Loglevel (debug,info)')


clients = {}


class TalkerServer(telnetlib3.TelnetServer):
    # remove 'USER' as a readonly env, we use this to set the actual /nick
    readonly_env = ['HOSTNAME', 'REMOTE_IP', 'REMOTE_HOST', 'REMOTE_PORT',]

    def connection_made(self, transport):
        telnetlib3.TelnetServer.connection_made(self, transport)

        global clients
        self.id = (self.client_ip, self.client_port)
        clients[self.id] = self
        self.env_update({'CHANNEL': '#default',        # the default 'channel',
                         'PS1': '%s-%v [%$CHANNEL] ',  # shell-version [#channel]
                         'TIMEOUT': '360',             # timeout is 6h (360m)
                         })

    def connection_lost(self, exc):
        telnetlib3.TelnetServer.connection_lost(self, exc)

        global clients
        clients.pop(self.id, None)

    def after_negotiation(self, status):
        telnetlib3.TelnetServer.after_negotiation(self, status)
        if not status.cancelled():
            if self.env['USER'] == 'unknown':
                self.shell.stream.write(
                    '** Set your nickname using /nick')

    def recieve(self, nick, msg):
        self.shell.stream.write('\r\x1b[K{}: {}\r\n'.format(
            self.shell.standout(nick), msg))
        self.shell.display_prompt(redraw=True)


class TalkerShell(telnetlib3.Telsh):
    """ A remote line editing shell for a "talker" implementation.
    """
    #: name of shell %s in prompt escape
    shell_name = 'pytalk'

    #: version of shell %v in prompt escape
    shell_ver = '0.1'

    #: A cyclical collections.OrderedDict of command names and nestable
    #  arguments, or None for end-of-command, used by ``tab_received()``
    #  to provide autocomplete and argument cycling.
    cmdset_autocomplete = collections.OrderedDict([
        ('/help', collections.OrderedDict([
            ('status', None),
            ('whoami', None),
            ('toggle', None),
            ('logoff', None),
            ('whereami', None),
            ('nick', None),
            ('join', None),
            ('part', None),
            ('quit', None),
            ]), ),
        ('/toggle', collections.OrderedDict([
            ('echo', None),
            ('outbinary', None),
            ('inbinary', None),
            ('goahead', None),
            ('color', None),
            ('xon-any', None),
            ('bell', None),
            ]), ),
        ('/status', None),
        ('/whoami', None),
        ('/whereami', None),
        ('/listclients', None),
        ('/logoff', None),
        ('/nick', None),
        ('/join', None),
        ('/part', None),
        ])

    #: Maximum nickname size
    MAX_NICK = 9

    #: Maximum channel size
    MAX_CHAN = 32

    def line_received(self, input):
        """ Callback for each line received, processing command(s) at EOL.
        """
        self.log.debug('line_received: {!r}'.format(input))
        input = input.rstrip(self.strip_eol)
        try:
            self._lastline.clear()
            retval = self.process_cmd(input)
            if retval is not None:
                self.stream.write('\r\n')
                self.display_prompt()
        except Exception:
            self.display_exception(*sys.exc_info(), level=logging.INFO)
            self.bell()

    def process_cmd(self, data):
        """ .. method:: process_cmd(input : string) -> int

            Callback from ``line_received()`` for input line processing.

            Derived from telsh: this 'talker' implementation does not implement
            shell escaping (shlex). Anything beginning with '/' is passed to
            cmdset_command; all else is passed to method 'say' (public chat)
        """
        if data.startswith('/'):
            self.stream.write('\r\n')
            return self.cmdset_command(*data.split(None, 1))
        return self.say(data)

    def cmdset_command(self, cmd, *args):
        self.log.debug('command {!r}{!r}'.format(cmd, args))
        if not len(cmd) and not len(args):
            return None
        if cmd in ('/help',):
            return self.cmdset_help(*args)
        elif cmd == '/debug':
            return self.cmdset_debug(*args)
        elif cmd in ('/quit', '/logoff',):
            self.server.logout()
        elif cmd == '/status':
            self.display_status()
        elif cmd == '/join':
            return self.cmdset_join(*args)
        elif cmd == '/part':
            return self.cmdset_assign('CHANNEL=')
        elif cmd == '/nick':
            return self.cmdset_nick(*args)
        elif cmd == '/listclients':
            return self.cmdset_listclients()
        elif cmd == '/whoami':
            self.stream.write('\r\n{}.'.format(self.server.__str__()))
        elif cmd == '/whereami':
            return self.cmdset_whereami(*args)
        elif cmd == '/toggle':
            return self.cmdset_toggle(*args)
        elif cmd == '/debug':
            return self.cmdset_debug(*args)
        elif cmd:
            disp_cmd = u''.join([name_unicode(char) for char in cmd])
            self.stream.write('\r\n{!s}: command not found.'.format(disp_cmd))
            return 1
        return 0

    def cmdset_listclients(self):
        """
        List clients currently connected. 
        """
        clients_info = ("{} - {}".format(server.env['USER'], key_)
                        for (key_, server) in clients.items())
        output = "\r\n".join(clients_info)
        self.stream.write("\r\n{}".format(output))
        return 0

    def cmdset_help(self, *args):
        if not len(args):
            self.stream.write('\r\nAvailable commands:\r\n')
            self.stream.write(', '.join(self.cmdset_autocomplete.keys()))
            return 0
        cmd = args[0].lower()
        if cmd == 'help':
            self.stream.write('\r\nDON\'T PANIC.')
            return -42
        elif cmd == 'logoff':
            self.stream.write('\r\nTerminate connection.')
        elif cmd == 'status':
            self.stream.write('\r\nDisplay operating parameters.')
        elif cmd == 'whoami':
            self.stream.write('\r\nDisplay session identifier.')
        elif cmd == 'whereami':
            self.stream.write('\r\nDisplay server name')
        elif cmd == 'toggle':
            self.stream.write('\r\nToggle operating parameters.')
        elif cmd == 'join':
            self.stream.write('\r\nSwitch-to talker channel.')
        elif cmd == 'part':
            self.stream.write('\r\nSwitch-off talker channel.')
        elif cmd == 'nick':
            self.stream.write('\r\nSet your handle.')
        else:
            return 1
        if (cmd and cmd in self.cmdset_autocomplete
                and self.cmdset_autocomplete[cmd] is not None):
            self.stream.write('\r\n{}'.format(', '.join(
                self.cmdset_autocomplete[cmd].keys())))
        return 0

    def say(self, data):

        mynick = self.server.env['USER']
        mychan = self.server.env['CHANNEL']

        # validate within a channel, and /nick has been set,
        if not mychan:
            self.stream.write('\r\nYou must first {} a channel !'.format(
                self.standout('/join')))
            return 1
        elif mynick == 'unknown':
            self.stream.write('\r\nYou must first set a {} !'.format(
                self.standout('/nick')))
            return 1

        # validate that our nickname isn't already taken, in
        # which case we become blocked until we select a new /nick
        for rc in ([client for client in clients.values()
                    if client != self.server]):
            if rc.env['USER'] == mynick:
                self.stream.write('\r\nYou cannot speak! Your nickname {} is '
                                  'already taken, select a new /nick !'.format(
                                      self.standout(mynick)))
                return 1
        # validate that our nickname isn't already taken, in
        data = u''.join([name_unicode(char) for char in data])

        # forward data to everybody in matching channel name
        for remote_client in ([client for client in clients.values()]):
            if remote_client.env['CHANNEL'] == mychan:
                remote_client.recieve(mynick, data)

        # loglevel info only for ourselves
        self.log.info('{}/{}: {}'.format(mynick, mychan, data))

    def cmdset_join(self, *args):
        chan = args[0] if args else 'default'
        chan = '#{}'.format(chan) if not chan.startswith('#') else chan
        if len(chan) > self.MAX_CHAN:
            self.stream.write('\r\nChannel name too long.')
            return 1
        self.cmdset_assign('CHANNEL={}'.format(chan))
        return 0

    def cmdset_nick(self, *args):
        mynick = self.server.env['USER']
        if not args:
            self.stream.write('\r\nYour name is {}'.format(
                self.standout(mynick)))
            return 0

        newnick = args[0]
        if len(newnick) > self.MAX_NICK:
            self.stream.write('\r\nNickname too long.')
            return 1
        for client in clients.values():
            if client.env['USER'] == newnick:
                self.stream.write('\r\nNickname {} already taken.'.format(
                    self.standout(mynick)))
                return 1
        self.cmdset_assign('USER={}'.format(newnick))
        self.log.info('{} renamed to {}/{}'.format(
            mynick, newnick, self.server.env['USER']))
        self.stream.write('\r\nYour name is now {}'.format(
            self.standout(newnick)))
        return 0


def start_server(loop, log, host, port):
    # create_server recieves a callable that returns a Protocol
    # instance; wrap using `lambda' so that the specified logger
    # instance (whose log-level is specified by cmd-line argument)
    # may be used.
    func = loop.create_server(
        lambda: TalkerServer(log=log, shell=TalkerShell), host, port)
    server = loop.run_until_complete(func)
    log.info('Listening on %s', server.sockets[0].getsockname())


def main():
    args = ARGS.parse_args()
    if ':' in args.host:
        args.host, port = args.host.split(':', 1)
        args.port = int(port)

    # use generic 'logging' instance, and set the log-level as specified by
    # command-line argument --loglevel
    fmt = '%(asctime)s %(levelname)s %(filename)s:%(lineno)d %(message)s'
    logging.basicConfig(format=fmt)
    log = logging.getLogger('telnet_server')
    log.setLevel(getattr(logging, args.loglevel.upper()))

    loop = asyncio.get_event_loop()
    start_server(loop, log, args.host, args.port)
    loop.run_forever()

if __name__ == '__main__':
    main()
