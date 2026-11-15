---
name: furnish-game-room
description: "Furnish a game room or teen hangout for video games and game nights: big screen, a sofa, beanbags facing the TV, a table for snacks and board games, shelves for the games. Comfort, not cover. For a nerf war or a kids' playroom use furnish-for-play"
---

# furnish-game-room

Use this for "turn the living room into a game room for teens", "a hangout for the kids and their
friends", "game night", "a console corner in the spare bedroom". It seats as many people as it can in front
of one screen. It is not `furnish-for-play`: a nerf war ("set up the living room for a nerf war") or a
playroom ("make it a playroom instead") is about cover and toys and belongs there. A family movie night
with no gaming is `furnish-living-room`. The room is the one the player names, else the living room.

## Pieces (curator: search these categories; planner: place them in this order)

| Order | Category | Priority | Rule (`propose_poses`) | relative_to | Notes |
|---|---|---|---|---|---|
| 1 | `tv_stand` | 1 | `against_wall` | none | Prefer the longest free wall that is not `under_window` (glare on the screen). |
| 1b | `tv` | 1 | `center` | the TV stand | A TV with `placement: on_surface` sits on top of the stand; it must be no wider than the stand. The largest screen the stand takes. |
| 2 | `sofa` | 1 | `facing` | the TV stand | The widest sofa that fits. A `sectional` only when the player asks for one and the room is over 30 m2. |
| 3 | `coffee_table` | 1 | `center` | the sofa | Snacks, controllers, board games. `center` with relative_to puts it 0.45 m in front of the seat. |
| 4 | `beanbag` (count 2) | 2 | `facing` | the TV stand | Floor seats in front of the screen: search `beanbag` with `max_w` 1.0. Room under 15 m2: count 1. |
| 5 | `rug` | 2 | `center` | none | Rugs never block anything. |
| 6 | `side_table` | 2 | `beside` | the sofa | Drinks within reach. |
| 7 | `ottoman` | 3 | `corner` | none | A spare seat to pull up. |
| 8 | `bookshelf` | 3 | `against_wall` | none | Board games and consoles. Tall: a wall the TV and sofa do not use. |
| 9 | `floor_lamp` | 3 | `corner` | none | Behind or beside the seating, never beside the screen. |

Small room (under 15 m2, a spare bedroom): priority 1 pieces, one beanbag and the rug; a `loveseat` instead
of the sofa when no sofa fits.

## Rules the tools already enforce (do not re-check by hand)

- 0.75 m walkway in front of seating, 0.9 m clear in front of the TV stand, door swings kept clear, nothing
  tall in front of a window.
- Every pose `propose_poses` returns already passes `validate_layout` against what is placed.

## When something does not fit

- A beanbag has no `facing` pose: `beside` the sofa, else `corner`, else leave it out.
- No poses for the sofa: a narrower sofa or a `loveseat` from the picks' alternates, `facing` the TV stand.
- No poses for another piece: read the `note`, try the next rule in its row, else drop it and say so.
- After 3 validate-and-fix rounds, drop the lowest-priority piece in a conflict and report it in `dropped`.

## Regenerate ("try another layout", "different furniture")

- The curator saves one or two `alternates` per piece (runner-ups from the same search), and `rule` and
  `relative_to` (the asset_id of the piece it faces or sits beside) from the table above.
- Another layout: `place_picks(new_layout=true)` moves the first piece placed (the anchor, the TV stand) to
  a clearly different wall; the seating follows it.
- Different furniture: `place_picks(new_furniture=true)` swaps in the alternates not used before.
- No detail ("regenerate", "I don't like it"): both. If the room has no clearly different spot left, say so.
