## Highlight Editor

Highlights apply visual styles to server output when a regex pattern
matches.  Rules are evaluated in order; multiple rules can match the
same text.

### Table Columns

| Column | Meaning |
|--------|---------|
| **#** | Priority order |
| **Pattern** | Regex matched against server output |
| **Highlight** | Named style applied to the match |
| **Flags** | **S** = Stop movement, **C** = Case-sensitive |

### Flags Explained

- **S (Stop movement)** — cancel any active autodiscover or randomwalk
  when this pattern matches.  Useful for detecting danger or important
  events during exploration.
- **C (Case-sensitive)** — match the pattern case-sensitively instead of
  the default case-insensitive matching.

### Form Fields

- **Enabled** — toggle the rule on/off
- **Stop** — cancel movement walks on match
- **Case** — case-sensitive matching
- **Pattern** — Python regex
- **Highlight** — a style name (see below)

### Style Names

Styles are composed from attributes and colors separated by underscores.
Attributes: `bold`, `italic`, `underline`, `blink`, `reverse`.
Colors: `red`, `green`, `yellow`, `blue`, `magenta`, `cyan`, `white`,
`black`.  Prefix with `on_` for background.

| Style | Effect |
|-------|--------|
| `bold_red` | Bold red text |
| `blink_black_on_yellow` | Blinking black text on yellow background |
| `underline_green` | Underlined green text |
| `reverse` | Reversed video |
| `bold_white_on_red` | Bold white text on red background |

### Pattern Syntax (Python Regex)

| Pattern | Matches |
|---------|---------|
| `treasure` | Literal text "treasure" |
| `\b(gold\|silver)\b` | Whole word "gold" or "silver" |
| `^You feel` | "You feel" at line start |
| `HP: (\d+)` | "HP:" followed by digits |

### Keyboard Shortcuts

| Key | Action |
|-----|--------|
| **+** / **=** | Move selected rule up (higher priority) |
| **-** | Move selected rule down |
| **Enter** | Edit selected / submit form |
| **Escape** | Cancel form / close editor |
