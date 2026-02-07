"""Smoke tests for the MUD server demo (bin/server_mud.py)."""

# std imports
import os
import sys

# 3rd party
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "bin"))
# 3rd party
import server_mud as mud  # pylint: disable=import-error,wrong-import-position

# local
from telnetlib3.telopt import GMCP, MSDP, MSSP, WILL


class MudMockWriter:
    """Mock writer for MUD command tests."""

    def __init__(self):
        self.written = []
        self._closing = False
        self._gmcp = []
        self._msdp = []
        self._iac_calls = []

    def write(self, data):
        self.written.append(data)

    def send_gmcp(self, package, data=None):
        self._gmcp.append((package, data))

    def send_msdp(self, data):
        self._msdp.append(data)

    def send_mssp(self, data):
        pass

    def set_ext_callback(self, opt, callback):
        pass

    def iac(self, *args):
        self._iac_calls.append(args)

    def get_extra_info(self, key, default=None):
        return default

    def close(self):
        self._closing = True

    def is_closing(self):
        return self._closing

    @property
    def output(self):
        return "".join(self.written)


@pytest.mark.asyncio
async def test_dispatch_look():
    w = MudMockWriter()
    p = mud.Player("Alice")
    cmds = mud.Commands(w, p)
    result = await cmds.dispatch("look")
    assert result is True
    assert "The Rusty Tavern" in w.output


@pytest.mark.asyncio
async def test_dispatch_move():
    w = MudMockWriter()
    p = mud.Player("Alice")
    cmds = mud.Commands(w, p)
    result = await cmds.dispatch("north")
    assert result is True
    assert p.room == "market"


@pytest.mark.asyncio
async def test_dispatch_quit():
    w = MudMockWriter()
    p = mud.Player("Alice")
    cmds = mud.Commands(w, p)
    result = await cmds.dispatch("quit")
    assert result is False


@pytest.mark.asyncio
async def test_dispatch_unknown_command():
    w = MudMockWriter()
    p = mud.Player("Alice")
    cmds = mud.Commands(w, p)
    result = await cmds.dispatch("xyzzy")
    assert result is True
    assert "Unknown command" in w.output


def test_room_info_has_numeric_ids():
    """Room.Info GMCP uses numeric IDs for Mudlet mapper compatibility."""
    w = MudMockWriter()
    p = mud.Player("Alice")
    mud.sessions[w] = p
    try:
        mud.send_room_gmcp(w, p)
    finally:
        mud.sessions.pop(w, None)
    assert len(w._gmcp) == 1
    pkg, data = w._gmcp[0]
    assert pkg == "Room.Info"
    assert isinstance(data["num"], int)
    assert "area" in data
    assert "environment" in data
    for dest_id in data["exits"].values():
        assert isinstance(dest_id, int)


def test_shell_negotiates_will_gmcp():
    """Server offers GMCP/MSDP/MSSP with WILL, not DO."""
    w = MudMockWriter()
    w.iac(WILL, GMCP)
    w.iac(WILL, MSDP)
    w.iac(WILL, MSSP)
    assert (WILL, GMCP) in w._iac_calls
    assert (WILL, MSDP) in w._iac_calls
    assert (WILL, MSSP) in w._iac_calls


def test_get_msdp_var_health():
    p = mud.Player("Alice")
    p.health = 75
    result = mud.get_msdp_var(p, "HEALTH")
    assert result == {"HEALTH": "75"}


def test_get_msdp_var_room_table():
    p = mud.Player("Alice")
    result = mud.get_msdp_var(p, "ROOM")
    room = result["ROOM"]
    assert room["VNUM"] == "1"
    assert room["NAME"] == "The Rusty Tavern"
    assert room["AREA"] == "town"
    assert room["TERRAIN"] == "Indoor"
    assert "n" in room["EXITS"]
    assert "e" in room["EXITS"]
    assert room["EXITS"]["n"] == "2"


def test_get_msdp_var_room_exits_short_form():
    p = mud.Player("Alice")
    p.room = "market"
    result = mud.get_msdp_var(p, "ROOM")
    exits = result["ROOM"]["EXITS"]
    assert "s" in exits
    assert "w" in exits
    assert "south" not in exits


def test_get_msdp_var_character_name():
    p = mud.Player("TestHero")
    result = mud.get_msdp_var(p, "CHARACTER_NAME")
    assert result == {"CHARACTER_NAME": "TestHero"}


def test_get_msdp_var_unknown():
    p = mud.Player("Alice")
    assert mud.get_msdp_var(p, "BOGUS") is None


def test_get_msdp_var_server_id():
    p = mud.Player("Alice")
    result = mud.get_msdp_var(p, "SERVER_ID")
    assert result == {"SERVER_ID": mud.SERVER_NAME}


def test_on_msdp_list_commands():
    w = MudMockWriter()
    p = mud.Player("Alice")
    mud.sessions[w] = p
    try:
        mud.on_msdp(w, {"LIST": "COMMANDS"})
    finally:
        mud.sessions.pop(w, None)
    assert len(w._msdp) == 1
    assert "COMMANDS" in w._msdp[0]
    assert "LIST" in w._msdp[0]["COMMANDS"]
    assert "SEND" in w._msdp[0]["COMMANDS"]
    assert "REPORT" in w._msdp[0]["COMMANDS"]


def test_on_msdp_list_reportable_variables():
    w = MudMockWriter()
    p = mud.Player("Alice")
    mud.sessions[w] = p
    try:
        mud.on_msdp(w, {"LIST": "REPORTABLE_VARIABLES"})
    finally:
        mud.sessions.pop(w, None)
    assert len(w._msdp) == 1
    reported = w._msdp[0]["REPORTABLE_VARIABLES"]
    assert "HEALTH" in reported
    assert "ROOM" in reported


def test_on_msdp_list_lists():
    w = MudMockWriter()
    p = mud.Player("Alice")
    mud.sessions[w] = p
    try:
        mud.on_msdp(w, {"LIST": "LISTS"})
    finally:
        mud.sessions.pop(w, None)
    assert len(w._msdp) == 1
    lists = w._msdp[0]["LISTS"]
    assert "COMMANDS" in lists
    assert "REPORTABLE_VARIABLES" in lists


def test_on_msdp_send():
    w = MudMockWriter()
    p = mud.Player("Alice")
    p.health = 80
    mud.sessions[w] = p
    try:
        mud.on_msdp(w, {"SEND": "HEALTH"})
    finally:
        mud.sessions.pop(w, None)
    assert len(w._msdp) == 1
    assert w._msdp[0]["HEALTH"] == "80"


def test_on_msdp_report_adds_to_set():
    w = MudMockWriter()
    p = mud.Player("Alice")
    mud.sessions[w] = p
    try:
        mud.on_msdp(w, {"REPORT": "HEALTH"})
    finally:
        mud.sessions.pop(w, None)
    assert "HEALTH" in p.msdp_reported
    assert len(w._msdp) == 1
    assert "HEALTH" in w._msdp[0]


def test_on_msdp_unreport_removes_from_set():
    w = MudMockWriter()
    p = mud.Player("Alice")
    p.msdp_reported.add("HEALTH")
    mud.sessions[w] = p
    try:
        mud.on_msdp(w, {"UNREPORT": "HEALTH"})
    finally:
        mud.sessions.pop(w, None)
    assert "HEALTH" not in p.msdp_reported


def test_on_msdp_reset_clears_reported():
    w = MudMockWriter()
    p = mud.Player("Alice")
    p.msdp_reported = {"HEALTH", "MANA", "ROOM"}
    mud.sessions[w] = p
    try:
        mud.on_msdp(w, {"RESET": ""})
    finally:
        mud.sessions.pop(w, None)
    assert len(p.msdp_reported) == 0


def test_push_msdp_reported_sends_only_subscribed():
    w = MudMockWriter()
    p = mud.Player("Alice")
    p.msdp_reported = {"HEALTH", "MANA"}
    mud.push_msdp_reported(w, p)
    assert len(w._msdp) == 1
    assert "HEALTH" in w._msdp[0]
    assert "MANA" in w._msdp[0]
    assert "ROOM" not in w._msdp[0]


def test_push_msdp_reported_empty_set_no_send():
    w = MudMockWriter()
    p = mud.Player("Alice")
    mud.push_msdp_reported(w, p)
    assert len(w._msdp) == 0


def test_send_vitals_no_msdp_without_report():
    w = MudMockWriter()
    p = mud.Player("Alice")
    mud.send_vitals(w, p)
    assert len(w._gmcp) == 1
    assert len(w._msdp) == 0


def test_on_msdp_no_session():
    w = MudMockWriter()
    mud.on_msdp(w, {"LIST": "COMMANDS"})
    assert len(w._msdp) == 0


def test_on_msdp_report_room_table_format():
    w = MudMockWriter()
    p = mud.Player("Alice")
    mud.sessions[w] = p
    try:
        mud.on_msdp(w, {"REPORT": "ROOM"})
    finally:
        mud.sessions.pop(w, None)
    assert "ROOM" in p.msdp_reported
    room = w._msdp[0]["ROOM"]
    assert isinstance(room, dict)
    assert room["VNUM"] == "1"
    assert room["NAME"] == "The Rusty Tavern"
    assert isinstance(room["EXITS"], dict)
