"""Tests for :mod:`telnetlib3.client_tui` data model, persistence, and command builder."""

from __future__ import annotations

# std imports
import sys
from dataclasses import asdict

# 3rd party
import pytest

textual = pytest.importorskip("textual", reason="textual not installed")

# local
from telnetlib3.client_tui import (  # noqa: E402
    DEFAULTS_KEY,
    SessionConfig,
    build_command,
    load_sessions,
    save_sessions,
)


class TestSessionConfig:
    def test_defaults(self) -> None:
        cfg = SessionConfig()
        assert cfg.port == 23
        assert cfg.encoding == "utf8"
        assert cfg.mode == "auto"
        assert cfg.colormatch == "vga"
        assert cfg.speed == 38400
        assert cfg.ssl is False
        assert cfg.no_repl is False

    def test_roundtrip(self) -> None:
        cfg = SessionConfig(
            name="test", host="example.com", port=2323, ssl=True, encoding="cp437", mode="raw"
        )
        data = asdict(cfg)
        restored = SessionConfig(**data)
        assert restored == cfg

    def test_unknown_fields_ignored(self) -> None:
        data = asdict(SessionConfig(name="x"))
        data["unknown_future_field"] = 42
        from dataclasses import fields

        known = {f.name for f in fields(SessionConfig)}
        filtered = {k: v for k, v in data.items() if k in known}
        cfg = SessionConfig(**filtered)
        assert cfg.name == "x"


class TestPersistence:
    def test_save_load_roundtrip(self, tmp_path, monkeypatch) -> None:
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

    def test_load_empty(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr("telnetlib3.client_tui.SESSIONS_FILE", tmp_path / "nope.json")
        monkeypatch.setattr("telnetlib3.client_tui.CONFIG_DIR", tmp_path)
        monkeypatch.setattr("telnetlib3.client_tui.DATA_DIR", tmp_path)
        assert load_sessions() == {}


class TestBuildCommand:
    def test_minimal(self) -> None:
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
    def test_mode_flags(self, mode: str, flag: str) -> None:
        cfg = SessionConfig(host="h", port=23, mode=mode)
        cmd = build_command(cfg)
        assert flag in cmd

    def test_auto_mode_no_flag(self) -> None:
        cfg = SessionConfig(host="h", port=23, mode="auto")
        cmd = build_command(cfg)
        assert "--raw-mode" not in cmd
        assert "--line-mode" not in cmd

    def test_ssl_flags(self) -> None:
        cfg = SessionConfig(host="h", port=992, ssl=True, ssl_no_verify=True)
        cmd = build_command(cfg)
        assert "--ssl" in cmd
        assert "--ssl-no-verify" in cmd

    def test_ssl_cafile(self) -> None:
        cfg = SessionConfig(host="h", port=992, ssl_cafile="/tmp/ca.pem")
        cmd = build_command(cfg)
        assert "--ssl-cafile" in cmd
        idx = cmd.index("--ssl-cafile")
        assert cmd[idx + 1] == "/tmp/ca.pem"

    def test_encoding(self) -> None:
        cfg = SessionConfig(host="h", port=23, encoding="cp437")
        cmd = build_command(cfg)
        assert "--encoding" in cmd
        idx = cmd.index("--encoding")
        assert cmd[idx + 1] == "cp437"

    def test_default_encoding_omitted(self) -> None:
        cfg = SessionConfig(host="h", port=23, encoding="utf8")
        cmd = build_command(cfg)
        assert "--encoding" not in cmd

    def test_always_will_do(self) -> None:
        cfg = SessionConfig(host="h", port=23, always_will="MXP,GMCP", always_do="MSSP")
        cmd = build_command(cfg)
        will_indices = [i for i, v in enumerate(cmd) if v == "--always-will"]
        assert len(will_indices) == 2
        assert cmd[will_indices[0] + 1] == "MXP"
        assert cmd[will_indices[1] + 1] == "GMCP"
        do_idx = cmd.index("--always-do")
        assert cmd[do_idx + 1] == "MSSP"

    def test_empty_always_will_omitted(self) -> None:
        cfg = SessionConfig(host="h", port=23, always_will="")
        cmd = build_command(cfg)
        assert "--always-will" not in cmd

    def test_display_options(self) -> None:
        cfg = SessionConfig(host="h", port=23, colormatch="cga", background_color="#101010")
        cmd = build_command(cfg)
        assert "--colormatch" in cmd
        assert "--background-color" in cmd

    def test_no_repl(self) -> None:
        cfg = SessionConfig(host="h", port=23, no_repl=True)
        cmd = build_command(cfg)
        assert "--no-repl" in cmd

    def test_connect_timeout_default_omitted(self) -> None:
        cfg = SessionConfig(host="h", port=23, connect_timeout=10.0)
        cmd = build_command(cfg)
        assert "--connect-timeout" not in cmd

    def test_connect_timeout_nonzero(self) -> None:
        cfg = SessionConfig(host="h", port=23, connect_timeout=5.0)
        cmd = build_command(cfg)
        assert "--connect-timeout" in cmd

    def test_ansi_keys_ascii_eol(self) -> None:
        cfg = SessionConfig(host="h", port=23, ansi_keys=True, ascii_eol=True)
        cmd = build_command(cfg)
        assert "--ansi-keys" in cmd
        assert "--ascii-eol" in cmd


class TestDefaultsInheritance:
    def test_new_from_defaults(self) -> None:
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


class TestPersistenceNegative:

    def test_corrupted_json(self, tmp_path, monkeypatch) -> None:
        sessions_file = tmp_path / "sessions.json"
        sessions_file.write_text("{invalid json", encoding="utf-8")
        monkeypatch.setattr("telnetlib3.client_tui.SESSIONS_FILE", sessions_file)
        monkeypatch.setattr("telnetlib3.client_tui.CONFIG_DIR", tmp_path)
        monkeypatch.setattr("telnetlib3.client_tui.DATA_DIR", tmp_path)
        with pytest.raises(Exception):
            load_sessions()

    def test_build_command_missing_host(self) -> None:
        cfg = SessionConfig(host="", port=23)
        cmd = build_command(cfg)
        assert "" in cmd
