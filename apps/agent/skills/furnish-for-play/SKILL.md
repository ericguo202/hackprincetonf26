---
name: furnish-for-play
description: "Furnish a room for play: a nerf-war arena with cover on both sides, or a kids' playroom"
---

# furnish-for-play

Use this for "set up the living room for a nerf war", "make it a playroom instead", "somewhere the kids can
play". The furniture becomes the map: darts stop on furniture, players hide behind it.

## Cover, from the catalog's `cover` field

- `full` (taller than 1.2 m, e.g. bookshelf, wardrobe): blocks a standing player.
- `half` (0.5 to 1.2 m, e.g. sofa, sectional, armchair, beanbag): cover when crouching.
- `none` (rug, play mat): no cover.

## Nerf war

| Order | Category | Priority | Rule | relative_to | Why |
|---|---|---|---|---|---|
| 1 | `sofa` or `sectional` | 1 | `against_wall` | none | Main half cover on one side. |
| 2 | `bookshelf` | 1 | `against_wall` | none | Full cover on the opposite wall from the sofa. |
| 3 | `armchair` | 2 | `corner` | none | Half cover in a corner on the bookshelf's side. |
| 4 | `beanbag` (count 2) | 2 | `center` | none | Low cover in the open middle; spread them apart. |
| 5 | `ottoman` | 3 | `center` | none | Low cover between the sides. |
| 6 | `rug` | 3 | `center` | none | Marks the middle; no cover. |

Spread the cover: both halves of the room need at least one `half` or `full` piece, so neither team spawns
exposed. Prefer different walls for the sofa and the bookshelf. Keep the doorways and a 0.75 m path between
pieces clear: the tools enforce this, and players need routes to run.

## Playroom (kids)

| Order | Category | Priority | Rule | relative_to |
|---|---|---|---|---|
| 1 | `play_mat` | 1 | `center` | none |
| 2 | `toy_storage` | 1 | `against_wall` | none |
| 3 | `kids_table` | 2 | `center` | none |
| 4 | `kids_chair` (count 2) | 2 | `beside` | the kids table |
| 5 | `beanbag` | 2 | `corner` | none |
| 6 | `bookshelf` (low, `half` cover) | 3 | `against_wall` | none |
| 7 | `play_equipment` or `canopy_tent` | 3 | `corner` | none |

Keep tall `full` cover pieces off the playroom unless they stand against a wall.

## Regenerate ("try another layout", "different furniture")

- The curator saves one or two `alternates` per piece (runner-ups from the same search), and `rule` and
  `relative_to` (the asset_id of the piece it faces or sits beside) from the table above.
- Another layout: `place_picks(new_layout=true)` moves the first piece placed (the anchor) somewhere
  clearly different from every earlier layout; the pieces placed relative to it follow.
- Different furniture: `place_picks(new_furniture=true)` swaps in the alternates not used before.
- No detail ("regenerate", "I don't like it"): both. If the room has no clearly different spot left, say so.
