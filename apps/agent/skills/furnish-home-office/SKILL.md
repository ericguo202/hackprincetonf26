---
name: furnish-home-office
description: "Furnish a home office for working from home, usually in a spare bedroom: desk against a wall, office chair facing it, bookshelf, desk lamp, plant. Not for a child's homework corner in a kids' room (furnish-kids-room)"
---

# furnish-home-office

Use this for "set up a home office in the spare bedroom", "I work from home, make me an office", "a study",
"a desk and shelves in the other bedroom". Neither hero house has a room labelled `office`: the office goes
in the room the player names, else the largest spare bedroom (the room scout picks it). For a bedroom that
stays a bedroom use `furnish-bedroom`.

## Pieces (curator: search these categories; planner: place them in this order)

| Order | Category | Priority | Rule (`propose_poses`) | relative_to | Notes |
|---|---|---|---|---|---|
| 1 | `desk` | 1 | `against_wall` | none | A desk is under 1.0 m, so it may stand under a window: daylight on the desk is good. Room of 11 m2 or more: the wider desk; smaller: `max_w` 1.2. |
| 2 | `office_chair` | 1 | `facing` | the desk | The library has no `desk_chair` category: `office_chair` is the desk chair. `facing` pulls it up to the middle of the desk's front. |
| 3 | `bookshelf` | 2 | `against_wall` | none | `max_w` 1.4. Tall: the tools keep it off the windows. A wall the desk does not use. |
| 4 | `table_lamp` | 2 | `center` | the desk | It stands on the desk (the search shows `placement: on_surface`). |
| 5 | `plant` | 3 | `corner` | none | |
| 6 | `rug` | 3 | `center` | none | Rugs never block anything. |
| 7 | `armchair` | 3 | `corner` | none | A reading chair, only in a room of 10 m2 or more. |
| 8 | `floor_lamp` | 3 | `beside` | the armchair | Only with the armchair; else skip it. |

Small room (under 9 m2): priority 1 and 2 only. The library has no filing cabinet, monitor or printer: do
not promise them.

## Rules the tools already enforce (do not re-check by hand)

- Door swings kept clear, 0.9 m clear in front of the desk for everything but its own chair, nothing
  taller than 1.0 m in front of a window.
- Every pose `propose_poses` returns already passes `validate_layout` against what is placed.

## When something does not fit

- No poses for the desk: search `desk` again with a smaller `max_w`. Without a desk this is not an office:
  say so instead of committing a chair and shelves alone.
- No `facing` pose for the chair (the desk's front is blocked): move the desk to another wall with
  `propose_poses`, else drop the chair and say so. Never leave the chair somewhere else in the room.
- No poses for another piece: read the `note`, try the next rule in its row, else drop it and say so.
- After 3 validate-and-fix rounds, drop the lowest-priority piece in a conflict and report it in `dropped`.

## Regenerate ("try another layout", "different furniture")

- The curator saves one or two `alternates` per piece (runner-ups from the same search), and `rule` and
  `relative_to` (the asset_id of the piece it faces or sits beside) from the table above.
- Another layout: `place_picks(new_layout=true)` moves the first piece placed (the anchor, the desk) to a
  clearly different wall; the chair and lamp follow it.
- Different furniture: `place_picks(new_furniture=true)` swaps in the alternates not used before.
- No detail ("regenerate", "I don't like it"): both. If the room has no clearly different spot left, say so.
