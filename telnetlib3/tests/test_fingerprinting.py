# std imports
import os
import sys
import copy
import json
import asyncio
import subprocess
from pathlib import Path

# 3rd party
import pytest

# local
from telnetlib3 import slc
from telnetlib3 import fingerprinting as fps

if sys.platform != "win32":
    from telnetlib3 import server_pty_shell
    from telnetlib3 import fingerprinting_display as fpd
else:
    server_pty_shell = None  # type: ignore[assignment]

# local
from telnetlib3.tests.accessories import (  # noqa: F401
    bind_host,
    create_server,
    open_connection,
    unused_tcp_port,
)


@pytest.fixture(autouse=True)
def _fast_probe_timeout(monkeypatch):
    """Reduce probe timeout for fast tests."""
    monkeypatch.setattr(fps, "_PROBE_TIMEOUT", 0.01)


requires_unix = pytest.mark.skipif(sys.platform == "win32", reason="requires termios (Unix only)")

_BINARY_PROBE = {"BINARY": {"status": "WILL", "opt": fps.BINARY}}


async def _noop(_):
    pass


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
        self._connect_time = None


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
        self.slctab = None
        self.comport_data = None
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


def _probe_writer(peername=("10.0.0.1", 9999), **extra):
    w = MockWriter(extra={"peername": peername, **extra})
    w._protocol = MockProtocol({})
    return w


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
    bold_magenta = ""

    def magenta(self, s):
        return s

    def cyan(self, s):
        return s

    def clear_eol(self):
        return ""


@pytest.mark.asyncio
async def test_probe_client_capabilities():
    options = [(fps.BINARY, "BINARY", ""), (fps.SGA, "SGA", "")]
    writer = MockWriter(will_options=[fps.BINARY], wont_options=[fps.SGA])
    results = await fps.probe_client_capabilities(writer, options=options, timeout=0.001)
    assert results["BINARY"]["status"] == "WILL"
    assert results["SGA"]["status"] == "WONT"


def test_save_fingerprint_data(tmp_path, monkeypatch):
    monkeypatch.setattr(fps, "DATA_DIR", str(tmp_path))
    writer = MockWriter(extra={"peername": ("10.0.0.1", 9999), "TERM": "xterm"})
    writer._protocol = MockProtocol({"TERM": "xterm", "ttype1": "xterm", "ttype2": "xterm-256"})
    probe = {
        "BINARY": {"status": "WILL", "opt": fps.BINARY},
        "SGA": {"status": "WONT", "opt": fps.SGA},
    }
    filepath = fps._save_fingerprint_data(writer, probe, 0.5)
    assert filepath is not None and Path(filepath).exists()

    with open(filepath, encoding="utf-8") as f:
        data = json.load(f)
    tp = data["telnet-probe"]
    assert tp["fingerprint-data"]["probed-protocol"] == "client"
    assert "BINARY" in tp["fingerprint-data"]["supported-options"]
    assert tp["session_data"]["ttype_cycle"] == ["xterm", "xterm-256"]
    assert "peername" not in tp["session_data"]["extra"]
    assert data["sessions"][0]["ip"] == "10.0.0.1"
    assert Path(filepath).parent.name == fps._UNKNOWN_TERMINAL_HASH

    monkeypatch.setattr(fps, "DATA_DIR", None)
    assert fps._save_fingerprint_data(writer, {}, 0.5) is None


def test_save_fingerprint_appends_session(tmp_path, monkeypatch):
    monkeypatch.setattr(fps, "DATA_DIR", str(tmp_path))
    writer = MockWriter(extra={"peername": ("10.0.0.1", 9999), "TERM": "xterm"})
    writer._protocol = MockProtocol({"TERM": "xterm"})

    fp1 = fps._save_fingerprint_data(writer, _BINARY_PROBE, 0.5)
    fp2 = fps._save_fingerprint_data(writer, _BINARY_PROBE, 0.5)
    assert fp1 == fp2
    with open(fp2, encoding="utf-8") as f:
        assert len(json.load(f)["sessions"]) == 2


def test_protocol_fingerprint():
    w = MockWriter(extra={"TERM": "xterm", "HOME": "/home/user"})
    w._protocol = MockProtocol({"HOME": "/home/user"})
    assert fps._create_protocol_fingerprint(w, _BINARY_PROBE)["HOME"] == "True"

    probe2 = {
        "TTYPE": {"status": "WILL", "opt": fps.TTYPE},
        "BINARY": {"status": "WILL", "opt": fps.BINARY},
        "SGA": {"status": "WONT", "opt": fps.SGA},
    }
    fp = fps._create_protocol_fingerprint(MockWriter(), probe2)
    assert fp["supported-options"] == ["BINARY", "TTYPE"]
    assert fp["refused-options"] == ["SGA"]


def test_protocol_hash_consistency():
    w1 = MockWriter(extra={"TERM": "xterm", "HOME": "/home/alice"})
    w1._protocol = MockProtocol({"HOME": "/home/alice"})
    w2 = MockWriter(extra={"TERM": "xterm", "HOME": "/home/bob"})
    w2._protocol = MockProtocol({"HOME": "/home/bob"})

    h1 = fps._hash_fingerprint(fps._create_protocol_fingerprint(w1, _BINARY_PROBE))
    h2 = fps._hash_fingerprint(fps._create_protocol_fingerprint(w2, _BINARY_PROBE))
    assert h1 == h2 and len(h1) == 16


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Ghostty", "Ghostty"),
        ("  Ghostty  ", "Ghostty"),
        ("", None),
        ("   ", None),
        ("bad\x00name", None),
        ("bad\x1bname", None),
        ("bad\x7fname", None),
        ("good name 123", "good name 123"),
    ],
)
def test_validate_suggestion(text, expected):
    assert fps._validate_suggestion(text) == expected


@requires_unix
def test_prompt_stores_suggestions(tmp_path, monkeypatch, capsys):
    filepath = tmp_path / "test.json"
    data = {
        "telnet-probe": {"fingerprint": "aaa"},
        "terminal-probe": {"fingerprint": "bbbb"},
        "sessions": [],
    }
    filepath.write_text(json.dumps(data))

    inputs = iter(["Ghostty", "GNU Telnet"])
    # pylint: disable=possibly-used-before-assignment
    monkeypatch.setattr(fpd, "_cooked_input", lambda prompt: next(inputs))
    fpd._prompt_fingerprint_identification(MockTerm(), data, str(filepath), {})
    assert data["suggestions"]["terminal-emulator"] == "Ghostty"
    assert data["suggestions"]["telnet-client"] == "GNU Telnet"
    assert "Help our database!" in capsys.readouterr().out

    with open(filepath, encoding="utf-8") as f:
        assert json.load(f)["suggestions"]["terminal-emulator"] == "Ghostty"


@requires_unix
def test_prompt_stores_revision(tmp_path, monkeypatch, capsys):
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
    fpd._prompt_fingerprint_identification(MockTerm(), data, str(filepath), names)
    assert data["suggestions"]["terminal-emulator-revision"] == "Ghostty2"
    assert data["suggestions"]["telnet-client-revision"] == "inetutils-2.5"
    captured = capsys.readouterr()
    assert "Suggest a revision" in captured.out
    assert "Your submission is under review." in captured.out


@requires_unix
@pytest.mark.asyncio
async def test_server_shell(monkeypatch):
    monkeypatch.setattr(fps.asyncio, "sleep", _noop)
    monkeypatch.setattr(fps, "DATA_DIR", None)

    writer = MockWriter(
        extra={"peername": ("127.0.0.1", 12345), "TERM": "xterm"}, will_options=[fps.BINARY]
    )
    await fps.fingerprinting_server_shell(MockReader([]), writer)
    assert writer._closing


@requires_unix
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
            "device_attributes": {"service_class": 62, "extensions": [22, 4]},
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
            "xtgettcap": {"supported": True, "capabilities": {"TN": "foot", "Co": "256"}},
            "foreground_color_hex": "#ffffffffffff",
            "cell_width": 6,
            "cell_height": 16,
            "width": 170,
            "height": 46,
        },
        "test_results": {
            "unicode_wide_results": {
                "17.0.0": {"n_errors": 0, "n_total": 10, "pct_success": 100.0, "cps": 8.7}
            },
            "emoji_vs16_results": {
                "9.0.0": {"n_errors": 2, "n_total": 12, "pct_success": 83.3, "cps": 9.2}
            },
            "language_results": None,
        },
    }

    fp = fpd._create_terminal_fingerprint(terminal_data)
    assert fp["software_name"] == "foot" and fp["software_version"] == "1.16.2"
    assert fp["number_of_colors"] == 16777216 and fp["sixel"] is True
    assert fp["kitty_graphics"] is False and fp["kitty_clipboard_protocol"] is False
    assert fp["iterm2_features"] == {"supported": False, "features": {}}
    assert fp["kitty_keyboard"]["disambiguate"] is False
    assert fp["kitty_notifications"] is False and fp["kitty_pointer_shapes"] is False
    assert fp["text_sizing"] == {"width": False, "scale": False}
    assert fp["da_service_class"] == 62 and fp["da_extensions"] == [4, 22]
    assert fp["ambiguous_width"] == 1
    assert fp["modes"]["2027"] == {
        "supported": True,
        "changeable": True,
        "enabled": True,
        "value": 1,
    }
    assert fp["modes"]["5522"]["supported"] is False
    assert "mode_name" not in fp["modes"]["2027"]
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
    for key in ("foreground_color_hex", "cell_width", "width", "height"):
        assert key not in fp


@requires_unix
def test_terminal_fingerprint_hash_excludes_session_vars():
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

    async with create_server(
        host=bind_host,
        port=unused_tcp_port,
        shell=fps.fingerprinting_server_shell,
        connect_maxwait=0.5,
    ):
        async with open_connection(
            host=bind_host, port=unused_tcp_port, connect_minwait=0.2, connect_maxwait=0.5
        ) as (reader, writer):
            try:
                await asyncio.wait_for(reader.read(100), timeout=1.0)
            except asyncio.TimeoutError:
                pass


@pytest.mark.parametrize(
    "ttype1,ttype2,expected",
    [
        ("ANSI", "VT100", True),
        ("ANSI", "", True),
        ("ANSI", None, True),
        ("ansi", "vt100", True),
        ("xterm", "xterm-256color", False),
        ("ANSI", "xterm", False),
        ("VT100", "ANSI", False),
        ("TINTIN++", "xterm-ghostty", False),
    ],
)
def test_is_maybe_ms_telnet(ttype1, ttype2, expected):
    extra = {"peername": ("127.0.0.1", 12345)}
    if ttype1 is not None:
        extra["ttype1"] = ttype1
    if ttype2 is not None:
        extra["ttype2"] = ttype2
    assert fps._is_maybe_ms_telnet(MockWriter(extra=extra)) is expected


@pytest.mark.asyncio
async def test_run_probe_ms_telnet_reduced():

    writer = MockWriter(
        extra={"peername": ("127.0.0.1", 12345), "ttype1": "ANSI", "ttype2": "VT100"},
        wont_options=[fps.BINARY, fps.SGA],
    )
    results, elapsed = await fps._run_probe(writer, verbose=False)
    probed_names = set(results.keys())
    legacy_names = {name for _, name, _ in fps.LEGACY_OPTIONS}
    assert not probed_names.intersection(legacy_names)
    assert "NEW_ENVIRON" not in probed_names


@pytest.mark.asyncio
async def test_run_probe_normal_client_full():

    writer = MockWriter(
        extra={"peername": ("127.0.0.1", 12345), "ttype1": "xterm", "ttype2": "xterm-256color"},
        wont_options=[fps.BINARY, fps.SGA],
    )
    results, elapsed = await fps._run_probe(writer, verbose=False)
    probed_names = set(results.keys())
    legacy_names = {name for _, name, _ in fps.LEGACY_OPTIONS}
    assert probed_names.issuperset(legacy_names) and "NEW_ENVIRON" in probed_names


def _make_ttype_data(ttype_cycle):
    return {"telnet-probe": {"session_data": {"ttype_cycle": ttype_cycle}}}


@requires_unix
@pytest.mark.parametrize(
    "ttype_cycle,expected_term",
    [
        (["ANSI", "VT100", "VT52", "VTNT", "VTNT"], "ansi"),
        (["ANSI", "ANSI"], "xterm-256color"),
        (["xterm-256color", "xterm-256color"], "xterm-256color"),
        ([], "xterm-256color"),
    ],
)
def test_setup_term_environ_ms_telnet(ttype_cycle, expected_term, monkeypatch):
    monkeypatch.setenv("TERM", "xterm-256color")
    fpd._setup_term_environ(_make_ttype_data(ttype_cycle))
    assert os.environ["TERM"] == expected_term


@requires_unix
def test_setup_term_environ_no_ttype_cycle(monkeypatch):
    monkeypatch.setenv("TERM", "vt220")
    fpd._setup_term_environ({})
    assert os.environ["TERM"] == "vt220"


@requires_unix
@pytest.mark.parametrize(
    "probe,expected",
    [
        ({"WILL": {"SGA": 3}}, False),
        ({"WONT": {"SGA": 3}}, True),
        ({"timeout": {"SGA": 3}}, True),
        ({}, True),
    ],
)
def test_client_requires_ga(probe, expected):
    data = {"telnet-probe": {"session_data": {"probe": probe}}}
    assert fpd._client_requires_ga(data) is expected


@requires_unix
def test_client_requires_ga_missing_keys():
    assert fpd._client_requires_ga({}) is True
    assert fpd._client_requires_ga({"telnet-probe": {}}) is True


@requires_unix
def test_run_ucs_detect_timeout(monkeypatch, capsys):

    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="ucs-detect", timeout=20)

    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/ucs-detect")
    monkeypatch.setattr("subprocess.run", fake_run)
    assert fpd._run_ucs_detect() is None
    assert capsys.readouterr().out.endswith("...\r\n")


@pytest.mark.asyncio
async def test_probe_default_options():

    writer = MockWriter(wont_options=[fps.BINARY])
    results = await fps.probe_client_capabilities(writer, timeout=0.01)
    assert "BINARY" in results and len(results) == len(fps.ALL_PROBE_OPTIONS)


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
    results = await fps.probe_client_capabilities(
        writer, options=[(opt, name, "test")], timeout=0.01
    )
    assert results[name]["status"] == expected_status
    assert results[name]["already_negotiated"] is True


def test_get_client_fingerprint():
    writer = MockWriter(
        extra={
            "TERM": "xterm-256color",
            "peername": ("10.0.0.1", 5555),
            "charset": "utf-8",
            "LANG": "en_US.UTF-8",
            "ttype1": "xterm",
            "USER": "testuser",
        }
    )
    fp = fps.get_client_fingerprint(writer)
    assert fp["TERM"] == "xterm-256color" and fp["peername"] == ("10.0.0.1", 5555)
    assert fp["charset"] == "utf-8" and fp["ttype1"] == "xterm" and fp["USER"] == "testuser"

    writer2 = MockWriter(extra={"peername": None})
    writer2._extra = {"peername": None}
    assert not fps.get_client_fingerprint(writer2)


@pytest.mark.asyncio
async def test_run_probe_verbose():

    writer = MockWriter(
        extra={"peername": ("127.0.0.1", 12345), "ttype1": "xterm"}, wont_options=[fps.BINARY]
    )
    await fps._run_probe(writer, verbose=True)
    written = "".join(writer.written)
    assert "Probing" in written and "\r\x1b[K" in written


@pytest.mark.asyncio
async def test_run_probe_mud_extended():

    writer = MockWriter(
        extra={"peername": ("127.0.0.1", 12345), "TERM": "mudlet"},
        wont_options=[fps.BINARY, fps.GMCP],
    )
    results, _ = await fps._run_probe(writer, verbose=False)
    assert "GMCP" in results


@pytest.mark.parametrize(
    "input_val,expected",
    [
        pytest.param(fps.BINARY, "BINARY", id="known_binary"),
        pytest.param(fps.SGA, "SGA", id="known_sga"),
        pytest.param(b"\xfe", "0xfe", id="unknown_bytes"),
        pytest.param(42, "42", id="int"),
        pytest.param("", "", id="empty_str"),
    ],
)
def test_opt_byte_to_name(input_val, expected):
    assert fps._opt_byte_to_name(input_val) == expected


def test_collect_rejected_options_with_data():
    writer = MockWriter()
    writer.rejected_will = {fps.BINARY, fps.SGA}
    writer.rejected_do = {fps.ECHO}
    rejected = fps._collect_rejected_options(writer)
    assert len(rejected["will"]) == 2 and len(rejected["do"]) == 1


def test_collect_extra_info_tuples_and_bytes():
    writer = MockWriter(extra={"peername": ("1.2.3.4", 99)})
    writer._protocol = MockProtocol(
        {"tspeed": (38400, 38400), "raw_data": b"\x01\x02\x03", "name": "test"}
    )
    info = fps._collect_extra_info(writer)
    assert info["tspeed"] == [38400, 38400]
    assert info["raw_data"] == "010203" and info["name"] == "test"


def test_collect_extra_info_removes_duplicate_keys():
    writer = MockWriter(extra={})
    writer._protocol = MockProtocol(
        {
            "TERM": "xterm",
            "term": "xterm",
            "COLUMNS": 80,
            "cols": 80,
            "LINES": 24,
            "rows": 24,
            "ttype1": "xterm",
        }
    )
    info = fps._collect_extra_info(writer)
    for key in ("term", "cols", "rows", "ttype1"):
        assert key not in info
    assert info["TERM"] == "xterm"


def test_collect_ttype_cycle():
    writer = MockWriter(extra={"ttype1": "xterm", "ttype2": "xterm-256color", "ttype3": "vt100"})
    writer._protocol = MockProtocol(
        {"ttype1": "xterm", "ttype2": "xterm-256color", "ttype3": "vt100"}
    )
    assert fps._collect_ttype_cycle(writer) == ["xterm", "xterm-256color", "vt100"]

    writer2 = MockWriter(extra={})
    writer2._protocol = MockProtocol({})
    assert not fps._collect_ttype_cycle(writer2)


def test_collect_protocol_timing():
    writer = MockWriter()
    writer._protocol.duration = 2.5
    writer._protocol.idle = 0.3
    writer._protocol._connect_time = 1234567890.0
    timing = fps._collect_protocol_timing(writer)
    assert timing["duration"] == 2.5 and timing["idle"] == 0.3
    assert timing["connect_time"] == 1234567890.0

    writer2 = MockWriter()
    writer2._protocol = type("P", (), {})()
    assert not fps._collect_protocol_timing(writer2)


def test_collect_slc_tab_with_data():

    writer = MockWriter()
    writer.remote_option[fps.LINEMODE] = True
    tab = dict(slc.generate_slctab(slc.BSD_SLC_TAB))
    tab[slc.SLC_SYNCH] = slc.SLC(mask=slc.SLC_NOSUPPORT, value=slc.theNULL)
    tab[slc.SLC_EC] = slc.SLC(mask=slc.SLC_DEFAULT, value=slc.theNULL)
    tab[slc.SLC_IP] = slc.SLC(mask=slc.SLC_DEFAULT, value=b"\x04")
    writer.slctab = tab
    slc_tab = fps._collect_slc_tab(writer)
    assert "nosupport" in slc_tab and "unset" in slc_tab and "set" in slc_tab


def test_collect_slc_tab_empty():
    writer = MockWriter()
    writer.slctab = {"something": True}
    assert not fps._collect_slc_tab(writer)
    assert not fps._collect_slc_tab(MockWriter())


@pytest.mark.parametrize(
    "extra,expected_term,expected_encoding",
    [
        pytest.param({"LANG": "en_US.UTF-8"}, "None", "UTF-8", id="lang_encoding"),
        pytest.param({}, "None", "None", id="no_lang"),
        pytest.param({"TERM": "syncterm"}, "Syncterm", "None", id="term_syncterm"),
        pytest.param({"TERM": "ansi-color"}, "Yes-ansi", "None", id="term_ansi"),
        pytest.param({"TERM": "xterm"}, "Yes", "None", id="term_normal"),
    ],
)
def test_create_protocol_fingerprint_term_encoding(extra, expected_term, expected_encoding):
    writer = MockWriter(extra=extra)
    writer._protocol = MockProtocol({})
    fp = fps._create_protocol_fingerprint(writer, {})
    assert fp["TERM"] == expected_term and fp["encoding"] == expected_encoding


def test_create_protocol_fingerprint_with_rejected_options():
    writer = MockWriter(extra={"TERM": "xterm"})
    writer._protocol = MockProtocol({})
    writer.rejected_will = {fps.BINARY}
    writer.rejected_do = {fps.ECHO}
    fp = fps._create_protocol_fingerprint(writer, {})
    assert "rejected-will" in fp and "rejected-do" in fp


def test_create_protocol_fingerprint_with_linemode_slc():

    writer = MockWriter(extra={"TERM": "xterm"})
    writer._protocol = MockProtocol({})
    writer.remote_option[fps.LINEMODE] = True
    tab = dict(slc.generate_slctab(slc.BSD_SLC_TAB))
    tab[slc.SLC_IP] = slc.SLC(mask=slc.SLC_DEFAULT, value=b"\x04")
    writer.slctab = tab
    probe = {"LINEMODE": {"status": "WILL", "opt": fps.LINEMODE}}
    assert "slc" in fps._create_protocol_fingerprint(writer, probe)


def test_count_protocol_folder_files(tmp_path):
    assert fps._count_protocol_folder_files(str(tmp_path / "nonexistent")) == 0
    (tmp_path / "a.json").write_text("{}")
    (tmp_path / "b.json").write_text("{}")
    (tmp_path / "c.txt").write_text("nope")
    assert fps._count_protocol_folder_files(str(tmp_path)) == 2


def test_count_fingerprint_folders(tmp_path):
    assert fps._count_fingerprint_folders(data_dir=str(tmp_path)) == 0
    client_dir = tmp_path / "client"
    client_dir.mkdir()
    (client_dir / "hash1").mkdir()
    (client_dir / "hash2").mkdir()
    (client_dir / "not_a_dir.txt").write_text("")
    assert fps._count_fingerprint_folders(data_dir=str(tmp_path)) == 2
    assert fps._count_fingerprint_folders(data_dir=None) == 0


def test_create_session_fingerprint():
    writer = MockWriter(
        extra={
            "peername": ("10.0.0.1", 5555),
            "TERM": "xterm",
            "USER": "alice",
            "HOME": "/home/alice",
            "LANG": "en_US.UTF-8",
            "charset": "utf-8",
        }
    )
    fp = fps._create_session_fingerprint(writer)
    assert fp["client-ip"] == "10.0.0.1" and fp["TERM"] == "xterm"
    assert fp["USER"] == "alice" and fp["LANG"] == "en_US.UTF-8"

    writer2 = MockWriter(extra={"peername": None})
    writer2._extra = {"peername": None}
    assert not fps._create_session_fingerprint(writer2)

    assert fps._create_session_fingerprint(MockWriter(extra={"term": "vt100"}))["TERM"] == "vt100"


def test_load_fingerprint_names(tmp_path):
    names_file = tmp_path / "fingerprint_names.json"
    names_file.write_text(json.dumps({"abc123": "Ghostty", "def456": "iTerm2"}))
    assert fps._load_fingerprint_names(data_dir=str(tmp_path)) == {
        "abc123": "Ghostty",
        "def456": "iTerm2",
    }
    assert fps._load_fingerprint_names(data_dir=str(tmp_path / "nope")) == {}
    assert fps._load_fingerprint_names(data_dir=None) == {}


def test_resolve_hash_name():
    names = {"abc": "Ghostty"}
    assert fps._resolve_hash_name("abc", names) == "Ghostty"
    assert fps._resolve_hash_name("unknown", names) == "unknown"


def test_save_fingerprint_data_makedirs(tmp_path, monkeypatch):
    new_dir = str(tmp_path / "new_data")
    monkeypatch.setattr(fps, "DATA_DIR", new_dir)
    filepath = fps._save_fingerprint_data(_probe_writer(), _BINARY_PROBE, 0.5)
    assert filepath is not None and os.path.exists(filepath) and os.path.isdir(new_dir)


def test_save_fingerprint_data_max_fingerprints(tmp_path, monkeypatch):
    monkeypatch.setattr(fps, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(fps, "FINGERPRINT_MAX_FINGERPRINTS", 0)
    assert fps._save_fingerprint_data(_probe_writer(), _BINARY_PROBE, 0.5) is None


def test_save_fingerprint_data_max_files(tmp_path, monkeypatch):
    monkeypatch.setattr(fps, "DATA_DIR", str(tmp_path))
    assert fps._save_fingerprint_data(_probe_writer(), _BINARY_PROBE, 0.5) is not None

    monkeypatch.setattr(fps, "FINGERPRINT_MAX_FILES", 0)
    assert (
        fps._save_fingerprint_data(_probe_writer(peername=("10.0.0.2", 9999)), _BINARY_PROBE, 0.5)
        is None
    )


def test_save_fingerprint_data_corrupt_existing(tmp_path, monkeypatch):
    monkeypatch.setattr(fps, "DATA_DIR", str(tmp_path))
    fp1 = fps._save_fingerprint_data(_probe_writer(), _BINARY_PROBE, 0.5)
    assert fp1 is not None
    with open(fp1, "w", encoding="utf-8") as f:
        f.write("not json {{{")
    fp2 = fps._save_fingerprint_data(_probe_writer(), _BINARY_PROBE, 0.5)
    assert fp2 is not None
    with open(fp2, encoding="utf-8") as f:
        assert len(json.load(f)["sessions"]) == 1


def test_save_fingerprint_data_mkdir_oserror(tmp_path, monkeypatch):
    monkeypatch.setattr(fps, "DATA_DIR", str(tmp_path))
    original_makedirs = os.makedirs

    def failing_makedirs(path, **kwargs):
        if "client" in path and fps._UNKNOWN_TERMINAL_HASH in path:
            raise OSError("permission denied")
        return original_makedirs(path, **kwargs)

    monkeypatch.setattr(os, "makedirs", failing_makedirs)
    assert fps._save_fingerprint_data(_probe_writer(), _BINARY_PROBE, 0.5) is None


def test_save_fingerprint_data_write_oserror(tmp_path, monkeypatch):
    monkeypatch.setattr(fps, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        fps, "_atomic_json_write", lambda fp, data: (_ for _ in ()).throw(OSError("disk full"))
    )
    assert fps._save_fingerprint_data(_probe_writer(), _BINARY_PROBE, 0.5) is None


def test_save_fingerprint_data_update_oserror(tmp_path, monkeypatch):
    monkeypatch.setattr(fps, "DATA_DIR", str(tmp_path))
    assert fps._save_fingerprint_data(_probe_writer(), _BINARY_PROBE, 0.5) is not None

    monkeypatch.setattr(
        fps, "_atomic_json_write", lambda fp, data: (_ for _ in ()).throw(OSError("disk full"))
    )
    assert fps._save_fingerprint_data(_probe_writer(), _BINARY_PROBE, 0.5) is None


@pytest.mark.parametrize(
    "extra,expected",
    [
        pytest.param({"TERM": "mudlet"}, True, id="mud_term"),
        pytest.param({"TERM": "xterm", "ttype1": "ZMUD"}, True, id="mud_ttype"),
        pytest.param({"TERM": "xterm"}, False, id="not_mud"),
    ],
)
def test_is_maybe_mud(extra, expected):
    assert fps._is_maybe_mud(MockWriter(extra=extra)) is expected


@pytest.mark.parametrize(
    "opt,expected",
    [pytest.param(fps.GMCP, True, id="gmcp"), pytest.param(fps.MSDP, True, id="msdp")],
)
def test_is_maybe_mud_by_option(opt, expected):
    w = MockWriter(extra={"TERM": "xterm"})
    w.remote_option[opt] = True
    assert fps._is_maybe_mud(w) is expected


def test_build_session_fingerprint_with_slc():

    w = _probe_writer()
    w.remote_option[fps.LINEMODE] = True
    tab = dict(slc.generate_slctab(slc.BSD_SLC_TAB))
    tab[slc.SLC_IP] = slc.SLC(mask=slc.SLC_DEFAULT, value=b"\x04")
    w.slctab = tab
    probe = {"LINEMODE": {"status": "WILL", "opt": fps.LINEMODE}}
    assert "slc_tab" in fps._build_session_fingerprint(w, probe, 0.1)


def test_build_session_fingerprint_with_rejected():
    w = _probe_writer()
    w.rejected_will = {fps.BINARY}
    probe = {"BINARY": {"status": "WONT", "opt": fps.BINARY}}
    assert "rejected" in fps._build_session_fingerprint(w, probe, 0.1)


@requires_unix
@pytest.mark.asyncio
async def test_server_shell_syncterm(monkeypatch):
    monkeypatch.setattr(fps.asyncio, "sleep", _noop)
    monkeypatch.setattr(fps, "DATA_DIR", None)

    writer = MockWriter(
        extra={"peername": ("127.0.0.1", 12345), "TERM": "syncterm"}, will_options=[fps.BINARY]
    )
    await fps.fingerprinting_server_shell(MockReader([]), writer)
    assert "\x1b[0;40 D" in "".join(writer.written) and writer._closing


@requires_unix
@pytest.mark.asyncio
async def test_server_shell_with_post_script(monkeypatch, tmp_path):
    monkeypatch.setattr(fps.asyncio, "sleep", _noop)
    monkeypatch.setattr(fps, "DATA_DIR", str(tmp_path))

    pty_called = []

    async def fake_pty_shell(reader, writer, exe, args, raw_mode=False):
        pty_called.append((exe, args, raw_mode))

    monkeypatch.setattr(server_pty_shell, "pty_shell", fake_pty_shell)

    writer = MockWriter(
        extra={"peername": ("127.0.0.1", 12345), "TERM": "xterm"}, will_options=[fps.BINARY]
    )
    writer._protocol = MockProtocol({"TERM": "xterm"})
    await fps.fingerprinting_server_shell(MockReader([]), writer)
    assert len(pty_called) == 1 and pty_called[0][2] is True


@requires_unix
@pytest.mark.parametrize(
    "input_fn,expected",
    [
        pytest.param(lambda prompt: (_ for _ in ()).throw(EOFError), "", id="eof"),
        pytest.param(lambda prompt: "hello", "hello", id="normal"),
    ],
)
def test_cooked_input(monkeypatch, input_fn, expected):
    import termios

    fake_attrs = [0, 0, 0, 0, 0, 0, [b"\x00"] * 32]
    monkeypatch.setattr(termios, "tcgetattr", lambda fd: list(fake_attrs))
    monkeypatch.setattr(termios, "tcsetattr", lambda fd, when, attrs: None)
    monkeypatch.setattr("builtins.input", input_fn)
    assert fps._cooked_input("test> ") == expected


def test_atomic_json_write_bytes_values(tmp_path):
    """_atomic_json_write encodes bytes values as UTF-8 or hex."""
    filepath = str(tmp_path / "test.json")
    fps._atomic_json_write(
        filepath, {"text": b"hello", "binary": b"\x80\xff", "nested": {"val": b"\x01"}}
    )
    with open(filepath, encoding="utf-8") as f:
        data = json.load(f)
    assert data["text"] == "hello"
    assert data["binary"] == "80ff"
    assert data["nested"]["val"] == "\x01"


def test_fingerprinting_main(monkeypatch, tmp_path):
    called = []
    monkeypatch.setattr(sys, "argv", ["fingerprinting", str(tmp_path / "test.json")])
    monkeypatch.setattr(fps, "fingerprinting_post_script", called.append)
    fps.main()
    assert called == [str(tmp_path / "test.json")]


def test_fingerprinting_main_usage(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["fingerprinting"])
    with pytest.raises(SystemExit, match="1"):
        fps.main()
    assert "Usage:" in capsys.readouterr().err


@requires_unix
def test_process_client_fingerprint_skips_ucs_detect_for_mud(monkeypatch, tmp_path, capsys):
    ucs_called = []
    monkeypatch.setattr(fpd, "_run_ucs_detect", lambda: ucs_called.append(1) or None)

    data = {"telnet-probe": {"session_data": {"probe": {"WONT": {"SGA": 3}}}}}
    filepath = str(tmp_path / "test.json")
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f)

    monkeypatch.setattr(fpd, "_setup_term_environ", lambda d: None)
    monkeypatch.setattr(fpd, "_make_terminal", lambda: None)
    try:
        fpd._process_client_fingerprint(filepath, data)
    except (ImportError, AttributeError, TypeError):
        pass
    capsys.readouterr()
    assert not ucs_called


def test_protocol_fingerprint_hash_stability():
    """Hash must not change across releases for the same probe data."""
    w = MockWriter(
        extra={"TERM": "xterm", "HOME": "/home/user", "USER": "alice", "SHELL": "/bin/bash"}
    )
    w._protocol = MockProtocol({"HOME": "/home/user", "USER": "alice", "SHELL": "/bin/bash"})
    probe = {
        "BINARY": {"status": "WILL", "opt": fps.BINARY},
        "TTYPE": {"status": "WILL", "opt": fps.TTYPE},
        "SGA": {"status": "WONT", "opt": fps.SGA},
    }
    fp = fps._create_protocol_fingerprint(w, probe)
    assert fps._hash_fingerprint(fp) == "426327fe80f38c2c"


def test_fingerprinting_server_on_request_environ():
    """FingerprintingServer includes HOME and SHELL in environ request."""
    srv = fps.FingerprintingServer.__new__(fps.FingerprintingServer)
    srv._extra = {}
    env = srv.on_request_environ()
    assert "HOME" in env
    assert "SHELL" in env
    assert "USER" in env


def test_fingerprint_server_shell_has_no_protocol_factory():
    """Shell is a plain callback, not annotated with protocol_factory."""
    assert not hasattr(fps.fingerprinting_server_shell, "protocol_factory")


def test_fingerprint_server_main_exists():
    """Entry point function is importable."""
    assert callable(fps.fingerprint_server_main)


def _noop_asyncio_run(coro):
    """Discard a coroutine without running it (avoids RuntimeWarning)."""
    coro.close()


def test_fingerprint_server_main_data_dir_flag(tmp_path, monkeypatch):
    """--data-dir sets DATA_DIR and passes remaining args through."""
    data_dir = str(tmp_path / "fp-data")
    monkeypatch.setattr(sys, "argv", ["prog", "--data-dir", data_dir, "127.0.0.1", "9999"])
    monkeypatch.setattr("telnetlib3.fingerprinting.asyncio.run", _noop_asyncio_run)

    captured: dict = {}
    from telnetlib3.server import parse_server_args

    original_parse = parse_server_args

    def patched_parse() -> dict:
        result = original_parse()
        captured.update(result)
        return result

    monkeypatch.setattr("telnetlib3.server.parse_server_args", patched_parse)

    old_data_dir = fps.DATA_DIR
    try:
        fps.fingerprint_server_main()
        assert fps.DATA_DIR == data_dir
        assert captured["host"] == "127.0.0.1"
        assert captured["port"] == 9999
    finally:
        fps.DATA_DIR = old_data_dir


def test_fingerprint_server_main_env_fallback(monkeypatch):
    """DATA_DIR unchanged when --data-dir is not provided."""
    monkeypatch.setattr(sys, "argv", ["prog"])
    monkeypatch.setattr("telnetlib3.fingerprinting.asyncio.run", _noop_asyncio_run)

    old_data_dir = fps.DATA_DIR
    try:
        fps.DATA_DIR = "/original"
        fps.fingerprint_server_main()
        assert fps.DATA_DIR == "/original"
    finally:
        fps.DATA_DIR = old_data_dir


def test_bytes_safe_encoder_non_serializable():
    with pytest.raises(TypeError):
        json.dumps({"x": object()}, cls=fps._BytesSafeEncoder)


def test_fingerprinting_mixin_without_telnet_server():
    class Standalone(fps.FingerprintingTelnetServer):
        pass

    obj = Standalone()
    with pytest.raises(TypeError, match="must be combined with TelnetServer"):
        obj.on_request_environ()


def test_build_session_fingerprint_comport():
    writer = _probe_writer()
    writer.comport_data = {"signature": "COM1"}
    writer.slctab = None
    writer.rejected_will = set()
    writer.rejected_do = set()
    probe_results = {"BINARY": fps.ProbeResult(status="WILL", opt=fps.BINARY)}
    session = fps._build_session_fingerprint(writer, probe_results, 0.5)
    assert session["comport"] == {"signature": "COM1"}


def test_save_fingerprint_data_existing_non_unknown_subdir(tmp_path, monkeypatch):
    monkeypatch.setattr(fps, "DATA_DIR", str(tmp_path))

    writer = _probe_writer()
    writer.slctab = None
    writer.comport_data = None
    writer.rejected_will = set()
    writer.rejected_do = set()
    probe_results = {"BINARY": fps.ProbeResult(status="WILL", opt=fps.BINARY)}

    protocol_fp = fps._create_protocol_fingerprint(writer, probe_results)
    telnet_hash = fps._hash_fingerprint(protocol_fp)
    telnet_dir = tmp_path / "client" / telnet_hash
    known_dir = telnet_dir / "known-terminal"
    known_dir.mkdir(parents=True)

    filepath = fps._save_fingerprint_data(writer, probe_results, 0.5)
    assert filepath is not None
    assert "known-terminal" in filepath
