# Lane C status: handoff for the next agent

Lane C is the game client's world and player: `apps/web/src/world/` and
`apps/web/src/player/` (AGENTS.md section 6). Written 2026-11-14 at main
`1d8b011`; updated at the end of the second session the same day (section 9), again for remote players (section 11), for the capsule radius (section 12), for crouch and contracts v2.4.0 (section 13), for the production pack path (section 14), and for the block walk-in and the pool noodle (section 15). Read AGENTS.md first. Rules that matter most here: stay inside
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
| Placeholders | `BlockScene.tsx`, `HouseScene.tsx`, `placeholder.ts` | Block: lawn, street, sidewalk, a massing box per house, a lit door with a trigger ring. House: floors by room label, walls with door gaps, an exit door. Every house zone has a fenced lawn yard, so an exterior door gap never drops the player into the void. Every room also gets a ceiling collider at `floor_y + height_m` (`HouseCeilings`, `ceilingTrimesh`), because `collision.glb` has no ceiling. |
| Collision groups | `world/collisionGroups.ts` | `ZONE_COLLIDER` (collision mesh and its placeholders), `FURNITURE_COLLIDER`. Two query filters: `CAMERA_QUERY` (zone only) and `SHOT_QUERY` (zone and furniture). |
| Frame order | `world/frameOrder.ts` | Physics −50, camera −10, blaster 0. Never use a positive `useFrame` priority: R3F stops rendering automatically. |
| Player | `player/Player.tsx`, `movement.ts`, `animState.ts`, `playerControl.ts` | An ecctrl capsule (WASD or arrows, Shift to run, Space to jump), with `lockForward` while the camera is on. Writes `me.pos`, `yaw` and `anim` to the store at 20 Hz for Lane D's `move`. `playerControl` moves the physics body itself (`teleport`); writing `me.pos` does not. |
| Camera | `player/CameraArm.tsx`, `armMath.ts` | Eye at an adult 1.6 m (`headOffsetY` 0.65 above the body centre; the model is still 1.3 m). Over-the-shoulder arm with collision, 3.0 m back and 0.4 m right, scroll range 0.6–3.5 m. Indoors it starts in first person. V toggles first person. Click to lock the pointer. The body hides below the larger of 1.6 m and r / tan(hFOV/4). It follows the interpolated body, which fixed an 8 cm per-frame jitter. |
| Blaster | `player/Blaster.tsx`, `blasterMath.ts`, `Crosshair.tsx`, `blasterHud.ts` | Hitscan: aim along the camera ray past the head, shoot from the head toward that aim point, 4 shots/s. Tracer, muzzle flash, impact puff. Crosshair and hit marker show only while the pointer is locked. Seams for Lane D: `getHitboxes` and `onFire` props on `<Player>`. |
| Furniture | `world/FurnitureLayer.tsx`, `furniturePlan.ts`, `indoorSpawns.ts` | Renders `store.houses[zone]`. A missing mesh becomes a labelled box from `size_m`. Colliders are built from `size_m`. 0.45 s drop-in. On a plan change: remount (no ghost colliders), push-out of the real body, and indoor spawns recomputed. |
| Labels | `world/WorldLabel.tsx`, `labelFade.ts` | Fixed screen size. Hidden below 1.5 m, gone by 10 m (door labels stay up to 40 m). |
| Remote players | `world/RemotePlayers.tsx`, `remoteInterp.ts`, `remoteTargets.ts` | One `<Character>` per other player in my zone, from `store.players`, interpolated 100 ms behind. Their hitboxes (sized to the model) feed the blaster. See section 11. |
| Character | `player/Character.tsx`, `characterRig.ts`, `clips.ts`, `characterAsset.ts` | Quaternius Toon Shooter soldier, 1.3 m tall. `<Character animState moving? />` reads only its props. 0.2 s crossfades. Origin at the feet. See section 6. |
| Dev only | `world/DevPlan.tsx`, `devFurniture.ts` | `VITE_DEV_PLAN` and the `window.__laneC` probe (sections 3 and 4). |

Tests: `pnpm test` runs 221 tests in 14 files. Two files also run under plain Node:
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
- Doorways (sections 9 and 12): with the 0.5 m capsule, all 13 doors in 6915 pass and every room is reachable (it was 10 of 13 at 0.6 m). 7014 has not been checked yet.
- Ceilings: the camera looking down clamps at 2.34 m under a 2.44 m ceiling (it was 3.54 m), and an 8 m/s launch stops at 2.47 m (it was 4.85 m).

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
| `VITE_DEV_REMOTES` | **Dev builds only, never deployed:** gated on `import.meta.env.DEV`, so `vite build` drops them even if the var is set on the host (checked: a production build with it set contains no trace of them). Two fake remote players in my zone on scripted paths (a runner on a circle; a walker that pauses and shoots), written into `store.players` at 20 Hz with arrival jitter. Your shots hit them: 3 hits play their death animation, and they respawn after 3 s. The console logs each `fire` payload. |
| `VITE_SOLO` | Lane D's: `net/game.ts` skips the game server. |

Current local `/.env.local`:
`VITE_PACK_BASE_URL=/packs/hou_navidad_01/v1`, `VITE_START_ROOM=hou_237901269.r4`, `VITE_DEV_PLAN=1`, `VITE_DEV_REMOTES=1`.

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
- **7014's doors are unchecked:** the radius is now 0.25 m, so per Lane A's widths every bathroom door in both houses should fit, and 6915 is confirmed headless (section 12). 7014 hasn't been run: its pack isn't on this machine or any branch. Run `apps/web/scripts/door-check.ts` on it once Lane A's publish lands.
- **Jump apex vs ceiling:** the 1.6 m eye sits exactly at the capsule top. A normal jump (4 m/s) peaks at about 2.40 m, which puts the first-person eye within the 0.1 m near plane of a 2.43 m ceiling, so the roof can clip at the top of a jump. Fixes: a lower `jumpVel` (3 m/s gives about a 0.46 m apex), or an eye a little below the capsule top. Not done.
- **No respawn yet:** `indoorSpawns` computes indoor spawns, but nothing uses them yet. That's the foam-fight round loop, Lane D server and C client.
- **Weapon choice:** the character holds the kit's `RocketLauncher`, tinted foam orange. Despite the name it's a chunky cartoon tube, the only prop in the kit that reads as a toy; the pistols and rifles are realistic. Changing it is one constant: `CHARACTER.keepWeapon`.

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

None of these were addressed on main as of the end of session 2 (checked in `apps/game/src/rooms/NeighborhoodRoom.ts`).

1. **Zone changes are rejected by the server.** `move` drops any step over `MAX_STEP_M` (3 m), and `enter_zone` doesn't reset that. A zone change always jumps more than 3 m into the new zone's frame, so after one door every later move is dropped and the player looks frozen to others. The first spawn hits the same rule, being more than 3 m from the server's default (0, 0, 0). Fix: accept the next position after `enter_zone` and after joining.
2. **Remote players:** interpolate from the rendered mesh transform, never from `body.translation()` or ecctrl's `currPos` (the 8 cm swing).
3. **Send `fire`.** `World.tsx` passes `onFire` to `<Player>`. Point it at your sender instead of `undefined` (dev mode passes `devFire`). The payload is `{origin, dir, target_id?}`: origin is the head, always within 1.5 m of `me.pos`, and `target_id` is set only on a player hit. Server side, `fire` is still a TODO. `devFire` in `world/fakeRemotes.ts` shows the expected rules: 3 hits eliminate, respawn after 3 s.
4. **Done on the Lane C side:** remote rendering (`RemotePlayers`, interpolated 100 ms behind) and `getHitboxes` (wired to `remoteHitboxes`). Lane D only needs to keep `store.players` updated: `pos` is the body centre, plus `yaw` in radians, `anim`, `zone` and `alive`.

## 8. Environment gotchas

- **Starting Vite:** run it as `./node_modules/.bin/vite` from `apps/web`. That was the reliable way to start it here.
- **Restart after a rename or rebase:** the module graph goes stale and serves 500s, or "Failed to reload … importing a module script failed". A rebase that rewrites files mid-load can also abort an `interior.glb` load: its 20 texture blobs fail with "Couldn't load texture blob:…". That was a one-off from the reload, not a pack problem.
- **Where the pack lives:** `apps/web/public/packs/`, gitignored, never committed (4.3 MB of mesh; it belongs in the bucket).
  - The `packs.zip` from Downloads extracted to `public/packs/hou_navidad_01/v1/`.
  - Lane A's `publish --local` writes `public/packs/packs/...` instead.
- **Browser console:** Vite forwards the browser console to its terminal output. That's useful when no browser automation is available.
- **Case-insensitive filenames:** macOS treats `CameraArm.tsx` and `cameraArm.ts` as the same file. That's why the camera math is `armMath.ts`.
- **Node checks:** for headless checks in Node, three's `GLTFLoader` needs a `ProgressEvent` stand-in (`globalThis.ProgressEvent ??= class { ... }`) to load an embedded `.gltf`.

## 9. Session 2 (2026-11-14, afternoon): what changed

Merged to main this session:
1. **Character crash fix.** React StrictMode's dev unmount and remount, plus `mixer.uncacheRoot()`, left stale `AnimationAction`s that crashed on `play()` (`_cacheIndex`). The rig now keeps only clips, and `actionFor()` asks the mixer for an action each time. A regression test covers it.
2. **Eye height 1.6 m:** `ARM.headOffsetY` went from 0.5 to 0.65. The model and capsule are unchanged at 1.3 m. A test pins `FOOT_BELOW_CENTRE_M + headOffsetY = 1.6`.
3. **Doorway diagnosis.** Recorded in HANDOFF.md as lane-c -> lane-a. Nothing was changed.
   - **Method:** a 0.6 m capsule driven headless through all 13 doors of 6915 (`hou_237901269`, the local v1 pack from `packs.zip`) against `collision.glb`.
   - **Data matches:** collision holes match `rooms.json` widths to within 1–3 cm.
   - **Pass:** 9 doors (0.69–0.84 m), plus both closets through their wider openings.
   - **Blocked:**
     - hall bathroom r10, 0.56 m;
     - primary bathroom r9, 0.58 m;
     - closet r2's second opening, 0.50 m. Its other opening is 0.74 m, so it may be one bifold traced as two; that's a question for Lane A.
   - **Unreachable from the living room:** only the two bathrooms.
   - **Judgment:** the widths look like real 24-inch bathroom doors, not a measurement error. The fix is a capsule radius decision; per the session brief the radius was not changed.
   - **Caveat:** Lane A has rebuilt the house packs since (closed doors solid, carved openings). Re-run this on the rebuilt pack before deciding.
4. **Ceiling colliders** per room (section 1, Placeholders row).

### Deferred: 1.75 m character scale-up (specced, not done)

1.3 m is child height next to a 0.85 m sofa. Recommended approach: make the model's full height 1.75 m, and shorten the capsule by the float gap so the collider spans 0.3–1.75 m. That also removes today's collider and model mismatch. Keep the radius at 0.3 m, or 0.25 m if the doorway decision lands that way.

| What | Now | At 1.75 m | Where |
| --- | --- | --- | --- |
| Capsule half-height | 0.40 | 0.475 | `movement.ts` |
| Capsule radius | 0.25 (section 12) | 0.25 | `movement.ts` |
| Float gap | 0.3 | 0.3 | unchanged |
| Model height (`CHARACTER.heightM`) | 2·(h+r) = 1.3 | 2·(h+r) + float = 1.75 | `characterAsset.ts`, formula |
| Model scale | 0.5916 | 0.7966 | derived at load |
| Run foot lift | 5.0 cm | about 6.7 cm | measured at load |
| Body centre / `FOOT_BELOW_CENTRE_M` | 0.95 | 1.025 | derived |
| Eye (`headOffsetY`) | 0.65 → eye at 1.6 m | 0.575 → eye at 1.6 m | `armMath.ts` |
| Hide threshold minimum (`hideBodyBelow`) | 1.6 | 2.15 | `armMath.ts` |
| Hide threshold, FOV part | r / tan(hFOV/4) | unchanged unless the radius changes | — |
| Arm back / max | 3.0 / 3.5 | keep, maybe max 4.0 | optional |
| Muzzle offset (right / forward / up) | 0.22 / 0.45 / −0.3 | about 0.3 / 0.6 / −0.4 | `blasterMath.ts`, cosmetic |
| Spawn drop height | 1.0 m | `FOOT_BELOW_CENTRE_M` + 0.1 | `World.tsx` |
| Remote hitbox | centre 0.30 m below body centre, radius 0.25, half-height 0.40 | centre 0.15 m below body centre, radius 0.25, half-height 0.625 | section 5, HANDOFF to Lane D |
| Spawn clearance, door trigger | 0.6 / 0.9 / 1.2 m | unchanged (radius-based) | — |
| `jumpVel` | 4.0 (about 0.8 m apex) | about 3.0, or the head goes through 2.43 m ceilings | `movement.ts` |

**Tests that need new expectations:**
1. `characterRig.test`: "1.3 m tall" becomes 1.75, and the `heightM` formula check gains `+ floatHeight`.
2. `armMath.test`: "frames the whole capsule" (twice the capsule height is 2.9 m, but the 3.0 m arm shows 2.83 m). Restate it as 1.5× the model's height, or raise the default arm to 3.2 m.
3. `armMath.test`: the "1.6 m minimum" literals become 2.15 and 2.14.
4. `armMath.test`: "narrow view raises the threshold" needs a 30° view instead of 40°.

The eye-height test keeps passing if `headOffsetY` is retuned to keep the eye at 1.6 m. Everything else derives from the constants.

**Contract flag:** the asset schema's cover classes are absolute metres ("full" means taller than 1.2 m). At 1.75 m, "full" cover no longer hides a standing head. Changing that needs all four lanes.

## 10. Where Lane C stands, and the next task

**Stands:**
- **Single player works end to end on main:**
  - pack loading with fallbacks, zones and doors, collision and ceilings;
  - the over-the-shoulder camera, with first person indoors at a 1.6 m eye;
  - the foam blaster;
  - furniture from plans, as boxes until real meshes are published;
  - the animated Toon Shooter character.
- **Remote players:** rendered, interpolated and shootable (section 11), proven with fake players (`VITE_DEV_REMOTES`). The networked fight needs Lane D's server items in section 7: accepting moves after a zone change, the `fire` handler, and passing their sender as `onFire`.
- **Verification:** most physics is proven headless. The character in the live page, the label fade, the jitter fix and all of crouch (section 13) still need a browser pass.

**Single next task:** check 7014's doors once Lane A's committed publish lands, then do the browser pass. (Section 12 records that the radius decision is done.)
1. After Lane A's publish is on main (HANDOFF 16:30, now OK'd by Lane C), set `/.env.local` to `VITE_PACK_BASE_URL=/packs/packs/hou_navidad_01/v1` and `VITE_START_ROOM=hou_236595179.r3` (the hero house). Delete the stale `apps/web/public/packs/hou_navidad_01/` from `packs.zip` first, so it never gets committed alongside.
2. Run `node --experimental-strip-types apps/web/scripts/door-check.ts apps/web/public/packs/packs/hou_navidad_01/v1/houses/hou_236595179` from the repo root. It exits non-zero if a bathroom is unreachable. Re-run it on 6915 there too, since Lane A rebuilt that house.
3. Browser pass with `VITE_DEV_REMOTES=1`: the soldier, the doorways, first person, shooting the fakes.
4. The 1.75 m scale-up (section 9) is still deferred. Do it in the same pass if the team wants it, since it touches the same constants.

## 11. Session 3: remote players and the weapon look

**Remote players** (`world/RemotePlayers.tsx`, `remoteInterp.ts`, `remoteTargets.ts`):
- **What renders:** one `<Character animState moving>` per player in `store.players` whose `zone` matches mine, excluding me (`me.id`).
- **Interpolation:**
  - Every patch is stamped on arrival (Colyseus patches have no timestamps).
  - Each player is drawn 100 ms behind the newest patch, lerping position and yaw (the short way round).
  - `anim` and `alive` come from the older patch and are never blended.
  - A zone change restarts that player's buffer, so it never lerps across frames.
  - It holds the last patch rather than extrapolating.
  - `moving` (for `Run_Shoot`) means a speed over 0.5 m/s.
- **Hitboxes:** built from the same interpolated samples that are drawn (the HANDOFF rule), and sized to the model: feet to feet + 1.3 m, with the capsule radius (0.25 since section 12). They are not sized to the floated capsule (0.3–1.6 m), so a dart at the knees hits and one just over the head misses.
- **Shooting:** `remoteHitboxes()` is passed as `<Player getHitboxes>`, so the blaster hits the nearest remote player, shows a red hit marker and puts `target_id` in the payload. The frame order is remote −20, before the blaster at 0.
- **If the model is missing:** remotes fall back to an orange capsule (the local player's is blue).

**Verification:** unit tests only (17 new). They cover interpolation at a given delay, zone filtering, model-sized hitboxes, the nearest of two targets with the right payload, a wall winning over a player, and the fake players' rules. **Not yet seen in a browser:** reload with `VITE_DEV_REMOTES=1` (now set in `/.env.local`) and shoot the two fakes.

**Weapon:** `CHARACTER.keepWeapon` is now `RocketLauncher`. Of the kit's 14 hand props, it's the one that reads as a toy: chunky, red and white, three nozzles, held by the Hazmat character in the pack preview. The others are realistic pistols, rifles, knives and grenades. It still gets the foam-orange tint. In hand it's about 0.77 × 0.54 m and rises to 1.34 m while aiming. It's one constant.

**Deliberately not done** (needs team agreement):
- crouch, which needs a seventh animation state: a contracts change under rule 2;
- melee and a second weapon slot, which are new scope;
- switching to human characters: section 5 forbids retargeting, and that option is two packs combined.

**Next task:** see section 10 (updated in section 12). For the scheduled networked fight, the remaining work is Lane D's (section 7, items 1 and 3).

## 12. Session 3, later: capsule radius 0.25 m (bathrooms reachable)

Lane A decided (HANDOFF 16:35) that bathrooms must be reachable in both houses, and that the door widths are real. The narrowest bathroom door is 0.57 m: 7014's primary bathroom r11, and 6915's hall bathroom r10.

- **The change:** `MOVEMENT.capsuleRadius` 0.3 → 0.25, so the capsule is 0.5 m wide. `capsuleHalfHeight` 0.35 → 0.40, so the capsule (and therefore the model, the 0.95 m foot offset and the 1.6 m eye) stays exactly 1.3 m. Tests pin all three.
- **What follows automatically:** remote hitboxes (radius 0.25, half-height 0.40), the FOV part of the hide threshold (the 1.6 m minimum now applies down to about a 36° horizontal view), and the placeholder capsules.
- **Tests updated:** the two blaster tests now read the radius instead of a literal 0.3. The narrow-view test went from 40° to 30°, because at radius 0.25 a 40° view no longer exceeds the minimum.
- **6915 result:** all 13 doors pass, including the 0.56 m hall bathroom, the 0.58 m primary bathroom and closet r2's 0.50 m opening. Every room is reachable from the living room.
- **7014:** not checked. Its pack isn't on this machine or any branch; `worldgen/out` doesn't exist here, and building it needs Lane A's pipeline. See section 10, step 2.
- **The door check is now a committed tool:** `apps/web/scripts/door-check.ts <house dir>`. It resolves Rapier through `@react-three/rapier`, so no new dependency, and it exits non-zero if a bathroom is unreachable.

**HANDOFF:** Lane C replied OK to Lane A's static-site hosting proposal (16:30), noting that the `.gitignore` line they'll remove is Lane C's and is fine to drop.

## 13. Session 3, later: crouch and contracts v2.4.0

**Contracts v2.4.0** (team-agreed; commit `79d72f5`):
- `ANIM_STATES` adds `crouch`.
- `fire` gains `weapon?: "blaster" | "noodle"` (`WEAPONS` in `game-state.ts`). An absent field means `blaster`. `noodle`, the pool noodle, is reserved for Lane D to define.
- No fixture carries an anim state or a `fire` payload, so none changed. `pnpm gen:types` gives no diff.
- A HANDOFF note tells Lanes B and D to pull and regenerate.
- It was pushed to main on its own, ahead of the client commit, so Lanes B and D could pull at once. For the few minutes between the two pushes, main failed web typecheck (and Render's `pnpm build`), because `clips.ts` had no `crouch` entry yet.

**Crouch, client side:**
- **Input:** hold **C**. Not Ctrl, which the browser owns (Ctrl+W).
- **Capsule: unchanged.** Crouch does NOT resize the capsule. An earlier version shrank it through ecctrl's `capsuleHalfHeight` prop, but ecctrl rebuilds the collider when that changes, and its balance spring can't re-stabilise the shorter capsule. In a browser the body tipped over and the game crashed. So the local player's physics capsule stays at standing height (0.3–1.6 m) while crouched.
- **Camera:** only the eye drops. `CameraArm` and `Blaster` read a `crouchedRef` and lower the head by the difference between the standing and crouched body centres (0.315 m), from 1.6 m to about 1.285 m. Shots fire from that lowered head.
- **Movement:** walk only at 1.3 m/s; no run. **Jumping is disabled while crouched** (`jumpVel` 0).
- **Anim priority:** eliminated > hit > crouch > shoot > locomotion. A player firing from crouch stays `crouch` on the wire, so remotes see and hit the lower body that's actually there.
- **Clip:** the Quaternius kit has no crouch clip and no crouch-walk. `crouch` plays the kit's `Duck` dodge trimmed to its first 0.5 s, down into its held 0.97 m pose, then clamped there. Nothing is faked or retargeted. Moving while crouched slides that pose, because no crouch-walk exists.
- **Remote players:** a crouched remote's hitbox spans 0–0.97 m and their model stands on the floor. Crouch keeps the standing capsule, so their body centre arrives at standing height, and the feet are always `FOOT_BELOW_CENTRE_M` below it (`remoteInterp.footBelowCentre`). Only the model and hitbox are lower. This was fixed in section 16: until then, the client still offset crouched remotes by the old crouched centre, which made them float 0.3 m and moved the hitbox to 0.315–1.285 m.
- **Blaster:** `fire` always sends `weapon: "blaster"`.

**Verified in a browser:** the capsule rebuild failing, which is why crouch no longer resizes it.

**Never seen in a browser** (inferred from code and unit tests): the eye drop, the Duck hold on the local and remote models, and the crouch feel (speed, transition, and the sliding pose while moving).

**Collider vs hitbox:**
- **Local player:** the collider stays at standing height while crouched, so it's taller than the crouched model. That can block a crouched player under something low that the model would fit under. With no capsule change, standing up under something low is no longer an issue.
- **Remote crouched players:** hitboxes span 0–0.97 m (model-sized, from `anim === "crouch"`). That's what other players' shots test, so the cover finding below holds. It's what remotes see, not the local physics.

**Cover finding** (headless, against 6915's real `collision.glb` and dev plan B, with the same numbers pinned in `apps/web/src/world/cover.test.ts`):
- **Setup:** plan B's 0.85 m sectional (2.8 × 1.9 m) stands in the middle of the living room. The target is 0.45 m behind it. Five standing shooters with 1.6 m eyes each fire at a 25 × 5 grid of aim points on the target's standing body.
- **Result:** 265 shots hit the standing target, and 103 of those still land crouched. So crouching behind the sofa blocks 61% of the shots that would hit you standing.
- **Why some still land:** they occur at aim heights of 0.75–0.95 m. A standing 1.6 m eye looks down over the sofa back, so lines aimed even 10 cm below the back clear its top edge. And the crouched head (0.97 m) is 12 cm above the 0.85 m back.
- **Plan A has no hiding spot:** its fixture sofa (0.79 m ABO) stands 0.305 m from the south wall, too narrow for the 0.5 m capsule.
- **This is correct, and kept:** a sofa is `half` cover under the section 9 definition (0.5 to 1.2 m), so partial is the right behaviour.
- **Full cover is a layout choice, not a physics change.** It's for Lane B's `furnish-for-play` skill: place `full` cover pieces taller than 1.2 m, such as a bookshelf, or arrange a sectional so that shooters can't look down over it.
- **What the tests guarantee:** crouching never makes you easier to hit, nothing aimed above the crouched head lands, and the sofa stops low shots in both postures.

**Next task:** unchanged (section 10): run the 7014 door check once Lane A's publish lands, then the browser pass, which now has to include crouch.

## 14. Production pack path (Gate 2 prep)

**Proven locally, not yet on Render.**
- **Build:** `tsc -b && vite build`, as Render runs it, with `VITE_PACK_BASE_URL=/packs/packs/hou_navidad_01/v1`.
- **Packs:** 6915, laid out the way Lane A will publish (`dist/packs/packs/hou_navidad_01/v1/...`, `dist/packs/listings/index.json`).
- **Host:** a plain static server, not Vite, run in two modes:
  - Render's `render.yaml` rewrite, `/*` → `/index.html`, where a missing file answers 200 with HTML;
  - plain 404.
- **Loaded in both modes:** the manifest, the listings index, `rooms.json` (10 rooms), the door, the derived spawn, `collision.glb` (2 meshes, 1,544 triangles), and `interior.glb`'s meshopt geometry (26 meshes, 40,433 triangles).
- **Fallbacks:** the missing furniture catalog and missing block meshes each fell back with one warning.
- **Not tested:**
  - **Interior textures:** WebP decoding needs a browser.
  - **The furniture catalog:** no library exists yet; Lane A hasn't published.
  - **The listings index:** a one-house stand-in built from Lane A's 16:46 numbers, in the scratch tree only.

**Missing-file handling** (`meshProbe.classifyHead`): 200 + HTML (Render's rewrite, Vite) and 404/403 mean missing. 405/501 (a host that rejects `HEAD`) mean unknown, so the loader tries instead of hiding a real mesh. `fetchJson` already treats non-2xx and HTML answers as missing.

**Dev switches never ship.** `world/devFlags.ts` gates `VITE_DEV_PLAN` and `VITE_START_ROOM` on `import.meta.env.DEV`, the same way `VITE_DEV_REMOTES` already was.
- **Before:** a production build with them set shipped the dev plan, plan B, the dev catalog and the start room ID.
- **Now:** building with all three set gives no trace of any of them in `dist/assets/*.js`.
- **Re-check after changes:** `VITE_DEV_PLAN=1 VITE_START_ROOM=x.r1 VITE_DEV_REMOTES=1 ./node_modules/.bin/vite build --outDir /tmp/x`, then grep the JS for `dev plan`, `p_dev_b`, `dev-remote`, `VITE_START_ROOM` and `__laneC`. All should be absent.
- **Effect:** deployed players start on the block, at the spawn derived near the hero's door (section 12), and walk in through the door once the listings index is published.
- **Choosing the hero house:** `VITE_HERO_LISTING` (e.g. `hou_236595179`) names it, so the derived spawn goes in front of that house even if the manifest lists another first. Unset or not on the block, it falls back to the first house in manifest order. It's not a dev switch: it ships in production builds, and Lane D sets it on Render (HANDOFF). Tests cover both manifest orderings.

**What Lane D needs** (HANDOFF, this session):
- set `VITE_PACK_BASE_URL=/packs/packs/hou_navidad_01/v1` and `VITE_HERO_LISTING=hou_236595179` on the web service (both are baked in at build time);
- keep the rewrite;
- set none of the dev switches;
- `buildFilter` already covers Lane A's committed publish;
- a static deploy doesn't restart the game service.

## 15. Block-to-house walk-in, and the pool noodle

**Walk-in, verified headless.** This is the only way into a house on the production build, which now starts players on the block.
- **Setup:** 6915 stands in as the hero, since it's the only house whose collision is on this machine (`~/Downloads/packs`); 7014 needs Lane A's publish. The run uses the client's own `loadPack` (with the hero spawn), `blockLayout`, `stepZone` and `roomIdAt`, the real `collision.glb`, the yard and ceilings, and a player-sized capsule at walking speed.
- **Found and fixed:** with one placeholder box over all rooms, the walk stopped 1.21 m from the door, outside the 0.9 m trigger. 6915's front door is on the living room's west wall, about a metre in from the bedrooms, and the box covered it. With that, nobody could get into the house on the production build. The block now draws and collides one box per room (`placeholder.houseBoxesLocal`), and a unit test with a recessed entry guards it.
- **After the fix:**
  1. From the derived spawn (2.20, −95.14) the capsule walks east along the street and switches zone at 5.3 s.
  2. It lands at (−1.99, −0.13), house-local coordinates (the block door was at (16.70, −95.19)), in `r4`, the living room, with the door disarmed.
  3. Walking in to (0.6, −1.2) keeps it in `r4` and re-arms the door.
  4. Walking back to the door switches to the block, landing at (15.50, −95.19), 1.2 m from the door.
  5. It doesn't bounce while standing there for 2 s.
  6. It re-enters after walking 2.5 m away and back.
- **Not covered:** this is a plain capsule, not ecctrl's floating one, and nothing was rendered.

**Pool noodle** (contracts 2.4.0 `weapon: "noodle"`):
- **Defaults:** every number is a named constant in `player/weapons.ts` (`NOODLE`, `WEAPON_RULES`), and Lane D may overrule any of them (HANDOFF).
  - **Swing:** melee, measured from the head. Reach is 2.0 m to the target's body surface in 3D. The arc is 60° wide, horizontal, centred on the aim, and grazing a body's side counts.
  - **Cooldown:** 0.8 s; the blaster stays at 4 shots/s.
  - **To eliminate:** 2 noodle hits, against 3 from the blaster.
- **What it hits:** players only. The swing ignores walls and furniture, and doesn't stop darts. That also means it can hit through a thin wall within 2 m; a server line-of-sight check would close that.
- **The `fire` payload:** `{origin: head, dir: aim, weapon: "noodle", target_id?}`. The swing plays the `shoot` anim; there's no new state.
- **Switching:** keys 1 and 2 (`weaponState`). The held weapon picks the prop in `Character`/`characterRig`.
- **The prop:** the kit has no noodle, so it's a 1.2 m foam cylinder on the blaster's hand bone, placed like the blaster.
- **Remote players always show the blaster:** the wire has no per-player weapon field, so showing it would be a contract change.
- **Not seen in a browser:** the noodle prop, the swing feel and the absence of a swing effect. There's no swoosh visual yet, only the anim and the hit marker.
- **Dev stand-in:** `fakeRemotes` uses health 6, so 3 blaster or 2 noodle hits eliminate.

## 16. Crouch capsule fix, noodle jiggle, one-shot replay (integrated)

- **Crouch:** an earlier agent session's fix is kept as written. It doesn't resize the capsule, which was verified to crash in a browser; it lowers the eye, disables jumping and plays Duck. Section 13 is corrected to match.
- **Noodle:** that session's procedural jiggle is kept as written. It's a damped spring on the mesh vertices, driven by the tip's motion and capped at 35% of the length; visual only, with no physics bodies or colliders. The noodle is held upright using a fixed hand-space direction vector. Measured on the real model, that's 0–10° from vertical at idle, 6–23° running and 40–57° in the shoot clips (the arm comes up to swing). Left as is: tilting forward on a swing reads as natural.
- **Crouched remotes and the local fallback follow the unchanged capsule:** `footBelowCentre` returns the standing offset in every state. Before this, a crouched remote floated 0.3 m with a 0.315–1.285 m hitbox. The local player's in-air feet fallback and placeholder capsule stay standing size too.
- **One-shot replay restored:** `if (prev === next && !plan.once) return`, now in `characterRig.crossfadeTo` and tested on the real rig. A repeated one-shot (hit, jump, death, Duck) restarts from frame 0. A repeated loop carries on.
  - **Shoot is a loop:** `Idle_Shoot` (or `Run_Shoot`) loops, so rapid blaster fire is one continuous clip, and this line never governed it.
  - **The crossfade effect only re-runs when its inputs change:** `animState`, `moving` or the rig. So a state that repeats without changing (`hit` → `hit`) doesn't re-trigger at all. A per-event replay would need a counter prop.
