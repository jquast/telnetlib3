"""
Room graph tracking, BFS pathfinding, and persistence for GMCP Room.Info data.

Incrementally builds a directed graph from GMCP ``Room.Info`` messages,
supports shortest-path search via BFS, and persists per-session room
data to ``~/.local/share/telnetlib3/rooms-{host}_{port}.json``.
"""

from __future__ import annotations

import os
import json
import tempfile
from collections import deque
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any


@dataclass
class Room:
    """A single room in the GMCP room graph."""

    num: str
    name: str = ""
    area: str = ""
    environment: str = ""
    exits: dict[str, str] = field(default_factory=dict)
    bookmarked: bool = False
    visit_count: int = 0
    last_visited: str = ""


class RoomGraph:
    """Directed graph of rooms built from GMCP Room.Info messages."""

    def __init__(self) -> None:
        self.rooms: dict[str, Room] = {}

    def update_room(self, info: dict[str, Any]) -> None:
        """
        Update or create a room from a GMCP ``Room.Info`` payload.

        :param info: GMCP Room.Info dict with at least ``num``.
        """
        num = str(info["num"])
        exits = info.get("exits", {})
        if isinstance(exits, dict):
            exits = {str(k): str(v) for k, v in exits.items()}
        else:
            exits = {}

        if num in self.rooms:
            room = self.rooms[num]
            room.name = str(info.get("name", room.name))
            room.area = str(info.get("area", room.area))
            room.environment = str(info.get("environment", room.environment))
            room.exits = exits
            room.visit_count += 1
            room.last_visited = datetime.now(timezone.utc).isoformat()
        else:
            self.rooms[num] = Room(
                num=num,
                name=str(info.get("name", "")),
                area=str(info.get("area", "")),
                environment=str(info.get("environment", "")),
                exits=exits,
                visit_count=1,
                last_visited=datetime.now(timezone.utc).isoformat(),
            )

    def find_path(self, src: str, dst: str) -> list[str] | None:
        """
        BFS shortest path from *src* to *dst*.

        :returns: List of direction names, or ``None`` if unreachable.
        """
        if src == dst:
            return []
        if src not in self.rooms:
            return None

        visited: set[str] = {src}
        queue: deque[tuple[str, list[str]]] = deque()
        queue.append((src, []))

        while queue:
            current, path = queue.popleft()
            room = self.rooms.get(current)
            if room is None:
                continue
            for direction, target in room.exits.items():
                if target == dst:
                    return path + [direction]
                if target not in visited and target in self.rooms:
                    visited.add(target)
                    queue.append((target, path + [direction]))

        return None

    def find_path_with_rooms(self, src: str, dst: str) -> list[tuple[str, str]] | None:
        """
        BFS shortest path returning ``[(direction, target_room_num), ...]``.

        Used by fast travel to verify arrival at each step.

        :returns: List of (direction, expected_room_num) pairs, or ``None``.
        """
        if src == dst:
            return []
        if src not in self.rooms:
            return None

        visited: set[str] = {src}
        queue: deque[tuple[str, list[tuple[str, str]]]] = deque()
        queue.append((src, []))

        while queue:
            current, path = queue.popleft()
            room = self.rooms.get(current)
            if room is None:
                continue
            for direction, target in room.exits.items():
                if target == dst:
                    return path + [(direction, target)]
                if target not in visited and target in self.rooms:
                    visited.add(target)
                    queue.append((target, path + [(direction, target)]))

        return None

    def toggle_bookmark(self, num: str) -> None:
        """Toggle the bookmark flag on a room."""
        if num in self.rooms:
            self.rooms[num].bookmarked = not self.rooms[num].bookmarked

    def search(self, query: str) -> list[Room]:
        """
        Case-insensitive substring search on room name and area.

        :param query: Search string.
        :returns: Matching rooms sorted bookmarked-first, then by name.
        """
        q = query.lower()
        results = [
            r for r in self.rooms.values()
            if q in r.name.lower() or q in r.area.lower()
        ]
        results.sort(key=lambda r: (not r.bookmarked, r.name.lower()))
        return results


def _xdg_data_dir() -> str:
    """Return XDG data directory for telnetlib3."""
    xdg = os.environ.get(
        "XDG_DATA_HOME", os.path.join(os.path.expanduser("~"), ".local", "share")
    )
    return os.path.join(xdg, "telnetlib3")


def rooms_path(session_key: str) -> str:
    """Return path to room graph JSON for *session_key* (``host:port``)."""
    safe = session_key.replace(":", "_")
    return os.path.join(_xdg_data_dir(), f"rooms-{safe}.json")


def current_room_path(session_key: str) -> str:
    """Return path to current room number file for *session_key*."""
    safe = session_key.replace(":", "_")
    return os.path.join(_xdg_data_dir(), f".current-room-{safe}")


def fasttravel_path(session_key: str) -> str:
    """Return path to fast travel command file for *session_key*."""
    safe = session_key.replace(":", "_")
    return os.path.join(_xdg_data_dir(), f".fasttravel-{safe}")


def load_rooms(path: str) -> RoomGraph:
    """
    Load a room graph from JSON file.

    :param path: Path to rooms JSON file.
    :returns: Populated :class:`RoomGraph`.
    """
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    graph = RoomGraph()
    rooms_data = data.get("rooms", {})
    for num, rdata in rooms_data.items():
        graph.rooms[str(num)] = Room(
            num=str(rdata.get("num", num)),
            name=rdata.get("name", ""),
            area=rdata.get("area", ""),
            environment=rdata.get("environment", ""),
            exits=rdata.get("exits", {}),
            bookmarked=rdata.get("bookmarked", False),
            visit_count=rdata.get("visit_count", 0),
            last_visited=rdata.get("last_visited", ""),
        )
    return graph


def save_rooms(path: str, graph: RoomGraph) -> None:
    """
    Atomically save a room graph to JSON file.

    Uses a temporary file and :func:`os.replace` for crash safety.

    :param path: Target path.
    :param graph: Room graph to persist.
    """
    data = {
        "version": 1,
        "rooms": {num: asdict(room) for num, room in graph.rooms.items()},
    }
    dir_path = os.path.dirname(path)
    os.makedirs(dir_path, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=dir_path, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, separators=(",", ":"))
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_current_room(path: str, room_num: str) -> None:
    """
    Write the current room number to a small file for TUI subprocess.

    :param path: File path.
    :param room_num: Current room number string.
    """
    dir_path = os.path.dirname(path)
    os.makedirs(dir_path, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=dir_path, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(room_num)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_current_room(path: str) -> str:
    """
    Read the current room number from disk.

    :param path: File path written by :func:`write_current_room`.
    :returns: Room number string, or empty string if unavailable.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except (OSError, ValueError):
        return ""


def write_fasttravel(
    path: str, steps: list[tuple[str, str]], slow: bool = False
) -> None:
    """
    Write fast travel steps to disk for the REPL to read.

    :param path: File path.
    :param steps: List of (direction, expected_room_num) pairs.
    :param slow: If ``True``, all autoreplies (including exclusive) fire.
    """
    data = {"steps": steps, "slow": slow}
    dir_path = os.path.dirname(path)
    os.makedirs(dir_path, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=dir_path, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_fasttravel(path: str) -> tuple[list[tuple[str, str]], bool]:
    """
    Read and delete fast travel steps from disk.

    :param path: File path written by :func:`write_fasttravel`.
    :returns: Tuple of (steps, slow) where steps is a list of
        (direction, expected_room_num) pairs and slow indicates
        whether exclusive autoreplies should fire.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        os.unlink(path)
        if isinstance(data, dict):
            steps = [(str(d), str(r)) for d, r in data.get("steps", [])]
            slow = bool(data.get("slow", False))
            return steps, slow
        # Legacy format: bare list
        return [(str(d), str(r)) for d, r in data], False
    except (OSError, ValueError, json.JSONDecodeError):
        return [], False
