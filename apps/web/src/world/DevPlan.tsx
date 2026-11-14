// Lane C, dev only: VITE_DEV_PLAN=1 furnishes the current house from the
// contract fixture plan, so the furniture path runs end to end before Lane B's
// planner and Lane A's catalog exist.
//
//  - Plan A is contracts/fixtures/placement_plan.json. It is authored for the
//    fixture room (hou_000123.r1); in any other house it is retargeted to the
//    target room (retargetPlan) and pieces that would not fit are dropped with
//    a warning.
//  - Plan B (devFurniture.ts) is a second layout. P swaps A <-> B: the
//    re-furnish path, with a sectional landing where the dev start puts you.
//  - Target room: the VITE_START_ROOM room if it is in this house, else the
//    house's living room, else its largest room.
//
// It writes store.houses[zone] through the store's own set(), as Lane D's
// network code does; with a game server connected, the next state patch
// replaces it. store.ts is not changed.

import { useEffect, useMemo } from "react";
import { useRapier } from "@react-three/rapier";

import type { PlacementPlan, Room } from "../contracts.gen";
import { useStore } from "../store";
import { DEV_PLAN_B } from "./devFurniture";
import { FIXTURE_RAW } from "./fixtures";
import { retargetPlan } from "./furniturePlan";
import { warnOnce, type LoadedPack } from "./pack";
import { FURNITURE_COLLIDER, SHOT_QUERY } from "./collisionGroups";
import { useIndoorSpawns } from "./indoorSpawns";
import { localPlayer } from "../player/playerControl";

import { START_ROOM } from "./devFlags";
const SWAP_KEY = "KeyP";

const FIXTURE_PLAN = JSON.parse(FIXTURE_RAW.plan) as PlacementPlan;
const FIXTURE_ROOM = JSON.parse(FIXTURE_RAW.room) as Room;

function targetRoom(rooms: Room[]): Room | undefined {
  return (
    rooms.find((r) => r.id === START_ROOM) ??
    rooms.find((r) => r.label === "living_room") ??
    [...rooms].sort((a, b) => b.area_m2 - a.area_m2)[0]
  );
}

function planFor(plan: PlacementPlan, listingId: string, rooms: Room[], pack: LoadedPack): PlacementPlan | null {
  const to = targetRoom(rooms);
  if (!to) return null;
  if (to.id === plan.room_id) return plan; // the fixture house: already in place
  const { plan: moved, misfits } = retargetPlan(plan, FIXTURE_ROOM, to, listingId, pack.catalog);
  if (misfits.length) {
    warnOnce(
      `devplan-misfit:${plan.plan_id}:${to.id}`,
      `[dev plan] ${plan.plan_id}: ${misfits.join(", ")} would not fit in ${to.id}; dropped`,
    );
  }
  return { ...moved, objects: moved.objects.filter((o) => !misfits.includes(o.instance_id)) };
}

export function DevPlan({ pack, zone }: { pack: LoadedPack; zone: string }) {
  const rooms = pack.roomsByHouse[zone];
  const plans = useMemo(() => {
    if (zone === "block" || !rooms?.length) return null;
    const a = planFor(FIXTURE_PLAN, zone, rooms, pack);
    const b = planFor(DEV_PLAN_B, zone, rooms, pack);
    return a && b ? [a, b] : null;
  }, [pack, zone, rooms]);

  useEffect(() => {
    if (!plans) return;
    let which = 0;
    const apply = () => {
      const p = plans[which];
      const s = useStore.getState();
      s.set({
        houses: {
          ...s.houses,
          [zone]: { plan_id: p.plan_id, objects: p.objects.map((o) => ({ ...o, by_player: "dev" })) },
        },
      });
      console.info(`[dev plan] ${zone}: ${p.plan_id} (${p.objects.length} pieces) in ${p.room_id}. P swaps.`);
    };
    apply();
    const onKey = (e: KeyboardEvent) => {
      const el = document.activeElement;
      if (e.code !== SWAP_KEY || e.repeat || el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement) return;
      which = 1 - which;
      apply();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [plans, zone]);

  return null;
}

/**
 * Dev only: window.__laneC, for checking the physics side from the browser
 * console (or an automated browser): collider counts, the live player body,
 * the current plan, and a dart raycast against zone + furniture collision.
 */
export function DevProbe() {
  const { world, rapier } = useRapier();
  useEffect(() => {
    const furnitureGroups = FURNITURE_COLLIDER;
    const probe = {
      colliders: () => {
        let total = 0;
        let furniture = 0;
        world.forEachCollider((c) => {
          total++;
          if (c.collisionGroups() === furnitureGroups) furniture++;
        });
        return { total, furniture };
      },
      furnitureBoxes: () => {
        const out: { x: number; y: number; z: number; hx: number; hy: number; hz: number }[] = [];
        world.forEachCollider((c) => {
          if (c.collisionGroups() !== furnitureGroups) return;
          const t = c.translation();
          const he = c.halfExtents();
          out.push({ x: t.x, y: t.y, z: t.z, hx: he.x, hy: he.y, hz: he.z });
        });
        return out;
      },
      player: () => localPlayer()?.getPos() ?? null,
      teleport: (x: number, y: number, z: number) => localPlayer()?.teleport([x, y, z]),
      plan: () => {
        const s = useStore.getState();
        const h = s.houses[s.me.zone];
        return h ? { plan_id: h.plan_id, objects: h.objects.length } : null;
      },
      spawns: () => useIndoorSpawns.getState().byHouse[useStore.getState().me.zone] ?? [],
      /** First zone/furniture hit along a ray: { dist, furniture } or null. */
      dart: (o: [number, number, number], d: [number, number, number], max = 40) => {
        const hit = world.castRay(
          new rapier.Ray({ x: o[0], y: o[1], z: o[2] }, { x: d[0], y: d[1], z: d[2] }),
          max,
          true,
          undefined,
          SHOT_QUERY,
        );
        return hit ? { dist: hit.timeOfImpact, furniture: hit.collider.collisionGroups() === furnitureGroups } : null;
      },
    };
    (window as unknown as { __laneC: typeof probe }).__laneC = probe;
    return () => {
      delete (window as unknown as { __laneC?: typeof probe }).__laneC;
    };
  }, [world, rapier]);
  return null;
}
