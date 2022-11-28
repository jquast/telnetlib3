"""
КОСМОС/300 Lunar server: a demonstration Telnet Server shell.

With this shell, multiple clients may instruct the lander to:

    - collect rock samples
    - launch sample return capsule
    - relay a message (talker).

All input and output is forced uppercase, but unicode is otherwise supported.

A simple character-at-a-time repl is provided, supporting backspace.
"""
# std imports
import collections
import contextlib
import logging
import asyncio


class Client(collections.namedtuple("Client", ["reader", "writer", "notify_queue"])):
    def __str__(self):
        return "#{1}".format(*self.writer.get_extra_info("peername"))


class Lander(object):
    """
    КОСМОС/300 Lunar module.
    """

    collecting = False
    capsule_amount = 0
    capsule_launched = False
    capsule_launching = False

    def __init__(self):
        self.log = logging.getLogger("lunar.lander")
        self.clients = []
        self._loop = asyncio.get_event_loop()

    def __str__(self):
        collector = "RUNNING" if self.collecting else "READY"
        capsule = (
            "LAUNCH IN-PROGRESS"
            if self.capsule_launching
            else "LAUNCHED"
            if self.capsule_launched
            else "{}/4".format(self.capsule_amount)
        )
        clients = ", ".join(map(str, self.clients))
        return "COLLECTOR {}\r\nCAPSULE {}\r\nUPLINKS: {}".format(
            collector, capsule, clients
        )

    @contextlib.contextmanager
    def register_link(self, reader, writer):
        client = Client(reader, writer, notify_queue=asyncio.Queue())
        self.clients.append(client)
        try:
            self.notify_event("LINK ESTABLISHED TO {}".format(client))
            yield client

        finally:
            self.clients.remove(client)
            self.notify_event("LOST CONNECTION TO {}".format(client))

    def notify_event(self, event_msg):
        self.log.info(event_msg)
        for client in self.clients:
            client.notify_queue.put_nowait(event_msg)

    def repl_readline(self, client):
        """
        Lander REPL, provides no process, local echo.
        """
        from telnetlib3 import WONT, ECHO, SGA

        client.writer.iac(WONT, ECHO)
        client.writer.iac(WONT, SGA)
        readline = asyncio.ensure_future(client.reader.readline())
        recv_msg = asyncio.ensure_future(client.notify_queue.get())
        client.writer.write("КОСМОС/300: READY\r\n")
        wait_for = set([readline, recv_msg])
        try:
            while True:
                client.writer.write("? ")

                # await (1) client input or (2) system notification
                done, pending = await asyncio.wait(
                    wait_for, return_when=asyncio.FIRST_COMPLETED
                )

                task = done.pop()
                wait_for.remove(task)
                if task == readline:
                    # (1) client input
                    cmd = task.result().rstrip().upper()

                    client.writer.echo(cmd)
                    self.process_command(client, cmd)

                    # await next,
                    readline = asyncio.ensure_future(client.reader.readline())
                    wait_for.add(readline)

                else:
                    # (2) system notification
                    msg = task.result()

                    # await next,
                    recv_msg = asyncio.ensure_future(client.notify_queue.get())
                    wait_for.add(recv_msg)

                    # show and display prompt,
                    client.writer.write("\r\x1b[K{}\r\n".format(msg))

        finally:
            for task in wait_for:
                task.cancel()

    async def repl_catime(self, client):
        """
        Lander REPL providing character-at-a-time processing.
        """
        read_one = asyncio.ensure_future(client.reader.read(1))
        recv_msg = asyncio.ensure_future(client.notify_queue.get())
        wait_for = set([read_one, recv_msg])

        client.writer.write("КОСМОС/300: READY\r\n")

        while True:
            cmd = ""

            # prompt
            client.writer.write("? ")
            while True:

                # await (1) client input (2) system notification
                done, pending = await asyncio.wait(
                    wait_for, return_when=asyncio.FIRST_COMPLETED
                )

                task = done.pop()
                wait_for.remove(task)
                if task == read_one:
                    # (1) client input
                    char = task.result().upper()

                    # await next,
                    read_one = asyncio.ensure_future(client.reader.read(1))
                    wait_for.add(read_one)

                    if char == "":
                        # disconnect, exit
                        return

                    elif char in "\r\n":
                        # carriage return, process command.
                        break

                    elif char in "\b\x7f":
                        # backspace
                        cmd = cmd[:-1]
                        client.writer.echo("\b")

                    else:
                        # echo input
                        cmd += char
                        client.writer.echo(char)

                else:
                    # (2) system notification
                    msg = task.result()

                    # await next,
                    recv_msg = asyncio.ensure_future(client.notify_queue.get())
                    wait_for.add(recv_msg)

                    # show and display prompt,
                    client.writer.write("\r\x1b[K{}\r\n".format(msg))
                    client.writer.write("? {}".format(cmd))

            # reached when user pressed return by inner 'break' statement.
            self.process_command(client, cmd)

    def process_command(self, client, cmd):
        result = "\r\n"
        if cmd == "HELP":
            result += (
                "COLLECT  COLLECT ROCK SAMPLE\r\n"
                " STATUS  DEVICE STATUS\r\n"
                " LAUNCH  LAUNCH RETURN CAPSULE\r\n"
                "  RELAY  MESSAGE TRANSMISSION RELAY"
            )
        elif cmd == "STATUS":
            result += str(self)
        elif cmd == "COLLECT":
            result += self.collect_sample(client)
        elif cmd == "LAUNCH":
            result += self.launch_capsule(client)
        elif cmd == "RELAY" or cmd.startswith("RELAY ") or cmd.startswith("R "):
            cmd, *args = cmd.split(None, 1)
            if args:
                self.notify_event("RELAY FROM {}: {}".format(client, args[0]))
            result = ""
        elif cmd:
            result += "NOT A COMMAND, {!r}".format(cmd)
        client.writer.write(result + "\r\n")

    def launch_capsule(self, client):
        if self.capsule_launched:
            return "ERROR: NO CAPSULE"
        elif self.capsule_launching:
            return "ERROR: LAUNCH SEQUENCE IN-PROGRESS"
        elif self.collecting:
            return "ERROR: COLLECTOR ACTIVE"
        self.capsule_launching = True
        self.notify_event("CAPSULE LAUNCH SEQUENCE INITIATED!")
        asyncio.get_event_loop().call_later(10, self.after_launch)
        for count in range(1, 10):
            asyncio.get_event_loop().call_later(
                count, self.notify_event, "{} ...".format(10 - count)
            )
        return "OK"

    def collect_sample(self, client):
        if self.collecting:
            return "ERROR: COLLECTION ALREADY IN PROGRESS"
        elif self.capsule_launched:
            return "ERROR: COLLECTOR CAPSULE NOT CONNECTED"
        elif self.capsule_launching:
            return "ERROR: LAUNCH SEQUENCE IN-PROGRESS."
        elif self.capsule_amount >= 4:
            return "ERROR: COLLECTOR CAPSULE FULL"
        self.collecting = True
        self.notify_event("SAMPLE COLLECTION HAS BEGUN")
        self._loop.call_later(7, self.collected_sample)
        return "OK"

    def collected_sample(self):
        self.notify_event("SAMPLE COLLECTED")
        self.capsule_amount += 1
        self.collecting = False

    def after_launch(self):
        self.capsule_launching = False
        self.capsule_launched = True
        self.notify_event("CAPSULE LAUNCHED SUCCESSFULLY")


# each client shares, even communicates through lunar 'lander' instance.
lander = Lander()


def shell(reader, writer):
    global lander
    with lander.register_link(reader, writer) as client:
        await lander.repl_readline(client)
