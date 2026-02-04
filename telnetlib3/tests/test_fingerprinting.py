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

    # new JSON structure
    assert "telnet-probe" in data
    tp = data["telnet-probe"]
    assert tp["fingerprint-data"]["probed-protocol"] == "client"
    assert "BINARY" in tp["fingerprint-data"]["supported-options"]
    assert tp["session-data"]["ttype_cycle"] == ["xterm", "xterm-256"]
    assert "peername" not in tp["session-data"]["extra"]

    # sessions list
    assert len(data["sessions"]) == 1
    assert data["sessions"][0]["ip"] == "10.0.0.1"

    # nested layout: client/{telnet_hash}/{unknown_terminal_hash}/
    assert Path(filepath).parent.name == fps._UNKNOWN_TERMINAL_HASH
    assert Path(filepath).parent.parent.parent.name == "client"

    # DATA_DIR=None skips save
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


def test_session_fingerprint():
    w = MockWriter(extra={"peername": ("10.0.0.1", 9999),
                          "TERM": "xterm", "USER": "jq"})
    identity = fps._create_session_fingerprint(w)
    assert identity["client-ip"] == "10.0.0.1"
    assert identity["TERM"] == "xterm"
    assert identity["USER"] == "jq"

    h = fps._hash_fingerprint(identity)
    assert len(h) == 16
    assert h == fps._hash_fingerprint(identity)


def test_atomic_json_write(tmp_path):
    filepath = tmp_path / "test.json"
    fps._atomic_json_write(str(filepath), {"key": "value"})
    assert filepath.exists()
    assert not filepath.with_suffix(".json.new").exists()
    with open(filepath) as f:
        assert json.load(f) == {"key": "value"}


def test_build_seen_counts(tmp_path, monkeypatch):
    monkeypatch.setattr(fpd, "DATA_DIR", str(tmp_path))
    folder = tmp_path / "client" / "aaa" / "bbbb"
    folder.mkdir(parents=True)
    (folder / "sess1.json").write_text("{}")
    (folder / "sess2.json").write_text("{}")

    data_first = {
        "telnet-probe": {
            "fingerprint": "aaa",
            "session-data": {"extra": {"USER": "jdoe"}},
        },
        "terminal-probe": {"fingerprint": "bbbb"},
        "sessions": [{"ip": "10.0.0.1"}],
    }
    result = fpd._build_seen_counts(data_first)
    assert "Welcome jdoe!" in result
    assert "Detected" in result
    assert "aaa" in result
    assert "bbbb" in result
    assert "1 other client" in result

    data_no_user = {
        "telnet-probe": {"fingerprint": "aaa"},
        "terminal-probe": {"fingerprint": "bbbb"},
        "sessions": [{"ip": "10.0.0.1"}],
    }
    result = fpd._build_seen_counts(data_no_user)
    assert "Welcome!" in result
    assert "unknown" not in result

    data_return = {
        "telnet-probe": {
            "fingerprint": "aaa",
            "session-data": {"extra": {"USER": "jdoe"}},
        },
        "terminal-probe": {"fingerprint": "bbbb"},
        "sessions": [
            {"ip": "10.0.0.1"},
            {"ip": "10.0.0.1"},
            {"ip": "10.0.0.1"},
        ],
    }
    result = fpd._build_seen_counts(data_return)
    assert "Welcome back jdoe!" in result
    assert "aaa" in result
    assert "bbbb" in result
    assert "#3" in result
    assert "1 other client" in result


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


def test_protocol_fingerprint_no_terminal_size():
    """Terminal size is not included in protocol fingerprint."""
    probe = {"NAWS": {"status": "WILL", "opt": fps.NAWS}}
    w = MockWriter(extra={"cols": 80, "rows": 24})
    fp = fps._create_protocol_fingerprint(w, probe)
    assert "terminal-size" not in fp
    assert "NAWS" in fp["supported-options"]


def test_protocol_fingerprint_same_hash_different_sizes():
    """Different window sizes produce the same protocol fingerprint hash."""
    probe = {"NAWS": {"status": "WILL", "opt": fps.NAWS}}
    w1 = MockWriter(extra={"cols": 80, "rows": 24})
    w2 = MockWriter(extra={"cols": 120, "rows": 40})
    w3 = MockWriter(extra={"cols": 80, "rows": 25})
    h1 = fps._hash_fingerprint(fps._create_protocol_fingerprint(w1, probe))
    h2 = fps._hash_fingerprint(fps._create_protocol_fingerprint(w2, probe))
    h3 = fps._hash_fingerprint(fps._create_protocol_fingerprint(w3, probe))
    assert h1 == h2 == h3


def test_protocol_fingerprint_timeout_as_refused():
    """Timed-out probes are included in refused-options."""
    probe = {
        "BINARY": {"status": "WILL", "opt": fps.BINARY},
        "SGA": {"status": "WONT", "opt": fps.SGA},
        "ECHO": {"status": "timeout", "opt": fps.ECHO},
    }
    fp = fps._create_protocol_fingerprint(MockWriter(), probe)
    assert fp["supported-options"] == ["BINARY"]
    assert fp["refused-options"] == ["ECHO", "SGA"]


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


def test_collect_rejected_options():
    w = MockWriter()
    w.rejected_will = {fps.AUTHENTICATION, fps.KERMIT}
    result = fps._collect_rejected_options(w)
    assert result["will"] == ["AUTHENTICATION", "KERMIT"]
    assert "do" not in result


def test_collect_rejected_options_empty():
    w = MockWriter()
    assert fps._collect_rejected_options(w) == {}


def test_collect_rejected_options_do():
    w = MockWriter()
    w.rejected_do = {fps.COM_PORT_OPTION}
    result = fps._collect_rejected_options(w)
    assert result["do"] == ["COM_PORT"]
    assert "will" not in result


def test_protocol_fingerprint_with_rejected():
    probe = {"BINARY": {"status": "WILL", "opt": fps.BINARY}}
    w = MockWriter()
    w.rejected_will = {fps.AUTHENTICATION, fps.KERMIT}
    fp = fps._create_protocol_fingerprint(w, probe)
    assert fp["rejected-will"] == ["AUTHENTICATION", "KERMIT"]
    assert "rejected-do" not in fp


def test_protocol_fingerprint_hash_differs_with_rejected():
    probe = {"BINARY": {"status": "WILL", "opt": fps.BINARY}}
    w1 = MockWriter()
    w2 = MockWriter()
    w2.rejected_will = {fps.AUTHENTICATION}
    h1 = fps._hash_fingerprint(
        fps._create_protocol_fingerprint(w1, probe))
    h2 = fps._hash_fingerprint(
        fps._create_protocol_fingerprint(w2, probe))
    assert h1 != h2


def test_session_fingerprint_includes_rejected():
    w = MockWriter(extra={
        "peername": ("127.0.0.1", 12345),
        "TERM": "xterm",
    })
    w.rejected_will = {fps.AUTHENTICATION}
    probe = {"BINARY": {"status": "WILL", "opt": fps.BINARY}}
    session = fps._build_session_fingerprint(w, probe, 0.5)
    assert session["rejected"]["will"] == ["AUTHENTICATION"]


def test_build_telnet_rows():
    data = {
        "telnet-probe": {
            "fingerprint": "abc123def456",
            "fingerprint-data": {
                "supported-options": ["BINARY", "SGA", "TTYPE", "NAWS"],
                "charset": "UTF-8", "encoding": "UTF-8",
                "USER": "True", "HOME": "True",
            },
            "session-data": {
                "extra": {
                    "TERM": "xterm-256color", "cols": 120, "rows": 40,
                    "LANG": "en_US.UTF-8", "charset": "UTF-8",
                    "tspeed": "38400,38400",
                },
                "ttype_cycle": ["xterm-256color", "xterm", "vt100"],
            },
        },
    }
    pairs = dict(fpd._build_telnet_rows(MockTerm(), data))
    assert "abc123def456" == pairs["Fingerprint"]
    assert "xterm-256color" in pairs["Terminal Type"]
    assert "Options" in pairs


def test_build_terminal_rows():
    data = {
        "telnet-probe": {
            "session-data": {"extra": {"cols": 173, "rows": 38}},
        },
        "terminal-probe": {
            "session-data": {
                "software_name": "ghostty", "software_version": "1.0",
                "ambiguous_width": 1,
                "terminal_results": {
                    "number_of_colors": 16777216,
                    "cell_width": 11, "cell_height": 25,
                    "screen_ratio": "16:9", "screen_ratio_name": "HD",
                },
                "test_results": {
                    "unicode_wide_results": {"15.0": {"pct_success": 100.0}},
                    "emoji_zwj_results": {"15.0": {"pct_success": 99.5}},
                    "emoji_vs16_results": {"15.0": {"pct_success": 100.0}},
                    "emoji_vs15_results": {"15.0": {"pct_success": 98.0}},
                },
            },
        },
    }
    pairs = dict(fpd._build_terminal_rows(MockTerm(), data))
    assert "ghostty" in pairs["Software"]
    assert "173x38" in pairs["Size"]


def test_show_detail(capsys):
    term = MockTerm()

    data = {
        "terminal-probe": {
            "session-data": {
                "software_name": "ghostty",
                "session_arguments": {"stream": "stderr"},
                "height": 28, "width": 120, "ambiguous_width": 1,
                "test_results": {
                    "emoji_zwj_results": {"15.0": {"pct_success": 99.5}},
                },
            },
        },
    }
    fpd._show_detail(term, data, "terminal")
    out = capsys.readouterr().out
    assert "Terminal Probe Results" in out
    assert "ghostty" in out

    data = {
        "telnet-probe": {
            "fingerprint": "abc123",
            "fingerprint-data": {"probed-protocol": "client"},
            "session-data": {"extra": {"TERM": "xterm"}},
        },
    }
    fpd._show_detail(term, data, "telnet")
    out = capsys.readouterr().out
    assert "Telnet Probe Data" in out
    assert "abc123" in out

    fpd._show_detail(term, {}, "terminal")
    assert "(no data)" in capsys.readouterr().out


def test_fingerprinting_post_script_direct(tmp_path, capsys):
    test_file = tmp_path / "test.json"
    test_file.write_text(json.dumps({
        "telnet-probe": {
            "fingerprint-data": {"probed-protocol": "client"},
            "session-data": {"extra": {"TERM": "xterm"}},
        },
        "sessions": [],
    }))
    fps.fingerprinting_post_script(test_file)
    assert "xterm" in capsys.readouterr().out


def test_display_compact_summary_fallback():
    assert isinstance(fpd._display_compact_summary(
        {"telnet-probe": {"fingerprint": "test"}}), bool)


@pytest.mark.parametrize("data,expected", [
    ({"sessions": [{"ip": "192.168.1.1"}]}, "192.168.1.1"),
    ({"sessions": []}, "unknown"),
    ({}, "unknown"),
])
def test_client_ip(data, expected):
    assert fpd._client_ip(data) == expected


@pytest.mark.parametrize("term_type,expected", [
    ("mudlet", True),
    ("MUDLET", True),
    ("cmud", True),
    ("xterm", False),
    ("syncterm", False),
    ("vt100", False),
    ("", False),
])
def test_is_maybe_mud(term_type, expected):
    w = MockWriter(extra={"TERM": term_type})
    assert fps._is_maybe_mud(w) == expected


def test_is_maybe_mud_ttype_cycle():
    w = MockWriter(extra={"TERM": "xterm", "ttype1": "tintin++"})
    assert fps._is_maybe_mud(w) is True


def test_paginate_short_text(capsys):
    fpd._paginate(MockTerm(), "line 1\nline 2\nline 3")
    out = capsys.readouterr().out
    assert "line 1" in out and "line 3" in out
    assert "s-stop" not in out


def test_load_fingerprint_names_missing(tmp_path):
    assert fps._load_fingerprint_names(str(tmp_path)) == {}


def test_load_fingerprint_names_valid(tmp_path):
    names = {"abc123": "Ghostty", "def456": "GNU Telnet"}
    (tmp_path / "fingerprint_names.json").write_text(json.dumps(names))
    assert fps._load_fingerprint_names(str(tmp_path)) == names


def test_load_fingerprint_names_none():
    assert fps._load_fingerprint_names(None) == {}


def test_resolve_hash_name():
    names = {"abc123": "Ghostty"}
    assert fps._resolve_hash_name("abc123", names) == "Ghostty"
    assert fps._resolve_hash_name("unknown", names) == "unknown"


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


def test_build_seen_counts_unknown_terminal(tmp_path, monkeypatch):
    """Unknown terminal hash is omitted from welcome message."""
    monkeypatch.setattr(fpd, "DATA_DIR", str(tmp_path))
    folder = tmp_path / "client" / "aaa" / fps._UNKNOWN_TERMINAL_HASH
    folder.mkdir(parents=True)
    (folder / "sess1.json").write_text("{}")

    data = {
        "telnet-probe": {"fingerprint": "aaa"},
        "terminal-probe": {"fingerprint": fps._UNKNOWN_TERMINAL_HASH},
        "sessions": [{"ip": "10.0.0.1"}],
    }
    result = fpd._build_seen_counts(data)
    assert "aaa" in result
    assert fps._UNKNOWN_TERMINAL_HASH not in result
    assert "and" not in result


def test_build_seen_counts_with_names(tmp_path, monkeypatch):
    monkeypatch.setattr(fpd, "DATA_DIR", str(tmp_path))
    folder = tmp_path / "client" / "aaa" / "bbbb"
    folder.mkdir(parents=True)
    (folder / "sess1.json").write_text("{}")

    data = {
        "telnet-probe": {"fingerprint": "aaa"},
        "terminal-probe": {"fingerprint": "bbbb"},
        "sessions": [{"ip": "10.0.0.1"}],
    }
    names = {"aaa": "GNU Telnet", "bbbb": "Ghostty"}
    result = fpd._build_seen_counts(data, names)
    assert "GNU Telnet" in result
    assert "Ghostty" in result
    assert "aaa" not in result
    assert "bbbb" not in result


def test_build_seen_counts_no_names(tmp_path, monkeypatch):
    monkeypatch.setattr(fpd, "DATA_DIR", str(tmp_path))
    folder = tmp_path / "client" / "aaa" / "bbbb"
    folder.mkdir(parents=True)
    (folder / "sess1.json").write_text("{}")

    data = {
        "telnet-probe": {"fingerprint": "aaa"},
        "terminal-probe": {"fingerprint": "bbbb"},
        "sessions": [{"ip": "10.0.0.1"}],
    }
    result = fpd._build_seen_counts(data)
    assert "aaa" in result
    assert "bbbb" in result


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


def test_prompt_no_revision_on_return(tmp_path, monkeypatch):
    filepath = tmp_path / "test.json"
    data = {
        "telnet-probe": {"fingerprint": "aaa"},
        "terminal-probe": {"fingerprint": "bbbb"},
        "sessions": [],
    }
    filepath.write_text(json.dumps(data))

    monkeypatch.setattr(fpd, "_cooked_input", lambda prompt: "")
    names = {"aaa": "GNU Telnet", "bbbb": "Ghostty"}
    fpd._prompt_fingerprint_identification(
        MockTerm(), data, str(filepath), names
    )
    assert "terminal-emulator-revision" not in data.get("suggestions", {})
    assert "telnet-client-revision" not in data.get("suggestions", {})


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
    assert saved["suggestions"]["telnet-client-revision"] == "inetutils-2.5"


def test_prompt_revision_same_name_ignored(tmp_path, monkeypatch):
    filepath = tmp_path / "test.json"
    data = {
        "telnet-probe": {"fingerprint": "aaa"},
        "terminal-probe": {"fingerprint": "bbbb"},
        "sessions": [],
    }
    filepath.write_text(json.dumps(data))

    inputs = iter(["Ghostty", "GNU Telnet"])
    monkeypatch.setattr(fpd, "_cooked_input", lambda prompt: next(inputs))
    names = {"aaa": "GNU Telnet", "bbbb": "Ghostty"}
    fpd._prompt_fingerprint_identification(
        MockTerm(), data, str(filepath), names
    )
    assert "terminal-emulator-revision" not in data.get("suggestions", {})
    assert "telnet-client-revision" not in data.get("suggestions", {})


def test_prompt_skip_empty_input(tmp_path, monkeypatch):
    filepath = tmp_path / "test.json"
    data = {
        "telnet-probe": {"fingerprint": "aaa"},
        "sessions": [],
    }
    filepath.write_text(json.dumps(data))

    monkeypatch.setattr(fpd, "_cooked_input", lambda prompt: "")

    fpd._prompt_fingerprint_identification(
        MockTerm(), data, filepath, {}
    )
    assert "suggestions" not in data


def test_prompt_skips_unknown_terminal_hash(tmp_path, monkeypatch):
    filepath = tmp_path / "test.json"
    data = {
        "telnet-probe": {"fingerprint": "aaa"},
        "terminal-probe": {"fingerprint": fps._UNKNOWN_TERMINAL_HASH},
        "sessions": [],
    }
    filepath.write_text(json.dumps(data))

    inputs = iter(["GNU Telnet"])
    monkeypatch.setattr(fpd, "_cooked_input", lambda prompt: next(inputs))

    fpd._prompt_fingerprint_identification(
        MockTerm(), data, filepath, {}
    )
    assert "terminal-emulator" not in data.get("suggestions", {})
    assert data["suggestions"]["telnet-client"] == "GNU Telnet"


def test_prompt_uses_software_name_default(tmp_path, monkeypatch):
    filepath = tmp_path / "test.json"
    data = {
        "telnet-probe": {"fingerprint": "aaa"},
        "terminal-probe": {
            "fingerprint": "bbbb",
            "session-data": {"software_name": "ghostty"},
        },
        "sessions": [],
    }
    filepath.write_text(json.dumps(data))

    prompts = []

    def mock_input(prompt):
        prompts.append(prompt)
        return ""

    monkeypatch.setattr(fpd, "_cooked_input", mock_input)

    fpd._prompt_fingerprint_identification(
        MockTerm(), data, filepath, {}
    )
    assert 'press return for "ghostty"' in prompts[0]
    assert data["suggestions"]["terminal-emulator"] == "ghostty"


def test_setup_term_environ_truecolor(monkeypatch):
    monkeypatch.delenv("COLORTERM", raising=False)
    data = {
        "terminal-probe": {"session-data": {
            "terminal_results": {"number_of_colors": 16777216},
        }},
    }
    fpd._setup_term_environ(data)
    import os
    assert os.environ["COLORTERM"] == "truecolor"


def test_setup_term_environ_removes_stale(monkeypatch):
    monkeypatch.setenv("COLORTERM", "stale-value")
    data = {
        "terminal-probe": {"session-data": {
            "terminal_results": {"number_of_colors": 256},
        }},
    }
    fpd._setup_term_environ(data)
    import os
    assert "COLORTERM" not in os.environ


def test_setup_term_environ_empty_data(monkeypatch):
    monkeypatch.setenv("COLORTERM", "stale")
    fpd._setup_term_environ({})
    import os
    assert "COLORTERM" not in os.environ


def test_build_database_entries(tmp_path, monkeypatch):
    monkeypatch.setattr(fpd, "DATA_DIR", str(tmp_path))
    for telnet_h, terminal_h, n_files in [
        ("aaa", "xxx", 3),
        ("aaa", "yyy", 1),
        ("bbb", "xxx", 2),
    ]:
        d = tmp_path / "client" / telnet_h / terminal_h
        d.mkdir(parents=True, exist_ok=True)
        for i in range(n_files):
            (d / f"sess{i}.json").write_text("{}")

    entries = fpd._build_database_entries()
    types = {e[0] for e in entries}
    assert "Telnet" in types
    assert "Terminal" in types
    assert entries[0][2] >= entries[-1][2]

    telnet_entries = {e[1]: e[2] for e in entries if e[0] == "Telnet"}
    assert telnet_entries["aaa"] == 4
    assert telnet_entries["bbb"] == 2

    terminal_entries = {e[1]: e[2] for e in entries if e[0] == "Terminal"}
    assert terminal_entries["xxx"] == 5
    assert terminal_entries["yyy"] == 1


def test_build_database_entries_skips_unknown_terminal(tmp_path, monkeypatch):
    """Unknown terminal hash directories are excluded from database entries."""
    monkeypatch.setattr(fpd, "DATA_DIR", str(tmp_path))
    populated = tmp_path / "client" / "aaa" / "xxx"
    populated.mkdir(parents=True)
    (populated / "sess.json").write_text("{}")
    unknown = tmp_path / "client" / "aaa" / fps._UNKNOWN_TERMINAL_HASH
    unknown.mkdir(parents=True)
    (unknown / "sess.json").write_text("{}")

    entries = fpd._build_database_entries()
    terminal_entries = [e for e in entries if e[0] == "Terminal"]
    assert len(terminal_entries) == 1
    assert terminal_entries[0][1] == "xxx"


def test_build_database_entries_with_names(tmp_path, monkeypatch):
    monkeypatch.setattr(fpd, "DATA_DIR", str(tmp_path))
    d = tmp_path / "client" / "aaa" / "xxx"
    d.mkdir(parents=True)
    (d / "sess.json").write_text("{}")

    names = {"aaa": "PuTTY", "xxx": "xterm"}
    entries = fpd._build_database_entries(names)
    display_names = {e[1] for e in entries}
    assert "PuTTY" in display_names
    assert "xterm" in display_names


def test_build_database_entries_unknown_terminal(tmp_path, monkeypatch):
    """Unknown terminal hash is excluded from database entries."""
    monkeypatch.setattr(fpd, "DATA_DIR", str(tmp_path))
    d = tmp_path / "client" / "aaa" / fps._UNKNOWN_TERMINAL_HASH
    d.mkdir(parents=True)
    (d / "sess.json").write_text("{}")

    entries = fpd._build_database_entries()
    terminal_entries = [e for e in entries if e[0] == "Terminal"]
    assert len(terminal_entries) == 0


def test_build_database_entries_no_data(tmp_path, monkeypatch):
    monkeypatch.setattr(fpd, "DATA_DIR", str(tmp_path))
    assert fpd._build_database_entries() == []


def test_build_database_entries_no_datadir(monkeypatch):
    monkeypatch.setattr(fpd, "DATA_DIR", None)
    assert fpd._build_database_entries() == []


def test_show_database_empty(capsys):
    fpd._show_database(MockTerm(), {}, [])
    assert "No fingerprints" in capsys.readouterr().out


def test_collect_slc_tab_with_linemode():
    from telnetlib3 import slc

    w = MockWriter()
    w.remote_option[fps.LINEMODE] = True
    w.slctab = slc.generate_slctab(slc.BSD_SLC_TAB)
    w.slctab[slc.SLC_EC] = slc.SLC(slc.SLC_VARIABLE, b"\x08")

    result = fps._collect_slc_tab(w)
    assert "set" in result
    assert result["set"]["SLC_EC"] == 0x08


def test_collect_slc_tab_empty_without_linemode():
    from telnetlib3 import slc

    w = MockWriter()
    w.remote_option[fps.LINEMODE] = False
    w.slctab = slc.generate_slctab(slc.BSD_SLC_TAB)
    w.slctab[slc.SLC_EC] = slc.SLC(slc.SLC_VARIABLE, b"\x08")

    assert fps._collect_slc_tab(w) == {}


def test_protocol_fingerprint_includes_slc():
    from telnetlib3 import slc

    probe = {"LINEMODE": {"status": "WILL", "opt": fps.LINEMODE}}
    w = MockWriter()
    w.remote_option[fps.LINEMODE] = True
    w.slctab = slc.generate_slctab(slc.BSD_SLC_TAB)
    w.slctab[slc.SLC_EC] = slc.SLC(slc.SLC_VARIABLE, b"\x08")

    fp = fps._create_protocol_fingerprint(w, probe)
    assert "slc" in fp
    assert fp["slc"]["set"]["SLC_EC"] == 0x08


def test_protocol_fingerprint_no_slc_without_linemode():
    probe = {"BINARY": {"status": "WILL", "opt": fps.BINARY}}
    fp = fps._create_protocol_fingerprint(MockWriter(), probe)
    assert "slc" not in fp


def test_protocol_fingerprint_hash_differs_with_slc():
    from telnetlib3 import slc

    probe = {"LINEMODE": {"status": "WILL", "opt": fps.LINEMODE}}
    w1 = MockWriter()
    w1.remote_option[fps.LINEMODE] = True
    w1.slctab = slc.generate_slctab(slc.BSD_SLC_TAB)

    w2 = MockWriter()
    w2.remote_option[fps.LINEMODE] = True
    w2.slctab = slc.generate_slctab(slc.BSD_SLC_TAB)
    w2.slctab[slc.SLC_EC] = slc.SLC(slc.SLC_VARIABLE, b"\x08")

    h1 = fps._hash_fingerprint(fps._create_protocol_fingerprint(w1, probe))
    h2 = fps._hash_fingerprint(fps._create_protocol_fingerprint(w2, probe))
    assert h1 != h2


def test_session_fingerprint_includes_slc():
    from telnetlib3 import slc

    w = MockWriter(extra={"peername": ("127.0.0.1", 12345)})
    w.remote_option[fps.LINEMODE] = True
    w.slctab = slc.generate_slctab(slc.BSD_SLC_TAB)
    w.slctab[slc.SLC_EC] = slc.SLC(slc.SLC_VARIABLE, b"\x08")

    probe = {"LINEMODE": {"status": "WILL", "opt": fps.LINEMODE}}
    session = fps._build_session_fingerprint(w, probe, 0.5)
    assert "slc_tab" in session
    assert session["slc_tab"]["set"]["SLC_EC"] == 0x08


def test_apply_unicode_borders():
    from prettytable import PrettyTable
    tbl = PrettyTable()
    fpd._apply_unicode_borders(tbl)
    assert tbl.horizontal_char == "\u2550"
    assert tbl.vertical_char == "\u2551"
    assert tbl.junction_char == "\u256c"
