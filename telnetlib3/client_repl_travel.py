"""Movement and pathfinding: fast/slow travel, autodiscover, randomwalk."""

# std imports
import random
import asyncio
import logging
import collections
from typing import TYPE_CHECKING, Optional

# local
from .stream_writer import TelnetWriterUnicode
from .client_repl_commands import _COMMAND_DELAY

if TYPE_CHECKING:
    from .session_context import SessionContext

_DISCOVER_ARRIVAL_TIMEOUT = 3.0
_DEFAULT_WALK_LIMIT = 999
_STANDARD_DIRS = frozenset(
    {
        "north",
        "south",
        "east",
        "west",
        "northeast",
        "northwest",
        "southeast",
        "southwest",
        "ne",
        "nw",
        "se",
        "sw",
    }
)
_BOUNCE_THRESHOLD = 3


async def _fast_travel(
    steps: list[tuple[str, str]],
    ctx: "SessionContext",
    log: logging.Logger,
    slow: bool = False,
    destination: str = "",
    correct_names: bool = True,
) -> None:
    """
    Execute fast travel by sending movement commands with GA/EOR pacing.

    Uses the same ``_wait_for_prompt`` / ``_echo_command`` functions that
    the autoreply engine and manual input use, so commands are paced by
    the server's GA/EOR prompt signal and echoed visibly.

    In fast mode (default), exclusive autoreplies are suppressed.
    Non-exclusive autoreplies still fire; travel pauses until they
    complete and then waits for a clean EOR with no match before
    sending the next direction.

    In slow mode, all autoreplies fire including exclusive ones.

    When the player arrives at an unexpected room, instead of aborting
    the function re-pathfinds from the actual position to *destination*
    and continues with the new route (up to 3 re-routes).

    :param steps: List of (direction, expected_room_num) pairs.
    :param ctx: Session context for sending commands.
    :param log: Logger.
    :param slow: If ``True``, allow exclusive autoreplies.
    :param destination: Final target room ID for re-pathfinding on detour.
    :param correct_names: If ``True`` (default), rewrite graph edges when
        arriving at a same-name room with a different ID.  Set to ``False``
        when distinct room IDs must be preserved.
    """
    wait_fn = ctx.wait_for_prompt
    echo_fn = ctx.echo_command

    from .autoreply import AutoreplyEngine

    def _get_engine() -> Optional["AutoreplyEngine"]:
        """Find the active autoreply engine, if any."""
        return ctx.autoreply_engine

    engine = _get_engine()
    if engine is not None and not slow:
        engine.suppress_exclusive = True

    mode = "slow travel" if slow else "fast travel"

    from .rooms import RoomGraph

    def _get_graph() -> Optional[RoomGraph]:
        graph: Optional[RoomGraph] = ctx.room_graph
        return graph

    def _room_name(num: str) -> str:
        """Look up a human-readable room name from the session's graph."""
        graph = _get_graph()
        if graph is not None:
            room = graph.rooms.get(num)
            if room is not None:
                return f"{room.name} ({num[:8]}...)"
        return num

    # Track room IDs the graph already knew about before this travel
    # started, so we can distinguish "ID rotation" (new hash for same
    # room) from "different room with the same name" (cave grids).
    _pre_existing_rooms: set[str] = set()
    graph = _get_graph()
    if graph is not None:
        _pre_existing_rooms = set(graph.rooms.keys())

    def _names_match(expected_num: str, actual_num: str) -> bool:
        """
        Check whether two room IDs likely refer to the same physical room.

        This handles MUDs that rotate room IDs (same physical room, new hash
        each visit).  Returns ``True`` only when:

        1. Both rooms share the same name.
        2. The *actual* room ID was **not** already in the graph before this
           travel began.  Pre-existing rooms are distinct locations that
           happen to share a name (e.g. a grid of "A cave" rooms).  A
           rotated ID produces a hash the graph has never seen.
        """
        if actual_num in _pre_existing_rooms:
            return False
        graph = _get_graph()
        if graph is None:
            return False
        expected = graph.rooms.get(expected_num)
        actual = graph.rooms.get(actual_num)
        if expected is None or actual is None:
            return False
        return expected.name == actual.name and bool(expected.name)

    def _correct_edge(
        prev_num: str,
        direction: str,
        old_target: str,
        new_target: str,
        step_idx: int,
        steps_list: list[tuple[str, str]],
    ) -> None:
        """
        Update the graph edge and rewrite only the current step.

        Earlier versions rewrote *all* remaining steps matching *old_target*, which corrupted paths
        through grids of same-named rooms (e.g. a cave system where many rooms share the name "A
        cave" but are distinct locations with different IDs).  Now only the step at *step_idx* is
        updated.
        """
        graph = _get_graph()
        if graph is not None:
            prev = graph.rooms.get(prev_num)
            if prev is not None and prev.exits.get(direction) == old_target:
                prev.exits[direction] = new_target
                log.info(
                    "%s: corrected exit %s of %s: %s -> %s",
                    mode,
                    direction,
                    prev_num[:8],
                    old_target[:8],
                    new_target[:8],
                )
        if step_idx < len(steps_list):
            d, r = steps_list[step_idx]
            if r == old_target:
                steps_list[step_idx] = (d, new_target)

    room_changed = ctx.room_changed
    max_retries = 3
    max_reroutes = 3

    if not destination and steps:
        destination = steps[-1][1]

    blocked_exits: list[tuple[str, str, str]] = []
    try:
        step_idx = 0
        reroute_count = 0
        while step_idx < len(steps):
            direction, expected_room = steps[step_idx]
            prev_room = ctx.current_room_num

            for attempt in range(max_retries + 1):
                # Delay between steps (and retries) for server rate limits.
                if step_idx > 0 or attempt > 0:
                    await asyncio.sleep(_COMMAND_DELAY)

                if room_changed is not None:
                    room_changed.clear()

                tag = f" [{step_idx + 1}/{len(steps)}]"
                if attempt == 0:
                    log.info("%s [%d/%d] %s", mode, step_idx + 1, len(steps), direction)
                    if echo_fn is not None:
                        echo_fn(direction + tag)
                else:
                    log.info(
                        "%s [%d/%d] %s (retry %d)",
                        mode,
                        step_idx + 1,
                        len(steps),
                        direction,
                        attempt,
                    )
                # Clear prompt_ready before sending so wait_fn waits
                # for a FRESH GA/EOR from this step's response.  The
                # server sends multiple GA/EORs per response (room
                # prompt + GMCP vitals updates), and stale signals
                # from the previous step cause wait_fn to return
                # before the current room output has been received.
                prompt_ready = ctx.prompt_ready
                if prompt_ready is not None:
                    prompt_ready.clear()

                ctx.active_command = direction
                if ctx.cx_dot is not None:
                    ctx.cx_dot.trigger()
                if ctx.tx_dot is not None:
                    ctx.tx_dot.trigger()
                ctx.writer.write(direction + "\r\n")  # type: ignore[arg-type]

                if wait_fn is not None:
                    await wait_fn()

                # Yield to let _read_server feed the room output to the
                # autoreply engine before we check reply_pending.
                await asyncio.sleep(0)

                engine = _get_engine()
                cond_cancelled = False
                if engine is not None:
                    while engine.reply_pending:
                        await asyncio.sleep(0.05)
                    if slow:
                        failed = engine.pop_condition_failed()
                        if failed is not None:
                            rule_idx, desc = failed
                            msg = (
                                f"Travel mode cancelled - failed "
                                f"conditional in AUTOREPLY "
                                f"#{rule_idx} [{desc}]"
                            )
                            log.warning("%s", msg)
                            if echo_fn is not None:
                                echo_fn(msg)
                            cond_cancelled = True
                    # In slow mode, exclusive rules enter exclusive mode
                    # (e.g. "kill" sent, waiting for "died\.").  Wait for
                    # combat to finish before moving to the next room.
                    # After exclusive/reply_pending clear, wait for a
                    # fresh prompt so the server response to the last
                    # autoreply command is processed -- it may trigger
                    # new matches (cascading always-rules).
                    if slow and (engine.exclusive_active or engine.reply_pending):
                        settle_passes = 0
                        max_settle = 20  # safety cap
                        while settle_passes < max_settle:
                            if engine.exclusive_active:
                                while engine.exclusive_active:
                                    engine.check_timeout()
                                    await asyncio.sleep(0.05)
                            while engine.reply_pending:
                                await asyncio.sleep(0.05)
                            # Wait for server to respond to whatever the
                            # autoreply just sent.  The prompt signal
                            # drives on_prompt() which may queue new
                            # replies.
                            if wait_fn is not None:
                                await wait_fn()
                            await asyncio.sleep(0)
                            # If neither exclusive nor reply_pending
                            # after the prompt, we've converged.
                            if not engine.exclusive_active and not engine.reply_pending:
                                break
                            settle_passes += 1
                if cond_cancelled:
                    break

                # GMCP Room.Info may arrive after the EOR.  Wait for it.
                actual = ctx.current_room_num
                if expected_room and actual != expected_room and room_changed is not None:
                    try:
                        await asyncio.wait_for(room_changed.wait(), timeout=2.0)
                    except asyncio.TimeoutError:
                        pass
                    actual = ctx.current_room_num

                if actual == expected_room:
                    break
                # Same-name room with different ID -- correct the edge
                # and continue as if we arrived at the expected room.
                # Skipped when correct_names=False to preserve distinct
                # room IDs in grids of same-named rooms.
                if (
                    correct_names
                    and expected_room
                    and actual
                    and actual != expected_room
                    and _names_match(expected_room, actual)
                ):
                    log.info(
                        "%s: room ID changed for %s (%s -> %s), correcting",
                        mode,
                        _room_name(actual),
                        expected_room[:8],
                        actual[:8],
                    )
                    _correct_edge(prev_room, direction, expected_room, actual, step_idx, steps)
                    expected_room = actual
                    break
                # Room didn't change -- server likely rejected move (rate limit).
                # Retry unless we've exhausted attempts.
                if actual == prev_room and attempt < max_retries:
                    continue
                # Arrived at wrong room -- try to re-route.
                break

            if cond_cancelled:
                break
            if expected_room and actual and actual != expected_room:
                move_blocked = actual == prev_room
                if move_blocked:
                    # Exit is impassable (server rejected the move after
                    # all retries).  Temporarily remove it from both the
                    # Room.exits dict and the BFS adjacency cache so
                    # re-routing won't try it again.
                    graph = _get_graph()
                    if graph is not None:
                        prev = graph.rooms.get(prev_room)
                        if prev is not None and direction in prev.exits:
                            blocked_exits.append((prev_room, direction, prev.exits[direction]))
                            del prev.exits[direction]
                            adj_exits = graph._adj.get(prev_room)
                            if adj_exits is not None:
                                adj_exits.pop(direction, None)
                            log.info(
                                "%s: blocked exit %s of %s (impassable)",
                                mode,
                                direction,
                                prev_room[:8],
                            )
                else:
                    # Update graph edge to reflect actual connection.
                    graph = _get_graph()
                    if graph is not None:
                        prev = graph.rooms.get(prev_room)
                        if prev is not None:
                            prev.exits[direction] = actual
                            log.info(
                                "%s: updated edge %s of %s: -> %s",
                                mode,
                                direction,
                                prev_room[:8],
                                actual[:8],
                            )

                # Try re-pathfinding from actual position.
                if (
                    destination
                    and actual
                    and actual != destination
                    and reroute_count < max_reroutes
                    and graph is not None
                ):
                    new_steps = graph.find_path_with_rooms(actual, destination)
                    if new_steps is not None:
                        reroute_count += 1
                        msg = (
                            f"{mode}: re-routing from "
                            f"{_room_name(actual)}"
                            f" ({reroute_count}/{max_reroutes})"
                        )
                        log.info("%s", msg)
                        if echo_fn is not None:
                            echo_fn(msg)
                        steps = new_steps
                        step_idx = 0
                        continue

                expected_name = _room_name(expected_room)
                actual_name = _room_name(actual)
                msg = (
                    f"{mode} stopped: expected {expected_name} after "
                    f"'{direction}', got {actual_name}"
                )
                log.warning("%s", msg)
                if echo_fn is not None:
                    echo_fn(msg)
                break
            step_idx += 1
    finally:
        # Restore temporarily blocked exits so the graph stays accurate
        # for future pathfinding (the block may be transient, e.g. a
        # quest gate that opens later).
        if blocked_exits:
            graph = _get_graph()
            if graph is not None:
                for room_num, exit_dir, target in blocked_exits:
                    prev = graph.rooms.get(room_num)
                    if prev is not None and exit_dir not in prev.exits:
                        prev.exits[exit_dir] = target
                    graph._adj.setdefault(room_num, {})[exit_dir] = target
        ctx.active_command = None
        engine = _get_engine()
        if engine is not None:
            engine.suppress_exclusive = False


async def _autodiscover(
    ctx: "SessionContext",
    log: logging.Logger,
    limit: int = _DEFAULT_WALK_LIMIT,
    resume: bool = False,
) -> None:
    """
    Explore unvisited exits reachable from the current room.

    BFS-discovers frontier exits (leading to unvisited or unknown rooms),
    travels to each, then returns to the starting room before trying the
    next.  Maintains an in-memory ``tried`` set to avoid retrying exits
    that failed or led to unexpected rooms.  Stops after *limit* exits
    or when no more branches remain.

    :param ctx: Session context with room graph and session attributes.
    :param log: Logger.
    :param limit: Maximum number of exits to explore.
    """
    if ctx.discover_active:
        return

    current = ctx.current_room_num
    graph = ctx.room_graph
    echo_fn = ctx.echo_command
    if not current or graph is None:
        if echo_fn is not None:
            echo_fn("AUTODISCOVER: no room data")
        return

    tried: set[tuple[str, str]] = set(ctx.blocked_exits)
    if resume and ctx.last_walk_mode == "autodiscover" and ctx.last_walk_tried:
        tried |= ctx.last_walk_tried
    inaccessible: set[str] = set()
    blocked_edges: dict[tuple[str, str], str] = {}

    branches = graph.find_branches(current)
    if not branches:
        if echo_fn is not None:
            echo_fn("AUTODISCOVER: no unvisited exits nearby")
        return

    ctx.discover_active = True
    ctx.discover_total = len(branches)
    ctx.discover_current = 0
    step_count = 0
    last_stuck_room = ""
    stuck_retries = 0
    try:
        while step_count < limit:
            pos = ctx.current_room_num
            # Re-discover from current position each iteration — picks up
            # newly revealed exits from rooms we just visited, nearest-first.
            branches = [
                (gw, d, t)
                for gw, d, t in graph.find_branches(pos)
                if (gw, d) not in tried and t not in inaccessible
            ]
            if not branches:
                break

            ctx.discover_total = step_count + len(branches)
            gw_room, direction, target_num = branches[0]
            step_count += 1
            ctx.discover_current = step_count

            # Travel to the gateway room (nearest-first, so usually short).
            if pos != gw_room:
                steps = graph.find_path_with_rooms(pos, gw_room)
                if steps is None:
                    tried.add((gw_room, direction))
                    if target_num:
                        inaccessible.add(target_num)
                    if echo_fn is not None:
                        echo_fn(
                            f"AUTODISCOVER [{step_count}]: " f"no path to gateway {gw_room[:8]}"
                        )
                    continue
                if echo_fn is not None:
                    echo_fn(f"AUTODISCOVER [{step_count}]: " f"heading to gateway {gw_room[:8]}")
                pre_travel = ctx.current_room_num
                await _fast_travel(steps, ctx, log, slow=False, destination=gw_room)
                actual = ctx.current_room_num
                if actual != gw_room:
                    tried.add((gw_room, direction))
                    if target_num:
                        inaccessible.add(target_num)
                    # Identify the edge that blocked us: if the player
                    # didn't move at all, the first step of the path is
                    # impassable.  Remove it from the BFS adjacency
                    # cache so subsequent pathfinding avoids it.
                    if actual == pre_travel and steps:
                        fail_dir, fail_target = steps[0]
                        edge = (pre_travel, fail_dir)
                        if edge not in blocked_edges:
                            blocked_edges[edge] = fail_target
                            adj_exits = graph._adj.get(pre_travel)
                            if adj_exits is not None:
                                adj_exits.pop(fail_dir, None)
                            log.info(
                                "AUTODISCOVER: blocked edge %s from %s", fail_dir, pre_travel[:8]
                            )
                    log.info("AUTODISCOVER: failed to reach gateway %s", gw_room[:8])
                    if echo_fn is not None:
                        echo_fn(
                            f"AUTODISCOVER [{step_count}]: "
                            f"gateway {gw_room[:8]} inaccessible, skipping"
                        )
                    if actual == last_stuck_room:
                        stuck_retries += 1
                    else:
                        last_stuck_room = actual
                        stuck_retries = 1
                    if stuck_retries >= 3:
                        if echo_fn is not None:
                            echo_fn(
                                f"AUTODISCOVER [{step_count}]: "
                                f"stuck at {actual[:8]}, all routes blocked, "
                                f"stopping"
                            )
                        break
                    continue

            # Step through the frontier exit.
            if echo_fn is not None:
                echo_fn(
                    f"AUTODISCOVER [{step_count}]: " f"exploring {direction} from {gw_room[:8]}"
                )
            await asyncio.sleep(_COMMAND_DELAY)
            ctx.active_command = direction
            send = ctx.send_line
            if ctx.cx_dot is not None:
                ctx.cx_dot.trigger()
            if ctx.tx_dot is not None:
                ctx.tx_dot.trigger()
            if send is not None:
                send(direction)
            elif isinstance(ctx.writer, TelnetWriterUnicode):
                ctx.writer.write(direction + "\r\n")
            else:
                ctx.writer.write((direction + "\r\n").encode("utf-8"))
            # Wait for room arrival using the event instead of polling.
            room_changed = ctx.room_changed
            arrived = False
            if room_changed is not None:
                room_changed.clear()
                try:
                    await asyncio.wait_for(room_changed.wait(), timeout=_DISCOVER_ARRIVAL_TIMEOUT)
                except asyncio.TimeoutError:
                    pass
                arrived = ctx.current_room_num != gw_room
            else:
                for _wait in range(30):
                    await asyncio.sleep(0.3)
                    if ctx.current_room_num != gw_room:
                        arrived = True
                        break
            if not arrived:
                ctx.active_command = None
                tried.add((gw_room, direction))
                ctx.blocked_exits.add((gw_room, direction))
                if target_num:
                    inaccessible.add(target_num)
                if echo_fn is not None:
                    echo_fn(f"AUTODISCOVER [{step_count}]: " f"no room change after {direction}")
                continue
            ctx.active_command = None

            tried.add((gw_room, direction))
            actual = ctx.current_room_num
            if target_num and actual != target_num and target_num in graph.rooms:
                if echo_fn is not None:
                    echo_fn(
                        f"AUTODISCOVER [{step_count}]: "
                        f"unexpected room {actual[:8]} "
                        f"(expected {target_num[:8]})"
                    )

            # Wait for any autoreply to settle.
            ar = ctx.autoreply_engine
            if ar is not None:
                settle = 0
                while settle < 60:
                    if ar.exclusive_active:
                        while ar.exclusive_active:
                            ar.check_timeout()
                            await asyncio.sleep(0.1)
                    while ar.reply_pending:
                        await asyncio.sleep(0.05)
                    await asyncio.sleep(0.1)
                    if not ar.exclusive_active and not ar.reply_pending:
                        break
                    settle += 1

            # Stay where we are — next iteration re-discovers branches
            # from current position, so nearby clusters get swept without
            # backtracking.
    except asyncio.CancelledError:
        pass
    finally:
        ctx.last_walk_mode = "autodiscover"
        ctx.last_walk_room = ctx.current_room_num
        ctx.last_walk_tried = tried
        ctx.discover_active = False
        ctx.discover_current = 0
        ctx.discover_total = 0
        ctx.discover_task = None
        ctx.active_command = None
        # Restore blocked edges so the graph stays accurate for future
        # pathfinding (the block may be transient, e.g. a level gate).
        for (room_num, exit_dir), target in blocked_edges.items():
            graph._adj.setdefault(room_num, {})[exit_dir] = target


async def _randomwalk(
    ctx: "SessionContext",
    log: logging.Logger,
    limit: int = _DEFAULT_WALK_LIMIT,
    resume: bool = False,
) -> None:
    """
    Random walk up to *limit* rooms, preferring unvisited exits.

    At each room the walker picks a random exit from those with the
    lowest walk visit count.  A per-walk ``walk_counts`` dict tracks
    how many times we have arrived at each room during this walk.  The
    room the player was in *before* triggering the walk (the
    "entrance") is seeded with an infinite count so it is never
    chosen — the walker will never leave through the direction it
    came from.

    Stops early when every reachable room (excluding the entrance)
    has been visited at least once.

    :param ctx: Session context with room graph and session attributes.
    :param log: Logger.
    :param limit: Maximum number of steps.
    """
    if ctx.randomwalk_active:
        return

    current = ctx.current_room_num
    graph = ctx.room_graph
    echo_fn = ctx.echo_command
    wait_fn = ctx.wait_for_prompt
    if not current or graph is None:
        if echo_fn is not None:
            echo_fn("RANDOMWALK: no room data")
        return

    adj = graph._adj
    exits = adj.get(current, {})
    if not exits:
        if echo_fn is not None:
            echo_fn("RANDOMWALK: no exits from current room")
        return

    # Per-walk visit counter.  The entrance room (the room we were in
    # before triggering the walk) is seeded at infinity so the walker
    # never prefers going back through it.
    entrance_room = ctx.previous_room_num
    walk_counts: dict[str, float] = {current: 1}
    if entrance_room:
        walk_counts[entrance_room] = float("inf")

    # blocked_exits is consulted per-room at scoring time rather than
    # seeding walk_counts globally — a blocked exit (A, east) should
    # only penalize that specific exit, not the destination room from
    # every other direction.

    def _flood_reachable() -> set[str]:
        """BFS flood from current room, excluding the entrance."""
        result: set[str] = set()
        q: collections.deque[str] = collections.deque([current])
        seen: set[str] = {current}
        if entrance_room:
            seen.add(entrance_room)
        while q:
            node = q.popleft()
            for dst in adj.get(node, {}).values():
                if dst not in seen:
                    seen.add(dst)
                    result.add(dst)
                    q.append(dst)
        return result

    reachable = _flood_reachable()

    ctx.randomwalk_active = True
    ctx.randomwalk_total = min(limit, len(reachable)) if reachable else limit
    ctx.randomwalk_current = 0
    visited: set[str] = {current}
    if resume and ctx.last_walk_mode == "randomwalk" and ctx.last_walk_visited:
        visited |= ctx.last_walk_visited

    try:
        stuck_count = 0
        bounce_count = 0
        prev_room: Optional[str] = None
        for step in range(limit):
            ctx.randomwalk_current = step + 1
            current = ctx.current_room_num
            exits = dict(adj.get(current, {}))
            if not exits:
                if echo_fn is not None:
                    echo_fn(
                        f"RANDOMWALK [{step + 1}/{ctx.randomwalk_total}]: " f"dead end, stopping"
                    )
                break

            # Check if all reachable rooms have been visited.
            if reachable and reachable.issubset(visited):
                if echo_fn is not None:
                    echo_fn(
                        f"RANDOMWALK [{step + 1}/{ctx.randomwalk_total}]: "
                        f"all {len(visited)} reachable rooms visited"
                    )
                break

            # Score each exit by walk visit count (lower is better).
            # Skip exits known to be blocked from this room.
            # Non-cardinal directions get a 0.1 penalty so they are
            # tried after cardinal exits at the same visit count.
            scored: list[tuple[float, str, str]] = []
            for d, dst in exits.items():
                if (current, d) in ctx.blocked_exits:
                    continue
                penalty = 0.0 if d in _STANDARD_DIRS else 0.1
                scored.append((walk_counts.get(dst, 0) + penalty, d, dst))

            if not scored:
                if echo_fn is not None:
                    echo_fn(
                        f"RANDOMWALK [{step + 1}/{ctx.randomwalk_total}]: "
                        f"all exits blocked, stopping"
                    )
                break

            min_count = min(s[0] for s in scored)
            best = [(d, dst) for cnt, d, dst in scored if cnt == min_count]
            direction, dst_num = random.choice(best)

            room = graph.get_room(dst_num)
            dst_label = room.name if room else dst_num[:8]
            if echo_fn is not None:
                echo_fn(
                    f"RANDOMWALK [{step + 1}/{ctx.randomwalk_total}]: "
                    f"{direction} -> {dst_label}"
                )

            ctx.active_command = direction
            if wait_fn is not None:
                await wait_fn()
            if ctx.cx_dot is not None:
                ctx.cx_dot.trigger()
            if ctx.tx_dot is not None:
                ctx.tx_dot.trigger()
            if isinstance(ctx.writer, TelnetWriterUnicode):
                ctx.writer.write(direction + "\r\n")
            else:
                ctx.writer.write((direction + "\r\n").encode("utf-8"))

            # Wait for room change using event instead of polling.
            room_changed = ctx.room_changed
            arrived = False
            if room_changed is not None:
                room_changed.clear()
                try:
                    await asyncio.wait_for(room_changed.wait(), timeout=ctx.room_arrival_timeout)
                except asyncio.TimeoutError:
                    pass
                arrived = ctx.current_room_num != current
            else:
                for _tick in range(30):
                    await asyncio.sleep(0.3)
                    if ctx.current_room_num != current:
                        arrived = True
                        break
            if not arrived:
                ctx.active_command = None
                stuck_count += 1
                if echo_fn is not None:
                    echo_fn(
                        f"RANDOMWALK [{step + 1}/{ctx.randomwalk_total}]: "
                        f"no room change after {direction}"
                    )
                # Mark only this specific exit as blocked.
                ctx.blocked_exits.add((current, direction))
                # Check if ALL exits from current room are now blocked.
                all_blocked = all((current, d) in ctx.blocked_exits for d in adj.get(current, {}))
                if all_blocked:
                    if echo_fn is not None:
                        echo_fn(
                            f"RANDOMWALK [{step + 1}/{ctx.randomwalk_total}]: "
                            f"all exits blocked, stopping"
                        )
                    break
                continue

            ctx.active_command = None
            stuck_count = 0
            actual = ctx.current_room_num
            walk_counts[actual] = walk_counts.get(actual, 0) + 1
            visited.add(actual)

            # Bounce detection: if we returned to the room we were in
            # 2 steps ago, we are ping-ponging.
            if prev_room is not None and actual == prev_room:
                bounce_count += 1
                if bounce_count >= _BOUNCE_THRESHOLD:
                    ctx.blocked_exits.add((current, direction))
                    if echo_fn is not None:
                        echo_fn(
                            f"RANDOMWALK [{step + 1}/{ctx.randomwalk_total}]: "
                            f"bounce detected on {direction}, blocking"
                        )
                    bounce_count = 0
                    # Check if all exits from actual (where we are now)
                    # are blocked after adding the bounce block.
                    all_blocked = all((actual, d) in ctx.blocked_exits for d in adj.get(actual, {}))
                    if all_blocked:
                        if echo_fn is not None:
                            echo_fn(
                                f"RANDOMWALK [{step + 1}/{ctx.randomwalk_total}]: "
                                f"all exits blocked after bounce, stopping"
                            )
                        break
            else:
                bounce_count = 0
            prev_room = current

            await asyncio.sleep(_COMMAND_DELAY)

            # Re-flood: the room graph's adjacency is updated live by
            # GMCP Room.Info, so newly discovered exits expand the
            # reachable set dynamically.
            new_reachable = _flood_reachable()
            if len(new_reachable) > len(reachable):
                reachable = new_reachable
                ctx.randomwalk_total = min(limit, len(reachable))

            # Wait for autoreplies to settle.
            ar = ctx.autoreply_engine
            if ar is not None:
                settle = 0
                while settle < 60:
                    if ar.exclusive_active:
                        while ar.exclusive_active:
                            ar.check_timeout()
                            await asyncio.sleep(0.1)
                    while ar.reply_pending:
                        await asyncio.sleep(0.05)
                    await asyncio.sleep(0.1)
                    if not ar.exclusive_active and not ar.reply_pending:
                        break
                    settle += 1
    except asyncio.CancelledError:
        pass
    finally:
        ctx.last_walk_mode = "randomwalk"
        ctx.last_walk_room = ctx.current_room_num
        ctx.last_walk_visited = visited
        ctx.randomwalk_active = False
        ctx.randomwalk_current = 0
        ctx.randomwalk_total = 0
        ctx.randomwalk_task = None
        ctx.active_command = None


async def _handle_travel_commands(
    parts: list[str], ctx: "SessionContext", log: logging.Logger
) -> list[str]:
    """
    Scan *parts* for travel commands, execute them, and return remaining parts.

    Recognised commands (case-insensitive, enclosed in backticks):

    - ```fast travel <id>``` -- fast travel to room *id*
    - ```slow travel <id>``` -- slow travel to room *id*
    - ```return fast``` -- fast travel back to the macro's starting room
    - ```return slow``` -- slow travel back to the macro's starting room
    - ```autodiscover``` -- explore unvisited exits from nearby rooms
    - ```randomwalk``` -- random walk preferring unvisited rooms

    Only the **first** travel command in the list is handled; everything
    before it is returned as-is (already sent by the caller), and everything
    after it is returned for the caller to send as chained commands once
    travel finishes.

    :param parts: Expanded command list from :func:`expand_commands`.
    :param ctx: Session context with room graph attributes.
    :param log: Logger.
    :returns: Commands that still need to be sent to the server.
    """
    from .client_repl_commands import _TRAVEL_RE

    for idx, cmd in enumerate(parts):
        m = _TRAVEL_RE.match(cmd)
        if not m:
            continue
        verb = m.group(1).lower()
        arg = m.group(2).strip()

        if verb in ("autodiscover", "randomwalk", "resume"):
            walk_limit = _DEFAULT_WALK_LIMIT
            if arg:
                try:
                    walk_limit = int(arg)
                except ValueError:
                    pass

            echo_fn = ctx.echo_command
            if verb == "resume":
                if not ctx.last_walk_mode:
                    if echo_fn is not None:
                        echo_fn("RESUME: no previous walk to resume")
                    return parts[idx + 1 :]
                if ctx.last_walk_room != ctx.current_room_num:
                    if echo_fn is not None:
                        echo_fn("RESUME: room changed since last walk, " "cannot resume")
                    return parts[idx + 1 :]
                verb = ctx.last_walk_mode
                do_resume = True
            else:
                # Auto-resume: if re-running the same mode from the
                # same room, carry over visited/tried state.
                do_resume = (
                    ctx.last_walk_mode == verb and ctx.last_walk_room == ctx.current_room_num
                )

            if verb == "autodiscover":
                await _autodiscover(ctx, log, limit=walk_limit, resume=do_resume)
            else:
                await _randomwalk(ctx, log, limit=walk_limit, resume=do_resume)
            return parts[idx + 1 :]

        slow = "slow" in verb
        is_return = verb.startswith("return")

        if is_return:
            room_id = ctx.macro_start_room or ctx.current_room_num
        else:
            room_id = arg

        if not room_id:
            log.warning("travel command with no room id: %r", cmd)
            break

        current = ctx.current_room_num
        if not current:
            log.warning("no current room -- cannot travel")
            break

        graph = ctx.room_graph
        if graph is None:
            log.warning("no room graph -- cannot travel")
            break

        path = graph.find_path_with_rooms(current, room_id)
        if path is None:
            log.warning("no path from %s to %s", current, room_id)
            break

        await _fast_travel(path, ctx, log, slow=slow, destination=room_id)
        return parts[idx + 1 :]

    return parts
