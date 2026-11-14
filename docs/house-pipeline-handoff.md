# Handoff: turning Matterport scans into playable house maps (Lane A)

Written 2026-11-14 at the end of a long session, for the next coding agent working on the house pipeline. Read `AGENTS.md` first (sections 3, 7, 9 and 11 matter most here), then this file.

Updated 2026-11-14 by a second session (branch `lane-a/house-quality`): wall-attached fixtures become clean boxes, open doors become slabs, hanging fans are removed, unseen walls fall back to the scan's texture, and quality is now judged from views away from the scan positions. Section 3.6 is the log of that session; sections 3.4, 4, 5, 6, 7 and 9 were brought up to date. Section 3.7 is its second round (same day): one photo per box face, texture synthesis for what no camera saw, the bar ledge, toilets left as scan. Section 3.8 is its third round: nothing may be missing (see-through pillars, half toilets, wall ends, the wall under a bar ledge) and hand removal of clutter.

The goal of this work: given any Matterport model of a home, produce a house pack that feels like walking through the real house (as close to Matterport's own viewer as possible), with straight walls and clean doorways, collision that plays well in a nerf fight, and room data the furnishing agent can use. The current result is good on both demo houses. This document records exactly what was built, what was tried and rejected and why, how quality is measured, and a ranked list of what to improve next so the pipeline keeps getting better and works on new properties.

---

## 1. Where things are

| What | Where |
|---|---|
| Branch | First session: merged into `main` (2026-11-14, after contracts v2.3.0). Second session: `lane-a/house-quality`, worktree `/Users/pfu/hp-house-quality`, not merged or committed yet when this was written. Start further house work on a fresh `lane-a/<topic>` branch from `main`. |
| Checkout and data | Main checkout `/Users/pfu/hackprincetonf26`. All gitignored outputs below now live in its `worldgen/out/` (moved from the old worktree). New worktrees symlink `worldgen/out` and `.env` to the main checkout's, so every worktree shares one data folder. **Several agents build into that folder at the same time**: experiment in your own pack version (`--version <N>`, see section 9), never in one somebody else is writing. |
| Pipeline code | `worldgen/build_house.py` (orchestration, rooms, doors, graph, collision), `worldgen/rebake.py` (clean shell, carving, fixtures, texture baking, glb export), `worldgen/panos.py` (panorama sampling, ray-caster, multi-view shading), `worldgen/house_mesh.py` (OBJ reading, collision.glb, gltf-transform compression, the older interior writers), `worldgen/sources/matterport.py` (the only Matterport client: read-only, cached), `worldgen/pack.py` (manifest; `furniture_catalog` is the shared library `furniture/v1/catalog.json`, contracts v2.3.0), `worldgen/publish.py` (`--local` only; also copies the furniture library). |
| Per-house config | `worldgen/blocks/hou_navidad_01.json` (listing id to Matterport model, which interior method to use, hero role). `worldgen/overrides/<model_id>.json` (hand room splits). |
| Tests | `worldgen/tests/test_build_house.py`, `worldgen/tests/test_rebake.py` (shell, carving, shading, relief boxes, door slabs, hanging objects), `worldgen/tests/test_pack.py` (manifest). |
| Preview and scoring tools | `worldgen/scripts/preview/` (see section 6). Not part of the package; run with `uv run python scripts/preview/<name>.py` from `worldgen/`. |
| Outputs (gitignored) | `worldgen/out/packs/hou_navidad_01/v<N>/houses/<listing_id>/{interior.glb, collision.glb, rooms.json, graph.json}` and `manifest.json`. Downloads in `worldgen/out/raw/<model_id>/` (MatterPak zip and OBJ, skybox faces, defurnished OBJ for 6915), caches in `worldgen/out/cache/` (Matterport JSON; uncompressed glbs in `cache/glb/<model>_interior_pano.glb` for v1 and `..._pano_v<N>.glb` for other versions). About 1.1 GB for the two houses. |
| Pack versions on disk (2026-11-14) | `v1`: first-session build, the one other lanes load; not rebuilt with the second-session code yet. `v9`: second-session build of both houses (the result described in 3.6). `v2`: in use by another agent (outdoor work); its 7014 house was built by an intermediate second-session revision. |
| Notes for other lanes | `HANDOFF.md` entries dated 2026-11-14 16:30 to 23:40. |

### The two houses (block `hou_navidad_01`, Houston 77083)

| | 7014 Navidad Rd (HERO, chosen by the user) | 6915 Navidad Rd (second) |
|---|---|---|
| Listing id | `hou_236595179` | `hou_237901269` |
| Matterport model | `y1cVPR1HsnL` | `xpspAtb5H59` |
| Rooms | living_room r3 28.2 m2 (vaulted), kitchen r4, dining_room r5, garage r6 36.9 m2, primary_bedroom r7, bedroom r8, r9, bathroom r10, r11, hallway r12, closets r1, r2 | closet r1, r2, kitchen r3, living_room r4 32.3 m2, primary_bedroom r5, bedroom r6, r7, hallway r8, bathroom r9, r10 |
| Panoramas | 38 total, 23 indoors | 31 total, 20 indoors |
| Doors listed / found open in scan | 16 / 10 | 13 / 8 |
| interior.glb / collision.glb | 4.9 MB / 91 KB | 3.4 MB / 132 KB |
| Texture pages (2048 px) | 17 | 15 |
| Score (section 6) | 14.07 mean abs error, 0.09% holes | 14.76, 0.01% holes |

Listing id convention (proposed, not yet agreed with whoever writes `costar.py`): `hou_` + CoStar ListingId. The demo's furnish target is `hou_236595179.r3`.

### Commands

```
cd worldgen
uv sync && npm i                                   # npm installs gltf-transform (compression)
uv run python -m worldgen.build_house --block hou_navidad_01 --listing hou_236595179   # ~5 min
uv run python -m worldgen.build_house --block hou_navidad_01 --listing hou_237901269
uv run pytest && uv run ruff check . && uv run python ../contracts/validate.py out/packs/hou_navidad_01
uv run python -m worldgen.publish hou_navidad_01 --local     # copies the pack into apps/web/public/packs

cd ../apps/web
VITE_PACK_BASE_URL=/packs/packs/hou_navidad_01/v1 VITE_START_ROOM=hou_236595179.r3 VITE_SOLO=1 npx -y pnpm@12.18.0 dev --port 5199
```

`npx -y pnpm@12.18.0` is needed because the repo pins pnpm 12.18.0 and the locally installed pnpm fails to switch to it. `--refresh` on build_house re-fetches the Matterport model JSON. `--bake-res` (texels per metre on walls and floors, default 256) and `--texture-size` (max texture edge after compression, default 2048) are the quality/size knobs.

Other build_house switches (second session):

| Switch | Use |
|---|---|
| `--version <N>` | Which pack version to write. Use your own number for experiments; the QA viewer opens it with `?pack=hou_navidad_01/v<N>`. |
| `--debug-dir <dir>` | Writes one image per wall, `relief_room<i>_wall<j>.png`: how far the scan stands out of the wall (brighter = further) with the fitted boxes outlined in red. The first thing to look at when a box is wrong or missing. |
| `--no-boxes` | Fallback: no boxes and no door slabs; wall-attached fixtures stay scan triangles, as in the first session. Use it when box fitting misbehaves on a new scan and there is no time to find out why. |
| `--holdout 3,11,18` | Leaves those panorama labels out of the bake, for an honest score (section 6). Use with `--version 99`; never ship such a build. |

---

## 2. The data we have per Matterport model

All read-only through `worldgen/sources/matterport.py`, which rejects GraphQL mutations (the keys can buy things; never unlock `mp:imagery` or anything else that is `locked`).

| Data | How we get it | Notes |
|---|---|---|
| Room vectors | `floors { rooms { classifications { label confidence } dimensionEstimates(units: metric) { area height ... } boundary { edges { id type thickness vertices { position { x y } } openings { type label width height } } } } }` | The floor plan. Edge ids are shared by the two rooms on either side of a wall. Edge `type` is `wall` or `invisible` (open plan). Opening `type` is `doorway`, `window` or `divider`. **Openings have width and height but no position along the edge.** Room `label` is empty; use `classifications[0].label`. Labels seen: Kitchen, Bedroom, Bathroom, Hallway, Garage, Void, None. No "Living Room": Matterport lumps open-plan living, dining and kitchen into one "Kitchen". |
| Panoramas | `panoLocations { id label position {x y z} rotation {x y z w} skybox(resolution: "2k") { status children } }` | **This is the key discovery of the session.** `children` are 6 signed cube-face JPG URLs per scan position (`status: available` on our keys; resolutions `low`, `high`, `2k`, `4k`). These are the photos Matterport's viewer shows. 2k = 6 faces of 2048x2048, about 5 MB per position. Not the locked `mp:imagery` bundle. |
| MatterPak | bundle `mp:matterpak` (unlocked), one zip asset | Full-resolution textured OBJ of the scan (200-470k triangles, about 60-90 JPG atlases) plus a 600 MB `cloud.xyz` point cloud we skip. Geometry is the real scan (lumpy). Its texture is Matterport's "dollhouse" skin: low quality, full of silver smeared patches where no camera saw the surface. |
| Defurnished mesh | `assets.meshes(formats: "obj")`, file `defurnished_50k.obj` | 50k triangles, **no texture at all**, but every group is semantically labelled `_typeNNN`: 042 floor, 041 walls, 043 ceiling, 032/024/033 windows, 003 outdoor ground, 071 countertop, 072 fireplace, others fixtures. Exists for about 70% of models (6915 has it; 7014 does not, it has `out_res_50k.obj` without semantics). |
| Scan positions | `panoLocations.position` | Camera height is about 1.0-1.6 m. Some positions are outdoors (drone or yard sweeps): 15 of 38 for 7014. |

Frames: Matterport is Z-up `(x, y, z)`. The pack's house-local frame is Y-up: `X = x, Y = z, Z = -y` (`house_mesh.Z_UP_TO_Y_UP`). rooms.json polygons are `[x, z]` = `(x, -y)` and counter-clockwise from above. All internal geometry stays Z-up until export.

**Panorama convention** (`panos.py` docstring, found by brute force in `scripts/preview/calib_panos.py`): `rotation` is camera-to-world; in the camera frame +X is forward, -Y is right, +Z is up. Faces: 0 up (image top points backward), 1 forward, 2 right, 3 back, 4 left, 5 down (image top points forward). The centre of face 5 is a blurred tripod disc (radius about tan 32 degrees). Calibration method: cast rays from pano centres into the MatterPak mesh, read the mesh's own texture colour at each hit, and score all 16 candidate mappings (4 forward axes x 2 handedness x rotation or inverse) by colour error against the faces. The right one scored 22, all others 35 or more; up and down faces were solved the same way. If a future model's panoramas look rotated or mirrored, rerun that script before touching anything else.

---

## 3. What was tried, in order, and why each was kept or dropped

This is the history the user asked for. Each step names the symptom that drove the next one.

### 3.1 Defurnished mesh with flat colours (kept as fallback, `interior_source: "defurnished"`)

Read `defurnished_50k.obj`, crop to the house footprint (union of room polygons, buffered 0.35 m, between floor - 0.3 and ceiling + 0.3), give each semantic class a flat PBR colour, export. Clean and small (262 KB), but it looks like a grey clay model. The user wanted it to look like the real house. Still available because it needs no textures and it is the fallback for scans that are not vacant.

### 3.2 Raw MatterPak textured mesh (kept as `interior_source: "matterpak"`, not recommended)

Cropped the MatterPak OBJ to the house, converted to Y-up, compressed with gltf-transform (meshopt geometry, WebP textures): 16.9 MB. Bugs found on the way:

- Pillow was not installed, so trimesh silently loaded every texture as None. `load_scan` and `write_textured_interior_glb` now raise if any texture is missing.
- `gltf-transform unlit` rewrites the file without meshopt compression; it must run before `optimize` (`house_mesh.compress_glb`).

Symptom: "crumpled silver foil" everywhere. Two causes, found separately:
1. The mesh has no normals and the viewer lit it, so every tiny lumpy triangle was shaded as its own facet. Fixed by `KHR_materials_unlit` on every material (the photos already contain the lighting). This is still applied to every interior.
2. The MatterPak texture itself contains smeared silver patches (surfaces no camera saw well), and the geometry is lumpy, so wall edges are not straight. Unlit could not fix that.

### 3.3 Clean shell textured by projecting the MatterPak texture (superseded)

Rebuilt walls, floors and ceilings as flat planes from the room vectors and projected the MatterPak texture onto them (orthographic rasterisation of nearby scan triangles into each plane's texture). Sub-steps, each fixing the previous symptom:

- Front-most surface per texel picked spiky noise that stuck out of walls. Switched to "surface nearest the plane".
- Nearest-to-plane picked the outside brick of exterior walls. Restricted to scan faces whose normal faces into the room (normals are consistent: 99.5% of floor faces point up).
- Texels with no scan behind them were filled by copying the nearest pixel, which made long streaks. Replaced with normalised convolution at increasing blur scales (`rebake._fill`, still used).
- Silver speckles that live in Matterport's texture were masked with a "small, darker, greyer blob than the surrounding paint" filter.

Symptom the user reported after this: door frames and edges still rough, some walls still speckled. Root cause: the MatterPak texture is simply poor wherever the scan was poor. No amount of filtering recovers information that is not there. This led to the panorama approach.

### 3.4 Clean shell + scan pieces, textured from the original panoramas (CURRENT, `interior_source: "panorama"`, the default)

The idea: Matterport's viewer looks good because it shows the panoramas, so use the panoramas as the texture source and the floor plan plus the scan as geometry. The pipeline, in the order `build_house.build` runs it:

1. **Rooms** (`read_rooms`, `chain_polygon`, `map_label`): polygonise each room's edges, map labels, turn small doored "Void" rooms (under 4 m2) into closets, drop outdoor and empty rooms. Hand splits from `overrides/<model>.json` (`apply_splits`; a split can target a room by label plus a point inside it via `"at"`, so a room can be split twice). The largest bedroom becomes `primary_bedroom` (`infer_primary`, confidence capped at 0.7).
2. **Floor height** (`scan_floor_z`): area-weighted mode of upward-facing MatterPak faces in a 2 cm histogram per floor, refined by a weighted mean around the mode.
3. **Doors** (`runs_from_rays`, `place_doors`, `opening_spans`): Matterport gives door widths only. Along each wall edge, every 5 cm, at five heights (0.3 to 1.5 m), cast rays across the wall from 0.25 m either side. A bin is "doorway" when at most one of the five heights hits a scanned surface. Each listed door goes to the unused gap whose width is closest to its own (gap between 0.5x and max(1.8x, x + 0.4 m) of the door width). A door with no matching gap (closed during the scan) is spread evenly along the edge and marked `open: False`. On the defurnished path the same matching uses gaps in the semantic wall surface (`runs_from_points`).
4. **rooms.json and graph.json** (`rooms_json`, `graph_json`): door and window openings in house-local coordinates, `to_room` from shared edge ids (only rooms whose outline passes through the opening's midpoint count, which matters for split rooms). Split parts are linked by an `open` edge whose width is the shared border minus walls along it (`split_open_width`).
5. **Indoor panoramas only** (`write_rebaked_interior_glb`): panoramas outside the union of room polygons are dropped. Outdoor sweeps saw doors open that indoor ones saw closed, saw the roof, and produced ghosting.
6. **Ceilings** (`measure_ceiling`): if under 80% of the downward-facing scan area above 1.8 m lies within 10 cm of the room's listed height, the ceiling is "not flat" (7014's vaulted living room and one more room). Those rooms keep the scanned ceiling as geometry and their walls rise to the 99.5th-percentile ceiling height + 5 cm.
7. **Shell planes** (`wall_runs`, `shell_planes`): inset each room polygon by `WALL_INSET` (0.05 m, about half a wall thickness), walk its outline, and keep the stretches that lie on a real wall edge (gaps under 0.3 m between wall pieces are closed; ends within 0.25 m of a corner are snapped to it, which removed cracks at corners). Floors and flat ceilings use the centreline polygon so neighbouring rooms meet under doorways. Where an open passage leads into a room with a lower ceiling, a "header" wall plane closes the step (this fixed holes at the vaulted-to-flat junction).
7a. **Wall ends at open edges** (`_open_end`, in `shell_planes`; second session, round three). Two rooms that share an open edge (a room-split line, an invisible edge) each stop their wall faces 5 cm short of it, so a wall that really runs straight on had a 10 cm strip of nothing, and a corner where two walls meet at such an edge had a 12 cm one (black strips in the viewer). Now every wall face that ends where its room's outline goes on without a wall looks up to 0.16 m ahead (`END_REACH`) for another room's wall face: one continuing in the same line gets half the gap (both extend and meet), one crossing the line gets the whole distance. A face that finds nothing ends in open space (a wall stub in an open plan): it gets an end cap, a plane across half the wall thickness, like a door jamb. Also in `wall_runs`: only walls that run along a stretch of the outline (direction within about 35 degrees) count as covering it; before, the slab of a wall meeting the outline at a right angle produced a 12 cm strip of wall face in thin air.
8. **Baking each plane** (`bake_plane`): one texel per 1/256 m (capped so the plane fits a 2048 page). For every texel, find the real scanned surface along the normal: first a per-plane median offset (the plan line can be several cm off a thick outer wall), then a per-texel correction clamped to plus or minus 6 cm and box-smoothed over 12 cm (keeps real relief, removes scan noise at blinds and glass). Then `panos.shade` colours the texel at that true 3D point. Second session (`_paint_unseen_planes`): a wall, floor or ceiling plane that cameras saw less than 5% of (`UNSEEN_PLANE`: the wall behind a shower curtain, a closet nobody scanned) is painted in the median colour of the seen walls (floors, ceilings) of its room, or of the whole house when its room has none. Before, it came out flat grey (200, 200, 200) in a white bathroom.
8b. **Filling what no camera saw** (`_fill`, `_plain_patch`, `_detail_tile`; second session). Colour: a smooth blend of nearby seen texels, as before. Texture: in holes more than about 8 texels across, the fine detail (image minus its blur) of a 96-texel square of the same plane is laid over the blend, mirrored so it repeats without seams and faded in over 8 texels from the hole's edge. Only grain is copied: structure broader than 4 texels (`DETAIL_GRAIN`) is removed first. The square is the fully seen one at the lower quartile of "strongest edge inside it", else the one with the weakest; a square whose detail has outliers (99th percentile over 5 times the median, `DETAIL_MAX_PEAK`: a line, a rim, a screw) is rejected, smaller squares (48, 24 texels) are tried, and when none qualifies the fill stays smooth. A feature repeated every two squares reads as a grid, which is worse than a smear. This is what makes the ceiling where a fan was, a floor patch behind a toilet or the side of a cabinet read as popcorn, tile or wood instead of a smear.
9. **Multi-view shading** (`panos.shade`, `_look`): for each point, candidate panoramas are the 12 nearest within 12 m; the geometric weight is cos(angle to normal) / distance squared, with cos > 0.12 and distance > 0.25 m. The best 5 views are checked for occlusion with an embree ray from the camera (tolerance 5 cm + 1.2 cm / cos). Inside the tripod blur the trust ramps from 0 to 1 over 0.4 (in face-plane units) outside the blur radius. With 3 or more views, any view more than 45 (0-255) from the per-channel median is dropped (photo-consistency: a door open in one sweep and closed in another, the photographer, reflections). Remaining views are blended with weight cubed (nearly best-view-wins with soft seams). Points none of the best 5 can see fall back to the other candidates.
10. **Carving openings** (`see_through`, `_square_openings`, `_frame_planes`): a wall texel becomes transparent (alpha MASK) only on positive evidence: there is no scanned surface within 0.15 m along its normal, AND some indoor camera on this side sees more than 0.35 m past it, AND the point 0.4 m behind the wall is inside another room. A binary opening removes specks. Each opening over 0.15 m2 that fills at least 75% of its bounding box is replaced by that rectangle; one that ends within 12 cm of the floor or top is snapped to it; a door-width opening (0.5 to 2.5 m) that stops above the floor is extended down when the scan has no wall surface below it either. Rectangles get jamb, lintel and sill planes 5 cm deep, baked like any other plane (the room on the other side adds the other half).
11. **Fixtures** (`piece_faces`, `_seen`, `_drop_crumbs`): MatterPak triangles inside a room are dropped when a shell plane replaces them (within 8 cm of the floor or a flat ceiling and roughly horizontal, or within 8 cm of a real wall stretch). Everything else (the fireplace, toilets, curtains, faucets, vaulted ceilings, and whatever steps 11b and 11c do not replace) is kept. Then (`_seen`, changed in round three): a connected piece of scan that the cameras see at least 25% of (`SEEN_PIECE`, by area, face centres seen from their front) is kept whole; of a piece seen less, only the seen faces stay (the roof above a vaulted ceiling, junk inside walls). Before round three every unseen face was dropped, which left half a toilet in any bathroom with a single scan position: the camera saw one side, Matterport's mesh has all of it. Faces kept unseen take the MatterPak texture. Finally connected pieces under 0.05 m2 are dropped. In vaulted rooms, ceiling triangles that reach into the room but have their centre in the neighbouring room are kept too (the neighbour's rules dropped them and left triangular holes along the edge of the vaulted ceiling).

    Hand fix (`overrides/<model>.json`, `"remove": [{"at": [x, y, z], "radius": r, "why": "..."}]`; round three): scan pieces within `radius` (horizontal) of the point are removed, and a stand-in solid (their convex outline over their height) joins the line-of-sight model so the object's photo is not printed on the wall and floor behind it; the hidden area is filled (step 8b). For clutter left in the house when it was scanned. Used once: the rubbish bin in 7014's garage.

    11a. **Hanging objects** (`piece_faces`, `_under_a_ceiling`, `_in_compact_clumps`, `hanging_solids`; second session). Fans and pendant lamps scan as a few crumpled blades, so they are removed. Flat-ceiling room: every face more than 1.9 m up and more than 0.35 m from a wall (as before). Vaulted room: such a face only when it is a small triangle (under 0.3 m2, `HANG_MAX_FACE`), has a downward-facing scan surface within 0.8 m above it (the ray starts 2 cm behind the face, so the side of a beam starts inside the beam and finds nothing), and belongs to a clump no longer than 2.2 m (`HANG_MAX_SPAN`) in its height layer. Each removed clump that is entirely above 1.75 m (`HANG_MIN_Z`) and no longer than 2.2 m gets a **stand-in solid** (its outline from above grown 0.3 m, over its height) that is added to the ray-caster used for line-of-sight while texturing (`Scan.sight`): without it the photo of the fan is projected onto the ceiling behind it. The ceiling above a removed fan stays (tested against the scan alone) and takes the scan's own texture where the stand-in hides it.

    11b. **Relief boxes** (`relief_runs`, `wall_relief`, `relief_rects`, `relief_boxes`, `box_planes`; second session; off with `--no-boxes`). Anything that stands out of a wall with a flat front becomes a box: kitchen cabinets and counters, fridges, stoves, dishwashers, bathtub fronts, vanities, wall bumps the floor plan does not have, doors folded back against a wall. Per room and per straight stretch of its outline (collinear segments joined):
    - *Relief map*: a 2 cm grid over the wall; a ray from up to 1 m inside the room (never from outside the room polygon) toward the wall stops at the first scan surface that faces the room; the cell holds how far that surface is from the wall line (0 = bare wall). Median filtered (5 cells).
    - *Levels*: repeatedly the most common depth among unassigned cells (6 cm window), while it covers at least 0.08 m2. Relief under 0.12 m (`RELIEF_MIN`: trim, sills) is ignored.
    - *Rectangles*, from the level that sticks out furthest to the shallowest: "solid" is every cell at least that deep (so a counter's rectangle runs on behind the stove standing 6 cm proud of it); closed, opened, holes under 0.35 m2 bridged (an oven window, a dent in a steel fridge); on an 8 cm grid the largest mostly-solid rectangle is taken, its edges moved to where the fine mask ends, and the rest searched again (up to 6 per region).
    - *Checks per rectangle*: at least 0.2 m wide and high; at least 60% of the cells no further-out rectangle explains are at this level, and at least 30% of all its cells; the depth must not trend by more than 0.15 m per metre (a slanted fireplace); it must not be both rough (median cell-to-cell change over 0.15 m per metre) and spread (median absolute deviation over 1.3 cm): that is a shower curtain.
    - *Snapping*: bottom within 15 cm of the floor goes to the floor (toe kicks), and so does a rectangle that reaches 1.5 m and ends within 40 cm of the floor; top within 10 cm of the ceiling and ends within 8 cm of the wall ends snap too. Depths on one wall within 3.5 cm of each other become equal.
    - *Boxes*: the rectangle extruded to its depth. On an open stretch of the outline (a split line, an invisible edge) the box is "unattached": it gets a back face, and rays from behind measure how far it goes on behind the line (a half wall under a breakfast bar).
    - *Contained boxes* (`drop_contained`; round three): a box whose eight corners lie within 4 cm of another box is removed (the same pantry measured from two walls; the upper part of a cabinet found again at another depth). Of twins the earlier stays.
    - *Seen check* (`seen_boxes`): each front is baked at 32 texels per metre; a box whose front the cameras see less than 35% of is dropped (scan debris, not a fixture).
    - *Hand fix* (`overrides/<model>.json`, `"drop_boxes": [{"at": [x, y, z], "why": "..."}]`): any box that contains the point (plan coordinates) is dropped and its scan triangles come back. `--debug-dir` writes `boxes.json` with every box's centre and size to find the point. Used once: the mirror in 7014's bathroom r11.
    - *Small rectangles* (`_drop_lone_small`): a rectangle is a fixture in its own right when it is at least 0.5 m wide and 0.25 m2, or at least 1.5 m tall. A smaller one is kept only when it continues such a rectangle (touching it along a side, depths within 0.07 m, `RELIEF_SAME_FRONT`: the rest of a fridge) or is built in (kept rectangles on two opposite sides of it, depths within 0.15 m, `RELIEF_BUILT_IN`: a microwave between wall cabinets, a stove between counters). A small rectangle on its own, or standing in front of one larger rectangle (a toilet's tank against a wall bump, a pedestal sink's column), is the flat part of something round and stays scan.
    - *Faces* (`box_planes`, `_hidden`): front, sides, top, bottom, back as needed; a side that touches the room's own wall, a bottom on the floor and a top at the ceiling are left out; parts buried in another box are cut away with a 6 mm margin; where two boxes' faces lie in the same plane the earlier box keeps the overlap and the later one is cut exactly (a margin there leaves a slit that shows the wall behind as a light line). "Same plane" is judged at the middle of the face (within 1.5 cm of the other face's plane, normals within 8 degrees), not by comparing plane offsets: runs of joined outline segments can be turned by half a degree, and comparing offsets then made each of two coincident faces look buried in the other box, so both were removed (the see-through pillar of round three).
    - *Line of sight* (`Scan.sight`): once the boxes are known, what a camera sees is decided by the clean model: the scan without the triangles the boxes replace, plus the boxes, plus the fan stand-ins. Boxes are therefore fitted before anything is baked. With the raw scan, the crumpled fridge decided texel by texel which camera saw the fridge's flat face.
    - *One photo per face* (`panos.shade_single`): a box face is not blended from several views per texel like a wall. The whole face goes to the panorama that sees most of it (largest sum of cos / distance squared); what that one cannot see goes to the next best, and so on; a later view's colours are scaled per channel to match the earlier ones where both see the face. Views more than about 75 degrees off the normal (cos under 0.25) do not count. Faces over 600,000 texels fall back to the blend.
    - *Unseen faces* (`_paint_unseen_faces`, `_detail_tile`): a face seen under 15% takes the median colour of the box's best-seen face plus that face's fine texture (see step 8b).
    - Scan triangles within 8 cm of a box (`BOX_TOL`) are removed from the fixtures.

    11c. **Door slabs** (`leaf_boxes`; second session). A connected piece of the remaining scan (plus the dropped tops of doors) whose best-fit plane is vertical, with 90% of its surface within 6 cm of the plane, starting within 25 cm of the floor, at least 1.6 m tall, 0.4 to 1.3 m wide, and covering half of that rectangle, becomes a free-standing slab (a box with both faces textured): an open door leaf, a shower screen. It then goes through the same seen check.

    11d. **Ledge slabs** (`ledge_boxes`; second session). No wall's relief map sees a breakfast bar's ledge: from the side it is a strip a few cm high. A connected piece of the left-over scan whose upward-facing triangles have at least 0.25 m2 within 4 cm of one height (60% of all its upward-facing area), 0.5 to 1.6 m above the floor, filling 60% of their bounding rectangle along the room's wall directions, becomes a floating slab as thick as the scan says (3 to 15 cm, default 4).

    11e. **Risers** (`ledge_risers`; round three). A breakfast bar is a half wall with a counter on one side and a ledge on top. The counter's box ends at counter height, the ledge floats 10 to 40 cm higher, and the wall between them is thin and seen edge-on by the relief maps, so it stayed crumpled scan with gaps. For every ledge and every box under it that is not attached to a wall (gap 0.03 to 0.45 m, footprints overlapping by 0.05 m2), a box fills the gap under the ledge: from the lower box's back face to the wall's other face, found by rays from the lower box's side between the two heights (12 cm when they find nothing, never under 6 cm).
12. **Baking fixtures** (`bake_triangles`): each kept triangle gets its own small chart (longest edge on the x axis, up to 160 texels per metre, capped at 1000 px per edge, 2 px padding), shelf-packed into 2048 pages and coloured by `shade` without snapping, one call per 3 m grid cell (`_shade_local`, second session: `shade` only considers the 12 panoramas nearest to the centre of the points it is given, so it must be given points from one place); where no panorama sees a texel, the MatterPak texture colour is used.
13. **Export** (`write_rebaked_interior_glb`, `compress_glb`): nodes `floor_N`, `walls_N`, `ceiling_N`, `fixtures_box_N` (boxes and slabs, second session), `fixtures_N`, `ceiling_scan_N`; unlit, double-sided materials; walls with carved texels use alpha MASK (cutoff 0.5). Then `gltf-transform unlit` and `optimize` (meshopt, WebP, no simplify, no join, no flatten).
14. **Collision** (`collision_walls`, `house_mesh.write_collision_glb`): wall boxes from the floor-plan edges. A door is cut out only when it was found open in the scan and joins two rooms; outside doors and doors closed during the scan stay solid (players enter by zone switch). Floor slabs per floor. A `fixtures` node holds the kept scan triangles plus, since the second session, the boxes and slabs as 12 triangles each, so counters, fridges, half walls, tubs and open doors block movement with flat faces instead of crumpled ones.

Visual check at each step used `scripts/preview/render_compare.py` (our render next to the real panorama face from the same pose) and `freeview.py` (eye-level views between scan positions and a top-down plan). The user's screenshots from the web client confirmed the result.

### 3.5 Things that were tried inside 3.4 and changed

| Tried | Problem | Now |
|---|---|---|
| All 38 panoramas, including outdoor ones | Front door ghosted (open outside, closed inside); roof visible above the vaulted ceiling | Indoor panoramas only; fixtures must be seen by one |
| Snapping every texel to the nearest scan hit | Blinds and glass are noisy, so windows smeared | Per-plane median offset, plus a clamped, smoothed local correction |
| Hard cut at the tripod blur | Visible circles on floors | Soft trust ramp |
| Weight to the 4th power, all candidate views | Slow, seams harsh | Best 5 views, weight cubed, photo-consistency median |
| Carving whatever looked empty | Half-height ragged doorways, missing slivers | Positive-evidence carving, rectangle squaring, extension to the floor, jamb planes |
| Keeping scan triangles inside the wall slab as jambs | Ragged, crumpled door frames | All scan faces in the wall slab dropped; jambs are generated planes |
| The "kitchen" half wall in 7014 treated as wall slab | Its counter top vanished | Wall distance measured only to real wall stretches (`wall_runs`), not to open passages |
| Door positions from the defurnished mesh's semantic walls | 7014 has no semantic mesh | Ray-casting across the wall on the MatterPak mesh (works on any scan) |
| Synthetic flat-coloured "wall cores" between wall faces (from 3.3) | Flat colour showed at every doorway | Removed; jambs are baked from the panoramas |

### 3.6 Second session (2026-11-14): fixtures as boxes, judged from where players stand

What started it: the user's screenshot of 7014's kitchen in the QA viewer, taken from above and to the side. Read section 6 first if you only read one thing: **the first session judged the result from the panorama positions, where projected photos hide every geometry error.** The same kitchen looks fine from its panorama and shattered from anywhere else.

What the screenshot showed, the cause, and what was done:

| Seen in the screenshot | Cause | Done | Result |
|---|---|---|---|
| Fridge, stove, microwave, dishwasher shattered into silver shards with holes | Steel and glass scan badly; the kept "fixture" triangles are the raw scan, each triangle textured on its own | Relief boxes (3.4 step 11b) | Flat-fronted appliances textured from the photos. The fridge front keeps some smeared patches (view-dependent steel, one camera) |
| Upper cabinets smeared, with stray triangles | Same; plus per-triangle charts pick different cameras | Relief boxes | Clean doors, one texture per face |
| Counter under the pass-through sticking out in orange shards | The half wall and its counter are not walls in the floor plan, so they stayed scan | Unattached boxes with a back extent on open stretches of the outline | The counter and half wall are boxes; the raised bar ledge above (0.15 m deep, under `RELIEF_MIN` from one side) is still scan |
| White door at the left with a saw-tooth edge | An open door leaf scanned as a crumpled sheet | Doors against a wall become thin relief boxes; free-standing ones become slabs (step 11c) | 7014: the doors folded back against walls are boxes (r7 to r11); the one in bathroom r11 still has ragged scan at its foot. 6915: one slab; one leaf in r5 is still scan (not checked which test it fails) |
| Flat tan area behind the fridge and above the cabinets | Wall texels no camera saw, blurred from far away | Unseen box faces take the box's colour; unseen planes take the room's colour (step 8) | No more grey planes |
| (Other rooms, found while checking) fans printed across ceilings, or left as crumpled blades in the vaulted living room | The fan's photo is projected onto the ceiling behind it; the vaulted-room rule kept everything | Hanging objects and stand-in solids (step 11a) | Fans are gone; a soft blurred patch remains on the ceiling where the fan hid it |
| (Found while checking) flat grey wall behind 6915's shower curtain | A plane with no seen texel was filled with a constant | `_paint_unseen_planes` | Room-coloured wall |

Things tried in this session and changed or dropped (so nobody repeats them):

| Tried | Problem | Now |
|---|---|---|
| One rectangle per connected region of a depth level (bounding box when 80% full, else greedy largest rectangles on the fine grid) | A stove 6 cm proud of the counter cut the counter into strips; a hole in the oven door cut the stove into three | Levels fitted from the furthest out inward with "at least this deep" as solid; holes under 0.35 m2 bridged; rectangles found on a coarse grid and refined |
| Accepting any rectangle that was 25% at its level | A slanted fireplace became a staircase of boxes; a shower curtain became a painted block with a grey wall around it | Tilt check, rough-and-spread check, 60% of unexplained cells at the level |
| Tie-breaking coincident box faces by growing one box 2 cm and shrinking the other | Left 2 cm rims that showed as thin light lines across cabinet fronts | Faces in the same plane looking the same way are a tie that the earlier box wins; everything else is cut with a closed test |
| Painting every poorly seen box face with the mean colour of the best face | A mirror made the mean grey; partly seen faces got flat patches | Median colour, and only for faces seen under 15% |
| `_under_a_ceiling` with a 1.5 m reach, per face | Loose scan surfaces a metre above the real ceiling (roof, attic) made a 1.9 m2 ceiling triangle "hang"; its hole and stand-in solid wrecked the hero living room | 0.8 m reach, small triangles only, compact clumps only, per height layer |
| A stand-in solid for every dropped clump | In flat rooms the dropped faces include the tops of cabinets and open doors; one hull reached the floor and enclosed the kitchen's camera: every cabinet came out flat-coloured and the walls black | Solids only for clumps entirely above 1.75 m and at most 2.2 m long |
| Dropping scanned-ceiling faces that the stand-in solid hides | A hole in the vaulted ceiling above the fan | `_seen` tests against the scan alone |
| MatterPak texture for shell texels far from anything seen | It is garbage exactly where no camera saw (orange smears, outdoor brick on an indoor wall) | Dropped; unseen planes take the room's colour |
| Boxes for toilets | They pass as boxes (flat tank front) and look like white blocks; some come out half box, half scan | Still open, see section 4 |

Measured (section 6 explains why the numbers barely move):

| | 7014 before | 7014 after | 6915 before | 6915 after |
|---|---|---|---|---|
| Mean abs error, all indoor panoramas (round one; round two is in 3.7) | 14.07 | 14.13 | 14.76 | 13.02 |
| 95th-percentile pixel error | 53.3 | 53.3 | 59.0 | 52.3 |
| Pixels off by more than 60 | 3.83% | 3.82% | 4.82% | 3.74% |
| Holes | 0.09% | 0.09% | 0.01% | 0.01% |
| Fixture surface replaced by boxes (measured on an intermediate revision) | 0 | about 60% (38 of 61 m2) | 0 | about 55% (34 of 60 m2) |
| Boxes / door slabs / hanging objects removed | 0 / 0 / 0 | 29 / 0 / 7 | 0 / 0 / 0 | 29 / 1 / 13 |
| interior.glb / collision.glb | 4.9 MB / 91 KB | 4.6 MB / 57 KB | 3.4 MB / 132 KB | 3.7 MB / 178 KB |

### 3.7 Second session, round two: one photo per face, and guessing what was not photographed

Started from two more of the user's screenshots (7014 bathroom r10, and the breakfast bar seen from the living room) and their question: can a single good shot be used for a surface, and can missing parts be predicted. Both work, within limits.

| Seen | Cause | Done | Result |
|---|---|---|---|
| Box faces smeared and patchy (toilet tank, fridge, cabinet doors with ghost edges) | Per-texel blending of views that disagree: steel and lacquer reflect differently into every camera, and a box face is a few cm off the real surface, so views do not line up. The raw scan also decided per texel which camera "sees" the face | One photo per face (`shade_single`) and line of sight against the clean model (3.4 step 11b) | The largest single improvement of the session. Cabinet doors, stove, microwave and dishwasher are sharp and coherent |
| Toilet and pedestal sink as blocks with stretched textures | Small flat parts of round objects were boxed | `_drop_lone_small` | Toilets and the pedestal sink are scan again and read as what they are; the vanity in r11 is a clean box. First attempt (keep any small rectangle within 0.10 m of a big one's depth) kept the tank as a box against the wall bump behind it and left the toilet half gone; the built-in rule separates a tank in front of a bump from a microwave between cabinets |
| Bar ledge shattered and twisted at its end | Not relief from any wall, so it stayed scan | `ledge_boxes` | A straight slab. Its top is seen only at grazing angles, so it is synthesised wood, darker and plainer than the real lacquered top |
| Ledge top white, with a patch of garden at one end (first attempt) | At 80 degrees off the normal a lacquered top shows the window it reflects | Views under cos 0.25 do not count for box faces | Fixed |
| Light horizontal line through the upper cabinets | `_hidden` cut coplanar fronts with a 6 mm margin: a slit, with the wall showing through | Ties are cut exactly | Fixed |
| Smooth smears where a fan hid the ceiling, on floors behind fixtures, flat-coloured cabinet sides | `_fill` only blurs | Texture synthesis (3.4 step 8b) | Patches carry the surface's grain. On a patterned surface (tile joints, plank lines) the pattern is not continued, only the grain |
| In the full build the toilet in 7014 r10 was mostly gone, though a partial bake of the same room had it | A first-session bug. `shade` takes the 12 panoramas nearest to the centre of the points it is given. `_seen` and `bake_triangles` gave it every fixture triangle of the house at once, so the centre was the middle of the house and rooms at the far ends had no candidate camera: their pieces counted as unseen (dropped) or fell back to the MatterPak texture | `_shade_local`: one `shade` call per 3 m grid cell | The toilet is back; 7014 keeps about twice as many scan triangles (926 instead of 447). Lesson: a partial bake and a full build can differ wherever code looks at "all points at once" |
| A row of ceiling lights stamped across 6915's bathroom ceiling, then a faint grid on 7014's bathtub (first two versions of the texture synthesis) | The sample square held a feature (a recessed light; the soft line of the tub's rim), and a feature repeated every two squares reads as a grid | Grain only, squares with outliers rejected, smooth fill as the fallback (3.4 step 8b) | Fixed on both. The back of a shower curtain, which no camera saw, is still the MatterPak texture: a coarse mosaic up close |
| "Glitter" on the bathroom walls | It is in the photos: orange-peel wall texture with glossy paint, photographed at a flat angle in a small room | Nothing | Open (section 4) |
| Green floors in the viewer screenshots | The QA viewer's room overlay tint | Nothing (not in the glb) | |

Why the answer to "can we predict what is missing" is only "its material": the panoramas are the only source of appearance. For a surface no camera saw there is nothing to recover, only something to make up, and what can be made up safely is colour and grain from the same object or room. Continuing a pattern (tile joints, cabinet door edges) or inventing the back of an object needs a model of what kitchens look like; a generative inpainting model could do it per texture but would invent details a buyer might take for real, and is out of scope here.

Measured after round two (pack v9; round one in brackets): 7014 mean abs error 14.25 (14.13), 6915 12.87 (13.02). 7014: 23 boxes (29), 2 ledge slabs, 926 scan fixture triangles (715), interior.glb 4.9 MB, collision.glb 60 KB. 6915: 26 boxes (29), 1 door slab, 8,048 scan fixture triangles (7,400), interior.glb 4.1 MB, collision.glb 189 KB. Fewer boxes and more scan triangles are intended: toilets and sink columns went back to scan, and pieces at the ends of the house are no longer dropped. The score again says nothing either way (section 6).

New tool for this kind of work: `scripts/preview/bake_rooms.py <block> <listing> <out.glb> r4 r10` bakes only the named rooms (about one minute for a kitchen and a bathroom instead of six for the house) with the whole house still deciding what cameras see.

### 3.8 Second session, round three: nothing may be missing

The user walked pack v9 in the QA viewer and found things that were simply not there. Their question: the Matterport tour feels spatially whole, so can missing walls be filled in? Yes, and none of it needed guessing: the walls come from the floor plan, so every hole was the pipeline deleting or not building something it knew about.

| Seen in the viewer | Cause | Done |
|---|---|---|
| The pillar between kitchen, dining and living room (a pantry) was see-through from two sides | It was two boxes, one measured from each of two outline stretches. The stretch made of joined segments is turned by 0.6 degrees, so the two coincident faces failed the "same plane" test and each was judged buried in the other box: both removed | `_hidden` judges ties at the face; `drop_contained` removes the duplicate box (3.4 step 11b) |
| Half of the toilet missing (r10) | One scan position in the bathroom: 0.87 of the toilet's 1.27 m2 of scan surface was never photographed and was dropped face by face | Pieces seen at least 25% are kept whole (3.4 step 11) |
| The bar ledge floating, crumpled scraps and gaps under it | The counter box ends at 0.89 m, the ledge is at 1.17 m; the half wall between them was nobody's | `ledge_risers` (3.4 step 11e) |
| Black vertical strips in walls, at the living / dining boundary | Wall faces of the two rooms stopped short of the open edge between them | Wall ends meet or get end caps (3.4 step 7a) |
| Triangular holes in the ceiling at the top of the pillar | Vaulted-ceiling triangles straddling the line to the flat-ceilinged kitchen were dropped by the kitchen's rules | Kept with the vaulted room (3.4 step 11) |
| A crumpled rubbish bin in the garage | Clutter left in the house; the scan is a broken shell | `"remove"` override (3.4 step 11). Automatic detection was not attempted: a bin and a toilet are the same thing to these rules (a free-standing lump on the floor) |

How to find this kind of fault quickly: build the stage in isolation and print it. The diagnosis for all four took one script that rebuilt the boxes without baking (10 seconds after the scan loads) and printed, per face, which other box cut how much of it, and, for the toilet, how much surface each rule removed. The renders only said "something is missing".

Measured after round three (round two in brackets): 7014 mean abs error 14.14 (14.25), holes 0.06% (0.09%); 6915 12.85 (12.87), holes 0.00% (0.01%). Kept scan triangles roughly doubled, as intended (whole pieces): 7014 1,644 fixture triangles (926), collision.glb 72 KB; 6915 16,723 (8,048), collision.glb 342 KB with 17,035 fixture triangles. If collision size becomes a problem for the game, decimate or box the fixture colliders; the visible mesh needs the triangles.

What "filled in" means now, in order of how much is guessed: (1) geometry from the floor plan or from boxes, textured from photos: no guess; (2) scan faces no camera saw, with the MatterPak texture: Matterport's guess; (3) plane and box areas no camera saw, with colour and grain borrowed from the same surface (3.7): our guess at the material. Nothing is invented beyond that.

---

## 4. Known problems, in the current output

Ranked by how much they hurt "feels like the real house". Re-ranked after the second session; (old N) is the number the problem had before.

1. **Round fixtures are raw scan**: toilets, pedestal sinks, faucets, the fireplace. They are whole since round three, but the scan of a toilet is lumpy, each of its triangles picks its own cameras, and the sides no camera saw carry the MatterPak texture (coarse, sometimes with silver smears). Next step: section 5 item 2.
2. **Mirrors and glass** (old 4). The depth camera sees into a mirror, so the scan bulges or tunnels there. A flat bulge passes every box check: 7014's bathroom r11 got a 0.34 m deep "pillar" in front of its mirror, removed by hand (`drop_boxes` in `overrides/y1cVPR1HsnL.json`). Mirror and steel textures are also view-dependent: speckled or blocky from anywhere but the camera.
3. **Exposure and white balance differ between panoramas** (old 1). Large walls show soft lighter and darker blotches where the best view changes (clearest on 6915's living room wall).
4. **The web client washes the colours out** (old 2). Lane C renders with tone mapping on; the textures are finished photos. Fix on their side: `toneMapped = false` on interior materials (HANDOFF.md 23:40). Until then any colour work here is judged through a filter.
5. **Hanging lights below 1.75 m** (6915's dining chandelier) are removed as geometry but get no stand-in solid, so their photo is printed on the ceiling above. **Where a fan was**, the ceiling is a filled patch about 1.5 m across. Since round two it carries the ceiling's grain; its brightness can still differ from the surroundings, and a few scraps survive (a blade tip outside the stand-in solid in 7014's living room; fan parts below 1.9 m in 6915's bedrooms).
6. **Boxes are fitted per wall and per depth level, so one object can be several boxes.** A fridge in a corner is the union of pieces seen from two walls: steps of a few cm and texture seams where they meet. Its steel front shows the room's reflection as one camera saw it (soft light and dark shapes), which is honest but looks odd from other angles.
7. **Open doors**: a free-standing leaf that does not pass the slab test (6915 r5) stays crumpled scan, and the door box in 7014's bathroom r11 has ragged scan left at its foot.
8. **The breakfast bar in 7014** (between kitchen and living room): the ledge is a slab and the half wall under it is closed (round three), but the ledge's top is synthesised (plain dark wood; the real one is lacquered and lighter), and the counter top under the ledge, which no camera sees well, is a guess too. **Glittering walls** in small bathrooms (7014 r10) come from the photos themselves (orange-peel texture, glossy paint, flat viewing angle); a filter that removes the glitter would also soften every other wall.
9. **Close-up blockiness** (old 3). 256 texels per metre is 4 mm; a 2k skybox face gives about 3 mm per pixel at 3 m, so the source is near its limit too.
10. **Windows** (old 5) show a flattened, slightly smeared photo of the outside (parallax between cameras).
11. **Doors open in one sweep and closed in another** (old 7) can still ghost slightly when fewer than 3 views see a spot.
12. **Fixture texture seams** (old 8). Per-triangle charts with 2 px padding bleed at lower mip levels. Far fewer triangles are left (7014: about 700 fixture faces instead of several thousand), so this matters less.
13. **Window positions in rooms.json are guesses on the panorama path** (old 9). `has_window` is right, positions are not. Matters for "crib not under a window".
14. **6 of 16 (7014) and 5 of 13 (6915) doors are solid** (old 10) because they were closed when scanned. Lane C also found (HANDOFF.md 14:54) that 6915's two bathroom doors are 0.56 and 0.58 m wide, narrower than the 0.6 m player capsule.
15. **Small holes** (old 11) at vaulted-to-flat ceiling junctions: fewer since round three (ceiling triangles straddling a room boundary are kept), not all gone.
15b. **Where clutter was removed by hand** (7014's garage bin) the wall and floor behind are a soft filled patch, and a trace of the object's photo can remain at its edge.
16. **GPU memory** (old 12): 19 pages of 2048 px on 7014 (17 before; box faces are baked at wall resolution), 16 on 6915.
17. **Room splits are hand-written** (old 13) for both houses (`overrides/*.json`).
18. **Build time** (old 14) is still 5 to 6 minutes per house. The box steps add a few seconds; the rest is baking.
19. **The score does not see any of the second session's improvements** (section 6). There is no number yet that falls when a fridge stops being shattered.
20. **Synthesised surfaces are guesses.** Texture synthesis repeats grain, not patterns: a filled floor patch has no tile joints, a cabinet side has no door edge. Anything seen under 15% (box faces) or 5% (walls) is colour and grain borrowed from a neighbour, not a photo of the thing.

---

## 5. How to improve it, ranked

Each item says what to do, where, and how to tell it worked. Look at the same room views before and after every change (section 6); the score only tells you that nothing broke. Problem numbers refer to section 4.

Done in the second session, kept here so the list stays whole: fixtures as boxes (was item 10, "primitive fitting"), hanging objects in vaulted rooms (was item 2), hold-out scoring and the two extra metrics (was item 6).

### High value, low risk

1. **A number that sees crumple** (problem 19). Everything in the second session was judged by eye. Two cheap candidates: (a) the area of scan triangles left in `fixtures_N` per room, as a share of that room's fixture surface (boxes plus scan); the build report already has the counts. (b) In `room_views.py`, render the same view with `TINT=1` and report the share of pixels that are tinted scan: what a player actually sees of the raw scan. Put both in the build report so a new house can be judged without looking at every room.
2. **One photo per scan piece** (problem 1). `shade_single` did for box faces what nothing yet does for the remaining scan triangles: `bake_triangles` shades each triangle's texels with the per-texel blend, so a toilet is a patchwork. Per connected piece of the fixtures, pick the panorama that sees most of its area (`panos.see` on the face centres), take every texel that view sees from it, and fall back to the blend elsewhere. Then consider replacing recognisable round fixtures (toilet, pedestal sink) by a catalog model placed at the scan's position. Check on 7014 r10 and r11.
3. **Exposure compensation between panoramas** (problem 3). Solve one colour gain per panorama (per channel, or a 3x3 affine) by least squares over texels seen by several panoramas: minimise the difference between each pair of views of the same point, with a weak prior pulling gains to 1. The data is already in `shade` (per-point `cols` and `wts` for up to 5 views); collect a random sample of multi-view points from all planes first, solve, then multiply each panorama's samples by its gain in `PanoSet.sample`. Expect the wall blotches to disappear and the score to drop.
4. **Continue patterns in filled patches** (problem 20). Round two added grain (`_detail_tile`). Tile joints and plank lines could be continued where the seen part of the plane is periodic: find the period by autocorrelation of the seen texels and tile whole periods instead of a 96-texel square. Only worth it for floors.
5. **The remaining door leaves** (problem 7). Look at 6915 r5's leaf in the tinted view and print why `leaf_boxes` rejects it (bulge, coverage, width, or the piece is connected to something else). In flat-ceiling rooms the old "above 1.9 m" rule also drops the top of a free-standing door before the slab test sees it as one piece; `leaf_boxes` is given those faces back, but they are a separate connected piece. Loosen `LEAF_MAX_BULGE` only after looking.
6. **One box per object** (problem 6). After `relief_boxes`, merge boxes in a room whose footprints overlap by more than half and whose heights overlap: replace them by their common bounding box when at least 85% of its volume is covered. Removes the fridge's steps and seams.
7. **Window positions from the panoramas** (problem 13). `see_through` already computes "no surface here, and a camera sees more than 0.35 m past it". On exterior walls (no room behind) that is a window. Collect those masks per exterior wall plane, take connected components, and use their extents for rooms.json window openings (keep Matterport's count and widths as a check).
8. **Higher resolution where players look** (problem 9). Try `--bake-res 384` with the `4k` skyboxes (`download_skyboxes(..., "4k")`; check status and size first, about 4x the data). Keep `--texture-size 2048` and let the per-plane cap split large walls across pages, or raise the page size to 4096 for walls only.

### Medium value

9. **Mirrors** (problem 2). Find them instead of dropping boxes by hand: a mirror is a wall region where the scan has a hole or a surface behind the wall plane (the relief ray from the room finds nothing within the wall's thickness, and a ray continuing through finds surface far behind), usually over a vanity. Give that wall region a plain reflective-looking material, or the best single view, and do not fit boxes in front of it.
10. **Islands and other free-standing blocks** (problem 8). `ledge_boxes` catches thin level slabs. A kitchen island or a free-standing counter (thick, standing on the floor, away from every wall and split line) is still scan: a relief pass looking down (a height map of each room from 1.3 m, levels and rectangles as for walls) would catch it. Neither demo house has one.
11. **Multi-band (Laplacian) blending** instead of weight cubed in `shade`: blend low frequencies across all good views and take high frequencies from the best view. Sharper texture with invisible seams. Do this after exposure compensation.
12. **Open the closed interior doors for play** (problem 14). For a listed interior door that was not found open: carve its rectangle (Matterport's width and height, centred where the ray detection found the strongest partial gap, else on the edge centre) from both rooms' wall planes, add jamb planes, and cut it in collision. Keep outside doors solid until the block exists.
13. **Proper UV unwrapping for what is left of the fixtures** (problems 12 and 16). Replace per-triangle charts in `bake_triangles` with `xatlas` charts per connected piece. Lower priority now that most fixture surface is boxes.
14. **Automatic room splits** (problem 17). Matterport's open-plan "Kitchen" needs to become kitchen / dining / living. Signals available after a build: floor material from the baked floor texture, the relief boxes at counter height (0.85 to 0.95 m tops mark the kitchen), the ceiling height change, half walls in the floor plan (dangling `wall` edges). Propose a split, write it to `overrides/<model>.json` as a suggestion, and keep human review for the hero house.
15. **Speed** (problem 18). Parallelise planes with `multiprocessing` (each worker needs its own embree scene), and cache baked plane images by a hash of the plane and the pano set so a re-run after a small change only re-bakes what changed. A 5 minute loop is what made the second session slow.

### Needed before more properties

16. **Vacancy check.** Both demo houses were scanned empty. A furnished scan would bake furniture into walls and floors, and the relief boxes would turn a sofa against a wall into a box. Before building a new house, compare the MatterPak mesh with the defurnished mesh (when present): area of MatterPak triangles more than 0.3 m from any defurnished surface, per m2 of floor. Above a threshold, pick another house.
17. **Multi-floor houses and stairs.** Untested. `scan_floor_z` already returns one height per floor and rooms carry `floor`, but nothing builds stairs. Plan: keep stair triangles as scan geometry (exclude staircase rooms from floor and ceiling planes and from relief boxes), add a ramp collider, emit `stairs` edges in graph.json, and texture each floor's planes from that floor's panoramas.
18. **Odd floor plans.** `chain_polygon` takes the largest polygon from `polygonize`; rooms that do not close are dropped silently. Log them and fall back to the concave hull of the room's scan floor faces. Relief boxes assume walls are straight runs; a curved wall yields nothing (safe) and stays scan.
19. **Batch runner.** A `worldgen/build_block.py --houses all` style loop over `blocks/<block>.json` that builds every house into a fresh version, writes one report per house (the dict `build` returns, the score, the room-view sheets) and fails loudly on any house whose score or hole fraction regresses.

---

## 6. Measuring quality

**Rule one (learned in the second session): never judge a build from the panorama positions alone.** Every texel was coloured by projecting a photo from a panorama's position, so from that position the picture is right whatever the geometry is: a shattered fridge and a flat one render the same. A player stands somewhere else. Look from room corners (`room_views.py`), from above (the QA viewer, `worldgen/viewer/`), and at the panorama comparison last.

`worldgen/scripts/preview/` (scratch-quality scripts; run from `worldgen/` with `uv run python scripts/preview/<name>.py`). They read the uncompressed glb in `out/cache/glb/` (`<model>_interior_pano.glb` for pack v1, `<model>_interior_pano_v<N>.glb` for other versions), because trimesh cannot read meshopt files.

| Script | Use |
|---|---|
| `room_views.py <glb> <rooms.json> <out dir> [room ids]` | One sheet per room: the view from each of its corners (0.6 m in, eye height 1.6 m) toward the far corner. This is the main check. `TINT=1` paints what is still raw scan (`fixtures_N` magenta, `ceiling_scan_N` cyan), so a problem can be traced to "scan left over" or "bad box". `NO_CEILING=1` hides ceilings. |
| `render_compare.py` | Renders from each panorama's pose and puts the render next to the real cube face. `--score <glb> <model json> <skybox dir> [labels]` prints `mean_abs_err`, `p95_err` (95th-percentile pixel error), `bad_fraction` (pixels off by more than 60) and `hole_fraction` over the 4 side faces of every indoor panorama (192 px), or of the listed labels only. Purple pixels are holes. |
| `freeview.py` | Renders from any eye position and look-at point (Matterport plan coordinates). `NO_CEILING=1` plus a camera high above gives a top-down plan (walls far from the centre show their faces in perspective: that is not a slab). |
| `plot_plan.py` | Plots the room vectors (walls, invisible edges, doors, windows) from the cached model JSON. |
| `calib_panos.py` | Re-derives the panorama orientation convention (section 2). |
| `bake_rooms.py <block> <listing> <out.glb> r4 r10` | Bakes only those rooms into an uncompressed glb, in about a minute. The loop for trying a change before a full build. |
| `build_house --debug-dir <dir>` | `relief_room<i>_wall<j>.png` (relief map with fitted rectangles in red) and `boxes.json` (every box: centre, width, depth, z range). |

Scores (2026-11-14; "before" is the first session's build, "after" the second session's, pack v9):

| Interior | mean abs error (0-255) | p95 | off by more than 60 | holes |
|---|---|---|---|---|
| 6915, raw MatterPak (3.2) | 16.02 | | | 1.49% |
| 6915 before | 14.76 | 59.0 | 4.82% | 0.01% |
| 6915 after round one (3.6) | 13.02 | 52.3 | 3.74% | 0.01% |
| 6915 after round two (3.7) | 12.87 | 51.7 | 3.61% | 0.01% |
| 6915 after round three (3.8) | 12.85 | 51.3 | 3.60% | 0.00% |
| 7014 before | 14.07 | 53.3 | 3.83% | 0.09% |
| 7014 after round one (3.6) | 14.13 | 53.3 | 3.82% | 0.09% |
| 7014 after round two (3.7) | 14.25 | 53.7 | 3.90% | 0.09% |
| 7014 after round three (3.8) | 14.14 | 53.0 | 3.80% | 0.06% |
| 6915 hold-out (panoramas 13, 0, 16 left out of the bake), before | 21.10 | 67.7 | 6.74% | 0.01% |
| 6915 hold-out, after (a round-one revision; not re-run for round two) | 21.05 | 67.3 | 6.68% | 0.01% |
| 7014 hold-out (11, 13, 18), before | 15.63 | 48.7 | 2.82% | 0.03% |
| 7014 hold-out, after (a round-one revision; not re-run for round two) | 15.99 | 50.3 | 3.11% | 0.02% |

How to read them:

- **The full score is biased**: the bake used the same panoramas it is scored against. It catches regressions (misaligned geometry, wrong carving, holes, a broken convention, a stand-in solid hiding a room: that one cost 1.8 points) and does not reward improvements.
- **The hold-out score is honest but narrow.** Run `build_house --holdout <labels> --version 99` and score with the same labels: the bake never saw those photos. It only works in rooms with several panoramas (living rooms, big bedrooms); kitchens and bathrooms usually have one, and those are the rooms the second session changed. So it did not move either.
- **Removing something on purpose costs points.** The real photos show the fans; a build without them scores a little worse (7014: 14.07 to about 14.15).
- **6915 improved by about 1.8 points and 7014 by none** although both changed in the same way. Not investigated; the likely reason is that 6915's kitchen and bathrooms are seen by more than one panorama (three stand in its kitchen, one in 7014's), and the score sees geometry only where a second camera looks at the same thing from another place.
- So: the score is a regression alarm. Improvement is judged on the room-view sheets, side by side, same rooms, before and after. Section 5 item 1 is the missing number.

The check to run after every change (about 3 minutes per house after the build):

```
cd worldgen
G=out/cache/glb/<model>_interior_pano_v<N>.glb
uv run python scripts/preview/room_views.py $G out/packs/<block>/v<N>/houses/<listing>/rooms.json <dir>/after
uv run python scripts/preview/render_compare.py --score $G out/cache/matterport/<model>.json out/raw/<model>/skybox_2k
```

Compare each room's sheet with the one from before the change. Then open the QA viewer on the same pack version and orbit above the kitchen and the bathrooms.

---

## 7. Adding a new property

The pipeline has no per-house code. A new scan needs a block entry, usually a room-split override, and a look at every room. Expect to spend the time in steps 5 and 6.

1. **Choose a vacant scan** (section 5 item 16). The scout's report (`docs/demo-candidates.md` on `lane-a/scout`) and `worldgen/out/scout/shortlist.json` list candidates with scores. A furnished scan will bake furniture into the walls and box the sofa.
2. **Add it to `worldgen/blocks/<block>.json`**: `listing_id` (`hou_<CoStar ListingId>`), `costar_listing_id`, `address`, `matterport_model_id`, `interior_source: "panorama"`, `role`. `interior_mesh` is only needed for `defurnished`.
3. **Build into your own version** with debug output: `uv run python -m worldgen.build_house --block <block> --listing <id> --version <N> --debug-dir <dir>`. Read the report:

    | Field | Healthy (both demo houses) | If it is off |
    |---|---|---|
    | `rooms`, `dropped` | Every real room listed; only "Void" and outdoor rooms dropped | A room that does not close is dropped silently (section 5 item 18) |
    | `doors` listed / found | About two thirds found | Few found: doors were closed for the scan; rooms behind them are unreachable in play |
    | `shell_seen_by_panos` | 0.75 to 0.80 | Much lower: panoramas missing or misaligned (rerun `calib_panos.py`), or a stand-in solid is hiding a room (see `hanging_objects_removed`) |
    | `relief_boxes` | 23 to 26 per house | 0: the scan has no flat-fronted fixtures, or wall lines are far from the real walls. Hundreds: scan noise is being boxed; look at the relief images |
    | `relief_boxes_unseen_dropped` | 0 to 1 | Many: boxes are being fitted to debris |
    | `door_leaf_slabs`, `ledge_slabs`, `ledge_risers` | 0 to 1, 0 to 2, 0 to 1 | Many ledges: shelves or furniture in a scan that is not empty |
    | `hanging_objects_removed` | 7 and 13 | These are stand-in solids, one per fan or lamp. More than about two per room: check the ceilings in the room views |
    | `vaulted_rooms` | 1 to 2 | Every room vaulted: the listed room heights do not match the scan |
    | `texture_pages` | 16 to 19 | Over 25: use `--texture-size 1024` or a lower `--bake-res` |

4. **Rooms**: look at the top-down plan (`freeview.py` with `NO_CEILING=1`, or the QA viewer) and `plot_plan.py`; if Matterport merged living, dining and kitchen, write `overrides/<model_id>.json` (see the two existing files for the format, including a second split with `"at"`). Put a split line on a half wall or counter edge when there is one: fixtures on an open stretch of a room's outline are measured from that line.
5. **Look at every room** from its corners (`room_views.py`, then again with `TINT=1`) and from above in the QA viewer. What to look for, and what to do:

    | You see | It is | Do |
    |---|---|---|
    | A flat-coloured block or pillar where the photo shows a mirror, a window or open air | A box fitted to a bulge in the scan | Find it in `boxes.json`, add `{"at": [x, y, z], "why": ...}` to `"drop_boxes"` in the override file, rebuild |
    | Crumpled, shattered geometry (magenta in the tinted view) | Scan that no box replaced | Look at that wall's relief image: no rectangle means a check rejected it (too rough, tilted, too small). Accept it, or see section 5 items 2 and 10 |
    | A box that is too big, too small or in pieces | Rectangle fitting | Relief image for that wall; the constants at the top of the relief section of `rebake.py` |
    | Many wrong boxes in the whole house | The scan does not suit box fitting (furnished, or very noisy) | Rebuild with `--no-boxes`: the first session's output, crumpled but never wrong |
    | Something see-through or half missing: a pillar, a cabinet side, a toilet | A stage removed it. Do not guess: rebuild that stage alone and print what it removes (section 3.8) | Boxes: `--debug-dir` writes `boxes.json`; `rebake._hidden` decides which faces are buried. Scan pieces: `rebake._seen` |
    | Clutter that is not part of the house (a bin, a ladder, a box of tiles) | Left there when the house was scanned | `"remove": [{"at": [x, y, z], "radius": r, "why": ...}]` in the override file |
    | A black strip in a wall or at a wall's end | Wall faces that do not meet at an open edge | `rebake._open_end`, `END_REACH` |
    | A hole in a vaulted ceiling, or a room with flat-coloured walls | A hanging-object stand-in went wrong | `hanging_objects_removed` in the report; the `HANG_*` constants in `rebake.py` |
    | A wall or a box side in plain colour with grain but no features | No camera saw it: colour and grain are borrowed (section 3.7) | Nothing, unless it should be an opening: then the door was closed for the scan |
    | A box face showing a window, sky or another room | A reflection, or a view at a flat angle | `min_cos` in `panos.shade_single`; for one face, `drop_boxes` |
    | The photo of one room printed on the wall of the next | A door closed in one sweep and open in another | Known (section 4 item 11) |

6. **Walk it**: `publish --local` (after copying or building into the version the client loads), open the web client with `VITE_START_ROOM=<listing>.r<N>`. Check that the player fits through the doors and cannot walk through counters.
7. **Score** with `render_compare.py --score` and keep the numbers with the house (they are the baseline for the next change). Run tests, ruff and `contracts/validate.py out/packs/<block>`.

---

## 8. Other open items for Lane A (not house quality, but blocking others)

- `listings/index.json` (`worldgen/costar.py` is still a stub). Without it houses have no door points (`Listing.world`) and the web client cannot enter a house from the block; `VITE_START_ROOM` is the workaround.
- `furniture/catalog.json` (`build_catalog.py` stub). The furnishing agent has nothing to place.
- `audit.json` per house (`audit.py` stub). The manifest already lists it. 6915 has a ready contradiction: listed 1,317 sqft vs about 1,017 sqft scanned.
- The block (`build_block.py` stub): render and collision for the street. The drone splats in the hackathon portal are an option for the exterior (about 420 MB each, need cropping).
- `publish.py` bucket upload (only `--local` exists).
- Merge `lane-a/hero-house` into `main` after the team agrees on 7014 as hero and the listing id format.

## 9. Gotchas

- **`worldgen/out` is shared by every worktree and several agents build into it at once** (on 2026-11-14 a second agent started building 6915 into pack v2 while this work was using v2 for experiments). Experiment in a version number of your own (`--version <N>`); the cached glb is then `<model>_interior_pano_v<N>.glb`, so two versions cannot overwrite each other's file. Never write into `v1` (what other lanes load) or a version someone else is building without the user's say-so. Delete your scratch versions (`out/packs/<block>/v<N>`) when done.
- **Never stop builds with a pattern** (`pkill -f build_house` would also kill another agent's build). Stop your own by process ID.
- A build that is running keeps the code it started with. Editing `rebake.py` during a build is safe; the result is from the old code.
- A stand-in solid for a removed fan hides everything behind it from the cameras. If a room's cabinets or walls come out in flat colours after a change to the hanging-object rules, that is the first suspect (`shell_seen_by_panos` drops too).

- Never call a Matterport mutation; `sources/matterport.py` blocks them. `download_bundle_asset` refuses any bundle that is not already `unlocked`.
- Signed URLs (skyboxes, meshes, MatterPak) expire and must never be logged or cached; the code requests fresh ones per download.
- `ruff format .` reformats files other lanes own (it touched `tests/test_costar.py` once); format only the files you changed.
- trimesh cannot read meshopt-compressed glbs; inspect `out/cache/glb/*` (uncompressed) instead of the pack's `interior.glb`.
- The Vite dev server answers a missing file with `index.html` and status 200; Lane C's loader detects that.
- `worldgen/worldgen.egg-info/*` is tracked in git but regenerated by `uv sync`; do not commit those changes.
- The shared Playwright MCP browser may be held by another session; a small script using the `playwright` npm package with `channel: "chrome"` works for screenshots of the web client.

## 10. Pack versions: which house map is live, and how a new one replaces it

**One live version, pinned in git.** `worldgen/blocks/<block>.json` holds `live_pack_version` (and a `pack_history` of every change, why, and how it compared). Everything reads that number: `publish` (it publishes only the live version), `audit` (default), `costar` (has_interior and world), and the agent's `data/packs.py` when `PACK_VERSION` is unset. Nothing picks "the newest folder" any more, so a test build in `worldgen/out` can never be shipped, audited or read by the agent by accident. `build_house` requires `--version` and refuses the live one; `normalize`/`build_catalog` refuse any furniture version a live manifest uses.

```
uv run python -m worldgen.versions status hou_navidad_01     # every version on this machine, which is LIVE, what is missing
uv run python -m worldgen.versions pin hou_navidad_01 --version N --reason "..." --compared-to-live "..."
```

**Version numbers per worktree** (so experiments never collide): house-quality 10-39, outdoor 40-69, block work 70-89, scratch 90-99. Delete your scratch versions when a test is done. The next candidate meant to go live takes the next free low number (2, 3, ...).

**Promotion rule: build on the live version, never drop what it already does well.** The goal is that versions build on each other: a candidate should keep everything the live version gets right and improve on it, so the metrics are mostly better and none is clearly worse. It is not a gate where every number must improve; small noise on one metric is fine. What blocks a promotion is a real regression: a house or room that looks visibly worse, a lost improvement from an earlier version, a new hole or broken doorway, or a score that gets clearly worse. If a trade-off is worth it anyway, the human accepts it in writing in `--compared-to-live`.

1. Build the candidate into its own version: `build_house --version N` for every house in the block, then `audit --block <block> --version N`.
2. Compare with the live version, per house, and write the numbers down (looking for regressions, not demanding a win on every line):
   - `render_compare.py --score` (lower is better; also hole_fraction). CAVEAT: the scorer reads the shared uncompressed cache `out/cache/glb/<model>_interior_pano.glb`, which every build overwrites whatever its version. Score immediately after your own build of that house, and never while another worktree is building the same model. Record the result in the candidate folder (`packs/<block>/vN/houses/<id>/quality.json`) so the number belongs to the version.
   - Walk both versions in the viewer at the same spots (living room, kitchen, each bedroom door, the front door) and compare side by side.
   - The audit findings and the doorway check (Lane C's door tool, 0.25 m capsule) should not get worse.
3. **Two improvements at once** (for example interior quality and the scanned lot): merge both branches, build one combined candidate, and check it against the live version AND against each improvement's own candidate. The thing to catch is one change overwriting or undoing the other: if the combined build has lost something that either one had alone, the changes interact; fix that before pinning. Never pin one improvement's candidate over another's without this check, or the second merge silently drops the first one's gain.
4. `versions pin` (it checks completeness and runs contracts/validate.py), commit `worldgen/blocks/<block>.json`, `publish`, and post a HANDOFF line so lane D sets `PACK_VERSION` on Render and lane C updates `VITE_PACK_BASE_URL`.
