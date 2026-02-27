## Command Syntax

Commands are separated by **`;`** (wait for server prompt) or **`:`** (send
immediately, no prompt wait).

| Syntax | Meaning |
|--------|---------|
| `get all;drop sword` | Send "get all", wait for prompt, then "drop sword" |
| `cast heal:look` | Send "cast heal", then "look" immediately without waiting |
| `3n;2e` | Repeat prefix — expands to `n;n;n;e;e` |
| `5attack` | Sends "attack" five times with prompt pacing |

## Backtick Commands

Backtick-enclosed commands are special directives processed by the client.
They are **not** split on `;` or `:` internally.

### Delay

Pause execution for a duration.

| Example | Effect |
|---------|--------|
| `` `delay 1s` `` | Pause 1 second |
| `` `delay 500ms` `` | Pause 500 milliseconds |
| `` `delay 0.5s` `` | Pause 0.5 seconds |

### When (Condition Gate)

Stop the command chain unless a GMCP vital condition is met.
Vitals are expressed as **percentages** of max.

| Example | Effect |
|---------|--------|
| `` `when HP%>=80` `` | Continue only if HP is at least 80% |
| `` `when MP%>50` `` | Continue only if MP is above 50% |
| `` `when HP%=100` `` | Continue only if HP is exactly 100% |

Operators: `>=`, `<=`, `>`, `<`, `=`

### Until (Wait for Pattern)

Pause the chain until a regex pattern appears in server output,
or a timeout expires (default 4 seconds). **Case-insensitive.**

| Example | Effect |
|---------|--------|
| `` `until died\\.` `` | Wait up to 4s for "died." |
| `` `until 10 died\\.` `` | Wait up to 10s for "died." |
| `` `until 2 treasure` `` | Wait up to 2s for "treasure" |

### Untils (Case-Sensitive Until)

Same as `until` but the pattern match is **case-sensitive**.

| Example | Effect |
|---------|--------|
| `` `untils 2 DEAD` `` | Wait up to 2s for exactly "DEAD" |
| `` `untils You dodge` `` | Wait up to 4s for "You dodge" |

### Fast Travel / Slow Travel

Navigate to a room by its GMCP room ID.  Fast travel suppresses exclusive
autoreplies (e.g. combat triggers); slow travel allows them to fire.

| Example | Effect |
|---------|--------|
| `` `fast travel abc123` `` | Fast travel to room abc123 |
| `` `slow travel abc123` `` | Slow travel (autoreplies fire) |

### Return Fast / Return Slow

Travel back to the room where the current macro started executing.

| Example | Effect |
|---------|--------|
| `` `return fast` `` | Return to start room (fast) |
| `` `return slow` `` | Return to start room (slow, autoreplies fire) |

### Autodiscover

BFS-explore unvisited exits from nearby rooms.  Accepts an optional
room limit (default 999).

| Example | Effect |
|---------|--------|
| `` `autodiscover` `` | Explore up to 999 unvisited exits |
| `` `autodiscover 50` `` | Explore up to 50 exits |

### Random Walk

Walk randomly, preferring rooms with unvisited exits.  Accepts an
optional step limit (default 999).

| Example | Effect |
|---------|--------|
| `` `randomwalk` `` | Random walk up to 999 steps |
| `` `randomwalk 100` `` | Random walk up to 100 steps |

### Resume

Resume the last autodiscover or randomwalk from where it stopped,
carrying over the visited/tried state.  Only works if still in the
same room.  Accepts an optional limit override.

| Example | Effect |
|---------|--------|
| `` `resume` `` | Resume last walk mode |
| `` `resume 200` `` | Resume with a 200-step limit |

## Combining Commands

Commands can be freely mixed:

```
kill bear;`until 10 died\\.`;get all;`delay 1s`;`return fast`
```

This sends "kill bear", waits up to 10s for "died.", sends "get all",
pauses 1 second, then fast-travels back to the starting room.
