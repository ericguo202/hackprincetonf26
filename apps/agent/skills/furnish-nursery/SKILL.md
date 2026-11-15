---
name: furnish-nursery
description: "Furnish a nursery for an infant: crib on an interior wall never under a window, the crib doubles as the changing spot with a dresser beside it, a rocking chair for feeds, clearances"
---

# furnish-nursery

Use this for "set up a nursery", "a room for the baby", "we're new parents, furnish the small bedroom",
or any bedroom request that is for an infant. For an older child's playroom, use `furnish-for-play`.
The room is usually the small bedroom next to the primary bedroom (the room scout picks it from the graph).

## Pieces (curator: search these categories; planner: place them in this order)

| Order | Category | Priority | Rule (`propose_poses`) | relative_to | Notes |
|---|---|---|---|---|---|
| 1 | `crib` | 1 | `against_wall` | none | Never under a window (the tools refuse it). Prefer an interior wall away from the door. Room under 9 m2 or longest free wall under 1.6 m: the compact crib (tag `small_room`). |
| 2 | `dresser` | 1 | `beside` | the crib | Diapers and clothes at arm's reach. The library has no changing table: the crib is the changing spot, so the dresser stands right next to it. |
| 3 | `rocking_chair` | 2 | `corner` | none | For night feeds. If no corner fits, `beside` the crib on the side away from the dresser. |
| 4 | `rug` | 2 | `center` | none | Soft floor for crawling; rugs never block anything. |
| 5 | `floor_lamp` | 3 | `beside` | the rocking chair | Soft light for night feeds, instead of the ceiling light. |
| 6 | `toy_storage` | 3 | `against_wall` | none | Low, on a wall the crib does not use. |
| 7 | `play_mat` | 3 | `center` | none | Tummy time. Skip it when the rug is placed in a room under 12 m2. |

Small room (under 9 m2): place only priority 1 and 2 pieces. Always say in the answer that the crib
doubles as the changing station, since there is no changing table.

## Infant safety (why the table looks like this)

- Crib not under or beside a window: blind and curtain cords and falls are the risk (CPSC window-covering
  cord guidance). `validate_layout` reports any crib in a window's keep-clear strip as `blocks_opening`,
  and `propose_poses` never offers one there.
- Nothing tall in the nursery: no `bookshelf` or `wardrobe` (full-height pieces can tip). The dresser is
  the only storage beside the crib.
- Keep the crib out of the door swing and leave the 0.75 m walkway to it: the tools enforce both.

## Rules the tools already enforce (do not re-check by hand)

- Door swings kept clear, 0.75 m walkway in front of pieces, nothing tall in front of a window, no crib
  under a window.
- Every pose `propose_poses` returns already passes `validate_layout` against what is placed.

## When something does not fit

- No poses for the crib: try the compact crib (search `crib` with `max_w` 1.0). Without a crib this is
  not a nursery: say so instead of committing a plan without one.
- No poses for another piece: read the `note`, try the next rule in its row, else drop it and say so.
- After 3 validate-and-fix rounds, drop the lowest-priority piece in a conflict and report it in `dropped`.

## Regenerate ("try another layout", "different furniture")

- The curator saves one or two `alternates` per piece (runner-ups from the same search), and `rule` and
  `relative_to` (the asset_id of the piece it faces or sits beside) from the table above.
- Another layout: `place_picks(new_layout=true)` moves the first piece placed (the anchor, the crib)
  somewhere clearly different from every earlier layout; the pieces placed relative to it follow.
- Different furniture: `place_picks(new_furniture=true)` swaps in the alternates not used before.
- No detail ("regenerate", "I don't like it"): both. If the room has no clearly different spot left, say so.
