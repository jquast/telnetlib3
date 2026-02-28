"""Tests for v3.0 code review fixes."""

# std imports
import asyncio
import logging
from unittest import mock

# 3rd party
import pytest

# local
from telnetlib3.session_context import SessionContext


class TestSessionContextClose:
    """SessionContext.close() cancels tasks and flushes timestamps."""

    def test_close_cancels_discover_task(self):
        ctx = SessionContext(session_key="test:23")
        task = mock.MagicMock(spec=asyncio.Task)
        task.done.return_value = False
        ctx.discover_task = task
        ctx.close()
        task.cancel.assert_called_once()
        assert ctx.discover_task is None

    def test_close_cancels_randomwalk_task(self):
        ctx = SessionContext(session_key="test:23")
        task = mock.MagicMock(spec=asyncio.Task)
        task.done.return_value = False
        ctx.randomwalk_task = task
        ctx.close()
        task.cancel.assert_called_once()
        assert ctx.randomwalk_task is None

    def test_close_skips_done_tasks(self):
        ctx = SessionContext(session_key="test:23")
        task = mock.MagicMock(spec=asyncio.Task)
        task.done.return_value = True
        ctx.discover_task = task
        ctx.close()
        task.cancel.assert_not_called()
        assert ctx.discover_task is None

    def test_close_cancels_save_timer(self):
        ctx = SessionContext(session_key="test:23")
        timer = mock.MagicMock(spec=asyncio.TimerHandle)
        ctx._save_timer = timer
        ctx.close()
        timer.cancel.assert_called_once()
        assert ctx._save_timer is None

    def test_close_flushes_timestamps(self):
        ctx = SessionContext(session_key="test:23")
        with mock.patch.object(ctx, "flush_timestamps") as flush:
            ctx.close()
            flush.assert_called_once()

    def test_close_idempotent(self):
        ctx = SessionContext(session_key="test:23")
        ctx.close()
        ctx.close()


class TestGmcpCallbackGuards:
    """GMCP callbacks log exceptions instead of crashing."""

    _CLIENT_DEFAULTS = {
        "encoding": "utf8",
        "encoding_errors": "strict",
        "force_binary": False,
        "connect_maxwait": 0.02,
    }

    def _make_connected_client(self, **kwargs):
        from telnetlib3.client import TelnetClient

        client = TelnetClient(**{**self._CLIENT_DEFAULTS, **kwargs})

        class _MockTransport:
            def __init__(self):
                self.data = bytearray()
                self._closing = False

            def write(self, data):
                self.data.extend(data)

            def is_closing(self):
                return self._closing

            def close(self):
                self._closing = True

            def get_extra_info(self, name, default=None):
                return default

        transport = _MockTransport()
        client.connection_made(transport)
        return client

    @pytest.mark.asyncio
    async def test_room_info_error_logged_not_raised(self):
        client = self._make_connected_client()
        ctx = SessionContext(session_key="test:23")
        ctx.rooms_file = "/nonexistent/rooms.db"
        client.writer._ctx = ctx
        with mock.patch.object(client, "_update_room_graph", side_effect=OSError("disk full")):
            client._on_gmcp("Room.Info", {"num": 1, "name": "Test"})

    @pytest.mark.asyncio
    async def test_chat_error_logged_not_raised(self):
        client = self._make_connected_client()
        ctx = SessionContext(session_key="test:23")
        client.writer._ctx = ctx
        with mock.patch.object(client, "_append_chat", side_effect=OSError("disk full")):
            client._on_gmcp("Comm.Channel.Text", {"chan": "gossip", "msg": "hi"})


class TestFireAndForgetLogging:
    """Fire-and-forget tasks log exceptions via done callbacks."""

    @pytest.mark.asyncio
    async def test_macro_task_logs_failure(self):
        log = logging.getLogger("test_macro")

        async def _failing_command(text, ctx, log):
            raise RuntimeError("macro boom")

        with mock.patch("telnetlib3.client_repl.execute_macro_commands", _failing_command):
            from telnetlib3.macros import Macro, build_macro_dispatch

            macros = [Macro(key="KEY_F1", text="/test")]
            ctx = mock.MagicMock()
            ctx.mark_macros_dirty = mock.MagicMock()
            dispatch = build_macro_dispatch(macros, ctx, log)
            handler = dispatch["KEY_F1"]

        with mock.patch.object(log, "warning") as mock_warn:
            await handler()
            await asyncio.sleep(0.1)
            mock_warn.assert_called()
            assert "macro execution failed" in str(mock_warn.call_args)


class TestRoomStoreResourceLeak:
    """RoomStore.close() is called even on exception."""

    def test_graph_closed_on_exception(self):
        mock_graph = mock.MagicMock()
        mock_graph.find_path_with_rooms.side_effect = RuntimeError("graph error")

        from telnetlib3.client_tui import RoomBrowserScreen

        screen = mock.MagicMock(spec=RoomBrowserScreen)
        screen._rooms_path = "/tmp/test.db"
        screen._current_room_file = "/tmp/current_room"
        screen._all_rooms = []
        screen.query_one = mock.MagicMock()
        screen._get_selected_room_num = mock.MagicMock(return_value="42")

        with (
            mock.patch("telnetlib3.rooms.RoomStore", return_value=mock_graph),
            mock.patch("telnetlib3.rooms.read_current_room", return_value="1"),
        ):
            with pytest.raises(RuntimeError, match="graph error"):
                RoomBrowserScreen._do_fast_travel(screen)
        mock_graph.close.assert_called_once()
