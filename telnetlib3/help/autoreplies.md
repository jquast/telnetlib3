## Autoreply Editor

Autoreplies automatically send commands when a **regex pattern** matches
server output.  Rules are evaluated in priority order (top to bottom);
the first match wins unless a rule is marked **Always**.

### Table Columns

| Column | Meaning |
|--------|---------|
| **#** | Priority order (1 = highest) |
| **Pattern** | Regex matched against server output |
| **Reply** | Command sequence sent when the pattern matches |
| **Flags** | **A** = Always, **I** = Immediate, **C** = Case-sensitive, **W** = When condition, **(off)** = disabled |
| **Last** | Timestamp of the last time this rule fired |

### Flags Explained

- **A (Always)** — match even while another rule's exclusive chain is
  active.  Without this flag, only the first matching rule fires per
  prompt.  Use for things like autoloot or stat tracking that should
  always trigger.
- **I (Immediate)** — send the reply immediately without waiting for the
  server's GA/EOR prompt.  Useful for login sequences or rapid responses
  where the server doesn't send a prompt between messages.
- **C (Case-sensitive)** — match the pattern case-sensitively instead of
  the default case-insensitive matching.
- **W (When)** — a vital-percentage condition gate is set on this rule.
- **(off)** — the rule is disabled and won't match.  Toggle via the
  Enabled switch in the form, or use Shift+F9 in-session to disable
  all autoreplies globally.

### Form Fields

- **Enabled** — toggle the rule on/off
- **Always** — match even during another rule's exclusive chain
- **Imm** (Immediate) — reply without waiting for prompt
- **Case** — case-sensitive pattern matching (default: off)
- **Pattern** — Python regex (case-insensitive by default, DOTALL,
  MULTILINE); use capture groups `(...)` for backreferences
- **Reply** — command sequence; use `\1`, `\2` for captured groups
- **Condition** — optional vital gate (e.g. HP% >= 80); the rule only
  fires when the condition is met

### Pattern Syntax (Python Regex)

Patterns use Python's `re` module with flags `MULTILINE | DOTALL`
(and `IGNORECASE` unless the **Case** toggle is enabled).

| Pattern | Matches |
|---------|---------|
| `bear attacks` | Literal text "bear attacks" (case-insensitive) |
| `(\w+) attacks you` | Captures the attacker's name as `\1` |
| `You receive (\d+) xp` | Captures the XP number as `\1` |
| `died\.` | "died." (dot must be escaped) |
| `\bkill\b` | Whole word "kill" only |
| `foo\|bar` | "foo" or "bar" |
| `^You stand` | "You stand" at the start of a line |

### Backreferences in Reply

| Reply | Effect |
|-------|--------|
| `kill \1` | Sends "kill" + the first captured group |
| `say I got \1 gold` | Interpolates capture group into reply |
| `\1;\2` | Both captured groups as separate commands |

### Condition Gate

The optional **Condition** field adds a vital-percentage gate:
the rule only fires when the condition is satisfied.

| Condition | Meaning |
|-----------|---------|
| HP% >= 80 | Only fire when HP is at least 80% of max |
| MP% > 50 | Only fire when MP is above 50% of max |
| HP% = 100 | Only fire when HP is exactly full |

### Example Autoreplies

| Pattern | Reply | Notes |
|---------|-------|-------|
| `(\w+) attacks you` | `kill \1` | Auto-attack aggressors |
| `You killed` | `get all;`delay 1s`;look` | Loot after kill |
| `You are hungry` | `eat bread` | Auto-eat |
| `^A bear` | `` `when HP%>=80`;kill bear;`until 10 died\\.`;get all `` | Conditional combat |

### Keyboard Shortcuts

| Key | Action |
|-----|--------|
| **+** / **=** | Move selected rule up (higher priority) |
| **-** | Move selected rule down |
| **L** | Sort by last-fired timestamp |
| **Enter** | Edit selected / submit form |
| **Escape** | Cancel form / close editor |
