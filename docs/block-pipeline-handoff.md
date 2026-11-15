# Block pipeline handoff (the outdoor neighbourhood, `hou_navidad_01`)

**This is a living document.** It is the single place an agent learns how the outdoor block is built, what
was tried, what works, what is still wrong, and how a new build reaches the game. If you work on the block:

1. Read this whole file before writing code (after AGENTS.md).
2. **Keep it live.** Before you stop (end of a round, end of a session, or before handing back to the
   human), update the sections your work touched: *Current state*, *Known problems and backlog*,
   *What was tried*, and add a line to the *Changelog* at the bottom with the date, what changed, the commit,
   and what you measured. Commit the doc with your code (`lane-a: ...`). A change that is not written here
   is lost to the next agent.
3. Record failures as carefully as successes. "Tried X, rejected because Y" saves the next agent hours.

Scope: Lane A (`worldgen/`), AGENTS.md sections 3, 7 (step 3), 10 (licences) and 14. The house interiors and
their scanned lots have their own living doc: `docs/house-pipeline-handoff.md`.

Last updated: 2026-11-15 00:30 (after packing the combined block into pack v2, then v3/v4).

---

## 1. Current state (read this first)

| What | State |
|---|---|
| Builder | `worldgen/block_combined.py` (procedural near zone + Vexcel 3D Cities far zone + ortho-fitted hard surfaces). On main since merge `f64c451` (branch `lane-a/block-combined`, last block commit `b9aceae`). |
| In the game | **Yes.** Live pack v3 (pinned by lane B for furniture v3) carries the block unchanged from v2 (`block/render.glb` 15.29 MB, `block/collision.glb` 14,098 triangles, 8 sidewalk spawns). Candidate pack v4 (new house lots, same block) is built but not live (waiting on lane C; HANDOFF lane-a 2026-11-14 23:55). Check with `uv run python -m worldgen.versions status hou_navidad_01`. |
| Verified | Real game client (`apps/web`, `VITE_SOLO=1`) loads the block, the player spawns on the sidewalk in front of 7014, 60 fps on an M-series laptop. Not verified: walking from the block through 7014's front door; frame rate on weaker laptops. |
| Quality bar | Set by the Lane A human: **"how real and not weird it looks"**, street level (eye 1.6 m, walking the sidewalk) first, drone/aerial second. Every round is judged by looking at the shots, not by a metric alone. |
| Budgets | `render.glb` under 25 MB (whole published site under ~95 MB, AGENTS.md section 5), collision under 20k triangles, at most a handful of billed Vexcel calls per round. |
| Latest numbers | render 15.3 MB, ~469k triangles written / ~1.15M drawn (168 tree instances of 9 meshes), collision 14,098; build ~60 s from cache; sidewalk gaps 0, dangling walk ends 1 of 96; paving IoU median 0.374 (regression check only, see section 8). |

---

## 2. Where everything is

### Code (all in git, `worldgen/`)

| File | Role |
|---|---|
| `block_combined.py` | **Entry point.** `uv run python -m worldgen.block_combined`. Near-zone polygon, wall colours from 3D Cities, ground classes, far-zone mesh cut, trees, cars removal, hero lots and the optional scanned-exterior swap, sun estimate, collision, glb write. Writes `out/block_compare/combined/`. |
| `block_hardscape.py` | Roads re-measured from the ortho, curbs (clean offsets, 1 m cells), gutters, sidewalks (constant setback/width per street side, bridged gaps, curb ramps), driveways (fitted rectangles + aprons), walks (centreline bands, joined, both ends connected), stoops, paving IoU and continuity checks. |
| `block_procedural.py` | The procedural prototype's builder (kept; its functions are reused). |
| `block_photo.py`, `block_photo_decode.mjs` | The photo-surface prototype (3D Cities tiles: Draco + KTX2 decode through `three`). Reused for the far zone. |
| `block_data.py` | Fetch and cache: Vexcel REST/MCP, OSM Overpass, ledgers. Block frame helpers. |
| `block_raster.py` | GeoTIFF reader (Vexcel DSM is big-endian tiled float32; PIL misreads it), ortho sampling (Mercator grid), ground from DSM. |
| `block_buildings.py` | Footprints to roofs: rectangle cover, hip/gable per rectangle, pitch/eave fitted to the DSM, storey prior. |
| `block_facade.py` | Walls, window/door/garage atlas, `place_openings` (garage door where the driveway meets the house, `garage_at`, `door_at`). |
| `block_ground.py` | Gridded terrain meshes with per-vertex colour, curbs/curb cuts. |
| `block_veg.py` | Crown segmentation from DSM minus ground (watershed). |
| `block_glb.py` | glTF writing (shared meshes for instancing, node matrices, extras) and `compress()` (gltf-transform meshopt + WebP; **use `--quantize-texcoord 16`**, see 7.4). |
| `build_block.py` | **Packs a build into a pack version**: `uv run python -m worldgen.build_block --block hou_navidad_01 --version N [--houses-from M] [--furniture F] [--reuse] [--force]`. Copies `houses/` from another version, copies `block/{render,collision}.glb`, writes manifest (block paths, hero first, 8 sidewalk spawns, furniture catalog). Refuses the live version. |
| `scripts/block_trees/{tree.py, atlas.py}` | Generates the 3 live-oak variants (CC0 bark/leaf textures from Poly Haven). Output in `out/block_compare/cache/trees/` (+ `LICENSE.json`). |
| `scripts/block_compare/{compare.html, shoot.mjs, cameras.json}` | **QA tooling.** Side-by-side viewer with one shared camera, and the shot renderer. See section 8. |
| `tests/test_block_combined.py`, `tests/test_block_procedural.py` | Unit tests (zone, vectorising, Chaikin, solar elevation, shared-mesh glb, ...). |
| `costar.py` (`live_block_ground`) | Sets `Listing.world.position.y` to the live block's terrain height at each front door. Re-run `worldgen.costar` after a new block goes live. |

### Data (gitignored, `worldgen/out/`, per machine; worktrees symlink to the main checkout's)

| Path | What |
|---|---|
| `out/block_compare/cache/vexcel/` | Every Vexcel response (ortho, DSM, footprints, parcels, 3D Cities tiles, car detections), the API spec (`docs/vexcel_v2_api-docs.json`) and the call ledgers `ledger_{procedural,photo,combined}.jsonl`. **Check here before any Vexcel call.** |
| `out/block_compare/cache/osm/` | Overpass response (road centrelines and names only: OSM has no buildings or fences here). |
| `out/block_compare/cache/trees/` | Tree meshes and textures + licences. |
| `out/block_compare/{photo,procedural,combined}/` | Each approach's `render.glb`, `collision.glb`, `shots/`, `notes.md` (round-by-round detail, the long version of this doc), `build_summary.json`. `combined/` also has `hardscape.json` (all paving polygons, block frame `(x, z)`), `near_zone.json`. |
| `out/packs/hou_navidad_01/v<N>/block/` | The block inside a pack version. |
| `worldgen/blocks/hou_navidad_01.json` (in git) | Block config: centre, houses, OSM `streets`, `live_pack_version`, `pack_history`. |

A fresh machine has none of `out/`: the Vexcel cache must be copied from the main checkout (`/Users/pfu/hackprincetonf26/worldgen/out/block_compare/cache/`) or re-fetched (about 25 billed calls in total; see section 5).

### Branches

`lane-a/block` (procedural prototype, `9e6c599`), `lane-a/block-photo` (photo prototype, `521a811`) and
`lane-a/block-combined` (`b9aceae`) are all merged into main. Their worktrees (`/Users/pfu/hp-block`,
`/Users/pfu/hp-block-photo`, `/Users/pfu/hp-block-combined`) can be removed once nobody needs them (check
`git status --ignored` for data first; data lives in the main checkout's `out/` anyway).

---

## 3. How to run it

```bash
cd worldgen && uv sync && npm i                         # once (unset VIRTUAL_ENV if uv warns)

# build the block (all inputs cached; ~60 s)
uv run python -m worldgen.block_combined                # -> out/block_compare/combined/

# look at it
node scripts/block_compare/shoot.mjs combined           # 13 shots -> out/block_compare/combined/shots/
node scripts/block_compare/shoot.mjs --serve            # http://localhost:8765/compare.html?a=procedural,combined
                                                         # also ?a=photo,procedural,combined or ?a=combined

# checks
uv run pytest && uv run ruff check .

# put it in the game (only after the human approves the look)
uv run python -m worldgen.build_block --block hou_navidad_01 --version N --houses-from <live> --furniture <live's> --reuse
uv run python -m worldgen.audit --block hou_navidad_01 --version N
uv run python ../contracts/validate.py out/packs/hou_navidad_01/vN
uv run python -m worldgen.versions pin hou_navidad_01 --version N --reason "..." --compared-to-live "..."
uv run python -m worldgen.costar --in ../costar-hackathon-kit --out out/listings/index.json   # house heights
uv run python -m worldgen.publish hou_navidad_01         # writes apps/web/public/packs (commit it)
# then a HANDOFF line: the new version number, for lane D's Render dashboard
```

`--houses-from` must be the live version (or the house candidate the human chose) so a block change never
silently swaps houses; `--furniture` must match the live pack's furniture version (read its manifest).
`--reuse` packs the last `combined/` build; without it build_block rebuilds first.

Testing a candidate in the real client before pinning: serve `worldgen/out` with CORS on a port and run
`apps/web` with `VITE_PACK_BASE_URL=http://localhost:<port>/packs/hou_navidad_01/vN VITE_SOLO=1
VITE_HERO_LISTING=hou_236595179` (optionally `VITE_START_ROOM=hou_236595179.r3` to start inside 7014).
`npx -y pnpm@12.18.0 dev` (the repo pins pnpm 12.18.0). Kill stale dev servers first (`lsof -iTCP:<port>`): a
leftover server on the same port silently serves the old pack.

---

## 4. Frames, datums and placement

- **Block frame:** origin at the ground at the block centre 29.70113, -95.670841; +X east, +Y up, +Z south,
  metres. Every block file and `hardscape.json` (2D `(x, z)`) uses it.
- **Height datum:** the DSM is EGM2008 (ground at the origin about 26.75 m); the 3D Cities mesh is
  ellipsoidal (ground at the origin -1.70 m), a ~28.4 m geoid offset. Both are anchored so the street at the
  origin is y = 0. Lawns sit 0.1-0.4 m above that.
- **Ortho:** Vexcel `ortho/extract` returns a plain JPEG on a Web-Mercator grid with no georeferencing; the
  pixel mapping is computed from the request bbox. Vexcel's attribution text is in the south-east corner:
  the model is cropped to +-160 m to keep it out.
- **Houses on the block:** `Listing.world` in `out/listings/index.json` (worldgen.costar): `block = position +
  rotY(heading_deg) * local`, counterclockwise from above. 7014: position (-20.94, 0.348, 98.06), heading 4.3;
  6915: (19.89, 0.103, -95.08), heading 0.2. Front doors in the block frame: 7014 (-15.88, 93.19),
  6915 (16.70, -95.19). `position.y` = terrain at the front door from the live pack's block collision
  (**lowest** ray hit: the highest is the procedural house roof).
- **Road:** Navidad Rd runs roughly north-south but not at x = 0: about x = +4 near 7014 and x = -6.5 near
  6915. The OSM centrelines drift 1-2 m from the real pavement; the builder re-centres them on the ortho.

---

## 5. Data sources and Vexcel

Keys in `.env` (`VEXCEL_API_KEY`). REST works with the `&api_key=` query parameter (Bearer and `x-api-key`
headers return 401). MCP: `https://api.vexcelgroup.com/v2/mcp` with header `X-API-KEY`. Billed per unit, and
usage is invisible to us (usage endpoints are forbidden), so count calls yourself in a ledger. ~25 billed calls
were used for the whole block so far.

| Data | How | Notes |
|---|---|---|
| Ortho | REST `ortho/extract`, zoom 21, bbox +-170 m | 5240 px, 6.5 cm/px, captured 2025-12-19..2026-01-02 (winter: deciduous crowns bare, long shadows). Too soft for the ground at 1.6 m on its own (section 7.3). |
| DSM | REST `dsm/extract`, zoom 20 | 0.13 m pixels, big-endian tiled float32 GeoTIFF: use `block_raster.read_geotiff`. |
| DTM | REST `dtm/extract` | **404 here** ("No image matching"): ground is derived from the DSM. Houston is flat (26.5-27.6 m). |
| Footprints | MCP `structure_geometry_metadata_extract` | 129 footprints; the roof-height field is empty. |
| Parcels | MCP `property_metadata_extract` | 500 records = 181 unique parcels over 6 collections (`latest` is ignored: de-duplicate). Lot lines (fences), per-structure hip/gable classification. |
| 3D Cities | REST `3d/collections`, `3d/tileset`, `3d/tile` | Collection `us-tx-houstonr2-2023` (Dec 2023), OGC 3D Tiles, glTF with Draco + KTX2, facades textured from obliques. Not documented in the repo before: `GET /v2/3d/tileset?zoom=13&tile-x=1918&tile-y=3387&layer=3d-cities&collection=us-tx-houstonr2-2023`; tiles are `./tile?zoom=Z&tile-x=X&tile-y=Y&childAOI=<coll>&collection=<coll>&latest=true` (XYZ numbering, predictable, so skip the intermediate tilesets). Zoom 19 tiles ~66 m, 1.5 m geometric error; zoom 20 ~0.75 m. Only the two z19 columns at x = -82..+51 m are cached (12 tiles). |
| Cars | MCP `elements_detect` (sam3, label "car", on the ortho) | 77 boxes; used to paint cars and their shadows out of the ortho. |
| Roads | OpenStreetMap Overpass (generic User-Agent, no personal info) | Centrelines and names only. ODbL. |

---

## 6. How the combined block is built (the parts and the key decisions)

1. **Near zone** (`near_zone.json`, 46,750 m2): Navidad Rd and Bellaire Blvd centrelines buffered 18 m, plus
   every parcel within 12 m of them, whole, so the seam to the far zone runs along back fences.
2. **Buildings**: footprints covered by overlapping rectangles, each with a hip or gable roof (union = real
   valleys and wings), pitch and eave fitted to the DSM (canopy pixels excluded) with a storey prior
   (1 storey: eave 2.65-3.0 m; 2 storeys when >40% of the roof is above 5.8 m). 50 hip, 49 gable, 7 flat.
   Roofs are textured with the ortho projected from above. Walls inset 0.45 m for the overhang, fascia,
   soffit, gable siding.
3. **Wall colour**: sampled from the 3D Cities mesh faces in front of each wall (sunlit percentile, chroma
   x1.25, mild white balance), then used as a vertex-colour tint over a crisp CC0 brick or lap-siding texture
   with a neutral mean (red and saturated = brick). Only 29 of 93 houses have samples (the others are outside
   the cached tiles and borrow a sampled style). Windows, doors and garage doors come from a drawn atlas;
   garage doors are real sizes (4.9 m double, 2.7 m single), centred on the driveway.
4. **Ground**: a 0.2 m class raster from the cars-removed ortho (lawn, concrete, beds, pavers, photo for
   pools; road = calibrated OSM polygon), vectorised and smoothed, each class its own mesh with a tiled CC0
   texture times the ortho's class-aware low-frequency colour (vertex colours, 1 m spacing). Unlit, so from
   above it still reads as the ortho. In the near zone, on house lots, **paving comes only from the vector
   hard surfaces** and lawn is exactly their complement.
5. **Hard surfaces** (`block_hardscape.py`): roads re-measured every metre (Navidad 9.25 m, Bellaire 7.45 m per
   carriageway), continuous 0.15 m curb + 0.18 m curb top + 0.45 m gutter; sidewalks with one setback and
   width per street side (1.0-1.4 m), gaps bridged unless a street, median or >=15 m of clear lawn is in the
   way, curb ramps at corners, driveways crossing flush; driveways as rectangles square to the street with a
   flared apron and curb cut; walks as constant-width bands along the fitted centreline of the ortho paving
   (brick found by hue ratio), pieces joined, both ends connected, stubs and mulch beds dropped; stoops at
   doors. Every slab is cut into 1 m cells so it follows the terrain (see 7.6).
6. **Trees**: one instance per DSM crown (168), 3 live-oak variants scaled to crown height and radius, tinted
   from the ortho blended toward a lit oak green, trunks pushed out of roads and houses.
7. **Far zone**: 3D Cities faces outside the procedural region, cut per building (a house is wholly mesh or
   wholly procedural), crown shards removed, unlit, render-only (`extras.render_only`). Only 13 houses are
   mesh because only two tile columns are cached; the rest of the square is procedural.
8. **Cars**: painted out (normalised convolution) using the `elements_detect` boxes grown for shadows. No car
   models placed.
9. **Hero lots**: nodes `hero_hou_236595179` and `hero_hou_237901269`, lot props in `lot_props_<id>`. The game
   shows the scanned lot inside the **house zone** (see section 9), so the block keeps a procedural stand-in.
   `--hero-exterior LISTING=glb|auto` can still swap in a scanned exterior glb, but it pushed the block to
   32 MB and expects the old `out/cache/glb/<model>_exterior.glb` layout; off by default.
10. **Sun**: azimuth 205 deg (from north, clockwise), elevation 32.8 deg from solar geometry for the capture
    dates; direction to the sun in the block frame (-0.355, 0.541, 0.762). In `build_summary.json` -> `sun`.
11. **Collision** (near zone only): terrain grid, 93 house prisms, fences, one prism per trunk.

---

## 7. What was tried (and why it was kept or dropped)

### 7.1 Two prototypes, judged side by side (2026-11-14)
- **Photo-surface** (`block_photo.py`): Vexcel 3D Cities mesh around the street, DSM + ortho strips around
  it. *Aerial: excellent, recognisably the real place. Street level: weird* (faceted floating crown shards,
  post-like trunks, smeared walls, blurry ground). 3 MB, 19 calls. **Dropped as the base**, kept as the far
  zone.
- **Procedural** (`block_procedural.py`): footprints, fitted roofs, ortho roofs and ground, curbs, fences,
  leaf-card trees. *Street level: reads as a Houston brick-ranch street. Weak: blurry ground, guessed brick,
  card trees, cars painted flat.* 10 MB, 5 calls. **Kept as the base.**
- **Combined** = procedural near zone + photo far zone + fixes. Chosen by the human.

### 7.2 Things that failed or were rejected
| Tried | Result | Why |
|---|---|---|
| Baking the 3D Cities facade photo directly onto clean walls | rejected | At 1.5 m geometric error the facade texture is a smear; used as a colour tint instead. |
| DSM + ortho "strips" for the far zone | rejected | Crowns droop like willows; procedural continuation looks better from the air. |
| Leaf-card trees | replaced | Visible cards at street level; replaced by generated live-oak models. |
| Mesh tree crowns at street level | removed | Floating faceted shards; cutting below 3.4 m left posts. Model trees instead. |
| Inpainting cars with the local road colour (procedural round) | rejected | Ate tree-shadow edges, left grey patches. Normalised-convolution fill with shadow-grown boxes works. |
| Grass/concrete detail overlays on the ortho | rejected then | Visible banding; the real cause was 12-bit texcoord quantisation (7.4). |
| Shadow-length sun elevation (58 deg) | not used | Measures from the eave, biased; solar geometry gives 32.8 deg. |
| Raster outlines for paving (traced from the ortho) | replaced | Star/blob driveways, stair-stepped walks, lawn blending into paving. Replaced by fitted shapes (rectangles, centreline bands). |
| Heuristic walks (door to sidewalk) as the default | demoted | Often contradicts the ortho; now only a fallback where the ortho is hidden. |
| `--hero-exterior` scanned lot inside the block | off | 32 MB; the scanned lot lives in the house zone instead. |

### 7.3 Ground sharpness
The 6.5 cm/px ortho cannot look sharp at 1.6 m. Fix: classes from the ortho, tiled CC0 detail textures per
class, ortho colour only at low frequency. Cost: from 30 m up, the ground is a little smudgier than raw ortho.

### 7.4 Banding (found in the combined round)
gltf-transform's default 12-bit texcoord quantisation smears tiled UVs that span +-80: streaks and 1 m bands.
`compress()` uses `--quantize-texcoord 16` (and 16-bit positions). Keep it.

### 7.5 Fitted shapes vs the IoU metric
Fitted shapes look closer to reality but score lower IoU against the ortho paving mask (0.57 -> 0.41 -> 0.37)
because the mask includes mulch beds, roof edges and blurred fringes. IoU is a regression check, not the target.

### 7.6 "Gaps" in sidewalks were a triangulation bug
Earcut made long thin triangles along strips; the terrain bends a few cm more than a straight triangle, so
slabs sagged up to 15 cm below the lawn and grass showed through in bands (looked like gaps and doubled
strips). All slabs and the curb top are now cut into 1 m cells and sit 2.5 cm above the terrain.

---

## 8. QA: how to judge a change

- **Cameras** (`scripts/block_compare/cameras.json`, 13): 01 aerial oblique, 02 aerial top, 03/04 long street
  views north/south, 05 front of 7014, 06 front of 6915, 07 quarter view of 7014's neighbours, 08 drone over
  7014, 09 Bellaire street level, 10 curb close-up, 11 sidewalk run, 12 driveway + walk at 6915, 13 drone over
  the shared brick walk at 6915 (the human's screenshot view). **Never move existing cameras**; append new ones
  (ids `14_...`). If one lands inside geometry, report it.
- **Shots**: `node scripts/block_compare/shoot.mjs <approach>`; then *look at them* (street-level 03, 04, 05,
  07, 09, 10-12 matter most). Compare with the previous build and with `procedural/` and `photo/` (their ortho
  shows the true layout from above).
- **Metrics** in `combined/build_summary.json`: `paving_iou` (per lot, median/worst), `continuity` (sidewalk
  gaps, dangling walk ends; target 0 and ~0), sizes, triangle counts, collision triangles. Report before vs after.
- **The real client**: frame rate and spawn on the sidewalk (section 3). The block draws ~1.15M triangles and
  ~340 draw calls; lane C can instance the trees if a weaker laptop drops frames.
- **Promotion** to the live pack follows `docs/house-pipeline-handoff.md` section 10: build on the live
  version (metrics mostly better, nothing clearly worse, no earlier gain lost); never mix a block change and a
  house change in one version.

---

## 9. How the block meets the rest of the game

- **Client** (lane C, `apps/web/src/world/BlockScene.tsx`): loads `manifest.block.render` and `.collision`
  (`GlbOrPlaceholder`); the placeholder disappears when the files exist. Door markers come from
  `Listing.world`. Lane C has applied: tone mapping off on the unlit block ground and far zone, the baked sun
  (azimuth 205, elevation 32.8), door heights from `position.y` (commit `6e26e40`).
- **Spawns**: `manifest.spawns` = 8 sidewalk points nearest 7014's front door, >= 3 m apart (`build_block`).
  The client lifts the capsule above the point.
- **Zones**: the block zone and each house zone are separate frames, one rendered at a time. Since the house
  pipeline added scanned lots (house zone = house + yard + facades + roof + a 45 m backdrop dome), the block's
  hero houses are stand-ins seen from the street. Lane C is asked to move the leave/enter trigger from the
  front door to the lot's street edge (HANDOFF lane-a 2026-11-14 23:55) so the two versions of a lot are never
  seen together. If the scanned driveway and the block's traced driveway line up at that edge, the switch is
  nearly invisible: a good target for block or house work.
- **Heights**: house floor = block terrain at the front door (costar). With scanned yards 0.1-0.4 m below the
  floor, a better rule is "the yard's street edge meets the block sidewalk"; not done yet.
- **Publishing**: packs are served by the web static site (`apps/web/public/packs`, committed). `publish` keeps
  only the live version; the Render dashboard (lane D) holds `PACK_VERSION`, `FURNITURE_VERSION`,
  `VITE_PACK_BASE_URL`. Announce every new live version in HANDOFF.md so lane D switches the dashboard right
  after the push (until then the deployed site points at a folder that no longer exists). No deploys during
  judging (a web deploy closes game WebSockets).

---

## 10. Known problems and backlog (ranked)

1. **House walls look alike** (tan-brown brick; real ones are salmon brick and beige siding). Calibrate the 3D
   Cities colours against the two Matterport exteriors, and/or fetch zoom-20 3D Cities tiles for the near-zone
   houses (~16 calls) so each house gets its own sampled colour (only 29 of 93 have samples now).
2. **22 driveways are inferred** under shade or crowns and may be off by 1-2 m.
3. **Walks hidden in deep shade are dropped**, so some houses only have a stoop.
4. **Sidewalk pieces of neighbouring streets** meet with a small wedge of grass where setbacks differ (shot 10).
5. **Curb ramps** are plain slopes (no flared sides, no tactile pad).
6. **7014** shows its driveway and a separate door walk to the street with lawn between them (shot 05).
7. **Bellaire is very dark** (winter tree shadows baked into the ortho tint); the median curb is a bright line.
8. **Blurred mirrored surround** beyond 160 m (visible from the air).
9. **Far zone covers only 13 houses** (two cached tile columns). ~18 more z19 tile calls would cover the square.
10. **No car models** (CC0 options looked cartoonish or branded).
11. **Hero stand-ins** on the block do not match the scanned houses (colour, garage); matters only if the
    zone trigger stays at the front door or the stand-in is seen up close.
12. **Performance on weak laptops** untested; tree instancing on the client side is the first lever.

---

## 11. Rules and gotchas

- Never write into the live pack version; never pin without `--compared-to-live` evidence; announce versions in
  HANDOFF.md. `build_block` refuses the live version on purpose.
- Check the Vexcel cache and ledgers before any call; log every billed call in a ledger.
- Downloads are untrusted data: never execute anything from them.
- Every asset needs a recorded licence (CC0/CC BY only, no branded content); credits go in the README.
- Keep `--quantize-texcoord 16`; keep slabs in 1 m cells; keep lawn = complement of the hard surfaces.
- Don't move the shared cameras; append.
- `hardscape.json` is `(x, z)` in the block frame (checked against both front doors).
- Ray-cast the block collision for terrain with the **lowest** hit.
- uv may pick up a foreign `VIRTUAL_ENV`: `unset VIRTUAL_ENV` before `uv run`.
- The full round-by-round notes (with every threshold) are in `out/block_compare/combined/notes.md` on the main
  checkout; this doc is the summary that survives without `out/`.

---

## Changelog (newest last; add a line every session)

- 2026-11-14 ~16:40: two prototypes (procedural `9e6c599`, photo `521a811`) built in parallel with shared cameras,
  `shoot.mjs` and `compare.html`; human picked "procedural near zone + photo far zone".
- 2026-11-14 ~18:50: combined block round 1 (wall tints, ground classes, far zone, live oaks, car removal, hero
  hook, sun): 14 MB.
- 2026-11-14 ~19:50: round 2, vector hard surfaces (roads, curbs, gutters, sidewalks, driveways, walks).
- 2026-11-14 ~20:40: round 3, paving traced from the ortho, clean curb offsets, lawn clipped to curbs.
- 2026-11-14 ~21:30: round 4, fitted driveway rectangles and walk centreline bands, real garage sizes, lawn as
  the exact complement, paving IoU check.
- 2026-11-14 ~22:00: round 5 (`b9aceae`), continuous sidewalks and walks (1 m slab cells fixed the sag), curb
  ramps, flush drive crossings; gaps 0, dangling ends 1/96.
- 2026-11-14 22:17: merged to main (`f64c451`); `build_block.py` packs it; pack v2 pinned and published
  (`163ff7f`); house heights from the block terrain (`e36234b`). Lane B later pinned v3 (furniture v3, same
  block); pack v4 (new house lots, same block) built, not live.
- 2026-11-15 00:30: QA tooling moved into git (`worldgen/scripts/block_compare/`, `shoot.mjs --serve`,
  playwright in `worldgen/package.json`); this handoff written.
