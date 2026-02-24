"""Tests for :mod:`telnetlib3.client_tui` data model, persistence, and command builder."""

from __future__ import annotations

# std imports
import sys
import datetime
from dataclasses import asdict

# 3rd party
import pytest

pytest.importorskip("textual", reason="textual not installed")

# local
from telnetlib3.client_tui import (  # noqa: E402
    DEFAULTS_KEY,
    SessionConfig,
    MacroEditScreen,
    TelnetSessionApp,
    AutoreplyEditScreen,
    _int_val,
    tui_main,
    _float_val,
    build_command,
    load_sessions,
    save_sessions,
    _relative_time,
    _AutoreplyTuple,
    _build_tooltips,
)


@pytest.fixture
def tui_tmp_paths(tmp_path, monkeypatch):
    monkeypatch.setattr("telnetlib3.client_tui.SESSIONS_FILE", tmp_path / "s.json")
    monkeypatch.setattr("telnetlib3.client_tui.CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr("telnetlib3.client_tui.DATA_DIR", str(tmp_path))
    return tmp_path


def test_session_config_defaults() -> None:
    cfg = SessionConfig()
    assert cfg.port == 23
    assert cfg.encoding == "utf8"
    assert cfg.mode == "auto"
    assert cfg.colormatch == "vga"
    assert cfg.speed == 38400
    assert cfg.ssl is False
    assert cfg.no_repl is False


def test_session_config_roundtrip() -> None:
    cfg = SessionConfig(
        name="test", host="example.com", port=2323, ssl=True, encoding="cp437", mode="raw"
    )
    data = asdict(cfg)
    restored = SessionConfig(**data)
    assert restored == cfg


def test_session_config_unknown_fields_ignored() -> None:
    data = asdict(SessionConfig(name="x"))
    data["unknown_future_field"] = 42
    from dataclasses import fields

    known = {f.name for f in fields(SessionConfig)}
    filtered = {k: v for k, v in data.items() if k in known}
    cfg = SessionConfig(**filtered)
    assert cfg.name == "x"


def test_persistence_save_load_roundtrip(tui_tmp_paths) -> None:
    sessions = {
        "myserver": SessionConfig(name="myserver", host="example.com", port=23),
        DEFAULTS_KEY: SessionConfig(encoding="cp437", colormatch="cga"),
    }
    save_sessions(sessions)
    loaded = load_sessions()
    assert "myserver" in loaded
    assert loaded["myserver"].host == "example.com"
    assert loaded[DEFAULTS_KEY].encoding == "cp437"
    assert loaded[DEFAULTS_KEY].colormatch == "cga"


def test_persistence_load_empty(tui_tmp_paths, monkeypatch) -> None:
    monkeypatch.setattr("telnetlib3.client_tui.SESSIONS_FILE", tui_tmp_paths / "nope.json")
    assert not load_sessions()


def test_build_command_minimal() -> None:
    cfg = SessionConfig(host="example.com", port=23)
    cmd = build_command(cfg)
    assert cmd[0] == sys.executable
    assert cmd[1] == "-c"
    assert "example.com" in cmd
    assert "23" in cmd
    assert "--ssl" not in cmd
    assert "--raw-mode" not in cmd
    assert "--line-mode" not in cmd


@pytest.mark.parametrize("mode,flag", [("raw", "--raw-mode"), ("line", "--line-mode")])
def test_build_command_mode_flags(mode: str, flag: str) -> None:
    cfg = SessionConfig(host="h", port=23, mode=mode)
    assert flag in build_command(cfg)


def test_build_command_auto_mode_no_flag() -> None:
    cfg = SessionConfig(host="h", port=23, mode="auto")
    cmd = build_command(cfg)
    assert "--raw-mode" not in cmd
    assert "--line-mode" not in cmd


@pytest.mark.parametrize(
    "cfg_kwargs,expected_flags",
    [
        ({"ssl": True, "ssl_no_verify": True, "port": 992}, ["--ssl", "--ssl-no-verify"]),
        (
            {"colormatch": "cga", "background_color": "#101010"},
            ["--colormatch", "--background-color"],
        ),
        ({"no_repl": True}, ["--no-repl"]),
        ({"connect_timeout": 5.0}, ["--connect-timeout"]),
        ({"ansi_keys": True, "ascii_eol": True}, ["--ansi-keys", "--ascii-eol"]),
    ],
)
def test_build_command_flags(
    cfg_kwargs: dict, expected_flags: list[str]  # type: ignore[type-arg]
) -> None:
    cfg = SessionConfig(host="h", port=cfg_kwargs.pop("port", 23), **cfg_kwargs)
    cmd = build_command(cfg)
    for flag in expected_flags:
        assert flag in cmd


def test_build_command_ssl_cafile() -> None:
    cfg = SessionConfig(host="h", port=992, ssl_cafile="/tmp/ca.pem")
    cmd = build_command(cfg)
    assert "--ssl-cafile" in cmd
    idx = cmd.index("--ssl-cafile")
    assert cmd[idx + 1] == "/tmp/ca.pem"


def test_build_command_encoding() -> None:
    cfg = SessionConfig(host="h", port=23, encoding="cp437")
    cmd = build_command(cfg)
    assert "--encoding" in cmd
    idx = cmd.index("--encoding")
    assert cmd[idx + 1] == "cp437"


def test_build_command_default_encoding_omitted() -> None:
    cfg = SessionConfig(host="h", port=23, encoding="utf8")
    assert "--encoding" not in build_command(cfg)


def test_build_command_always_will_do() -> None:
    cfg = SessionConfig(host="h", port=23, always_will="MXP,GMCP", always_do="MSSP")
    cmd = build_command(cfg)
    will_indices = [i for i, v in enumerate(cmd) if v == "--always-will"]
    assert len(will_indices) == 2
    assert cmd[will_indices[0] + 1] == "MXP"
    assert cmd[will_indices[1] + 1] == "GMCP"
    do_idx = cmd.index("--always-do")
    assert cmd[do_idx + 1] == "MSSP"


def test_build_command_empty_always_will_omitted() -> None:
    cfg = SessionConfig(host="h", port=23, always_will="")
    assert "--always-will" not in build_command(cfg)


def test_build_command_connect_timeout_default_omitted() -> None:
    cfg = SessionConfig(host="h", port=23, connect_timeout=10.0)
    assert "--connect-timeout" not in build_command(cfg)


def test_defaults_inheritance_new_from_defaults() -> None:
    defaults = SessionConfig(
        name=DEFAULTS_KEY, encoding="cp437", colormatch="cga", mode="raw", loglevel="debug"
    )
    new_cfg = SessionConfig(**asdict(defaults))
    new_cfg.name = "new_session"
    new_cfg.host = "example.com"
    new_cfg.last_connected = ""

    assert new_cfg.encoding == "cp437"
    assert new_cfg.colormatch == "cga"
    assert new_cfg.mode == "raw"
    assert new_cfg.loglevel == "debug"
    assert new_cfg.name == "new_session"
    assert new_cfg.host == "example.com"


def test_persistence_corrupted_json(tui_tmp_paths, monkeypatch) -> None:
    sessions_file = tui_tmp_paths / "sessions.json"
    sessions_file.write_text("{invalid json", encoding="utf-8")
    monkeypatch.setattr("telnetlib3.client_tui.SESSIONS_FILE", sessions_file)
    with pytest.raises(Exception):
        load_sessions()


def test_build_command_missing_host() -> None:
    cfg = SessionConfig(host="", port=23)
    cmd = build_command(cfg)
    assert "" in cmd


def test_macro_screen_loads_empty(tmp_path) -> None:
    path = str(tmp_path / "macros.json")
    screen = MacroEditScreen(path=path)
    assert screen._path == path
    assert screen._macros == []


def test_macro_screen_loads_file(tmp_path) -> None:
    import json

    sk = "test.host:23"
    fp = tmp_path / "macros.json"
    fp.write_text(json.dumps({sk: {"macros": [{"key": "KEY_F5", "text": "look;"}]}}))
    screen = MacroEditScreen(path=str(fp), session_key=sk)
    screen._load_from_file()
    assert len(screen._macros) == 1
    assert screen._macros[0] == ("KEY_F5", "look;", True)


def test_macro_screen_save(tmp_path) -> None:
    sk = "test.host:23"
    fp = tmp_path / "macros.json"
    screen = MacroEditScreen(path=str(fp), session_key=sk)
    screen._macros = [("KEY_F5", "look;", True), ("KEY_ALT_N", "north;", True)]
    screen._save_to_file()

    from telnetlib3.macros import load_macros

    loaded = load_macros(str(fp), sk)
    assert len(loaded) == 2
    assert loaded[0].key == "KEY_F5"
    assert loaded[0].text == "look;"
    assert loaded[1].key == "KEY_ALT_N"


def test_autoreply_screen_loads_empty(tmp_path) -> None:
    path = str(tmp_path / "autoreplies.json")
    screen = AutoreplyEditScreen(path=path)
    assert screen._path == path
    assert screen._rules == []


def test_autoreply_screen_loads_file(tmp_path) -> None:
    import json

    sk = "test.host:23"
    fp = tmp_path / "autoreplies.json"
    fp.write_text(
        json.dumps({sk: {"autoreplies": [{"pattern": r"\d+ gold", "reply": "get gold;"}]}})
    )
    screen = AutoreplyEditScreen(path=str(fp), session_key=sk)
    screen._load_from_file()
    assert len(screen._rules) == 1
    assert screen._rules[0] == _AutoreplyTuple(r"\d+ gold", "get gold;")


def test_autoreply_screen_save(tmp_path) -> None:
    sk = "test.host:23"
    fp = tmp_path / "autoreplies.json"
    screen = AutoreplyEditScreen(path=str(fp), session_key=sk)
    screen._rules = [_AutoreplyTuple(r"\d+ gold", "get gold;")]
    screen._save_to_file()

    from telnetlib3.autoreply import load_autoreplies

    loaded = load_autoreplies(str(fp), sk)
    assert len(loaded) == 1
    assert loaded[0].pattern.pattern == r"\d+ gold"
    assert loaded[0].reply == "get gold;"


@pytest.mark.parametrize(
    "entry_extra,field_idx,expected",
    [({"when": {"HP%": ">50"}}, 8, {"HP%": ">50"}), ({"immediate": True}, 9, True)],
)
def test_autoreply_screen_loads_field(tmp_path, entry_extra, field_idx, expected) -> None:
    import json

    sk = "test.host:23"
    fp = tmp_path / "autoreplies.json"
    entry = {"pattern": "x", "reply": "y;", **entry_extra}
    fp.write_text(json.dumps({sk: {"autoreplies": [entry]}}))
    screen = AutoreplyEditScreen(path=str(fp), session_key=sk)
    screen._load_from_file()
    assert screen._rules[0][field_idx] == expected


@pytest.mark.parametrize(
    "rule_kwargs,json_key,expected,absent",
    [
        ({"when": {"MP%": ">=30"}}, "when", {"MP%": ">=30"}, False),
        ({"immediate": True}, "immediate", True, False),
        ({}, "immediate", None, True),
    ],
)
def test_autoreply_screen_saves_field(tmp_path, rule_kwargs, json_key, expected, absent) -> None:
    import json

    sk = "test.host:23"
    fp = tmp_path / "autoreplies.json"
    screen = AutoreplyEditScreen(path=str(fp), session_key=sk)
    screen._rules = [_AutoreplyTuple("x", "y;", **rule_kwargs)]
    screen._save_to_file()
    raw = json.loads(fp.read_text())
    entry = raw[sk]["autoreplies"][0]
    if absent:
        assert json_key not in entry
    else:
        assert entry[json_key] == expected


def test_autoreply_screen_rejects_bad_regex(tmp_path) -> None:
    import re

    fp = tmp_path / "autoreplies.json"
    screen = AutoreplyEditScreen(path=str(fp))
    screen._rules = [_AutoreplyTuple("[invalid", "x")]
    with pytest.raises(re.error):
        screen._save_to_file()


def test_helper_relative_time_empty() -> None:
    assert not _relative_time("")


def test_helper_relative_time_invalid() -> None:
    result = _relative_time("not-a-date")
    assert result == "not-a-date"[:10]


@pytest.mark.parametrize(
    "timedelta_kwargs,expected_substr",
    [({"days": 5}, "5d ago"), ({"minutes": 10}, "10m ago"), ({"hours": 3}, "3h ago")],
)
def test_helper_relative_time(timedelta_kwargs, expected_substr) -> None:
    past = datetime.datetime.now() - datetime.timedelta(**timedelta_kwargs)
    assert expected_substr in _relative_time(past.isoformat())


def test_helper_relative_time_seconds_ago() -> None:
    past = datetime.datetime.now() - datetime.timedelta(seconds=30)
    result = _relative_time(past.isoformat())
    assert "30s ago" in result or "29s ago" in result


@pytest.mark.parametrize(
    "func,input_val,fallback,expected",
    [
        (_int_val, "42", 0, 42),
        (_int_val, "abc", 42, 42),
        (_float_val, "1.5", 0.0, 1.5),
        (_float_val, "abc", 1.5, 1.5),
    ],
)
def test_helper_val_conversion(func, input_val, fallback, expected) -> None:
    assert func(input_val, fallback) == expected


def test_helper_build_tooltips() -> None:
    tips = _build_tooltips()
    assert isinstance(tips, dict)
    assert len(tips) > 0


def test_tui_main(monkeypatch) -> None:
    called = []
    monkeypatch.setattr(TelnetSessionApp, "run", lambda self: called.append(True))
    tui_main()
    assert called
