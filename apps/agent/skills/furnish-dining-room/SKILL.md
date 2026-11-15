---
name: furnish-dining-room
description: "Furnish a dining room for family dinners: dining table sized to the room, chairs facing it, rug under it, a bench or sideboard on a wall. For a living room that must keep its sofa and TV as well, use furnish-open-plan"
---

# furnish-dining-room

Use this for "set up the dining room for family dinners", "a table for six", "somewhere to eat together",
"a dining room for hosting". The room scout takes the `dining_room`; a house without one (open plan) gets
the table in its living room. If the player wants lounging and dining in the same room ("living room for
movies and dinners"), use `furnish-open-plan` instead: this skill fills the room with the table.

## Pieces (curator: search these categories; planner: place them in this order)

| Order | Category | Priority | Rule (`propose_poses`) | relative_to | Notes |
|---|---|---|---|---|---|
| 1 | `dining_table` | 1 | `center` | none | The middle of the room, so chairs fit on every side. Size it by the room (below). |
| 2 | `dining_chair` (count 4) | 1 | `facing` | the dining table | One chair model, count 4 (the most one pick takes). `facing` pulls each chair up to a free side of the table, long sides first, across from each other. A chair with no free side is dropped: fewer chairs is fine, say how many seats there are. |
| 3 | `rug` | 2 | `center` | none | Under the table; rugs never block anything. |
| 4 | `bench` | 2 | `against_wall` | none | Extra seats along a wall; a bench is low, so it may stand under a window. |
| 5 | `plant` | 3 | `corner` | none | |
| 6 | `dresser` | 3 | `against_wall` | none | The library has no sideboard: a low dresser on a wall serves as one. Only in a room of 12 m2 or more. |

Table size, from `area_m2` in the room file (search `dining_table` with this `max_w`):

- Under 10 m2: `max_w` 1.4 (a table for four).
- 10 to 16 m2: `max_w` 1.7.
- 16 m2 or more: any, the extendable table included.

Small room (under 9 m2): priority 1 and 2 only.

## Rules the tools already enforce (do not re-check by hand)

- Door swings kept clear, 0.9 m clear on the table's front side for everything but its own chairs,
  nothing taller than 1.0 m in front of a window (dining chairs are low enough).
- Every pose `propose_poses` returns already passes `validate_layout` against what is placed.

## When something does not fit

- No poses for the table: search `dining_table` again with a smaller `max_w`. Without a table this is not
  a dining room: say so instead of committing chairs alone.
- A chair has no `facing` pose: every free side of the table is taken or blocked. Leave it out; never put
  a dining chair in a corner or on a wall away from the table.
- No poses for another piece: read the `note`, try the next rule in its row, else drop it and say so.
- After 3 validate-and-fix rounds, drop the lowest-priority piece in a conflict and report it in `dropped`.

## Regenerate ("try another layout", "different furniture")

- The curator saves one or two `alternates` per piece (runner-ups from the same search), and `rule` and
  `relative_to` (the asset_id of the piece it faces or sits beside) from the table above.
- Another layout: `place_picks(new_layout=true)` moves or turns the first piece placed (the anchor, the
  table); the chairs follow it.
- Different furniture: `place_picks(new_furniture=true)` swaps in the alternates not used before.
- No detail ("regenerate", "I don't like it"): both. If the room has no clearly different spot left, say so.
