# Scan-position walls: what the game client has to do (for Lane C)

Written by Lane A on 2026-11-15. It covers one new thing in the house packs: the view past a house's yard. Background, thresholds and what was tried are in `docs/house-pipeline-handoff.md` section 3.13 item 10; you do not need them to implement this.

## What it is

Past the fence, a house built this way has four upright walls around the lot, 4 m outside it and 12 m high. They carry a photo of what lies beyond: the neighbours' houses, the street, far trees. There is one set of these four walls **per Matterport scan position** in the yard (15 for 7014 Navidad). Each set shows the photo taken from its scan position, so from that spot the view over the fence is exactly what Matterport shows there.

All sets sit in the same place. **The client must draw only one set at a time: the one whose scan position is nearest the player.** A client that draws every node draws 15 walls on top of each other, and the view past the fence flickers.

## What is in `interior.glb`

| Nodes | What | Draw |
|---|---|---|
| `backdrop_s<kk>_<page>` (for example `backdrop_s07_0`) | The walls of scan position `kk` (two digits, from 00). One node per texture page; today each set is one node, but treat every node with the same `kk` as one set. | Only the nearest set |
| `backdrop_<n>` (for example `backdrop_0`) | The ground strip between the yard and the walls, and the sky dome | Always, as before |

Each `backdrop_s…` node carries its scan position in glTF `extras`, on the node and on its mesh:

```json
"extras": { "sweep": [x, y, z] }
```

`sweep` is in the house-local frame of `rooms.json` and of the furniture placements: metres, Y up. three.js's `GLTFLoader` puts it in `object.userData.sweep`.

A house has this backdrop exactly when its `interior.glb` has nodes named `backdrop_s…`. A house without them (6915 Navidad today, and every older pack) needs nothing: its backdrop is the usual `backdrop_<n>` nodes.

## The rule

In a house zone, for the local player:

```
sets  = nodes whose name matches /^backdrop_s(\d+)_/, grouped by the number
eye   = the local camera position, in house-local coordinates
best  = the set whose `sweep` is nearest to `eye` (plain 3D distance)
show the nodes of `best`, hide the nodes of every other set
```

- Evaluate it when the player has moved about 0.25 m, or every frame; it is 15 distance checks.
- Each client decides for its own camera. Nothing goes over the network and no state or message changes.
- Indoors the rule is the same (the walls are what shows through windows and open doors).
- When the nearest position changes, the picture past the fence jumps a little. A cross-fade of about 0.3 s between the two sets hides most of it. It is optional; a hard switch is acceptable.

## How to render the walls

- **Unlit photo textures**, like every other photo texture of the house: no tone mapping, no lighting, no shadows cast or received.
- **Alpha cut-out**: the material is alpha MASK with cutoff 0.5 (the sky is cut out of the walls so the sky dome shows behind them). Keep the cutoff; do not blend.
- **Double sided**, as exported.
- **No collision and no hits**: they are not in `collision.glb`. Leave them out of dart ray casts and of anything that asks "what is the player looking at".
- **Not furniture cover, not spawn area.** The lot's `bounds` in `collision.glb` still stop the player at the yard's edge, 4 m before the walls.

## Memory

Each set is one texture of 2048 x 2048 or smaller. 15 sets are about 60 megapixels if all are on the GPU at once. three.js uploads a texture the first time its mesh is drawn, so with the rule above only the sets a player has stood near are uploaded. If memory is tight, dispose the textures of sets far from the player (keep the nearest three or four).

## How to check it works

Pack and house: see the latest lane-a line in `HANDOFF.md` for the pack version that has these walls for 7014 (`hou_236595179`).

1. Stand in 7014's back yard and turn around: over the fence you see sharp photos of the neighbours' roofs, with sky above them, no flicker.
2. Walk from the back yard along the side of the house to the driveway: the view past the fence changes in small jumps, each one near a spot where the picture is exactly right.
3. From the driveway, look across the street: the houses there are photos, at their real size.
4. Log the active set while walking: it changes about every 2 to 5 m.

If you see doubled or flickering houses past the fence, more than one set is visible.

## What Lane A can change if you need it

The node names, the `sweep` field, the wall height and distance, the texture size per set, and the number of sets (a set per second scan position halves the memory) are all choices in `worldgen/farfield.py` (`sweep_walls`). Ask in `HANDOFF.md`.
