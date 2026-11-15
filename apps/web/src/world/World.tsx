// Lane C: the scene. Loads the pack, renders the zone the local player is in
// (the block, or one house), and runs the door trigger that switches zones.
//
// WASD / arrows to move, Shift to run, Space to jump. Drag to orbit the
// camera, wheel to zoom. Walk into a lit door to go inside; walk back to it
// to leave. E within 2 m of a front door does the same (player/DoorPrompt).
//
// Zones (AGENTS.md section 8): me.zone is "block" or a listing_id, and me.pos
// is in that zone's own frame. Only one zone's geometry is mounted at a time.
// net/game.ts sees me.zone change and sends enter_zone.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Canvas } from "@react-three/fiber";
import { Physics } from "@react-three/rapier";
import { Sky } from "@react-three/drei";

import { useStore } from "../store";
import { Crosshair } from "../player/Crosshair";
import { DoorPrompt } from "../player/DoorPrompt";
import { WeaponBar } from "../player/WeaponBar";
import { Player } from "../player/Player";
import { BlockScene } from "./BlockScene";
import { PHYSICS_PRIORITY } from "./frameOrder";
import { HouseScene } from "./HouseScene";
import {
  blockSpawn,
  devStart,
  pressFrontDoor,
  roomIdAt,
  stepZone,
  type LoadedPack,
  type Vec3,
  type ZoneState,
} from "./pack";
import { usePack } from "./usePack";
import { useFrontDoors } from "./frontDoorState";
import { bodyCentreAt } from "./landing";
import { SUN_DIR_BLOCK, sunPosition } from "./sun";
import { SunLight } from "./SunLight";
import { DevPlan, DevProbe } from "./DevPlan";
import { DevModeToggle } from "./DevModeToggle";
import { DevRemotes } from "./DevRemotes";
import { DEV_REMOTES_ON, devFire } from "./fakeRemotes";
import { RemotePlayers } from "./RemotePlayers";
import { RemoteShots } from "./RemoteShots";
import { RespawnWatcher } from "./RespawnWatcher";
import { type FocusState, focusArrival, stepFocus } from "./focusTeleport";
import { sendFire } from "../net/game";
import { remoteHitboxes } from "./remoteTargets";
import { DEV_PLAN_B, withDevCatalog } from "./devFurniture";
import { DEV_MODE_TOGGLE_ON, DEV_PLAN_ON, START_ROOM } from "./devFlags";
import { FIXTURE_RAW } from "./fixtures";
import type { Asset, PlacementPlan } from "../contracts.gen";

/** Sky's sun, far out along the baked sun direction (it only reads the direction). */
const SKY_SUN = sunPosition([0, 0, 0], SUN_DIR_BLOCK, 100);
/** A floor point (spawn, door landing, dev room) to the body centre: landing.ts. */
const lift = bodyCentreAt;

/**
 * Dev switch: VITE_START_ROOM=<room_id> starts inside that room's house at the
 * room's centroid instead of on the block. Unset in production.
 */

/** Where the page starts: the dev room if set and valid, else the block's sidewalk. */
function initialPlacement(pack: LoadedPack): { zone: string; pos: Vec3; roomId: string | null } {
  return (START_ROOM ? devStart(pack, START_ROOM) : null) ?? { zone: "block", pos: blockSpawn(pack.manifest), roomId: null };
}

export function World() {
  const pack = usePack();
  return (
    <>
      <Canvas shadows camera={{ position: [0, 3.4, 9], fov: 50 }}>
        {/* Sky and live sun both from the baked sun (sun.ts, Lane A HANDOFF 22:20). */}
        <Sky sunPosition={SKY_SUN} />
        <ambientLight intensity={0.45} />
        {/* Fixed 1/60 s step with interpolation (the defaults) suits 60-144 Hz displays;
            the camera must follow the interpolated body, see CameraArm. */}
        <Physics updatePriority={PHYSICS_PRIORITY} timeStep={1 / 60} interpolate>
          {pack.status === "ready" ? <Zones pack={pack.pack} /> : null}
        </Physics>
      </Canvas>
      <Crosshair />
      <DoorPrompt />
      <WeaponBar />
      <PackBadge status={pack} />
    </>
  );
}

/**
 * Every asset the dev plans use, so the dev catalog can stand in for each. A
 * function, not a module constant, so a production build (DEV_PLAN_ON false)
 * never runs it and drops it with the rest of the dev plan.
 */
const devPlanAssetIds = () =>
  [...(JSON.parse(FIXTURE_RAW.plan) as PlacementPlan).objects, ...DEV_PLAN_B.objects].map((o) => o.asset_id);

function Zones({ pack: loaded }: { pack: LoadedPack }) {
  // VITE_DEV_PLAN: dev stand-ins under the pack's own catalog (see DevPlan.tsx).
  const pack = useMemo(
    () =>
      DEV_PLAN_ON
        ? withDevCatalog(loaded, JSON.parse(FIXTURE_RAW.asset) as Asset, devPlanAssetIds())
        : loaded,
    [loaded],
  );
  const setMe = useStore((s) => s.setMe);
  const [start] = useState(() => initialPlacement(pack));
  // Lane D: where a foam-fight respawn lands on the block (indoors uses indoorSpawns).
  const blockSpawn = useMemo(() => (start.zone === "block" ? lift(start.pos) : null), [start]);
  // The zone we render. Owned here and copied into the store, not read back from
  // it: on the first render the store still holds its default ("block"), and
  // reading that would mount the block for a frame and fetch its meshes.
  const [zone, setZone] = useState(start.zone);
  const [spawn, setSpawn] = useState<Vec3>(() => lift(start.pos));
  // Camera yaw for the next Player mount (focus teleport faces the house); undefined = default.
  const [spawnYaw, setSpawnYaw] = useState<number | undefined>(undefined);
  // Bumped on every zone change: remounts the Player at the new spawn, in the new frame.
  const [epoch, setEpoch] = useState(0);
  const zoneState = useRef<ZoneState>({ zone: start.zone, armed: true });

  // Put the store where the Player starts, whatever it held before the pack
  // loaded. The Player already mounts at that spawn (initial state above).
  useEffect(() => {
    zoneState.current = { zone: start.zone, armed: true };
    setMe(start);
  }, [start, setMe]);

  // Put the player in `zone` at `at` (that zone's frame, floor height): store first,
  // then remount the Player there. Used by the door trigger and the focus teleport.
  const goTo = useCallback(
    (to: string, at: Vec3, yaw?: number) => {
      const rooms = pack.roomsByHouse[to] ?? [];
      setMe({ zone: to, pos: at, roomId: to === "block" ? null : roomIdAt(at, rooms) });
      setZone(to);
      setSpawn(lift(at));
      setSpawnYaw(yaw);
      setEpoch((e) => e + 1);
    },
    [pack, setMe],
  );

  // E at a front door (player/DoorPrompt): the same switch as walking in, from
  // the same zone state, so the trigger stays disarmed after it and can't bounce.
  useEffect(() => {
    const addresses: Record<string, string> = {};
    for (const l of pack.listings) if (l.address) addresses[l.id] = l.address;
    const press = () => {
      const { state, change } = pressFrontDoor(zoneState.current, useStore.getState().me.pos, pack.doors);
      if (!change) return false;
      zoneState.current = state;
      goTo(change.zone, change.spawn);
      return true;
    };
    useFrontDoors.getState().set(pack.doors, addresses, press);
    return () => useFrontDoors.getState().set([], {}, null);
  }, [pack, goTo]);

  // Door trigger and room tracking, on every position the Player writes (20 Hz).
  useEffect(
    () =>
      useStore.subscribe((s, prev) => {
        if (s.me.pos === prev.me.pos) return;
        const { state, change } = stepZone(zoneState.current, s.me.pos, pack.doors);
        zoneState.current = state;
        if (change) {
          goTo(change.zone, change.spawn);
          return;
        }
        if (s.me.zone !== "block") {
          const roomId = roomIdAt(s.me.pos, pack.roomsByHouse[s.me.zone] ?? []);
          if (roomId !== s.me.roomId) setMe({ roomId });
        }
      }),
    [pack, setMe, goTo],
  );

  // Lane D: focus teleport (HANDOFF lane-b 16:45). When the agent's focus_listing
  // changes MY beacon, land outside that house's door on the block, facing it.
  // The first value seen is a starting point, not a jump (focusTeleport.ts).
  const focusState = useRef<FocusState>({ seen: false, listing: undefined });
  useEffect(
    () =>
      useStore.subscribe((s) => {
        const id = s.me.id;
        if (!s.connected || !id) return;
        const { state, goTo: listing } = stepFocus(focusState.current, s.focus[id]);
        focusState.current = state;
        if (!listing || s.players[id]?.alive === false) return;
        const door = pack.doors.find((d) => d.listing_id === listing);
        if (!door) return; // not a house on this block
        const arrival = focusArrival(door, 0, pack.manifest.spawns as Vec3[]);
        // Right outside the door: the trigger stays disarmed until we walk clear.
        zoneState.current = { zone: "block", armed: false };
        goTo("block", arrival.pos, arrival.yaw);
      }),
    [pack, goTo],
  );

  const inHouse = zone !== "block" && pack.manifest.houses.some((h) => h.listing_id === zone);
  return (
    <>
      {inHouse ? <HouseScene pack={pack} listingId={zone} /> : <BlockScene pack={pack} />}
      <SunLight headingDeg={inHouse ? (pack.doors.find((d) => d.listing_id === zone)?.world.heading_deg ?? 0) : 0} />
      {DEV_PLAN_ON ? <DevPlan pack={pack} zone={zone} /> : null}
      {import.meta.env.DEV ? <DevProbe /> : null}
      {DEV_MODE_TOGGLE_ON ? <DevModeToggle /> : null}
      <RemotePlayers zone={zone} />
      <RemoteShots />
      <RespawnWatcher zone={zone} blockSpawn={blockSpawn} />
      {DEV_REMOTES_ON ? <DevRemotes pack={pack} zone={zone} /> : null}
      {/* getHitboxes: remote players as drawn this frame. onFire: Lane D sends `fire`; in dev-remotes mode, the stand-in. */}
      <Player
        key={epoch}
        spawn={spawn}
        spawnYaw={spawnYaw}
        zone={zone}
        getHitboxes={remoteHitboxes}
        onFire={DEV_REMOTES_ON ? devFire : sendFire}
      />
    </>
  );
}

/** Small corner tag so a placeholder world reads as intentional, not broken. */
function PackBadge({ status }: { status: ReturnType<typeof usePack> }) {
  const zone = useStore((s) => s.me.zone);
  const roomId = useStore((s) => s.me.roomId);
  let text: string;
  if (status.status === "loading") text = "Loading world pack…";
  else if (status.status === "error") text = `World pack failed: ${status.error}`;
  else {
    const { manifest, source } = status.pack;
    const where = zone === "block" ? "on the block" : `inside ${zone}${roomId ? ` · ${roomId}` : ""}`;
    text = `${manifest.block_id} v${manifest.version} · ${source === "fixture" ? "fixture world, placeholder geometry" : "pack"} · ${where}`;
  }
  return (
    <div
      style={{
        position: "absolute",
        left: 16,
        bottom: 16,
        padding: "6px 10px",
        borderRadius: 6,
        background: "rgba(16,20,26,0.75)",
        color: "#e8eaed",
        fontSize: 12,
        pointerEvents: "none",
      }}
    >
      {text}
    </div>
  );
}
