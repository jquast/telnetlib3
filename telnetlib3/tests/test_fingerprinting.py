# 3rd party
import pytest

# local
from telnetlib3 import fingerprinting as fps
from telnetlib3.tests.accessories import (  # noqa: F401
    bind_host,
    unused_tcp_port,
    create_server,
    open_connection,
)


class MockOption(dict):
    def __init__(self, values=None):
        super().__init__(values or {})
        self._values = self

    def enabled(self, opt):
        return self.get(opt) is True


class MockProtocol:
    def __init__(self, extra=None):
        self._extra = extra or {}
        self.duration = 1.5
        self.idle = 0.1


class MockWriter:
    def __init__(self, extra=None, will_options=None, wont_options=None):
        self.written = []
        self._closing = False
        self._extra = extra or {"peername": ("127.0.0.1", 12345)}
        self._will_options = set(will_options or [])
        self._wont_options = set(wont_options or [])
        self._iac_calls = []
        self.remote_option = MockOption()
        self.local_option = MockOption()
        self.pending_option = MockOption()
        self.protocol = MockProtocol()
        self._protocol = MockProtocol(self._extra)

    def write(self, data):
        self.written.append(data)

    async def drain(self):
        pass

    def get_extra_info(self, key, default=None):
        return self._extra.get(key, default)

    def is_closing(self):
        return self._closing

    def close(self):
        self._closing = True

    def iac(self, cmd, opt):
        self._iac_calls.append((cmd, opt))
        if opt in self._will_options:
            self.remote_option._values[opt] = True
        elif opt in self._wont_options:
            self.remote_option._values[opt] = False


class MockReader:
    def __init__(self, data):
        self._data = list(data)
        self._idx = 0

    async def read(self, n):
        if self._idx >= len(self._data):
            return ""
        result = self._data[self._idx]
        self._idx += 1
        return result


@pytest.mark.parametrize("extra,expected_keys", [
    ({"TERM": "xterm", "cols": 80, "rows": 24}, ["TERM", "cols", "rows"]),
    ({"charset": "UTF-8", "LANG": "en_US.UTF-8"}, ["charset", "LANG"]),
    ({"peername": ("10.0.0.1", 54321), "tspeed": "38400,38400"}, ["peername", "tspeed"]),
    ({}, []),
])
def test_get_client_fingerprint(extra, expected_keys):
    writer = MockWriter(extra)
    result = fps.get_client_fingerprint(writer)
    for key in expected_keys:
        assert result[key] == extra[key]


def test_describe_client():
    extra = {
        "peername": ("192.168.1.100", 54321),
        "TERM": "xterm-256color",
        "cols": 120,
        "rows": 40,
        "charset": "UTF-8",
        "USER": "testuser",
        "xdisploc": "localhost:0.0",
    }
    result = fps.describe_client(MockWriter(extra))
    assert "Client: 192.168.1.100:54321" in result
    assert "TERM: xterm-256color" in result
    assert "Size: 120x40" in result
    assert "CHARSET: UTF-8" in result
    assert "USER: testuser" in result
    assert "DISPLAY: localhost:0.0" in result
    # Output is sorted
    lines = result.split("\r\n")
    assert lines == sorted(lines)


def test_describe_client_empty():
    class EmptyWriter:
        def get_extra_info(self, key, default=None):
            return default

    result = fps.describe_client(EmptyWriter())
    assert "Client: unknown" in result
    assert "(no negotiated attributes)" in result


def test_describe_client_ttype_cycle():
    # Shows other unique TTYPE values, not the selected TERM
    extra = {"peername": ("127.0.0.1", 12345), "ttype1": "XTERM", "ttype2": "XTERM-256COLOR",
             "TERM": "XTERM-256COLOR"}
    result = fps.describe_client(MockWriter(extra))
    assert "TTYPE: XTERM" in result
    assert "XTERM-256COLOR" not in result.split("TTYPE:")[1]

    # No TTYPE line when all values match TERM
    extra2 = {"peername": ("127.0.0.1", 12345), "ttype1": "xterm", "TERM": "xterm"}
    assert "TTYPE:" not in fps.describe_client(MockWriter(extra2))


def test_format_probe_results():
    results = {
        "BINARY": {"status": "WILL", "opt": fps.BINARY, "description": ""},
        "SGA": {"status": "WONT", "opt": fps.SGA, "description": ""},
        "ECHO": {"status": "timeout", "opt": fps.ECHO, "description": ""},
    }
    output = fps.format_probe_results(results, probe_time=0.5)
    assert "Telnet protocols: BINARY" in output
    assert "1 supported" in output
    assert "1 refused" in output
    assert "1 no-response" in output
    assert "0.50s" in output


@pytest.mark.asyncio
async def test_probe_client_capabilities():
    options = [(fps.BINARY, "BINARY", ""), (fps.SGA, "SGA", "")]
    writer = MockWriter(will_options=[fps.BINARY], wont_options=[fps.SGA])
    results = await fps.probe_client_capabilities(writer, options=options, timeout=0.001)
    assert results["BINARY"]["status"] == "WILL"
    assert results["SGA"]["status"] == "WONT"

    # Already negotiated options are detected
    writer2 = MockWriter()
    writer2.remote_option._values[fps.BINARY] = True
    results2 = await fps.probe_client_capabilities(
        writer2, options=[(fps.BINARY, "BINARY", "")], timeout=0.001
    )
    assert results2["BINARY"]["already_negotiated"] is True


def test_all_probe_options_defined():
    assert len(fps.ALL_PROBE_OPTIONS) >= 35


def test_save_fingerprint_data(tmp_path, monkeypatch):
    import json
    import shutil
    monkeypatch.setattr(fps, "DATA_DIR", tmp_path)

    writer = MockWriter(extra={"peername": ("127.0.0.1", 12345), "TERM": "xterm"})
    writer._protocol = MockProtocol({"TERM": "xterm", "ttype1": "xterm", "ttype2": "xterm-256"})
    probe_results = {
        "BINARY": {"status": "WILL", "opt": fps.BINARY},
        "SGA": {"status": "WONT", "opt": fps.SGA},
    }
    filepath = fps._save_fingerprint_data(writer, probe_results, 0.5)

    assert filepath is not None and filepath.exists()
    with open(filepath) as f:
        data = json.load(f)

    assert data["protocol-fingerprint"] == filepath.parent.name
    assert data["protocol-fingerprint-data"]["probed-protocol"] == "client"
    assert "BINARY" in data["protocol-fingerprint-data"]["supported-options"]
    assert data["session-fingerprint"]["ttype_cycle"] == ["xterm", "xterm-256"]
    assert "peername" not in data["session-fingerprint"]["extra"]
    shutil.rmtree(filepath.parent)


def test_save_fingerprint_data_skipped_when_no_data_dir(monkeypatch):
    monkeypatch.setattr(fps, "DATA_DIR", None)
    writer = MockWriter(extra={"TERM": "xterm"})
    assert fps._save_fingerprint_data(writer, {}, 0.5) is None


@pytest.mark.asyncio
async def test_server_shell(monkeypatch, tmp_path):
    import shutil

    async def fast_sleep(_):
        pass
    monkeypatch.setattr(fps.asyncio, "sleep", fast_sleep)
    monkeypatch.setattr(fps, "DATA_DIR", tmp_path)
    monkeypatch.setattr(fps, "FINGERPRINT_POST_SCRIPT", "")

    reader = MockReader([])
    writer = MockWriter(extra={"peername": ("127.0.0.1", 12345), "TERM": "xterm"},
                        will_options=[fps.BINARY])
    await fps.fingerprinting_server_shell(reader, writer)
    written = "".join(writer.written)
    assert "'extra':" in written  # pprint dict output
    assert writer._closing

    for d in tmp_path.iterdir():
        if d.is_dir():
            shutil.rmtree(d)


@pytest.mark.asyncio
async def test_server_shell_display_disabled(monkeypatch):
    async def fast_sleep(_):
        pass
    monkeypatch.setattr(fps.asyncio, "sleep", fast_sleep)
    monkeypatch.setattr(fps, "DATA_DIR", None)
    monkeypatch.setattr(fps, "DISPLAY_OUTPUT", False)

    reader = MockReader([])
    writer = MockWriter(extra={"peername": ("127.0.0.1", 12345)})
    await fps.fingerprinting_server_shell(reader, writer)

    assert writer._closing
    written = "".join(writer.written)
    assert "'extra':" not in written


@pytest.mark.asyncio
async def test_fingerprint_probe_integration(bind_host, unused_tcp_port):
    import asyncio

    async with create_server(host=bind_host, port=unused_tcp_port, shell=fps.fingerprinting_server_shell,
                             connect_maxwait=0.5):
        async with open_connection(host=bind_host, port=unused_tcp_port, connect_minwait=0.2,
                                   connect_maxwait=0.5) as (reader, writer):
            output = ""
            while True:
                try:
                    chunk = await asyncio.wait_for(reader.read(1), timeout=1.0)
                    if not chunk:
                        break
                    output += chunk
                except asyncio.TimeoutError:
                    break

            assert "'extra':" in output  # pprint dict output
            assert "'supported':" in output


@pytest.mark.asyncio
async def test_fingerprint_probe_results_match_client(bind_host, unused_tcp_port):
    import asyncio

    server_results = None

    async def probe_and_capture(reader, writer):
        nonlocal server_results
        server_results, _ = await fps._run_probe(writer, verbose=False)
        writer.write("done\r\n")
        await writer.drain()
        writer.close()

    async with create_server(host=bind_host, port=unused_tcp_port, shell=probe_and_capture,
                             connect_maxwait=0.5):
        async with open_connection(host=bind_host, port=unused_tcp_port, connect_minwait=0.2,
                                   connect_maxwait=0.8) as (reader, writer):
            await asyncio.wait_for(reader.read(100), timeout=2.0)

    supported = [n for n, i in server_results.items() if i["status"] == "WILL"]
    assert all(opt in supported for opt in ["TTYPE", "NAWS", "NEW_ENVIRON", "CHARSET", "BINARY"])


def test_categorize_term():
    # Protocol-matched terminals
    assert fps._categorize_term("syncterm") == "Syncterm"
    assert fps._categorize_term("SYNCTERM") == "Syncterm"
    # ANSI terminals
    assert fps._categorize_term("ansi") == "Yes-ansi"
    assert fps._categorize_term("xterm-ansi") == "Yes-ansi"
    # Generic terminals
    assert fps._categorize_term("xterm") == "Yes"
    assert fps._categorize_term("vt100") == "Yes"
    # None/empty
    assert fps._categorize_term(None) == "None"
    assert fps._categorize_term("") == "None"


def test_categorize_terminal_size():
    assert fps._categorize_terminal_size(80, 25) == "Yes-80x25"
    assert fps._categorize_terminal_size(80, 24) == "Yes-80x24"
    assert fps._categorize_terminal_size(120, 40) == "Yes-Other"
    assert fps._categorize_terminal_size(None, 24) == "None"
    assert fps._categorize_terminal_size(80, None) == "None"


def test_protocol_fingerprint_env_vars():
    probe = {"BINARY": {"status": "WILL", "opt": fps.BINARY}}

    # HOME not negotiated - not in fingerprint
    writer1 = MockWriter(extra={"TERM": "xterm"})
    assert "HOME" not in fps._create_protocol_fingerprint(writer1, probe)

    # HOME negotiated with value
    writer2 = MockWriter(extra={"TERM": "xterm", "HOME": "/home/user"})
    writer2._protocol = MockProtocol({"HOME": "/home/user"})
    assert fps._create_protocol_fingerprint(writer2, probe)["HOME"] == "True"

    # HOME negotiated as empty string
    writer3 = MockWriter(extra={"TERM": "xterm", "HOME": ""})
    writer3._protocol = MockProtocol({"HOME": ""})
    assert fps._create_protocol_fingerprint(writer3, probe)["HOME"] == "None"


def test_protocol_fingerprint_encoding():
    probe = {"BINARY": {"status": "WILL", "opt": fps.BINARY}}

    writer1 = MockWriter(extra={"LANG": "en_US.UTF-8"})
    assert fps._create_protocol_fingerprint(writer1, probe)["encoding"] == "UTF-8"

    writer2 = MockWriter(extra={"LANG": "en_US"})  # no encoding suffix
    assert fps._create_protocol_fingerprint(writer2, probe)["encoding"] == "None"

    writer3 = MockWriter(extra={})
    assert fps._create_protocol_fingerprint(writer3, probe)["encoding"] == "None"


def test_protocol_fingerprint_options_sorted():
    writer = MockWriter()
    probe = {
        "TTYPE": {"status": "WILL", "opt": fps.TTYPE},
        "BINARY": {"status": "WILL", "opt": fps.BINARY},
        "SGA": {"status": "WONT", "opt": fps.SGA},
    }
    fp = fps._create_protocol_fingerprint(writer, probe)
    assert fp["supported-options"] == ["BINARY", "TTYPE"]
    assert fp["refused-options"] == ["SGA"]


def test_protocol_hash_consistency():
    probe = {"BINARY": {"status": "WILL", "opt": fps.BINARY}}

    # Different HOME/USER values produce same hash (both anonymized to "True")
    writer1 = MockWriter(extra={"TERM": "xterm", "HOME": "/home/alice"})
    writer1._protocol = MockProtocol({"HOME": "/home/alice"})
    writer2 = MockWriter(extra={"TERM": "xterm", "HOME": "/home/bob"})
    writer2._protocol = MockProtocol({"HOME": "/home/bob"})
    hash1 = fps._hash_protocol_fingerprint(fps._create_protocol_fingerprint(writer1, probe))
    hash2 = fps._hash_protocol_fingerprint(fps._create_protocol_fingerprint(writer2, probe))
    assert hash1 == hash2
    assert len(hash1) == 16

    # Different options produce different hash
    probe2 = {"BINARY": {"status": "WILL", "opt": fps.BINARY},
              "SGA": {"status": "WILL", "opt": fps.SGA}}
    hash3 = fps._hash_protocol_fingerprint(fps._create_protocol_fingerprint(writer1, probe2))
    assert hash1 != hash3


def test_protocol_folder_limit(tmp_path, monkeypatch):
    import shutil
    monkeypatch.setattr(fps, "DATA_DIR", tmp_path)
    monkeypatch.setattr(fps, "FINGERPRINT_MAX_FILES", 2)

    writer = MockWriter(extra={"TERM": "xterm"})
    probe = {"BINARY": {"status": "WILL", "opt": fps.BINARY}}

    path1 = fps._save_fingerprint_data(writer, probe, 0.5)
    path2 = fps._save_fingerprint_data(writer, probe, 0.5)
    path3 = fps._save_fingerprint_data(writer, probe, 0.5)

    assert path1 is not None and path2 is not None
    assert path3 is None  # at limit
    assert fps._count_protocol_folder_files(path1.parent) == 2
    shutil.rmtree(path1.parent)


def test_count_protocol_folder_files(tmp_path):
    assert fps._count_protocol_folder_files(tmp_path / "nonexistent") == 0
    (tmp_path / "a.json").touch()
    (tmp_path / "b.json").touch()
    (tmp_path / "c.txt").touch()  # not .json
    assert fps._count_protocol_folder_files(tmp_path) == 2


@pytest.mark.asyncio
async def test_post_fingerprint_script(tmp_path, monkeypatch, capsys):
    # Empty script does nothing
    monkeypatch.setattr(fps, "FINGERPRINT_POST_SCRIPT", "")
    await fps._execute_post_fingerprint_script(tmp_path / "test.json")

    # module:function format executes
    monkeypatch.setattr(fps, "FINGERPRINT_POST_SCRIPT",
                        "telnetlib3.fingerprinting:fingerprinting_post_script")
    test_file = tmp_path / "test.json"
    test_file.write_text('{"test": "data"}')
    await fps._execute_post_fingerprint_script(test_file)

    # fingerprinting_post_script pretty-prints JSON
    fps.fingerprinting_post_script(test_file)
    assert "test" in capsys.readouterr().out
