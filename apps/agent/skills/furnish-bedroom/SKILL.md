---
name: furnish-bedroom
description: "Furnish a bedroom for adults, a couple's primary bedroom or a guest room: bed with its head against a wall and sized to the room, a nightstand on each free side, dresser, rug, lamp. Not for a baby (furnish-nursery) or for children (furnish-kids-room)"
---

# furnish-bedroom

Use this for "furnish the primary bedroom for a couple", "set up the bedroom", "make the spare bedroom a
guest room", "a room for my parents when they visit". For a baby use `furnish-nursery`; for children who
sleep there use `furnish-kids-room`; for a room that becomes an office with no bed use
`furnish-home-office`. A bedroom that also needs a work corner ("a couple who works from home", "with a
desk") stays here: add the desk rows below.

## Pieces (curator: search these categories; planner: place them in this order)

| Order | Category | Priority | Rule (`propose_poses`) | relative_to | Notes |
|---|---|---|---|---|---|
| 1 | `bed` | 1 | `against_wall` | none | The back of a bed is its head, so `against_wall` puts the head on the wall. Size it by the room (below). |
| 2 | `nightstand` (count 2) | 1 | `beside` | the bed | One on each side. If only one side is free the second is dropped: one is enough, never put it anywhere else. |
| 3 | `dresser` | 2 | `against_wall` | none | A wall the bed does not use. |
| 4 | `rug` | 2 | `center` | none | Rugs never block anything; it may lie partly under the bed. |
| 5 | `table_lamp` | 3 | `center` | the nightstand | It stands on the nightstand, so it must be no wider and no deeper than it. |
| 6 | `wardrobe` | 3 | `against_wall` | none | Primary bedroom only. Tall: the tools keep it off the windows. Room under 15 m2: the one-door wardrobe (`max_w` 0.6). |
| 7 | `armchair` | 3 | `corner` | none | A reading chair. Guest room: a place to put a bag down. Skip it when there is a desk. |
| 8 | `desk` | 1 when the brief says they work from home or asks for a desk, else skip | `against_wall` | none | A wall the bed does not use; under 1.0 m, so it may stand under a window. `max_w` 1.2. |
| 9 | `office_chair` | 1 with the desk | `facing` | the desk | `facing` pulls it up to the desk's front. |

Work corner: place the desk and chair before the dresser (search them with the others; in save_picks put
them right after the nightstands) so they get a wall, and drop the wardrobe first if the room is tight.

Bed size, from `area_m2` and the longest free wall in the room file (search `bed` with this `max_w`):

- 15 m2 or more: any bed, king included (`max_w` 2.05).
- 13 to 15 m2, or a smaller room whose longest free wall is 3 m or more: a queen (`max_w` 1.65).
- Otherwise: a single-width bed (`max_w` 1.05) with one nightstand (count 1).

Guest room: the same table without the wardrobe; one nightstand is enough.

## Rules the tools already enforce (do not re-check by hand)

- Door swings kept clear, the walkway at the foot of the bed, 0.75 m in front of the dresser and wardrobe,
  nothing taller than 1.0 m in front of a window (a tall headboard counts).
- Every pose `propose_poses` returns already passes `validate_layout` against what is placed.

## When something does not fit

- No poses for the bed: the room is too small for that bed. A room without a bed is not a bedroom: say so
  instead of committing nightstands and a dresser alone, and offer a narrower bed.
- No poses for another piece: read the `note`, try the next rule in its row, else drop it and say so.
- After 3 validate-and-fix rounds, drop the lowest-priority piece in a conflict and report it in `dropped`.

## Regenerate ("try another layout", "different furniture")

- The curator saves one or two `alternates` per piece (runner-ups from the same search), and `rule` and
  `relative_to` (the asset_id of the piece it faces or sits beside) from the table above.
- Another layout: `place_picks(new_layout=true)` moves the first piece placed (the anchor, the bed) to a
  clearly different wall; the nightstands and lamp follow it.
- Different furniture: `place_picks(new_furniture=true)` swaps in the alternates not used before.
- No detail ("regenerate", "I don't like it"): both. If the room has no clearly different spot left, say so.
