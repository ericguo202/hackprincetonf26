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

Tests: `pnpm test` runs 251 tests in 18 files. Two files also run under plain Node:
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
| `VITE_PACK_BASE_URL` | Pack folder, e.g. `/packs/packs/hou_navidad_01/v2`. Unset means the contract fixtures and placeholder geometry. The published root (listings, shared `furniture/v<N>/`) is derived from it by `publishedRoot()`: three levels up for `.../packs/<block>/v<N>`. |
| `VITE_START_ROOM` | `<listing_id>.rN`: start inside that house at the room's area centroid, skipping the block. The only way into a house until a listings index exists. A wrong ID logs one warning and starts on the block. |
| `VITE_DEV_PLAN` | Furnish the current house from `contracts/fixtures/placement_plan.json`, moved to centre on the target room (pieces that don't fit are dropped with a warning). Dev stand-ins cover any asset the catalog lacks, matched by category. **P** swaps between this plan and plan B (`devFurniture.ts`), which drops a sectional on the room centre to exercise push-out. |
| `VITE_DEV_REMOTES` | **Dev builds only, never deployed:** gated on `import.meta.env.DEV`, so `vite build` drops them even if the var is set on the host (checked: a production build with it set contains no trace of them). Two fake remote players in my zone on scripted paths (a runner on a circle; a walker that pauses and shoots), written into `store.players` at 20 Hz with arrival jitter. Your shots hit them: 3 hits play their death animation, and they respawn after 3 s. The console logs each `fire` payload. |
| `VITE_SOLO` | Lane D's: `net/game.ts` skips the game server. |

Pack v2 is the live pack (Lane A, HANDOFF 22:20). It is committed and served from `apps/web/public/packs/` (listings index, `packs/hou_navidad_01/v2/`, `furniture/v2/`); v1 is no longer published.
- **Local `/.env.local` for v2:** `VITE_PACK_BASE_URL=/packs/packs/hou_navidad_01/v2`, plus `VITE_SOLO=1` to run without the game server. Add `VITE_START_ROOM=hou_236595179.r3` (7014's living room) to skip the block, and `VITE_DEV_PLAN=1` / `VITE_DEV_REMOTES=1` as needed.
- **Hero house:** 7014 Navidad (`hou_236595179`), listed first in the v2 manifest. Render sets `VITE_HERO_LISTING=hou_236595179`.
- **Spawns:** v2's manifest has 8 sidewalk spawns in front of 7014, so the derived spawn (section 12) is only a fallback now.

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
- **Interior looks washed out: fixed (section 17, not checked in a browser).** `interior.glb` meshes now have `material.toneMapped = false`, since their textures are finished photos (Lane A's HANDOFF suggestion). It applies only to the interior.
- **Blocky walls up close:** walls look blocky within about 30 cm. The camera arm pulls in 0.15 m from walls, so this can show indoors.
- **Character and collider don't line up:** the model is 1.3 m with feet on the floor, but ecctrl floats the 1.3 m capsule 0.3 m up, so the collider spans 0.3–1.6 m. Shot hitboxes for remote players should match the model, not the capsule: centre at feet + 0.65 m, which is 0.30 m below the body centre (`FOOT_BELOW_CENTRE_M` − 0.65).
- **7014's doors are unchecked:** the radius is now 0.25 m, so per Lane A's widths every bathroom door in both houses should fit, and 6915 is confirmed headless (section 12). 7014 hasn't been run: its pack isn't on this machine or any branch. Run `apps/web/scripts/door-check.ts` on it once Lane A's publish lands.
- **Jump apex vs ceiling: fixed (section 17, not checked in a browser).** `jumpVel` is now 3.0 m/s (about a 0.46 m apex), down from 4.0 (about 0.8 m). The 1.6 m first-person eye no longer reaches the 2.43 m ceilings at the top of a jump. Crouch still sets it to 0.
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
| `jumpVel` | 3.0 (about 0.46 m apex; section 17) | 3.0 is fine: the eye rises to about 2.1 m | `movement.ts` |

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

## 17. Interior tone mapping off; jumpVel 3

Neither fix has been checked in a browser. Both rest on code and unit tests only.

- **Interior colours:** `world/interiorMaterials.ts` `disableToneMapping(root)` sets `toneMapped = false` (and `needsUpdate`) on every mesh material under the loaded `interior.glb` scene. The materials are `KHR_materials_unlit`, so `MeshBasicMaterial`, with finished photo textures. It runs once per loaded scene, before the first paint, through a new optional `prepare` prop on `GlbOrPlaceholder`. Only `HouseScene`'s interior passes it, so furniture, characters, effects and the block keep tone mapping. `alphaTest` / alpha mask is untouched, so doorways stay cut out. Tests: interior meshes (multi-material and nested included) are switched off, other scenes are unchanged, `alphaTest` is kept, and a second pass is a no-op.
- **Jump height:** `MOVEMENT.jumpVel` is 3.0 (was 4.0), so the jump peaks at about 0.46 m and the first-person eye at about 2.1 m, under 2.43 m ceilings. Crouch still sets `jumpVel` to 0. No test pinned the old value.
- **Checks:** typecheck, lint and 227 tests pass. A production build with `VITE_DEV_PLAN`, `VITE_START_ROOM` and `VITE_DEV_REMOTES` set contains none of `dev plan`, `p_dev_b`, `dev-remote`, `VITE_START_ROOM` or `__laneC`.

## 18. Furniture keyed on content, not array identity

**The bug:** `net/game.ts` (Lane D) writes `houses: j.houses` from Colyseus `toJSON()` on every state patch. So `store.houses[zone].objects` is a new array about 20 times a second per connected player, even when the furniture hasn't changed (Lane B's 18:12 HANDOFF line to Lane D). FurnitureLayer memoized `objects` and `rects` on array identity, and its layout effect ran on `[planId, zone, rects]`. So footprints, `houseSpawns` + `setSpawns`, and the local player's push-out/teleport all re-ran on every patch. In multiplayer that would teleport a player standing next to furniture in small jumps, and churn indoor spawns.

**Fixed on the Lane C side**, so it holds whatever Lane D does:
- **`world/furnitureKey.ts`:** `furnitureKey(house)` builds a content key from `plan_id` plus each object, sorted by `instance_id`, as `instance_id|asset_id|x|y|z|rot_deg`. Positions are rounded to 1 mm and rotation to 0.1°, so float noise isn't a change.
- **`world/useFurniturePlan.ts`:**
  - `useStableFurniture(house)` keeps the objects array from the first render with a given key, and only replaces it when the key changes.
  - `useFurnitureLayout` is FurnitureLayer's former layout effect (spawns, then push-out moving the real body), now keyed on the content key, zone, rooms and footprints (`rects`, so a catalog change re-runs it too).
- **FurnitureLayer:** uses both hooks. It remounts its furniture group on the content key instead of `plan_id`, so any change tears down the old RigidBodies with no ghost colliders, including a re-furnish that reuses a `plan_id`. An identical patch never remounts.
- **Constraint:** the effect also depends on `rooms` by identity. `HouseScene` memoizes `rooms` per house, so that holds today; passing a fresh array there would bring the per-patch re-runs back.

**Tests:**
- `furnitureKey.test.ts`: same content in a new array, reordered, or below-noise float changes give the same key. A moved, rotated, swapped, added or removed object, or the same `plan_id` with different objects, gives a different key.
- `useFurniturePlan.test.tsx`: renders the hooks with react-dom, with a fake local player inside the fixture sofa.
  - **50 identical-content new arrays:** push-out and `setSpawns` run once, the player is pushed out once, and the objects array keeps one identity.
  - **A real change (the sofa moved, same `plan_id`):** they run exactly once more, and the remount key changes.
- The existing re-furnish and push-out tests still pass.

**Not seen in a browser with two players.** It's verified by unit tests only; the ~20 Hz patch stream from a real game server was simulated, not observed.

## 19. Weapons only in foam mode

**Not seen in a browser.** It's checked by unit tests and the production-build grep only.

**The problem:** the game has two modes, `store.mode = "explore" | "foam"`. The server writes it and it's `"explore"` by default. Lane C never read it, so the blaster and the pool noodle worked even while touring the house.

**What changed:**
- **`player/foamMode.ts`, the one place that decides:**
  - pure rules: `effectiveMode`, `weaponsEnabled`, `heldWeaponProp` and `weaponForKey`;
  - `useFoamMode()` for components;
  - `foamModeNow()` for frame loops and event handlers.
- **Explore mode:**
  - **Blaster and noodle:** don't fire and send no `onFire`. The trigger is dropped every frame while not in foam, so there's no tracer, muzzle flash, impact or `shoot` state from local input. Darts already in flight when the mode flips finish their flight.
  - **HUD:** the crosshair and hit marker are hidden.
  - **Keys 1/2:** ignored.
  - **The model:** holds no weapon. `Character`'s `weapon` prop is `null`, which hides both the blaster and the noodle, for the local player and for remote players alike.
  - **Animations:** unchanged otherwise.
- **Foam mode:** exactly the previous behaviour.
- **Switching mid-session:** takes effect within one frame. The hooks re-render on the store change, and the blaster reads `foamModeNow()` every frame. Nothing remounts: props are toggled in place, and `Player` (with its ecctrl body) stays mounted.
- **Dev only:** with `VITE_SOLO=1` there's no server, so the mode never changes. In a dev build with `VITE_SOLO=1`, **F** toggles a local override between explore and foam (`world/DevModeToggle.tsx`). It's gated by `DEV_MODE_TOGGLE_ON` in `world/devFlags.ts`, the same way as the other dev switches. A production build with `VITE_DEV_PLAN`, `VITE_START_ROOM`, `VITE_DEV_REMOTES` and `VITE_SOLO` all set contains none of `dev plan`, `p_dev_b`, `dev-remote`, `VITE_START_ROOM`, `__laneC`, `dev mode toggle`, `DEV_MODE_TOGGLE` or `DevModeToggle`.

**Unit tests (10 new):**
- **`foamMode.test.ts`:**
  - weapons off in explore and on in foam;
  - `foamModeNow()` following `store.mode` live;
  - the dev override taking priority, and being ignored when the toggle is off;
  - keys 1/2 picking weapons in foam and ignored in explore;
  - the held prop being `null` in explore and the selected weapon in foam.
- **`characterRig.test.ts`:** on the real model, explore hides both props and foam shows the selected one.

## 20. No firing while eliminated

While the server has my `Player.alive` false (the 3 s before respawn), the blaster and noodle do nothing and nothing is sent; the crosshair and hit marker are hidden. Keys 1/2 still switch weapons, so you respawn holding what you picked. Holding the trigger through a respawn does not auto-fire; press again.

- `player/foamMode.ts`: `isAlive(meId, players)` (pure), `canAttackNow(foam, alive)`, `meAliveNow()` for frame loops, `useMeAlive()` for components. No id yet (SOLO, before join) or my entry missing counts as alive, so a missing server never locks the trigger.
- `player/Blaster.tsx`: the existing per-frame gate is now `canAttackNow(foamModeNow(), meAliveNow())`. `player/Crosshair.tsx` hides on the same gate.
- Lane D's respawn path (`world/RespawnWatcher.tsx`, `world/respawn.ts`, `Player.tsx` elimination handling) is untouched.
- Tested: `foamMode.test.ts` (no id, missing entry, alive, dead and respawn, another player's death, the 4-row gate table, keys 1/2 while dead). Not yet seen in a browser.

## 21. Weapon hotbar

A two-slot weapon bar in the style of a battle-royale hotbar, bottom centre: **1** Foam blaster, **2** Pool noodle. Each slot is a 64 px rounded square with the key number top-left, an icon and a short name under it. The selected slot gets a yellow border, 1.12x scale and full opacity; the other is dimmed. Changes ease over 120 ms.

- **Files:** `player/WeaponBar.tsx` (DOM overlay, mounted next to `<Crosshair />` in `world/World.tsx`), `player/hotbar.ts` (pure logic), `player/weaponIcons.tsx`, `player/attackClock.ts`.
- **Slots** come from `weapons.ts` `WEAPON_KEYS`, so they can't drift from the keys. Names are a `Record<Weapon, string>`, so a new weapon fails typecheck until it's named.
- **Cooldown sweep:** after an attack, a dark shade over the selected slot shrinks so the slot clears from the bottom up, over that weapon's interval (`WEAPON_RULES`: blaster 0.25 s, noodle 0.8 s). Same rule as Blaster's `canAttack`. Blaster now calls `markAttack(weapon)` (a tiny zustand store, `attackClock.ts`) next to its own `lastShotAt`; its logic is otherwise unchanged. The bar animates with rAF only while a cooldown runs.
- **Visibility** (`barView`): only in foam mode, whether or not the pointer is locked. Greyed (40%, grayscale) while eliminated (`useMeAlive`). Hidden while a text input, textarea or contenteditable has focus, so it doesn't cover the chat panel. `pointer-events: none` throughout. Scales to 0.75 below 700 px wide.
- **Icons:** original inline SVGs (orange toy blaster with a dart, bendy green noodle tube). No logos, not traced from the kit or any product. CC0, recorded in the `weaponIcons.tsx` header (`public/characters/CREDITS.md` lists model files only).
- **Tested** (`hotbar.test.ts`, `WeaponBar.test.tsx`): slot order from the keys, highlight per weapon, explore hidden / foam shown / dead dimmed, typing hides, cooldown fraction at t = 0, half and full for both weapons, sweep only on the selected slot. **Not yet seen in a browser:** the Chrome extension wasn't connected, so the F / 1 / 2 / shot check is still to do. The dev server compiles and serves every new module.

## 22. Pack v2 block: door heights, block tone mapping, baked sun

Lane A's notes 1 to 3 (HANDOFF 22:20). Branch `lane-c/block-v2`.

- **Door heights.** `Listing.world.position.y` is now the block ground at each front door (7014 0.348 m, 6915 0.103 m).
  - `houseToBlock` / `blockToHouse` already added and removed `position.y`, so `Door.block`, `exitTo` and `deriveSpawn` were already at ground height.
  - What changed: the landing is explicit. `world/landing.ts` `bodyCentreAt(floor)` = floor + `FOOT_BELOW_CENTRE_M` (0.95) + `SPAWN_DROP_M` (0.05). It replaces World.tsx's untested 1 m lift (the same 1.0 m total today, but now tied to the capsule).
  - The block door marker's ring and label now add the door's local y (they sat on the group at `position.y` already).
  - The door trigger compares XZ only, so a raised ground can't stop it firing. A test stands a body on the raised ground and checks it does fire.
- **Tested with the real v2 listings index and rooms.json** (`world/landing.test.ts`):
  - 7014: door local y −0.003, so `exitTo` y = 0.345 and the body lands at 1.345.
  - 6915: door local y 0.004, so `exitTo` y = 0.107 and the body lands at 1.107.
  - Entering lands on the room `floor_y` (−0.003 / 0.004) in the house frame.
  - `door-check.ts` on both v2 houses exits 0. Closets, 7014's kitchen and dining room, and 6915's kitchen are unreachable, the same as v1 (byte-for-byte the same houses).
- **Block tone mapping.** `disableToneMapping(root, { only })`, plus `disableUnlitToneMapping`, which switches off only `MeshBasicMaterial`. It's passed as BlockScene's render `prepare`. v2's `render.glb` has 37 materials: 19 unlit (ground, paving, curbs, far-zone tiles) are switched off; 18 lit (roofs, walls, trim, fences, bark, leaves) keep tone mapping. Unit-tested on a stand-in scene.
- **Sun.** `world/sun.ts` `SUN_DIR_BLOCK` = (−0.355, 0.541, 0.762), azimuth 205°, elevation 32.8°. A test checks the vector against the angles (+X east, +Z south).
  - `world/SunLight.tsx` replaces World.tsx's fixed light: intensity 1.2 kept, light at target + dir × 60 m, target = the local player's body.
  - Its shadow box is 60 × 60 m around the player (2048² map) and follows them every frame at `SUN_PRIORITY` −5. That's after physics and the camera, never positive.
  - Indoors the same sun is rotated into the house frame (`sunDirInHouse`). Sky's `sunPosition` uses the same direction. Ambient 0.45 is unchanged.
- **Not seen in a browser:** the landing on the real `collision.glb` lawn, the unlit block's look with tone mapping off, live shadows lining up with the baked ones, and shadow-edge shimmer as the box follows the player (no texel snapping yet).
- **Trees (note 4), not done:** the 168 live oaks are 9 meshes as about 340 separate draw calls. Make an `InstancedMesh` per variant only if the frame rate drops on weaker laptops. Lane A saw 60 fps on an M-series laptop.

## 23. Openable interior doors

**Seen in a browser, 7014 (`hou_236595179`, pack v2; v3's house files are byte-identical) only:** E opens and closes the r3 → r9 door. A closed door blocks walking, and an open one lets you through. Nothing else below has been seen in a browser.

- **Why:** doors that were closed during the Matterport scan are baked into `collision.glb` as solid wall boxes, so you couldn't get through them.
- **Split (`world/doorSplit.ts`, pure):**
  - `extractWallBoxes(scene)` reads the `walls` mesh (8 vertices per box).
  - `splitClosedDoors(boxes, rooms, frontDoor)` cuts each box that spans a `rooms.json` door opening into the solid pieces around the openings, plus a lintel from y 2.03 up. Several doors on one wall are handled.
  - The front door (the opening nearest `Listing.world.door`) stays solid: the zone trigger handles it.
  - Doors reported by both rooms are deduplicated.
  - 7014: 64 boxes, 7 opened doors. 6915 is covered by unit tests only.
- **Colliders (`world/HouseScene.tsx`):** `collision.glb` is still one trimesh, minus the `walls` node (`skipNodes`). The split boxes are `CuboidCollider`s (`ZONE_COLLIDER`). It warns once if `collision.glb` has no `walls` mesh. Placeholder collision (no pack, fixture mode) has no openable doors.
- **Doors (`world/HouseDoors.tsx`):**
  - Closed: a 2 m `CuboidCollider` fills the gap.
  - The leaf is an off-white box hinged at point `a`. It swings 90° over 0.3 s with smoothstep easing. Visual only.
- **State (`world/doorState.ts`):** doors are kept per listing id. `HouseDoors` sets them on mount and clears them (with their open flags) on unmount, for that listing only. Readers use `doorsFor(state, zone)`. There is no zone subscription: the old reset-on-zone-change could wipe the list if a zone write landed after the doors registered.
- **Prompt (`player/DoorPrompt.tsx`):**
  - "E  Open door" / "E  Close door" shows within 1.5 m of a door midpoint (XZ, house-local).
  - E toggles the door. It won't close while you're within 0.4 m of the door line.
  - It's ignored while typing.
- **Dev probe:** `window.__laneC.doors()` returns `{zone, pos, activeDoors, open, source, packBase}`. `source: null` means `collision.glb` never loaded; `wallBoxes: 0` means it has no `walls` mesh. It's dev-only and absent from the production build (section 14 grep).
- **Gotcha:** with no `VITE_PACK_BASE_URL`, the client runs on the contracts fixture: there's no `collision.glb`, so there are no doors. Locally, use `VITE_PACK_BASE_URL=/packs/packs/hou_navidad_01/v3` (v2 was replaced by v3 on main).
- **Tests:** `doorSplit.test.ts` (7014 and 6915 real files from pack v3, synthetic two-door wall), `doorInteract.test.ts`, `doorState.test.ts` (a zone write after the doors register, zone flapping, per-listing clear, re-split keeps open flags).
- **Not seen in a browser:**
  - 7014's other six doors.
  - Every 6915 door.
  - The leaf's swing direction and look.
  - The close guard at 0.4 m.
  - Walking through a door that has more than one door on its wall.
  - Darts against the door collider.
  - Door state after leaving and re-entering the house.
  - Multiplayer: door state is local to each client, not shared through the game server.

## 24. Other players' shots from store.fx

**Not seen in a browser.** It's checked by unit tests and the production-build grep only. In SOLO nothing writes `store.fx`, and the `VITE_DEV_REMOTES` fakes don't emit fx, so it needs two real clients on the game server to see it.

- **Source:** Lane D's `net/broadcasts.ts` keeps the latest 8 `fx` broadcasts in `store.fx` (`{id, kind, origin, dir, at}`, ids monotonic, newest last). The server sends `{kind: weapon, origin, dir}` to everyone but the shooter, in every zone, with no shooter id or zone (`NeighborhoodRoom.ts:159`).
- **`world/remoteFx.ts` (pure):**
  - `fxInMyZone(fx, bodies)`: keep an event only if its origin is within `FX_MATCH_M` (1.5 m, in 3D) of a remote body centre drawn in my zone this frame. A shot from another zone is in another frame, so it matches nobody and is dropped.
  - `seenIds` / `takeUnseen`: each id plays once. Ids already in the list on mount are marked seen, so a reload or remount never replays old shots. The seen set is trimmed to ids still in the list.
  - `unitDir`, `swooshYaw`.
- **Bodies:** `RemotePlayers` now also publishes the interpolated body centres of every remote in my zone (alive or not) via `remoteTargets.setRemoteBodies` / `remoteBodies()`, from the same samples it draws and builds hitboxes from.
- **`world/RemoteShots.tsx`** (mounted next to `RemotePlayers` in `World.tsx` Zones; `REMOTE_FX_PRIORITY` 0, after remotes at −20). It's named `RemoteShots`, not `RemoteFx`, because macOS's case-insensitive filenames would make `RemoteFx.tsx` collide with `remoteFx.ts`.
  - **blaster:** a Rapier ray from the origin along dir, `SHOT_QUERY` (zone + furniture), 40 m (`BLASTER.rangeM`). Tracer to the hit point (or 40 m), plus an impact puff on a hit. Pools: 8 tracers, 4 puffs. No muzzle flash.
  - **noodle:** one pooled flat ring segment, 60° wide, 1.1–1.5 m from the head at head height, centred on dir. It fades over 0.25 s and grows a little. No physics.
  - No mode gating: fx only arrives in foam mode.
- **Shared effects: `player/shotFx.tsx`.** `useTracerPool(n)` and `usePuffPool(n)` were pulled out of `Blaster.tsx`, which now uses them (12 tracers, 1 puff, same as before). The tracer streak, speed, puff timing and look are unchanged.
- **Tests (`remoteFx.test.ts`, 14):** in my zone, another zone, no remotes, 1.4 m vs 1.6 m, 3D distance, several remotes. Once per id: mount marks all seen, new ids once in order, the same events in a new array aren't replayed, an 8-entry rolling cap over 30 events plays each exactly once with the set bounded, a burst between frames. Dir normalization and the swoosh yaw.
- **Known env-dependent test (already on main):** `foamMode.test.ts` "the override is ignored unless the dev toggle is on" fails when `/.env.local` sets `VITE_SOLO=1`, because Vitest loads it. All 327 tests pass with that file moved aside.
- **Not seen in a browser:** the remote tracer and puff, the swoosh's orientation and look, and the match tolerance on a running shooter. The body is drawn 100 ms behind, and the head is 0.65 m above the body centre, so a shooter at 6 m/s is about 0.9 m off, still inside 1.5 m.

## 25. E at front doors

**Not seen in a browser.** It's checked by unit tests and the production-build grep only. The Chrome extension wasn't connected, and the dev server compiles and serves every changed module.

- **What it does:** E is a shortcut for the existing zone switch, and walking into a door works exactly as before.
  - On the block, within 2.0 m (XZ, `FRONT_DOOR_KEY_M`) of any `pack.doors[].block`, the prompt reads "E  Enter <address>": the address comes from the listings index, falling back to the listing id.
  - Indoors, within 2.0 m of that house's `door.local`, it reads "E  Leave house".
- **One code path (`world/pack.ts`):** `crossDoor(state, door)` is the only zone switch. It goes to enterTo from the block and to exitTo from the house, and it disarms the trigger. `stepZone` (walk-in) and `pressFrontDoor` (E) both call it, so E lands exactly where walking in does and can't bounce: the trigger re-arms only after you walk 1.8 m clear. E works while the trigger is disarmed (a key press is deliberate, and `e.repeat` is ignored). `frontDoorInRange` picks the door.
- **Wiring:** World's `Zones` publishes `pack.doors`, the addresses and a `press()` closure (over its `zoneState` ref and `goTo`) to `world/frontDoorState.ts`, a small zustand store, because `DoorPrompt` is a DOM sibling of the canvas. It is cleared on unmount.
- **Precedence (`doorInteract.ts`):** `doorTargetAt` / `pickDoorAction`. An interior door (1.5 m) and a front door (2.0 m) can both be in range: the nearer wins, and the interior door wins a tie. Interior doors never count on the block. E is ignored while typing (`isTextEntry`). `player/DoorPrompt.tsx` keeps its overlay style and handles both kinds.
- **Tests (`world/frontDoor.test.ts`, real v3 listings index, rooms.json and 7014's collision.glb):**
  - E 1.5 m from 7014's block door enters at enterTo in r3. The walk-in trigger alone doesn't fire there.
  - E at the door gives the same result as walking in, both ways.
  - E 1.9 m inside goes out to exitTo. At 2.1 m nothing happens, outside or inside.
  - 40 steps standing at the landing don't bounce.
  - Only the current house's door counts.
  - The prompt text, including the id fallback.
  - Precedence: the pure table; a real interior door away from the front door; a synthetic interior door beside the front door (nearer, farther, tie). 7014's nearest real interior door is 2.7 m from its front door, so the two never compete there.
- **Not seen in a browser:** the prompt on the block, entering and leaving with E, and the overlap with an interior door.
