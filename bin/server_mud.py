#!/usr/bin/env python
"""Mini-MUD server demo with combat, GMCP/MSDP/MSSP.

Usage::

    $ python bin/server_mud.py
    $ telnet localhost 6023
"""

# std imports
import json
import time
import random
import asyncio
import logging
import argparse
import unicodedata
from typing import Any

# local
import telnetlib3
from telnetlib3.telopt import GMCP, MSDP, MSSP, WILL
from telnetlib3.server_shell import readline2

log = logging.getLogger("mud")


SERVER_NAME = "Mini-MUD Demo"
START_ROOM = "tavern"
IDLE_TIMEOUT = 120
FIST_DAMAGE = (3, 7)
MAX_HEALTH = 100
MAX_MANA = 50
HEAL_AMOUNT = 5
HEAL_INTERVAL = 2
ATTACK_COOLDOWN = 2
DODGE_DURATION = 2
DODGE_CHANCE = 0.9
BANNED_WORDS = ["cisco", "admin", "root"]
MSSP_DATA: dict[str, Any] = {
    "NAME": SERVER_NAME,
    "CODEBASE": "telnetlib3",
    "LANGUAGE": ["English"],
    "GAMEPLAY": ["Adventure", "Combat"],
}


ROOM_IDS: dict[str, int] = {"tavern": 1, "market": 2, "smithy": 3, "temple": 4, "forest": 5}
EXIT_SHORT: dict[str, str] = {"north": "n", "south": "s", "east": "e", "west": "w"}
MSDP_COMMANDS: list[str] = ["LIST", "SEND", "REPORT", "UNREPORT", "RESET"]
MSDP_REPORTABLE: list[str] = [
    "CHARACTER_NAME",
    "SERVER_ID",
    "HEALTH",
    "HEALTH_MAX",
    "MANA",
    "MANA_MAX",
    "ROOM",
    "WEAPON",
    "OPPONENT_NAME",
    "OPPONENT_HEALTH",
    "OPPONENT_HEALTH_MAX",
]
MSDP_CONFIGURABLE: list[str] = []

ROOMS: dict[str, dict[str, Any]] = {
    "tavern": {
        "name": "The Rusty Tavern",
        "sanctuary": True,
        "environment": "Indoor",
        "desc": "A cozy tavern with wooden tables and a roaring fireplace."
        " A warm glow surrounds you -- no violence is permitted here.",
        "exits": {"north": "market", "east": "smithy"},
    },
    "market": {
        "name": "Dirty Cobble Path",
        "environment": "City",
        "desc": "Uneven cobblestones stretch ahead, slick with grime and puddles.",
        "exits": {"south": "tavern", "west": "temple"},
    },
    "smithy": {
        "name": "The Smithy",
        "environment": "Indoor",
        "desc": "Hot coals glow red as the blacksmith hammers away at an anvil.",
        "exits": {"west": "tavern"},
    },
    "temple": {
        "name": "Muddy Bend",
        "environment": "Field",
        "desc": "The path curves around a steep hill, thick mud sucking at every step.",
        "exits": {"east": "market", "north": "forest"},
    },
    "forest": {
        "name": "Dark Forest",
        "environment": "Forest",
        "desc": "Twisted trees block out the sun in this mysterious woodland.",
        "exits": {"south": "temple"},
    },
}


class Weapon:  # pylint: disable=too-few-public-methods
    """A weapon that can be held or placed in a room."""

    def __init__(self, name: str, damage: tuple[int, int], start_room: str) -> None:
        self.name = name
        self.damage = damage
        self.start_room = start_room
        self.location: str | None = start_room
        self.holder: "Player | None" = None

    @property
    def damage_display(self) -> str:
        """Format damage range for display."""
        return f"{self.damage[0]}-{self.damage[1]}"


class Player:  # pylint: disable=too-few-public-methods
    """A connected player."""

    def __init__(self, name: str = "Adventurer") -> None:
        self.name = name
        self.health = self.max_health = MAX_HEALTH
        self.mana = self.max_mana = MAX_MANA
        self.room = START_ROOM
        self.weapon: Weapon | None = None
        self.is_dodging = False
        self.debug_mode = False
        self.last_activity = time.monotonic()
        self.msdp_reported: set[str] = set()


WEAPONS = [
    Weapon("Rusty Sword", (10, 15), "smithy"),
    Weapon("Battle Axe", (15, 20), "forest"),
    Weapon("Magic Staff", (12, 18), "temple"),
    Weapon("Dagger", (5, 10), "market"),
]

sessions: dict[Any, Player] = {}


def strip_control_chars(text: str) -> str:
    """Remove control characters from *text*."""
    return "".join(c for c in text if unicodedata.category(c) != "Cc")


def tell(writer: Any, *lines: str) -> None:
    """Write *lines* to *writer* with CRLF wrapping."""
    writer.write("\r\n" + "\r\n".join(lines) + "\r\n")


def send_message(writer: Any, text: str) -> None:
    """Send a feedback message with a trailing blank line."""
    tell(writer, text, "")


def find_writer(player: Player) -> Any:
    """Return the writer for *player*, or ``None``."""
    return next((w for w, p in sessions.items() if p is player), None)


def players_in_room(room_key: str, exclude: Player | None = None) -> list[Player]:
    """Return active players in *room_key*."""
    return [p for p in sessions.values() if p.room == room_key and p is not exclude]


def weapons_in_room(room_key: str) -> list[Weapon]:
    """Return weapons lying in *room_key*."""
    return [w for w in WEAPONS if w.location == room_key]


def drop_weapon(player: Player) -> str | None:
    """Drop player's weapon into their room; return name."""
    if wp := player.weapon:
        wp.location = player.room
        wp.holder = None
        player.weapon = None
        return wp.name
    return None


def broadcast_room(source: Any, room_key: str, text: str, exclude: list[Any] | None = None) -> None:
    """Send *text* to all writers in *room_key* except *source*."""
    log.info("[%s] %s", ROOMS[room_key]["name"], text)
    msg = f"\r\n{text}\r\n> "
    skip = set(exclude or ())
    for w, p in sessions.items():
        if w is not source and w not in skip and p.room == room_key:
            w.write(msg)


def update_room_all(room_key: str) -> None:
    """Push GMCP Room.Info to everyone in *room_key*."""
    for w, p in sessions.items():
        if p.room == room_key:
            send_room_gmcp(w, p)


def announce_all(writer: Any, msg: str) -> None:
    """Send *msg* to all connected players."""
    for w in sessions:
        if w is not writer:
            w.write(f"\r\n{msg}\r\n> ")
    send_message(writer, msg)


def resolve_target(writer: Any, prefix: str, candidates: list[Any], err: str) -> Any:
    """Resolve an abbreviated target name."""
    matches = [c for c in candidates if c.name.lower().startswith(prefix.lower())]
    if len(matches) == 1:
        return matches[0]
    if matches:
        send_message(writer, f"Did you mean: {', '.join(m.name for m in matches)}?")
    else:
        send_message(writer, err)
    return None


def send_vitals(writer: Any, player: Player) -> None:
    """Push Char.Vitals GMCP and MSDP reported variables."""
    wn = player.weapon.name if player.weapon else "Fists"
    st = "dodging" if player.is_dodging else "ready"
    writer.send_gmcp(
        "Char.Vitals",
        {
            "hp": player.health,
            "maxhp": player.max_health,
            "mp": player.mana,
            "maxmp": player.max_mana,
            "weapon": wn,
            "status": st,
        },
    )
    push_msdp_reported(writer, player)


def send_room_gmcp(writer: Any, player: Player) -> None:
    """Push Room.Info GMCP."""
    room = ROOMS[player.room]
    people = [
        {"name": p.name, "hp": p.health, "maxhp": p.max_health}
        for p in players_in_room(player.room, exclude=player)
    ]
    writer.send_gmcp(
        "Room.Info",
        {
            "num": ROOM_IDS[player.room],
            "name": room["name"],
            "area": "town",
            "environment": room.get("environment", "Urban"),
            "exits": {d: ROOM_IDS[r] for d, r in room["exits"].items()},
            "players": people,
            "items": [w.name for w in weapons_in_room(player.room)],
        },
    )


def send_status(writer: Any, msg: str) -> None:
    """Push Char.Status GMCP message."""
    if writer:
        writer.send_gmcp("Char.Status", {"message": msg})


def on_gmcp(writer: Any, package: str, data: Any) -> None:
    """Handle incoming GMCP from a client."""
    player = sessions.get(writer)
    if not player or not player.debug_mode:
        return
    writer.write(f"[DEBUG GMCP] {package}: {json.dumps(data)}\r\n")


def get_msdp_var(  # pylint: disable=too-many-return-statements
    player: Player, var: str
) -> dict[str, Any] | None:
    """Return MSDP value dict for *var*, or ``None`` if unknown."""
    if var == "CHARACTER_NAME":
        return {"CHARACTER_NAME": player.name}
    if var == "SERVER_ID":
        return {"SERVER_ID": SERVER_NAME}
    if var == "HEALTH":
        return {"HEALTH": str(player.health)}
    if var == "HEALTH_MAX":
        return {"HEALTH_MAX": str(player.max_health)}
    if var == "MANA":
        return {"MANA": str(player.mana)}
    if var == "MANA_MAX":
        return {"MANA_MAX": str(player.max_mana)}
    if var == "ROOM":
        room = ROOMS[player.room]
        exits = {EXIT_SHORT.get(d, d): str(ROOM_IDS[dest]) for d, dest in room["exits"].items()}
        return {
            "ROOM": {
                "VNUM": str(ROOM_IDS[player.room]),
                "NAME": room["name"],
                "AREA": "town",
                "TERRAIN": room.get("environment", "Urban"),
                "EXITS": exits,
            }
        }
    if var == "WEAPON":
        return {"WEAPON": player.weapon.name if player.weapon else "Fists"}
    if var == "OPPONENT_NAME":
        return {"OPPONENT_NAME": ""}
    if var == "OPPONENT_HEALTH":
        return {"OPPONENT_HEALTH": "0"}
    if var == "OPPONENT_HEALTH_MAX":
        return {"OPPONENT_HEALTH_MAX": "0"}
    return None


def push_msdp_reported(writer: Any, player: Player) -> None:
    """Push all MSDP variables in *player*'s report set."""
    if not player.msdp_reported:
        return
    merged: dict[str, Any] = {}
    for var in player.msdp_reported:
        val = get_msdp_var(player, var)
        if val is not None:
            merged.update(val)
    if merged:
        writer.send_msdp(merged)


def on_msdp(writer: Any, variables: dict[str, Any]) -> None:
    """Handle incoming MSDP from a client."""
    player = sessions.get(writer)
    if not player:
        return
    if player.debug_mode:
        writer.write(f"[DEBUG MSDP] {variables!r}\r\n")
    for cmd, val in variables.items():
        if cmd == "LIST":
            _msdp_list(writer, val)
        elif cmd == "SEND":
            _msdp_send(writer, player, val)
        elif cmd == "REPORT":
            _msdp_report(writer, player, val)
        elif cmd == "UNREPORT":
            _msdp_unreport(player, val)
        elif cmd == "RESET":
            player.msdp_reported.clear()


def _msdp_list(writer: Any, what: str | list[str]) -> None:
    """Handle MSDP LIST command."""
    if isinstance(what, list):
        for item in what:
            _msdp_list(writer, item)
        return
    if what == "COMMANDS":
        writer.send_msdp({"COMMANDS": MSDP_COMMANDS})
    elif what == "LISTS":
        writer.send_msdp(
            {"LISTS": ["COMMANDS", "LISTS", "REPORTABLE_VARIABLES", "CONFIGURABLE_VARIABLES"]}
        )
    elif what == "REPORTABLE_VARIABLES":
        writer.send_msdp({"REPORTABLE_VARIABLES": MSDP_REPORTABLE})
    elif what == "CONFIGURABLE_VARIABLES":
        writer.send_msdp({"CONFIGURABLE_VARIABLES": MSDP_CONFIGURABLE})


def _msdp_send(writer: Any, player: Player, what: str | list[str]) -> None:
    """Handle MSDP SEND -- one-time variable fetch."""
    names = [what] if isinstance(what, str) else what
    merged: dict[str, Any] = {}
    for var in names:
        val = get_msdp_var(player, var)
        if val is not None:
            merged.update(val)
    if merged:
        writer.send_msdp(merged)


def _msdp_report(writer: Any, player: Player, what: str | list[str]) -> None:
    """Handle MSDP REPORT -- subscribe to variable updates."""
    names = [what] if isinstance(what, str) else what
    merged: dict[str, Any] = {}
    for var in names:
        if var in MSDP_REPORTABLE:
            player.msdp_reported.add(var)
            val = get_msdp_var(player, var)
            if val is not None:
                merged.update(val)
    if merged:
        writer.send_msdp(merged)


def _msdp_unreport(player: Player, what: str | list[str]) -> None:
    """Handle MSDP UNREPORT -- unsubscribe from variable updates."""
    names = [what] if isinstance(what, str) else what
    for var in names:
        player.msdp_reported.discard(var)


def show_room(writer: Any, player: Player) -> None:
    """Display the current room to *writer*."""
    room = ROOMS[player.room]
    lines = [room["name"], room["desc"]]
    if item_names := [w.name for w in weapons_in_room(player.room)]:
        lines.append(f"Items here: {', '.join(item_names)}")
    others = [p.name for p in sessions.values() if p.room == player.room and p is not player]
    if others:
        lines.append(f"Players here: {', '.join(others)}")
    lines.append(f"Exits: {', '.join(room['exits'])}")
    tell(writer, *lines, "")


def process_death(writer: Any, player: Player, killer: Player) -> None:
    """Handle a player dying -- respawn in tavern."""
    death_room = player.room
    kw = find_writer(killer)
    dropped = drop_weapon(player)
    tell(writer, "", "You have been slain!")
    send_status(writer, "You have died")
    if dropped:
        writer.send_gmcp("Char.Items.Remove", {"name": dropped})
    if kw:
        send_message(kw, f"{player.name} has been slain!")
        send_status(kw, f"{player.name} has been slain")
    broadcast_room(writer, death_room, f"{player.name} has been slain!", exclude=[kw] if kw else [])
    player.room = START_ROOM
    player.health = player.max_health
    player.is_dodging = False
    tell(writer, "You materialize in a burst of light.")
    send_vitals(writer, player)
    show_room(writer, player)
    send_room_gmcp(writer, player)
    writer.write("> ")
    broadcast_room(writer, player.room, f"{player.name} materializes" " in a burst of light.")
    update_room_all(death_room)
    update_room_all(START_ROOM)


class Commands:
    """Command dispatcher -- ``do_*`` methods are discovered dynamically."""

    ALIASES: dict[str, str] = {
        "n": "north",
        "s": "south",
        "e": "east",
        "w": "west",
        "l": "look",
        "i": "inventory",
    }

    def __init__(self, writer: Any, player: Player) -> None:
        self.writer = writer
        self.player = player
        self._cmd_map: dict[str, Any] = {
            name[3:]: getattr(self, name)
            for name in sorted(dir(self))
            if name.startswith("do_") and callable(getattr(self, name))
        }

    def _resolve(self, word: str) -> str | list[str]:
        """Resolve an abbreviated command word."""
        word = self.ALIASES.get(word, word)
        if word in self._cmd_map:
            return word
        matches = [c for c in self._cmd_map if c.startswith(word)]
        return matches[0] if len(matches) == 1 else matches

    async def dispatch(self, text: str) -> bool:
        """Parse and dispatch a command.

        :returns: ``False`` to disconnect, ``True`` to continue.
        """
        if not text:
            return True

        w = self.writer
        p = self.player

        if any(b in text for b in BANNED_WORDS[:2]):
            announce_all(w, f"*DING!* {p.name}" " is laughed out of the tavern!")
            return False
        if BANNED_WORDS[-1] in text:
            announce_all(
                w,
                f"{p.name} rings the bell and yells,"
                f" '{BANNED_WORDS[-1]}!', and everybody"
                " in the tavern breaks out in laughter!",
            )
            return True

        p.last_activity = time.monotonic()

        if text.startswith("pick up "):
            verb, argument = "get", text[8:].strip()
        elif text.startswith("pick "):
            verb, argument = "get", text[5:].strip()
        else:
            parts = text.split(maxsplit=1)
            argument = parts[1].strip() if len(parts) > 1 else ""
            resolved = self._resolve(parts[0])
            if isinstance(resolved, list):
                if resolved:
                    send_message(w, f"Did you mean: {', '.join(resolved)}?")
                else:
                    send_message(w, "Unknown command. Type 'help' for commands.")
                return True
            verb = resolved

        method = self._cmd_map.get(verb)
        if method is None:
            send_message(w, "Unknown command. Type 'help' for commands.")
            return True
        return await method(argument)

    # -- commands -------------------------------------------------------

    async def do_help(self, argument: str) -> bool:
        """Show available commands."""
        if argument:
            resolved = self._resolve(argument)
            if isinstance(resolved, str):
                doc = self._cmd_map[resolved].__doc__
                send_message(self.writer, f"{resolved}: {doc}")
            else:
                send_message(self.writer, "Unknown command.")
        else:
            lines = ["Commands:"]
            for name, method in self._cmd_map.items():
                lines.append(f"  {name:15s} - {method.__doc__}")
            lines += ["", "Aliases: n/s/e/w, l=look, i=inventory", "Commands can be abbreviated."]
            tell(self.writer, *lines)
        return True

    async def do_look(self, *_args: str) -> bool:
        """Look around the current room."""
        show_room(self.writer, self.player)
        send_room_gmcp(self.writer, self.player)
        return True

    async def do_north(self, *_args: str) -> bool:
        """Go north."""
        return await self._move("north")

    async def do_south(self, *_args: str) -> bool:
        """Go south."""
        return await self._move("south")

    async def do_east(self, *_args: str) -> bool:
        """Go east."""
        return await self._move("east")

    async def do_west(self, *_args: str) -> bool:
        """Go west."""
        return await self._move("west")

    async def do_stats(self, *_args: str) -> bool:
        """View your stats."""
        p = self.player
        wn = p.weapon.name if p.weapon else "Fists"
        room_name = ROOMS[p.room]["name"]
        tell(
            self.writer,
            f"Name: {p.name}" f"  HP: {p.health}/{p.max_health}" f"  MP: {p.mana}/{p.max_mana}",
            f"Weapon: {wn}  Room: {room_name}",
            "",
        )
        send_vitals(self.writer, p)
        return True

    async def do_inventory(self, *_args: str) -> bool:
        """Check held items."""
        if wp := self.player.weapon:
            tell(self.writer, f"Holding: {wp.name}" f" ({wp.damage_display} damage)", "")
            self.writer.send_gmcp(
                "Char.Items.List",
                [{"name": wp.name, "type": "weapon", "damage": wp.damage_display}],
            )
        else:
            tell(self.writer, "You are empty-handed.", "")
            self.writer.send_gmcp("Char.Items.List", [])
        return True

    async def do_say(self, argument: str) -> bool:
        """Speak to the room."""
        if not argument:
            send_message(self.writer, "Say what?")
        else:
            send_message(self.writer, f'You say, "{argument}"')
            broadcast_room(self.writer, self.player.room, f'{self.player.name} says, "{argument}"')
        return True

    async def do_debug(self, argument: str) -> bool:
        """Toggle debug mode."""
        self.player.debug_mode = {"on": True, "off": False}.get(
            argument, not self.player.debug_mode
        )
        state = "on" if self.player.debug_mode else "off"
        send_message(self.writer, f"Debug mode {state}.")
        return True

    async def do_get(self, argument: str) -> bool:
        """Pick up a weapon."""
        w, p = self.writer, self.player
        if not argument:
            send_message(w, "Pick up what?")
            return True
        pfx = argument.lower()
        found = [wp for wp in WEAPONS if wp.location == p.room and wp.name.lower().startswith(pfx)]
        if not found:
            send_message(w, "Nothing like that here.")
        elif len(found) > 1:
            names = ", ".join(wp.name for wp in found)
            send_message(w, f"Did you mean: {names}?")
        elif p.weapon:
            send_message(w, "Drop your weapon first.")
        else:
            weapon = found[0]
            weapon.location = None
            weapon.holder = p
            p.weapon = weapon
            send_message(w, f"You pick up the {weapon.name}.")
            w.send_gmcp(
                "Char.Items.Add",
                {"name": weapon.name, "type": "weapon", "damage": weapon.damage_display},
            )
            send_vitals(w, p)
            broadcast_room(w, p.room, f"{p.name} picks up" f" the {weapon.name}.")
            update_room_all(p.room)
        return True

    async def do_drop(self, *_args: str) -> bool:
        """Drop your weapon."""
        w, p = self.writer, self.player
        if not (weapon := p.weapon):
            send_message(w, "You're not holding anything.")
            return True
        weapon.location = p.room
        weapon.holder = None
        p.weapon = None
        send_message(w, f"You drop the {weapon.name}.")
        w.send_gmcp("Char.Items.Remove", {"name": weapon.name})
        send_vitals(w, p)
        broadcast_room(w, p.room, f"{p.name} drops the {weapon.name}.")
        update_room_all(p.room)
        return True

    async def do_attack(self, argument: str) -> bool:
        """Attack another player (2s cooldown)."""
        w, p = self.writer, self.player
        if not argument:
            send_message(w, "Attack whom?")
            return True
        if ROOMS[p.room].get("sanctuary"):
            send_message(w, "You cannot attack in this sanctuary!")
            return True
        candidates = [o for o in sessions.values() if o.room == p.room and o is not p]
        target = resolve_target(w, argument, candidates, "No such player here.")
        if target:
            await self._attack(target)
        return True

    async def do_dodge(self, *_args: str) -> bool:
        """Dodge attacks for 2 seconds."""
        w, p = self.writer, self.player
        if p.is_dodging:
            send_message(w, "You're already dodging!")
            return True
        p.is_dodging = True
        tell(w, "You enter a defensive stance!")
        send_vitals(w, p)
        broadcast_room(w, p.room, f"{p.name} assumes a defensive stance.")
        await asyncio.sleep(DODGE_DURATION)
        p.is_dodging = False
        tell(w, "Your dodge ends.")
        send_vitals(w, p)
        return True

    async def do_quit(self, *_args: str) -> bool:
        """Leave the game."""
        self.writer.write("Farewell, adventurer!\r\n")
        broadcast_room(self.writer, self.player.room, f"{self.player.name} has left.")
        return False

    # -- helpers --------------------------------------------------------

    async def _move(self, direction: str) -> bool:
        """Move player in *direction*."""
        w, p = self.writer, self.player
        if p.is_dodging:
            send_message(w, "You can't move while dodging!")
            return True
        room = ROOMS[p.room]
        if direction not in room["exits"]:
            send_message(w, "You can't go that way.")
            return True
        old = p.room
        broadcast_room(w, p.room, f"{p.name} leaves {direction}.")
        p.room = room["exits"][direction]
        tell(w, f"You go {direction}.")
        show_room(w, p)
        send_room_gmcp(w, p)
        send_vitals(w, p)
        broadcast_room(w, p.room, f"{p.name} arrives.")
        update_room_all(old)
        return True

    async def _attack(self, target: Player) -> None:
        """Execute an attack against *target*."""
        w, p = self.writer, self.player
        tw = find_writer(target)
        wp = p.weapon
        dmg = random.randint(*(wp.damage if wp else FIST_DAMAGE))
        wn = wp.name if wp else "fists"
        if target.is_dodging and random.random() < DODGE_CHANCE:
            tell(w, f"{target.name} dodges your attack!")
            send_status(w, f"Attack dodged by {target.name}")
            if tw:
                tw.write(f"\r\n{p.name} swings" " but you dodge!\r\n> ")
                send_status(tw, f"Dodged {p.name}'s attack")
        else:
            target.health -= dmg
            tell(w, f"You hit {target.name}" f" with {wn} for {dmg} damage!")
            send_status(w, f"Hit {target.name} for {dmg}")
            if tw:
                tw.write(
                    f"\r\n{p.name} hits you for {dmg}!"
                    f" (HP: {target.health}"
                    f"/{target.max_health})\r\n> "
                )
                send_status(tw, f"Hit by {p.name} for {dmg}")
                send_vitals(tw, target)
            broadcast_room(
                w, p.room, f"{p.name} attacks {target.name}!", exclude=[tw] if tw else []
            )
            if target.health <= 0 and tw:
                process_death(tw, target, p)
        tell(w, "You recover your stance...")
        send_vitals(w, p)
        await asyncio.sleep(ATTACK_COOLDOWN)
        w.write("\r\n")


async def background_tick(writer: Any, player: Player) -> None:
    """Periodic healing and idle timeout."""
    while True:
        await asyncio.sleep(HEAL_INTERVAL)
        if player.room == START_ROOM and player.health < player.max_health:
            player.health = min(player.health + HEAL_AMOUNT, player.max_health)
            writer.write(
                "\r\nThe tavern's warmth heals you."
                f" (HP: {player.health}"
                f"/{player.max_health})\r\n> "
            )
            send_vitals(writer, player)
        if time.monotonic() - player.last_activity >= IDLE_TIMEOUT:
            writer.write("\r\nIdle timeout.\r\n")
            writer.close()
            return


async def shell(reader: Any, writer: Any) -> None:
    """Main MUD session for one connected client."""
    writer.iac(WILL, GMCP)
    writer.iac(WILL, MSDP)
    writer.iac(WILL, MSSP)
    writer.set_ext_callback(GMCP, lambda pkg, data: on_gmcp(writer, pkg, data))
    writer.set_ext_callback(MSDP, lambda variables: on_msdp(writer, variables))
    writer.write("Welcome to the Mini-MUD!\r\n")

    env = writer.get_extra_info("USER") or writer.get_extra_info("LOGNAME") or ""
    if env:
        writer.write(f'What is your name? (return for "{env}") ')
    else:
        writer.write("What is your name? ")

    if (raw := await readline2(reader, writer)) is None:
        writer.close()
        return
    name = strip_control_chars(raw).strip() or env

    if not name:
        tell(writer, "A name is required.")
        writer.close()
        return

    if any(w in name.lower() for w in BANNED_WORDS):
        tell(writer, "That name is not allowed.")
        writer.close()
        return

    if any(p.name.lower() == name.lower() for p in sessions.values()):
        tell(writer, f"{name} is already playing!")
        writer.close()
        return

    player = Player(name)

    sessions[writer] = player
    player.last_activity = time.monotonic()
    log.info("connect: %s (%d online)", player.name, len(sessions))

    tell(writer, f"Hello, {name}!")
    broadcast_room(writer, player.room, f"{player.name} arrives.")

    mssp = dict(MSSP_DATA)
    mssp["PLAYERS"] = str(len(sessions))
    mssp["UPTIME"] = "999"
    mssp.setdefault("CREATED", "2026")
    mssp.setdefault("CONTACT", "admin@example.com")
    writer.send_mssp(mssp)

    send_room_gmcp(writer, player)
    send_vitals(writer, player)
    show_room(writer, player)

    commands = Commands(writer, player)
    bg_task = asyncio.create_task(background_tick(writer, player))
    try:
        while True:
            writer.write("> ")
            inp = await readline2(reader, writer)
            if inp is None:
                break
            text = strip_control_chars(inp).strip()
            writer.write("\r\n")
            cmd = text.lower()
            log.debug("%s: %s", player.name, cmd)
            if not await commands.dispatch(cmd):
                break
    finally:
        bg_task.cancel()
        await asyncio.gather(bg_task, return_exceptions=True)
        sessions.pop(writer, None)
        drop_weapon(player)
        log.info("disconnect: %s (%d online)", player.name, len(sessions))
        broadcast_room(None, player.room, f"{player.name} has left.")
        update_room_all(player.room)
        writer.close()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    ap = argparse.ArgumentParser(description="Mini-MUD demo server with telnetlib3")
    ap.add_argument("--host", default="127.0.0.1", help="bind address (default: 127.0.0.1)")
    ap.add_argument("--port", type=int, default=6023, help="bind port (default: 6023)")
    ap.add_argument("--log-level", default="INFO", help="log level (default: INFO)")
    return ap.parse_args(argv)


async def main(argv: list[str] | None = None) -> None:
    """Start the MUD server."""
    args = parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format="%(asctime)s %(message)s",
        datefmt="%H:%M:%S",
    )
    server = await telnetlib3.create_server(host=args.host, port=args.port, shell=shell)
    log.info("%s running on %s:%d", SERVER_NAME, args.host, args.port)
    print(
        f"{SERVER_NAME} running on"
        f" {args.host}:{args.port}\n"
        f"Connect with: telnet {args.host} {args.port}\n"
        "Or use GMCP client:"
        " python bin/client_gmcp.py\n"
        "Press Ctrl+C to stop"
    )
    await server.wait_closed()


if __name__ == "__main__":
    asyncio.run(main())
