"""Tests for :mod:`telnetlib3.rooms` room graph, pathfinding, and persistence."""

from __future__ import annotations

# std imports
import os
import json
from typing import Any

# 3rd party
import pytest

# local
from telnetlib3.rooms import (
    Room,
    RoomGraph,
    RoomStore,
    load_prefs,
    prefs_path,
    rooms_path,
    save_prefs,
    _xdg_data_dir,
    fasttravel_path,
    read_fasttravel,
    write_fasttravel,
    current_room_path,
    read_current_room,
    _session_file_path,
    write_current_room,
)


@pytest.fixture()
def store(tmp_path: Any) -> RoomStore:
    """Return a fresh RoomStore backed by a temporary database."""
    db = str(tmp_path / "rooms.db")
    s = RoomStore(db)
    yield s
    s.close()


def _build_linear(store: RoomStore) -> None:
    store.update_room({"num": "A", "exits": {"east": "B"}})
    store.update_room({"num": "B", "exits": {"east": "C", "west": "A"}})
    store.update_room({"num": "C", "exits": {"west": "B"}})


def _build_graph(store: RoomStore) -> None:
    store.update_room({"num": "A", "exits": {"east": "B", "north": "X"}})
    store.update_room({"num": "B", "exits": {"east": "C", "west": "A"}})
    store.update_room({"num": "C", "exits": {"west": "B"}})


def test_update_room_new(store: RoomStore) -> None:
    store.update_room(
        {
            "num": "100",
            "name": "Town Square",
            "area": "midgaard",
            "environment": "outdoors",
            "exits": {"north": "101", "south": "102"},
        }
    )
    r = store.get_room("100")
    assert r is not None
    assert r.name == "Town Square"
    assert r.area == "midgaard"
    assert r.environment == "outdoors"
    assert r.exits == {"north": "101", "south": "102"}
    assert r.visit_count == 1
    assert r.last_visited


def test_update_room_existing(store: RoomStore) -> None:
    store.update_room(
        {"num": "100", "name": "Town Square", "area": "midgaard", "exits": {"north": "101"}}
    )
    store.update_room(
        {
            "num": "100",
            "name": "Town Square (rebuilt)",
            "area": "midgaard",
            "exits": {"north": "101", "east": "103"},
        }
    )
    r = store.get_room("100")
    assert r is not None
    assert r.name == "Town Square (rebuilt)"
    assert r.exits == {"north": "101", "east": "103"}
    assert r.visit_count == 2


def test_update_room_numeric_id(store: RoomStore) -> None:
    store.update_room({"num": 42, "name": "Numeric Room"})
    assert store.get_room("42") is not None


def test_update_room_missing_optional_fields(store: RoomStore) -> None:
    store.update_room({"num": "1"})
    r = store.get_room("1")
    assert r is not None
    assert not r.name
    assert not r.area
    assert r.exits == {}


def test_update_room_invalid_exits_ignored(store: RoomStore) -> None:
    store.update_room({"num": "1", "exits": "not-a-dict"})
    r = store.get_room("1")
    assert r is not None
    assert r.exits == {}


def test_rooms_property(store: RoomStore) -> None:
    store.update_room({"num": "1", "name": "A"})
    store.update_room({"num": "2", "name": "B"})
    all_rooms = store.rooms
    assert len(all_rooms) == 2
    assert "1" in all_rooms
    assert "2" in all_rooms


def test_get_room_missing(store: RoomStore) -> None:
    assert store.get_room("999") is None


def test_close(tmp_path: Any) -> None:
    db = str(tmp_path / "close_test.db")
    s = RoomStore(db)
    s.update_room({"num": "1", "name": "Test"})
    s.close()
    s2 = RoomStore(db, read_only=True)
    assert s2.get_room("1") is not None
    s2.close()


def test_direct_neighbor(store: RoomStore) -> None:
    _build_linear(store)
    assert store.find_path("A", "B") == ["east"]


def test_multi_hop(store: RoomStore) -> None:
    _build_linear(store)
    assert store.find_path("A", "C") == ["east", "east"]


def test_reverse_path(store: RoomStore) -> None:
    _build_linear(store)
    assert store.find_path("C", "A") == ["west", "west"]


def test_same_room(store: RoomStore) -> None:
    _build_linear(store)
    assert store.find_path("A", "A") == []


def test_no_path(store: RoomStore) -> None:
    store.update_room({"num": "A", "exits": {"east": "B"}})
    store.update_room({"num": "B", "exits": {}})
    store.update_room({"num": "C", "exits": {"west": "B"}})
    assert store.find_path("A", "C") is None


def test_find_path_unknown_src(store: RoomStore) -> None:
    assert store.find_path("X", "Y") is None


def test_one_way_exits(store: RoomStore) -> None:
    store.update_room({"num": "A", "exits": {"down": "B"}})
    store.update_room({"num": "B", "exits": {}})
    assert store.find_path("A", "B") == ["down"]
    assert store.find_path("B", "A") is None


def test_cycle_handling(store: RoomStore) -> None:
    store.update_room({"num": "A", "exits": {"east": "B"}})
    store.update_room({"num": "B", "exits": {"east": "C"}})
    store.update_room({"num": "C", "exits": {"east": "A"}})
    assert store.find_path("A", "C") == ["east", "east"]


def test_target_not_in_graph_but_reachable(store: RoomStore) -> None:
    store.update_room({"num": "A", "exits": {"east": "B"}})
    assert store.find_path("A", "B") == ["east"]


def test_find_path_with_rooms(store: RoomStore) -> None:
    store.update_room({"num": "A", "exits": {"east": "B"}})
    store.update_room({"num": "B", "exits": {"north": "C"}})
    store.update_room({"num": "C", "exits": {}})
    result = store.find_path_with_rooms("A", "C")
    assert result == [("east", "B"), ("north", "C")]


def test_find_path_with_rooms_same(store: RoomStore) -> None:
    store.update_room({"num": "A", "exits": {}})
    assert store.find_path_with_rooms("A", "A") == []


def test_bfs_distances(store: RoomStore) -> None:
    _build_linear(store)
    d = store.bfs_distances("A")
    assert d == {"A": 0, "B": 1, "C": 2}


def test_bfs_distances_unreachable(store: RoomStore) -> None:
    store.update_room({"num": "A", "exits": {}})
    store.update_room({"num": "B", "exits": {}})
    d = store.bfs_distances("A")
    assert d == {"A": 0}


def test_bfs_distances_unknown_src(store: RoomStore) -> None:
    assert not store.bfs_distances("X")


def test_basic_same_name(store: RoomStore) -> None:
    store._conn.execute(
        "INSERT INTO room (num, name, last_visited) VALUES (?, ?, ?)",
        ("1", "A dusty road", "2024-01-01"),
    )
    store._conn.execute(
        "INSERT INTO room (num, name, last_visited) VALUES (?, ?, ?)",
        ("2", "A dusty road", "2024-01-03"),
    )
    store._conn.execute(
        "INSERT INTO room (num, name, last_visited) VALUES (?, ?, ?)",
        ("3", "A dusty road", "2024-01-02"),
    )
    store._conn.execute(
        "INSERT INTO room (num, name, last_visited) VALUES (?, ?, ?)",
        ("4", "Town Square", "2024-01-01"),
    )
    store._conn.commit()
    result = store.find_same_name("1")
    assert len(result) == 2
    assert result[0].num == "3"
    assert result[1].num == "2"


def test_excludes_self(store: RoomStore) -> None:
    store._conn.execute(
        "INSERT INTO room (num, name, last_visited) VALUES (?, ?, ?)", ("1", "Forest", "2024-01-01")
    )
    store._conn.execute(
        "INSERT INTO room (num, name, last_visited) VALUES (?, ?, ?)", ("2", "Forest", "2024-01-02")
    )
    store._conn.commit()
    result = store.find_same_name("1")
    assert all(r.num != "1" for r in result)


def test_missing_room(store: RoomStore) -> None:
    assert store.find_same_name("999") == []


def test_empty_name(store: RoomStore) -> None:
    store._conn.execute("INSERT INTO room (num, name) VALUES (?, ?)", ("1", ""))
    store._conn.execute("INSERT INTO room (num, name) VALUES (?, ?)", ("2", ""))
    store._conn.commit()
    assert store.find_same_name("1") == []


def test_no_matches(store: RoomStore) -> None:
    store.update_room({"num": "1", "name": "Unique Room"})
    store.update_room({"num": "2", "name": "Different Room"})
    assert store.find_same_name("1") == []


def test_never_visited_sort_first(store: RoomStore) -> None:
    store._conn.execute(
        "INSERT INTO room (num, name, last_visited) VALUES (?, ?, ?)", ("1", "Road", "2024-01-01")
    )
    store._conn.execute(
        "INSERT INTO room (num, name, last_visited) VALUES (?, ?, ?)", ("2", "Road", "2024-06-01")
    )
    store._conn.execute(
        "INSERT INTO room (num, name, last_visited) VALUES (?, ?, ?)", ("3", "Road", "")
    )
    store._conn.commit()
    result = store.find_same_name("1")
    assert result[0].num == "3"
    assert result[1].num == "2"


def test_limit(store: RoomStore) -> None:
    store._conn.execute(
        "INSERT INTO room (num, name, last_visited) VALUES (?, ?, ?)", ("0", "Road", "2024-01-01")
    )
    for i in range(1, 30):
        store._conn.execute(
            "INSERT INTO room (num, name, last_visited) VALUES (?, ?, ?)",
            (str(i), "Road", f"2024-01-{i:02d}"),
        )
    store._conn.commit()
    result = store.find_same_name("0", limit=5)
    assert len(result) == 5


def test_default_limit_99(store: RoomStore) -> None:
    store._conn.execute(
        "INSERT INTO room (num, name, last_visited) VALUES (?, ?, ?)", ("0", "Road", "2024-01-01")
    )
    for i in range(1, 120):
        store._conn.execute(
            "INSERT INTO room (num, name, last_visited) VALUES (?, ?, ?)",
            (str(i), "Road", f"2024-01-{i % 28 + 1:02d}"),
        )
    store._conn.commit()
    result = store.find_same_name("0")
    assert len(result) == 99


def test_toggle_bookmark(store: RoomStore) -> None:
    store.update_room({"num": "1", "name": "Room One"})
    r = store.get_room("1")
    assert r is not None
    assert not r.bookmarked
    assert store.toggle_bookmark("1") is True
    r = store.get_room("1")
    assert r is not None
    assert r.bookmarked
    assert store.toggle_bookmark("1") is False
    r = store.get_room("1")
    assert r is not None
    assert not r.bookmarked


def test_toggle_bookmark_missing_room(store: RoomStore) -> None:
    assert store.toggle_bookmark("999") is False


def test_search_by_name(store: RoomStore) -> None:
    store.update_room({"num": "1", "name": "Dark Forest", "area": "wild"})
    store.update_room({"num": "2", "name": "Town Square", "area": "town"})
    store.update_room({"num": "3", "name": "Forest Path", "area": "wild"})
    results = store.search("forest")
    assert len(results) == 2
    assert all("forest" in r.name.lower() for r in results)


def test_search_by_area(store: RoomStore) -> None:
    store.update_room({"num": "1", "name": "Room A", "area": "caladan"})
    store.update_room({"num": "2", "name": "Room B", "area": "arrakis"})
    results = store.search("caladan")
    assert len(results) == 1
    assert results[0].num == "1"


def test_search_case_insensitive(store: RoomStore) -> None:
    store.update_room({"num": "1", "name": "DARK FOREST"})
    assert len(store.search("dark")) == 1
    assert len(store.search("DARK")) == 1


def test_search_bookmarked_first(store: RoomStore) -> None:
    store.update_room({"num": "1", "name": "Alpha Room"})
    store.update_room({"num": "2", "name": "Beta Room"})
    store.toggle_bookmark("2")
    results = store.search("room")
    assert results[0].num == "2"
    assert results[1].num == "1"


def test_search_empty_query(store: RoomStore) -> None:
    store.update_room({"num": "1", "name": "Room A"})
    store.update_room({"num": "2", "name": "Room B"})
    assert len(store.search("")) == 2


def test_alias_is_room_store() -> None:
    assert RoomGraph is RoomStore


def test_rooms_path_format() -> None:
    p = rooms_path("example.com:4000")
    basename = os.path.basename(p)
    assert basename.startswith("rooms-")
    assert basename.endswith(".db")
    assert "telnetlib3" in p


def test_current_room_path_format() -> None:
    p = current_room_path("host:23")
    assert os.path.basename(p).startswith(".current-room-")


def test_fasttravel_path_format() -> None:
    p = fasttravel_path("host:23")
    assert os.path.basename(p).startswith(".fasttravel-")


@pytest.mark.parametrize(
    "malicious_key",
    ["../../etc/passwd:80", "../../../tmp/evil:23", "/absolute/path:99", "..%2f..%2fetc/shadow:22"],
)
def test_session_file_path_traversal(malicious_key: str) -> None:
    result = _session_file_path("rooms-", malicious_key)
    assert ".." not in result
    assert os.path.dirname(result) == _xdg_data_dir()


def test_current_room_file_write_read_roundtrip(tmp_path: Any) -> None:
    path = str(tmp_path / ".current-room")
    write_current_room(path, "abc123")
    assert read_current_room(path) == "abc123"


def test_current_room_file_read_missing_file(tmp_path: Any) -> None:
    path = str(tmp_path / "nonexistent")
    assert not read_current_room(path)


def test_fasttravel_file_write_read_roundtrip(tmp_path: Any) -> None:
    path = str(tmp_path / ".fasttravel")
    steps = [("north", "101"), ("east", "102")]
    write_fasttravel(path, steps)
    result_steps, result_slow = read_fasttravel(path)
    assert result_steps == steps
    assert result_slow is False
    assert not os.path.exists(path)


def test_write_read_slow_mode(tmp_path: Any) -> None:
    path = str(tmp_path / ".fasttravel")
    steps = [("north", "101")]
    write_fasttravel(path, steps, slow=True)
    result_steps, result_slow = read_fasttravel(path)
    assert result_steps == steps
    assert result_slow is True


def test_fasttravel_file_read_missing_file(tmp_path: Any) -> None:
    path = str(tmp_path / "nonexistent")
    assert read_fasttravel(path) == ([], False)


def test_finds_frontier_exit(store: RoomStore) -> None:
    _build_graph(store)
    branches = store.find_branches("A")
    dirs = [(gw, d) for gw, d, _ in branches]
    assert ("A", "north") in dirs


def test_unknown_target_is_frontier(store: RoomStore) -> None:
    store.update_room({"num": "A", "exits": {"east": "B"}})
    branches = store.find_branches("A")
    assert len(branches) == 1
    assert branches[0] == ("A", "east", "B")


def test_unvisited_target_is_frontier(store: RoomStore) -> None:
    store.update_room({"num": "A", "exits": {"east": "B"}})
    store._conn.execute(
        "INSERT INTO room (num, name, visit_count) VALUES (?, ?, ?)", ("B", "Empty", 0)
    )
    store._conn.commit()
    branches = store.find_branches("A")
    assert len(branches) == 1
    assert branches[0][2] == "B"


def test_visited_target_not_frontier(store: RoomStore) -> None:
    store.update_room({"num": "A", "exits": {"east": "B"}})
    store.update_room({"num": "B", "exits": {"west": "A"}})
    branches = store.find_branches("A")
    assert len(branches) == 0


def test_sorted_by_distance(store: RoomStore) -> None:
    store.update_room({"num": "A", "exits": {"east": "B"}})
    store.update_room({"num": "B", "exits": {"east": "C", "north": "Y"}})
    store.update_room({"num": "C", "exits": {"north": "Z"}})
    branches = store.find_branches("A")
    gateways = [gw for gw, _, _ in branches]
    assert gateways.index("B") < gateways.index("C")


def test_empty_graph(store: RoomStore) -> None:
    assert store.find_branches("A") == []


def test_find_branches_unknown_src(store: RoomStore) -> None:
    store.update_room({"num": "A", "exits": {"east": "B"}})
    assert store.find_branches("Z") == []


def test_find_branches_limit(store: RoomStore) -> None:
    store.update_room({"num": "A", "exits": {"n": "X1", "s": "X2", "e": "X3"}})
    branches = store.find_branches("A", limit=2)
    assert len(branches) == 2


def test_no_duplicates(store: RoomStore) -> None:
    store.update_room({"num": "A", "exits": {"east": "B"}})
    store.update_room({"num": "B", "exits": {"east": "C", "west": "A"}})
    branches = store.find_branches("A")
    pairs = [(gw, d) for gw, d, _ in branches]
    assert len(pairs) == len(set(pairs))


def test_prefs_path_format() -> None:
    p = prefs_path("example.com:4000")
    basename = os.path.basename(p)
    assert basename.startswith("prefs-")
    assert basename.endswith(".json")
    assert "telnetlib3" in p


def test_save_load_roundtrip(tmp_path: Any, monkeypatch: Any) -> None:
    monkeypatch.setattr("telnetlib3.rooms._xdg_data_dir", lambda: str(tmp_path))
    prefs = {"skip_autowander_confirm": True, "skip_autodiscover_confirm": False}
    save_prefs("host:1234", prefs)
    loaded = load_prefs("host:1234")
    assert loaded["skip_autowander_confirm"] is True
    assert loaded["skip_autodiscover_confirm"] is False


def test_load_missing_file() -> None:
    result = load_prefs("nonexistent:9999")
    assert result == {}


def test_save_overwrites(tmp_path: Any, monkeypatch: Any) -> None:
    monkeypatch.setattr("telnetlib3.rooms._xdg_data_dir", lambda: str(tmp_path))
    save_prefs("h:1", {"skip_autowander_confirm": False})
    save_prefs("h:1", {"skip_autowander_confirm": True})
    loaded = load_prefs("h:1")
    assert loaded["skip_autowander_confirm"] is True
