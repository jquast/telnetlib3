## Macro Editor

Macros bind a **keystroke** to a **command sequence**.  When the key is
pressed during a telnet session, the command text is expanded and executed
exactly as if you had typed it at the input line.

### Table Columns

| Column | Meaning |
|--------|---------|
| **Key** | The keystroke that triggers the macro (e.g. F2, Ctrl+A) |
| **Text** | The command sequence to execute |
| **Last** | Timestamp of the last time this macro was triggered |

### Buttons

| Button | Action |
|--------|--------|
| **Add** | Create a new macro |
| **Edit** | Edit the selected macro |
| **Copy** | Duplicate the selected macro |
| **Delete** | Delete the selected macro (with confirmation) |
| **Save** | Save all changes to disk and close |
| **Cancel** | Discard changes and close |

### Form Fields

- **Enabled** — toggle the macro on/off without deleting it
- **Key** — click "Capture" then press the desired keystroke
- **Text** — the command sequence (use `;` and `:` separators, backtick
  commands)

### Keyboard Shortcuts

| Key | Action |
|-----|--------|
| **+** / **=** | Move selected macro up (higher priority) |
| **-** | Move selected macro down |
| **L** | Sort by last-used timestamp |
| **Enter** | Edit selected / submit form |
| **Escape** | Cancel form / close editor |

### Insert Buttons

The form provides insert buttons for common backtick commands.
**Fast Travel** and **Slow Travel** open the room picker to select a
destination.  Others insert a template you can edit.

### Example Macros

| Key | Text |
|-----|------|
| F2 | `kill bear;`until 10 died\\.`;get all` |
| F5 | `` `slow travel abc123` `` |
| Ctrl+R | `` `return fast` `` |
| F3 | `` `autodiscover 50` `` |
| F4 | `3n;2e;look` |
