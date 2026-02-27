"""Tests for telnetlib3.help topic loader."""
import pytest

from telnetlib3.help import get_help


@pytest.mark.parametrize("topic", ["macro", "autoreply", "highlight", "keybindings"])
def test_get_help_returns_string(topic: str) -> None:
    result = get_help(topic)
    assert isinstance(result, str)
    assert len(result) > 100


def test_macro_includes_commands() -> None:
    result = get_help("macro")
    assert "## Macro Editor" in result
    assert "## Command Syntax" in result


def test_autoreply_includes_commands() -> None:
    result = get_help("autoreply")
    assert "## Autoreply Editor" in result
    assert "## Command Syntax" in result


def test_highlight_no_commands() -> None:
    result = get_help("highlight")
    assert "## Highlight Editor" in result
    assert "## Command Syntax" not in result


def test_keybindings_contains_key_sections() -> None:
    result = get_help("keybindings")
    assert "Session Keys" in result
    assert "GMCP Keys" in result
    assert "Line Editing" in result
    assert "Command Processing" in result
    assert "F1" in result
    assert "Ctrl+]" in result


def test_unknown_topic_raises() -> None:
    with pytest.raises(ValueError, match="unknown help topic"):
        get_help("nonexistent")


def test_render_help_md_no_gmcp() -> None:
    from telnetlib3.client_repl_dialogs import _render_help_md

    lines = _render_help_md(has_gmcp=False)
    text = "\n".join(lines)
    assert "F1" in text
    assert "F8" in text
    assert "F3" not in text
    assert "F7" not in text


def test_render_help_md_with_gmcp() -> None:
    from telnetlib3.client_repl_dialogs import _render_help_md

    lines = _render_help_md(has_gmcp=True)
    text = "\n".join(lines)
    assert "F1" in text
    assert "F3" in text
    assert "F7" in text
