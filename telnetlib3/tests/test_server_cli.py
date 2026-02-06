"""Tests for server CLI argument parsing and PTY support detection."""

# std imports
import sys
from unittest import mock

# 3rd party
import pytest

# local
import telnetlib3
from telnetlib3 import server


def test_pty_support_detection_with_modules():
    """PTY_SUPPORT is True when all required modules are available."""
    # local
    if sys.platform == "win32":
        assert server.PTY_SUPPORT is False
    else:
        assert server.PTY_SUPPORT is True


def test_parse_server_args_includes_pty_options_when_supported():
    """CLI parser includes --pty-exec when PTY is supported."""
    # local
    if not server.PTY_SUPPORT:
        pytest.skip("PTY not supported on this platform")

    with mock.patch.object(sys, "argv", ["server"]):
        result = server.parse_server_args()
        assert "pty_exec" in result
        assert "pty_fork_limit" in result


def test_parse_server_args_excludes_pty_options_when_not_supported():
    """CLI parser sets PTY options to defaults when PTY is not supported."""
    # local
    original_support = server.PTY_SUPPORT
    try:
        server.PTY_SUPPORT = False
        with mock.patch.object(sys, "argv", ["server"]):
            result = server.parse_server_args()
            assert result["pty_exec"] is None
            assert result["pty_fork_limit"] == 0
            assert result["pty_args"] is None
    finally:
        server.PTY_SUPPORT = original_support


def test_run_server_raises_on_pty_exec_without_support():
    """run_server raises NotImplementedError when pty_exec is used without PTY support."""
    # local
    original_support = server.PTY_SUPPORT
    try:
        server.PTY_SUPPORT = False
        with pytest.raises(NotImplementedError, match="PTY support is not available"):
            # std imports
            import asyncio

            asyncio.run(server.run_server(pty_exec="/bin/bash"))
    finally:
        server.PTY_SUPPORT = original_support


def test_telnetlib3_import_exposes_pty_support():
    """Telnetlib3 package exposes PTY_SUPPORT flag."""
    # local
    assert hasattr(telnetlib3, "PTY_SUPPORT")
    assert isinstance(telnetlib3.PTY_SUPPORT, bool)


def test_telnetlib3_pty_shell_exports_conditional():
    """pty_shell exports are only in __all__ when PTY is supported."""
    # local
    if telnetlib3.PTY_SUPPORT:
        assert "make_pty_shell" in telnetlib3.__all__
        assert "pty_shell" in telnetlib3.__all__
    else:
        assert "make_pty_shell" not in telnetlib3.__all__
        assert "pty_shell" not in telnetlib3.__all__


def test_parse_server_args_never_send_ga():
    """--never-send-ga flag is parsed correctly."""
    # local
    with mock.patch.object(sys, "argv", ["server"]):
        result = server.parse_server_args()
        assert result["never_send_ga"] is False

    with mock.patch.object(sys, "argv", ["server", "--never-send-ga"]):
        result = server.parse_server_args()
        assert result["never_send_ga"] is True
