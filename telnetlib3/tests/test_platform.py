# std imports
import sys

# 3rd party
import pytest


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only code path")
def test_pty_support_false_on_windows():
    """PTY_SUPPORT is False and server_pty_shell is None on Windows."""
    import telnetlib3
    from telnetlib3 import server

    assert server.PTY_SUPPORT is False
    assert telnetlib3.PTY_SUPPORT is False
    assert telnetlib3.server_pty_shell is None


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only code path")
@pytest.mark.asyncio
async def test_client_shell_win32_not_implemented():
    """telnet_client_shell raises NotImplementedError on Windows."""
    from telnetlib3.client_shell import telnet_client_shell

    with pytest.raises(NotImplementedError, match="win32"):
        await telnet_client_shell(None, None)
