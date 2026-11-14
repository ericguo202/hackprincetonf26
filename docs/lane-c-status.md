# Lane C status: handoff for the next agent

Lane C is the game client's world and player: `apps/web/src/world/` and
`apps/web/src/player/` (AGENTS.md section 6). Written 2026-11-14 at main
`1d8b011`. Read AGENTS.md first. Rules that matter most here: stay inside
those two folders (plus `apps/web/public/characters/`). Never edit
`apps/web/src/store.ts` (Lane D) or anything in `contracts/` (all four lanes
agree first). Cross-lane needs go in `HANDOFF.md`.

## 1. What is built and merged

Everything below is on `main`. Branch: `lane-c/player`.

| Area | Files | What it does |
| --- | --- | --- |
| Pack loading | `world/pack.ts`, `usePack.ts`, `fixtures.ts` | Loads `manifest.json` from `VITE_PACK_BASE_URL` and validates it by hand against `manifest.schema.json` (a test checks the key lists against the schema file). It also loads the listings index, `rooms.json` and the furniture catalog. Falls back to `contracts/fixtures/` when the base URL is unset or the manifest is bad. A missing side file degrades to empty, with one warning each. |
| Zones and doors | `world/pack.ts` (`stepZone`, `makeDoor`), `World.tsx` | `me.zone` is `"block"` or a `listing_id`, and positions are in that zone's frame. Walking within 0.9 m of a door point switches zone. It re-arms at 1.8 m, so there's no bounce. `me.roomId` comes from `roomAt()`. Only one zone's geometry is mounted at a time. |
| Meshes | `world/Glb.tsx`, `gltf.ts`, `meshProbe.ts`, `collision.ts` | Every `.glb` gets a `HEAD` probe first. Missing (non-2xx, or Vite's `index.html`) means one warning and the placeholder; the loader never sees it, so nothing throws. `LoadBoundary` only catches files that exist but fail to parse. The meshopt decoder is three's own (`setMeshoptDecoder`). `collision.glb` becomes one trimesh. Render meshes never collide. |
| Placeholders | `BlockScene.tsx`, `HouseScene.tsx`, `placeholder.ts` | Block: lawn, street, sidewalk, a massing box per house, a lit door with a trigger ring. House: floors by room label, walls with door gaps, an exit door. Every house zone has a fenced lawn yard, so an exterior door gap never drops the player into the void. |
| Collision groups | `world/collisionGroups.ts` | `ZONE_COLLIDER` (collision mesh and its placeholders), `FURNITURE_COLLIDER`. Two query filters: `CAMERA_QUERY` (zone only) and `SHOT_QUERY` (zone and furniture). |
| Frame order | `world/frameOrder.ts` | Physics −50, camera −10, blaster 0. Never use a positive `useFrame` priority: R3F stops rendering automatically. |
| Player | `player/Player.tsx`, `movement.ts`, `animState.ts`, `playerControl.ts` | An ecctrl capsule (WASD or arrows, Shift to run, Space to jump), with `lockForward` while the camera is on. Writes `me.pos`, `yaw` and `anim` to the store at 20 Hz for Lane D's `move`. `playerControl` moves the physics body itself (`teleport`); writing `me.pos` does not. |
| Camera | `player/CameraArm.tsx`, `armMath.ts` | Over-the-shoulder arm with collision, 3.0 m back and 0.4 m right, scroll range 0.6–3.5 m. Indoors it starts in first person. V toggles first person. Click to lock the pointer. The body hides below the larger of 1.6 m and r / tan(hFOV/4). It follows the interpolated body, which fixed an 8 cm per-frame jitter. |
| Blaster | `player/Blaster.tsx`, `blasterMath.ts`, `Crosshair.tsx`, `blasterHud.ts` | Hitscan: aim along the camera ray past the head, shoot from the head toward that aim point, 4 shots/s. Tracer, muzzle flash, impact puff. Crosshair and hit marker show only while the pointer is locked. Seams for Lane D: `getHitboxes` and `onFire` props on `<Player>`. |
| Furniture | `world/FurnitureLayer.tsx`, `furniturePlan.ts`, `indoorSpawns.ts` | Renders `store.houses[zone]`. A missing mesh becomes a labelled box from `size_m`. Colliders are built from `size_m`. 0.45 s drop-in. On a plan change: remount (no ghost colliders), push-out of the real body, and indoor spawns recomputed. |
| Labels | `world/WorldLabel.tsx`, `labelFade.ts` | Fixed screen size. Hidden below 1.5 m, gone by 10 m (door labels stay up to 40 m). |
| Character | `player/Character.tsx`, `characterRig.ts`, `clips.ts`, `characterAsset.ts` | Quaternius Toon Shooter soldier, 1.3 m tall. `<Character animState moving? />` reads only its props. 0.2 s crossfades. Origin at the feet. See section 6. |
| Dev only | `world/DevPlan.tsx`, `devFurniture.ts` | `VITE_DEV_PLAN` and the `window.__laneC` probe (sections 3 and 4). |

Tests: `pnpm test` runs 155 tests in 11 files. Two files also run under plain Node:
`node --experimental-strip-types apps/web/src/world/geometry.test.ts` (21 tests) and
`node --experimental-strip-types apps/web/src/player/animState.test.ts` (7 tests).
`tsconfig.app.json` has `allowImportingTsExtensions` so those `.ts` imports work.

## 2. Verified in a real browser vs only headless

Be careful here: most of the physics was proven headless, not in the page.
The Claude Chrome extension never connected during this work, so every browser
check below was made by the human.

**Seen in a browser:**
- **The real house:** `hou_237901269` loads and is walkable through `VITE_START_ROOM`, and the camera arm works against its walls.
- **The jitter report:** the capsule visibly jittered. The fix (follow the interpolated body) has **not** been re-checked in a browser since.
- **Dev plan furniture:** it rendered as boxes with reason labels. Two bugs were found there and fixed (labels growing to fill the screen up close; missing-mesh throws).
  - The throw fix was confirmed from the browser console messages that Vite forwards to its terminal: after the fix, each missing mesh logs one warning and nothing throws.
  - The label fix has **not** been re-checked in a browser.

**Headless only** (Rapier in Node against the real `collision.glb`, or unit tests):
- Walls stop the capsule. The fenced yard catches a walk out the side door.
- The camera ray ignores furniture and clamps 0.15 m short of walls.
- Darts stop on walls and furniture. A bookshelf between the camera and the head doesn't block a shot.
- Furniture colliders stop the player.
- A re-furnish leaves no ghost colliders, and push-out moves the body. Without push-out, the body ends up on top of the new sectional.
- The jitter numbers: an 8 cm swing at 60, 120 and 144 Hz, down to 0 cm.
- Character scale (1.3 m), feet on the floor at rest and through the run cycle (foot lift), one tinted weapon, clip names matching the shipped file.

**Not verified anywhere yet:**
- The character in the page: feet following ecctrl's `standPoint` in practice, the crossfades, first person hiding the model, and its facing while running.
- The door trigger on a real block (the hero pack has no listings index, so there are no doors).
- The drop-in animation, and the blaster's effects and feel.

## 3. Dev switches

Vite reads env files from the **repo root**: `apps/web/vite.config.ts` sets
`envDir: "../.."`. So use `/.env.local` at the repo root (gitignored by `.env.*`).
An `apps/web/.env.local` is ignored.

| Var | Effect |
| --- | --- |
| `VITE_PACK_BASE_URL` | Pack folder, e.g. `/packs/hou_navidad_01/v1`. Unset means the contract fixtures and placeholder geometry. The published root (listings, shared `furniture/v<N>/`) is derived from it by `publishedRoot()`: three levels up for `.../packs/<block>/v<N>`. |
| `VITE_START_ROOM` | `<listing_id>.rN`: start inside that house at the room's area centroid, skipping the block. The only way into a house until a listings index exists. A wrong ID logs one warning and starts on the block. |
| `VITE_DEV_PLAN` | Furnish the current house from `contracts/fixtures/placement_plan.json`, moved to centre on the target room (pieces that don't fit are dropped with a warning). Dev stand-ins cover any asset the catalog lacks, matched by category. **P** swaps between this plan and plan B (`devFurniture.ts`), which drops a sectional on the room centre to exercise push-out. |
| `VITE_SOLO` | Lane D's: `net/game.ts` skips the game server. |

Current local `/.env.local`:
`VITE_PACK_BASE_URL=/packs/hou_navidad_01/v1`, `VITE_START_ROOM=hou_237901269.r4`, `VITE_DEV_PLAN=1`.

**Heads-up:** Lane A has since made 7014 Navidad (`hou_236595179`, living room `hou_236595179.r3`) the hero house.
- **Rebuild the local pack:** `cd worldgen && uv run python -m worldgen.publish hou_navidad_01 --local`.
- **Base URL:** that copies to `apps/web/public/packs/packs/...`, so the base becomes `/packs/packs/hou_navidad_01/v1`. It also copies the furniture library, so real furniture can load.
- **Start room:** point `VITE_START_ROOM` at `hou_236595179.r3`. The pack on disk today is still the older zip, which only has 6915 (`hou_237901269`).

## 4. `window.__laneC` (dev builds only)

Defined in `world/DevPlan.tsx` (`DevProbe`). Mounted in dev whether or not `VITE_DEV_PLAN` is set.

| Call | Returns |
| --- | --- |
| `__laneC.colliders()` | `{ total, furniture }`, a collider count in the Rapier world |
| `__laneC.furnitureBoxes()` | `[{ x, y, z, hx, hy, hz }]` per furniture collider |
| `__laneC.player()` | Live body centre `[x, y, z]` (raw Rapier translation) |
| `__laneC.teleport(x, y, z)` | Moves the physics body (body-centre coordinates) |
| `__laneC.plan()` | `{ plan_id, objects }` for the current zone, or `null` |
| `__laneC.spawns()` | Indoor spawns for the current house `[{ roomId, point, floorY }]` |
| `__laneC.dart([x,y,z], [dx,dy,dz], max = 40)` | First zone or furniture hit: `{ dist, furniture }`, or `null` |

Re-furnish check: `colliders()` then press P, then `colliders()` again. The furniture count should go from 2 to 4, and `player()` should no longer be at the room centre.

## 5. Known gaps

- **No block pack:** Lane A hasn't published one. No `block/render.glb` or `collision.glb`, and `spawns` is `[]`, so the block is always placeholder geometry.
- **No listings index:** `listings/index.json` doesn't exist yet (Lane A's `costar.py` is a stub). No listing has a door point, so you can't walk into a house from the block. Use `VITE_START_ROOM`.
- **Furniture renders as boxes** until real meshes are published. The labelled box is also the deliberate fallback if they never are. With Lane A's `publish --local`, the library lands in `public/packs/furniture/v1/`, so real meshes should start loading; that path isn't verified.
- **Interior looks washed out:** Lane A's HANDOFF suggests `material.toneMapped = false` on `interior.glb` meshes, since its textures are finished photos. Not done.
- **Blocky walls up close:** walls look blocky within about 30 cm. The camera arm pulls in 0.15 m from walls, so this can show indoors.
- **Character and collider don't line up:** the model is 1.3 m with feet on the floor, but ecctrl floats the 1.3 m capsule 0.3 m up, so the collider spans 0.3–1.6 m. Shot hitboxes for remote players should match the model, not the capsule: centre at feet + 0.65 m, which is 0.30 m below the body centre (`FOOT_BELOW_CENTRE_M` − 0.65).
- **No respawn yet:** `indoorSpawns` computes indoor spawns, but nothing uses them yet. That's the foam-fight round loop, Lane D server and C client.
- **Weapon choice:** the character's weapon is the pack's Pistol, tinted foam orange. Changing it is one constant: `CHARACTER.keepWeapon`.

## 6. Character pack facts

- **Pack:** Quaternius Toon Shooter Game Kit, CC0. The license is recorded in `characterAsset.ts` and `apps/web/public/characters/CREDITS.md`. `Character_Soldier.gltf` is the unmodified pack file, a self-contained `.gltf` of 2.2 MB.
- **Clips:** 17, listed in `clips.ts` `PACK_CLIPS`; a test checks them against the file.
- **Mapping:**

  | State | Clip |
  | --- | --- |
  | idle | Idle |
  | run | Run_Gun |
  | jump | Jump, then the Jump_Idle loop |
  | shoot | Idle_Shoot, or Run_Shoot when `moving` |
  | hit | HitReact, played once |
  | eliminated | Death, played once and held |
- **Weapons:** the right hand carries all 14 of the kit's weapons in one file, and glTF has no hidden flag. `characterRig` removes 13 per instance.
- **Feet:** the model's origin is its feet. The local player moves the model each frame so its feet meet ecctrl's `standPoint`, falling back to `FOOT_BELOW_CENTRE_M` (0.95 m) below the body centre in the air.

## 7. Open HANDOFF items for Lane D

None of these were addressed on main as of `1d8b011` (checked in `apps/game/src/rooms/NeighborhoodRoom.ts`).

1. **Zone changes are rejected by the server.** `move` drops any step over `MAX_STEP_M` (3 m), and `enter_zone` doesn't reset that. A zone change always jumps more than 3 m into the new zone's frame, so after one door every later move is dropped and the player looks frozen to others. The first spawn hits the same rule, being more than 3 m from the server's default (0, 0, 0). Fix: accept the next position after `enter_zone` and after joining.
2. **Remote players:** interpolate from the rendered mesh transform, never from `body.translation()` or ecctrl's `currPos` (the 8 cm swing).
3. **Blaster seams on `<Player>`, not wired yet:**
   - `onFire(payload)`: send it as `fire`. The origin is the head, always within 1.5 m of `me.pos`.
   - `getHitboxes()`: return remote players in this zone as capsules (`blasterMath.Hitbox`). Size them to the model, as in section 5.
   - Server side, `fire` is still a TODO.
4. **Rendering remote players:** `<Character animState={p.anim} moving={speed > 0.5} />` at `p.pos − [0, FOOT_BELOW_CENTRE_M, 0]`, rotated by `p.yaw`. It reads nothing but its props.

## 8. Environment gotchas

- **Starting Vite:** run it as `./node_modules/.bin/vite` from `apps/web`. That was the reliable way to start it here.
- **Restart after a rename or rebase:** the module graph goes stale and serves 500s, or "Failed to reload … importing a module script failed". A rebase that rewrites files mid-load can also abort an `interior.glb` load: its 20 texture blobs fail with "Couldn't load texture blob:…". That was a one-off from the reload, not a pack problem.
- **Where the pack lives:** `apps/web/public/packs/`, gitignored, never committed (4.3 MB of mesh; it belongs in the bucket).
  - The `packs.zip` from Downloads extracted to `public/packs/hou_navidad_01/v1/`.
  - Lane A's `publish --local` writes `public/packs/packs/...` instead.
- **Browser console:** Vite forwards the browser console to its terminal output. That's useful when no browser automation is available.
- **Case-insensitive filenames:** macOS treats `CameraArm.tsx` and `cameraArm.ts` as the same file. That's why the camera math is `armMath.ts`.
- **Node checks:** for headless checks in Node, three's `GLTFLoader` needs a `ProgressEvent` stand-in (`globalThis.ProgressEvent ??= class { ... }`) to load an embedded `.gltf`.
