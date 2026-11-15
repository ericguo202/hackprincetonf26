---
name: furnish-kids-room
description: "Furnish a children's bedroom (toddlers to pre-teens, one child or two sharing): single beds on the walls, low toy storage, a small table and chairs, a rug. Children sleep here. For a baby use furnish-nursery; for a playroom or a nerf war use furnish-for-play"
---

# furnish-kids-room

Use this for "make the spare bedroom a room for our two kids", "a bedroom for our daughter", "the boys
share a room". The children sleep here, so the beds come first. For a baby in a crib use
`furnish-nursery`. For a playroom or a nerf war ("make it a playroom instead", "somewhere the kids can
play") use `furnish-for-play`: that one has no beds. The room is the one the player names, else the
largest spare bedroom (the room scout picks it).

## Pieces (curator: search these categories; planner: place them in this order)

| Order | Category | Priority | Rule (`propose_poses`) | relative_to | Notes |
|---|---|---|---|---|---|
| 1 | `bed` (count 1 per child, 2 at most) | 1 | `against_wall` | none | A single-width bed: search `bed` with `max_w` 1.05 (the library has no `twin_bed` category). The same bed for both children. |
| 2 | `toy_storage` | 1 | `against_wall` | none | Low, on a wall the beds do not use. Prefer the wide bin over the small cube. |
| 3 | `nightstand` | 2 | `beside` | the bed | One, shared; with two beds on one wall it lands between or next to them. |
| 4 | `rug` (or `play_mat`) | 2 | `center` | none | Soft floor to play on; never blocks anything. One of the two, not both, in a room under 12 m2. |
| 5 | `kids_table` | 2 | `center` | none | Drawing and homework. |
| 6 | `kids_chair` (count 2) | 2 | `facing` | the kids table | One per child; `facing` pulls each chair up to a free side of the table. |
| 7 | `dresser` | 3 | `against_wall` | none | Low (`max_w` 1.0). Clothes storage. |
| 8 | `beanbag` | 3 | `corner` | none | A reading spot (`max_w` 1.0). |

Small room (under 10 m2): beds, toy storage and rug only. If only one bed fits, place one and say so.

## Child safety (why the table looks like this)

- Nothing tall: no `bookshelf` and no `wardrobe` (full-height pieces can tip when climbed). Storage is the
  low toy storage and the dresser.
- Keep the floor in the middle open: the table and chairs are small and the rug marks the play area.

## Rules the tools already enforce (do not re-check by hand)

- Door swings kept clear, the walkway at the foot of each bed, nothing taller than 1.0 m in front of a
  window (a bed with a tall headboard counts).
- Every pose `propose_poses` returns already passes `validate_layout` against what is placed.

## When something does not fit

- The second bed has no pose: keep one bed and say the room sleeps one child; do not drop the toy storage
  to force it in.
- No bed at all fits: say so instead of committing a room without a bed.
- No poses for another piece: read the `note`, try the next rule in its row, else drop it and say so.
- After 3 validate-and-fix rounds, drop the lowest-priority piece in a conflict and report it in `dropped`.

## Regenerate ("try another layout", "different furniture")

- The curator saves one or two `alternates` per piece (runner-ups from the same search), and `rule` and
  `relative_to` (the asset_id of the piece it faces or sits beside) from the table above.
- Another layout: `place_picks(new_layout=true)` moves the first piece placed (the anchor, the first bed)
  to a clearly different wall; the other pieces follow.
- Different furniture: `place_picks(new_furniture=true)` swaps in the alternates not used before.
- No detail ("regenerate", "I don't like it"): both. If the room has no clearly different spot left, say so.
