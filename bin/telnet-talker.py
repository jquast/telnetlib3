#!/usr/bin/env python3
"""
An example 'Talker' implementation using the telnetlib3 library::

    A talker is a chat system that people use to talk to each other.
    Dating back to the 1980s, they were a predecessor of instant messaging.
    People log into the talkers remotely (usually via telnet), and have
    a basic text interface with which to communicate with each other.

    https://en.wikipedia.org/wiki/Talker

This demonstrates augmenting the Telsh shell to provide an irc-like interface,
as well as a "fullscreen" experience with basic terminal capabilities
"""
import collections
import functools
import argparse
import logging
import random
import time
import sys

import asyncio
from telnetlib3 import Telsh, TelnetStream, TelnetServer
from telnetlib3.telsh import prompt_eval, resolve_prompt, name_unicode, EDIT

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

MAX_HISTORY = 50
clients, channel_history = {}, {}

History = collections.namedtuple('History', (
    'time', 'action', 'data', 'nick', 'target',))

# TODO: We should be using blessed/terminfo(5) database, for various
#       terminal capabilities used here, would require dispatching
#       terminfo(5) lookups to subprocesses.
# TODO: A full readline-like implementation is needed.
# TODO: If we do use blessed, we could provide readline history.

class TalkerServer(TelnetServer):
    """ This TelnetServer implementation registers each new connection in a
    globals `clients' hash, and receives irc events by method `receive'.
    """

    #: Whether fullscreen mode should be enabled for terminals whose
    #: shell value ``does_styling`` is true.
    do_fullscreen = True

    #: default channel that users auto-join
    main_channel = '#default'

    def __init__(self,
                 shell=Telsh,
                 stream=TelnetStream,
                 encoding='utf-8',
                 log=logging):
        super().__init__(shell, stream, encoding, log)

        if 'USER' in self.readonly_env:
            self.readonly_env.remove('USER')
        self._test_lag = asyncio.Future()

    def connection_made(self, transport):
        super().connection_made(transport)

        # register channel in global list `clients', which it is removed from
        # on disconnect. This is used for a very primitive, yet effective
        # method of IPC and client<->server<->client communication.
        global clients
        self.id = (self.client_ip, self.client_port)
        clients[self.id] = self
        self.env_update({'PS1': '[%T] [Lag: %$LAG] [%$CHANNEL] ',
                         'PS2': '[%T] ',
                         'TIMEOUT': '360',
                         'LAG': '??',
                         })
        self._ping = time.time()
        self._test_lag = self._loop.call_soon(self.send_timing_mark)

    def connection_lost(self, exc):
        self._test_lag.cancel()
        super().connection_lost(exc)
        global clients
        if self.id in clients:
            channel = clients[self.id].env.get('CHANNEL')
            clients[self.id].shell.broadcast(
                'quit', exc or 'connection lost', channel)
        clients.pop(self.id, None)

    def begin_negotiation(self):
        """ Begin negotiation just as the standard TelnetServer,
            except that the prompt is not displayed early.  Instead,
            display a banner, showing the prompt only *after*
            negotiation has completed (or timed out).
        """
        if self._closing:
            self._telopt_negotiation.cancel()
            return
        from telnetlib3.telopt import DO, TTYPE
        self.stream.iac(DO, TTYPE)
        self._loop.call_soon(self.check_telopt_negotiation)
        self.display_banner()

    def display_banner(self):
        """ Our own on-connect banner. """
        # we do not display the prompt until negotiation
        # is considered successful.
        self.shell.display_text(
            prompt_eval(self.shell, u'\r\n'.join((
                u'', u'',
                u'Welcome to %s version %v',
                u'Local time is %t %Z (%z)',
                u'Plese wait... {}'.format(random_busywait()),
                u'' u'',))))

    def after_telopt_negotiation(self, status):
        """ augment default callback, checking and warning if
            /nick is unset, and displaying the prompt for the first
            time (unless the user already smashed the return key.)
        """
        super().after_telopt_negotiation(status)
        if status.cancelled():
            return
        mynick = self.env['USER']
        if mynick == 'unknown':
            self.shell.display_text(u''.join((
                '\r\n', '{} Set your nickname using /nick'.format(
                    self.shell.standout('!!')), '\r\n')))
        else:
            for client in clients.values():
                while (client != self and
                       client.env['USER'] == self.env['USER']):
                    self.env['USER'] = '{}_'.format(self.env['USER'])
            if self.env['USER'] != mynick:
                old, new = mynick, self.env['USER']
                self.shell.display_text('Handle {} already taken, '
                                        'using {}.'.format(
                                            self.shell.standout(old),
                                            self.shell.standout(new)))

        if self.do_fullscreen and self.shell.does_styling:
            self.shell.mode_fullscreen = True
        self._loop.call_later(0, self.shell.cmdset_join)
        self.shell._redraw.cancel()
        self.shell._redraw = self._loop.call_later(0.100, self.shell.redraw_received)

    def send_timing_mark(self):
        from telnetlib3.telopt import DO, TM
        self._test_lag.cancel()
        self._ping = time.time()
        self.stream.iac(DO, TM)

    def handle_timing_mark(self, cmd):
        lag_time = time.time() - self._ping
        self.env_update({'LAG': '{:0.2f}'.format(lag_time)})
        self._test_lag = self._loop.call_later(30, self.send_timing_mark)

    def receive(self, action, *args, resolver=None):
        ps2 = prompt_eval(self.shell, self.env.get('PS2', '[%P] '), resolver=resolver)
        if action == 'say':
            (nick, msg) = args
            self.shell.display_text(
                '{ps2}{nick}: {msg}'.format(
                    ps2=ps2, nick=self.shell.standout(nick), msg=msg))
        elif action == 'privmsg':
            self.log.info('{}'.format(args))
            (from_nick, to_nick, msg) = args
            mine = from_nick == self.env['USER']
            decorate, pnick = (('=>', to_nick) if mine else ('<=', from_nick))
            self.shell.display_text(
                '{ps2}{decorate} {pnick}: {msg}'.format(
                    ps2=ps2, decorate=self.shell.dim(decorate),
                    pnick=self.shell.standout(pnick), msg=msg))
        elif action == 'me':
            (nick, msg) = args
            self.shell.display_text(
                '{ps2}{decorator} {nick} {msg}'.format(
                    ps2=ps2, decorator=self.shell.dim('*'),
                    nick=nick, msg=msg))
        elif action == 'join':
            (nick, msg, channel) = args
            self.shell.display_text(
                '{ps2}{decorator} {nick} has joined {channel}{msg}.'
                .format(ps2=ps2, decorator=self.shell.dim('**'),
                        nick=self.shell.standout(nick),
                        channel=self.shell.dim(channel),
                        msg=' (:: {})'.format(msg) if msg else ''))
        elif action in ('part', 'quit'):
            (nick, msg, channel) = args
            self.shell.display_text(
                '{ps2}{decorator} {nick} has left {channel}{msg}.'
                .format(ps2=ps2, decorator=self.shell.dim('**'),
                        nick=self.shell.standout(nick),
                        channel=self.shell.dim(channel),
                        msg=' (:: {})'.format(msg) if msg else ''))
        elif action == 'rename':
            (newnick, oldnick) = args
            self.shell.display_text(
                '{ps2}{decorator} {oldnick} has renamed to {newnick}.'
                .format(ps2=ps2, decorator=self.shell.dim('**'),
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

    #: Maximum nickname size
    MAX_NICK = 14

    #: Maximum channel size
    MAX_CHAN = 24

    #: Has a prompt yet been displayed? (after_telopt_negotiation)
    _prompt_displayed = False

    #: Fullscreen mode? (Entered when does_styling is True)
    mode_fullscreen = False

    #: Private message history for each session instance, allowing redraws
    #: to interleave with channel broadcast history on redraw.
    private_history = collections.deque([], MAX_HISTORY)

    #: Used as a callback for redrawing screen on winsize change.
    _redraw = asyncio.Future()

    def display_text(self, text=None):
        """ prepare for displaying text to scroll region when fullscreen,
            otherwise, simply output '\r\n', then display value of `text',
            if any.  """
        if self.mode_fullscreen and self.window_height:
            # CSI y;x H, cursor address (y,x) (cup)
            # move to bottom-right of scrolling region and write
            # a space; causing a scroll before writing output
            self.stream.write('\x1b[{};{}H '.format(
                    self.window_height - 2,
                    self.window_width + 1))
            self.stream.write('')
        else:
            # return carriage
            self.stream.write('\r')
        # clear to eol
        self.stream.write('\x1b[K')

        if text:
            self.stream.write(text)
            if not self.mode_fullscreen:
                self.stream.write('\r\n')

    def line_received(self, text):
        """ Callback for each line received, processing command(s) at EOL.
        """
        self.log.debug('line_received: {!r}'.format(text))
        text = text.rstrip()
        try:
            self._lastline.clear()
            if not text:
               self.display_text('')
               self.display_prompt(redraw=True)
            elif self.process_cmd(text) is not None:
               self.display_prompt()
        except Exception:
            self.display_text('')
            self.display_exception(*sys.exc_info())
            self.bell()
            self.display_text('')
            self.display_prompt()

    def display_prompt(self, redraw=False):
        """ Talker shell prompt supports fullscreen mode: when
            fullscreen, send cursor position sequence for bottom
            of window, prefixing the prompt string; otherwise
            simply prefix with \r\n, or, only \r when redraw
            is True (standard behavior).
        """
        if self.mode_fullscreen and self.window_height:
            disp_char = lambda char: (
                self.standout(name_unicode(char))
                if not self.stream.can_write(char)
                or not char.isprintable()
                else char)
            # move to last line, first column, clear_eol
            prefix = '\x1b[{};1H\x1b[K'.format(self.window_height)
            # display current prompt (perhaps redraw/continuation)
            text = ''.join([disp_char(char) for char in self.lastline])
            output = ''.join((prefix, self.prompt, text,))
            self.stream.write(output)
            self.stream.send_ga()
        else:
            super().display_prompt(redraw)
        self._prompt_displayed = True

    def process_cmd(self, data):
        """ Callback from ``line_received()`` for input line processing.

            Derived from telsh: this 'talker' implementation does not implement
            shell escaping (shlex). Anything beginning with '/' is passed to
            ``command()`` with leading '/' removed, and certain commands
            such as /assign, /set, /command, and; anything else is passed
            to method 'cmdset_say' (public chat)
        """
        _ = self.dim
        if data.startswith('/'):
            cmd, *args = data.split(None, 1)
            # prepare output buffer for command output, only a carriage return
            # followed by a clear_eol sequence is displayed on empty string.
            self.display_text('')
            return self.command(cmd[1:], *args)
        if not data.strip():
            # Nothing to say!
            return 0
        return self.cmdset_say(data)

    @property
    def autocomplete_cmdset(self):
        ac_cmdset = super().autocomplete_cmdset
        val = collections.OrderedDict([
            ('/{}'.format(key), val) for key, val in sorted(ac_cmdset.items())
        ])
        print(val)
        return val

    @property
    def cmdset_toggle_subcmds(self):
        " Returns sub-cmds for `toggle' command. "
        subcmds = dict(super().cmdset_toggle_subcmds)
        subcmds.update({'fullscreen': None})
        return collections.OrderedDict([
            (key, val) for key, val in sorted(subcmds.items())
        ])


    @property
    def table_toggle_options(self):
        tbl_opts = super().table_toggle_options
        tbl_opts.update({'fullscreen': self.mode_fullscreen})
        return tbl_opts

    def cmdset_toggle(self, *args):
        if len(args) is 0:
            return super().cmdset_toggle()

        opt = args[0].lower()
        if opt in ('fullscreen', '_all'):
            self.mode_fullscreen = not self.mode_fullscreen
            if self.mode_fullscreen:
                self.enter_fullscreen()
            else:
                self.exit_fullscreen()
            self.stream.write('fullscreen {}abled.'.format(
                'en' if self.mode_fullscreen else 'dis'))
            if opt == '_all':
                super().cmdset_toggle(*args)
            return 0
        return super().cmdset_toggle(*args)

    cmdset_toggle.__doc__ = Telsh.cmdset_toggle.__doc__

    def cmdset_refresh(self, *args):
        """ Refresh the screen. """
        self.redraw_received(history=True)

    def editing_received(self, char, cmd):
        if cmd == EDIT.RP:
            # repaint (^r)
            self.cmdset_refresh()
        elif cmd == EDIT.AYT:
            if self.mode_fullscreen:
                # move to character buffer before output
                self.display_text('')
            super().editing_received(char, cmd)
        else:
            super().editing_received(char, cmd)

    def winsize_received(self, lines, cols):
        """ Reset scrolling region size on receipt of winsize changes.

        A Future is used or a 250ms delay for a call to ``self.refresh``,
        so that only the most recent winsize notification is received,
        so that when a window is grown or shrunk, signaling several winsize
        changes in a sequence, only the last-most after 250ms delay is
        used.
        """
        self._redraw.cancel()
        self.log.debug('scheduling redraw event (winsize)')
        self._redraw = self.server._loop.call_later(0.250, self.redraw_received)

    def redraw_received(self, history=True):
        """ Callback to redraw the screen.

        For fullscreen sessions, re-displays channel history of current channel,
        interleaved with any private messages for the current session.  Called
        by Future timer on window resize notification.
        """
        self._redraw.cancel()
        self.display_text(self.window_clear)
        if self.mode_fullscreen:
            self.enter_fullscreen()
        if history:
            self.display_history()
        if self.mode_fullscreen:
            self.display_prompt()

    def display_history(self):
        " Display history of current channel. "
        # we should go backwards, calculating the width as well, for exactly
        # one page height sized history, currently some lines may be extra
        # when they wrap around the margin.
        history = channel_history.get(self.server.env['CHANNEL'], [])
        avail, wanted = len(history), self.window_height - 2
        lastmost = slice(max(0, avail - wanted), avail)
        for hist in list(history)[lastmost]:
            resolver = functools.partial(resolve_prompt, timevalue=hist.time)
            self.server.receive(hist.action, hist.nick, *hist.data, resolver=resolver)

    @property
    def window_height(self):
        return int(self.server.env['LINES'])

    @property
    def window_width(self):
        return int(self.server.env['COLUMNS'])

    @property
    def window_clear(self):
        if self.does_styling:
            return '\x1b[H\x1b[2J'
        return ''

    def enter_fullscreen(self, lines=None, cols=None):
        """ Enter fullscreen mode (scrolling region, inputbar @bottom). """
        lines = lines or self.window_height
        cols = cols or self.window_width
        if self.window_height and self.does_styling:
            # enable line wrapping
            self.stream.write('\x1b[7h')
            # (create status line) CSI y;x H, cursor address (y,x) (cup)
            self.stream.write('\x1b[{};1H'.format(lines - 1))
            self.stream.write('_' * cols)
            # (scroll region) CSI #1; #2 r: set scrolling region (csr)
            self.stream.write('\x1b[1;{}r'.format(lines - 2))
            # (move-to prompt) CSI y;x H, cursor address (y,x) (cup)
            self.stream.write('\x1b[{};1H'.format(lines))
            self.mode_fullscreen = True

    def exit_fullscreen(self, lines=None):
        """ Exit fullscreen mode. """
        lines = lines or self.window_height
        if lines and self.does_styling:
            # CSI r: reset scrolling region (csr)
            self.stream.write('\x1b[r')
            self.stream.write('\x1b[{};1H'.format(lines))
            self.stream.write('\r\x1b[K\r\n')
        self.mode_fullscreen = False

    def cmdset_channels(self, *args):
        " List active channels and number of users. "
        _ = self.underline
        channels = channels_available()
        max_channel = map(len, channels)
        chan_width = max(len('channel') + 2, max(map(len, channels)) + 2)
        self.display_text("{0}  {1}".format(
            _('channel'.rjust(chan_width)),
            _('# users')))
        self.display_text('\r\n'.join(sorted([
            "{0:>{1}}  {2}".format(channel, chan_width, n_users)
            for channel, n_users in sorted(channels.items())
        ])))
        return 0

    def cmdset_users(self, *args):
        " List clients currently connected. "
        _ = self.underline
        all_client_servers = clients.values()
        max_user = map(len, [c.env['USER'] for c in all_client_servers])
        max_channel = map(len, channels_available())
        user_width = max(len('user') + 2, max(max_user) + 2)
        chan_width = max(len('channel') + 2, max(max_channel) + 2)
        self.display_text("{0}  {1}  {2}".format(
            _('user'.rjust(user_width)),
            _('channel'.rjust(chan_width)),
            _('origin')))
        self.display_text('\r\n'.join(sorted([
            '{env[USER]:>{user_width}}  '
            '{env[CHANNEL]:>{chan_width}}  '
            '{env[REMOTE_HOST]:>15}'.format(env=server.env,
                                            user_width=user_width,
                                            chan_width=chan_width)
            for server in all_client_servers
        ])))
        return 0

    def store_private_history(self, **kwargs):
        self.private_history.append(History(**kwargs))

    def broadcast(self, action, *data, target=None):
        """ Broadcast a message to all other telnet sessions, unless
            target is specified (as a nickname). Returns shell
            exitcode (0 success, 1 if failed).
        """
        mynick = self.server.env['USER']
        mychan = self.server.env['CHANNEL']

        # validate within a channel, and /nick has been set,
        if not (mychan or target) and action != 'part':
            self.display_text('You must first {} a channel !'.format(
                self.standout('/join')))
            return 1

        elif mynick == 'unknown':
            self.display_text('\r\nYou must first set a {} !'.format(
                self.standout('/nick')))
            return 1

        # validate that our nickname isn't already taken, in
        # which case we become blocked until we select a new /nick
        for rc in ([client for client in clients.values()
                    if client != self.server]):
            if rc.env['USER'] == mynick:
                self.display_text('Your nickname {} is already taken, '
                                  'select a new /nick !'.format(
                                      self.standout(mynick)))
                return 1

        store = (self.store_private_history if target is not None
                 else store_channel_history)
        store(time=time.localtime(), action=action, data=data,
              nick=mynick, target=mychan)

        sent = 0
        for remote_client in ([client for client in clients.values()]):
            if target is None:
                # forward data to everybody in matching channel name
                if remote_client.env['CHANNEL'] == mychan:
                    remote_client.receive(action, mynick, *data)
                    sent += 1
            else:
                tnick = remote_client.env['USER']
                if tnick.lower() == target.lower():
                    remote_client.receive(action, mynick, *data)
                    sent += 1
                elif tnick.lower() == mynick.lower():
                    # when sending to a target, reverse nickname
                    # when cc:ing ourselves
                    remote_client.receive(action, mynick, *data)

        self.log.info('{from_nick} {action} {target}: {data}'
                      .format(from_nick=mynick, action=action,
                              target=target or mychan, data=data))
        if target and sent is 0:
            # not received
            return 1
        return None

    def cmdset_whois(self, *args):
        if not len(args):
            self.display_text('No nickname specified.')
            return 1
        nick = args[0]
        for remote_client in ([client for client in clients.values()]):
            tnick = remote_client.env['USER']
            if tnick.lower() == nick.lower():
                self.display_text('{}'.format(remote_client))
                return 0
        self.display_text('{} not found.'.format(nick))
        return 1

    def cmdset_me(self, *args):
        " Broadcast 'action' message to current channel. "
        # transpose any unprintable characters from input, to prevent a
        # user from broadcasting cursor position sequences, for example.
        msg = u''.join([name_unicode(char) for char in ' '.join(args)])
        if msg:
            return self.broadcast('me', msg)

    def cmdset_msg(self, *args):
        " send message to another user. "
        # transpose any unprintable characters from input, to prevent a
        # user from broadcasting cursor position sequences, for example.
        if not len(args):
            self.display_text('No nickname specified.')
            return 1
        nick, *msg = args[0].split(None, 1)
        if not msg:
            self.display_text("{} didn't hear you.".format(nick))
            return 1
        retval = self.broadcast('privmsg', nick, msg[0], target=nick)
        if (retval not in (0, None)):
            self.display_text("{} wasn't listening.".format(nick))
            return retval
        return None

    def cmdset_say(self, *args):
        " Broadcast message to current channel. "
        # transpose any unprintable characters from input, to prevent a
        # user from broadcasting cursor position sequences, for example.
        msg = u''.join([name_unicode(char) for char in ' '.join(args)])
        if msg:
            return self.broadcast('say', msg)

    @property
    def cmdset_join_subcmds(self):
        ch_names = channels_available().keys()
        ch_names.append(self.server.main_channel)
        return collections.OrderedDict([
            (ch_name, None,) for ch_name in sorted(set(ch_names))
        ])

    def cmdset_join(self, *args):
        " Switch-to talker channel. "
        mynick = self.server.env['USER']
        self.log.info('{}'.format(args))
        chan, *msg = (args[0].split(None, 1) if args
                      else (self.server.main_channel,))
        msg = ' '.join(msg) or ''
        if not chan.startswith('#'):
            chan = '#{}'.format(chan)
        if len(chan) > self.MAX_CHAN:
            self.display_text('Channel name too long.')
            return 1

        # part previous channel, if any
        prev = self.server.env['CHANNEL']
        if prev and chan.lower() == prev.lower():
            self.display_text('You are already here.')
            return 1
        if prev:
            self.broadcast('part', prev, 'joining another channel')

        # join new channel
        self.assign('CHANNEL={}'.format(chan))
        self.display_history()
        self.log.info('{} has joined {}{}'.format(
            mynick, chan, msg and ': {}'.format(msg) or ''))
        val = self.broadcast('join', msg, chan)
        self._redraw = self.server._loop.call_later(0.100, self.redraw_received)
        return val

    def cmdset_part(self, *args):
        " Switch-off talker channel. "
        mynick = self.server.env['USER']
        chan, *msg = (args[0].split(None, 1) if args
                      else (self.server.env['CHANNEL'],))
        msg = ' '.join(msg) or ''
        if not chan:
            self.display_text('Channel not set.')
            return 1
        if not chan.startswith('#'):
            chan = '#{}'.format(chan)

        cur = self.server.env['CHANNEL']
        if cur and chan.lower() != cur.lower():
            self.display_text('You cannot leave where you have not been!')
            return 1
        if len(chan) > self.MAX_CHAN:
            self.display_text('Channel name too long.')
            return 1

        val = self.broadcast('part', msg, chan)
        self.assign('CHANNEL=')
        self.log.info('{} has left {}{}'.format(
            mynick, chan, msg and ': {}'.format(msg) or ''))
        return val

    def cmdset_nick(self, *args):
        " Display or change handle. "
        mynick = self.server.env['USER']
        if not args:
            self.display_text('Your handle is {}'.format(
                self.standout(mynick)))
            return 0
        newnick = args[0]
        if len(newnick) > self.MAX_NICK:
            self.display_text('Nickname too long.')
            return 1
        elif newnick == mynick:
            self.display_text('You are what you are.')
            return 1
        for client in clients.values():
            if client.env['USER'] == newnick:
                self.display_text('Nickname {} already taken.'.format(
                    self.standout(newnick)))
                return 1
        self.assign('USER={}'.format(newnick))
        self.log.info('{} renamed to {}'.format(mynick, newnick))
        self.display_text('Your name is now {}'.format(
            self.standout(newnick)))
        return self.broadcast('rename', mynick)

    def cmdset_set(self, *args):
        " Display operating parameters. "
        # overload to prevent setting values through /set
        if len(args):
            return 1
        return super().cmdset_set()

    def cmdset_quit(self, *args):
        " Disconnect from server. "
        if self.mode_fullscreen:
            self.exit_fullscreen()
        return self.server.logout()

    def cmdset_clear(self, *args):
        " Clear the screen. "
        self.redraw_received(history=False)

def random_busywait():
    # Just a silly function for the on-connect banner
    word_a = random.choice(('initializing', 'indexing', 'configuring',
                            'particulating', 'prioritizing',
                            'preparing', 'iterating', 'modeling',
                            'generating', 'gathering', 'computing',
                            'building', 'resolving', 'adjusting',
                            're-ordering', 'sorting', 'allocating',
                            'multiplexing', 'scheduling', 'routing',
                            'parsing', 'pairing', 'partitioning',
                            'refactoring', 'factoring', 'freeing',
                            'repositioning',
                            ))
    word_b = random.choice(('b-tree', 'directory', 'hash',
                            'random-order', 'compute', 'lookup',
                            'in-order', 'inverse', 'root',
                            'first-order', 'threaded',
                            'priority', 'bit', 'circular',
                            'bi-directional', 'multi-dimensional',
                            'decision', 'module', 'dynamic',
                            'associative', 'linked', 'acyclic',
                            'radix', 'binomial', 'binary', 'parallel',
                            'sparse', 'cartesian', 'redundant',
                            'duplicate', 'unique', ))
    word_c = random.choice(('structure', 'tree', 'datasets',
                            'stores', 'jobs', 'functions',
                            'callbacks', 'matrices', 'arrays',
                            'tables', 'queues', 'fields', 'stack',
                            'heap', 'segments', 'map', 'graph',
                            'namespaces', 'procedure', 'processes',
                            'lists', 'sectors', 'stackframe',))
    return u'{} {} {}'.format(word_a.capitalize(), word_b, word_c)

def channels_available():
    channels = {}
    for client in clients.values():
        ch_name = client.env['CHANNEL']
        channels[ch_name] = channels.get(ch_name, 0) + 1
    return channels

def store_channel_history(**kwargs):
    """ Record channel broadcast history.

    Allows for screen refresh and to see limited channel history
    upon joining.
    """
    record = History(**kwargs)
    channel = record.target

    global channel_history
    if not channel in channel_history:
        channel_history[channel] = collections.deque([], MAX_HISTORY)

    channel_history[channel].append(record)

def start_server(loop, log, host, port):
    # create_server receives a callable that returns a Protocol
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

# vim: set shiftwidth=4 tabstop=4 softtabstop=4 expandtab textwidth=79 :
