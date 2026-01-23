# jdq(2025): This file was modified from cpython 3.12 test_telnetlib.py, to make it compatible
# with more versions of python, and, to use pytest instead of unittest.
# std imports
import io
import re
import socket
import selectors
import threading
import contextlib

# 3rd party
import pytest

# Skip the whole module if a working socket is not available
try:
    _s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    _s.close()
except OSError:
    pytest.skip("Working socket required", allow_module_level=True)

# local
import telnetlib3.telnetlib as telnetlib  # noqa: E402

HOST = "127.0.0.1"


def server(evt, serv):
    serv.listen()
    evt.set()
    try:
        conn, addr = serv.accept()
        conn.close()
    except TimeoutError:
        pass
    finally:
        serv.close()


@pytest.fixture
def server_port():
    """
    Start a listening socket in a background thread that accepts one connection.

    Yields the bound port number.
    """
    evt = threading.Event()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(60)  # Safety net. Look issue 11812
    # Bind to an ephemeral port on localhost
    sock.bind((HOST, 0))
    port = sock.getsockname()[1]
    thread = threading.Thread(target=server, args=(evt, sock), daemon=True)
    thread.start()
    evt.wait()
    try:
        yield port
    finally:
        thread.join()


@contextlib.contextmanager
def captured_stdout():
    """Local replacement for test.support.captured_stdout()"""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        yield buf


class SocketStub(object):
    """A socket proxy that re-defines sendall()"""

    def __init__(self, reads=()):
        self.reads = list(reads)  # Intentionally make a copy.
        self.writes = []
        self.block = False

    def sendall(self, data):
        self.writes.append(data)

    def recv(self, size):
        out = b""
        while self.reads and len(out) < size:
            out += self.reads.pop(0)
        if len(out) > size:
            self.reads.insert(0, out[size:])
            out = out[:size]
        return out


class TelnetAlike(telnetlib.Telnet):
    def fileno(self):
        """Provide a real OS-level file descriptor so selectors and any code that calls fileno() can
        work, even though the network I/O is mocked."""
        s = getattr(self, "_fileno_sock", None)
        if s is None:
            try:
                s1, s2 = socket.socketpair()
                s1.setblocking(False)
                s2.setblocking(False)
                self._fileno_sock, self._fileno_peer = s1, s2
            except AttributeError:
                # Fallback if socketpair is unavailable; a plain socket still yields a valid FD
                self._fileno_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self._fileno_sock.setblocking(False)
                self._fileno_peer = None
        return self._fileno_sock.fileno()

    def close(self):
        # Close the internal fileno() provider sockets, but leave the mocked self.sock alone
        try:
            if getattr(self, "_fileno_sock", None) is not None:
                try:
                    self._fileno_sock.close()
                finally:
                    self._fileno_sock = None
            if getattr(self, "_fileno_peer", None) is not None:
                try:
                    self._fileno_peer.close()
                finally:
                    self._fileno_peer = None
        finally:
            # Do not close self.sock here; tests manage the stubbed socket lifecycle
            pass

    def sock_avail(self):
        return not self.sock.block

    def msg(self, msg, *args):
        with captured_stdout() as out:
            telnetlib.Telnet.msg(self, msg, *args)
        self._messages += out.getvalue()
        return


class MockSelector(selectors.BaseSelector):
    def __init__(self):
        self.keys = {}

    @property
    def resolution(self):
        return 1e-3

    def register(self, fileobj, events, data=None):
        key = selectors.SelectorKey(fileobj, 0, events, data)
        self.keys[fileobj] = key
        return key

    def unregister(self, fileobj):
        return self.keys.pop(fileobj)

    def select(self, timeout=None):
        block = False
        for fileobj in self.keys:
            if isinstance(fileobj, TelnetAlike):
                block = fileobj.sock.block
                break
        if block:
            return []
        else:
            return [(key, key.events) for key in self.keys.values()]

    def get_map(self):
        return self.keys


@contextlib.contextmanager
def mocktest_socket(reads):
    def new_conn(*ignored):
        return SocketStub(reads)

    try:
        old_conn = socket.create_connection
        socket.create_connection = new_conn
        yield None
    finally:
        socket.create_connection = old_conn
    return


def make_telnet(reads=(), cls=TelnetAlike):
    """Return a telnetlib.Telnet object that uses a SocketStub with reads queued up to be read."""
    for x in reads:
        assert type(x) is bytes, x
    with mocktest_socket(reads):
        telnet = cls("dummy", 0)
        telnet._messages = ""  # debuglevel output
    return telnet


class TestGeneral:
    def test_basic(self, server_port):
        # connects
        telnet = telnetlib.Telnet(HOST, server_port)
        telnet.sock.close()

    def test_context_manager(self, server_port):
        with telnetlib.Telnet(HOST, server_port) as tn:
            assert tn.get_socket() is not None
        assert tn.get_socket() is None

    def test_timeout_default(self, server_port):
        assert socket.getdefaulttimeout() is None
        socket.setdefaulttimeout(30)
        try:
            telnet = telnetlib.Telnet(HOST, server_port)
        finally:
            socket.setdefaulttimeout(None)
        assert telnet.sock.gettimeout() == 30
        telnet.sock.close()

    def test_timeout_none(self, server_port):
        # None, having other default
        assert socket.getdefaulttimeout() is None
        socket.setdefaulttimeout(30)
        try:
            telnet = telnetlib.Telnet(HOST, server_port, timeout=None)
        finally:
            socket.setdefaulttimeout(None)
        assert telnet.sock.gettimeout() is None
        telnet.sock.close()

    def test_timeout_value(self, server_port):
        telnet = telnetlib.Telnet(HOST, server_port, timeout=30)
        assert telnet.sock.gettimeout() == 30
        telnet.sock.close()

    def test_timeout_open(self, server_port):
        telnet = telnetlib.Telnet()
        telnet.open(HOST, server_port, timeout=30)
        assert telnet.sock.gettimeout() == 30
        telnet.sock.close()

    def test_getters(self, server_port):
        # Test telnet getter methods
        telnet = telnetlib.Telnet(HOST, server_port, timeout=30)
        t_sock = telnet.sock
        assert telnet.get_socket() == t_sock
        assert telnet.fileno() == t_sock.fileno()
        telnet.sock.close()


class ExpectAndReadBase:
    @pytest.fixture(autouse=True)
    def _mock_selector(self, monkeypatch):
        monkeypatch.setattr(telnetlib, "_TelnetSelector", MockSelector)


class TestRead(ExpectAndReadBase):
    def test_read_until(self):
        """read_until(expected, timeout=None) test the blocking version of read_util."""
        want = [b"xxxmatchyyy"]
        telnet = make_telnet(want)
        data = telnet.read_until(b"match")
        assert data == b"xxxmatch", (telnet.cookedq, telnet.rawq, telnet.sock.reads)

        reads = [b"x" * 50, b"match", b"y" * 50]
        expect = b"".join(reads[:-1])
        telnet = make_telnet(reads)
        data = telnet.read_until(b"match")
        assert data == expect

    def test_read_all(self):
        """read_all() Read all data until EOF; may block."""
        reads = [b"x" * 500, b"y" * 500, b"z" * 500]
        expect = b"".join(reads)
        telnet = make_telnet(reads)
        data = telnet.read_all()
        assert data == expect

    def test_read_some(self):
        """read_some() Read at least one byte or EOF; may block."""
        # test 'at least one byte'
        telnet = make_telnet([b"x" * 500])
        data = telnet.read_some()
        assert len(data) >= 1
        # test EOF
        telnet = make_telnet()
        data = telnet.read_some()
        assert b"" == data

    def _read_eager(self, func_name):
        """read_*_eager() Read all data available already queued or on the socket, without
        blocking."""
        want = b"x" * 100
        telnet = make_telnet([want])
        func = getattr(telnet, func_name)
        telnet.sock.block = True
        assert b"" == func()
        telnet.sock.block = False
        data = b""
        while True:
            try:
                data += func()
            except EOFError:
                break
        assert data == want

    def test_read_eager(self):
        # read_eager and read_very_eager make the same guarantees
        # (they behave differently but we only test the guarantees)
        self._read_eager("read_eager")
        self._read_eager("read_very_eager")
        # NB -- we need to test the IAC block which is mentioned in the
        # docstring but not in the module docs

    def read_very_lazy(self):
        want = b"x" * 100
        telnet = make_telnet([want])
        assert b"" == telnet.read_very_lazy()
        while telnet.sock.reads:
            telnet.fill_rawq()
        data = telnet.read_very_lazy()
        assert want == data
        with pytest.raises(EOFError):
            telnet.read_very_lazy()

    def test_read_lazy(self):
        want = b"x" * 100
        telnet = make_telnet([want])
        assert b"" == telnet.read_lazy()
        data = b""
        while True:
            try:
                read_data = telnet.read_lazy()
                data += read_data
                if not read_data:
                    telnet.fill_rawq()
            except EOFError:
                break
            assert want.startswith(data)
        assert data == want


class nego_collector(object):
    def __init__(self, sb_getter=None):
        self.seen = b""
        self.sb_getter = sb_getter
        self.sb_seen = b""

    def do_nego(self, sock, cmd, opt):
        self.seen += cmd + opt
        if cmd == tl.SE and self.sb_getter:
            sb_data = self.sb_getter()
            self.sb_seen += sb_data


tl = telnetlib


class TestWrite:
    """The only thing that write does is replace each tl.IAC for tl.IAC+tl.IAC."""

    def test_write(self):
        data_sample = [
            b"data sample without IAC",
            b"data sample with" + tl.IAC + b" one IAC",
            b"a few" + tl.IAC + tl.IAC + b" iacs" + tl.IAC,
            tl.IAC,
            b"",
        ]
        for data in data_sample:
            telnet = make_telnet()
            telnet.write(data)
            written = b"".join(telnet.sock.writes)
            assert data.replace(tl.IAC, tl.IAC + tl.IAC) == written


class TestOption:
    # RFC 854 commands
    cmds = [tl.AO, tl.AYT, tl.BRK, tl.EC, tl.EL, tl.GA, tl.IP, tl.NOP]

    def _test_command(self, data):
        """Helper for testing IAC + cmd."""
        telnet = make_telnet(data)
        data_len = len(b"".join(data))
        nego = nego_collector()
        telnet.set_option_negotiation_callback(nego.do_nego)
        txt = telnet.read_all()
        cmd = nego.seen
        assert len(cmd) > 0  # we expect at least one command
        assert cmd[:1] in self.cmds
        assert cmd[1:2] == tl.NOOPT
        assert data_len == len(txt + cmd)
        nego.sb_getter = None  # break the nego => telnet cycle

    def test_IAC_commands(self):
        for cmd in self.cmds:
            self._test_command([tl.IAC, cmd])
            self._test_command([b"x" * 100, tl.IAC, cmd, b"y" * 100])
            self._test_command([b"x" * 10, tl.IAC, cmd, b"y" * 10])
        # all at once
        self._test_command([tl.IAC + cmd for (cmd) in self.cmds])

    def test_SB_commands(self):
        # RFC 855, subnegotiations portion
        send = [
            tl.IAC + tl.SB + tl.IAC + tl.SE,
            tl.IAC + tl.SB + tl.IAC + tl.IAC + tl.IAC + tl.SE,
            tl.IAC + tl.SB + tl.IAC + tl.IAC + b"aa" + tl.IAC + tl.SE,
            tl.IAC + tl.SB + b"bb" + tl.IAC + tl.IAC + tl.IAC + tl.SE,
            tl.IAC + tl.SB + b"cc" + tl.IAC + tl.IAC + b"dd" + tl.IAC + tl.SE,
        ]
        telnet = make_telnet(send)
        nego = nego_collector(telnet.read_sb_data)
        telnet.set_option_negotiation_callback(nego.do_nego)
        txt = telnet.read_all()
        assert txt == b""
        want_sb_data = tl.IAC + tl.IAC + b"aabb" + tl.IAC + b"cc" + tl.IAC + b"dd"
        assert nego.sb_seen == want_sb_data
        assert b"" == telnet.read_sb_data()
        nego.sb_getter = None  # break the nego => telnet cycle

    def test_debuglevel_reads(self):
        # test all the various places that self.msg(...) is called
        given_a_expect_b = [
            # Telnet.fill_rawq
            (b"a", ": recv b''\n"),
            # Telnet.process_rawq
            (tl.IAC + bytes([88]), ": IAC 88 not recognized\n"),
            (tl.IAC + tl.DO + bytes([1]), ": IAC DO 1\n"),
            (tl.IAC + tl.DONT + bytes([1]), ": IAC DONT 1\n"),
            (tl.IAC + tl.WILL + bytes([1]), ": IAC WILL 1\n"),
            (tl.IAC + tl.WONT + bytes([1]), ": IAC WONT 1\n"),
        ]
        for a, b in given_a_expect_b:
            telnet = make_telnet([a])
            telnet.set_debuglevel(1)
            _ = telnet.read_all()
            assert b in telnet._messages

    def test_debuglevel_write(self):
        telnet = make_telnet()
        telnet.set_debuglevel(1)
        telnet.write(b"xxx")
        expected = "send b'xxx'\n"
        assert expected in telnet._messages

    def test_debug_accepts_str_port(self):
        # Issue 10695
        with mocktest_socket([]):
            telnet = TelnetAlike("dummy", "0")
            telnet._messages = ""
        telnet.set_debuglevel(1)
        telnet.msg("test")
        assert re.search(r"0.*test", telnet._messages)


class TestExpect(ExpectAndReadBase):
    def test_expect(self):
        """Expect(expected, [timeout]) Read until the expected string has been seen, or a timeout is
        hit (default is no timeout); may block."""
        want = [b"x" * 10, b"match", b"y" * 10]
        telnet = make_telnet(want)
        _, _, data = telnet.expect([b"match"])
        assert data == b"".join(want[:-1])
