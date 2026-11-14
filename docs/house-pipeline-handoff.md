# Handoff: turning Matterport scans into playable house maps (Lane A)

Written 2026-11-14 at the end of a long session, for the next coding agent working on the house pipeline. Read `AGENTS.md` first (sections 3, 7, 9 and 11 matter most here), then this file.

The goal of this work: given any Matterport model of a home, produce a house pack that feels like walking through the real house (as close to Matterport's own viewer as possible), with straight walls and clean doorways, collision that plays well in a nerf fight, and room data the furnishing agent can use. The current result is good on both demo houses. This document records exactly what was built, what was tried and rejected and why, how quality is measured, and a ranked list of what to improve next so the pipeline keeps getting better and works on new properties.

---

## 1. Where things are

| What | Where |
|---|---|
| Branch | `lane-a/hero-house`, pushed to origin, rebased on `main` at `da4bb49`. Not merged into `main` yet. |
| Worktree used | `/Users/pfu/hp-hero` (the main checkout is `/Users/pfu/hackprincetonf26`). `.env` in the worktree is a symlink to the main checkout's `.env` (gitignored). |
| Pipeline code | `worldgen/build_house.py` (orchestration, rooms, doors, graph, collision), `worldgen/rebake.py` (clean shell, carving, fixtures, texture baking, glb export), `worldgen/panos.py` (panorama sampling, ray-caster, multi-view shading), `worldgen/house_mesh.py` (OBJ reading, collision.glb, gltf-transform compression, the older interior writers), `worldgen/sources/matterport.py` (the only Matterport client: read-only, cached), `worldgen/pack.py` (manifest), `worldgen/publish.py` (`--local` only). |
| Per-house config | `worldgen/blocks/hou_navidad_01.json` (listing id to Matterport model, which interior method to use, hero role). `worldgen/overrides/<model_id>.json` (hand room splits). |
| Tests | `worldgen/tests/test_build_house.py`, `worldgen/tests/test_rebake.py` (40 tests total with the older ones). |
| Preview and scoring tools | `worldgen/scripts/preview/` (see section 6). Not part of the package; run with `uv run python scripts/preview/<name>.py` from `worldgen/`. |
| Outputs (gitignored) | `worldgen/out/packs/hou_navidad_01/v1/houses/<listing_id>/{interior.glb, collision.glb, rooms.json, graph.json}` and `manifest.json`. Downloads in `worldgen/out/raw/<model_id>/` (MatterPak zip and OBJ, skybox faces, defurnished OBJ for 6915), caches in `worldgen/out/cache/` (Matterport JSON, uncompressed glbs). About 1.1 GB for the two houses. |
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
8. **Baking each plane** (`bake_plane`): one texel per 1/256 m (capped so the plane fits a 2048 page). For every texel, find the real scanned surface along the normal: first a per-plane median offset (the plan line can be several cm off a thick outer wall), then a per-texel correction clamped to plus or minus 6 cm and box-smoothed over 12 cm (keeps real relief, removes scan noise at blinds and glass). Then `panos.shade` colours the texel at that true 3D point.
9. **Multi-view shading** (`panos.shade`, `_look`): for each point, candidate panoramas are the 12 nearest within 12 m; the geometric weight is cos(angle to normal) / distance squared, with cos > 0.12 and distance > 0.25 m. The best 5 views are checked for occlusion with an embree ray from the camera (tolerance 5 cm + 1.2 cm / cos). Inside the tripod blur the trust ramps from 0 to 1 over 0.4 (in face-plane units) outside the blur radius. With 3 or more views, any view more than 45 (0-255) from the per-channel median is dropped (photo-consistency: a door open in one sweep and closed in another, the photographer, reflections). Remaining views are blended with weight cubed (nearly best-view-wins with soft seams). Points none of the best 5 can see fall back to the other candidates.
10. **Carving openings** (`see_through`, `_square_openings`, `_frame_planes`): a wall texel becomes transparent (alpha MASK) only on positive evidence: there is no scanned surface within 0.15 m along its normal, AND some indoor camera on this side sees more than 0.35 m past it, AND the point 0.4 m behind the wall is inside another room. A binary opening removes specks. Each opening over 0.15 m2 that fills at least 75% of its bounding box is replaced by that rectangle; one that ends within 12 cm of the floor or top is snapped to it; a door-width opening (0.5 to 2.5 m) that stops above the floor is extended down when the scan has no wall surface below it either. Rectangles get jamb, lintel and sill planes 5 cm deep, baked like any other plane (the room on the other side adds the other half).
11. **Fixtures** (`piece_faces`, `_seen`, `_drop_crumbs`): MatterPak triangles inside a room are dropped when a shell plane replaces them (within 8 cm of the floor or a flat ceiling and roughly horizontal, or within 8 cm of a real wall stretch). Everything else (counters, the fireplace, tubs, toilets, cabinets, vaulted ceilings) is kept. In flat-ceiling rooms, things hanging more than 1.9 m up and more than 0.35 m from a wall (fans, lamps; they scan as crumpled sheets) are dropped. Faces no indoor camera sees are dropped (removes the roof above a vaulted ceiling and junk inside walls), then connected pieces under 0.05 m2.
12. **Baking fixtures** (`bake_triangles`): each kept triangle gets its own small chart (longest edge on the x axis, up to 160 texels per metre, capped at 1000 px per edge, 2 px padding), shelf-packed into 2048 pages and coloured by `shade` without snapping; where no panorama sees a texel, the MatterPak texture colour is used.
13. **Export** (`write_rebaked_interior_glb`, `compress_glb`): nodes `floor_N`, `walls_N`, `ceiling_N`, `fixtures_N`, `ceiling_scan_N`; unlit, double-sided materials; walls with carved texels use alpha MASK (cutoff 0.5). Then `gltf-transform unlit` and `optimize` (meshopt, WebP, no simplify, no join, no flatten).
14. **Collision** (`collision_walls`, `house_mesh.write_collision_glb`): wall boxes from the floor-plan edges. A door is cut out only when it was found open in the scan and joins two rooms; outside doors and doors closed during the scan stay solid (players enter by zone switch). Floor slabs per floor. A `fixtures` node holds the kept scan triangles so counters, half walls and tubs block movement.

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

---

## 4. Known problems, in the current output

Ranked by how much they hurt "feels like the real house".

1. **Exposure and white balance differ between panoramas.** Large walls show soft lighter and darker blotches where the best view changes (clearest on 6915's living room wall in the web client). Weight cubed blends seams but does not equalise exposure.
2. **The web client washes the colours out.** Lane C renders with tone mapping on; the textures are finished photos. Fix on their side: `toneMapped = false` on interior materials (noted in HANDOFF.md 23:40). Until then any colour work here is judged through a filter.
3. **Close-up blockiness.** 256 texels per metre is 4 mm; a 2k skybox face gives about 3 mm per pixel at 3 m, so the source is near its limit too. With an over-the-shoulder camera pressed to a wall, it looks blocky.
4. **Mirrors, glass and shiny surfaces** (bathroom mirrors, shower glass, the fireplace glass, stainless appliances) are view-dependent; any fixed texture looks speckled or wrong.
5. **Windows** show a flattened, slightly smeared photo of the outside (parallax between cameras).
6. **Thin objects in vaulted rooms** (7014's living-room fan) remain as crumpled scan geometry; the hanging-object filter only runs in flat-ceiling rooms.
7. **Doors open in one sweep and closed in another** can still ghost slightly when fewer than 3 views see a spot (photo-consistency needs 3).
8. **Fixture texture seams.** Per-triangle charts with 2 px padding bleed at lower mip levels; counters can show faint triangle edges at a distance.
9. **Window positions in rooms.json are guesses on the panorama path.** `place_windows` uses the defurnished mesh's window samples; on the panorama path it gets none and spreads windows evenly along the edge. `has_window` is right, positions are not. This matters for the furnishing rules ("crib not under a window").
10. **6 of 16 (7014) and 5 of 13 (6915) doors are solid** because they were closed when scanned. Bedrooms behind closed doors are unreachable in play.
11. **Small holes** remain (0.09% of rendered pixels on 7014), mostly at vaulted-to-flat ceiling junctions (purple slivers in the previews).
12. **GPU memory** is about 370 MB per house (15-17 pages of 2048 px), more than half of it fixture pages that are mostly padding.
13. **Room splits are hand-written** for both houses (`overrides/*.json`). A new property will not get a living room without one.
14. **Build time** is about 5 minutes per house, almost all in Python loops around `shade` and per-plane work.

---

## 5. How to improve it, ranked

Each item says what to do, where, and how to tell it worked. Measure every change with the score in section 6 and look at the same set of preview images before and after.

### High value, low risk

1. **Exposure compensation between panoramas** (problem 1). Solve one colour gain per panorama (per channel, or a 3x3 affine) by least squares over texels seen by several panoramas: minimise the difference between each pair of views of the same point, with a weak prior pulling gains to 1. The data is already in `shade` (per-point `cols` and `wts` for up to 5 views); collect a random sample of multi-view points from all planes first, solve, then multiply each panorama's samples by its gain in `PanoSet.sample`. Expect the wall blotches to disappear and the score to drop.
2. **Hanging-object removal in vaulted rooms too** (problem 6). In `piece_faces`, for non-flat ceilings, drop connected pieces whose top is within 0.6 m of the scanned ceiling above them and whose bottom is more than 0.5 m below it, away from walls. Or replace fans with a catalog model.
3. **Window positions from the panoramas** (problem 9). `see_through` already computes "no surface here, and a camera sees more than 0.35 m past it". On exterior walls (no room behind) that is a window. Collect those masks per exterior wall plane, take connected components, and use their extents for rooms.json window openings (keep Matterport's count and widths as a check).
4. **Proper UV unwrapping for fixtures** (problems 8 and 12). Replace per-triangle charts in `bake_triangles` with `xatlas` (pip `xatlas`) charts per connected piece: far less padding waste, no bleeding seams, fewer pages.
5. **Higher resolution where players look** (problem 3). Try `--bake-res 384` with the `4k` skyboxes (`download_skyboxes(..., "4k")`; check status and size first, about 4x the data). Keep `--texture-size 2048` and let the per-plane cap split large walls across pages, or raise the page size to 4096 for walls only.
6. **Measure with held-out panoramas** (see section 6). Without it, every change that "matches the panoramas better" also looks better at the panorama positions for free.

### Medium value

7. **Multi-band (Laplacian) blending** instead of weight cubed in `shade`: blend low frequencies across all good views and take high frequencies from the best view. Sharper texture with invisible seams. Do this after exposure compensation.
8. **View-dependent surfaces** (problem 4). Detect texels where views disagree strongly even after exposure compensation (the photo-consistency deviation is already computed); on fixtures with large disagreement, use the single best view instead of a blend, and for mirrors (vertical, bathroom, large disagreement) consider a simple reflective material instead of a texture.
9. **Open the closed interior doors for play** (problem 10). For a listed interior door that was not found open: carve its rectangle (Matterport's width and height, centred where the ray detection found the strongest partial gap, else on the edge centre) from both rooms' wall planes, add jamb planes, and cut it in collision. The room behind has its own textures. Keep outside doors solid until the block exists.
10. **Primitive fitting for fixtures** (the user's "polygon heuristics" question). Run RANSAC plane fitting on the kept fixture triangles per room to find counter tops (horizontal, 0.85-0.95 m), island and half-wall tops, cabinet fronts and tub rims; replace a fitted region by a clean box or slab baked from the panoramas, and keep the rest as scan. This gives straight counter edges the same way the shell gives straight walls, while odd objects stay as scan geometry. Do it as an optional pass that only replaces pieces whose fit error is small, so weird furniture or fixtures fall back to the scan.
11. **Automatic room splits** (problem 13). Matterport's open-plan "Kitchen" needs to become kitchen / dining / living. Signals that are already available after a build: floor material from the baked floor texture (tile vs wood vs carpet: cluster the floor texels by colour and texture), counters and cabinets from fixture faces at 0.9 m, the ceiling height change (vaulted vs flat), half walls in the floor plan (dangling `wall` edges). Propose a split along the boundary between floor-material clusters, write it to `overrides/<model>.json` as a suggestion, and keep human review for the hero house. On the defurnished path, `_type071` countertops mark the kitchen directly.
12. **Speed** (problem 14). `shade` is vectorised per chunk but `bake_plane` runs plane by plane and `bake_triangles` group by group. Parallelise planes with `multiprocessing` (each worker needs its own embree scene; build it from `scan.tris` in the worker), and cache baked plane images by a hash of the plane and the pano set so a re-run after a small change only re-bakes what changed.

### Needed before more properties

13. **Vacancy check.** Both demo houses were scanned empty. A furnished scan would bake furniture into walls and floors and keep sofas as fixtures. Before building a new house, compare the MatterPak mesh with the defurnished mesh (when present): area of MatterPak triangles more than 0.3 m from any defurnished surface, per m2 of floor. Above a threshold, either pick another house, or (much harder) use the defurnished mesh for geometry and fill the floor and wall areas hidden by furniture with `_fill` from the visible parts.
14. **Multi-floor houses and stairs.** Untested. `scan_floor_z` already returns one height per floor and rooms carry `floor`, but nothing builds stairs: a "staircase" room's floor plane would be flat at the lower floor. Plan: keep stair triangles as scan geometry (exclude staircase rooms from floor and ceiling planes), add a ramp collider from the bottom to the top landing, emit `stairs` edges in graph.json, and texture each floor's planes from that floor's panoramas (filter `PanoSet` by camera height relative to `floor_z`).
15. **Odd floor plans.** The pipeline assumes room polygons close. `chain_polygon` takes the largest polygon from `polygonize`; the scout found a median 92% of rooms close cleanly. Rooms that do not close are dropped silently today; log them and fall back to the concave hull of the room's scan floor faces.
16. **Batch runner.** A `worldgen/build_block.py --houses all` style loop over `blocks/<block>.json` that builds every house, writes one report per house (the dict `build` returns plus the score) and fails loudly on any house whose score or hole fraction regresses.

---

## 6. Measuring quality

`worldgen/scripts/preview/` (scratch-quality scripts, kept so the next agent can see what was checked):

| Script | Use |
|---|---|
| `render_compare.py` | Ray-traces the uncompressed glb (`out/cache/glb/<model>_interior_pano.glb`; trimesh cannot read meshopt) from each panorama's pose, unlit, and puts it next to the real cube face. `--score` prints `{"indoor_panos", "mean_abs_err", "hole_fraction"}` over the 4 side faces of every indoor panorama (192 px). Purple pixels are holes. |
| `freeview.py` | Renders from any eye position and look-at point (Matterport plan coordinates). Eye level 1.65 m. `NO_CEILING=1` plus a camera at z = 17 gives a top-down plan, which is the fastest way to decide room splits. |
| `plot_plan.py` | Plots the room vectors (walls, invisible edges, doors, windows) from the cached model JSON. |
| `calib_panos.py` | Re-derives the panorama orientation convention (section 2). |

Baselines (2026-11-14):

| Interior | mean abs error (0-255) | holes |
|---|---|---|
| 6915, raw MatterPak (3.2) | 16.02 | 1.49% |
| 6915, panorama rebuild (current) | 14.76 | 0.01% |
| 7014, panorama rebuild (current) | 14.07 | 0.09% |

Read these with care:

- **The score is biased**: the bake used the same panoramas it is scored against. It is good at catching regressions (misaligned geometry, wrong carving, holes, a broken convention) and bad at rewarding improvements. Next step: add a hold-out option (for example `--holdout 3,11,18`: bake with those pano labels removed from `PanoSet`, then score only on them). That turns it into a real measure of what a player sees between scan positions.
- **Mean absolute error is not very sensitive** to small ugly artefacts (silver shards, crumpled fans) that people notice immediately. The raw MatterPak scored only 1.3 points worse while looking much worse. Keep looking at images. A useful addition: report the 95th-percentile error per face and the fraction of pixels with error over 60.
- Always look at the same views before and after a change: `render_compare.py ... 11,13,17,18,22` for 7014 and `14,3,4` for 6915, plus the free views listed in `freeview.py`'s docstring, plus the web client.

---

## 7. Adding a new property

1. Choose a vacant scan (section 5 item 13). The scout's report (`docs/demo-candidates.md` on `lane-a/scout`) and `worldgen/out/scout/shortlist.json` in the `hp-scout` worktree list candidates with scores.
2. Add it to `worldgen/blocks/<block>.json`: `listing_id` (`hou_<CoStar ListingId>`), `costar_listing_id`, `address`, `matterport_model_id`, `interior_source: "panorama"`, `role`. `interior_mesh` is only needed for `defurnished`.
3. Build it and read the report `build_house` prints: rooms and areas, dropped rooms, doors listed vs found, vaulted rooms, `shell_seen_by_panos` (fraction of shell texels some camera saw; about 0.8 on both houses, a big drop means panoramas are missing or misaligned), `carved_wall_m2`, `framed_openings`, panoramas indoors.
4. Look at the top-down plan (`freeview.py` with `NO_CEILING=1`) and `plot_plan.py`; if Matterport merged living, dining and kitchen, write `overrides/<model_id>.json` (see the two existing files for the format, including a second split with `"at"`).
5. Rebuild, run `render_compare.py` on a few panoramas and `--score`, walk a few free views.
6. `publish --local`, open the web client with `VITE_START_ROOM=<listing>.r<N>`, walk it.
7. Run tests, ruff and `contracts/validate.py`.

---

## 8. Other open items for Lane A (not house quality, but blocking others)

- `listings/index.json` (`worldgen/costar.py` is still a stub). Without it houses have no door points (`Listing.world`) and the web client cannot enter a house from the block; `VITE_START_ROOM` is the workaround.
- `furniture/catalog.json` (`build_catalog.py` stub). The furnishing agent has nothing to place.
- `audit.json` per house (`audit.py` stub). The manifest already lists it. 6915 has a ready contradiction: listed 1,317 sqft vs about 1,017 sqft scanned.
- The block (`build_block.py` stub): render and collision for the street. The drone splats in the hackathon portal are an option for the exterior (about 420 MB each, need cropping).
- `publish.py` bucket upload (only `--local` exists).
- Merge `lane-a/hero-house` into `main` after the team agrees on 7014 as hero and the listing id format.

## 9. Gotchas

- Never call a Matterport mutation; `sources/matterport.py` blocks them. `download_bundle_asset` refuses any bundle that is not already `unlocked`.
- Signed URLs (skyboxes, meshes, MatterPak) expire and must never be logged or cached; the code requests fresh ones per download.
- `ruff format .` reformats files other lanes own (it touched `tests/test_costar.py` once); format only the files you changed.
- trimesh cannot read meshopt-compressed glbs; inspect `out/cache/glb/*` (uncompressed) instead of the pack's `interior.glb`.
- The Vite dev server answers a missing file with `index.html` and status 200; Lane C's loader detects that.
- `worldgen/worldgen.egg-info/*` is tracked in git but regenerated by `uv sync`; do not commit those changes.
- The shared Playwright MCP browser may be held by another session; a small script using the `playwright` npm package with `channel: "chrome"` works for screenshots of the web client.
