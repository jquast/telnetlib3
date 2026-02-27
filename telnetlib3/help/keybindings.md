## telnetlib3 — Keybindings

### Session Keys

| Key | Action |
|-----|--------|
| **F1** | This help screen |
| **F6** | Edit highlights (TUI editor) |
| **Shift+F6** | Toggle highlights on/off |
| **F8** | Edit macros (TUI editor) |
| **F9** | Edit autoreplies (TUI editor) |
| **Shift+F9** | Toggle autoreplies on/off |
| **Ctrl+L** | Repaint screen |
| **Ctrl+]** | Disconnect |

### GMCP Keys (available when server supports GMCP)

| Key | Action |
|-----|--------|
| **F3** | Random walk (explore random exits) |
| **F4** | Autodiscover (explore unvisited exits) |
| **F5** | Resume last walk |
| **F7** | Browse rooms / fast travel |

### Line Editing

| Key | Action |
|-----|--------|
| **Left** / **Right** | Move cursor |
| **Home** / **Ctrl+A** | Beginning of line |
| **End** / **Ctrl+E** | End of line |
| **Ctrl+Left** | Move word left |
| **Ctrl+Right** | Move word right |
| **Backspace** | Delete before cursor |
| **Delete** | Delete at cursor |
| **Ctrl+K** | Kill to end of line |
| **Ctrl+U** | Kill entire line |
| **Ctrl+W** | Kill word back |
| **Ctrl+Y** | Yank (paste killed text) |
| **Ctrl+C** | Copy input line to clipboard |
| **Ctrl+V** | Paste from clipboard |
| **Ctrl+Z** | Undo |
| **Up** / **Down** | History navigation |

### Command Processing

Commands are separated by **`;`** (wait for server prompt) or **`|`**
(send immediately, no prompt wait).  For example, `get all;drop sword`
sends "get all", waits for the prompt, then sends "drop sword".
A repeat prefix like `3n;2e` expands to `n;n;n;e;e`.

See the **Command Syntax** section (F8 macro editor help) for the full
reference on backtick commands, condition gates, travel, and more.
