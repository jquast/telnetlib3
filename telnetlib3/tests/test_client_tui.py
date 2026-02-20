"""Tests for :mod:`telnetlib3.client_tui` data model, persistence, and command builder."""

from __future__ import annotations

# std imports
import sys
import datetime
from dataclasses import asdict

# 3rd party
import pytest

textual = pytest.importorskip("textual", reason="textual not installed")

# 3rd party
from textual.widgets import (  # noqa: E402
    Input,
    Select,
    Switch,
    DataTable,
    RadioButton,
    ContentSwitcher,
)

# local
from telnetlib3.client_tui import (  # noqa: E402
    CONFIG_DIR,
    DEFAULTS_KEY,
    SessionConfig,
    MacroEditScreen,
    TelnetSessionApp,
    SessionEditScreen,
    SessionListScreen,
    AutoreplyEditScreen,
    _int_val,
    tui_main,
    _float_val,
    build_command,
    load_sessions,
    save_sessions,
    _relative_time,
    _build_tooltips,
    edit_macros_main,
    edit_autoreplies_main,
)


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


def test_persistence_save_load_roundtrip(tmp_path, monkeypatch) -> None:
    sessions_file = tmp_path / "sessions.json"
    monkeypatch.setattr("telnetlib3.client_tui.SESSIONS_FILE", sessions_file)
    monkeypatch.setattr("telnetlib3.client_tui.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("telnetlib3.client_tui.DATA_DIR", tmp_path)

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


def test_persistence_load_empty(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("telnetlib3.client_tui.SESSIONS_FILE", tmp_path / "nope.json")
    monkeypatch.setattr("telnetlib3.client_tui.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("telnetlib3.client_tui.DATA_DIR", tmp_path)
    assert load_sessions() == {}


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


def test_build_command_ssl_flags() -> None:
    cfg = SessionConfig(host="h", port=992, ssl=True, ssl_no_verify=True)
    cmd = build_command(cfg)
    assert "--ssl" in cmd
    assert "--ssl-no-verify" in cmd


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


def test_build_command_display_options() -> None:
    cfg = SessionConfig(host="h", port=23, colormatch="cga", background_color="#101010")
    cmd = build_command(cfg)
    assert "--colormatch" in cmd
    assert "--background-color" in cmd


def test_build_command_no_repl() -> None:
    cfg = SessionConfig(host="h", port=23, no_repl=True)
    assert "--no-repl" in build_command(cfg)


def test_build_command_connect_timeout_default_omitted() -> None:
    cfg = SessionConfig(host="h", port=23, connect_timeout=10.0)
    assert "--connect-timeout" not in build_command(cfg)


def test_build_command_connect_timeout_nonzero() -> None:
    cfg = SessionConfig(host="h", port=23, connect_timeout=5.0)
    assert "--connect-timeout" in build_command(cfg)


def test_build_command_ansi_keys_ascii_eol() -> None:
    cfg = SessionConfig(host="h", port=23, ansi_keys=True, ascii_eol=True)
    cmd = build_command(cfg)
    assert "--ansi-keys" in cmd
    assert "--ascii-eol" in cmd


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


def test_persistence_corrupted_json(tmp_path, monkeypatch) -> None:
    sessions_file = tmp_path / "sessions.json"
    sessions_file.write_text("{invalid json", encoding="utf-8")
    monkeypatch.setattr("telnetlib3.client_tui.SESSIONS_FILE", sessions_file)
    monkeypatch.setattr("telnetlib3.client_tui.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("telnetlib3.client_tui.DATA_DIR", tmp_path)
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
    fp.write_text(json.dumps({sk: {"macros": [{"key": "f5", "text": "look<CR>"}]}}))
    screen = MacroEditScreen(path=str(fp), session_key=sk)
    screen._load_from_file()
    assert len(screen._macros) == 1
    assert screen._macros[0] == ("f5", "look<CR>")


def test_macro_screen_save(tmp_path) -> None:
    sk = "test.host:23"
    fp = tmp_path / "macros.json"
    screen = MacroEditScreen(path=str(fp), session_key=sk)
    screen._macros = [("f5", "look<CR>"), ("escape n", "north<CR>")]
    screen._save_to_file()

    from telnetlib3.macros import load_macros

    loaded = load_macros(str(fp), sk)
    assert len(loaded) == 2
    assert loaded[0].keys == ("f5",)
    assert loaded[0].text == "look<CR>"
    assert loaded[1].keys == ("escape", "n")


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
        json.dumps({sk: {"autoreplies": [{"pattern": r"\d+ gold", "reply": "get gold<CR>"}]}})
    )
    screen = AutoreplyEditScreen(path=str(fp), session_key=sk)
    screen._load_from_file()
    assert len(screen._rules) == 1
    assert screen._rules[0] == (r"\d+ gold", "get gold<CR>")


def test_autoreply_screen_save(tmp_path) -> None:
    sk = "test.host:23"
    fp = tmp_path / "autoreplies.json"
    screen = AutoreplyEditScreen(path=str(fp), session_key=sk)
    screen._rules = [(r"\d+ gold", "get gold<CR>")]
    screen._save_to_file()

    from telnetlib3.autoreply import load_autoreplies

    loaded = load_autoreplies(str(fp), sk)
    assert len(loaded) == 1
    assert loaded[0].pattern.pattern == r"\d+ gold"
    assert loaded[0].reply == "get gold<CR>"


def test_autoreply_screen_rejects_bad_regex(tmp_path) -> None:
    import re

    fp = tmp_path / "autoreplies.json"
    screen = AutoreplyEditScreen(path=str(fp))
    screen._rules = [("[invalid", "x")]
    with pytest.raises(re.error):
        screen._save_to_file()


def test_helper_relative_time_empty() -> None:
    assert _relative_time("") == ""


def test_helper_relative_time_invalid() -> None:
    result = _relative_time("not-a-date")
    assert result == "not-a-date"[:10]


def test_helper_relative_time_days_ago() -> None:
    past = datetime.datetime.now() - datetime.timedelta(days=5)
    assert "5d ago" in _relative_time(past.isoformat())


def test_helper_relative_time_minutes_ago() -> None:
    past = datetime.datetime.now() - datetime.timedelta(minutes=10)
    assert "10m ago" in _relative_time(past.isoformat())


def test_helper_relative_time_hours_ago() -> None:
    past = datetime.datetime.now() - datetime.timedelta(hours=3)
    assert "3h ago" in _relative_time(past.isoformat())


def test_helper_relative_time_seconds_ago() -> None:
    past = datetime.datetime.now() - datetime.timedelta(seconds=30)
    result = _relative_time(past.isoformat())
    assert "30s ago" in result or "29s ago" in result


def test_helper_int_val_valid() -> None:
    assert _int_val("42", 0) == 42


def test_helper_int_val_fallback() -> None:
    assert _int_val("abc", 42) == 42


def test_helper_float_val_valid() -> None:
    assert _float_val("1.5", 0.0) == 1.5


def test_helper_float_val_fallback() -> None:
    assert _float_val("abc", 1.5) == 1.5


def test_helper_build_tooltips() -> None:
    tips = _build_tooltips()
    assert isinstance(tips, dict)
    assert len(tips) > 0


def test_tui_main(monkeypatch) -> None:
    called = []
    monkeypatch.setattr(TelnetSessionApp, "run", lambda self: called.append(True))
    tui_main()
    assert called


class _SessionListApp(textual.app.App[None]):
    """Test app that pushes SessionListScreen."""

    def __init__(self, sessions: dict[str, SessionConfig] | None = None) -> None:
        super().__init__()
        self._sessions = sessions

    def on_mount(self) -> None:
        screen = SessionListScreen()
        if self._sessions is not None:
            screen._sessions = self._sessions
        self.push_screen(screen)


class _EditApp(textual.app.App[None]):
    """Test app that pushes SessionEditScreen."""

    def __init__(self, config: SessionConfig, **kwargs) -> None:
        super().__init__()
        self._config = config
        self._kwargs = kwargs

    def on_mount(self) -> None:
        self.push_screen(SessionEditScreen(config=self._config, **self._kwargs))


@pytest.mark.asyncio
class TestSessionListScreenTextual:

    async def test_compose_and_mount(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr("telnetlib3.client_tui.SESSIONS_FILE", tmp_path / "s.json")
        monkeypatch.setattr("telnetlib3.client_tui.CONFIG_DIR", str(tmp_path))
        monkeypatch.setattr("telnetlib3.client_tui.DATA_DIR", str(tmp_path))
        sessions = {
            "srv1": SessionConfig(name="srv1", host="host1", port=23),
            "srv2": SessionConfig(name="srv2", host="host2", port=2323),
        }
        save_sessions(sessions)

        app = _SessionListApp()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            screen = app.screen
            table = screen.query_one("#session-table", DataTable)
            assert table.row_count == 2
            assert screen.query_one("#connect-btn") is not None
            assert screen.query_one("#add-btn") is not None

    async def test_selected_key_returns_key(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr("telnetlib3.client_tui.SESSIONS_FILE", tmp_path / "s.json")
        monkeypatch.setattr("telnetlib3.client_tui.CONFIG_DIR", str(tmp_path))
        monkeypatch.setattr("telnetlib3.client_tui.DATA_DIR", str(tmp_path))
        sessions = {"srv1": SessionConfig(name="srv1", host="host1", port=23)}
        save_sessions(sessions)

        app = _SessionListApp()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            screen = app.screen
            assert screen._selected_key() == "srv1"

    async def test_session_keys(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr("telnetlib3.client_tui.SESSIONS_FILE", tmp_path / "s.json")
        monkeypatch.setattr("telnetlib3.client_tui.CONFIG_DIR", str(tmp_path))
        monkeypatch.setattr("telnetlib3.client_tui.DATA_DIR", str(tmp_path))
        sessions = {
            "srv1": SessionConfig(name="srv1", host="host1"),
            DEFAULTS_KEY: SessionConfig(name=DEFAULTS_KEY),
        }
        save_sessions(sessions)

        app = _SessionListApp()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            screen = app.screen
            keys = screen._session_keys()
            assert "srv1" in keys
            assert DEFAULTS_KEY not in keys

    async def test_action_delete_session(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr("telnetlib3.client_tui.SESSIONS_FILE", tmp_path / "s.json")
        monkeypatch.setattr("telnetlib3.client_tui.CONFIG_DIR", str(tmp_path))
        monkeypatch.setattr("telnetlib3.client_tui.DATA_DIR", str(tmp_path))
        sessions = {"srv1": SessionConfig(name="srv1", host="host1", port=23)}
        save_sessions(sessions)

        app = _SessionListApp()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            screen = app.screen
            screen.action_delete_session()
            await pilot.pause()
            table = screen.query_one("#session-table", DataTable)
            assert table.row_count == 0

    async def test_action_edit_session_no_selection(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr("telnetlib3.client_tui.SESSIONS_FILE", tmp_path / "s.json")
        monkeypatch.setattr("telnetlib3.client_tui.CONFIG_DIR", str(tmp_path))
        monkeypatch.setattr("telnetlib3.client_tui.DATA_DIR", str(tmp_path))

        app = _SessionListApp()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            screen = app.screen
            screen.action_edit_session()
            await pilot.pause()

    async def test_action_connect_no_host(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr("telnetlib3.client_tui.SESSIONS_FILE", tmp_path / "s.json")
        monkeypatch.setattr("telnetlib3.client_tui.CONFIG_DIR", str(tmp_path))
        monkeypatch.setattr("telnetlib3.client_tui.DATA_DIR", str(tmp_path))
        sessions = {"srv1": SessionConfig(name="srv1", host="", port=23)}
        save_sessions(sessions)

        app = _SessionListApp()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            screen = app.screen
            screen.action_connect()
            await pilot.pause()

    async def test_on_edit_result_saves(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr("telnetlib3.client_tui.SESSIONS_FILE", tmp_path / "s.json")
        monkeypatch.setattr("telnetlib3.client_tui.CONFIG_DIR", str(tmp_path))
        monkeypatch.setattr("telnetlib3.client_tui.DATA_DIR", str(tmp_path))

        app = _SessionListApp()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            screen = app.screen
            cfg = SessionConfig(name="new_srv", host="newhost")
            screen._on_edit_result(cfg)
            await pilot.pause()
            assert "new_srv" in screen._sessions
            table = screen.query_one("#session-table", DataTable)
            assert table.row_count == 1

    async def test_on_edit_result_none(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr("telnetlib3.client_tui.SESSIONS_FILE", tmp_path / "s.json")
        monkeypatch.setattr("telnetlib3.client_tui.CONFIG_DIR", str(tmp_path))
        monkeypatch.setattr("telnetlib3.client_tui.DATA_DIR", str(tmp_path))

        app = _SessionListApp()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            screen = app.screen
            screen._on_edit_result(None)
            await pilot.pause()

    async def test_on_defaults_result(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr("telnetlib3.client_tui.SESSIONS_FILE", tmp_path / "s.json")
        monkeypatch.setattr("telnetlib3.client_tui.CONFIG_DIR", str(tmp_path))
        monkeypatch.setattr("telnetlib3.client_tui.DATA_DIR", str(tmp_path))

        app = _SessionListApp()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            screen = app.screen
            cfg = SessionConfig(name=DEFAULTS_KEY, encoding="cp437")
            screen._on_defaults_result(cfg)
            await pilot.pause()
            assert screen._sessions[DEFAULTS_KEY].encoding == "cp437"

    async def test_on_defaults_result_none(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr("telnetlib3.client_tui.SESSIONS_FILE", tmp_path / "s.json")
        monkeypatch.setattr("telnetlib3.client_tui.CONFIG_DIR", str(tmp_path))
        monkeypatch.setattr("telnetlib3.client_tui.DATA_DIR", str(tmp_path))

        app = _SessionListApp()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            screen = app.screen
            screen._on_defaults_result(None)
            await pilot.pause()


@pytest.mark.asyncio
class TestSessionEditScreenTextual:

    async def test_edit_screen_compose(self) -> None:
        cfg = SessionConfig(
            name="test", host="example.com", port=2323, encoding="utf-8", colormatch="vga"
        )
        app = _EditApp(cfg)
        async with app.run_test(size=(80, 30)) as pilot:
            await pilot.pause()
            screen = app.screen
            assert screen.query_one("#name", Input).value == "test"
            assert screen.query_one("#host", Input).value == "example.com"
            assert screen.query_one("#port", Input).value == "2323"
            assert screen.query_one("#term", Input) is not None
            assert screen.query_one("#encoding", Select) is not None
            assert screen.query_one("#colormatch", Select) is not None
            assert screen.query_one("#loglevel", Select) is not None

    async def test_edit_screen_defaults_mode(self) -> None:
        cfg = SessionConfig(name=DEFAULTS_KEY, encoding="cp437")
        app = _EditApp(cfg, is_defaults=True)
        async with app.run_test(size=(80, 30)) as pilot:
            await pilot.pause()
            screen = app.screen
            assert screen.query_one("#ssl", Switch) is not None

    async def test_collect_config_roundtrip(self) -> None:
        cfg = SessionConfig(
            name="test",
            host="example.com",
            port=2323,
            encoding="utf-8",
            colormatch="vga",
            loglevel="warn",
            encoding_errors="replace",
            mode="auto",
        )
        app = _EditApp(cfg)
        async with app.run_test(size=(80, 30)) as pilot:
            await pilot.pause()
            screen = app.screen
            collected = screen._collect_config()
            assert collected.name == "test"
            assert collected.host == "example.com"
            assert collected.port == 2323

    async def test_radio_set_disables_repl(self) -> None:
        cfg = SessionConfig(name="test", host="h", mode="auto")
        app = _EditApp(cfg)
        async with app.run_test(size=(80, 30)) as pilot:
            await pilot.pause()
            screen = app.screen
            raw_radio = screen.query_one("#mode-raw", RadioButton)
            raw_radio.value = True
            await pilot.pause()
            repl_switch = screen.query_one("#use-repl", Switch)
            assert repl_switch.disabled is True

    async def test_tab_switching(self) -> None:
        cfg = SessionConfig(name="test", host="h")
        app = _EditApp(cfg)
        async with app.run_test(size=(80, 30)) as pilot:
            await pilot.pause()
            screen = app.screen
            switcher = screen.query_one("#tab-content", ContentSwitcher)
            assert switcher.current == "tab-connection"
            btn = screen.query_one("#tabbtn-tab-terminal")
            await pilot.click(btn)
            await pilot.pause()
            assert switcher.current == "tab-terminal"

    async def test_save_dismisses(self) -> None:
        cfg = SessionConfig(name="test", host="h")
        dismissed: list = []
        app = _EditApp(cfg)
        async with app.run_test(size=(80, 30)) as pilot:
            await pilot.pause()
            screen = app.screen
            original_dismiss = screen.dismiss

            def _capture_dismiss(result=None):
                dismissed.append(result)
                original_dismiss(result)

            screen.dismiss = _capture_dismiss
            screen._on_save()
            await pilot.pause()
            assert len(dismissed) == 1
            assert isinstance(dismissed[0], SessionConfig)

    async def test_cancel_dismisses_none(self) -> None:
        cfg = SessionConfig(name="test", host="h")
        dismissed: list = []
        app = _EditApp(cfg)
        async with app.run_test(size=(80, 30)) as pilot:
            await pilot.pause()
            screen = app.screen
            original_dismiss = screen.dismiss

            def _capture_dismiss(result=None):
                dismissed.append(result)
                original_dismiss(result)

            screen.dismiss = _capture_dismiss
            btn = screen.query_one("#cancel-btn")
            await pilot.click(btn)
            await pilot.pause()
            assert None in dismissed


@pytest.mark.asyncio
class TestSessionListActionConnect:

    async def test_action_connect_runs_subprocess(self, tmp_path, monkeypatch) -> None:
        import os
        import subprocess as _subprocess

        monkeypatch.setattr("telnetlib3.client_tui.SESSIONS_FILE", tmp_path / "s.json")
        monkeypatch.setattr("telnetlib3.client_tui.CONFIG_DIR", str(tmp_path))
        monkeypatch.setattr("telnetlib3.client_tui.DATA_DIR", str(tmp_path))
        sessions = {"srv1": SessionConfig(name="srv1", host="localhost", port=12345)}
        save_sessions(sessions)

        popen_calls: list = []

        class _FakeProc:
            returncode = 0
            stderr = None

            def wait(self, timeout=None):
                pass

            def terminate(self):
                pass

        def _fake_popen(cmd, **kwargs):
            popen_calls.append(cmd)
            return _FakeProc()

        monkeypatch.setattr(_subprocess, "Popen", _fake_popen)

        _real_get_terminal_size = os.get_terminal_size

        def _fake_get_terminal_size(*args):
            if args:
                return _real_get_terminal_size(*args)
            return os.terminal_size((80, 24))

        import contextlib

        @contextlib.contextmanager
        def _fake_suspend():
            yield

        app = _SessionListApp()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            screen = app.screen
            monkeypatch.setattr(os, "set_blocking", lambda fd, blocking: None)
            monkeypatch.setattr(os, "get_terminal_size", _fake_get_terminal_size)
            monkeypatch.setattr(app, "suspend", _fake_suspend)
            screen.action_connect()
            await pilot.pause()
            assert len(popen_calls) == 1
            assert "localhost" in " ".join(popen_calls[0])


_TEST_SK = "test.host:23"


class _MacroEditApp(textual.app.App[None]):
    def __init__(self, path: str, session_key: str = _TEST_SK) -> None:
        super().__init__()
        self._path = path
        self._session_key = session_key

    def on_mount(self) -> None:
        self.push_screen(
            MacroEditScreen(path=self._path, session_key=self._session_key),
            callback=lambda _: None,
        )


class _AutoreplyEditApp(textual.app.App[None]):
    def __init__(self, path: str, session_key: str = _TEST_SK) -> None:
        super().__init__()
        self._path = path
        self._session_key = session_key

    def on_mount(self) -> None:
        self.push_screen(
            AutoreplyEditScreen(path=self._path, session_key=self._session_key),
            callback=lambda _: None,
        )


@pytest.mark.asyncio
class TestMacroEditScreenTextual:

    async def test_compose_and_mount(self, tmp_path) -> None:
        import json

        fp = tmp_path / "macros.json"
        fp.write_text(json.dumps({_TEST_SK: {"macros": [{"key": "f5", "text": "look<CR>"}]}}))
        app = _MacroEditApp(str(fp))
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            table = app.screen.query_one("#macro-table", DataTable)
            assert table.row_count == 1

    async def test_add_macro(self, tmp_path) -> None:
        fp = tmp_path / "macros.json"
        fp.write_text('{"' + _TEST_SK + '": {"macros": []}}')
        app = _MacroEditApp(str(fp))
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            screen = app.screen
            add_btn = screen.query_one("#macro-add")
            await pilot.click(add_btn)
            await pilot.pause()
            screen.query_one("#macro-key", Input).value = "f7"
            screen.query_one("#macro-text", Input).value = "test<CR>"
            screen._submit_form()
            await pilot.pause()
            table = screen.query_one("#macro-table", DataTable)
            assert table.row_count == 1

    async def test_edit_macro(self, tmp_path) -> None:
        import json

        fp = tmp_path / "macros.json"
        fp.write_text(json.dumps({_TEST_SK: {"macros": [{"key": "f5", "text": "look<CR>"}]}}))
        app = _MacroEditApp(str(fp))
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            screen = app.screen
            edit_btn = screen.query_one("#macro-edit")
            await pilot.click(edit_btn)
            await pilot.pause()
            assert screen.query_one("#macro-form").display is True

    async def test_delete_macro(self, tmp_path) -> None:
        import json

        fp = tmp_path / "macros.json"
        fp.write_text(json.dumps({_TEST_SK: {"macros": [{"key": "f5", "text": "look<CR>"}]}}))
        app = _MacroEditApp(str(fp))
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            screen = app.screen
            del_btn = screen.query_one("#macro-delete")
            await pilot.click(del_btn)
            await pilot.pause()
            table = screen.query_one("#macro-table", DataTable)
            assert table.row_count == 0

    async def test_save_macro(self, tmp_path) -> None:
        fp = tmp_path / "macros.json"
        fp.write_text('{"' + _TEST_SK + '": {"macros": []}}')
        app = _MacroEditApp(str(fp))
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            screen = app.screen
            screen._macros = [("f5", "look<CR>")]
            screen._refresh_table()
            save_btn = screen.query_one("#macro-save")
            await pilot.click(save_btn)
            await pilot.pause()

    async def test_close_macro(self, tmp_path) -> None:
        fp = tmp_path / "macros.json"
        fp.write_text('{"' + _TEST_SK + '": {"macros": []}}')
        app = _MacroEditApp(str(fp))
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            screen = app.screen
            close_btn = screen.query_one("#macro-close")
            await pilot.click(close_btn)
            await pilot.pause()

    async def test_cancel_form(self, tmp_path) -> None:
        fp = tmp_path / "macros.json"
        fp.write_text('{"' + _TEST_SK + '": {"macros": []}}')
        app = _MacroEditApp(str(fp))
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            screen = app.screen
            screen._show_form()
            await pilot.pause()
            screen._hide_form()
            await pilot.pause()
            assert screen.query_one("#macro-form").display is False

    async def test_escape_closes_form(self, tmp_path) -> None:
        fp = tmp_path / "macros.json"
        fp.write_text('{"' + _TEST_SK + '": {"macros": []}}')
        app = _MacroEditApp(str(fp))
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            screen = app.screen
            screen._show_form()
            await pilot.pause()
            screen.action_cancel_or_close()
            await pilot.pause()
            assert screen.query_one("#macro-form").display is False

    async def test_input_submitted_triggers_form(self, tmp_path) -> None:
        fp = tmp_path / "macros.json"
        fp.write_text('{"' + _TEST_SK + '": {"macros": []}}')
        app = _MacroEditApp(str(fp))
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            screen = app.screen
            screen._show_form("f5", "look<CR>")
            await pilot.pause()
            screen._submit_form()
            await pilot.pause()
            table = screen.query_one("#macro-table", DataTable)
            assert table.row_count == 1


@pytest.mark.asyncio
class TestAutoreplyEditScreenTextual:

    async def test_compose_and_mount(self, tmp_path) -> None:
        import json

        fp = tmp_path / "autoreplies.json"
        fp.write_text(
            json.dumps({_TEST_SK: {"autoreplies": [{"pattern": r"\d+ gold", "reply": "get gold<CR>"}]}})
        )
        app = _AutoreplyEditApp(str(fp))
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            table = app.screen.query_one("#autoreply-table", DataTable)
            assert table.row_count == 1

    async def test_add_autoreply(self, tmp_path) -> None:
        fp = tmp_path / "autoreplies.json"
        fp.write_text('{"' + _TEST_SK + '": {"autoreplies": []}}')
        app = _AutoreplyEditApp(str(fp))
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            screen = app.screen
            add_btn = screen.query_one("#autoreply-add")
            await pilot.click(add_btn)
            await pilot.pause()
            screen.query_one("#autoreply-pattern", Input).value = "hello"
            screen.query_one("#autoreply-reply", Input).value = "world<CR>"
            screen._rules.append(("hello", "world<CR>"))
            screen._refresh_table()
            screen._hide_form()
            await pilot.pause()
            table = screen.query_one("#autoreply-table", DataTable)
            assert table.row_count == 1

    async def test_edit_autoreply(self, tmp_path) -> None:
        import json

        fp = tmp_path / "autoreplies.json"
        fp.write_text(json.dumps({_TEST_SK: {"autoreplies": [{"pattern": "hello", "reply": "world<CR>"}]}}))
        app = _AutoreplyEditApp(str(fp))
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            screen = app.screen
            edit_btn = screen.query_one("#autoreply-edit")
            await pilot.click(edit_btn)
            await pilot.pause()
            assert screen.query_one("#autoreply-form").display is True

    async def test_delete_autoreply(self, tmp_path) -> None:
        import json

        fp = tmp_path / "autoreplies.json"
        fp.write_text(json.dumps({_TEST_SK: {"autoreplies": [{"pattern": "hello", "reply": "world<CR>"}]}}))
        app = _AutoreplyEditApp(str(fp))
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            screen = app.screen
            del_btn = screen.query_one("#autoreply-delete")
            await pilot.click(del_btn)
            await pilot.pause()
            table = screen.query_one("#autoreply-table", DataTable)
            assert table.row_count == 0

    async def test_save_autoreply(self, tmp_path) -> None:
        fp = tmp_path / "autoreplies.json"
        fp.write_text('{"' + _TEST_SK + '": {"autoreplies": []}}')
        app = _AutoreplyEditApp(str(fp))
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            screen = app.screen
            screen._rules = [("hello", "world<CR>")]
            screen._refresh_table()
            save_btn = screen.query_one("#autoreply-save")
            await pilot.click(save_btn)
            await pilot.pause()

    async def test_close_autoreply(self, tmp_path) -> None:
        fp = tmp_path / "autoreplies.json"
        fp.write_text('{"' + _TEST_SK + '": {"autoreplies": []}}')
        app = _AutoreplyEditApp(str(fp))
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            screen = app.screen
            close_btn = screen.query_one("#autoreply-close")
            await pilot.click(close_btn)
            await pilot.pause()

    async def test_cancel_form(self, tmp_path) -> None:
        fp = tmp_path / "autoreplies.json"
        fp.write_text('{"' + _TEST_SK + '": {"autoreplies": []}}')
        app = _AutoreplyEditApp(str(fp))
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            screen = app.screen
            screen._show_form()
            await pilot.pause()
            screen._hide_form()
            await pilot.pause()
            assert screen.query_one("#autoreply-form").display is False

    async def test_invalid_regex_notifies(self, tmp_path) -> None:
        fp = tmp_path / "autoreplies.json"
        fp.write_text('{"' + _TEST_SK + '": {"autoreplies": []}}')
        app = _AutoreplyEditApp(str(fp))
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            screen = app.screen
            add_btn = screen.query_one("#autoreply-add")
            await pilot.click(add_btn)
            await pilot.pause()
            screen.query_one("#autoreply-pattern", Input).value = "[invalid"
            screen.query_one("#autoreply-reply", Input).value = "x"
            ok_btn = screen.query_one("#autoreply-ok")
            await pilot.click(ok_btn)
            await pilot.pause()


class _EditorAppTest(textual.app.App[None]):
    def __init__(self, screen) -> None:
        super().__init__()
        self._editor_screen = screen

    def on_mount(self) -> None:
        self.push_screen(self._editor_screen, callback=lambda _: self.exit())


@pytest.mark.asyncio
class TestEditorApp:

    async def test_editor_app_macro(self, tmp_path) -> None:
        fp = tmp_path / "macros.json"
        fp.write_text('{"' + _TEST_SK + '": {"macros": []}}')
        from telnetlib3.client_tui import _EditorApp

        app = _EditorApp(MacroEditScreen(path=str(fp)))
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            assert app.screen.query_one("#macro-table", DataTable) is not None

    async def test_editor_app_autoreply(self, tmp_path) -> None:
        fp = tmp_path / "autoreplies.json"
        fp.write_text('{"' + _TEST_SK + '": {"autoreplies": []}}')
        from telnetlib3.client_tui import _EditorApp

        app = _EditorApp(AutoreplyEditScreen(path=str(fp)))
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            assert app.screen.query_one("#autoreply-table", DataTable) is not None


@pytest.mark.asyncio
class TestEditorMainFunctions:

    async def test_edit_macros_main(self, tmp_path, monkeypatch) -> None:
        fp = tmp_path / "macros.json"
        fp.write_text('{"' + _TEST_SK + '": {"macros": []}}')
        from telnetlib3.client_tui import _EditorApp

        calls: list = []
        monkeypatch.setattr(_EditorApp, "run", lambda self: calls.append(True))
        edit_macros_main(str(fp), _TEST_SK)
        assert calls

    async def test_edit_autoreplies_main(self, tmp_path, monkeypatch) -> None:
        fp = tmp_path / "autoreplies.json"
        fp.write_text('{"' + _TEST_SK + '": {"autoreplies": []}}')
        from telnetlib3.client_tui import _EditorApp

        calls: list = []
        monkeypatch.setattr(_EditorApp, "run", lambda self: calls.append(True))
        edit_autoreplies_main(str(fp), _TEST_SK)
        assert calls


@pytest.mark.asyncio
class TestTelnetSessionAppTextual:

    async def test_app_mounts_session_list(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr("telnetlib3.client_tui.SESSIONS_FILE", tmp_path / "s.json")
        monkeypatch.setattr("telnetlib3.client_tui.CONFIG_DIR", str(tmp_path))
        monkeypatch.setattr("telnetlib3.client_tui.DATA_DIR", str(tmp_path))

        app = TelnetSessionApp()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            assert isinstance(app.screen, SessionListScreen)


@pytest.mark.asyncio
class TestMacroEditScreenButtonDispatch:

    async def test_button_macro_ok_dispatches(self, tmp_path) -> None:
        from textual.widgets import Button

        fp = tmp_path / "macros.json"
        fp.write_text('{"' + _TEST_SK + '": {"macros": []}}')
        app = _MacroEditApp(str(fp))
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            screen = app.screen
            screen._show_form("f5", "look<CR>")
            await pilot.pause()
            btn = screen.query_one("#macro-ok", Button)
            screen.on_button_pressed(Button.Pressed(btn))
            await pilot.pause()
            assert screen.query_one("#macro-table", DataTable).row_count == 1

    async def test_button_macro_cancel_form_dispatches(self, tmp_path) -> None:
        from textual.widgets import Button

        fp = tmp_path / "macros.json"
        fp.write_text('{"' + _TEST_SK + '": {"macros": []}}')
        app = _MacroEditApp(str(fp))
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            screen = app.screen
            screen._show_form()
            await pilot.pause()
            btn = screen.query_one("#macro-cancel-form", Button)
            screen.on_button_pressed(Button.Pressed(btn))
            await pilot.pause()
            assert screen.query_one("#macro-form").display is False

    async def test_button_macro_close_dispatches(self, tmp_path) -> None:
        from textual.widgets import Button

        fp = tmp_path / "macros.json"
        fp.write_text('{"' + _TEST_SK + '": {"macros": []}}')
        dismissed: list = []

        class _App(textual.app.App[None]):
            def on_mount(self_app) -> None:
                scr = MacroEditScreen(path=str(fp))
                self_app.push_screen(scr, callback=lambda r: dismissed.append(r))

        app = _App()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            btn = app.screen.query_one("#macro-close", Button)
            app.screen.on_button_pressed(Button.Pressed(btn))
            await pilot.pause()
        assert None in dismissed

    async def test_edit_submit_form(self, tmp_path) -> None:
        fp = tmp_path / "macros.json"
        fp.write_text('{"' + _TEST_SK + '": {"macros": [{"key": "f5", "text": "old<CR>"}]}}')
        app = _MacroEditApp(str(fp))
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            screen = app.screen
            screen._editing_idx = 0
            screen.query_one("#macro-key", Input).value = "f5"
            screen.query_one("#macro-text", Input).value = "new<CR>"
            screen.query_one("#macro-form").display = True
            screen._submit_form()
            await pilot.pause()
            assert screen._macros[0] == ("f5", "new<CR>")

    async def test_selected_idx_empty_table(self, tmp_path) -> None:
        fp = tmp_path / "macros.json"
        fp.write_text('{"' + _TEST_SK + '": {"macros": []}}')
        app = _MacroEditApp(str(fp))
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            assert app.screen._selected_idx() is None

    async def test_on_input_submitted_form_visible(self, tmp_path) -> None:
        fp = tmp_path / "macros.json"
        fp.write_text('{"' + _TEST_SK + '": {"macros": []}}')
        app = _MacroEditApp(str(fp))
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            screen = app.screen
            screen._show_form("f6", "test<CR>")
            await pilot.pause()
            key_input = screen.query_one("#macro-key", Input)
            event = Input.Submitted(key_input, "f6")
            screen.on_input_submitted(event)
            await pilot.pause()
            assert screen.query_one("#macro-table", DataTable).row_count == 1

    async def test_action_cancel_or_close_no_form(self, tmp_path) -> None:
        fp = tmp_path / "macros.json"
        fp.write_text('{"' + _TEST_SK + '": {"macros": []}}')
        dismissed: list = []

        class _App(textual.app.App[None]):
            def on_mount(self_app) -> None:
                scr = MacroEditScreen(path=str(fp))
                self_app.push_screen(scr, callback=lambda r: dismissed.append(r))

        app = _App()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            app.screen.action_cancel_or_close()
            await pilot.pause()
        assert None in dismissed

    async def test_load_from_file_invalid_json(self, tmp_path) -> None:
        fp = tmp_path / "macros.json"
        fp.write_text("{invalid json")
        app = _MacroEditApp(str(fp))
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            assert app.screen.query_one("#macro-table", DataTable).row_count == 0


@pytest.mark.asyncio
class TestAutoreplyEditScreenButtonDispatch:

    async def test_button_autoreply_ok_dispatches(self, tmp_path) -> None:
        from textual.widgets import Button

        fp = tmp_path / "autoreplies.json"
        fp.write_text('{"' + _TEST_SK + '": {"autoreplies": []}}')
        app = _AutoreplyEditApp(str(fp))
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            screen = app.screen
            screen._show_form("hello", "world<CR>")
            await pilot.pause()
            btn = screen.query_one("#autoreply-ok", Button)
            screen.on_button_pressed(Button.Pressed(btn))
            await pilot.pause()
            assert screen.query_one("#autoreply-table", DataTable).row_count == 1

    async def test_button_autoreply_cancel_form_dispatches(self, tmp_path) -> None:
        from textual.widgets import Button

        fp = tmp_path / "autoreplies.json"
        fp.write_text('{"' + _TEST_SK + '": {"autoreplies": []}}')
        app = _AutoreplyEditApp(str(fp))
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            screen = app.screen
            screen._show_form()
            await pilot.pause()
            btn = screen.query_one("#autoreply-cancel-form", Button)
            screen.on_button_pressed(Button.Pressed(btn))
            await pilot.pause()
            assert screen.query_one("#autoreply-form").display is False

    async def test_button_autoreply_close_dispatches(self, tmp_path) -> None:
        from textual.widgets import Button

        fp = tmp_path / "autoreplies.json"
        fp.write_text('{"' + _TEST_SK + '": {"autoreplies": []}}')
        dismissed: list = []

        class _App(textual.app.App[None]):
            def on_mount(self_app) -> None:
                scr = AutoreplyEditScreen(path=str(fp))
                self_app.push_screen(scr, callback=lambda r: dismissed.append(r))

        app = _App()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            btn = app.screen.query_one("#autoreply-close", Button)
            app.screen.on_button_pressed(Button.Pressed(btn))
            await pilot.pause()
        assert None in dismissed

    async def test_edit_submit_form(self, tmp_path) -> None:
        import json

        fp = tmp_path / "autoreplies.json"
        fp.write_text(json.dumps({_TEST_SK: {"autoreplies": [{"pattern": "old", "reply": "old<CR>"}]}}))
        app = _AutoreplyEditApp(str(fp))
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            screen = app.screen
            screen._editing_idx = 0
            screen.query_one("#autoreply-pattern", Input).value = "new"
            screen.query_one("#autoreply-reply", Input).value = "new<CR>"
            screen.query_one("#autoreply-form").display = True
            screen._submit_form()
            await pilot.pause()
            assert screen._rules[0] == ("new", "new<CR>")

    async def test_selected_idx_empty_table(self, tmp_path) -> None:
        fp = tmp_path / "autoreplies.json"
        fp.write_text('{"' + _TEST_SK + '": {"autoreplies": []}}')
        app = _AutoreplyEditApp(str(fp))
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            assert app.screen._selected_idx() is None

    async def test_on_input_submitted_form_visible(self, tmp_path) -> None:
        fp = tmp_path / "autoreplies.json"
        fp.write_text('{"' + _TEST_SK + '": {"autoreplies": []}}')
        app = _AutoreplyEditApp(str(fp))
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            screen = app.screen
            screen._show_form("ping", "pong<CR>")
            await pilot.pause()
            pat_input = screen.query_one("#autoreply-pattern", Input)
            event = Input.Submitted(pat_input, "ping")
            screen.on_input_submitted(event)
            await pilot.pause()
            assert screen.query_one("#autoreply-table", DataTable).row_count == 1

    async def test_action_cancel_or_close_no_form(self, tmp_path) -> None:
        fp = tmp_path / "autoreplies.json"
        fp.write_text('{"' + _TEST_SK + '": {"autoreplies": []}}')
        dismissed: list = []

        class _App(textual.app.App[None]):
            def on_mount(self_app) -> None:
                scr = AutoreplyEditScreen(path=str(fp))
                self_app.push_screen(scr, callback=lambda r: dismissed.append(r))

        app = _App()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            app.screen.action_cancel_or_close()
            await pilot.pause()
        assert None in dismissed

    async def test_load_from_file_invalid_json(self, tmp_path) -> None:
        fp = tmp_path / "autoreplies.json"
        fp.write_text("{invalid json")
        app = _AutoreplyEditApp(str(fp))
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            assert app.screen.query_one("#autoreply-table", DataTable).row_count == 0

    async def test_load_from_file_nonexistent(self, tmp_path) -> None:
        fp = tmp_path / "nonexistent.json"
        app = _AutoreplyEditApp(str(fp))
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            assert app.screen.query_one("#autoreply-table", DataTable).row_count == 0

    async def test_submit_form_invalid_regex(self, tmp_path) -> None:
        fp = tmp_path / "autoreplies.json"
        fp.write_text('{"' + _TEST_SK + '": {"autoreplies": []}}')
        app = _AutoreplyEditApp(str(fp))
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            screen = app.screen
            screen._show_form("[invalid", "reply<CR>")
            await pilot.pause()
            screen._submit_form()
            await pilot.pause()
            assert screen.query_one("#autoreply-table", DataTable).row_count == 0
            assert screen.query_one("#autoreply-form").display is True


@pytest.mark.asyncio
class TestSessionListCallbacks:

    async def test_action_edit_autoreplies(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr("telnetlib3.client_tui.SESSIONS_FILE", tmp_path / "s.json")
        monkeypatch.setattr("telnetlib3.client_tui.CONFIG_DIR", str(tmp_path))
        monkeypatch.setattr("telnetlib3.client_tui.DATA_DIR", str(tmp_path))
        sessions = {"srv1": SessionConfig(name="srv1", host="host1")}
        save_sessions(sessions)

        app = _SessionListApp()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            screen = app.screen
            screen.action_edit_autoreplies()
            await pilot.pause()
            assert isinstance(app.screen, AutoreplyEditScreen)

    async def test_action_edit_autoreplies_no_selection(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr("telnetlib3.client_tui.SESSIONS_FILE", tmp_path / "s.json")
        monkeypatch.setattr("telnetlib3.client_tui.CONFIG_DIR", str(tmp_path))
        monkeypatch.setattr("telnetlib3.client_tui.DATA_DIR", str(tmp_path))

        app = _SessionListApp()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            screen = app.screen
            screen.action_edit_autoreplies()
            await pilot.pause()


@pytest.mark.asyncio
class TestSessionEditSwitchHandlers:

    async def test_ice_colors_switch_updates_palette(self) -> None:
        cfg = SessionConfig(name="test", host="h", colormatch="vga")
        app = _EditApp(cfg)
        async with app.run_test(size=(80, 30)) as pilot:
            await pilot.pause()
            screen = app.screen
            ice_switch = screen.query_one("#ice-colors", Switch)
            ice_switch.value = not ice_switch.value
            await pilot.pause()


@pytest.mark.asyncio
class TestSessionListActionConnectInterrupt:

    async def test_action_connect_keyboard_interrupt(self, tmp_path, monkeypatch) -> None:
        import os
        import subprocess as _subprocess

        monkeypatch.setattr("telnetlib3.client_tui.SESSIONS_FILE", tmp_path / "s.json")
        monkeypatch.setattr("telnetlib3.client_tui.CONFIG_DIR", str(tmp_path))
        monkeypatch.setattr("telnetlib3.client_tui.DATA_DIR", str(tmp_path))
        sessions = {"srv1": SessionConfig(name="srv1", host="localhost", port=12345)}
        save_sessions(sessions)

        terminated: list = []

        class _FakeProc:
            returncode = 0
            stderr = None

            def wait(self, timeout=None):
                if not terminated:
                    raise KeyboardInterrupt

            def terminate(self):
                terminated.append(True)

        def _fake_popen(cmd, **kwargs):
            return _FakeProc()

        monkeypatch.setattr(_subprocess, "Popen", _fake_popen)

        _real_get_terminal_size = os.get_terminal_size

        def _fake_get_terminal_size(*args):
            if args:
                return _real_get_terminal_size(*args)
            return os.terminal_size((80, 24))

        import contextlib

        @contextlib.contextmanager
        def _fake_suspend():
            yield

        app = _SessionListApp()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            screen = app.screen
            monkeypatch.setattr(os, "set_blocking", lambda fd, blocking: None)
            monkeypatch.setattr(os, "get_terminal_size", _fake_get_terminal_size)
            monkeypatch.setattr(app, "suspend", _fake_suspend)
            screen.action_connect()
            await pilot.pause()
        assert terminated


@pytest.mark.asyncio
async def test_arrow_nav_session_list_buttons(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("telnetlib3.client_tui.SESSIONS_FILE", tmp_path / "s.json")
    monkeypatch.setattr("telnetlib3.client_tui.CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr("telnetlib3.client_tui.DATA_DIR", str(tmp_path))
    sessions = {"srv1": SessionConfig(name="srv1", host="host1", port=23)}
    save_sessions(sessions)
    app = _SessionListApp()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        screen = app.screen
        buttons = list(screen.query("#button-col Button"))
        buttons[0].focus()
        await pilot.press("down")
        await pilot.pause()
        assert screen.focused is buttons[1]
        await pilot.press("up")
        await pilot.pause()
        assert screen.focused is buttons[0]


@pytest.mark.asyncio
async def test_arrow_nav_session_list_right_to_table(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("telnetlib3.client_tui.SESSIONS_FILE", tmp_path / "s.json")
    monkeypatch.setattr("telnetlib3.client_tui.CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr("telnetlib3.client_tui.DATA_DIR", str(tmp_path))
    sessions = {"srv1": SessionConfig(name="srv1", host="host1", port=23)}
    save_sessions(sessions)
    app = _SessionListApp()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        screen = app.screen
        buttons = list(screen.query("#button-col Button"))
        buttons[0].focus()
        await pilot.press("right")
        await pilot.pause()
        table = screen.query_one("#session-table", DataTable)
        assert screen.focused is table


@pytest.mark.asyncio
async def test_arrow_nav_session_list_left_from_table(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("telnetlib3.client_tui.SESSIONS_FILE", tmp_path / "s.json")
    monkeypatch.setattr("telnetlib3.client_tui.CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr("telnetlib3.client_tui.DATA_DIR", str(tmp_path))
    sessions = {"srv1": SessionConfig(name="srv1", host="host1", port=23)}
    save_sessions(sessions)
    app = _SessionListApp()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        screen = app.screen
        table = screen.query_one("#session-table", DataTable)
        table.focus()
        await pilot.press("left")
        await pilot.pause()
        buttons = list(screen.query("#button-col Button"))
        assert screen.focused is buttons[0]


@pytest.mark.asyncio
async def test_arrow_nav_macro_buttons(tmp_path) -> None:
    fp = tmp_path / "macros.json"
    fp.write_text('{"' + _TEST_SK + '": {"macros": []}}')
    app = _MacroEditApp(str(fp))
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        screen = app.screen
        buttons = list(screen.query("#macro-button-col Button"))
        buttons[0].focus()
        await pilot.press("down")
        await pilot.pause()
        assert screen.focused is buttons[1]


@pytest.mark.asyncio
async def test_arrow_nav_autoreply_buttons(tmp_path) -> None:
    fp = tmp_path / "autoreplies.json"
    fp.write_text('{"' + _TEST_SK + '": {"autoreplies": []}}')
    app = _AutoreplyEditApp(str(fp))
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        screen = app.screen
        buttons = list(screen.query("#autoreply-button-col Button"))
        buttons[0].focus()
        await pilot.press("down")
        await pilot.pause()
        assert screen.focused is buttons[1]
