# std imports
import json
from pathlib import Path

# 3rd party
import pytest

# local
from telnetlib3 import fingerprinting as fps
from telnetlib3 import fingerprinting_display as fpd
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
        self.rejected_will: set = set()
        self.rejected_do: set = set()
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


class MockTerm:
    normal = ""
    clear = ""
    civis = ""
    cnorm = ""
    height = 50
    width = 80
    forestgreen = staticmethod(lambda x: x)
    firebrick1 = staticmethod(lambda x: x)
    darkorange = staticmethod(lambda x: x)
    bold_magenta = staticmethod(lambda x: x)

    def magenta(self, s):
        return s

    def cyan(self, s):
        return s

    def clear_eol(self):
        return ""


@pytest.mark.asyncio
async def test_probe_client_capabilities():
    options = [(fps.BINARY, "BINARY", ""), (fps.SGA, "SGA", "")]
    writer = MockWriter(will_options=[fps.BINARY],
                        wont_options=[fps.SGA])
    results = await fps.probe_client_capabilities(
        writer, options=options, timeout=0.001)
    assert results["BINARY"]["status"] == "WILL"
    assert results["SGA"]["status"] == "WONT"


def test_save_fingerprint_data(tmp_path, monkeypatch):
    monkeypatch.setattr(fps, "DATA_DIR", str(tmp_path))

    writer = MockWriter(extra={"peername": ("10.0.0.1", 9999),
                               "TERM": "xterm"})
    writer._protocol = MockProtocol(
        {"TERM": "xterm", "ttype1": "xterm", "ttype2": "xterm-256"})
    probe = {
        "BINARY": {"status": "WILL", "opt": fps.BINARY},
        "SGA": {"status": "WONT", "opt": fps.SGA},
    }
    filepath = fps._save_fingerprint_data(writer, probe, 0.5)
    assert filepath is not None and Path(filepath).exists()

    with open(filepath) as f:
        data = json.load(f)

    assert "telnet-probe" in data
    tp = data["telnet-probe"]
    assert tp["fingerprint-data"]["probed-protocol"] == "client"
    assert "BINARY" in tp["fingerprint-data"]["supported-options"]
    assert tp["session_data"]["ttype_cycle"] == ["xterm", "xterm-256"]
    assert "peername" not in tp["session_data"]["extra"]
    assert len(data["sessions"]) == 1
    assert data["sessions"][0]["ip"] == "10.0.0.1"
    assert Path(filepath).parent.name == fps._UNKNOWN_TERMINAL_HASH

    monkeypatch.setattr(fps, "DATA_DIR", None)
    assert fps._save_fingerprint_data(writer, {}, 0.5) is None


def test_save_fingerprint_appends_session(tmp_path, monkeypatch):
    monkeypatch.setattr(fps, "DATA_DIR", str(tmp_path))

    writer = MockWriter(extra={"peername": ("10.0.0.1", 9999),
                               "TERM": "xterm"})
    writer._protocol = MockProtocol({"TERM": "xterm"})
    probe = {"BINARY": {"status": "WILL", "opt": fps.BINARY}}

    fp1 = fps._save_fingerprint_data(writer, probe, 0.5)
    fp2 = fps._save_fingerprint_data(writer, probe, 0.5)
    assert fp1 == fp2

    with open(fp2) as f:
        data = json.load(f)
    assert len(data["sessions"]) == 2


def test_protocol_fingerprint():
    probe = {"BINARY": {"status": "WILL", "opt": fps.BINARY}}
    w = MockWriter(extra={"TERM": "xterm", "HOME": "/home/user"})
    w._protocol = MockProtocol({"HOME": "/home/user"})
    assert fps._create_protocol_fingerprint(w, probe)["HOME"] == "True"

    probe2 = {
        "TTYPE": {"status": "WILL", "opt": fps.TTYPE},
        "BINARY": {"status": "WILL", "opt": fps.BINARY},
        "SGA": {"status": "WONT", "opt": fps.SGA},
    }
    fp = fps._create_protocol_fingerprint(MockWriter(), probe2)
    assert fp["supported-options"] == ["BINARY", "TTYPE"]
    assert fp["refused-options"] == ["SGA"]


def test_protocol_hash_consistency():
    probe = {"BINARY": {"status": "WILL", "opt": fps.BINARY}}
    w1 = MockWriter(extra={"TERM": "xterm", "HOME": "/home/alice"})
    w1._protocol = MockProtocol({"HOME": "/home/alice"})
    w2 = MockWriter(extra={"TERM": "xterm", "HOME": "/home/bob"})
    w2._protocol = MockProtocol({"HOME": "/home/bob"})

    h1 = fps._hash_fingerprint(
        fps._create_protocol_fingerprint(w1, probe))
    h2 = fps._hash_fingerprint(
        fps._create_protocol_fingerprint(w2, probe))
    assert h1 == h2
    assert len(h1) == 16


@pytest.mark.parametrize("text,expected", [
    ("Ghostty", "Ghostty"),
    ("  Ghostty  ", "Ghostty"),
    ("", None),
    ("   ", None),
    ("bad\x00name", None),
    ("bad\x1bname", None),
    ("bad\x7fname", None),
    ("good name 123", "good name 123"),
])
def test_validate_suggestion(text, expected):
    assert fps._validate_suggestion(text) == expected


def test_prompt_stores_suggestions(tmp_path, monkeypatch):
    filepath = tmp_path / "test.json"
    data = {
        "telnet-probe": {"fingerprint": "aaa"},
        "terminal-probe": {"fingerprint": "bbbb"},
        "sessions": [],
    }
    filepath.write_text(json.dumps(data))

    inputs = iter(["Ghostty", "GNU Telnet"])
    monkeypatch.setattr(fpd, "_cooked_input", lambda prompt: next(inputs))

    fpd._prompt_fingerprint_identification(
        MockTerm(), data, str(filepath), {}
    )
    assert data["suggestions"]["terminal-emulator"] == "Ghostty"
    assert data["suggestions"]["telnet-client"] == "GNU Telnet"

    with open(filepath) as f:
        saved = json.load(f)
    assert saved["suggestions"]["terminal-emulator"] == "Ghostty"


def test_prompt_stores_revision(tmp_path, monkeypatch):
    filepath = tmp_path / "test.json"
    data = {
        "telnet-probe": {"fingerprint": "aaa"},
        "terminal-probe": {"fingerprint": "bbbb"},
        "sessions": [],
    }
    filepath.write_text(json.dumps(data))

    inputs = iter(["Ghostty2", "inetutils-2.5"])
    monkeypatch.setattr(fpd, "_cooked_input", lambda prompt: next(inputs))
    names = {"aaa": "GNU Telnet", "bbbb": "Ghostty"}
    fpd._prompt_fingerprint_identification(
        MockTerm(), data, str(filepath), names
    )
    assert data["suggestions"]["terminal-emulator-revision"] == "Ghostty2"
    assert data["suggestions"]["telnet-client-revision"] == "inetutils-2.5"

    with open(filepath) as f:
        saved = json.load(f)
    assert saved["suggestions"]["terminal-emulator-revision"] == "Ghostty2"


@pytest.mark.asyncio
async def test_server_shell(monkeypatch):
    async def noop(_):
        pass
    monkeypatch.setattr(fps.asyncio, "sleep", noop)
    monkeypatch.setattr(fps, "DATA_DIR", None)

    writer = MockWriter(extra={"peername": ("127.0.0.1", 12345),
                               "TERM": "xterm"},
                        will_options=[fps.BINARY])
    await fps.fingerprinting_server_shell(MockReader([]), writer)
    assert writer._closing


def test_create_terminal_fingerprint():
    terminal_data = {
        "software_name": "foot",
        "software_version": "1.16.2",
        "ambiguous_width": 1,
        "terminal_results": {
            "number_of_colors": 16777216,
            "sixel": True,
            "kitty_graphics": False,
            "kitty_clipboard_protocol": False,
            "iterm2_features": {"supported": False, "features": {}},
            "kitty_keyboard": {
                "disambiguate": False,
                "report_all_keys": False,
                "report_alternates": False,
                "report_events": False,
                "report_text": False,
            },
            "kitty_notifications": False,
            "kitty_pointer_shapes": False,
            "text_sizing": {"width": False, "scale": False},
            "device_attributes": {
                "service_class": 62,
                "extensions": [22, 4],
            },
            "modes": {
                "2027": {
                    "supported": True,
                    "changeable": True,
                    "enabled": True,
                    "value": 1,
                    "mode_name": "GRAPHEME_CLUSTERING",
                    "mode_description": "Grapheme Clustering",
                    "value_description": "SET",
                },
                "5522": {
                    "supported": False,
                    "changeable": False,
                    "enabled": False,
                    "value": 0,
                    "mode_name": "UNKNOWN",
                    "mode_description": "Unknown mode",
                    "value_description": "NOT_RECOGNIZED",
                },
            },
            "xtgettcap": {
                "supported": True,
                "capabilities": {"TN": "foot", "Co": "256"},
            },
            "foreground_color_hex": "#ffffffffffff",
            "cell_width": 6,
            "cell_height": 16,
            "width": 170,
            "height": 46,
        },
        "test_results": {
            "unicode_wide_results": {
                "17.0.0": {
                    "n_errors": 0,
                    "n_total": 10,
                    "pct_success": 100.0,
                    "codepoints_per_second": 8.7,
                },
            },
            "emoji_vs16_results": {
                "9.0.0": {
                    "n_errors": 2,
                    "n_total": 12,
                    "pct_success": 83.3,
                    "codepoints_per_second": 9.2,
                },
            },
            "language_results": None,
        },
    }

    fp = fpd._create_terminal_fingerprint(terminal_data)

    assert fp["software_name"] == "foot"
    assert fp["software_version"] == "1.16.2"
    assert fp["number_of_colors"] == 16777216
    assert fp["sixel"] is True
    assert fp["kitty_graphics"] is False
    assert fp["kitty_clipboard_protocol"] is False
    assert fp["iterm2_features"] == {"supported": False, "features": {}}
    assert fp["kitty_keyboard"]["disambiguate"] is False
    assert fp["kitty_notifications"] is False
    assert fp["kitty_pointer_shapes"] is False
    assert fp["text_sizing"] == {"width": False, "scale": False}
    assert fp["da_service_class"] == 62
    assert fp["da_extensions"] == [4, 22]
    assert fp["ambiguous_width"] == 1

    assert fp["modes"]["2027"] == {
        "supported": True,
        "changeable": True,
        "enabled": True,
        "value": 1,
    }
    assert fp["modes"]["5522"]["supported"] is False
    assert "mode_name" not in fp["modes"]["2027"]
    assert "mode_description" not in fp["modes"]["2027"]

    assert fp["xtgettcap"]["supported"] is True
    assert fp["xtgettcap"]["capabilities"]["TN"] == "foot"

    assert fp["test_results"]["unicode_wide_results"] == {
        "unicode_version": "17.0.0",
        "n_errors": 0,
        "n_total": 10,
    }
    assert fp["test_results"]["emoji_vs16_results"] == {
        "unicode_version": "9.0.0",
        "n_errors": 2,
        "n_total": 12,
    }
    assert "language_results" not in fp["test_results"]
    assert "pct_success" not in fp["test_results"]["unicode_wide_results"]

    assert "foreground_color_hex" not in fp
    assert "cell_width" not in fp
    assert "width" not in fp
    assert "height" not in fp


def test_terminal_fingerprint_hash_excludes_session_vars():
    import copy
    base = {
        "software_name": "foot",
        "software_version": "1.16.2",
        "ambiguous_width": 1,
        "terminal_results": {
            "number_of_colors": 16777216,
            "sixel": True,
            "kitty_graphics": False,
            "kitty_clipboard_protocol": False,
            "device_attributes": {"service_class": 62, "extensions": [4, 22]},
            "modes": {},
            "xtgettcap": {"supported": True, "capabilities": {"TN": "foot"}},
            "text_sizing": {"width": False, "scale": False},
            "foreground_color_hex": "#000000000000",
            "width": 80,
            "height": 24,
            "cell_width": 6,
            "cell_height": 16,
        },
        "test_results": {},
    }
    data1 = copy.deepcopy(base)
    data2 = copy.deepcopy(base)
    data2["terminal_results"]["foreground_color_hex"] = "#ffffffffffff"
    data2["terminal_results"]["width"] = 200
    data2["terminal_results"]["height"] = 50

    fp1 = fpd._create_terminal_fingerprint(data1)
    fp2 = fpd._create_terminal_fingerprint(data2)
    assert fps._hash_fingerprint(fp1) == fps._hash_fingerprint(fp2)


@pytest.mark.asyncio
async def test_fingerprint_probe_integration(bind_host, unused_tcp_port):
    import asyncio
    async with create_server(
        host=bind_host, port=unused_tcp_port,
        shell=fps.fingerprinting_server_shell, connect_maxwait=0.5,
    ):
        async with open_connection(
            host=bind_host, port=unused_tcp_port,
            connect_minwait=0.2, connect_maxwait=0.5,
        ) as (reader, writer):
            try:
                await asyncio.wait_for(reader.read(100), timeout=1.0)
            except asyncio.TimeoutError:
                pass
