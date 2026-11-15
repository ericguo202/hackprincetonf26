# Handoff: turning Matterport scans into playable house maps (Lane A)

**This is the one document for the house pipeline: the inside of each house (Matterport scan to `interior.glb`, `collision.glb`, `rooms.json`, `graph.json`) and its scanned lot outdoors (yard, facades, roof, fences, trees, backdrop).** Any agent working on `worldgen/build_house.py`, `rebake.py`, `panos.py`, `exterior.py`, `aerial.py`, `roof.py`, `fences.py`, `vegetation.py`, `worldgen/overrides/`, `worldgen/scripts/preview/` or a house pack must read section 0 before writing code, and keep this file current in the same commit as any change to a heuristic, threshold, ranked list or decision. Read `AGENTS.md` first (sections 3, 7, 9, 11). The block (the street around the houses) is a separate pipeline, `worldgen/build_block.py` and `block_*.py`, not covered here.

Written by three sessions on 2026-11-14 and brought up to date on 2026-11-15. Section 0 is the summary and the rules; sections 1 to 10 are the full record (3.4 the method step by step, 3.6 to 3.10 each session's log with "tried and changed" tables, 4 known problems ranked, 5 improvements ranked, 6 measuring, 7 adding a property, 9 gotchas, 10 pack versions).

---

## 0. Start here

### 0.1 The goal and the bar

Given any Matterport model of a home, produce a house zone that feels like walking through the real house and its yard: "as close to Matterport's own viewer as possible", "playable outdoor areas that look just as good as the Matterport", straight walls and clean doorways, collision that plays well in a nerf fight, room data the furnishing agent can use. **The house must look right from anywhere a player stands, not only from where the scanner stood: nothing see-through, clipped, missing, shattered or floating.** Every room and every corner of the yard counts, not only the scripted demo path (the user, 2026-11-14: "wouldn't we want players to be able to explore all rooms possible?"). The method must work on a new scan with few hand tweaks, so every heuristic and threshold is written down here.

### 0.2 Where things stand (2026-11-15)

- **All house work is merged into `main`** (fast-forward to 0429cc2 on 2026-11-15; history in sections 3.6 to 3.10). Work continues on branch `lane-a/combined-quality`, worktree `/Users/pfu/hp-combined-quality` (shared data: section 1). Rebase it onto `main` before every merge (0.5).
- **Packs** (`worldgen/out/packs/hou_navidad_01/`; `uv run python -m worldgen.versions status hou_navidad_01` is the source of truth):

  | Version | What | Status |
  |---|---|---|
  | v1 | First-session houses (panorama-textured interiors, no lot) | old live |
  | v2 | v1's houses byte for byte + the block (lane-a/block-combined) + furniture v2 | old live |
  | v3 | v2 + furniture v3 | **LIVE** (pinned in `worldgen/blocks/hou_navidad_01.json`) |
  | v4 | v3's block and furniture byte for byte + the **v43 houses** | built, **not live**: waits for Lane C (HANDOFF 23:55: drop the placeholder `HouseGround` slab, move the leave-the-house trigger off the front door) |
  | v10 | Second-session interior only (3.6 to 3.8) | reference |
  | v40, v41 | Outdoors on the first-session interior; first combined candidate | superseded |
  | v43 | Previous house candidate: second-session interior + lot + third-session roofs, trees, fences (3.10); what v4 holds | audited, `quality.json` per house |
  | **v44** | **Current house candidate** (fourth session, 3.12): v43 + on 7014 the drone splat's roof shape and shingles, brick and siding on walls no camera saw, straight deck railings. 6915 has no splat on disk and builds as in v43 | built 2026-11-15, not committed, not in a block pack yet |
  | v9, v95 | Old copy of v10; another session's pack (its manifest fails `contracts/validate.py`) | not ours to delete without asking |

- **Measured** (lower score is better; section 6 says why the score is a regression alarm, not a judge):

  | | 7014 v1/v2/v3 | 7014 v43 | 6915 v1/v2/v3 | 6915 v43 |
  |---|---|---|---|---|
  | Indoor mean abs error / p95 / bad / holes | 14.07 / 53.3 / 3.83% / 0.09% | 14.93 / 57.3 / 4.55% / 0.04% | 14.76 / 59.0 / 4.82% / 0.01% | 13.20 / 53.3 / 3.90% / 0.00% |
  | Outdoor mean abs error | no lot | 33.45 | no lot | 25.57 |
  | interior.glb / collision.glb | 5.0 MB / 94 KB | 15.9 MB / 771 KB | 3.5 MB / 136 KB | 11.4 MB / 1.02 MB |
  | Outside doors open (`door_check.py`) | 0 | 2 (front, back) | 0 | 2 |

  7014's indoor rise is only the cameras that look out of the opened doors (the photos show those doors closed). `rooms.json` and `graph.json` are identical between v1 and v43; the house-local frame is unchanged (floor at y = 0, Matterport origin and axes).
  v44 against v43 (3.12). 7014: indoor 14.93 (same), outdoor 33.42 (was 33.45; no camera moved by more than 2: the porch camera -1.9, the two deck cameras +1.4), interior.glb 16.3 MB, collision.glb 742 KB, both outside doors open, `rooms.json` and `graph.json` byte-identical. 6915 (no splat on disk): all four files byte-identical to v43, so a house without a splat is untouched by the new code. `audit.json` written for both; 194 tests pass.
- **Not verified yet:** the houses in the real game client (`apps/web`). Everything was checked in ray-traced renders and the QA viewer (`worldgen/viewer`, three.js, headless walk test passes).
- **The user directs the next improvements** (2026-11-15: "I will tell you how to improve those houses, don't start without me"). Do not start a new round of changes on your own; propose from 0.7 and section 5 and wait.

### 0.3 The pipeline in one page

`build_house.build` (one house; about 12 to 20 minutes with the lot):
1. **Rooms, doors, graph** from Matterport's floor plan (`read_rooms`, hand splits from `overrides/<model>.json`, doors located by rays across each wall: 3.4 steps 1 to 4).
2. **Interior** (`rebake.write_rebaked_interior_glb`): walls, floors and ceilings are clean planes from the plan; vaulted ceilings stay scan and their walls stop at the scanned ceiling; fixtures flat against a wall become boxes (relief maps), open doors slabs, bar ledges slabs with risers; fans removed with stand-in solids for line of sight; everything else stays scan; every texel coloured from the skybox panoramas (planes: per-texel blend of views; box faces: one photo per face); what no camera saw takes colour and grain from the same surface (3.4 steps 5 to 13, 3.6 to 3.8).
3. **Outdoors** (`exterior.add_exterior`, through the `extra` hook just before export): ground heightfield from rays cast upward, facades moved onto the scanned wall face, the **roof fitted to the drone splat's roof sheet when the model has a splat, else to Vexcel's surface model** (`splat.py`, `aerial.py`, `roof.py`) with measured eaves and porch ceilings, **fence panels** (`fences.py`), **railing panels from the splat** (`railings.py`), **tree and shrub models** (`vegetation.py`), the remaining outdoor scan pieces, a 45 m backdrop dome and ground disk, front and back doors opened (3.9, 3.10, 3.12). The splat only fills gaps: roof shape and shingles, wall texels no camera saw, railings.
4. **Compress and collision** (`house_mesh.compress_glb`: unlit, meshopt, WebP; `write_collision_glb`: plan walls, floors, boxes, kept scan, plus `ground`, `outdoor`, `bounds`).

Packing the houses with the live block and furniture is not this pipeline: `build_block.py --houses-from <version>` copies `houses/` unchanged (main, 2026-11-15). `costar.py` sets `Listing.world.position` y from the live block's ground at each front door.

### 0.4 Decisions the user made (do not re-open them without the user)

| Date | Decision |
|---|---|
| 2026-11-14 | Hero house is **7014 Navidad Rd** (`hou_236595179`, Matterport `y1cVPR1HsnL`, demo room `hou_236595179.r3`); 6915 (`hou_237901269`, `xpspAtb5H59`) is the second house. Both must keep building. |
| 2026-11-14 | Bathrooms must be reachable in both houses; door widths are real measurements and are not widened (Lane C shrank the capsule to 0.25 m). Closets need not be reachable. |
| 2026-11-14 | House zone = house + its scanned lot, same house-local frame and same files (new nodes, no contract change). Past the scan's edge: a panorama backdrop, not the Vexcel block. |
| 2026-11-14 | Front and back doors open for play; the garage door stays shut. |
| 2026-11-14 | Trees: fine to cut the scanned canopies; replace them with models that fill the same space. Use the other data layers (Vexcel) to fill in roofs and the outdoors. |
| 2026-11-14 | Every room and yard corner matters; never deprioritise something because it is "not in the demo" (ordering work along the demo path is fine; say it as an order). |
| 2026-11-15 | All house work merged into `main`; the user tells the agent what to improve next. |

**Open questions (ask, do not decide):** (1) unscanned side yards (7014's south strip): fence them off, or keep them walkable with plain walls (current); (2) where players leave the house zone now that the front door opens onto the yard (with Lane C; HANDOFF 18:24, 23:55); (3) answered on 2026-11-15: 7014's roof texture comes from the drone splat (3.12), shadows baked in are fine; (4) GPU memory (33 and 28 texture pages of 2048 px): lower outdoor resolution, xatlas charts, or a separate `exterior.glb` (a manifest contract change, all four lanes).

### 0.5 Ground rules for agents on this pipeline

- **Your own git worktree** on a `lane-a/<topic>` branch (or `lane-a/combined-quality`), never the main checkout. New worktree: `git worktree add -b lane-a/<topic> /Users/pfu/hp-<topic> origin/main`, then symlink `worldgen/out` and `.env` from `/Users/pfu/hackprincetonf26` into it, `cd worldgen && uv sync && npm i`. Never touch other agents' worktrees or branches (`git worktree list`).
- **`worldgen/out` is one folder shared by every worktree**, and several agents build into it at once. Build only into your own versions: **house experiments v44 to v69** (v40 to v43 are taken), interior-only experiments 11 to 39, scratch 90 to 99; delete scratch when done. **Never write into v1 to v4**, never edit `worldgen/blocks/hou_navidad_01.json`, `apps/web/public/packs/` or `render.yaml`, never pin or publish unless the user asks.
- **Stop your own processes by PID only** (never `pkill -f`): it once killed another session's builds. `python -m worldgen.exterior` must always get `--dest`: its default output is read by the block builder.
- **Matterport is read-only** (`worldgen/sources/matterport.py`, never unlock or buy, never log signed URLs). **Vexcel is billed**: everything here uses cached rasters; ask the user before any new call. Secrets stay in `.env`.
- **Baseline before a change, re-score and look after it.** Never judge from the panorama positions or the score alone (section 6): look from places no camera stood (0.6). Run `compare_panos.py` after any change that moves a score, and look at the side-by-side of every camera that moves by more than 3.
- **Before each commit**, from `worldgen/` (unset `VIRTUAL_ENV` if uv warns): `uv run pytest`, `uv run ruff check .`, `uv run python ../contracts/validate.py out/packs/hou_navidad_01` (one known error from v95). Format only the files you changed (never `ruff format .`). Never commit `worldgen/worldgen.egg-info/*`. Commit messages start with `lane-a:`.
- **Commit and push only when the user asks.** Merging into main (the user's procedure, 2026-11-15): commit open work; `git fetch origin && git rebase --rebase-merges origin/main` (the branch carries merge commits); on a `HANDOFF.md` conflict keep main's file plus your branch-only lines, each placed after the last main line with an earlier or equal time (`HANDOFF.md` is append-only); check `git diff origin/main...HEAD --stat` touches only `worldgen/`, `docs/`, `HANDOFF.md` (stop and ask otherwise); run the checks; from the main checkout `git pull --ff-only && git merge --ff-only lane-a/combined-quality && git push origin main`, push the branch (`--force-with-lease` after a rebase); if main moved meanwhile, rebase again, never a merge commit.
- **Going live:** tell the user the candidate's version number; Lane A packs it with the live block and furniture into the next pack version (`build_block.py --houses-from N`) and pins it under the promotion rule (section 10). Post a dated `HANDOFF.md` line whenever another lane is affected (new node names, collision changes, sizes).

### 0.6 The working loop

From `worldgen/` in your worktree:

```
# quick: bake only some rooms (about 1 min), or only some outdoor parts (1 to 3 min)
uv run python scripts/preview/bake_rooms.py hou_navidad_01 hou_236595179 out/cache/glb/<you>/rooms.glb r3 r4
uv run python -m worldgen.exterior --block hou_navidad_01 --listing=hou_236595179 --parts roof,facade,yard --dest out/cache/glb/<you>/ext.glb
# full build of a house into your version (background; about 12 to 20 min each)
uv run python -m worldgen.build_house --block hou_navidad_01 --listing=hou_236595179 --version 44
uv run python -m worldgen.audit --block hou_navidad_01 --version 44
# checks, right after your own build (the scorer reads out/cache/glb/<model>_interior_pano_v<N>.glb)
G=out/cache/glb/y1cVPR1HsnL_interior_pano_v44.glb
uv run python scripts/preview/render_compare.py --score $G out/cache/matterport/y1cVPR1HsnL.json out/raw/y1cVPR1HsnL/skybox_2k
OUTDOOR=1 uv run python scripts/preview/render_compare.py --score $G out/cache/matterport/y1cVPR1HsnL.json out/raw/y1cVPR1HsnL/skybox_2k
uv run python scripts/preview/compare_panos.py out/cache/glb/y1cVPR1HsnL_interior_pano_v43.glb $G out/cache/matterport/y1cVPR1HsnL.json out/raw/y1cVPR1HsnL/skybox_2k [--outdoor]
uv run python scripts/preview/room_views.py $G out/packs/hou_navidad_01/v44/houses/hou_236595179/rooms.json <dir> [r3 r4 ...]   # TINT=1: raw scan in magenta
uv run python scripts/preview/door_check.py out/packs/hou_navidad_01/v44 hou_236595179 [--all]
uv run python scripts/preview/render_compare.py $G out/cache/matterport/y1cVPR1HsnL.json out/raw/y1cVPR1HsnL/skybox_2k <out.jpg> <pano label>   # render next to the real photo
python3 ../worldgen/viewer/serve.py --port <free port>   # from the repo root of your worktree; ?pack=hou_navidad_01/v44&house=hou_236595179
```

Spell out paths in zsh (an unquoted `$VAR` holding two paths is not split, and the scorer then fails silently). macOS has no `timeout`. The headless viewer walk test: `npm i --prefix <scratch> playwright-core@1.52.0`, then `PW_CORE=<scratch>/node_modules/playwright-core node scripts/preview/viewer_walk.mjs "<viewer url>" [out_prefix]`.

**Standard free views** (`scripts/preview/freeview.py <glb> <out.jpg> "eye;look;fov" ...`, Matterport plan coordinates x, y, z; house-local is (x, z = -y)):
- 7014: street `"24,3,1.6;5,4,2.2;75"`; walk-up to the front door `"13,4.5,1.6;5.4,4.5,1.4;70"`; inside the entry alcove `"8.5,4.6,1.6;5.4,4.5,1.5;75"`; driveway close-up `"22,-1,1.7;18.5,2.7,0.5;60"`; far back-yard corner `"-13,13,1.6;3,4,1.8;75"`; the user's two back-yard positions `"-14.8,12.6,1.7;0,5,2.5;80"` and `"-9.4,11.4,1.7;-16,18,2;80"`; back porch from its camera `"-3.0,3.5,1.5;-10,8,2.6;90"`; back of the house `"-9,10,1.7;0,5,2.8;70"`; south side strip `"-3,-5,2.0;3,0,1.5;80"`; from above `"18,-8,10;3,3,2.5;55"`.
- 6915: front yard `"-14.9,-8.8,1.7;0,-2,2.5;75"`; back wall and eave `"12,-2,1.7;2,-3,2.8;70"` and `"10,-3.5,1.7;5.2,-3.2,2.5;55"`; back fence `"8,0,1.6;16,-5,1.2;75"`; from above `"-16,-12,10;0,-3,2.5;55"`.
- Indoors: `room_views.py` corner sheets; living rooms r3 (7014) and r4 (6915), kitchens, bathrooms r10, r11 (7014) and r9, r10 (6915).

### 0.7 What works, what failed, what is next (summary; details in sections 3 to 5)

**Works (keep):** the panoramas as the only texture source (3.4); clean geometry where the scan is noisy (plan walls, boxes, ground heightfield, facades, fitted roof, fence panels) and scan where it is not; one photo per box face and per fence panel; line of sight against a clean model (scan minus what was replaced plus what was built: boxes, fan stand-ins, the fitted roof); measuring from the scan instead of guessing (door positions, wall-face offsets, soffit and porch-ceiling heights, flat vs built undersides); positive evidence before carving, filling or moving anything; `_shade_local` (one shading call per 3 m cell) everywhere points are spread out; hand overrides for what rules cannot decide (`splits`, `drop_boxes`, `remove`, `remove_outdoor`, `inside`); judging from room corners, free views and the per-camera comparison.

**Failed (do not retry without a new idea):** the MatterPak texture as a texture source or a fallback for unseen walls (smears, brick indoors); judging from the panorama positions or the score; boxing round fixtures; per-texel blending on shiny box faces; patch-copy texture synthesis (stamped lights, grids); stand-in solids for every hanging face; flattening a porch with open rafters; the aerial photo on a roof in winter shadow; per-texel photos on roof edges and eaves (sky and leaf colours: plain trim colour instead); automatic rules for scan artefacts and masonry notches (both judged the two houses wrongly: use the overrides); the default outdoor `exterior.py` output path for experiments; reused variable names in long functions (four broken builds). Each has its row in a "tried and changed" table in 3.5 to 3.10.

**Next, ranked (the user picks; sections 4 and 5 have the full lists):**
1. Verify the v43 houses in the real game client once Lane C has removed the placeholder yard slab and moved the zone triggers; fix what shows there (tone mapping, alpha cutoffs on leaves and fences, texture memory, the seam between the block's ground and the scanned yard at the door).
2. Round fixtures (toilets, pedestal sinks, faucets, the fireplace) are still raw scan: one photo per scan piece (5 item 2), then possibly catalog models placed at the scan's position.
3. Deck, railings, posts, the AC unit, open porch rafters as boxes, one photo per face (5 outdoors 17): most of the raw scan a player still touches outdoors and most outdoor collision triangles.
4. Eave widths from the scan where it has soffits (5 outdoors 18): roof edges are only placed to 0.3 to 0.5 m.
5. Tree variety (more species, bare-branch variants) and repainting the backdrop's sky where the old canopies were (5 outdoors 19).
6. Exposure compensation between panoramas (5 item 3): wall blotches indoors, sun and shade outdoors.
7. A number that sees crumple (5 item 1): share of raw-scan pixels in the room views and free views.
8. More from the drone splat as a gap filler (3.12 built the roof, unseen walls and deck railings; the user turned down splat trees and a far-field mesh): 6915's splat (not downloaded), the gable walls at roof steps, porch posts and the AC unit, the wall under the neighbour's tree (5 outdoors 25).

### 0.8 Interfaces between the parts (keep them, or tell the user before changing them)

- `exterior.py`, `fences.py` and `vegetation.py` use from `rebake`: `pack`, `plane_mesh`, `bake_plane(..., fallback, relief)`, `bake_triangles`, `_drop_crumbs`, `_shade_local`, `_material`, `_fill`, `room_top`, `ceiling_z`, `measure_ceiling`, `load_scan`, `Plane`, `ShellRoom` (`cuts`, `ceiling`), `Scan` (`rays`, `sight`/`eyes`), `WALL_INSET`, `PAGE`, `PAD`; from `panos`: `shade`, `PanoSet.subset`, `Raycaster`; from `house_mesh`: `Z_UP_TO_Y_UP`, `WallSegment`, `wall_box`. It deliberately does not use `rb._seen` (outdoors uses `_seen_faces`, face by face).
- `build_house` passes `exterior.add_exterior(scene, scan, rooms, panos, doors, house_outline, aerial=, remove=)` through `write_rebaked_interior_glb(..., extra=)`, and outdoor collision through `write_collision_glb(..., extra=collision_meshes(...))`. `house_outline` (not the name `footprint`, which is the MatterPak crop region) comes from `exterior.house_footprint(scan, shell_rooms(parts), void_polys(parts, overrides))`; `play_doors(parts, spans, outline)` picks the opened doors.
- Generated outdoor geometry plugs in through one hook in `add_exterior`: `fences.replace_fences`, `railings.replace_railings` (needs the splat), then `vegetation.replace_vegetation`, each `(scene, scan, g, faces, tree, avail, panos, walls, report) -> (taken, solids)`: it adds its nodes, returns which scan faces it replaces and its collision triangles. A new replacer (decks, posts) belongs there.
- Node names other lanes see (HANDOFF.md 17:00, 18:24, 23:30, 23:55): `floor_N`, `walls_N`, `ceiling_N`, `ceiling_scan_N`, `fixtures_box_N`, `fixtures_N`, `yard_N`, `facade_N`, `roof_N`, `backdrop_N`, `outdoor_N`, `trees_N`, `fence_N` (and `fence_rN`: railing panels, same material kind as fences, 3.12), `plants_N`; collision `walls`, `floor`, `fixtures`, `ground`, `outdoor`, `bounds`. Renaming any of them needs a HANDOFF line.

---

## 1. Where things are

| What | Where |
|---|---|
| Branch | Everything is on `main` (first session 2026-11-14; second, outdoors and third sessions fast-forwarded in on 2026-11-15 at 0429cc2). Continue on `lane-a/combined-quality`, worktree `/Users/pfu/hp-combined-quality` (rebase onto `main` before each merge, 0.5). The older branches `lane-a/house-quality`, `lane-a/outdoor`, `lane-a/combined` and their worktrees (`/Users/pfu/hp-house-quality`, `/Users/pfu/hp-outdoor`, `/Users/pfu/hp-combined`) are superseded; do not build on them. |
| Checkout and data | Main checkout `/Users/pfu/hackprincetonf26`. All gitignored outputs below now live in its `worldgen/out/` (moved from the old worktree). New worktrees symlink `worldgen/out` and `.env` to the main checkout's, so every worktree shares one data folder. **Several agents build into that folder at the same time**: experiment in your own pack version (`--version <N>`, see section 9), never in one somebody else is writing. |
| Pipeline code | `worldgen/build_house.py` (orchestration, rooms, doors, graph, collision), `worldgen/rebake.py` (clean shell, carving, fixtures, texture baking, glb export), `worldgen/panos.py` (panorama sampling, ray-caster, multi-view shading), `worldgen/exterior.py` (the scanned lot outdoors: yard, facades, roof, outdoor pieces, backdrop, outdoor collision; section 3.9), `worldgen/aerial.py` (Vexcel surface model, ortho and roof outline in the house's frame), `worldgen/roof.py` (roof planes fitted to the surface model), `worldgen/vegetation.py` (tree and shrub models in place of scanned ones), `worldgen/fences.py` (fence panels in place of scanned fences) (all four: section 3.10), `worldgen/splat.py` (the drone splat as an offline source: roof sheet, shingles, wall fill) and `worldgen/railings.py` (railing panels from it) (section 3.12), `worldgen/house_mesh.py` (OBJ reading, collision.glb, gltf-transform compression, the older interior writers), `worldgen/sources/matterport.py` (the only Matterport client: read-only, cached), `worldgen/pack.py` (manifest; `furniture_catalog` is the shared library `furniture/v1/catalog.json`, contracts v2.3.0), `worldgen/publish.py` (`--local` only; also copies the furniture library). |
| Per-house config | `worldgen/blocks/hou_navidad_01.json` (listing id to Matterport model, which interior method to use, hero role). `worldgen/overrides/<model_id>.json` (hand room splits). |
| Tests | `worldgen/tests/test_build_house.py`, `worldgen/tests/test_rebake.py` (shell, carving, shading, relief boxes, door slabs, hanging objects), `worldgen/tests/test_exterior.py` (outdoors), `test_aerial.py`, `test_roof.py`, `test_vegetation.py`, `test_fences.py` (3.10), `test_splat.py` (3.12), `worldgen/tests/test_pack.py` (manifest). |
| Preview and scoring tools | `worldgen/scripts/preview/` (see section 6). Not part of the package; run with `uv run python scripts/preview/<name>.py` from `worldgen/`. |
| Outputs (gitignored) | `worldgen/out/packs/hou_navidad_01/v<N>/houses/<listing_id>/{interior.glb, collision.glb, rooms.json, graph.json}` and `manifest.json`. Downloads in `worldgen/out/raw/<model_id>/` (MatterPak zip and OBJ, skybox faces, defurnished OBJ for 6915), caches in `worldgen/out/cache/` (Matterport JSON; uncompressed glbs in `cache/glb/<model>_interior_pano.glb` for v1 and `..._pano_v<N>.glb` for other versions). About 1.1 GB for the two houses. |
| Pack versions on disk | See 0.2. In short: v3 LIVE (v1's houses), v4 built with the v43 houses but not live, v43 the house candidate. |
| Notes for other lanes | `HANDOFF.md`, the lane-a entries dated 2026-11-14 (house pipeline: 17:00, 18:24, 23:30, 23:55). |

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
uv run python -m worldgen.exterior --block hou_navidad_01 --listing=hou_236595179 --dest out/cache/glb/<yours>.glb   # outdoors only; ALWAYS --dest (section 9)
uv run pytest && uv run ruff check . && uv run python ../contracts/validate.py out/packs/hou_navidad_01
uv run python -m worldgen.publish hou_navidad_01 --local     # copies the pack into apps/web/public/packs

cd ../apps/web
VITE_PACK_BASE_URL=/packs/packs/hou_navidad_01/v1 VITE_START_ROOM=hou_236595179.r3 VITE_SOLO=1 npx -y pnpm@12.18.0 dev --port 5199
```

`npx -y pnpm@12.18.0` is needed because the repo pins pnpm 12.18.0 and the locally installed pnpm fails to switch to it. `--refresh` on build_house re-fetches the Matterport model JSON. `--bake-res` (texels per metre on walls and floors, default 256) and `--texture-size` (max texture edge after compression, default 2048) are the quality/size knobs. `--no-exterior` builds the interior alone (no yard, facades, roof or backdrop). On `python -m worldgen.exterior`: `--dest <glb>` (where to write), `--parts roof,facade` (bake only some of yard, facade, roof, backdrop, pieces: one to two minutes instead of eight), `--no-aerial` (roof from the scan alone); `--version N` (N > 1) also writes the uncompressed glb as `out/cache/glb/<model>_interior_pano_vN.glb`, so experiments do not overwrite the v1 file used for scoring.

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

### 3.9 Outdoors: the scanned lot (`worldgen/exterior.py`, added 2026-11-14)

Decided with the user on 2026-11-14: the house zone becomes **house + its scanned lot**, in the same house-local frame and the same files (new nodes in `interior.glb` and `collision.glb`, no contract change). Past the scan's edge, a **panorama backdrop**. Trees: **scan geometry with a sky cutout** (fall back to library trees only if that looks bad). Doors: the **front and back doors open** for play; the garage door stays shut.

Why it works: Matterport scans of houses include outdoor sweeps (15 of 38 positions on 7014, 11 of 31 on 6915), and the MatterPak mesh covers the whole lot, about 15 m around the house, with good ground, fence, deck, porch and facade geometry. So the outdoors gets the same treatment as the interior: clean geometry where the scan is noisy, scan triangles where it is not, every texel coloured from the panoramas (outdoor ones only; `outdoor_panos` = outside the union of room polygons).

`add_exterior` runs inside `write_rebaked_interior_glb` through its `extra` hook, after the interior is baked. In order:

1. **Ground** (`ground_grid`): a heightfield on a 0.15 m grid (`CELL`). Per cell, four rays are cast **upward from below the scan**; the first upward-facing surface hit (normal z > 0.6, stepping past others up to 4 times) is the ground, whatever is above it. Median of the 4, then a 5x5 median filter (textured at 96 texels/m, `GROUND_RES`, in tiles of at most 10 m) (grass noise goes, steps such as a deck edge or a curb stay). Cells inside the house outline are removed. The lot = cells with ground, closed (3), holes filled, opened (2), **every piece of 4 m2 or more** (the house splits front and back yards), outline simplified by 0.11 m.
2. **Facades** (`house_footprint`, `facade_ring`, `facade_planes`): the house outline = union of room centreline polygons and dropped "Void" rooms, notches under 0.6 m closed; then every notch under 1.5 m wide (mitred closing, so it stays rectangular) with **no scanned ground** in it is added (a closet the plan left out, a chimney chase: 7014's closet behind bedroom r8). A notch with ground (7014's open entry alcove) stays outside. The same footprint decides which doors are outside doors. Each segment is moved out onto the real outer wall face (median hit distance of rays from 0.6 m outside, at 0.5 to 2.0 m up, counting only hits 0.02 to 0.45 m outside the centreline; segments under 0.6 m or unmeasured borrow their neighbours' offset), corners rebuilt by intersecting neighbouring segments, falling back to one uniform offset if the result is invalid or a corner moves over 0.7 m. Planes run from 0.15 m below the lowest ground along them to the eave height, or next to a room taller than that (a garage, a vaulted room) to that room's top + 0.1 m. `eave_height` measures the soffit per segment: horizontal rays at the wall every 5 cm up; the wall ends where most rays stop hitting within 12 cm of the facade plane for 30 cm (window heads, unscanned glass, dip for less); length-weighted median over segments, clamped to [floor + 2.1, highest room top + 1.0] (7014: about 2.3-2.45 m; 6915: about 2.45-2.5 m). Baked at 128 texels/m (`FACADE_RES`) with `bake_plane(..., fallback=True)`: texels no outdoor camera sees take the MatterPak's own colour where the scan has a surface there.
3. **Roof** (the fallback since the third session; with aerial data the roof is built as in 3.10): `roof_coverage` = fraction of 0.5 m cells of the outline under upward-facing scan faces between the ceiling + 0.15 m and + 4 m (tree canopies are higher). At 0.7 or more (6915: 0.96) the **scanned roof** is kept as scan pieces (step 4), with sky-coloured texels on upward faces replaced by their chart's colour (`unsky`: cameras look past the roof edge). Below it (7014: 0.02) a **generated hip roof**: the facade outline plus a 0.45 m overhang (`OVERHANG`) at the eave height, height growing with the distance to the eave line (exact for convex outlines, a rounded valley at concave corners) but never below any room's top + 0.1 m inside that room (7014's garage and vaulted living room), pitch = median slope of sloped scan faces just above the eaves (14 to 40 degrees) else 5/12; a flat soffit under the overhang and a 0.2 m fascia band. Texels whose photo colour is sky count as unseen (`no_sky`) and are filled from the shingles around them.
4. **Pieces** (`outdoor_faces`): every other scan face inside the lot (buffered 1 m) that is not inside the house volume, not on the facade (within 0.1 m and vertical), not in the wall slab, not on the ground (within 0.1 m of the heightfield and facing up), not more than 0.25 m below it, and not under a generated roof. Kept only if an outdoor camera sees it; pieces under 0.03 m2 dropped. Faces more than 2 m above the ground and away from the roof (`TREE_MIN_Z`) are **trees**: baked separately at 40 texels/m (`TREE_RES`) with alpha cut where the texel is sky (`_sky`: blue, or bright and grey); the rest at 96 (`PIECE_RES`).
5. **Backdrop** (`backdrop_patches`): a ground disk and a dome of radius 45 m around the middle of the lot. The disk (32 texels/m) has a hole 0.3 m inside the lot and 0.5 m around the house footprint, follows the lot's edge height near the lot and flattens farther out; the dome is four 90 x 60 degree tiles at 16 texels per degree plus a cap. Shading normals point at an eye 1.5 m above the middle of the lot, so every outdoor camera counts as a frontal view and the ray-cast occlusion test against the scan picks the cameras that see past the house and the trees. It is a baked, ground-anchored panorama: right near the capture points, some parallax far from them.
6. **Doors** (`build_house.play_doors`): outside doors (one room) on the house outline (both ends within 0.25 m), found open in the scan (so their position is measured), not in a garage, get `play_open`. They are cut from the room's wall planes (`ShellRoom.cuts`, `rebake._cut_boxes`), from the facade, and from collision; `door_frames` adds jambs, a lintel and a threshold through the whole wall (room wall surface to facade), baked from every camera. Both houses: front + back door. Doors into dropped Void closets are not on the outline and stay shut.
7. **Collision** (`collision_meshes`): `ground` (the yard heightfield on a 0.45 m grid, about 5k triangles), `outdoor` (non-tree pieces below 2.2 m: fences, deck, railings, posts, the AC unit, shrubs, trunks) and `bounds` (0.3 m boxes, 4 m tall, along the outline of lot + house, simplified 0.3 m), added to `collision.glb` by `house_mesh.write_collision_glb(extra=...)`.
8. **Textures**: planes and patches share pages (`rebake.pack`); every patch is cropped to the bounding box of its piece (`plan_tiles`), otherwise empty tile corners cost whole pages.

Nodes added to `interior.glb`: `yard_N`, `facade_N`, `roof_N`, `backdrop_N` (shared pages), `outdoor_N`, `trees_N` (alpha MASK). Turn it off per house with `"exterior": false` in `blocks/<block>.json` or for a run with `--no-exterior`. Iterate on the outdoors alone with `uv run python -m worldgen.exterior --block <block> --listing <id>` (about 2.5-6 min; writes `out/cache/glb/<model>_exterior.glb` for the preview scripts, not the pack).

Tried while building it, and changed:

| Tried | Problem | Now |
|---|---|---|
| Ground cells from triangle centroids (lowest 10% by area) | The MatterPak covers flat concrete with few triangles metres long: the whole 7014 driveway had no ground cells | Upward ray casting per cell (4 rays) |
| Keep only the largest lot piece | The house splits front and back yards; only the back yard survived (285 m2 instead of 518) | Every piece of 4 m2 or more |
| One full texture per 15 m tile | 13 shell pages on 7014 for mostly empty tile corners | Each piece cropped to its bounding box |
| Generated roof shaded like any surface | Cameras look past the real roof edge: blue and grey sky on the roof | Sky-coloured samples count as unseen (`no_sky`) |
| Unseen facade texels filled from neighbours | Side walls no camera sees became grey smears | MatterPak texture fallback (`bake_plane(fallback=True)`) |
| Trees at the fixture resolution | 59k canopy triangles at 96 texels/m: half the outdoor pages | Separate tree pages at 40 texels/m |
| Backdrop disk with a hole only where the lot is | 7014's lot does not wrap around the house (the north side yard was not scanned), so the disk ran under the house and poked through the floors near the deck: indoor score 14.07 -> 17.58 | The disk always has a hole around the house footprint |
| House outline = kept rooms only | An unmodelled closet in 7014 became an outdoor notch; its door leaf was kept as an "outdoor piece" in front of the bedroom's closet door | Void rooms and ground-less notches belong to the house (`house_footprint`) |
| Outline gaps from the convex hull | Rectangular notches became triangles (diagonal facades); 6915's unscanned front garden became a wedge of house | Mitred closing, 1.5 m |
| Collision ground = the render mesh | 49k triangles | 0.45 m grid |
| Facade offset accepted down to 0.1 m inside the wall centreline | Where the outer wall was not scanned, rays hit inside surfaces: a facade plane stood in front of 7014's recessed fireplace and a closet door, seen from the room (indoor score 16.4) | Only hits outside the centreline; the MatterPak fallback only from faces facing out |
| Eave = 97th percentile height of facade-facing scan faces | A tree trunk on the facade line: 12.6 m (12 m facades); gable walls: 3.6 m, with the real roof's photo painted on facades above the real soffit and the generated roof floating over it | Wall coverage profile per segment, median over segments |
| Outdoor pieces filtered with `rebake._seen` after merging the second session (whole pieces kept once 25% is seen) | Tree canopies and fences are huge connected pieces: 7014's interior.glb went from 22 to 33 MB, 255k tree triangles | `exterior._seen_faces`: face by face, outdoors only; the interior keeps whole pieces |

### 3.10 Third session (2026-11-14, night): roofs from the aerial data, tree models, fence panels

Started from the user's walk of pack v41 in the QA viewer. What they pointed at: roofs on both houses ("parts of skies and trees in them, and that crumpled up foil look"), trees ("fine to cut", replace by models that fill the same space), and "the fence and areas behind the shrubbery don't look coherent". Their question: can the other data layers fill these in. Yes: the scan sees a house from the ground, Vexcel sees it from above, and the two are registered well enough (below) to be used as one.

**What Vexcel data is used, and where it comes from.** Nothing new was fetched. The block builders (lane-a/block-combined) cached, for the whole block: the surface model `dsm_extract_urban_bbox..._z20.tif` (float32 GeoTIFF, EPSG:3857, 0.149 m pixels = 0.13 m on the ground) and the ortho `ortho_extract_urban_bbox..._z21.jpg` (0.065 m on the ground), both under `out/block_compare/cache/vexcel/`. `worldgen.costar` cached each listing's `property/extract` answer under `out/cache/vexcel/`: the structure outline (`footprint.wkt`, which traces the roof's edge, eaves and porch roofs included) and roof attributes (both houses: `shingle`, `gable`; 7014 19% tree cover). `worldgen/aerial.py` reads these into the house's Matterport plan frame (plan -> house-local (x, -y) -> block by `Listing.world` position and heading -> lat/lon by `worldgen/geo.py` -> EPSG:3857). It never calls the network; a house whose block has no cached rasters gets `aerial.load() = None` and the roof falls back to the scan (step 3 of 3.9). For a new property: fetch `dsm/extract` (zoom 20) and `ortho/extract` (zoom 21) with `layer=urban, srid=4326, crop=bounding_box` for a box at least 40 m around the house into that folder with the same file names (the parameters are in `block_data.py` on lane-a/block-combined); two billed calls.

**Registration, measured.** Horizontal: the room outline drawn on the ortho and on the surface model sits on the building to within about 0.3 m on both houses (`Listing.world` is the Matterport geo anchor refined onto the Vexcel footprint by worldgen.costar). Vertical: `Aerial.level` ties the surface model's datum to the scan's on the yard's ground (the scanned ground heightfield, every 7th cell, more than 1.5 m from the walls; the offset is the median over samples within 0.25 m of the lowest tenth's median, so trees, cars and eaves do not count). Spread of those samples (median absolute deviation): 3.4 cm on 7014, 3.2 cm on 6915. After levelling, the fitted roof clears the scanned ceilings by a median of 0.3 to 1.2 m in every room of 7014.

**The roof** (`worldgen/roof.py`, `exterior.aerial_roof`, `aerial_roof_patches`), replacing step 3 of 3.9 when aerial data exists:

1. *Region* (`roof_region`): the facade outline grown by `MIN_OVERHANG` (0.3 m), united with Vexcel's outline of the roof (it adds the real eaves, 7014's back porch roof and entry alcove, and 6915's attached garage, which the floor plan lacks), gaps under 0.6 m closed. Without a Vexcel outline: the facades plus `OVERHANG`.
2. *Planes* (`roof.fit_roof`): the surface model sampled on a 0.15 m grid (`STEP`) over the region; cells within 0.45 m of the edge (`EDGE`) are not fitted (the raster mixes roof and ground there). Repeatedly: of up to 300 flat, unused cells (`SEEDS`), each proposes the plane through it with the local gradient (heights smoothed by 1 cell); the proposal with the largest connected set of unused cells within 0.10 m (`TOL`) wins, is refitted by least squares to those cells (twice, growing in between) and takes them. Stop when the best is under 2 m2 (`MIN_AREA`) or after 16 planes. A plane steeper than 1.2 (`MAX_SLOPE`, 50 degrees) or with a median height under floor + 1.9 m (ground, when the region overshoots the building) is discarded with its cells. Every other cell (edges, ridge rounding, vents, a tree over the roof) takes the plane of the nearest fitted cell.
3. *Height* (`Roof.height`): the cell's plane; within 0.45 m (`REACH`) of another plane's cells, whichever of those planes the raster agrees with best, which is the lower plane at a ridge or hip and the higher one in a valley, so planes meet in straight lines at their true intersection. Where the raster is more than 0.30 m (`TRUST`) off the cell's own plane (eaves, a tree) the cell's plane stands.
4. *Never below the ceilings* (`ceiling_floor`): flat rooms: the listed height; vaulted rooms: the scanned ceiling (`rebake._ceiling_grid`: per 0.2 m, the first downward-facing surface above 1.9 m that a ray going up from 1.5 m meets; holes filled from the nearest sample); plus `ROOF_GAP` (0.12 m), each room grown 0.12 m, max-filtered over 0.45 m. The build report says how much of the roof this lifts and by how much (`lifted_by_ceilings`, `max_lift_m`): 2.6% and 0.36 m on 7014, 4.0% and 0.24 m on 6915. Large values mean the registration is off.
5. *Walls follow the roof.* Outside: `facade_planes` ends each wall at the roof's underside all along the segment (sampled every 0.1 m), so a gable end is a pentagon with its siding and vent from the photos; before, facades next to a tall room rose as rectangles to that room's top and were painted with the sky and trees the cameras saw past the real roof (the slabs in the user's screenshot). Inside: a vaulted room's walls (and the wall strips over open passages into lower rooms) end `WALL_OVER_CEILING` (8 cm) above the scanned ceiling next to them (`rebake._under_ceiling`: the highest ceiling sample 0.1 to 0.5 m into the room and 0.3 m either way along the wall) instead of rising as rectangles to the ceiling's highest point, where they stood above the real roof at the eaves.
6. *Surfaces* (`aerial_roof_patches`): the top (0.2 m mesh, 64 texels/m); the underside outside the walls (eaves, porch and alcove ceilings; step 6a), as tiles cropped to their pieces; a band `SOFFIT` (0.18 m) high around the edge, following the roof's edge height (fascia, rake boards).

    6a. *The underside is measured* (`eave_underside`). Like the ground, upside down: per 0.15 m cell under the roof and outside the walls, a ray goes up from 1.2 m above the ground, and the downward-facing surfaces (normal z under -0.5) it meets between floor + 1.9 m and the fitted top + `EAVE_OVER` (0.5 m: the top is only placed to about 0.3 m) are noted, the first and the highest (up to 6 hits). Cells within 0.3 m of a measured one take the nearest value; a 0.75 m median filter flattens the scan's crumple.

        *Flat or built.* A measured underside becomes a plane only where it is flat: at least `EAVE_FLAT` (0.7) of the measurements within about 0.5 m lie within `EAVE_FLAT_TOL` (4 cm) of their smoothed height (a drywall or plywood ceiling: 7014's entry alcove). Where they do not (open rafters: 7014's back porch, 32.7 m2 with its rim) the area is *built*: regions at least 0.6 m across, grown 0.6 m to reach the roof's edge. There no plane is made, every scan face that does not face up is kept, and the roof's top is lifted to clear the highest scanned board by 6 cm (`under.cover`, joined to the roof's floor).

        *Which flat measurements are believed.* Where the area outside the walls is wider than 2 x `EAVE_EDGE` (1.2 m: a porch, an alcove) any height; in a narrower strip (an ordinary overhang) only one less than `EAVE_DROP` (0.7 m) under the guess (a soffit boxed level from the fascia lies 0.2 to 0.6 m under the top at the wall; the head of a recessed garage door under a gable's rake, 1.5 m). The guess is the top less `SOFFIT`; the underside is never above it, and within `EAVE_RIM` (0.3 m) of the roof's edge it is the guess, so a flat porch ceiling rises over its last 0.3 m to meet the edge band. The downward-facing scan faces within 0.2 m of the measured ceiling (right up to the edge) are dropped, and where the measured ceiling or soffit is lower than the guess, so is every scan face outside the walls from 0.1 m under it up to the roof (gutter fronts, fascia, framing above a ceiling). Measured on 46% of 7014's underside, 17% of 6915's.
7. *Colour of the top.* Texels a ground camera saw (sky-coloured samples do not count) keep the photo. The rest takes the aerial photo, scaled per channel to the photographed shingles where both exist, but only when the aerial photo shows the roof evenly lit: scale within 0.6 to 1.6 (`ORTHO_GAIN`) and brightness over the roof (90th over 10th percentile) under 3.0 (`ORTHO_SPREAD`). 6915: scale (1.07, 0.95, 1.06), spread 2.13: used, with its vents and staining. 7014: scale (3.6, 2.8, 2.2), spread 4.8 (the roof lies in winter shadow under bare branches): not used; the roof takes the plain shingle colour (median of the dark photographed texels), a little lighter on planes facing a sun at azimuth 205, elevation 50 (`SUN`) so ridges and hips still read in an unlit render. Aerial texels more than 0.4 m above the fitted roof (a tree over it) are filled from the roof around them. The two sources are cross-faded over 0.3 m and the roof's edge always takes the fill.
8. *Edge band*: one plain trim colour, the median of its bright (mean over 150) photographed texels (7014: 179, 166, 156; 6915: 203, 185, 161); the bands are baked first. *Underside*: photos, sampled at the real ceiling (`snap`), with sky-coloured samples rejected and seen specks under about 0.25 m across opened away (leaves between a camera and the ceiling); what no camera saw takes a plain colour, blended in over 0.25 m: the median of the seen part when the tile is seen at least half, else the trim colour, which is also the whole tile's colour when under 15% is seen (soffits are painted like the trim; a tile's few dark seen texels made unseen eaves brown).
9. *Line of sight.* The fitted roof joins the model that decides what a camera sees outdoors (`eyes` in `add_exterior`: the scan plus the roof's triangles). 7014's scan has almost no roof (2% coverage), so cameras in the yard "saw" the far slope, the porch ceiling and the sky through it.
10. *What it replaces* (`outdoor_faces`): scan faces under the roof's region from `SOFFIT` + 0.3 m below its top to 0.6 m above (the scanned roof, gutters, fascia, eaves) are dropped; higher ones are tree canopy.

Result: 6915: 4 planes (72, 60, 20, 18 m2: the main gable and the garage's), pitch 22.3 degrees. 7014: 11 planes (the main ridge, the garage's and the bedroom wing's gables facing the street, the low back porch roof), pitch 21.1 degrees.

Also changed: `rebake.bake_plane(..., relief=)`: how far in front of a plane a texel may be sampled at the real surface (default 6 cm as before; facades pass `FACADE_RELIEF`, 0.28 m). 7014's garage door sits in a recess: the facade plane is fitted to the door, the wall and gable above stand about 20 cm in front of it, hid the plane from every camera, and the gable came out as a dark smear.

Tried on the roof and changed:

| Tried | Problem | Now |
|---|---|---|
| A vaulted room's missing ceiling samples, and the ring just outside the room, counted as the room's highest point | A 1.4 m high ring of raised roof around 7014's living room (10% of the roof lifted) | Samples are filled from the nearest one and lookups clamp to the grid: 2.6% lifted, 0.36 m at most |
| The aerial photo scaled to the ground photos' shingle colour, always | 7014's roof lies in winter shadow: scale 3.6, sunlit dapples blown out to white, shadows light blue | Used only when evenly lit (step 7), else plain shingle colour with plane shading |
| The edge band and eaves textured from the photos like any surface | The roof is placed to about 0.3 m and leaves hang in front of it: sky and leaf colours along every edge, a mosaic on porch ceilings | Edge band: one trim colour. Eaves: sky rejected, specks opened away, unseen parts take the seen part's colour |
| The facade plane fitted to 7014's garage door, used for the gable above it | The door sits in a recess; the wall above it stands 20 cm in front of the plane and hid it from every camera: a dark smear | `relief` of 0.28 m for facades |
| The fitted roof not part of the line-of-sight model | Photos projected through the roof: the far slope, leaf colours on the porch ceiling | `eyes` = scan + roof |
| Underside = top less `SOFFIT` everywhere | It sat above the real alcove and porch ceilings, which hid it from the cameras (a mosaic) while the crumpled scan ceiling stayed visible below | Measured underside (step 6a) |
| The measured underside used right up to the roof's edge, with the band reaching down to it | A wavy fascia; then, with a line fitted along each edge, deep plain slabs around the alcove | The underside is the guess within 0.3 m of the edge; the band is always `SOFFIT` high |
| Every measured underside flattened into a plane | 7014's back porch has open rafters. Flattened, the plane sat between rafter bottoms and boards, was hidden from the cameras by the scan it was meant to replace, and came out as a mosaic; the per-panorama comparison with v41 (`compare_panos.py --outdoor`: +7.7 at the porch camera) found it | Flat or built (step 6a): built undersides stay scan |
| A tree model for every measured crown | The sapling beside 7014's porch post became a crown growing through the porch roof | For `vegetation.py` the house is everything under the roof (walls united with the roof's region); `_clip_plants` drops any plant triangle that still reaches under the roof |
| Any measured underside believed | Under the rake of 7014's garage gable the first downward-facing surface is the head of the recessed garage door, 1.5 m below the roof: the band hung down over the whole gable and hid its siding | Narrow strips believe a measurement only within `EAVE_DROP` of the guess; wide areas (porches, alcoves) any |
| The MatterPak texture as fallback for every unseen facade texel with scan up to `relief` in front of it | With 0.28 m of relief the fallback reached things standing in front of the wall | The fallback only takes scan within 0.12 m of the plane, as before |
| A rule for scan artefacts: drop low pieces that float above the ground away from the house | It finds the blob beside 7014's driveway scan position (1 m2, 0.6 to 1.8 m up, not in any photo: it takes the sky's colour), and also 6915's kettle grill, whose legs were not scanned | By hand: `"remove_outdoor"` in the override file. The right test is whether the photos show the object (its colours agree between cameras and differ from what is behind it); not built |
| A rule for masonry notches: rays from inside a notch across its mouth, "walled off" when most meet scan | Wrong both ways: 7014's open entry alcove came out walled off (and lost its front door), 6915's corner fireplace open | By hand: `"inside"` polygons in the override file. `house_footprint` is unchanged |
| `parts` as the name of the new bake option | Shadowed twice: by the room list in `exterior.main` (every bake empty) and by a mesh list in `add_exterior` (bakes of all parts exported no scan pieces while pieces-only bakes were fine) | Named `bake`. Section 9 |

**Two hand overrides for the outdoors** (`worldgen/overrides/<model>.json`, plan coordinates), for what the rules cannot decide:

- `"remove_outdoor": [{"at": [x, y, z], "radius": r, "why": ...}]`: outdoor scan faces within `radius` of the point horizontally and within max(radius, 1 m) of its height are dropped. For scan artefacts that are in no photo. Used once: a 1 m2 blob floating beside 7014's driveway scan position, in the demo's opening view. (Pack v41 did not show it only because of the `_seen_faces` bug described at the end of this section.)
- `"inside": [{"polygon": [[x, y], ...], "why": ...}]`: polygons added to the house outline (through `build_house.void_polys`), for masonry the floor plan leaves out as a notch. Used twice on 6915: the corner fireplace (a triangular notch in the east wall; its firebox floor counts as scanned ground, so the notch was yard, and facade planes lined it, black from the firebox and visible through gaps in the scanned chimney: the dark patch on the back wall beside the back door) and a slot 0.6 m wide between two rooms.

**Trees and shrubs as models** (`worldgen/vegetation.py`, called from `add_exterior` after the outdoor scan faces are chosen). The scanned canopies were torn sheets with the sky cut out, and a third of 7014's file. The user's decision: cut them, and put models in their place that fill the same space. The models are the three procedural live oaks the block builders made (`out/vegetation/v1/`: `live_oak_{a,b,c}.npz`, 4.2 to 4.8k triangles each, `leaf_rgba.png`, `bark_diff.jpg`, `LICENSE.json`; geometry procedural, textures from Poly Haven, CC0; copied from `out/block_compare/cache/trees`, generator `scripts/block_trees/` on lane-a/block-combined). If that folder is missing nothing is replaced and the report says why. `measure(scan, g, walls, panos)` runs first, then `replace_vegetation` builds the nodes:

1. *Candidates*: scan faces more than 0.25 m above the ground heightfield and outside the facade outline grown 0.05 m (all of them, not only the camera-seen ones: crowns are measured whole).
2. *Foliage* (`planarity`): the area-weighted orientation tensor of face normals per 0.5 m voxel (`VOXEL`), summed over 3 x 3 x 3 voxels; largest eigenvalue over trace. Under 0.62 (`LEAFY_MAX`) is foliage. Measured: a fence 0.99, crowns 0.41 to 0.44. A pergola lattice, a deck railing and the AC unit also read as foliage, hence steps 4, 5 and 9.
3. *Canopy grid* (`canopy_grid`): foliage above 2.0 m (`CANOPY_MIN`) and more than 1.0 m from the walls (`HOUSE_CLEAR`), on a 0.25 m plan grid with the highest and lowest foliage per cell; gaps up to two cells closed.
4. *Structures* (`slab_cells`): cells thinner than 0.5 m whose top varies under 0.5 m within 1.25 m, in patches of 3 m2 or more, are a pergola, an awning or cables: kept as scan.
5. *Trunks* (`find_trunks`): 0.1 m plan cells where steep faces (|normal z| under 0.7) fill at least 5 of the seven 0.25 m height bins between 0.3 and 2.05 m, counted over 3 x 3 cells and grouped. A group is a column when it is at most 1.6 m across and at most 0.8 of its horizontal normals share one axis (else a flat panel). A column is a post, not a trunk, when no foliage or slab is within 1 m above it, it is painted (median photo colour: min channel over 140, spread under 45), it stands under a slab, it is within 0.3 m of the house wall, or crown height over trunk radius exceeds 90 (`TRUNK_SLENDER`; the worst is dropped, crowns are shared out again, repeated). Scan within its radius + 0.3 m of a post is kept at any height.
6. *Crowns* (`segment_crowns`): each connected foliage patch is shared between the trunks within 1.5 m of it; each cell goes to the nearest trunk whose reach covers it (45 x trunk radius, clamped to 3 to 14 m). A trunk's crown needs 1 m2; a piece no trunk claims needs 4 m2.
7. *Trees*. With a trunk: the tree stands at the trunk. Without one, small and in the lot (crown starting below 2.5 m, ending below 8 m, centre at least 1 m inside the lot): it stands at the median of the low plant-coloured foliage under the crown (30 faces or more), else under the crown's middle. Without one otherwise: a neighbour's tree, only if 8 m2 or more; it stands 1.5 m outside the lot across the nearest edge and has no collision. Measured per tree: the principal axes of the crown's cells; radius = 95th percentile x 1.08, the minor at least half the major; top = 98th percentile + 0.3 m; bottom = 10th percentile, at least 2.0 m; lean = crown centre minus trunk, capped at 0.35 x height; leaf colour = the median non-sky photo colour, pulled 40% toward (92, 116, 66), luma held to 55 to 125.
8. *Model* (`place_tree`): the variant is chosen by crown radius over crown height. The trunk part maps onto ground-to-crown-bottom and the crown part onto bottom-to-top, so the lowest leaves sit where the scan's did (players walk under them). The crown is scaled to the two radii along the measured axis and sheared by the lean; the trunk is thinned or thickened to the measured radius (0.45 to 1.6 x the model's). Yaw and card choices are seeded from the position, so rebuilds are identical. Leaf cards or bark triangles with a vertex inside the facade outline grown 0.2 m, below the lowest ground + 6 m, are dropped (nothing grows into the house).
9. *Shrubs* (`find_shrubs`): foliage up to 1.6 m in 0.25 m voxels where at least half the faces are plant-coloured in the scan's own texture (green, or olive: g at least 0.86 r, b under 0.8 g). A group is a shrub when it reaches below 0.6 m, is at least 0.5 m tall, 0.6 to 5 m across, covers 0.6 m2, has 150 faces, and foliage is 0.6 of the scan surface in its box. A shrub within 1 m of a yard tree's base is that tree's own growth. **A shrub the cameras saw 0.35 or more of stays as scanned** (`SHRUB_SEEN_MAX`): 6915's rose bush and 7014's sago palm looked worse as balls of leaf cards. A shrub seen less becomes a half-ellipsoid of leaf cards (about 7 per m2, 0.5 to 0.85 m each).
10. *What goes* (`taken`): the foliage of modelled crowns and anything inside them with planarity under 0.85; other high foliage only within 1.5 m of a modelled crown or when plant-coloured; each trunk's column and non-flat scan within 1.5 radii + 0.25 m; under yard-tree crowns (the crown's ellipse + 0.5 m) foliage above 1.2 m and plant-coloured foliage below that; non-flat scan within 0.6 m of the base; foliage above 1.2 m within 1 m of the house inside the ellipse + 1 m; ground scraps 5 to 30 cm high in the cleared cells. Never posts, slabs or kept shrubs.
11. *Shading without lights.* Everything is rendered unlit, so the shading is in the texture: one RGBA leaf page with a row per leaf tint (up to 4, the nearest merged) and a column per light level (0.42, 0.66, 0.92, 1.2, with a slight blue-to-yellow shift), each slot the 2 x 2 leaf atlas at 512 px. A card's level = 0.12 + 0.30 x height in the crown + 0.22 x distance from the middle + 0.30 x facing a guessed sun (-0.3, -0.64, 0.7) + noise (sd 0.07, seeded by position). The atlas alpha is dilated by one texel against thinning in the distance. Bark is the tiling texture scaled to the measured trunk brightness (0.6 to 1.5), x 0.8 inside the crown.
12. *Output*: nodes `plants_0` (leaves, alpha MASK), `plants_1`, `plants_2` (bark, a repeating texture). Collision: an 8-sided prism per yard trunk up to the crown's bottom, and one per bush. `report["vegetation"]` lists every tree and shrub with its measurements; `measure()["debug"]["rejected_shrubs"]` says why each low blob was not replaced.

Result in pack v43. 7014: four trees (the front oak at plan (16.6, 9.8), 12 m tall, crown from 2.7 m, radii 6.9 / 5.9 m, trunk radius 0.27 m, leaning 3.3 m south; the grey back-yard tree; the yellow-green tree, without a found trunk; the south neighbour's tree, 13.7 m, outside the lot) and one shrub (ground cover by the north fence, seen 0.26). 168,634 scan faces replaced by 10,522 triangles; 5.2 megapixels of texture (leaf page 2048 px, two bark pages 512 x 1024). Kept as scan: the sapling beside the porch post (see the roof's table: for plants the house is everything under the roof), the pergola and its posts, the utility pole, the AC unit, the sago palm, the branch pile, the stump. 6915: no crown in the lot (the trees in its views are backdrop photos), the one shrub is seen 0.73 and stays; nothing replaced. Checked in three.js (the QA viewer): leaf cutouts and tiled bark render as in the ray-traced previews.

Tried and dropped: vertex colours for tint and shading (trimesh writes them as `_color`, not `COLOR_0`); any column under foliage as a trunk (pergola posts, downspouts and a utility pole became trees); `HOUSE_CLEAR` 0.7 m (the eaves joined a neighbour's crown into a ring around the house, with a 12 m "tree" inside it); removing every high foliage scrap (it ate the eave of 6915's unscanned garage); finer planarity or triangle size to tell the AC unit from leaves (neither does; colour does); replacing every shrub; letting a shrub's height run up under a tree (a 2 m pillar bush around the trunk).

**Fences as flat panels** (`worldgen/fences.py`, called from `add_exterior` before the vegetation). The scanned fences were wavy sheets with each triangle picking its own photos, and whatever a shrub or tree hid was a blur. Now every boundary fence is a straight panel textured from the photos, and hidden stretches are filled with boards from the same fence.

1. *Find runs* (`find_runs`). Scan faces with |normal z| under 0.5 inside the lot grown 1.5 m, minus the facade outline grown 0.3 m (all scan faces there, not only the seen ones). Points are sampled on them every 3 cm (`SAMPLE`) by area, with a fixed seed: face counts are useless, the MatterPak covers a flat fence with a few large triangles. Plan cells of 5 cm (`CELL`) are weighted by how many 10 cm height bins of the band 0.3 to 1.3 m above the ground they fill (`SOLID_BAND`); cells with fewer than 3 bins are ignored. Up to 40 times: the strongest line by a Hough search (0.5 degree steps, 3-cell smoothing), refitted twice by least squares to the cells within 0.12 m (`FENCE_TOL`); stop when the peak is below `FENCE_MIN_LEN / CELL x 10 bins x SOLID_MIN x 0.5`. Along each line, in 10 cm bins (`S_BIN`), a bin is solid when at least 70% of the band is filled (`SOLID_MIN`); holes under 0.5 m are bridged (`GAP_CLOSE`), single bins dropped. A stretch must be at least 1.5 m long and 1.2 to 2.6 m tall (`FENCE_MIN_LEN`, `FENCE_MIN_H`, `FENCE_MAX_H`). Each stretch gets its own least-squares line and three tests: flat (at least 60% of its points within 4 cm of the line, `FLAT_MIN`, `FLAT_TOL`; else it is a hedge), tall above the higher ground (median height at least 1.2 m; else a deck railing), and not under a roof (at most 50% of rays, started 0.2 m beside the run and 0.3 m below its top and reaching 1.2 m above it, meet a downward-facing surface, `ROOF_MAX`, `ROOF_REACH`; else a wall under its eaves). The ground under a run is the lowest of the heightfield at -0.3, 0 and +0.3 m across it. Run ends within 0.4 m of another run's line move onto the crossing point (`_join_corners`).
2. *Top line* (`_smooth_top`). Per bin, the scan surface followed upward until 20 cm are empty, then a 0.7 m median. Bumps under 2.0 m long are removed by an opening (`BUMP_MAX`: posts, overhanging shrubs; beyond the run's ends the line counts as low, so an end bump goes too); dips under 4.0 m are filled by a closing (`DIP_MAX`: the scanner missed the top behind a tree). Simplified to 3 cm. Steps and slopes survive.
3. *Panels* (`run_planes`). One `rebake.Plane` (kind `fence`) per run, from 8 cm below the ground (`GROUND_SINK`) to the top line; runs over 21 m are split (`PIECE_MAX`). A boundary fence is one double-sided plane on the seen side, so the far side is the mirrored near side. If both sides are seen at least 30% (`TWO_SIDED_MIN`, a coarse test at 8 texels/m) it becomes two planes 4 cm apart (`TWO_SIDED_GAP`); neither demo house triggers this (forced once on 6915: it runs). A panel seen under 8% (`UNSEEN_PANEL`) borrows the best-seen panel's picture, lined up at the top (`_borrow`).
4. *Texture* (`bake_run_plane`, 96 texels/m). Photos are sampled at the scan's real offset from the flat panel, per 0.25 m column block, smoothed over about 1 m (`_surface`). Views (`_views`): up to 16 panoramas (`VIEW_MAX_PANOS`), cosine over 0.2 (`VIEW_MIN_COS`), 0.25 to 14 m (`VIEW_MAX_DIST`); line of sight through `scan.eyes`, what is in the way looked up in `scan.rays`; a view is "sharp" when distance / cosine is under 26 m (`VIEW_SHARP`). *Occluders*: anything that is not ground (normal z at most 0.6) and more than 0.2 m in front of the panel, measured square to it (`OCCLUDER_GAP`); its texels do not count. Dense occluders (over 30% of a 0.25 m window, `OCCLUDER_DENSE`, `OCCLUDER_CLOSE`) also get a 0.4 m margin (`OCCLUDER_MARGIN`); a few twigs only hide themselves. *Leaf colour*: texels with green over red + 6 and over blue + 15 (`LEAF_OVER_RED`, `LEAF_OVER_BLUE`), in patches of at least 15 texels after a 3-texel closing (`LEAF_MIN_TEXELS`), are dropped with a 0.1 m margin (`LEAF_MARGIN`). *Compositing* (`_composite`): each texel goes to the visible view with the highest score, sharp before soft, then cos / distance squared; later views are gain-matched (0.7 to 1.4) where at least 200 texels overlap; seams cross-fade over 6 texels (`SEAM`). *Disagreement* (`_drop_disagreeing`, after a 2-texel blur): with three or more views, one more than 45 from the median is dropped (`CONSISTENT`); with two views more than 67.5 apart, the one within 1.0 m of an occluder's outline is dropped (`OCCLUDER_NEAR`). *Sky*: only where the scan has no surface within 0.2 m; in the top 0.25 m (`SKY_BAND`) such texels are cut out with alpha, so pointed board tops keep their points; elsewhere they count as unseen.
5. *Fill* (`fill_along`). Rows are aligned at the top line. Broad colour: the texel's own soft photo, or a smooth blend of seen texels around it (`_blend`, sigma 3 to 400). Detail: the picture minus a 12-texel blur (`DETAIL_SIGMA`), clipped to 3 times the typical detail. Holes are columns missing more than 25% of their height (`SPAN_MIN`); each is shifted as a whole by a whole number of board widths, at least its own length, onto "clean" texels (sharp, and more than 0.6 m from any occluder, `SOURCE_CLEAR`), up to 6 shifts per hole (`FILL_PASSES`). What is left takes single-board copies in its row, then the nearest detail from any row. The board width (`board_period`) is the shortest autocorrelation lag between 7 and 32 cm (`PERIOD_RANGE`) with a correlation at least 80% of the best and over 0.15 (`PERIOD_MIN_CORR`), default 14 cm (`BOARD_DEFAULT`); both demo houses fall back to the default. A panel under 15% sharp uses its soft views as they are (`SHARP_MIN`).
6. *What goes* (`taken_faces`): seen faces within 0.15 m of a run and below its top + 0.15 m (`FENCE_TAKE`). Where more than 0.04 m2 of scan per 0.2 m of run lies 0.15 to 0.5 m from it (`OBJECT_AREA`, `OBJECT_BIN`, `OBJECT_REACH`: something leans on the fence), only faces within 6 cm go (`FENCE_SHEET`); this keeps 6915's pallet and teal sheet whole.
7. *Collision and export*: an 8 cm thick `house_mesh.wall_box` per top-line segment. Nodes `fence_N`, double-sided, alpha MASK; the texture page is cropped to its used quarter or half. `report["fences"]`: runs, length_m, two_sided, panels, faces_replaced, collision_triangles, megatexels, pages, seen, seen_sharp, board_period_m.

Result:

| | 7014 | 6915 |
|---|---|---|
| Runs, total length | 5 (south 20.7, north 18.7, west 19.5, north-east 3.0, south-east return 2.4 m), 64.5 m | 4 (east 17.7, north 12.6, south 12.8, gate 2.2 m), 45.3 m |
| Scan faces replaced | 2,481 (97.7 m2) | 8,434 (80.0 m2) |
| Texture | 1.19 megatexels, 1 page | 0.82 megatexels, 1 page |
| Seen / sharp | 0.80 / 0.76 | 0.90 / 0.89 |
| Rejected | three hedge-like stretches (flatness 0.35 to 0.49), two of them 0.9 m deck railings | two garage-side walls under eaves |

7014's south-east return is seen by no camera and borrows the south fence's boards. The yellow boards in 7014's back fence are real: new, unweathered boards and posts, at the same places in the back-yard panoramas (labels 8 and 6). Fences were only a small share of the outdoor collision triangles (7014: 38,683 to 36,202 + 144 for the panels, before the tree models took theirs); the rest is deck, plants and trunks.

Tried and dropped: `rebake.bake_plane` (the wall blend) and the box-face path (`shade_single`) for fence panels (both print shrubs, palm fronds and vines on the boards, and their fill mirrors a patch into a dark kaleidoscope); a closing over the top line before the opening (it filled the fence up to its posts and sky was painted above the boards); the occluder gap measured along the ray (at a flat angle the fence's own posts counted as occluders); a margin around every occluder (a bare crape myrtle blanked a quarter of 7014's west fence); the ground as an occluder with a margin (every fence foot unseen); `rebake._fill` for a hole's base colour (it pulled black from outside the panel's shape); the nearest board per row for every missing texel (one board repeated as a comb); a coarse agreement test for lens flare (it threw out 45% of the best camera's texels on 7014's west fence); blue alone as sky (shaded and flare-tinted boards on 6915's east fence went unseen).

**Also fixed in passing.** `exterior._seen_faces` judged every outdoor scan face with one `panos.shade` call, which asks only the 12 panoramas nearest the middle of the points it is given: 3 of 7014's 15 outdoor cameras were ignored, and which pieces at the lot's edges survived changed whenever the set of faces (and so its middle) changed. It now goes through `rebake._shade_local` (one call per 3 m cell), like the interior since 3.7.

**Pack v43, measured** (v41 in brackets; scores in each house's `quality.json`):

| | 7014 | 6915 |
|---|---|---|
| interior.glb | 15.9 MB (23.8) | 11.4 MB (13.8) |
| Texture pages (2048 px) | 33: 17 interior, 16 outdoor (32) | 28: 16 + 12 (27) |
| collision.glb / outdoor triangles | 771 KB / 28,711 (974 KB / 38,683) | 1.02 MB / 29,637 (1.29 MB / 43,263) |
| Outdoor scan triangles rendered (`outdoor_N` + `trees_N`) | 40,029 + 8,340 (48,209 + 140,509) | 34,099 + 5,646 (62,391 + 8,538) |
| Indoor score, mean abs error | 14.93 (14.88) | 13.20 (13.12) |
| Outdoor score | 33.45 (32.21) | 25.57 (24.95) |
| Outside doors open (`door_check.py`), viewer walk test | 2, passes | 2, passes |

Reading the scores. Indoors only the panoramas that look out of the opened front and back doors moved (7014 pano 30 +0.8; 6915 pano 24 +1.7). Outdoors v43 scores 0.6 to 1.2 worse although it looks better from everywhere a player stands, for the reason section 6 gives: v41 kept the eaves, the porch underside, the canopies and the roof as scan triangles with the photos projected onto them from the very positions the score renders from; v43 replaces them with a fitted roof, planes and models. `compare_panos.py --outdoor` shows where: cameras under an eave or the porch (7014 panos 4 and 9: +3.9 and +3.8; 6915 pano 25 at the front door: +2.9) and cameras under the oak (the model's leaves are not the photographed ones). The same comparison also found a real fault (the flattened porch, +7.7 before it was fixed), so run it after every outdoor change and look at the side-by-side of every camera that moves by more than 3.

### 3.11 Feasibility study (2026-11-15): the drone splat as an offline source for the outdoors

Nothing in the pipeline changed; this is a record of what was measured, so the next step starts from numbers. The user's idea: Matterport's aerial view of 7014 (a drone-built Gaussian splat, `out/raw/splat/<model>/splat/splat.ply`, see `docs/splat-experiment.md` on `lane-a/splat-exp`) looks right from every angle, so use it **offline** to fill what the ground scan lacks, and ship only meshes and textures (no splat at runtime, no Lane C work, no contract change). That is different from the shelved experiment, which rendered the splat in the client.

Scripts (experiments, not part of the package): `worldgen/scripts/splat/` (`splatlib.py`: PLY reader and an orthographic Gaussian rasteriser with alpha and depth, numba; run with `uv run --with numba python scripts/splat/<name>.py` from `worldgen/`; `sview.py`: v43 plus experiment meshes through the free-view renderer; `e1`, `p1` to `p5`: one test each). Outputs: `out/splat_bake/y1cVPR1HsnL/`. A 90 x 90 m top view at 4 cm renders in 6 s.

| Question | Measured on 7014 | Verdict |
|---|---|---|
| Is the splat registered to our frame well enough to bake from? | Splat ground minus our scanned yard: median -4.6 cm, MAD 5.8 cm (894 samples). No registration step. | Yes |
| How fine is it? | Opaque Gaussians, median size: 2.6 cm within 15 m of the house (1267 per m2), 6 cm at 15 to 30 m, 16 cm at 30 to 45 m, 24 cm beyond. | Texture-grade near the house, backdrop-grade beyond 30 m |
| Roof shape | v43's Vexcel-fitted roof is off: the splat's top surface is a median 0.24 m lower, only 44% of cells within 0.3 m. `roof.fit_roof` **unchanged**, fed the splat's roof layer (per 0.1 m cell, the densest 5 cm height bin between 1.9 and 9 m: a roof is a sheet, a canopy is spread out), finds 12 planes; 77% of cells within 10 cm of them. The outline also comes from the splat (211 m2 against 201). | Yes; needs the plane-junction rules retuned (steps where planes meet) |
| Roof texture | Top-down ortho of only the Gaussians within 0.2 m of the fitted planes (cuts the tree over the roof): real shingles, ridges, vents; 72% of texels at alpha over 0.5, the rest (under the oak) to be filled from the same plane. Sun and leaf shadows are baked in. | Yes; answers open question 3 without a billed Vexcel call |
| Walls no ground camera saw | Ortho along each facade's normal, slab of 0.25 m: brick, siding, windows and gable vents are there; alpha over 0.5 on 29 to 94% of a wall (median about 64%); softer than the panoramas; glass is see-through. | Yes as the fallback for unseen texels (instead of the plain colour and the MatterPak fallback), never over a photographed texel |
| Deck and railing | Top view: crisp boards. Side views of a 0.5 m slab at each rail: a clean, straight railing with alpha (posts, balusters, rails). | Yes: deck = flat slab, railing = alpha panels like `fences.py`, textured from these renders |
| Far field (beyond the lot) | 2.5D surface from the top-down depth at 0.25 m, trees taken out (height over ground above 2 m and rough or green), each face textured from the best of 9 ortho renders (down + 8 at 25 degrees, depth-tested). From above and from across the yard: real houses, roofs, street and cars with true parallax. At eye level next to a capture point the photo dome is still sharper. | Yes for the ground disk and the 5 to 40 m ring; keep a dome for the sky and the horizon |
| Trees | Per tree blob, stacks of alpha cards 1 m apart in x and y plus horizontal ones every 1.2 m, each the ortho render of that slab at 28 px per m. From 8 m or more: the real tree, real colours, right place and size. Closer than about 5 m: blocky and soft (the Gaussians are 3 to 8 cm). 46 trees within 48 m made 767 cards and 109 megapixels: must drop to 8 to 12 px per m for trees past the fence. | Yes beyond the fence; for trees the player walks under, use the splat for position, crown size and leaf colour of a model instead |

Tried and changed inside the study:

| Tried | Problem | Now |
|---|---|---|
| Roof layer = lowest strong height bin per cell | Columns of junk under the tree: spikes | The densest bin, then planes |
| Roof texture clipped to v43's roof | v43's planes are 0.2 to 0.7 m off, the slab misses the shingles (53% coverage) | Fit the planes to the splat first |
| Far field as the raw top surface | Every canopy became a solid block with curtain walls down to the ground | Trees detected and dropped to the ground layer, rebuilt as cards |

Not tested: 6915 (its splat, 420 MB, is not downloaded; free, no key), the game client (alpha-card sorting, texture memory), sizes after decimation. The far field as geometry re-opens the 2026-11-14 decision "past the scan's edge: a panorama backdrop"; the user asked for it on 2026-11-15, confirm before building.

### 3.12 Fourth session (2026-11-15): the drone splat fills what the ground scan lacks

The user's ruling after 3.11: the tree cards and the far-field mesh were not better than what v43 has ("p4_v43 looks better than p4_splat_all for most things"), baked shadows on the roof are fine, and the splat is "not a thing to replace what we have now, but using all of its angles to improve the geometry and texture of objects that are incomplete". So the splat is a **gap filler, offline only**: nothing of it ships, a photographed texel is never replaced, and a house without a splat builds exactly as before (`splat.load` returns None; `"splat": false` on the house in `blocks/<block>.json`, or `--no-splat` on `python -m worldgen.exterior`, turns it off).

New: `worldgen/splat.py` (reader, one orthographic rasteriser `Splat.flatten`, compiled with numba, a new dependency), `worldgen/railings.py`, `fit_roof(snap=True)` in `worldgen/roof.py`, `tests/test_splat.py`. The splat file is `out/raw/splat/<model>/splat/splat.ply` (download: `docs/splat-experiment.md` on `lane-a/splat-exp`; free, no key; only 7014's is on disk); its crop within `RADIUS` (60 m) is cached as `out/cache/splat/<model>.npz`.

**0. Level check** (`exterior.splat_level`). The splat and the scan share a frame; the check is the yard: the scan's ground minus the splat's densest layer under it, every 7th ground cell more than 1.5 m from the walls. 7014: 6.2 cm, spread (MAD) 7 cm. The offset is added to the splat's heights; an offset or spread over `SPLAT_LEVEL` (0.2 m) means it is not this house's splat and it is not used.

**1. The roof's shape** (`exterior.splat_roof`, preferred over `aerial_roof`, which stays the fallback):
- *Sheet* (`Splat.roof_sheet`): per 0.1 m cell (`SHEET_CELL`), the 5 cm height bin (`SHEET_BIN`) between floor + 1.9 m and floor + 9 m with the most opacity (3 x 3 x 3 mean, at least `SHEET_MIN` 0.05, Gaussians under alpha `SHEET_ALPHA` 0.2 not counted). A roof is a thin sheet, the tree over it is spread over metres, so the densest bin is the roof even under a canopy.
- *Planes* (`roof.fit_roof`, same search as 3.10 on a 0.1 m grid): planes down to 1 m2 and up to 24 of them (`SPLAT_PLANE`; the sheet is sharp enough for the small cross gables Vexcel's raster blurred away). 7014: 14 planes, 83% of the sheet within 10 cm of them.
- *Outline* (`Splat.roof_outline`): first a fit over everything within `EDGE_REACH` (1.5 m) of the walls (and Vexcel's outline when there is one); the roof is then the walls plus `MIN_OVERHANG` plus every cell whose sheet is within `EDGE_TOL` (0.15 m) of those planes, opened by 0.2 m, notches and tongues under 0.7 m closed or cut (`EDGE_SMOOTH`), the cell staircase straightened by `EDGE_STRAIGHT` (0.22 m). Then the planes are fitted again inside it, never below the ceilings. Eaves, the back porch roof and the garage come out to about a cell instead of Vexcel's 0.3 to 0.5 m.
- *Facets* (`fit_roof(snap=True)`): every cell within `SNAP_REACH` (1.5 m) of a plane's fitted cells takes the plane the sheet agrees with best over 0.5 m (`SNAP_WINDOW`; mean absolute difference under `SNAP_TOL` 0.15 m); cells with no sheet keep the nearest fitted cell's plane.
- *Junctions* (`Roof.kind`, used by `Roof.surface`): within `REACH` of a neighbouring plane the two meet exactly on their line: the lower of the two where each lies below the other on its own fitted cells nearby (a ridge or hip), the higher where each lies above (a valley), and a step where neither (a porch roof under the main roof, a gable end over a lower wing). Decided per place over `PAIR_NEAR` (1 m), not per pair of planes: one long slope meets another in a hip at one end and a valley at the other. This took the height jumps along 7014's eaves from 16 to 5 (the 5 are real steps).

**2. The roof's shingles** (`Splat.roof_colour`, `bake_patch` with `meta["whole"]`): the Gaussians within `ROOF_BAND` (0.2 m) of the fitted roof, seen from straight above at `ROOF_RES`; the tree over the roof is outside the slab. Covered = alpha over `ROOF_SEEN` (0.3: the slab holds nothing but roof). The splat takes every texel of the top (a ground camera sees a roof at a grazing angle and through leaves, and two sources on one slope show as a seam); what the drone did not see is filled from the shingles around it. No exposure match to the ground photos. Sun and leaf shadows are baked in (accepted by the user). Eaves' undersides and the fascia band are unchanged (3.10 step 6).

**3. Walls no camera saw** (`splat.WallFill`, through `rebake.bake_plane(extra=)`): per facade plane, the Gaussians within `WALL_BAND` (0.25 m) of it flattened onto it; a texel is covered at alpha over `SEEN` (0.6) after dropping specks. Only texels no ground camera saw take it, before the MatterPak texture fallback and the plain colour. A wall is filled only when the splat covers at least `WALL_MIN` (0.7) of what the cameras missed on it. Exposure: one gain per wall from the texels both sources have (500 or more), limited to `SPLAT_GAIN` (0.8 to 1.25); a wall without such texels takes the median gain of the walls before it. The report lists every wall (`splat.facade_walls`: size, direction, share missed, share covered, used).

**4. Deck railings** (`railings.replace_railings`, in the replacer hook between fences and plants; nodes `fence_r<N>`, alpha MASK, double sided): straight thin lines of Gaussians 0.45 to 1.4 m above the ground (`RAIL_BAND`; the ground is the deck's top on a deck), at least 1 m long, thinner than 4.5 cm across (`RAIL_THIN`), with nothing 1.45 to 2.05 m above the ground over them (`RAIL_TALL`: a fence, a shrub; a porch roof is higher and does not count), whose top is 0.7 to 1.2 m up (`RAIL_HEIGHT`). Each becomes one panel: the Gaussians within 12 cm of its plane flattened onto it, alpha cut at 0.5, so the gaps between balusters are holes. A panel is kept only if it covers 12 to 80% of its area (`RAIL_COVER`) and has a top rail along 85% of its length (`RAIL_TOP_FULL`). Its brightness is matched to the ground photos' with one gain (`railings.paint`: median brightness over the inside of the solid parts, counting only photo samples within `RAIL_HUE` 0.1 of the splat's own hue, limited to `RAIL_PAINT` 0.6 to 2.0; 7014: 2.0). The scan faces within 15 cm of it are dropped (the deck's own boards stay); a thin box collides instead. 7014: both deck railings (3.5 m and 3.4 m, 1.06 m high); three other candidates rejected (no top rail).

Tried and changed:

| Tried | Problem | Now |
|---|---|---|
| Roof sheet needs a summed opacity of 0.15 | Holes under the oak and in deep shade; the back porch roof (seen through open rafters) had 60% of its cells and no plane, so the outline dropped it and the rafters showed from above | 0.05; the plane search rejects what is not roof |
| Unfitted cells take the nearest fitted cell's plane (as 3.10) | With a sharp sheet the facets ended half-way between fitted cells: steps of 0.2 to 0.7 m along hips and eaves | Snap to the best-agreeing plane, then join neighbours on their intersection line |
| How two planes meet decided once per pair | A long south slope is a hip against one plane's end and a valley elsewhere: the medians cancelled and a real junction became a step | Per place, from the fitted cells within 1 m |
| Smallest plane 2 m2 (as 3.10) | 7014's two small cross gables on the west side were split between their neighbours | 1 m2 on the splat path |
| Outline simplified by 0.08 m | The 0.1 m cell staircase survived: wavy fascia | 0.22 m, after closing and opening by 0.35 m |
| Splat shingles scaled to the ground photos' roof texels | Those few texels are leaves and sky as often as shingles: the gain hit its limit and the roof came out pale pink | No gain on the roof |
| Wall gain limited to 0.7 to 1.5 | Every wall hit 1.5 (the panoramas are exposed brighter), and the sunlit south brick wall, which has no photographed texels of its own, washed out to white | 0.8 to 1.25 |
| A wall filled when the splat covers half of what the cameras missed | 7014's south-west wall under the neighbour's tree (58% covered, in deep shade): windows as black blobs in grey smears, worse than the plain colour | 70%; that wall stays plain |
| Railings: any thin line 0.45 to 1.25 m up | 7 false rails on 7014 (shrubs, young trunks, a fence stub) | The top must end at 0.7 to 1.2 m, and the panel needs a top rail |
| Railing colour: the walls' gain (1.25), then a gain per channel from the photos | Maroon against the photos' bright red; per channel the lawn seen between the balusters pulled green up and the railing came out orange | One brightness gain from photo samples of the railing's own hue |
| Tree cards and a far-field mesh from the splat (3.11) | Not better than v43's models and photo backdrop (the user) | Not built |

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
18. **Build time** (old 14) is still 5 to 6 minutes per house. The box steps add a few seconds; the rest is baking. With the outdoors (section 3.9), about 10-12 minutes.
19. **The score does not see any of the second session's improvements** (section 6). There is no number yet that falls when a fridge stops being shattered.
20. **Synthesised surfaces are guesses.** Texture synthesis repeats grain, not patterns: a filled floor patch has no tile joints, a cabinet side has no door edge. Anything seen under 15% (box faces) or 5% (walls) is colour and grain borrowed from a neighbour, not a photo of the thing.

### Outdoors (sections 3.9 and 3.10), ranked the same way

Re-ranked after the third session (pack v43). Gone from the list: the size limit (7014 was 23.8 of 25 MB, now 15.9), torn tree canopies, wavy fences with blurred holes, the crumpled scanned roof of 6915, the guessed hip roof of 7014, facades rising above the roof painted with sky, the crumpled entry alcove ceiling.

1. **Every tree is the same live oak.** The young trees in 7014's back yard are miniature oaks with tinted leaves; the neighbour's tree beyond the south fence is a guess at where its trunk stands. Leaves of the old scanned canopies are still painted on the backdrop's sky near where the trees were. A sapling beside 7014's porch post, twigs at the grey tree's foot and shrubs the cameras saw well (the sago palm, 6915's rose bush) stay raw scan.
2. **Backdrop parallax.** The street, parked cars and the neighbours' houses are photos on a ground disk and a 45 m dome: right near the capture points, sliding as you walk away from them. Cars on the street are flattened onto the road.
3. **Roof edges are placed to about 0.3 to 0.5 m** on a house without a splat (6915; with one the outline comes from the splat to about a cell, 3.12). The surface model is 0.13 m per pixel and soft at edges, and Vexcel's outline is a handful of vertices, so an eave can be wider than the real one: standing under it you see more roof and less sky than in the photo (the outdoor score at those cameras is 2 to 4 points worse than v41, which kept the scanned eaves). Eaves no camera saw are one plain trim colour. Roof vents and ridge caps are not modelled (6915's metal flue survives as a scan piece).
4. **7014's roof** has the splat's shingles since v44 (3.12): sun and leaf shadows are baked in, a tenth of it (under the oak) is filled from the shingles around it, and it is softer than a ground photo (the Gaussians are about 3 cm). **Where one roof stands above another** (7014: the main roof's south gable end over the lower east wing, 1.3 m) the step is a cliff in the roof's height field, coloured with stretched shingles: it should be a wall with the gable's siding. 6915's roof is still Vexcel's (no splat on disk).
5. **Built porch roofs stay raw scan.** 7014's back porch has open rafters; they are kept as scanned (crumpled posts, rafter ends, a ragged rim where the scan ends) because a flat plane there was worse. The deck's boards, posts, the AC unit and the pergola are raw scan too. 7014's deck railings are panels from the splat since v44 (3.12): straight, with real gaps, but flat (5 cm posts have no depth) and soft close up.
6. **Walls no camera saw.** On 7014 since v44 five walls (87 m2: the north and south sides, gable ends) have the splat's brick, siding and windows, softer than the photographed walls and with glass filled in by blur; 13 smaller or badly covered walls are unchanged, among them the south-west wall under the neighbour's tree, which stays one plain colour (the splat covers 58% of it, in deep shade). On 6915 (no splat) all such walls are plain. The ground beside them is a blur.
7. **Fence leftovers**: lens flare from the photos on 6915's east fence; the filled stretches behind 7014's trees and palms are plausible but a little greyer or washed; under 1 m the boards are blocky (96 texels/m); the board width for the fill is the 14 cm default on both houses; things leaning on a fence (6915's pallet) are also printed on the panel behind them.
8. **Outdoor collision** is still about 29k raw scan triangles per house (deck, railings, plants, 6915's garage walls); fences are flat boxes now and tree trunks are prisms.
9. **6915's garage was not scanned inside.** Its walls are scan pieces seen from outside, its roof comes from the aerial data, and under that roof there is a guessed sloping underside and no floor.
10. **Windows** stay photos on both sides (not carved): from inside you see the photo of the yard, not the yard geometry.
11. **Opened doors** show the yard where the indoor photos show a closed door; this costs about 0.8 on the indoor score of 7014 (v10 14.14, v43 14.93) and is intended.
12. **Unscanned side yards** (7014's north side) are outside the lot and fenced off; looking toward them shows the backdrop disk, mostly fill.
13. **Texture memory.** 33 pages of 2048 px on 7014 (17 interior, 16 outdoor), 28 on 6915: roughly 500 to 700 MB of GPU memory with mipmaps. The file size is no longer the constraint; this is.

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

### Outdoors

Done in the third session (3.10), kept here so the list stays whole: the size limit (was 17: cutting the scanned canopies took 7014 from 23.8 to 15.9 MB without a contract change), tree models (was 18), the roof's shape from the Vexcel surface model (was 20), fences as panels.

17. **Deck, posts and the AC unit as boxes** (outdoor problems 5 and 8; railings are done where a splat exists, 3.12). The interior's relief boxes, outdoors: a deck is a slab at the height the scan gives, a railing a row of posts and two rails, a porch post a box from deck to beam, each face from one photo. This removes most of the remaining raw scan a player touches and most of the 29k outdoor collision triangles. For open-raftered porch roofs: rafters as boxes along the lines the scan shows, boards as one plane above them.
18. **Eave widths from the scan** (outdoor problem 3; only for houses without a splat now). Where the scan has a soffit, its outer edge is the real roof edge to a few cm: move the roof region's edge there per outline segment (as `facade_ring` moves walls onto the scanned wall face), and keep the aerial outline only where the scan has none.
19. **Tree variety** (outdoor problem 1). More than one species in `out/vegetation/v<N>` (a crape myrtle, a small ornamental, a palm), chosen by crown shape and leaf colour; a bare-branch variant for trees the photos show leafless. Repaint the backdrop's sky where old canopies were (sky-coloured fill inside the removed crowns' silhouettes).
20. **A roof texture for a house without a splat** (6915; 7014's is done, 3.12) (outdoor problem 4): Vexcel has other capture dates (the block session's `elements_detect` metadata names collection us-tx-houston-2026 for the cached ortho; `ortho/extract` takes `order-by` and a collection). One billed call per try; ask the user first. Or tile a shingle texture (CC0) tinted to the photographed colour.
21. **Backdrop** (outdoor problem 2): pick the dome texels from the cameras on the player's side (one dome per yard, cross-faded by the client), or put the block geometry (lane-a/block-combined) around the lot.
22. **Carve windows on both sides** (outdoor problem 10): an exterior wall texel that no camera sees a surface at, and that sees the yard behind, is glass; cut it from the room's wall and the facade, keep collision solid.
23. **Exposure compensation outdoors** together with item 3 (sun and shade change between outdoor sweeps).
25. **More from the splat** (3.12), in order of value: download 6915's splat and build it the same way (free, no key; check the level report first); gable walls at roof steps (find label boundaries of `Roof.kind` 0 with a height difference over 0.3 m, build a vertical plane, texture it with `Splat.plane_image`); porch posts as boxes with the splat's side views; a wall filled only partly (`WALL_MIN`) could take the splat on its well-covered part and the plain colour, cross-faded, on the rest; an exposure match per wall that knows sun from shade.
24. **Texture memory** (outdoor problem 13): xatlas charts for the remaining outdoor scan pieces, a lower `DOME_RES` / `DISK_RES` (the backdrop is 13.5 of about 27 outdoor megatexels), or a separate `exterior.glb` the client can drop indoors (a manifest contract change, all four lanes).

---

## 6. Measuring quality

**Rule one (learned in the second session): never judge a build from the panorama positions alone.** Every texel was coloured by projecting a photo from a panorama's position, so from that position the picture is right whatever the geometry is: a shattered fridge and a flat one render the same. A player stands somewhere else. Look from room corners (`room_views.py`), from above (the QA viewer, `worldgen/viewer/`), and at the panorama comparison last.

`worldgen/scripts/preview/` (scratch-quality scripts; run from `worldgen/` with `uv run python scripts/preview/<name>.py`). They read the uncompressed glb in `out/cache/glb/` (`<model>_interior_pano.glb` for pack v1, `<model>_interior_pano_v<N>.glb` for other versions), because trimesh cannot read meshopt files.

| Script | Use |
|---|---|
| `room_views.py <glb> <rooms.json> <out dir> [room ids]` | One sheet per room: the view from each of its corners (0.6 m in, eye height 1.6 m) toward the far corner. This is the main check. `TINT=1` paints what is still raw scan (`fixtures_N` magenta, `ceiling_scan_N` cyan), so a problem can be traced to "scan left over" or "bad box". `NO_CEILING=1` hides ceilings. |
| `render_compare.py` | Renders from each panorama's pose and puts the render next to the real cube face. `--score <glb> <model json> <skybox dir> [labels]` prints `mean_abs_err`, `p95_err` (95th-percentile pixel error), `bad_fraction` (pixels off by more than 60) and `hole_fraction` over the 4 side faces of every indoor panorama (192 px), or of the listed labels only. Purple pixels are holes. |
| `freeview.py` | Renders from any eye position and look-at point (Matterport plan coordinates). Since the third session rays pass up to 40 alpha-cut layers (leaf cards) and textures repeat (tiled bark); the indoor score still uses 6 layers, the outdoor score (`OUTDOOR=1`) 40. `NO_CEILING=1` plus a camera high above gives a top-down plan (walls far from the centre show their faces in perspective: that is not a slab). |
| `plot_plan.py` | Plots the room vectors (walls, invisible edges, doors, windows) from the cached model JSON. |
| `calib_panos.py` | Re-derives the panorama orientation convention (section 2). |
| `bake_rooms.py <block> <listing> <out.glb> r4 r10` | Bakes only those rooms into an uncompressed glb, in about a minute. The loop for trying a change before a full build. |
| `build_house --debug-dir <dir>` | `relief_room<i>_wall<j>.png` (relief map with fitted rectangles in red) and `boxes.json` (every box: centre, width, depth, z range). |
| `compare_panos.py <a.glb> <b.glb> <model json> <skybox dir> [--outdoor]` | Mean abs error per panorama for two builds, largest change first; its mean reproduces `--score`. When the score moves, this says where: v10 against v41 showed every panorama within 0.2 except the ones looking through the opened front and back doors. A change in a room the change should not touch means it leaks there. About a minute per house. |
| `door_check.py <pack version dir> <listing> [--all]` | Can a player walk through each door: nine rays across every outside door (`--all`: every door) in collision.glb, at 0.5, 1.0, 1.5 m and 0.2 m either side of the centre. Prints OPEN or the collision nodes in the way (`walls`, `fixtures` for a door-leaf slab, `outdoor`, `bounds`). Expected with the outdoors: front and back doors OPEN. Geometry only: no steps or slopes. |
| `plan_outline.py <block> <listing> <out.jpg> [--below 2.2] [--res 0.04]` | The raw scan from above (triangles below `--below` m, so roofs and canopies do not hide the ground) with the outdoor outlines on it: house footprint red, facade ring blue, lot green, panoramas green indoors and yellow outdoors, 5 m ticks in plan coordinates. The first thing to look at for any lot, footprint or facade problem (it showed the missing driveway and the closet notch). |
| `viewer_walk.mjs <viewer url> [out prefix]` (Node) | Headless walk test of the QA viewer: the pack loads, each "yard: outside ... door" spawn and the first room spawn land on the ground, holding W moves the capsule, screenshots with an out prefix; exit 1 if loading fails, the page throws or a capsule falls. Needs `playwright-core@1.52.0` installed in any scratch folder (`PW_CORE=<it>/node_modules/playwright-core`); uses Playwright's cached Chromium. A private browser, because the shared Playwright MCP browser once took another session's key presses. |

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
| 7014 with the outdoors, v40 (3.9; first-session interior + opened front and back doors) | 14.81 indoors (the opened doors show the yard where the photos show closed doors); 32.3 outdoors (`OUTDOOR=1`) | | | 0.04%; 0% |
| 6915 with the outdoors, v40 (3.9) | 15.03 indoors; 24.95 outdoors | | | 0%; 0% |
| 7014 combined, v41 (second-session interior f1b2737 + outdoors) | 14.88 indoors (v10 14.14; every panorama that does not see the opened doors within 0.2 of v10); 32.21 outdoors | 57.0 | 4.47% | 0.04%; 0% |
| 6915 combined, v41 | 13.12 indoors (v10 12.85; same pattern); 24.95 outdoors | 52.7 | 3.81% | 0%; 0% |
| 7014 third session, v43 (3.10: aerial roof, tree models, fence panels) | 14.93 indoors; 33.45 outdoors (v41 32.21; see 3.10 for why worse is not worse) | 57.3 | 4.55% | 0.04%; 0% |
| 6915 third session, v43 | 13.20 indoors; 25.57 outdoors (v41 24.95) | 53.3 | 3.90% | 0%; 0.01% |

How to read them:

- **The full score is biased**: the bake used the same panoramas it is scored against. It catches regressions (misaligned geometry, wrong carving, holes, a broken convention, a stand-in solid hiding a room: that one cost 1.8 points) and does not reward improvements.
- **The hold-out score is honest but narrow.** Run `build_house --holdout <labels> --version 99` and score with the same labels: the bake never saw those photos. It only works in rooms with several panoramas (living rooms, big bedrooms); kitchens and bathrooms usually have one, and those are the rooms the second session changed. So it did not move either.
- **Removing something on purpose costs points.** The real photos show the fans; a build without them scores a little worse (7014: 14.07 to about 14.15).
- **6915 improved by about 1.8 points and 7014 by none** although both changed in the same way. Not investigated; the likely reason is that 6915's kitchen and bathrooms are seen by more than one panorama (three stand in its kitchen, one in 7014's), and the score sees geometry only where a second camera looks at the same thing from another place.
- So: the score is a regression alarm. Improvement is judged on the room-view sheets, side by side, same rooms, before and after. Section 5 item 1 is the missing number.
- Outdoors: `OUTDOOR=1` scores the outdoor panoramas instead (same bias). Look at `render_compare.py` captures 5, 35 and 32 for 7014 and 27, 20 for 6915, plus free views from places no camera stood (the street, the far corners of the back yard), because that is where the generated roof, the facade fallback and the backdrop show their limits.

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
    | `exterior.roof.source` | `aerial` | Missing or `generated`: no aerial data was found. Run `worldgen.costar` first (the listing needs `world` in `out/listings/index.json`) and check that rasters covering the house are in `out/block_compare/cache/vexcel/` (3.10) |
    | `exterior.roof.planes`, `pitch_deg` | 4 and 11 planes, 21 to 22 degrees | 1 plane or a pitch near 0 on a pitched roof: the region is off the building (check `vexcel_outline`), or trees cover the roof |
    | `exterior.roof.ground_spread_m` | 0.03 | Over 0.1: the surface model and the scan disagree about the ground; the heading or position in `Listing.world` is wrong |
    | `exterior.roof.lifted_by_ceilings`, `max_lift_m` | 0.03 to 0.04, 0.2 to 0.4 m | Over 0.1 or over 0.6 m: the roof sits below the scanned ceilings: registration, or a room's listed height is wrong |
    | `exterior.roof.aerial_photo.used` | true on 6915, false on 7014 (winter shadow) | false is fine: the roof takes the photographed shingle colour |
    | `exterior.roof.underside_measured` | 0.4 | 0: the scan has no eaves or porch ceilings; the underside is a guess 0.18 m under the roof |

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
    | Outdoors: a blob in mid air or on the ground near a scan position, in sky or leaf colours | A scan artefact (the operator, the tripod) that no photo shows | `"remove_outdoor"` in the override file (3.10). Check it against the photos first (`render_compare.py ... <out.jpg> <label>`): 6915's grill looks like one and is real |
    | Outdoors: a dark slot or patch in an outside wall, or a wall that steps in where the house is straight | A notch in the floor plan that is masonry (a fireplace, a chase) and was taken for yard | `"inside"` polygon in the override file (3.10); `plan_outline.py` shows the footprint |
    | Outdoors: a slab of roof where the photo shows sky, seen from under an eave | The roof's edge is placed to 0.3 to 0.5 m (section 4, outdoors 3) | Section 5 item 18 |
    | Outdoors: a porch ceiling that is a mosaic | A built underside (rafters) was flattened | `underside_kept_as_scan_m2` in the report should cover it; `EAVE_FLAT`, `EAVE_FLAT_TOL` |

6. **Walk it**: `publish --local` (after copying or building into the version the client loads), open the web client with `VITE_START_ROOM=<listing>.r<N>`. Check that the player fits through the doors and cannot walk through counters.
7. **Score** with `render_compare.py --score` and keep the numbers with the house (they are the baseline for the next change). Run tests, ruff and `contracts/validate.py out/packs/<block>`.

---

## 8. Other open items for Lane A (not house quality, but blocking others)

Done since this list was first written: `listings/index.json` (`worldgen/costar.py`, with `Listing.world` poses and door points), the furniture library (`furniture/v<N>/`), `audit.json` per house (`worldgen/audit.py`), the block (`worldgen/build_block.py`), and the hero-house merge. Still open:

- `publish.py` bucket upload (only the static-site publish exists; packs are served from `apps/web/public/packs`).
- The seam where the block's ground meets each scanned lot at the front door (`costar.py` sets the house's y from the block ground; the yard heightfield is 0.1 to 0.4 m under the floor): look at it in the game client when v4 goes live.

## 9. Gotchas

- **`worldgen/out` is shared by every worktree and several agents build into it at once** (on 2026-11-14 a second agent started building 6915 into pack v2 while this work was using v2 for experiments). Experiment in a version number of your own (`--version <N>`); the cached glb is then `<model>_interior_pano_v<N>.glb`, so two versions cannot overwrite each other's file. Never write into `v1` (what other lanes load) or a version someone else is building without the user's say-so. Delete your scratch versions (`out/packs/<block>/v<N>`) when done.
- **Never stop builds with a pattern** (`pkill -f build_house` would also kill another agent's build). Stop your own by process ID.
- **`python -m worldgen.exterior` without `--dest` writes `out/cache/glb/<model>_exterior.glb`, and the block builder reads that file** (`block_combined --hero-exterior auto` on lane-a/block-combined puts it on the block as the hero house). An experiment written there shows up in the block. Always pass `--dest out/cache/glb/<your folder>/<name>.glb`.
- **Reused names have broken builds four times.** `footprint` in `build_house.build` (3.9); in the third session `bake`, a helper function defined inside `add_exterior` that replaced its own `bake` argument; and `parts`, twice: the new bake option was overwritten by the room list in `exterior.main` (every bake empty) and by a list of meshes inside `add_exterior` (complete bakes exported no scan pieces; pieces-only bakes were fine, so the forks testing with `--parts pieces` saw nothing wrong). Before adding a name to `build`, `main` or `add_exterior`, search the whole function for it.
- **A partial bake and a complete bake can differ.** After a complete outdoor bake, read `outdoor_faces` and `tree_faces` in the report: 0 means the pieces were skipped, not that the lot is empty.
- **The splat path needs numba** (in `pyproject.toml` since the fourth session; `uv sync`). The first call compiles the rasteriser (a few seconds, cached). `splat.load` reads a 420 MB file once and caches the 60 m crop in `out/cache/splat/`; delete that file after replacing a splat.
- In `build_house.py` the splat module is imported as `splat`, not `sp`: `apply_splits` has a loop variable `sp` (ruff F402 caught it; the fifth near-miss of the reused-names kind).
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

**Version numbers** (so experiments never collide): interior-only experiments 11-39, house builds 44-69 (40, 41 and 43 are taken; 43 is the current candidate), block work 70-89, scratch 90-99, and the low numbers (1 to 4 so far) are the packs that are or were meant to go live, made by Lane A with `build_block.py --houses-from <house version>`. Delete scratch versions when a test is done. As of 2026-11-15: v3 live, v4 = v3 + the v43 houses, built, not live.

**Promotion rule: build on the live version, never drop what it already does well.** The goal is that versions build on each other: a candidate should keep everything the live version gets right and improve on it, so the metrics are mostly better and none is clearly worse. It is not a gate where every number must improve; small noise on one metric is fine. What blocks a promotion is a real regression: a house or room that looks visibly worse, a lost improvement from an earlier version, a new hole or broken doorway, or a score that gets clearly worse. If a trade-off is worth it anyway, the human accepts it in writing in `--compared-to-live`.

1. Build the candidate into its own version: `build_house --version N` for every house in the block, then `audit --block <block> --version N`.
2. Compare with the live version, per house, and write the numbers down (looking for regressions, not demanding a win on every line):
   - `render_compare.py --score` (lower is better; also hole_fraction). The scorer reads the uncompressed copy of the build: `out/cache/glb/<model>_interior_pano_vN.glb` for a build of version N > 1 (`..._interior_pano.glb` for v1), so worktrees that build different versions no longer overwrite each other's file (before lane-a/outdoor every build wrote the same file). Score right after your own build of that house. Record the result in the candidate folder (`packs/<block>/vN/houses/<id>/quality.json`) so the number belongs to the version.
   - Walk both versions in the viewer at the same spots (living room, kitchen, each bedroom door, the front door) and compare side by side.
   - The audit findings and the doorway check (Lane C's door tool, 0.25 m capsule) should not get worse.
3. **Two improvements at once** (for example interior quality and the scanned lot): merge both branches, build one combined candidate, and check it against the live version AND against each improvement's own candidate. The thing to catch is one change overwriting or undoing the other: if the combined build has lost something that either one had alone, the changes interact; fix that before pinning. Never pin one improvement's candidate over another's without this check, or the second merge silently drops the first one's gain.
4. `versions pin` (it checks completeness and runs contracts/validate.py), commit `worldgen/blocks/<block>.json`, `publish`, and post a HANDOFF line so lane D sets `PACK_VERSION` on Render and lane C updates `VITE_PACK_BASE_URL`.
