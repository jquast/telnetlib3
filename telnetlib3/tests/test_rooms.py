"""Tests for :mod:`telnetlib3.rooms` room graph, pathfinding, and persistence."""

from __future__ import annotations

import json
import os

import pytest

from telnetlib3.rooms import (
    Room,
    RoomGraph,
    load_rooms,
    save_rooms,
    rooms_path,
    current_room_path,
    fasttravel_path,
    write_current_room,
    read_current_room,
    write_fasttravel,
    read_fasttravel,
)


class TestRoomGraph:

    def test_update_room_new(self) -> None:
        g = RoomGraph()
        g.update_room({"num": "100", "name": "Town Square", "area": "midgaard",
                        "environment": "outdoors", "exits": {"north": "101", "south": "102"}})
        assert "100" in g.rooms
        r = g.rooms["100"]
        assert r.name == "Town Square"
        assert r.area == "midgaard"
        assert r.environment == "outdoors"
        assert r.exits == {"north": "101", "south": "102"}
        assert r.visit_count == 1
        assert r.last_visited != ""

    def test_update_room_existing(self) -> None:
        g = RoomGraph()
        g.update_room({"num": "100", "name": "Town Square", "area": "midgaard",
                        "exits": {"north": "101"}})
        g.update_room({"num": "100", "name": "Town Square (rebuilt)", "area": "midgaard",
                        "exits": {"north": "101", "east": "103"}})
        assert g.rooms["100"].name == "Town Square (rebuilt)"
        assert g.rooms["100"].exits == {"north": "101", "east": "103"}
        assert g.rooms["100"].visit_count == 2

    def test_update_room_numeric_id(self) -> None:
        g = RoomGraph()
        g.update_room({"num": 42, "name": "Numeric Room"})
        assert "42" in g.rooms

    def test_update_room_missing_optional_fields(self) -> None:
        g = RoomGraph()
        g.update_room({"num": "1"})
        r = g.rooms["1"]
        assert r.name == ""
        assert r.area == ""
        assert r.exits == {}

    def test_update_room_invalid_exits_ignored(self) -> None:
        g = RoomGraph()
        g.update_room({"num": "1", "exits": "not-a-dict"})
        assert g.rooms["1"].exits == {}


class TestFindPath:

    @staticmethod
    def _build_linear_graph() -> RoomGraph:
        g = RoomGraph()
        g.update_room({"num": "A", "exits": {"east": "B"}})
        g.update_room({"num": "B", "exits": {"east": "C", "west": "A"}})
        g.update_room({"num": "C", "exits": {"west": "B"}})
        return g

    def test_direct_neighbor(self) -> None:
        g = self._build_linear_graph()
        assert g.find_path("A", "B") == ["east"]

    def test_multi_hop(self) -> None:
        g = self._build_linear_graph()
        assert g.find_path("A", "C") == ["east", "east"]

    def test_reverse_path(self) -> None:
        g = self._build_linear_graph()
        assert g.find_path("C", "A") == ["west", "west"]

    def test_same_room(self) -> None:
        g = self._build_linear_graph()
        assert g.find_path("A", "A") == []

    def test_no_path(self) -> None:
        g = RoomGraph()
        g.update_room({"num": "A", "exits": {"east": "B"}})
        g.update_room({"num": "B", "exits": {}})
        g.update_room({"num": "C", "exits": {"west": "B"}})
        assert g.find_path("A", "C") is None

    def test_unknown_src(self) -> None:
        g = RoomGraph()
        assert g.find_path("X", "Y") is None

    def test_one_way_exits(self) -> None:
        g = RoomGraph()
        g.update_room({"num": "A", "exits": {"down": "B"}})
        g.update_room({"num": "B", "exits": {}})
        assert g.find_path("A", "B") == ["down"]
        assert g.find_path("B", "A") is None

    def test_cycle_handling(self) -> None:
        g = RoomGraph()
        g.update_room({"num": "A", "exits": {"east": "B"}})
        g.update_room({"num": "B", "exits": {"east": "C"}})
        g.update_room({"num": "C", "exits": {"east": "A"}})
        assert g.find_path("A", "C") == ["east", "east"]

    def test_target_not_in_graph_but_reachable(self) -> None:
        g = RoomGraph()
        g.update_room({"num": "A", "exits": {"east": "B"}})
        assert g.find_path("A", "B") == ["east"]

    def test_find_path_with_rooms(self) -> None:
        g = RoomGraph()
        g.update_room({"num": "A", "exits": {"east": "B"}})
        g.update_room({"num": "B", "exits": {"north": "C"}})
        g.update_room({"num": "C", "exits": {}})
        result = g.find_path_with_rooms("A", "C")
        assert result == [("east", "B"), ("north", "C")]

    def test_find_path_with_rooms_same(self) -> None:
        g = RoomGraph()
        g.update_room({"num": "A", "exits": {}})
        assert g.find_path_with_rooms("A", "A") == []


class TestBookmarkAndSearch:

    def test_toggle_bookmark(self) -> None:
        g = RoomGraph()
        g.update_room({"num": "1", "name": "Room One"})
        assert not g.rooms["1"].bookmarked
        g.toggle_bookmark("1")
        assert g.rooms["1"].bookmarked
        g.toggle_bookmark("1")
        assert not g.rooms["1"].bookmarked

    def test_toggle_bookmark_missing_room(self) -> None:
        g = RoomGraph()
        g.toggle_bookmark("999")

    def test_search_by_name(self) -> None:
        g = RoomGraph()
        g.update_room({"num": "1", "name": "Dark Forest", "area": "wild"})
        g.update_room({"num": "2", "name": "Town Square", "area": "town"})
        g.update_room({"num": "3", "name": "Forest Path", "area": "wild"})
        results = g.search("forest")
        assert len(results) == 2
        assert all("forest" in r.name.lower() for r in results)

    def test_search_by_area(self) -> None:
        g = RoomGraph()
        g.update_room({"num": "1", "name": "Room A", "area": "caladan"})
        g.update_room({"num": "2", "name": "Room B", "area": "arrakis"})
        results = g.search("caladan")
        assert len(results) == 1
        assert results[0].num == "1"

    def test_search_case_insensitive(self) -> None:
        g = RoomGraph()
        g.update_room({"num": "1", "name": "DARK FOREST"})
        assert len(g.search("dark")) == 1
        assert len(g.search("DARK")) == 1

    def test_search_bookmarked_first(self) -> None:
        g = RoomGraph()
        g.update_room({"num": "1", "name": "Alpha Room"})
        g.update_room({"num": "2", "name": "Beta Room"})
        g.toggle_bookmark("2")
        results = g.search("room")
        assert results[0].num == "2"
        assert results[1].num == "1"

    def test_search_empty_query(self) -> None:
        g = RoomGraph()
        g.update_room({"num": "1", "name": "Room A"})
        g.update_room({"num": "2", "name": "Room B"})
        assert len(g.search("")) == 2


class TestPersistence:

    def test_save_load_roundtrip(self, tmp_path: Any) -> None:
        g = RoomGraph()
        g.update_room({"num": "100", "name": "Town", "area": "mid",
                        "environment": "indoor", "exits": {"n": "101"}})
        g.toggle_bookmark("100")

        path = str(tmp_path / "rooms.json")
        save_rooms(path, g)

        loaded = load_rooms(path)
        assert "100" in loaded.rooms
        r = loaded.rooms["100"]
        assert r.name == "Town"
        assert r.area == "mid"
        assert r.environment == "indoor"
        assert r.exits == {"n": "101"}
        assert r.bookmarked is True
        assert r.visit_count == 1

    def test_save_creates_directory(self, tmp_path: Any) -> None:
        path = str(tmp_path / "sub" / "dir" / "rooms.json")
        save_rooms(path, RoomGraph())
        assert os.path.exists(path)

    def test_load_empty_graph(self, tmp_path: Any) -> None:
        path = str(tmp_path / "rooms.json")
        save_rooms(path, RoomGraph())
        loaded = load_rooms(path)
        assert len(loaded.rooms) == 0

    def test_save_is_valid_json(self, tmp_path: Any) -> None:
        g = RoomGraph()
        g.update_room({"num": "1", "name": "Test"})
        path = str(tmp_path / "rooms.json")
        save_rooms(path, g)
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert data["version"] == 1
        assert "1" in data["rooms"]


class TestPathHelpers:

    def test_rooms_path_format(self) -> None:
        p = rooms_path("example.com:4000")
        assert p.endswith("rooms-example.com_4000.json")
        assert "telnetlib3" in p

    def test_current_room_path_format(self) -> None:
        p = current_room_path("host:23")
        assert p.endswith(".current-room-host_23")

    def test_fasttravel_path_format(self) -> None:
        p = fasttravel_path("host:23")
        assert p.endswith(".fasttravel-host_23")


class TestCurrentRoomFile:

    def test_write_read_roundtrip(self, tmp_path: Any) -> None:
        path = str(tmp_path / ".current-room")
        write_current_room(path, "abc123")
        assert read_current_room(path) == "abc123"

    def test_read_missing_file(self, tmp_path: Any) -> None:
        path = str(tmp_path / "nonexistent")
        assert read_current_room(path) == ""


class TestFasttravelFile:

    def test_write_read_roundtrip(self, tmp_path: Any) -> None:
        path = str(tmp_path / ".fasttravel")
        steps = [("north", "101"), ("east", "102")]
        write_fasttravel(path, steps)
        result_steps, result_slow = read_fasttravel(path)
        assert result_steps == steps
        assert result_slow is False
        assert not os.path.exists(path)

    def test_write_read_slow_mode(self, tmp_path: Any) -> None:
        path = str(tmp_path / ".fasttravel")
        steps = [("north", "101")]
        write_fasttravel(path, steps, slow=True)
        result_steps, result_slow = read_fasttravel(path)
        assert result_steps == steps
        assert result_slow is True

    def test_read_missing_file(self, tmp_path: Any) -> None:
        path = str(tmp_path / "nonexistent")
        assert read_fasttravel(path) == ([], False)
