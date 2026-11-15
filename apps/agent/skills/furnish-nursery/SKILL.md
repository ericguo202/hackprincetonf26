---
name: furnish-nursery
description: "Furnish a nursery for an infant: crib on an interior wall never under a window, a changing table beside it, a dresser, a rocking chair for feeds, clearances. For children out of the crib use furnish-kids-room"
---

# furnish-nursery

Use this for "set up a nursery", "a room for the baby", "we're new parents, furnish the small bedroom",
or any bedroom request that is for an infant. For an older child's bedroom use `furnish-kids-room`; for a
playroom use `furnish-for-play`.
The room is usually the small bedroom next to the primary bedroom (the room scout picks it from the graph).

## Pieces (curator: search these categories; planner: place them in this order)

| Order | Category | Priority | Rule (`propose_poses`) | relative_to | Notes |
|---|---|---|---|---|---|
| 1 | `crib` | 1 | `against_wall` | none | Never under a window (the tools refuse it). Prefer an interior wall away from the door. Room under 9 m2 or longest free wall under 1.6 m: the compact crib (tag `small_room`). |
| 2 | `changing_table` | 1 | `beside` | the crib | The changing spot, right next to the crib; its open shelves hold the diapers. If no side of the crib is free, `against_wall`. |
| 3 | `dresser` | 2 | `against_wall` | none | Clothes. Low (`max_w` 1.0 in a room under 12 m2). In the smallest rooms it is dropped: the changing table's shelves are the storage. |
| 4 | `rocking_chair` | 2 | `corner` | none | For night feeds. If no corner fits, `beside` the crib on the side away from the changing table. |
| 5 | `rug` | 2 | `center` | none | Soft floor for crawling; rugs never block anything. |
| 6 | `floor_lamp` | 3 | `beside` | the rocking chair | Soft light for night feeds, instead of the ceiling light. |
| 7 | `toy_storage` | 3 | `against_wall` | none | Low, on a wall the crib does not use. |
| 8 | `play_mat` | 3 | `center` | none | Tummy time. Skip it when the rug is placed in a room under 12 m2. |

Small room (under 9 m2): place only priority 1 and 2 pieces.

## Infant safety (why the table looks like this)

- Crib not under or beside a window: blind and curtain cords and falls are the risk (CPSC window-covering
  cord guidance). `validate_layout` reports any crib in a window's keep-clear strip as `blocks_opening`,
  and `propose_poses` never offers one there.
- Nothing tall in the nursery: no `bookshelf` or `wardrobe` (full-height pieces can tip). The changing
  table and the dresser are the storage; both are under 1.0 m.
- The changing table stands beside the crib so the baby is moved one step, with a rail on three sides.
- Keep the crib out of the door swing and leave the 0.75 m walkway to it: the tools enforce both.

## Rules the tools already enforce (do not re-check by hand)

- Door swings kept clear, 0.75 m walkway in front of pieces, nothing tall in front of a window, no crib
  under a window.
- Every pose `propose_poses` returns already passes `validate_layout` against what is placed.

## When something does not fit

- No poses for the crib: try the compact crib (search `crib` with `max_w` 1.0). Without a crib this is
  not a nursery: say so instead of committing a plan without one.
- No poses for the changing table by either rule: keep the crib, put the dresser `beside` the crib as the
  changing spot, and say there was no room for the changing table.
- No poses for another piece: read the `note`, try the next rule in its row, else drop it and say so.
- After 3 validate-and-fix rounds, drop the lowest-priority piece in a conflict and report it in `dropped`.

## Regenerate ("try another layout", "different furniture")

- The curator saves one or two `alternates` per piece (runner-ups from the same search), and `rule` and
  `relative_to` (the asset_id of the piece it faces or sits beside) from the table above.
- Another layout: `place_picks(new_layout=true)` moves the first piece placed (the anchor, the crib)
  somewhere clearly different from every earlier layout; the pieces placed relative to it follow.
- Different furniture: `place_picks(new_furniture=true)` swaps in the alternates not used before.
- No detail ("regenerate", "I don't like it"): both. If the room has no clearly different spot left, say so.
