"""Tests for GMCP client integration."""

# std imports
import sys
import types
import logging
from unittest import mock

# 3rd party
import pytest

# local
from telnetlib3.client import TelnetClient, _DEFAULT_GMCP_MODULES, _get_argument_parser
from telnetlib3.telopt import GMCP


_CLIENT_DEFAULTS = {
    "encoding": "utf8",
    "encoding_errors": "strict",
    "force_binary": False,
    "connect_maxwait": 0.02,
}


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


def _make_client(**kwargs):
    return TelnetClient(**{**_CLIENT_DEFAULTS, **kwargs})


def _make_connected_client(**kwargs):
    client = _make_client(**kwargs)
    transport = _MockTransport()
    client.connection_made(transport)
    return client, transport


@pytest.mark.asyncio
async def test_default_gmcp_data_dict():
    client = _make_client()
    assert client._gmcp_data == {}


@pytest.mark.asyncio
async def test_default_gmcp_modules():
    client = _make_client()
    assert client._gmcp_modules == _DEFAULT_GMCP_MODULES


@pytest.mark.asyncio
async def test_custom_gmcp_modules():
    modules = ["Char 1", "IRE.Rift 1"]
    client = _make_client(gmcp_modules=modules)
    assert client._gmcp_modules == modules


@pytest.mark.asyncio
async def test_gmcp_log_default_false():
    client = _make_client()
    assert client._gmcp_log is False


@pytest.mark.asyncio
async def test_gmcp_log_enabled():
    client = _make_client(gmcp_log=True)
    assert client._gmcp_log is True


@pytest.mark.asyncio
async def test_gmcp_data_on_writer():
    client, _ = _make_connected_client()
    assert client.writer._gmcp_data is client._gmcp_data


@pytest.mark.asyncio
async def test_ext_callback_registered_for_gmcp():
    client, _ = _make_connected_client()
    assert client.writer._ext_callback[GMCP] == client._on_gmcp


@pytest.mark.asyncio
async def test_on_gmcp_stores_data():
    client = _make_client()
    client._on_gmcp("Char.Vitals", {"hp": 100, "maxhp": 100})
    assert client._gmcp_data["Char.Vitals"] == {"hp": 100, "maxhp": 100}


@pytest.mark.asyncio
async def test_on_gmcp_overwrites_previous():
    client = _make_client()
    client._on_gmcp("Room.Info", {"name": "Town Square"})
    client._on_gmcp("Room.Info", {"name": "Dark Forest"})
    assert client._gmcp_data["Room.Info"] == {"name": "Dark Forest"}


@pytest.mark.asyncio
async def test_on_gmcp_merges_partial_dict_update():
    client = _make_client()
    client._on_gmcp("Char.Vitals", {"hp": 100, "maxhp": 100, "sp": 50, "maxsp": 50})
    client._on_gmcp("Char.Vitals", {"hp": 63})
    assert client._gmcp_data["Char.Vitals"] == {
        "hp": 63, "maxhp": 100, "sp": 50, "maxsp": 50,
    }


@pytest.mark.asyncio
async def test_on_gmcp_replaces_non_dict_with_dict():
    client = _make_client()
    client._on_gmcp("Room.Name", "Old Name")
    client._on_gmcp("Room.Name", {"name": "New Place"})
    assert client._gmcp_data["Room.Name"] == {"name": "New Place"}


@pytest.mark.asyncio
async def test_on_gmcp_replaces_dict_with_non_dict():
    client = _make_client()
    client._on_gmcp("Room.Info", {"name": "Town"})
    client._on_gmcp("Room.Info", "plain string")
    assert client._gmcp_data["Room.Info"] == "plain string"


@pytest.mark.asyncio
async def test_on_gmcp_stores_none():
    client = _make_client()
    client._on_gmcp("Core.Goodbye", None)
    assert client._gmcp_data["Core.Goodbye"] is None


@pytest.mark.asyncio
async def test_on_gmcp_logs_debug_by_default():
    client = _make_client()
    with mock.patch.object(client.log, "debug") as mock_debug:
        client._on_gmcp("Char.Vitals", {"hp": 50})
        mock_debug.assert_called_once()


@pytest.mark.asyncio
async def test_on_gmcp_logs_info_when_enabled():
    client = _make_client(gmcp_log=True)
    with mock.patch.object(client.log, "info") as mock_info:
        client._on_gmcp("Char.Vitals", {"hp": 50})
        mock_info.assert_called_once()


@pytest.mark.asyncio
async def test_hello_sent_on_will_gmcp():
    client, transport = _make_connected_client()
    client.writer.always_do = {GMCP}
    transport.data.clear()
    client.writer.handle_will(GMCP)
    data = bytes(transport.data)
    assert b"Core.Hello" in data
    assert b"Core.Supports.Set" in data


@pytest.mark.asyncio
async def test_hello_idempotent():
    client, transport = _make_connected_client()
    client.writer.always_do = {GMCP}
    client.writer.handle_will(GMCP)
    transport.data.clear()
    client.writer.remote_option[GMCP] = True
    client.writer.handle_will(GMCP)
    data = bytes(transport.data)
    assert b"Core.Hello" not in data


@pytest.mark.asyncio
async def test_hello_includes_version():
    from telnetlib3.accessories import get_version
    client, transport = _make_connected_client()
    client.writer.always_do = {GMCP}
    transport.data.clear()
    client.writer.handle_will(GMCP)
    data = bytes(transport.data)
    assert get_version().encode() in data


@pytest.mark.asyncio
async def test_hello_uses_custom_modules():
    modules = ["IRE.Rift 1", "Char 1"]
    client, transport = _make_connected_client(gmcp_modules=modules)
    client.writer.always_do = {GMCP}
    transport.data.clear()
    client.writer.handle_will(GMCP)
    data = bytes(transport.data)
    assert b"IRE.Rift 1" in data


@pytest.mark.asyncio
async def test_no_hello_without_always_do():
    client, transport = _make_connected_client()
    transport.data.clear()
    client.writer.handle_will(GMCP)
    data = bytes(transport.data)
    assert b"Core.Hello" not in data


def test_gmcp_modules_cli_flag():
    parser = _get_argument_parser()
    args = parser.parse_args(["example.com", "--gmcp-modules", "Char 1,Room 1"])
    assert args.gmcp_modules == "Char 1,Room 1"


def test_gmcp_modules_cli_default_none():
    parser = _get_argument_parser()
    args = parser.parse_args(["example.com"])
    assert args.gmcp_modules is None


def test_gmcp_log_cli_flag():
    parser = _get_argument_parser()
    args = parser.parse_args(["example.com", "--gmcp-log"])
    assert args.gmcp_log is True


def test_gmcp_log_cli_default_false():
    parser = _get_argument_parser()
    args = parser.parse_args(["example.com"])
    assert args.gmcp_log is False


def test_transform_args_gmcp_modules():
    from telnetlib3.client import _transform_args
    parser = _get_argument_parser()
    args = parser.parse_args(["example.com", "--gmcp-modules", "Char 1,IRE.Rift 1"])
    result = _transform_args(args)
    assert result["gmcp_modules"] == ["Char 1", "IRE.Rift 1"]


def test_transform_args_gmcp_modules_none():
    from telnetlib3.client import _transform_args
    parser = _get_argument_parser()
    args = parser.parse_args(["example.com"])
    result = _transform_args(args)
    assert result["gmcp_modules"] is None


if sys.platform != "win32":
    from telnetlib3.client_repl import HAS_PROMPT_TOOLKIT

    if HAS_PROMPT_TOOLKIT:
        from telnetlib3.client_repl import PromptToolkitRepl

        def _mock_writer(gmcp_data=None):
            return types.SimpleNamespace(
                will_echo=False,
                log=types.SimpleNamespace(debug=lambda *a, **kw: None),
                get_extra_info=lambda name, default=None: default,
                _gmcp_data=gmcp_data,
            )

        def _toolbar_text(repl):
            """Join toolbar formatted text tuples into a single string."""
            return "".join(t for _, t in repl._get_toolbar())

        def test_toolbar_static_when_no_gmcp():
            w = _mock_writer()
            repl = PromptToolkitRepl(w, logging.getLogger("test"))
            text = _toolbar_text(repl)
            assert isinstance(text, str)

        def test_toolbar_static_when_empty_gmcp():
            w = _mock_writer(gmcp_data={})
            repl = PromptToolkitRepl(w, logging.getLogger("test"))
            text = _toolbar_text(repl)
            assert isinstance(text, str)

        def test_toolbar_shows_vitals():
            gmcp = {"Char.Vitals": {"hp": 100, "maxhp": 200, "mp": 50}}
            w = _mock_writer(gmcp_data=gmcp)
            repl = PromptToolkitRepl(w, logging.getLogger("test"))
            text = _toolbar_text(repl)
            assert "100/200 50%" in text
            assert "50" in text

        def test_toolbar_shows_room_info():
            gmcp = {"Room.Info": {"name": "Castle Entrance"}}
            w = _mock_writer(gmcp_data=gmcp)
            repl = PromptToolkitRepl(w, logging.getLogger("test"))
            text = _toolbar_text(repl)
            assert "Castle Entrance" in text

        def test_toolbar_shows_room_name_string():
            gmcp = {"Room.Name": "Dark Forest"}
            w = _mock_writer(gmcp_data=gmcp)
            repl = PromptToolkitRepl(w, logging.getLogger("test"))
            text = _toolbar_text(repl)
            assert "Dark Forest" in text

        def test_toolbar_includes_static_parts():
            gmcp = {"Char.Vitals": {"hp": 100, "maxhp": 100}}
            w = _mock_writer(gmcp_data=gmcp)
            repl = PromptToolkitRepl(w, logging.getLogger("test"))
            text = _toolbar_text(repl)
            assert "100/100 100%" in text

        def test_toolbar_hp_only_display():
            gmcp = {"Char.Vitals": {"hp": 50}}
            w = _mock_writer(gmcp_data=gmcp)
            repl = PromptToolkitRepl(w, logging.getLogger("test"))
            text = _toolbar_text(repl)
            assert "50" in text
            assert "HP:" in text
