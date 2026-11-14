---
name: furnish-living-room
description: "Furnish a living room (movie nights, family time, reading): which pieces, in which order, with which placement rule"
---

# furnish-living-room

Use this for any living-room request that is not about play ("for a family that watches movies together",
"cozy", "for reading", "for guests"). For a nerf war or a playroom, use `furnish-for-play`.

## Pieces (curator: search these categories; planner: place them in this order)

| Order | Category | Priority | Rule (`propose_poses`) | relative_to | Notes |
|---|---|---|---|---|---|
| 1 | `tv_stand` | 1 | `against_wall` | none | Prefer the longest free wall that is not `under_window` (glare). |
| 2 | `sofa` (or `sectional` in a room over 30 m2) | 1 | `facing` | the TV stand | `facing` keeps it 2.2 to 3.6 m from the TV and backs it to a wall when it can. |
| 3 | `coffee_table` | 1 | `center` | the sofa | `center` with relative_to puts it 0.45 m in front of the seat. |
| 4 | `rug` | 2 | `center` | none | Under the seating area; rugs never block anything. |
| 5 | `side_table` | 2 | `beside` | the sofa | Backs aligned with the sofa. |
| 6 | `armchair` | 2 | `facing` the TV, else `beside` the sofa | the TV stand or sofa | Never plain `center`: a chair floating mid-room faces nothing. |
| 7 | `floor_lamp` | 3 | `corner` | none | Or `beside` the sofa on the other side from the side table. |
| 8 | `bookshelf` | 3 | `against_wall` | none | A wall the sofa and TV do not use. Tall (full cover). |
| 9 | `plant` | 3 | `corner` | none | |

Movie nights: priorities 1 and 2 matter most; pick the larger sofa that fits (more seats) and an armchair.
Small room (under 15 m2): skip priority 3 pieces and the armchair.

## Rules the tools already enforce (do not re-check by hand)

- 0.75 m walkway in front of seating, door swings kept clear, nothing tall in front of a window.
- Every pose `propose_poses` returns already passes `validate_layout` against what is placed.

## When something does not fit

- No poses: read the `note`. Try the next rule in the table, a different `relative_to`, or ask the curator
  for a smaller piece (in an edit, search is not available to the planner: drop it and say so).
- After 3 validate-and-fix rounds, drop the lowest-priority piece in a conflict and report it in `dropped`.

## Regenerate ("try another layout", "different furniture")

- The curator saves one or two `alternates` per piece (runner-ups from the same search), and `rule` and
  `relative_to` (the asset_id of the piece it faces or sits beside) from the table above.
- Another layout: `place_picks(new_layout=true)` moves the first piece placed (the anchor) somewhere
  clearly different from every earlier layout; the pieces placed relative to it follow.
- Different furniture: `place_picks(new_furniture=true)` swaps in the alternates not used before.
- No detail ("regenerate", "I don't like it"): both. If the room has no clearly different spot left, say so.
