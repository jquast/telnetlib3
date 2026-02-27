"""
Room graph tracking, BFS pathfinding, and SQLite persistence for GMCP Room.Info data.

Incrementally builds a directed graph from GMCP ``Room.Info`` messages,
supports shortest-path search via BFS, and persists per-session room
data to ``~/.local/share/telnetlib3/rooms-{host}_{port}.db``.
"""

from __future__ import annotations

# std imports
import os
import re
import json
import random
import sqlite3
from typing import Any, Optional
from datetime import datetime, timezone
from collections import deque
from dataclasses import field, dataclass

# local
from ._paths import _atomic_write

_EXIT_DIR_RE = re.compile(
    r"\s*"
    r"(?:\{[^}]*\}\s*)?"
    r"\[(?:[nswe]|n[ew]|s[ew]|[a-z]+)"
    r"(?:,(?:[nswe]|n[ew]|s[ew]|[a-z]+))*\]"
    r"\s*$"
)


def strip_exit_dirs(name: str) -> str:
    """Strip trailing exit-direction lists like ``[n,s,w,e]`` from a room name.

    Also handles optional ``{SPICE}``-style tags before the bracket list.
    """
    return _EXIT_DIR_RE.sub("", name)


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
    blocked: bool = False
    home: bool = False
    marked: bool = False


class RoomStore:
    """SQLite-backed room graph with in-memory adjacency cache."""

    def __init__(
        self, db_path: str, read_only: bool = False, session_key: str = ""
    ) -> None:
        """
        Open or create an SQLite room database.

        :param db_path: Path to the ``.db`` file.
        :param read_only: Open in read-only mode (no table creation).
        :param session_key: ``host:port`` identifier stored as metadata.
        """
        dir_path = os.path.dirname(db_path)
        if dir_path:
            os.makedirs(dir_path, exist_ok=True)
        mode = "ro" if read_only else "rwc"
        uri = f"file:{db_path}?mode={mode}"
        self._conn = sqlite3.connect(uri, uri=True)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA temp_store=MEMORY")
        self._conn.execute("PRAGMA cache_size=-4000")  # 4 MB
        self._conn.execute("PRAGMA mmap_size=8388608")  # 8 MB
        if not read_only:
            self._create_tables()
            if session_key:
                self._conn.execute(
                    "INSERT OR REPLACE INTO meta VALUES ('session_key', ?)",
                    (session_key,),
                )
                self._conn.commit()
        self._adj: dict[str, dict[str, str]] = {}
        self._load_adjacency()

    def _create_tables(self) -> None:
        """Create schema tables if they do not exist."""
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS room (
                num TEXT PRIMARY KEY,
                name TEXT NOT NULL DEFAULT '',
                area TEXT NOT NULL DEFAULT '',
                environment TEXT NOT NULL DEFAULT '',
                bookmarked INTEGER NOT NULL DEFAULT 0,
                visit_count INTEGER NOT NULL DEFAULT 0,
                last_visited TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS exit (
                src_num TEXT NOT NULL,
                direction TEXT NOT NULL,
                dst_num TEXT NOT NULL,
                PRIMARY KEY (src_num, direction)
            );
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
        """)
        for col in ("blocked", "home", "marked"):
            try:
                self._conn.execute(
                    f"ALTER TABLE room ADD COLUMN {col} INTEGER NOT NULL DEFAULT 0"
                )
            except sqlite3.OperationalError:
                pass
        self._conn.execute("INSERT OR IGNORE INTO meta VALUES ('version', '1')")
        self._conn.commit()

    def _load_adjacency(self) -> None:
        """Load full adjacency graph into memory for BFS."""
        self._adj.clear()
        try:
            for src, direction, dst in self._conn.execute(
                "SELECT src_num, direction, dst_num FROM exit"
            ):
                self._adj.setdefault(src, {})[direction] = dst
        except sqlite3.OperationalError:
            pass

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()

    def _row_to_room(self, row: tuple[Any, ...]) -> Room:
        """Convert a SELECT row to a :class:`Room`."""
        num = row[0]
        exits = dict(self._adj.get(num, {}))
        return Room(
            num=num,
            name=row[1],
            area=row[2],
            environment=row[3],
            exits=exits,
            bookmarked=bool(row[4]),
            visit_count=row[5],
            last_visited=row[6],
            blocked=bool(row[7]),
            home=bool(row[8]),
            marked=bool(row[9]),
        )

    _ROOM_COLS = (
        "num, name, area, environment, bookmarked, visit_count, last_visited,"
        " blocked, home, marked"
    )

    @property
    def rooms(self) -> dict[str, Room]:
        """
        Return all rooms as a dict (for compatibility).

        Not cached.
        """
        result: dict[str, Room] = {}
        for row in self._conn.execute(f"SELECT {self._ROOM_COLS} FROM room"):
            room = self._row_to_room(row)
            result[room.num] = room
        return result

    def room_summaries(
        self,
    ) -> list[tuple[str, str, str, int, bool, str, bool, bool, bool]]:
        """
        Return lightweight room summary tuples.

        Each tuple is ``(num, name, area, exit_count, bookmarked,
        last_visited, blocked, home, marked)``.

        Counts exits via SQL aggregation instead of materialising
        :class:`Room` objects, which avoids copying exit dicts for every
        room.
        """
        rows = self._conn.execute(
            "SELECT r.num, r.name, r.area, COUNT(e.direction),"
            " r.bookmarked, r.last_visited, r.blocked, r.home, r.marked"
            " FROM room r LEFT JOIN exit e ON r.num = e.src_num"
            " GROUP BY r.num"
        ).fetchall()
        return [
            (r[0], r[1], r[2], r[3], bool(r[4]), r[5],
             bool(r[6]), bool(r[7]), bool(r[8]))
            for r in rows
        ]

    def room_area(self, num: str) -> str:
        """Return the area of a single room, or ``""`` if not found."""
        row = self._conn.execute("SELECT area FROM room WHERE num = ?", (num,)).fetchone()
        return row[0] if row else ""

    def get_room(self, num: str) -> Optional[Room]:
        """
        Get a single room by number.

        :param num: Room number.
        :returns: :class:`Room` or ``None`` if not found.
        """
        row = self._conn.execute(
            f"SELECT {self._ROOM_COLS} FROM room WHERE num = ?", (num,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_room(row)

    def _has_room(self, num: str) -> bool:
        """Return ``True`` if room *num* exists in the database."""
        row = self._conn.execute("SELECT 1 FROM room WHERE num = ?", (num,)).fetchone()
        return row is not None

    def update_room(self, info: dict[str, Any]) -> None:
        """
        Update or create a room from a GMCP ``Room.Info`` payload.

        :param info: GMCP Room.Info dict with at least ``num``.
        """
        num = str(info["num"])
        exits = info.get("exits", {})
        if isinstance(exits, dict):
            exits = {str(k): str(v) for k, v in exits.items() if v}
        else:
            exits = {}

        name = strip_exit_dirs(str(info.get("name", "")))
        area = str(info.get("area", ""))
        environment = str(info.get("environment", ""))
        now = datetime.now(timezone.utc).isoformat()

        self._conn.execute(
            "INSERT INTO room"
            " (num, name, area, environment, visit_count, last_visited)"
            " VALUES (?, ?, ?, ?, 1, ?)"
            " ON CONFLICT(num) DO UPDATE SET"
            " name=excluded.name, area=excluded.area,"
            " environment=excluded.environment,"
            " visit_count=visit_count+1,"
            " last_visited=excluded.last_visited",
            (num, name, area, environment, now),
        )
        self._conn.execute("DELETE FROM exit WHERE src_num = ?", (num,))
        if exits:
            self._conn.executemany(
                "INSERT INTO exit (src_num, direction, dst_num) VALUES (?, ?, ?)",
                [(num, d, dst) for d, dst in exits.items()],
            )
        self._conn.commit()
        self._adj[num] = exits

    _MARKER_COLS = ("bookmarked", "blocked", "home", "marked")

    def set_marker(self, num: str, marker: str) -> bool:
        """
        Toggle a marker on a room, clearing all other markers.

        Markers are mutually exclusive: only one of ``bookmarked``,
        ``blocked``, ``home``, ``marked`` can be set at a time.  If the
        requested marker is already set, all markers are cleared.

        For ``home``, the one-per-area constraint is also enforced.

        :param num: Room number.
        :param marker: One of ``"bookmarked"``, ``"blocked"``, ``"home"``,
            ``"marked"``.
        :returns: New state of *marker*, or ``False`` if room not found.
        """
        if marker not in self._MARKER_COLS:
            raise ValueError(f"unknown marker: {marker!r}")
        row = self._conn.execute(
            f"SELECT {marker}, area FROM room WHERE num = ?", (num,)
        ).fetchone()
        if row is None:
            return False
        new_state = not bool(row[0])
        self._conn.execute(
            "UPDATE room SET bookmarked=0, blocked=0, home=0, marked=0"
            " WHERE num = ?",
            (num,),
        )
        if new_state:
            self._conn.execute(
                f"UPDATE room SET {marker} = 1 WHERE num = ?", (num,)
            )
            if marker == "home":
                area = row[1]
                self._conn.execute(
                    "UPDATE room SET home = 0 WHERE area = ? AND num != ?",
                    (area, num),
                )
        self._conn.commit()
        return new_state

    def toggle_bookmark(self, num: str) -> bool:
        """Toggle bookmark on a room (exclusive with other markers)."""
        return self.set_marker(num, "bookmarked")

    def toggle_blocked(self, num: str) -> bool:
        """Toggle blocked state on a room (exclusive with other markers)."""
        return self.set_marker(num, "blocked")

    def toggle_home(self, num: str) -> bool:
        """Toggle home state on a room (exclusive with other markers)."""
        return self.set_marker(num, "home")

    def toggle_marked(self, num: str) -> bool:
        """Toggle mark on a room (exclusive with other markers)."""
        return self.set_marker(num, "marked")

    def get_home_for_area(self, area: str) -> str | None:
        """
        Return the home room number for the given area.

        :param area: Area name.
        :returns: Room number string, or ``None`` if no home is set.
        """
        row = self._conn.execute(
            "SELECT num FROM room WHERE area = ? AND home = 1", (area,)
        ).fetchone()
        return row[0] if row else None

    def blocked_rooms(self) -> frozenset[str]:
        """Return frozenset of all blocked room numbers."""
        return frozenset(
            row[0]
            for row in self._conn.execute(
                "SELECT num FROM room WHERE blocked = 1"
            )
        )

    def _room_nums(self) -> frozenset[str]:
        """Return the set of all room numbers in the database."""
        return frozenset(row[0] for row in self._conn.execute("SELECT num FROM room"))

    def bfs_distances(
        self, src: str, blocked: frozenset[str] = frozenset()
    ) -> dict[str, int]:
        """
        BFS from *src* returning distance to every reachable room.

        :param src: Source room number.
        :param blocked: Room numbers to treat as impassable.
        :returns: ``{room_num: distance}`` for all reachable rooms.
        """
        known = self._room_nums()
        if src not in known:
            return {}
        distances: dict[str, int] = {src: 0}
        queue: deque[str] = deque([src])
        while queue:
            current = queue.popleft()
            d = distances[current]
            for target in self._adj.get(current, {}).values():
                if target not in distances and target in known and target not in blocked:
                    distances[target] = d + 1
                    queue.append(target)
        return distances

    def find_path(
        self, src: str, dst: str, blocked: frozenset[str] = frozenset()
    ) -> list[str] | None:
        """
        BFS shortest path from *src* to *dst*.

        :param blocked: Room numbers to treat as impassable.
        :returns: List of direction names, or ``None`` if unreachable.
        """
        if src == dst:
            return []
        if not self._has_room(src):
            return None

        visited: set[str] = {src}
        queue: deque[tuple[str, list[str]]] = deque([(src, [])])

        while queue:
            current, path = queue.popleft()
            for direction, target in self._adj.get(current, {}).items():
                if target == dst:
                    return path + [direction]
                if (
                    target not in visited
                    and self._has_room(target)
                    and target not in blocked
                ):
                    visited.add(target)
                    queue.append((target, path + [direction]))

        return None

    def find_path_with_rooms(
        self, src: str, dst: str, blocked: frozenset[str] = frozenset()
    ) -> list[tuple[str, str]] | None:
        """
        BFS shortest path returning ``[(direction, target_room_num), ...]``.

        :param blocked: Room numbers to treat as impassable.
        :returns: List of (direction, expected_room_num) pairs, or ``None``.
        """
        if src == dst:
            return []
        if not self._has_room(src):
            return None

        visited: set[str] = {src}
        queue: deque[tuple[str, list[tuple[str, str]]]] = deque([(src, [])])

        while queue:
            current, path = queue.popleft()
            for direction, target in self._adj.get(current, {}).items():
                if target == dst:
                    return path + [(direction, target)]
                if (
                    target not in visited
                    and self._has_room(target)
                    and target not in blocked
                ):
                    visited.add(target)
                    queue.append((target, path + [(direction, target)]))

        return None

    def find_same_name(self, num: str, limit: int = 99) -> list[Room]:
        """
        Find rooms with the same name as *num*, sorted by least-recently-visited.

        :param num: Room number to match name against.
        :param limit: Maximum results to return.
        :returns: List of matching rooms, excluding *num* itself.
        """
        row = self._conn.execute("SELECT name FROM room WHERE num = ?", (num,)).fetchone()
        if row is None or not row[0]:
            return []
        target_name = row[0]
        rows = self._conn.execute(
            f"SELECT {self._ROOM_COLS}"
            " FROM room WHERE name = ? AND num != ?"
            " ORDER BY last_visited ASC LIMIT ?",
            (target_name, num, limit),
        ).fetchall()
        return [self._row_to_room(r) for r in rows]

    def find_branches(
        self, src: str, limit: int = 99, blocked: frozenset[str] = frozenset()
    ) -> list[tuple[str, str, str]]:
        """
        Find exits from known rooms leading to unvisited or unknown rooms.

        :param src: Source room number to search from.
        :param limit: Maximum number of branches to return.
        :param blocked: Room numbers to treat as impassable.
        :returns: ``[(gateway_room_num, direction, target_num), ...]``
            sorted by BFS distance from *src*.
        """
        if not self._has_room(src):
            return []

        visited: set[str] = {src}
        queue: deque[tuple[str, int]] = deque([(src, 0)])
        branches: list[tuple[int, str, str, str]] = []

        while queue:
            current, dist = queue.popleft()
            for direction, target in self._adj.get(current, {}).items():
                if target in blocked:
                    continue
                target_vc = self._conn.execute(
                    "SELECT visit_count FROM room WHERE num = ?", (target,)
                ).fetchone()
                if target_vc is None or target_vc[0] == 0:
                    branches.append((dist, current, direction, target))
                elif target not in visited:
                    visited.add(target)
                    queue.append((target, dist + 1))

        branches.sort(key=lambda b: b[0])
        # Shuffle branches within each distance tier so autodiscover
        # does not deterministically prefer one direction.
        shuffled: list[tuple[int, str, str, str]] = []
        i = 0
        while i < len(branches):
            dist = branches[i][0]
            j = i
            while j < len(branches) and branches[j][0] == dist:
                j += 1
            tier = branches[i:j]
            random.shuffle(tier)
            shuffled.extend(tier)
            i = j
        return [(gw, d, t) for _, gw, d, t in shuffled[:limit]]

    def search(self, query: str) -> list[Room]:
        """
        Case-insensitive substring search on room name and area.

        :param query: Search string.
        :returns: Matching rooms sorted bookmarked-first, then by name.
        """
        q = f"%{query}%"
        rows = self._conn.execute(
            f"SELECT {self._ROOM_COLS}"
            " FROM room WHERE name LIKE ? COLLATE NOCASE"
            " OR area LIKE ? COLLATE NOCASE",
            (q, q),
        ).fetchall()
        results = [self._row_to_room(r) for r in rows]
        results.sort(key=lambda r: (not r.bookmarked, r.name.lower()))
        return results


RoomGraph = RoomStore


def _xdg_data_dir() -> str:
    """Return XDG data directory for telnetlib3."""
    from ._paths import DATA_DIR

    return DATA_DIR


def _session_file_path(prefix: str, session_key: str, ext: str = "") -> str:
    """Return a per-session file path under the XDG data directory."""
    from ._paths import safe_session_slug

    return os.path.join(_xdg_data_dir(), f"{prefix}{safe_session_slug(session_key)}{ext}")


def rooms_path(session_key: str) -> str:
    """Return path to room graph SQLite DB for *session_key* (``host:port``)."""
    return _session_file_path("rooms-", session_key, ".db")


def current_room_path(session_key: str) -> str:
    """Return path to current room number file for *session_key*."""
    return _session_file_path(".current-room-", session_key)


def fasttravel_path(session_key: str) -> str:
    """Return path to fast travel command file for *session_key*."""
    return _session_file_path(".fasttravel-", session_key)


def prefs_path(session_key: str) -> str:
    """Return path to preferences JSON for *session_key* (``host:port``)."""
    return _session_file_path("prefs-", session_key, ".json")


def load_prefs(session_key: str) -> dict[str, bool | str]:
    """
    Load per-session preferences from disk.

    :param session_key: Session identifier (``host:port``).
    :returns: Dict of preference values (booleans and strings).
    """
    path = prefs_path(session_key)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            result: dict[str, bool | str] = {}
            for k, v in data.items():
                if isinstance(v, str):
                    result[str(k)] = v
                else:
                    result[str(k)] = bool(v)
            return result
    except (OSError, ValueError):
        pass
    return {}


def save_prefs(session_key: str, prefs: dict[str, bool | str]) -> None:
    """
    Atomically save per-session preferences to disk.

    :param session_key: Session identifier (``host:port``).
    :param prefs: Dict of preference values (booleans and strings).
    """
    path = prefs_path(session_key)
    _atomic_write(path, json.dumps(prefs, separators=(",", ":")))


def write_current_room(path: str, room_num: str) -> None:
    """
    Write the current room number to a small file for TUI subprocess.

    :param path: File path.
    :param room_num: Current room number string.
    """
    _atomic_write(path, room_num)


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


def write_fasttravel(path: str, steps: list[tuple[str, str]], slow: bool = False) -> None:
    """
    Write fast travel steps to disk for the REPL to read.

    :param path: File path.
    :param steps: List of (direction, expected_room_num) pairs.
    :param slow: If ``True``, all autoreplies (including exclusive) fire.
    """
    data = {"steps": steps, "slow": slow}
    _atomic_write(path, json.dumps(data))


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
    except (OSError, ValueError):
        return [], False
