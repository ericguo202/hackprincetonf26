---
name: furnish-open-plan
description: "Furnish one large living room for both lounging and dining (open plan, no separate dining room): TV, sofa and coffee table first, then a dining table with chairs in the open part of the room. For a living room without a dining table use furnish-living-room"
---

# furnish-open-plan

Use this for "furnish the living room for both lounging and family dinners", "living and dining in one
room", "we eat and watch TV in the same room", or any living-room request that also asks for a place to eat.
6915 Navidad has no dining room: its living room is the open plan. A living room with no dining table is
`furnish-living-room`; a room that is only for eating is `furnish-dining-room`; a nerf war or a playroom is
`furnish-for-play`.

## Pieces (curator: search these categories; planner: place them in this order)

The lounge group goes in first and takes the best wall; the dining group then takes the open floor that
is left beside it.

| Order | Category | Priority | Rule (`propose_poses`) | relative_to | Notes |
|---|---|---|---|---|---|
| 1 | `tv_stand` | 1 | `against_wall` | none | Prefer the longest free wall that is not `under_window` (glare). |
| 1b | `tv` | 1 | `center` | the TV stand | A TV with `placement: on_surface` sits on top of the stand; it must be no wider than the stand. |
| 2 | `sofa` | 1 | `facing` | the TV stand | A sofa, not a sectional: the room also has to hold the table. |
| 3 | `coffee_table` | 1 | `center` | the sofa | `center` with relative_to puts it 0.45 m in front of the seat. It goes in before the dining table so the table cannot take the spot between sofa and TV. |
| 4 | `dining_table` | 1 | `center` | none | The open floor beside the lounge group. `max_w` 1.65: the room also holds the sofa. |
| 5 | `dining_chair` (count 4) | 1 | `facing` | the dining table | One chair model. `facing` pulls each chair up to a free side of the table. A chair with no free side is dropped: say how many seats there are. |
| 6 | `rug` | 2 | `center` | none | Rugs never block anything. |
| 7 | `side_table` | 3 | `beside` | the sofa | |
| 8 | `floor_lamp` | 3 | `corner` | none | |
| 9 | `plant` | 3 | `corner` | none | |

Room under 24 m2: a loveseat instead of the sofa (search `loveseat`), a table for four (`max_w` 1.4), and
no priority 3 pieces. Under 18 m2 the room cannot hold both groups: say so and offer `furnish-living-room`
or `furnish-dining-room`.

## Rules the tools already enforce (do not re-check by hand)

- 0.75 m walkway in front of seating, 0.9 m clear in front of the TV stand and on the table's front side
  (its own chairs excepted), door swings kept clear, nothing tall in front of a window.
- Every pose `propose_poses` returns already passes `validate_layout` against what is placed.

## When something does not fit

- No `center` pose for the table: `against_wall`, on a wall the TV and sofa do not use. Still nothing:
  drop the dining group as a whole (table and chairs) and say so.
- The table landed between the sofa and the TV: switch it to an alternative pose off that line.
- A chair has no `facing` pose: leave it out; never put a dining chair away from the table.
- No poses for another piece: read the `note`, try the next rule in its row, else drop it and say so.
- After 3 validate-and-fix rounds, drop the lowest-priority piece in a conflict and report it in `dropped`.

## Regenerate ("try another layout", "different furniture")

- The curator saves one or two `alternates` per piece (runner-ups from the same search), and `rule` and
  `relative_to` (the asset_id of the piece it faces or sits beside) from the table above.
- Another layout: `place_picks(new_layout=true)` moves the first piece placed (the anchor, the TV stand) to
  a clearly different wall; the sofa follows it and the dining group takes the floor that is then free.
- Different furniture: `place_picks(new_furniture=true)` swaps in the alternates not used before.
- No detail ("regenerate", "I don't like it"): both. If the room has no clearly different spot left, say so.
