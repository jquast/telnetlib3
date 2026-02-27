## Room Browser

The room browser displays a searchable, hierarchical view of all rooms
discovered via GMCP.  Rooms are grouped by name and can be filtered by
area.

### Buttons

| Button | Action |
|--------|--------|
| **Travel** | Fast travel to the selected room (skip exclusive autoreplies) |
| **Slow** | Slow travel (wait for autoreplies in each room) |
| **Help** | Open this help screen |
| **Close** | Close the room browser |

### Marker Buttons (bottom bar)

| Button | Action |
|--------|--------|
| **Bookmark ╾** | Toggle a bookmark on the selected room |
| **Block ⌀** | Toggle block — blocked rooms are excluded from all travel |
| **Home ⌂** | Set as home room for this area (one per area) |
| **Mark ➽** | Toggle a visual marker (no functional effect) |

### Tree View

Rooms are grouped by name.  Parent nodes show the room name with a count
of matching rooms and distance or last-visit info.  Leaf nodes show the
room ID.  A column heading row shows the field layout.

### Area Filter

Use the **Area** dropdown on the left to restrict the tree to a single
area.  Select the blank entry to show all areas.

### Keyboard Shortcuts

| Key | Action |
|-----|--------|
| **Enter** | Fast travel to the selected room |
| **\*** | Toggle bookmark on the selected room |
| **B** | Toggle block on the selected room |
| **H** | Toggle home on the selected room |
| **M** | Toggle mark on the selected room |
| **N** | Sort rooms by name |
| **I** | Sort rooms by ID |
| **D** | Sort rooms by distance from current room |
| **L** | Sort rooms by last-visited time |
| **F1** | Open this help screen |
| **Escape** | Close the room browser |

### Search

Type in the search field to filter rooms by name.  The search matches
room names case-insensitively.  Use arrow keys to move between the
search field and the tree.

### Bookmarks

Bookmarked rooms are marked with **╾** in the tree.  Use the
Bookmark button or press **\*** to toggle the bookmark on the selected
room.

### Blocked Rooms

Blocked rooms are marked with **⌀** and are excluded from all travel
pathfinding, random walk, and autodiscover.  Use this to prevent travel
through rooms you want to avoid (e.g. a dangerous stairwell or a
one-way trap).

### Home Rooms

Home rooms are marked with **⌂**.  Only one home room can be set per
area.  Setting a new home in an area clears the previous one.  Use the
backtick command `` `home` `` to fast travel to the home room of your
current area.

### Marks

Marked rooms are marked with **➽**.  Marks are purely visual with no
functional effect — use them to flag rooms of interest.

### Fast Travel vs Slow Travel

- **Fast travel** moves through rooms without stopping, skipping
  exclusive autoreplies.  Use this for quick navigation.
- **Slow travel** waits for autoreplies to finish in each room along
  the path.  Use this when autoreplies need to fire during movement.
