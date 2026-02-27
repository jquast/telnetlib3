"""Tests for confirm dialog: subprocess launcher and in-app Textual screen."""

from __future__ import annotations

# std imports
import json
from typing import Any

# 3rd party
import pytest

# local
from telnetlib3.client_repl import _confirm_dialog


@pytest.fixture()
def _mock_subprocess(tmp_path: Any, monkeypatch: Any) -> Any:
    """Stub subprocess.run to write a result file without launching Textual."""
    result_data: dict[str, bool] = {"confirmed": False}

    class _Holder:
        data = result_data

    def fake_run(cmd: Any, check: bool = False) -> None:
        path = cmd[-2]
        with open(path, "w", encoding="utf-8") as f:
            json.dump(_Holder.data, f)

    monkeypatch.setattr("subprocess.run", fake_run)
    monkeypatch.setattr("telnetlib3.client_repl._restore_after_subprocess", lambda buf: None)
    monkeypatch.setattr("telnetlib3.client_repl._terminal_cleanup", lambda: "")
    return _Holder


def test_cancel_returns_false(_mock_subprocess: Any) -> None:
    _mock_subprocess.data = {"confirmed": False}
    ok = _confirm_dialog("Test", "body")
    assert ok is False


def test_confirmed_returns_true(_mock_subprocess: Any) -> None:
    _mock_subprocess.data = {"confirmed": True}
    ok = _confirm_dialog("Test", "body")
    assert ok is True


def test_missing_result_file(monkeypatch: Any) -> None:
    monkeypatch.setattr("subprocess.run", lambda cmd, check=False: None)
    monkeypatch.setattr("telnetlib3.client_repl._restore_after_subprocess", lambda buf: None)
    monkeypatch.setattr("telnetlib3.client_repl._terminal_cleanup", lambda: "")
    ok = _confirm_dialog("Test", "body")
    assert ok is False


def test_warning_passed_in_command(_mock_subprocess: Any) -> None:
    _mock_subprocess.data = {"confirmed": False}
    ok = _confirm_dialog("Test", "body", warning="Danger!")
    assert ok is False


pytest.importorskip("textual")

# 3rd party
from textual.app import App, ComposeResult  # noqa: E402
from textual.widgets import Button  # noqa: E402

# local
from telnetlib3.client_tui import _ConfirmDialogScreen  # noqa: E402


class _ConfirmTestApp(App[bool]):
    """Minimal app that pushes a _ConfirmDialogScreen on mount."""

    def __init__(self, **dialog_kwargs: Any) -> None:
        super().__init__()
        self._dialog_kwargs = dialog_kwargs
        self.result: bool | None = None

    def compose(self) -> ComposeResult:
        yield Button("placeholder")

    def on_mount(self) -> None:
        self.push_screen(_ConfirmDialogScreen(**self._dialog_kwargs), callback=self._on_result)

    def _on_result(self, value: bool) -> None:
        self.result = value
        self.exit(value)


@pytest.mark.asyncio()
async def test_dismiss_true_on_ok() -> None:
    app = _ConfirmTestApp(title="Delete?", body="Really delete?")
    async with app.run_test() as pilot:
        await pilot.click("#confirm-ok")
        assert app.result is True


@pytest.mark.asyncio()
async def test_dismiss_false_on_cancel() -> None:
    app = _ConfirmTestApp(title="Delete?", body="Really delete?")
    async with app.run_test() as pilot:
        await pilot.click("#confirm-cancel")
        assert app.result is False


@pytest.mark.asyncio()
async def test_dismiss_false_on_escape() -> None:
    app = _ConfirmTestApp(title="Delete?", body="Really delete?")
    async with app.run_test() as pilot:
        await pilot.press("escape")
        assert app.result is False
