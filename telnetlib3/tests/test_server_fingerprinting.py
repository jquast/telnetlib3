# std imports
import json
import asyncio

# 3rd party
import pytest

# local
from telnetlib3 import fingerprinting as fps
from telnetlib3 import server_fingerprinting as sfp
from telnetlib3.telopt import VAR, USERVAR


@pytest.fixture(autouse=True)
def _fast_fingerprint(monkeypatch):
    """Zero out all fingerprint session delays for fast tests."""
    monkeypatch.setattr(sfp, "_NEGOTIATION_SETTLE", 0.0)
    monkeypatch.setattr(sfp, "_BANNER_WAIT", 0.01)
    monkeypatch.setattr(sfp, "_POST_RETURN_WAIT", 0.01)
    monkeypatch.setattr(sfp, "_PROBE_TIMEOUT", 0.01)


class MockOption(dict):
    def __init__(self, values=None):
        super().__init__(values or {})

    def enabled(self, opt):
        return self.get(opt) is True


class MockWriter:
    def __init__(self, extra=None, will_options=None, wont_options=None):
        self._extra = extra or {"peername": ("127.0.0.1", 12345)}
        self._will_options = set(will_options or [])
        self._wont_options = set(wont_options or [])
        self._iac_calls = []
        self._writes: list[bytes] = []
        self.remote_option = MockOption()
        self.local_option = MockOption()
        self.environ_encoding = "ascii"
        self.environ_send_raw = None
        self.mssp_data = None
        self.zmp_data: list[list[str]] = []
        self.atcp_data: list[tuple[str, str]] = []
        self.aardwolf_data: list[dict[str, object]] = []
        self.mxp_data: list[bytes] = []
        self.comport_data: dict[str, object] | None = None
        self._closing = False

    def get_extra_info(self, key, default=None):
        return self._extra.get(key, default)

    def iac(self, cmd, opt):
        self._iac_calls.append((cmd, opt))
        if opt in self._will_options:
            self.remote_option[opt] = True
        elif opt in self._wont_options:
            self.remote_option[opt] = False

    def write(self, data):
        self._writes.append(data)

    async def drain(self):
        pass

    def is_closing(self):
        return self._closing

    def close(self):
        self._closing = True


class MockReader:
    def __init__(self, chunks=None):
        self._chunks = list(chunks or [])
        self._idx = 0

    async def read(self, n):
        if self._idx >= len(self._chunks):
            await asyncio.sleep(10)
            return b""
        chunk = self._chunks[self._idx]
        self._idx += 1
        return chunk[:n]


class InteractiveMockReader:
    """MockReader that gates chunks behind writer responses.

    The first chunk is available immediately.  Each subsequent chunk is
    released only after the writer has accumulated one more write than
    before, simulating a server that waits for client input before
    sending the next prompt.
    """

    def __init__(self, chunks, writer):
        self._chunks = list(chunks)
        self._writer = writer
        self._idx = 0

    async def read(self, n):
        if self._idx >= len(self._chunks):
            await asyncio.sleep(10)
            return b""
        needed_writes = self._idx
        while len(self._writer._writes) < needed_writes:
            await asyncio.sleep(0.001)
        chunk = self._chunks[self._idx]
        self._idx += 1
        return chunk[:n]


_BINARY_PROBE = {"BINARY": {"status": "WILL", "opt": fps.BINARY}}


def _save(writer=None, save_path=None, **overrides):
    session_data = {
        "option_states": overrides.pop("option_states", {}),
        "banner_before_return": sfp._format_banner(overrides.pop("banner_before", b"")),
        "banner_after_return": sfp._format_banner(overrides.pop("banner_after", b"")),
        "timing": {
            "probe": overrides.pop("probe_time", 0.1),
            "total": overrides.pop("total_time", 1.0),
        },
    }
    session_entry = {
        "host": overrides.pop("host", "example.com"),
        "port": overrides.pop("port", 23),
        "ip": overrides.pop("ip", "10.0.0.1"),
        "connected": "2026-01-01T00:00:00+00:00",
    }
    defaults = {
        "writer": writer or MockWriter(extra={"peername": ("10.0.0.1", 23)}),
        "probe_results": overrides.pop("probe_results", _BINARY_PROBE),
        "session_data": session_data,
        "session_entry": session_entry,
    }
    defaults.update(overrides)
    if save_path is not None:
        defaults["save_path"] = save_path
    return sfp._save_server_fingerprint_data(**defaults)


@pytest.mark.asyncio
async def test_probe_server_capabilities():
    options = [(fps.BINARY, "BINARY", ""), (fps.SGA, "SGA", "")]
    writer = MockWriter(will_options=[fps.BINARY], wont_options=[fps.SGA])
    results = await sfp.probe_server_capabilities(writer, options=options, timeout=0.01)
    assert results["BINARY"]["status"] == "WILL"
    assert results["SGA"]["status"] == "WONT"


@pytest.mark.parametrize(
    "opt,value,name,expected_status",
    [
        pytest.param(fps.SGA, False, "SGA", "WONT", id="already_wont"),
        pytest.param(fps.BINARY, True, "BINARY", "WILL", id="already_will"),
    ],
)
@pytest.mark.asyncio
async def test_probe_already_negotiated(opt, value, name, expected_status):
    writer = MockWriter()
    writer.remote_option[opt] = value
    results = await sfp.probe_server_capabilities(
        writer, options=[(opt, name, "test")], timeout=0.01
    )
    assert results[name]["status"] == expected_status
    assert results[name]["already_negotiated"] is True


@pytest.mark.asyncio
async def test_probe_timeout_and_defaults():
    writer = MockWriter()
    results = await sfp.probe_server_capabilities(
        writer, options=[(fps.BINARY, "BINARY", "")], timeout=0.01
    )
    assert results["BINARY"]["status"] == "timeout"

    writer2 = MockWriter(wont_options=[fps.BINARY])
    results2 = await sfp.probe_server_capabilities(writer2, timeout=0.01)
    assert "BINARY" in results2
    base = fps.QUICK_PROBE_OPTIONS + fps.EXTENDED_OPTIONS
    expected = len(base) - len([o for o in base if o[0] in sfp._CLIENT_ONLY_WILL])
    assert len(results2) == expected


def test_collect_server_option_states():
    writer = MockWriter()
    states = sfp._collect_server_option_states(writer)
    assert not states["server_offered"]
    assert not states["server_requested"]

    writer.remote_option[fps.SGA] = True
    writer.remote_option[fps.ECHO] = True
    writer.local_option[fps.NAWS] = True
    states = sfp._collect_server_option_states(writer)
    assert "SGA" in states["server_offered"]
    assert "ECHO" in states["server_offered"]
    assert "NAWS" in states["server_requested"]


def test_create_server_protocol_fingerprint():
    writer = MockWriter()
    fp = sfp._create_server_protocol_fingerprint(writer, {})
    assert fp["probed-protocol"] == "server"
    assert fp["offered-options"] == []
    assert fp["refused-options"] == []
    assert fp["requested-options"] == []

    writer.local_option[fps.NAWS] = True
    probe = {
        "BINARY": {"status": "WILL", "opt": fps.BINARY},
        "SGA": {"status": "WONT", "opt": fps.SGA},
        "ECHO": {"status": "timeout", "opt": fps.ECHO},
        "TTYPE": {"status": "WILL", "opt": fps.TTYPE},
    }
    fp = sfp._create_server_protocol_fingerprint(writer, probe)
    assert fp["offered-options"] == ["BINARY", "TTYPE"]
    assert fp["refused-options"] == ["ECHO", "SGA"]
    assert fp["requested-options"] == ["NAWS"]


def test_server_fingerprint_hash_consistency():
    probe = {
        "BINARY": {"status": "WILL", "opt": fps.BINARY},
        "SGA": {"status": "WONT", "opt": fps.SGA},
    }
    fp1 = sfp._create_server_protocol_fingerprint(
        MockWriter(extra={"peername": ("10.0.0.1", 23)}), probe
    )
    fp2 = sfp._create_server_protocol_fingerprint(
        MockWriter(extra={"peername": ("10.0.0.2", 2323)}), probe
    )
    h1 = fps._hash_fingerprint(fp1)
    h2 = fps._hash_fingerprint(fp2)
    assert h1 == h2 and len(h1) == 16


def test_format_banner():
    assert sfp._format_banner(b"Hello\r\nWorld") == "Hello\r\nWorld"
    assert not sfp._format_banner(b"")


def test_format_banner_surrogateescape():
    """High bytes are preserved as surrogates, not replaced with U+FFFD."""
    result = sfp._format_banner(b"\xff\xfe\xb1")
    assert "\ufffd" not in result
    assert result == "\udcff\udcfe\udcb1"
    raw = result.encode("ascii", errors="surrogateescape")
    assert raw == b"\xff\xfe\xb1"


def test_format_banner_json_roundtrip():
    """Surrogates survive JSON serialization and can recover raw bytes."""
    banner = sfp._format_banner(b"Hello\xb1\xb2World")
    encoded = json.dumps(banner)
    decoded = json.loads(encoded)
    assert decoded == banner
    raw = decoded.encode("ascii", errors="surrogateescape")
    assert raw == b"Hello\xb1\xb2World"


@pytest.mark.asyncio
async def test_read_banner():
    reader = MockReader([b"Welcome to BBS\r\n"])
    assert await sfp._read_banner(reader, timeout=0.1) == b"Welcome to BBS\r\n"

    assert await sfp._read_banner(MockReader([]), timeout=0.01) == b""


@pytest.mark.asyncio
async def test_read_banner_max_bytes():
    big = b"A" * 200
    reader = MockReader([big])
    result = await sfp._read_banner(reader, timeout=0.1, max_bytes=50)
    assert result == b"A" * 50


@pytest.mark.asyncio
async def test_read_banner_until_quiet_max_bytes():
    big = b"B" * 200
    reader = MockReader([big])
    result = await sfp._read_banner_until_quiet(
        reader, quiet_time=0.01, max_wait=0.05, max_bytes=80
    )
    assert result == b"B" * 80


@pytest.mark.asyncio
async def test_read_banner_until_quiet_collects_multiple_chunks():
    reader = MockReader([b"chunk1", b"chunk2", b"chunk3"])
    result = await sfp._read_banner_until_quiet(reader, quiet_time=0.01, max_wait=1.0)
    assert result == b"chunk1chunk2chunk3"


def test_save_server_fingerprint_data(tmp_path, monkeypatch):
    monkeypatch.setattr(fps, "DATA_DIR", str(tmp_path))
    filepath = _save(
        probe_results={
            "BINARY": {"status": "WILL", "opt": fps.BINARY},
            "SGA": {"status": "WONT", "opt": fps.SGA},
        },
        banner_before=b"Welcome",
        banner_after=b"Login:",
        total_time=3.0,
    )
    assert filepath is not None
    with open(filepath, encoding="utf-8") as f:
        data = json.load(f)
    assert data["server-probe"]["fingerprint-data"]["probed-protocol"] == "server"
    assert data["sessions"][0]["host"] == "example.com"
    assert "server" in filepath


def test_save_server_fingerprint_explicit_path(tmp_path):
    save_path = str(tmp_path / "result.json")
    assert _save(writer=MockWriter(), save_path=save_path) == save_path
    with open(save_path, encoding="utf-8") as f:
        data = json.load(f)
    assert "server-probe" in data

    nested = str(tmp_path / "nested" / "dir" / "result.json")
    assert _save(writer=MockWriter(), save_path=nested) == nested


def test_save_server_fingerprint_max_files(tmp_path, monkeypatch):
    monkeypatch.setattr(fps, "DATA_DIR", str(tmp_path))
    assert _save() is not None

    monkeypatch.setattr(fps, "FINGERPRINT_MAX_FILES", 0)
    w2 = MockWriter(extra={"peername": ("10.0.0.2", 23)})
    assert _save(writer=w2, ip="10.0.0.2") is None


def test_save_server_fingerprint_max_fingerprints(tmp_path, monkeypatch):
    monkeypatch.setattr(fps, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(fps, "FINGERPRINT_MAX_FINGERPRINTS", 0)
    assert _save() is None


def test_save_server_fingerprint_data_dir_none(monkeypatch):
    monkeypatch.setattr(fps, "DATA_DIR", None)
    assert _save() is None


def test_count_server_fingerprint_folders(tmp_path):
    assert fps._count_fingerprint_folders(data_dir=str(tmp_path), side="server") == 0
    assert fps._count_fingerprint_folders(data_dir=None, side="server") == 0
    server_dir = tmp_path / "server"
    server_dir.mkdir()
    (server_dir / "hash1").mkdir()
    (server_dir / "hash2").mkdir()
    (server_dir / "not_a_dir.txt").write_text("")
    assert fps._count_fingerprint_folders(data_dir=str(tmp_path), side="server") == 2


def test_save_appends_session(tmp_path, monkeypatch):
    monkeypatch.setattr(fps, "DATA_DIR", str(tmp_path))
    fp1 = _save()
    fp2 = _save()
    assert fp1 == fp2
    with open(fp2, encoding="utf-8") as f:
        assert len(json.load(f)["sessions"]) == 2


def test_banner_data_in_saved_fingerprint(tmp_path):
    save_path = str(tmp_path / "result.json")
    _save(
        writer=MockWriter(),
        save_path=save_path,
        banner_before=b"Hello\r\n",
        banner_after=b"Login: ",
    )
    with open(save_path, encoding="utf-8") as f:
        session = json.load(f)["server-probe"]["session_data"]
    assert "Hello" in session["banner_before_return"]
    assert session["banner_after_return"] == "Login: "
    assert "probe" in session["timing"]


@pytest.mark.asyncio
async def test_fingerprinting_client_shell(tmp_path):
    save_path = str(tmp_path / "result.json")
    reader = MockReader([b"Welcome to BBS\r\nLogin: "])
    writer = MockWriter(will_options=[fps.SGA, fps.ECHO])

    await sfp.fingerprinting_client_shell(
        reader,
        writer,
        host="localhost",
        port=23,
        save_path=save_path,
        silent=True,
        banner_quiet_time=0.01,
        banner_max_wait=0.01,
        mssp_wait=0.01,
    )

    assert writer._closing
    with open(save_path, encoding="utf-8") as f:
        data = json.load(f)
    assert data["server-probe"]["fingerprint-data"]["probed-protocol"] == "server"
    assert data["sessions"][0]["host"] == "localhost"


@pytest.mark.asyncio
async def test_fingerprinting_client_shell_no_save(monkeypatch):

    monkeypatch.setattr(fps, "DATA_DIR", None)

    writer = MockWriter()
    await sfp.fingerprinting_client_shell(
        MockReader([]),
        writer,
        host="localhost",
        port=23,
        silent=True,
        banner_quiet_time=0.01,
        banner_max_wait=0.01,
        mssp_wait=0.01,
    )
    assert writer._closing


@pytest.mark.asyncio
async def test_fingerprinting_client_shell_display(tmp_path, monkeypatch, capsys):

    monkeypatch.setattr(sfp, "_JQ", None)

    save_path = str(tmp_path / "result.json")
    reader = MockReader([b"Hello"])
    writer = MockWriter(will_options=[fps.SGA])

    await sfp.fingerprinting_client_shell(
        reader,
        writer,
        host="localhost",
        port=23,
        save_path=save_path,
        banner_quiet_time=0.01,
        banner_max_wait=0.01,
        mssp_wait=0.01,
    )

    captured = capsys.readouterr()
    output = json.loads(captured.out)
    assert output["server-probe"]["fingerprint-data"]["probed-protocol"] == "server"
    assert output["sessions"][0]["host"] == "localhost"
    session = output["server-probe"]["session_data"]
    assert isinstance(session.get("banner_before_return", ""), str)
    assert "server_requested" not in session.get("option_states", {})


def test_save_fingerprint_name(tmp_path):
    names_path = fps._save_fingerprint_name("abcd1234abcd1234", "my-server", str(tmp_path))
    with open(names_path, encoding="utf-8") as f:
        names = json.load(f)
    assert names["abcd1234abcd1234"] == "my-server"

    fps._save_fingerprint_name("ffff0000ffff0000", "other-server", str(tmp_path))
    with open(names_path, encoding="utf-8") as f:
        names = json.load(f)
    assert names["abcd1234abcd1234"] == "my-server"
    assert names["ffff0000ffff0000"] == "other-server"

    fps._save_fingerprint_name("abcd1234abcd1234", "renamed", str(tmp_path))
    with open(names_path, encoding="utf-8") as f:
        names = json.load(f)
    assert names["abcd1234abcd1234"] == "renamed"


def test_parse_environ_send_ibm_os400():
    """Parse an OS/400-style SEND with IBMRSEED + binary seed data."""
    raw = USERVAR + b"IBMRSEED\xb6\xd7>\xd5<H\xe4\xa3" + VAR + USERVAR
    entries = sfp._parse_environ_send(raw)
    assert len(entries) == 3
    assert entries[0]["type"] == "USERVAR"
    assert entries[0]["name"] == "IBMRSEED"
    assert entries[1] == {"type": "VAR", "name": "*"}
    assert entries[2] == {"type": "USERVAR", "name": "*"}


def test_parse_environ_send_standard():
    """Parse a standard SEND requesting USER and LANG."""
    raw = VAR + b"USER" + VAR + b"LANG"
    entries = sfp._parse_environ_send(raw)
    assert len(entries) == 2
    assert entries[0] == {"type": "VAR", "name": "USER"}
    assert entries[1] == {"type": "VAR", "name": "LANG"}


def test_collect_option_states_with_environ_send():
    writer = MockWriter()
    writer.environ_send_raw = VAR + b"USER"
    states = sfp._collect_server_option_states(writer)
    assert "environ_requested" in states
    assert states["environ_requested"][0]["name"] == "USER"


def test_save_fingerprint_name_no_data_dir():
    with pytest.raises(ValueError):
        fps._save_fingerprint_name("abcd1234abcd1234", "test", None)


@pytest.mark.asyncio
async def test_fingerprinting_client_shell_set_name(tmp_path, monkeypatch):

    monkeypatch.setattr(fps, "DATA_DIR", str(tmp_path))

    save_path = str(tmp_path / "result.json")
    reader = MockReader([b"Welcome"])
    writer = MockWriter(will_options=[fps.SGA, fps.ECHO])

    await sfp.fingerprinting_client_shell(
        reader,
        writer,
        host="localhost",
        port=23,
        save_path=save_path,
        silent=True,
        set_name="my-bbs",
        banner_quiet_time=0.01,
        banner_max_wait=0.01,
        mssp_wait=0.01,
    )

    with open(save_path, encoding="utf-8") as f:
        data = json.load(f)
    protocol_hash = data["server-probe"]["fingerprint"]

    names = fps._load_fingerprint_names(str(tmp_path))
    assert names[protocol_hash] == "my-bbs"


@pytest.mark.asyncio
async def test_fingerprinting_client_shell_encoding(tmp_path):

    save_path = str(tmp_path / "result.json")
    writer = MockWriter(will_options=[fps.SGA])

    await sfp.fingerprinting_client_shell(
        MockReader([]),
        writer,
        host="localhost",
        port=23,
        save_path=save_path,
        silent=True,
        environ_encoding="cp037",
        banner_quiet_time=0.01,
        banner_max_wait=0.01,
        mssp_wait=0.01,
    )

    with open(save_path, encoding="utf-8") as f:
        data = json.load(f)
    assert data["server-probe"]["session_data"]["encoding"] == "cp037"


@pytest.mark.asyncio
async def test_fingerprinting_client_shell_set_name_no_data_dir(monkeypatch):

    monkeypatch.setattr(fps, "DATA_DIR", None)

    writer = MockWriter()
    await sfp.fingerprinting_client_shell(
        MockReader([]),
        writer,
        host="localhost",
        port=23,
        silent=True,
        set_name="should-warn",
        banner_quiet_time=0.01,
        banner_max_wait=0.01,
        mssp_wait=0.01,
    )
    assert writer._closing


@pytest.mark.asyncio
async def test_fingerprinting_client_shell_mssp(tmp_path, monkeypatch, capsys):

    monkeypatch.setattr(sfp, "_JQ", None)

    save_path = str(tmp_path / "result.json")
    reader = MockReader([b"Welcome to TestMUD\r\n"])
    writer = MockWriter(will_options=[fps.SGA])
    writer.mssp_data = {"NAME": "TestMUD", "PLAYERS": "42", "CODEBASE": "telnetlib3"}

    await sfp.fingerprinting_client_shell(
        reader,
        writer,
        host="localhost",
        port=23,
        save_path=save_path,
        banner_quiet_time=0.01,
        banner_max_wait=0.01,
        mssp_wait=0.01,
    )

    with open(save_path, encoding="utf-8") as f:
        data = json.load(f)
    assert data["server-probe"]["session_data"]["mssp"] == {
        "NAME": "TestMUD",
        "PLAYERS": "42",
        "CODEBASE": "telnetlib3",
    }

    captured = capsys.readouterr()
    output = json.loads(captured.out)
    assert output["server-probe"]["session_data"]["mssp"]["NAME"] == "TestMUD"


@pytest.mark.asyncio
async def test_fingerprinting_client_shell_no_mssp(tmp_path):

    save_path = str(tmp_path / "result.json")
    writer = MockWriter(will_options=[fps.SGA])

    await sfp.fingerprinting_client_shell(
        MockReader([]),
        writer,
        host="localhost",
        port=23,
        save_path=save_path,
        silent=True,
        banner_quiet_time=0.01,
        banner_max_wait=0.01,
        mssp_wait=0.01,
    )

    with open(save_path, encoding="utf-8") as f:
        data = json.load(f)
    assert "mssp" not in data["server-probe"]["session_data"]


class ErrorReader(MockReader):
    """MockReader whose read() raises a connection error."""

    def __init__(self, exc: Exception):
        super().__init__()
        self._exc = exc

    async def read(self, n: int) -> bytes:
        raise self._exc


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exc",
    [
        ConnectionResetError(104, "Connection reset by peer"),
        ConnectionAbortedError("Connection aborted"),
        EOFError("EOF"),
    ],
)
async def test_fingerprinting_client_shell_connection_error(exc):
    """Connection errors produce a warning, not an unhandled exception."""

    writer = MockWriter()
    await sfp.fingerprinting_client_shell(
        ErrorReader(exc),
        writer,
        host="192.0.2.1",
        port=23,
        silent=True,
        banner_quiet_time=0.01,
        banner_max_wait=0.01,
        mssp_wait=0.01,
    )
    assert writer._closing


@pytest.mark.asyncio
async def test_probe_server_capabilities_quick_default():
    """Default scan_type='quick' excludes legacy options."""
    writer = MockWriter(wont_options=[fps.BINARY])
    results = await sfp.probe_server_capabilities(writer, timeout=0.01)
    probed_names = set(results.keys())
    legacy_names = {name for _, name, _ in fps.LEGACY_OPTIONS}
    assert not probed_names.intersection(legacy_names)


@pytest.mark.asyncio
async def test_probe_server_capabilities_full():
    """scan_type='full' includes legacy options."""
    writer = MockWriter(wont_options=[fps.BINARY])
    results = await sfp.probe_server_capabilities(writer, timeout=0.01, scan_type="full")
    probed_names = set(results.keys())
    legacy_names = {name for _, name, _ in fps.LEGACY_OPTIONS}
    assert probed_names.issuperset(legacy_names)
    base = fps.ALL_PROBE_OPTIONS + fps.EXTENDED_OPTIONS
    expected = len(base) - len([o for o in base if o[0] in sfp._CLIENT_ONLY_WILL])
    assert len(results) == expected


@pytest.mark.asyncio
async def test_scan_type_recorded_in_fingerprint(tmp_path):
    """scan_type appears in both session_data and fingerprint-data."""

    for scan_type in ("quick", "full"):
        save_path = str(tmp_path / f"{scan_type}.json")
        reader = MockReader([b"Welcome"])
        writer = MockWriter(will_options=[fps.SGA])

        await sfp.fingerprinting_client_shell(
            reader,
            writer,
            host="localhost",
            port=23,
            save_path=save_path,
            silent=True,
            scan_type=scan_type,
            banner_quiet_time=0.01,
            banner_max_wait=0.01,
            mssp_wait=0.01,
        )

        with open(save_path, encoding="utf-8") as f:
            data = json.load(f)
        assert data["server-probe"]["session_data"]["scan_type"] == scan_type
        assert data["server-probe"]["fingerprint-data"]["scan-type"] == scan_type


def test_parse_environ_send_empty_payload():
    """Bare SB NEW_ENVIRON SEND SE (empty payload) means 'send all' per RFC 1572."""
    entries = sfp._parse_environ_send(b"")
    assert len(entries) == 2
    assert entries[0] == {"type": "VAR", "name": "*"}
    assert entries[1] == {"type": "USERVAR", "name": "*"}


@pytest.mark.asyncio
async def test_probe_skipped_when_closing(tmp_path):
    """Probe burst is skipped when the connection is already closed."""

    save_path = str(tmp_path / "result.json")
    writer = MockWriter(will_options=[fps.SGA])
    writer._closing = True

    await sfp.fingerprinting_client_shell(
        MockReader([]),
        writer,
        host="localhost",
        port=23,
        save_path=save_path,
        silent=True,
        banner_quiet_time=0.01,
        banner_max_wait=0.01,
        mssp_wait=0.01,
    )

    assert not writer._iac_calls
    with open(save_path, encoding="utf-8") as f:
        data = json.load(f)
    assert data["server-probe"]["fingerprint-data"]["offered-options"] == []
    assert data["server-probe"]["fingerprint-data"]["refused-options"] == []


@pytest.mark.parametrize(
    "banner,expected",
    [
        pytest.param(b"Welcome\r\n", b"\r\n", id="no_prompt"),
        pytest.param(b"", b"\r\n", id="empty"),
        pytest.param(b"Continue? (yes/no) ", b"yes\r\n", id="yes_no_parens"),
        pytest.param(b"Continue? (y/n) ", b"y\r\n", id="y_n_parens"),
        pytest.param(b"Accept terms? [Yes/No]:", b"yes\r\n", id="yes_no_brackets"),
        pytest.param(b"Accept? [Y/N]:", b"y\r\n", id="y_n_brackets"),
        pytest.param(b"Accept YES/NO now", b"yes\r\n", id="yes_no_uppercase"),
        pytest.param(b"Confirm y/n\r\n> ", b"y\r\n", id="y_n_trailing_newline"),
        pytest.param(b"Type yes/no please", b"yes\r\n", id="yes_no_space_delimited"),
        pytest.param(b"systemd/network", b"\r\n", id="false_positive_word"),
        pytest.param(b"beyond", b"\r\n", id="substring_y_n_not_matched"),
        pytest.param(b"Enter your name:", b"\r\n", id="name_prompt_no_who"),
        pytest.param(b"Type 'help' for a list of commands:", b"help\r\n", id="help_single_quotes"),
        pytest.param(b'Enter your name (or "help"):', b"help\r\n", id="help_double_quotes"),
        pytest.param(
            b"HELP               to see available commands.\r\n",
            b"help\r\n",
            id="help_bare_command_listing",
        ),
        pytest.param(b"Type HELP for info", b"help\r\n", id="help_bare_mid_sentence"),
        pytest.param(b"helpful tips", b"\r\n", id="help_inside_word_not_matched"),
        pytest.param(
            b"connect <name>\r\n"
            b"WHO                to see players connected.\r\n"
            b"HELP               to see available commands.\r\n",
            b"help\r\n",
            id="help_from_command_listing",
        ),
        pytest.param(b"Color? ", b"y\r\n", id="color_question"),
        pytest.param(b"Do you want color? ", b"y\r\n", id="color_in_sentence"),
        pytest.param(b"ANSI COLOR? ", b"y\r\n", id="color_uppercase"),
        pytest.param(b"color ? ", b"y\r\n", id="color_space_before_question"),
        pytest.param(b"colorful display", b"\r\n", id="color_no_question_mark"),
        pytest.param(
            b"Select charset:\r\n1) ASCII\r\n2) ISO-8859-1\r\n5) UTF-8\r\n",
            b"5\r\n",
            id="menu_utf8",
        ),
        pytest.param(b"3) utf-8\r\nChoose: ", b"3\r\n", id="menu_utf8_lowercase"),
        pytest.param(b"Choose encoding: 1) UTF8", b"1\r\n", id="menu_utf8_no_hyphen"),
        pytest.param(
            b"12) UTF-8\r\nSelect: ",
            b"12\r\n",
            id="menu_utf8_multidigit",
        ),
        pytest.param(b"[5] UTF-8\r\nSelect: ", b"5\r\n", id="menu_utf8_brackets"),
        pytest.param(b"[2] utf-8\r\n", b"2\r\n", id="menu_utf8_brackets_lower"),
        pytest.param(b"3. UTF-8\r\n", b"3\r\n", id="menu_utf8_dot"),
        pytest.param(b"1) ASCII\r\n2) Latin-1\r\n", b"\r\n", id="menu_no_utf8"),
        pytest.param(b"(1) Ansi\r\n(2) VT100\r\n", b"1\r\n", id="menu_ansi_parens"),
        pytest.param(b"[1] ANSI\r\n[2] VT100\r\n", b"1\r\n", id="menu_ansi_brackets"),
        pytest.param(b"(3) ansi\r\n", b"3\r\n", id="menu_ansi_lowercase"),
        pytest.param(b"[12] Ansi\r\n", b"12\r\n", id="menu_ansi_multidigit"),
        pytest.param(b"(1] ANSI\r\n", b"1\r\n", id="menu_ansi_mixed_brackets"),
        pytest.param(b"3. ANSI\r\n", b"3\r\n", id="menu_ansi_dot"),
        pytest.param(b"3. English/ANSI\r\n", b"3\r\n", id="menu_english_ansi"),
        pytest.param(b"2. English/ANSI\r\n", b"2\r\n", id="menu_english_ansi_2"),
        pytest.param(
            b"1) ASCII\r\n2) UTF-8\r\n(3) Ansi\r\n",
            b"2\r\n",
            id="menu_utf8_preferred_over_ansi",
        ),
        pytest.param(
            b"1. ASCII\r\n2. UTF-8\r\n3. English/ANSI\r\n",
            b"2\r\n",
            id="menu_utf8_dot_preferred_over_ansi_dot",
        ),
        pytest.param(b"gb/big5", b"big5\r\n", id="gb_big5"),
        pytest.param(b"GB/Big5\r\n", b"big5\r\n", id="gb_big5_mixed_case"),
        pytest.param(b"Select: GB / Big5 ", b"big5\r\n", id="gb_big5_spaces"),
        pytest.param(b"gb/big 5\r\n", b"big5\r\n", id="gb_big5_space_before_5"),
        pytest.param(b"bigfoot5", b"\r\n", id="big5_inside_word_not_matched"),
        pytest.param(
            b"Press [.ESC.] twice within 15 seconds to CONTINUE...",
            b"\x1b\x1b",
            id="esc_twice_mystic",
        ),
        pytest.param(
            b"Press [ESC] twice to continue",
            b"\x1b\x1b",
            id="esc_twice_no_dots",
        ),
        pytest.param(
            b"Press ESC twice to continue",
            b"\x1b\x1b",
            id="esc_twice_bare",
        ),
        pytest.param(
            b"Press <Esc> twice for the BBS ... ",
            b"\x1b\x1b",
            id="esc_twice_angle_brackets",
        ),
        pytest.param(
            b"\x1b[33mPress [.ESC.] twice within 10 seconds\x1b[0m",
            b"\x1b\x1b",
            id="esc_twice_ansi_wrapped",
        ),
        pytest.param(
            b"\x1b[1;1H\x1b[2JPress [.ESC.] twice within 15 seconds to CONTINUE...",
            b"\x1b\x1b",
            id="esc_twice_after_clear_screen",
        ),
        pytest.param(
            b"\x1b[31mColor? \x1b[0m",
            b"y\r\n",
            id="color_ansi_wrapped",
        ),
        pytest.param(
            b"\x1b[1mContinue? (y/n)\x1b[0m ",
            b"y\r\n",
            id="yn_ansi_wrapped",
        ),
    ],
)
def test_detect_yn_prompt(banner, expected):
    assert sfp._detect_yn_prompt(banner) == expected


@pytest.mark.asyncio
async def test_fingerprinting_shell_yn_prompt(tmp_path):
    """Banner with y/n prompt causes 'y\\r\\n' instead of bare '\\r\\n'."""
    save_path = str(tmp_path / "result.json")
    reader = MockReader([b"Do you accept? (y/n) "])
    writer = MockWriter(will_options=[fps.SGA])

    await sfp.fingerprinting_client_shell(
        reader,
        writer,
        host="localhost",
        port=23,
        save_path=save_path,
        silent=True,
        banner_quiet_time=0.01,
        banner_max_wait=0.01,
        mssp_wait=0.01,
    )

    assert b"y\r\n" in writer._writes


@pytest.mark.asyncio
async def test_fingerprinting_shell_yes_no_prompt(tmp_path):
    """Banner with yes/no prompt causes 'yes\\r\\n' instead of bare '\\r\\n'."""
    save_path = str(tmp_path / "result.json")
    reader = MockReader([b"Continue? (yes/no) "])
    writer = MockWriter(will_options=[fps.SGA])

    await sfp.fingerprinting_client_shell(
        reader,
        writer,
        host="localhost",
        port=23,
        save_path=save_path,
        silent=True,
        banner_quiet_time=0.01,
        banner_max_wait=0.01,
        mssp_wait=0.01,
    )

    assert b"yes\r\n" in writer._writes


@pytest.mark.asyncio
async def test_fingerprinting_shell_esc_twice_prompt(tmp_path):
    """Banner with ESC-twice botcheck sends two raw ESC bytes."""
    save_path = str(tmp_path / "result.json")
    reader = MockReader([b"Press [.ESC.] twice within 15 seconds to CONTINUE..."])
    writer = MockWriter(will_options=[fps.SGA])

    await sfp.fingerprinting_client_shell(
        reader,
        writer,
        host="localhost",
        port=23,
        save_path=save_path,
        silent=True,
        banner_quiet_time=0.01,
        banner_max_wait=0.01,
        mssp_wait=0.01,
    )

    assert b"\x1b\x1b" in writer._writes


@pytest.mark.asyncio
async def test_fingerprinting_shell_no_yn_prompt(tmp_path):
    """Banner without y/n prompt sends bare '\\r\\n'."""
    save_path = str(tmp_path / "result.json")
    reader = MockReader([b"Welcome to BBS\r\n"])
    writer = MockWriter(will_options=[fps.SGA])

    await sfp.fingerprinting_client_shell(
        reader,
        writer,
        host="localhost",
        port=23,
        save_path=save_path,
        silent=True,
        banner_quiet_time=0.01,
        banner_max_wait=0.01,
        mssp_wait=0.01,
    )

    assert b"\r\n" in writer._writes
    assert b"y\r\n" not in writer._writes
    assert b"yes\r\n" not in writer._writes



@pytest.mark.asyncio
async def test_fingerprinting_shell_help_prompt(tmp_path):
    """Banner with 'help' option causes 'help\\r\\n' response."""
    save_path = str(tmp_path / "result.json")
    reader = MockReader([b"Type 'help' for a list of commands:"])
    writer = MockWriter(will_options=[fps.SGA])

    await sfp.fingerprinting_client_shell(
        reader,
        writer,
        host="localhost",
        port=23,
        save_path=save_path,
        silent=True,
        banner_quiet_time=0.01,
        banner_max_wait=0.01,
        mssp_wait=0.01,
    )

    assert b"help\r\n" in writer._writes


@pytest.mark.asyncio
async def test_fingerprinting_shell_multi_prompt(tmp_path):
    """Server asks color first, then presents a UTF-8 charset menu."""
    save_path = str(tmp_path / "result.json")
    writer = MockWriter(will_options=[fps.SGA])
    reader = InteractiveMockReader([
        b"Color? ",
        b"Select charset:\r\n1) ASCII\r\n2) UTF-8\r\n",
        b"Welcome!\r\n",
    ], writer)

    await sfp.fingerprinting_client_shell(
        reader,
        writer,
        host="localhost",
        port=23,
        save_path=save_path,
        silent=True,
        banner_quiet_time=0.01,
        banner_max_wait=0.01,
        mssp_wait=0.01,
    )

    assert b"y\r\n" in writer._writes
    assert b"2\r\n" in writer._writes


@pytest.mark.asyncio
async def test_fingerprinting_shell_multi_prompt_stops_on_bare_return(tmp_path):
    """Loop stops after a bare \\r\\n response (no prompt detected)."""
    save_path = str(tmp_path / "result.json")
    writer = MockWriter(will_options=[fps.SGA])
    reader = InteractiveMockReader([
        b"Color? ",
        b"Welcome!\r\n",
    ], writer)

    await sfp.fingerprinting_client_shell(
        reader,
        writer,
        host="localhost",
        port=23,
        save_path=save_path,
        silent=True,
        banner_quiet_time=0.01,
        banner_max_wait=0.01,
        mssp_wait=0.01,
    )

    assert b"y\r\n" in writer._writes
    prompt_writes = [w for w in writer._writes if w in (b"y\r\n", b"\r\n")]
    assert len(prompt_writes) == 2
    assert prompt_writes == [b"y\r\n", b"\r\n"]


@pytest.mark.asyncio
async def test_fingerprinting_shell_multi_prompt_max_replies(tmp_path):
    """Loop does not exceed _MAX_PROMPT_REPLIES rounds."""
    save_path = str(tmp_path / "result.json")
    writer = MockWriter(will_options=[fps.SGA])
    reader = InteractiveMockReader(
        [b"Color? "] * (sfp._MAX_PROMPT_REPLIES + 1),
        writer,
    )

    await sfp.fingerprinting_client_shell(
        reader,
        writer,
        host="localhost",
        port=23,
        save_path=save_path,
        silent=True,
        banner_quiet_time=0.01,
        banner_max_wait=0.01,
        mssp_wait=0.01,
    )

    y_writes = [w for w in writer._writes if w == b"y\r\n"]
    assert len(y_writes) == sfp._MAX_PROMPT_REPLIES


class TestCullDisplay:
    """Tests for _cull_display bytes conversion."""

    def test_bytes_utf8(self):
        assert sfp._cull_display(b"hello") == "hello"

    def test_bytes_binary(self):
        assert sfp._cull_display(b"\x80\xff") == "80ff"

    def test_bytes_in_dict(self):
        result = sfp._cull_display({"data_bytes": b"\x01"})
        assert result == {"data_bytes": "\x01"}
        json.dumps(result)

    def test_bytes_in_nested_list(self):
        result = sfp._cull_display({"items": [{"val": b"\xfe\xed"}]})
        assert result == {"items": [{"val": "feed"}]}
        json.dumps(result)

    def test_empty_bytes_culled(self):
        result = sfp._cull_display({"data_bytes": b""})
        assert result == {}


@pytest.mark.asyncio
async def test_read_banner_until_quiet_responds_to_dsr():
    """DSR (ESC[6n) in banner data triggers a CPR response (ESC[1;1R)."""
    reader = MockReader([b"Hello\x1b[6nWorld"])
    writer = MockWriter()
    result = await sfp._read_banner_until_quiet(
        reader, quiet_time=0.01, max_wait=0.05, writer=writer,
    )
    assert result == b"Hello\x1b[6nWorld"
    assert b"\x1b[1;1R" in writer._writes


@pytest.mark.asyncio
async def test_read_banner_until_quiet_multiple_dsr():
    """Multiple DSR requests each get a CPR response."""
    reader = MockReader([b"\x1b[6n", b"banner\x1b[6n"])
    writer = MockWriter()
    await sfp._read_banner_until_quiet(
        reader, quiet_time=0.01, max_wait=0.05, writer=writer,
    )
    cpr_count = sum(1 for w in writer._writes if w == b"\x1b[1;1R")
    assert cpr_count == 2


@pytest.mark.asyncio
async def test_read_banner_until_quiet_no_dsr_no_write():
    """No DSR in banner means no CPR writes."""
    reader = MockReader([b"Welcome to BBS\r\n"])
    writer = MockWriter()
    await sfp._read_banner_until_quiet(
        reader, quiet_time=0.01, max_wait=0.05, writer=writer,
    )
    assert not writer._writes


@pytest.mark.asyncio
async def test_read_banner_until_quiet_no_writer_ignores_dsr():
    """Without a writer, DSR is silently ignored."""
    reader = MockReader([b"Hello\x1b[6n"])
    result = await sfp._read_banner_until_quiet(
        reader, quiet_time=0.01, max_wait=0.05,
    )
    assert result == b"Hello\x1b[6n"


@pytest.mark.asyncio
async def test_fingerprinting_shell_dsr_response(tmp_path):
    """Full session responds to DSR in the pre-return banner."""
    save_path = str(tmp_path / "result.json")
    reader = MockReader([b"\x1b[6nWelcome to BBS\r\n"])
    writer = MockWriter(will_options=[fps.SGA])

    await sfp.fingerprinting_client_shell(
        reader,
        writer,
        host="localhost",
        port=23,
        save_path=save_path,
        silent=True,
        banner_quiet_time=0.01,
        banner_max_wait=0.01,
        mssp_wait=0.01,
    )

    assert b"\x1b[1;1R" in writer._writes


@pytest.mark.asyncio
async def test_fingerprinting_settle_dsr_response(tmp_path):
    """DSR arriving during negotiation settle gets an immediate CPR reply."""
    save_path = str(tmp_path / "result.json")
    reader = MockReader([b"\x1b[6nWelcome\r\n"])
    writer = MockWriter(will_options=[fps.SGA])

    await sfp.fingerprinting_client_shell(
        reader,
        writer,
        host="localhost",
        port=23,
        save_path=save_path,
        silent=True,
        banner_quiet_time=0.01,
        banner_max_wait=0.01,
        mssp_wait=0.01,
    )

    assert b"\x1b[1;1R" in writer._writes


@pytest.mark.asyncio
async def test_fingerprinting_shell_delayed_prompt(tmp_path):
    """Bare-return banner followed by ESC-twice prompt still gets answered."""
    save_path = str(tmp_path / "result.json")
    writer = MockWriter(will_options=[fps.SGA])
    reader = InteractiveMockReader([
        b"Starting BBS-DOS...\r\n",
        b"Press [.ESC.] twice within 15 seconds to CONTINUE...",
        b"Welcome!\r\n",
    ], writer)

    await sfp.fingerprinting_client_shell(
        reader,
        writer,
        host="localhost",
        port=23,
        save_path=save_path,
        silent=True,
        banner_quiet_time=0.01,
        banner_max_wait=0.01,
        mssp_wait=0.01,
    )

    assert b"\x1b\x1b" in writer._writes


@pytest.mark.asyncio
async def test_read_banner_virtual_cursor_defeats_robot_check():
    """DSR-space-DSR produces CPR col=1 then col=2 (width=1)."""
    reader = MockReader([b"\x1b[6n \x1b[6n"])
    writer = MockWriter()
    cursor = sfp._VirtualCursor()
    await sfp._read_banner_until_quiet(
        reader, quiet_time=0.01, max_wait=0.05, writer=writer, cursor=cursor,
    )
    cpr_writes = [w for w in writer._writes if b"R" in w]
    assert cpr_writes[0] == b"\x1b[1;1R"
    assert cpr_writes[1] == b"\x1b[1;2R"


@pytest.mark.asyncio
async def test_read_banner_virtual_cursor_separate_chunks():
    """DSR in separate chunks still tracks cursor correctly."""
    reader = MockReader([b"\x1b[6n", b" \x1b[6n"])
    writer = MockWriter()
    cursor = sfp._VirtualCursor()
    await sfp._read_banner_until_quiet(
        reader, quiet_time=0.01, max_wait=0.05, writer=writer, cursor=cursor,
    )
    cpr_writes = [w for w in writer._writes if b"R" in w]
    assert cpr_writes[0] == b"\x1b[1;1R"
    assert cpr_writes[1] == b"\x1b[1;2R"


@pytest.mark.asyncio
async def test_read_banner_virtual_cursor_wide_char():
    """Wide CJK character advances cursor by 2."""
    reader = MockReader([b"\x1b[6n\xe4\xb8\xad\x1b[6n"])
    writer = MockWriter()
    cursor = sfp._VirtualCursor()
    await sfp._read_banner_until_quiet(
        reader, quiet_time=0.01, max_wait=0.05, writer=writer, cursor=cursor,
    )
    cpr_writes = [w for w in writer._writes if b"R" in w]
    assert cpr_writes[0] == b"\x1b[1;1R"
    assert cpr_writes[1] == b"\x1b[1;3R"


def test_virtual_cursor_backspace():
    """Backspace moves cursor left."""
    cursor = sfp._VirtualCursor()
    cursor.advance(b"AB\x08")
    assert cursor.col == 2


def test_virtual_cursor_cr():
    """Carriage return resets cursor to column 1."""
    cursor = sfp._VirtualCursor()
    cursor.advance(b"Hello\r")
    assert cursor.col == 1


def test_virtual_cursor_ansi_stripped():
    """ANSI color codes do not advance cursor."""
    cursor = sfp._VirtualCursor()
    cursor.advance(b"\x1b[31mX\x1b[0m")
    assert cursor.col == 2
