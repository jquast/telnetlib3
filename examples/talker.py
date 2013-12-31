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
import time
import sys

import asyncio
from telnetlib3 import Telsh, TelnetStream, TelnetServer

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

# TODO: We should be using blessings(terminfo database), for various
#       terminal capabilities used here, would require dispatching
#       terminfo lookups to subprocesses.
# TODO: If we do use blessings, we could use the keyboard-awareness
#       branch to provide readline history abilities.


class TalkerServer(TelnetServer):
    def __init__(self,
                 shell=Telsh,
                 stream=TelnetStream,
                 encoding='utf8',
                 log=logging):
        super().__init__(shell, stream, encoding, log)

    # remove 'USER' as a readonly env, we use this to set the actual /nick
    readonly_env = ['HOSTNAME', 'REMOTE_IP', 'REMOTE_HOST', 'REMOTE_PORT',]

    def connection_made(self, transport):
        super().connection_made(transport)

        global clients
        self.id = (self.client_ip, self.client_port)
        clients[self.id] = self
        self.env_update(
            {   # the default 'channel',
                'CHANNEL': '#default',
                # {shell}-{version} [Lag: {2.2f}s] [#channel]
                'PS1': '%s-%v [Lag: %$LAG] [%$CHANNEL] ',
                # timeout is 6h (360m)
                'TIMEOUT': '360',
                # Lag/pingtime, measured with TM (Timing Mark)
                'LAG': '??',
            })
        self._ping = time.time()
        self._test_lag = self._loop.call_soon(self.send_timing_mark)

    def connection_lost(self, exc):
        self._test_lag.cancel()
        super().connection_lost(exc)
        global clients
        clients.pop(self.id, None)

    def after_telopt_negotiation(self, status):
        if not status.cancelled():
            if self.env['USER'] == 'unknown':
                self.shell.stream.write('\r\n'
                                        '** Set your nickname using /nick'
                                        '\r\n')
                self.shell.display_prompt()
        super().after_telopt_negotiation(status)

    def send_timing_mark(self):
        from telnetlib3.telopt import DO, TM
        self._test_lag.cancel()
        self._ping = time.time()
        self.stream.iac(DO, TM)

    def handle_timing_mark(self, cmd):
        lag_time = time.time() - self._ping
        self.env_update({'LAG': '{:0.2f}'.format(lag_time)})
        self._test_lag = self._loop.call_later(30, self.send_timing_mark)

    def recieve(self, action, *args):
        if action == 'say':
            (nick, msg) = args
            self.shell.display_text('{}: {}'.format(
                self.shell.standout(nick), msg))
        elif action == 'me':
            (nick, msg) = args
            self.shell.display_text(
                '{decorator} {nick} {msg}'
                .format(decorator=self.shell.dim('*'),
                        nick=nick,
                        msg=msg))
        elif action == 'join':
            (nick, msg) = args
            self.shell.display_text(
                '{decorator} {nick} has joined {channel}{msg}'
                .format(decorator=self.shell.dim('**'),
                        nick=self.shell.standout(nick),
                        channel=self.shell.dim(self.env['CHANNEL']),
                        msg=' (:: {})'.format(msg) if msg else ''))
        elif action == 'part':
            (nick, msg) = args
            self.shell.display_text(
                '{decorator} {nick} has left {channel}{msg}'
                .format(decorator=self.shell.dim('**'),
                        nick=self.shell.standout(nick),
                        channel=self.shell.dim(self.env['CHANNEL']),
                        msg=' (:: {})'.format(msg) if msg else ''))
        elif action == 'rename':
            (newnick, oldnick) = args
            self.shell.display_text(
                '{decorator} {oldnick} has renamed to {newnick}'
                .format(decorator=self.shell.dim('**'),
                        oldnick=self.shell.standout(oldnick),
                        newnick=self.shell.standout(newnick),))
        self.shell.display_prompt(redraw=True)


class TalkerShell(Telsh):
    """ A remote line editing shell for a "talker" implementation.
    """
    #: name of shell %s in prompt escape
    shell_name = 'pytalk'

    #: version of shell %v in prompt escape
    shell_ver = '0.3'

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
        ('/slc', None),
        ('/whoami', None),
        ('/whereami', None),
        ('/channels', None),
        ('/users', None),
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
        """ Callback from ``line_received()`` for input line processing.

            Derived from telsh: this 'talker' implementation does not implement
            shell escaping (shlex). Anything beginning with '/' is passed to
            cmdset_command with leading '/' removed, and certain commands
            such as /assign, /set, /command, and; anything else is passed
            to method 'cmdset_say' (public chat)
        """
        self.display_text()
        if data.startswith('/'):
            cmd, *args = data.split(None, 1)
            self.log.info((data, cmd, args))
            val = self.cmdset_command(cmd[1:], *args)
            if val != None:
                self.stream.write('\r\n')
            return val
        if not data.strip():
            # Nothing to say!
            return 0
        val = self.cmdset_say(data)
        if val != None:
            self.stream.write('\r\n')
        return val

    def cmdset_toggle(self, *args):
        self.log.debug((self, args))
        if len(args) is 0:
            self.stream.write('{} [{}]'.format(
                'fullscreen', self.standout('ON') if self.mode_fullscreen
                else self.dim('off')))
            return super().cmdset_toggle(*args)
        opt = args[0].lower()
        if opt in ('fullscreen', '_all'):
            self.mode_fullscreen = not self.mode_fullscreen
            if self.mode_fullscreen:
                self.enter_fullscreen()
            else:
                self.exit_fullscreen()
            self.display_text('fullscreen {}abled.'.format(
                'en' if self.send_bell else 'dis'))
    cmdset_toggle.__doc__ = Telsh.cmdset_toggle.__doc__
    def cmdset_channels(self, *args):
        " List active channels and number of users. "
        channels = {}
        for client in clients.values():
            channel = client.env['CHANNEL']
            channels[channel] = channels.get(channel, 0) + 1
        self.stream.write("{}  {}".format(
            self.underline('channel'.rjust(15)),
            self.underline('# users')))
        self.stream.write("\r\n{}".format(
            '\r\n'.join([
                "{:>15}  {:<7}".format(channel, num_users)
                for channel, num_users in sorted(channels.items())])))
        return 0

    def cmdset_users(self, *args):
        " List clients currently connected. "
        self.stream.write("{}  {}  {}".format(
            self.underline('user'.rjust(15)),
            self.underline('channel'.rjust(15)),
            self.underline('origin'.rjust(15))))
        userlist = ['{env[USER]:>15}  '
                    '{env[CHANNEL]:>15}  '
                    '{env[REMOTE_HOST]:>15}'.format(env=server.env)
                    for server in clients.values()]
        self.stream.write("\r\n{}".format(
            '\r\n'.join(sorted(userlist))))
        return 0

    def broadcast(self, action, data):
        mynick = self.server.env['USER']
        mychan = self.server.env['CHANNEL']

        # validate within a channel, and /nick has been set,
        if not mychan:
            self.stream.write('You must first {} a channel !'.format(
                self.standout('/join')))
            return 1

        elif mynick == 'unknown':
            self.stream.write('\r\nYou must first set a {} !'.format(
                self.standout('/nick')))
            return 1

        else:
            # validate that our nickname isn't already taken, in
            # which case we become blocked until we select a new /nick
            for rc in ([client for client in clients.values()
                        if client != self.server]):
                if rc.env['USER'] == mynick:
                    self.stream.write('Your nickname {} is already taken, '
                                      'select a new /nick !'.format(
                                          self.standout(mynick)))
                    return 1

        # forward data to everybody in matching channel name
        for remote_client in ([client for client in clients.values()]):
            if remote_client.env['CHANNEL'] == mychan:
                remote_client.recieve(action, mynick, data)

        self.log.info('{} {} {}: {}'.format(mychan, mynick, action, data))
        return None

    def cmdset_me(self, *args):
        " Broadcast 'action' message to current channel. "
        from telnetlib3.telsh import name_unicode
        # transpose any unprintable characters from input, to prevent a
        # user from broadcasting cursor position sequences, for example.
        msg = u''.join([name_unicode(char) for char in ' '.join(args)])
        if msg:
            return self.broadcast('me', msg)

    def cmdset_say(self, *args):
        " Broadcast message to current channel. "
        from telnetlib3.telsh import name_unicode
        # transpose any unprintable characters from input, to prevent a
        # user from broadcasting cursor position sequences, for example.
        msg = u''.join([name_unicode(char) for char in ' '.join(args)])
        if msg:
            return self.broadcast('say', msg)

    def cmdset_join(self, *args):
        " Switch-to talker channel. "
        mynick = self.server.env['USER']
        chan, msg = (args[0].split(None, 1) if args
                     else ('default', ''))
        if not chan.startswith('#'):
            chan = '#{}'.format(chan)
        if len(chan) > self.MAX_CHAN:
            self.stream.write('Channel name too long.')
            return 1
        self.cmdset_assign('CHANNEL={}'.format(chan))
        self.log.info('{} has joined {}{}'.format(
            mynick, chan, msg and ': {}'.format(msg) or ''))
        return self.broadcast('join', msg)

    def cmdset_part(self, *args):
        " Switch-off talker channel. "
        mynick = self.server.env['USER']
        chan, msg = (args[0].split(None, 1) if args
                     else (self.server.env['CHANNEL'], ''))
        if not chan:
            self.stream.write('Channel not set.')
            return 1
        if not chan.startswith('#'):
            chan = '#{}'.format(chan)
        if len(chan) > self.MAX_CHAN:
            self.stream.write('Channel name too long.')
            return 1
        val = self.broadcast('part', msg)
        self.cmdset_assign('CHANNEL=')
        self.log.info('{} has left {}{}'.format(
            mynick, chan, msg and ': {}'.format(msg) or ''))
        return val

    def cmdset_nick(self, *args):
        " Display or change handle. "
        mynick = self.server.env['USER']
        if not args:
            self.stream.write('Your handle is {}'.format(
                self.standout(mynick)))
            return 0
        newnick = args[0]
        if len(newnick) > self.MAX_NICK:
            self.stream.write('Nickname too long.')
            return 1
        elif newnick == mynick:
            self.stream.write('You is what it is.')
            return 1
        for client in clients.values():
            if client.env['USER'] == newnick:
                self.stream.write('Nickname {} already taken.'.format(
                    self.standout(newnick)))
                return 1
        self.cmdset_assign('USER={}'.format(newnick))
        self.log.info('{} renamed to {}'.format(mynick, newnick))
        self.stream.write('Your name is now {}'.format(
            self.standout(newnick)))
        return self.broadcast('rename', mynick)

    def cmdset_quit(self, *args):
        " Disconnect from server. "
        if self.mode_fullscreen:
            self.exit_fullscreen()
        return self.server.logout()


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
