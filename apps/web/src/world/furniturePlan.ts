// Lane C: pure helpers around placement plans. No React, no three.js.
//
//  - footprints: plan objects + catalog -> Rects (what collides, what blocks spawns)
//  - fits: is a footprint inside its room?
//  - retargetPlan: move a plan authored for one room into another (dev only:
//    the contract fixture plan is for the fixture room, not the hero house)
//  - houseSpawns: indoor spawn points from the free floor after a plan
//    (AGENTS.md section 11: >= 0.6 m from walls and furniture, >= 3 m apart)

import type { Asset, PlacementPlan, Point2, Room } from "../contracts.gen";
import {
  SPAWN_SEPARATION_M,
  pointInPolygon,
  rectCorners,
  rectFromPlacement,
  spawnPoints,
  type Rect,
} from "./geometry.ts";

type PlanObject = PlacementPlan["objects"][number];

/** Footprints of every placed object the catalog knows; unknown assets are skipped. */
export function footprints(objects: PlanObject[], catalog: Record<string, Asset>): Rect[] {
  const out: Rect[] = [];
  for (const o of objects) {
    const a = catalog[o.asset_id];
    if (a) out.push(rectFromPlacement(o, a));
  }
  return out;
}

/** All four corners of the footprint inside the room polygon. */
export function fits(rect: Rect, room: Room): boolean {
  return rectCorners(rect).every((c) => pointInPolygon(c, room.polygon));
}

/** Area-weighted centroid (same as pack.polygonCentroid, kept local to stay dependency-free). */
function centroid(poly: Point2[]): Point2 {
  let a = 0;
  let cx = 0;
  let cz = 0;
  for (let i = 0; i < poly.length; i++) {
    const [x0, z0] = poly[i];
    const [x1, z1] = poly[(i + 1) % poly.length];
    const c = x0 * z1 - x1 * z0;
    a += c;
    cx += (x0 + x1) * c;
    cz += (z0 + z1) * c;
  }
  if (Math.abs(a) < 1e-9) {
    const n = poly.length;
    return [poly.reduce((s, p) => s + p[0], 0) / n, poly.reduce((s, p) => s + p[1], 0) / n];
  }
  return [cx / (3 * a), cz / (3 * a)];
}

/**
 * Move a plan from `from` to `to` by translating it so the rooms' centroids
 * coincide. Rotations and the layout's shape are kept. Also returns the
 * instance_ids whose footprint does not fit inside `to`, so the caller can
 * say so instead of rendering furniture through a wall. Dev use only: real
 * plans come from Lane B's layout planner, already in the right room.
 */
export function retargetPlan(
  plan: PlacementPlan,
  from: Room,
  to: Room,
  listingId: string,
  catalog: Record<string, Asset>,
): { plan: PlacementPlan; misfits: string[] } {
  const [fx, fz] = centroid(from.polygon);
  const [tx, tz] = centroid(to.polygon);
  const dx = tx - fx;
  const dz = tz - fz;
  const objects = plan.objects.map((o) => ({
    ...o,
    x: o.x + dx,
    y: o.y + (to.floor_y - from.floor_y),
    z: o.z + dz,
  }));
  const misfits = objects
    .filter((o) => {
      const a = catalog[o.asset_id];
      return a ? !fits(rectFromPlacement(o, a), to) : false;
    })
    .map((o) => o.instance_id);
  return {
    plan: { ...plan, listing_id: listingId, room_id: to.id, objects },
    misfits,
  };
}

export type Spawn = { roomId: string; point: Point2; floorY: number };

/**
 * Indoor spawn points for a whole house after a plan: per room, the free
 * floor away from walls and furniture (spawnPoints), then a greedy pass so no
 * two spawns in the house are closer than `minSeparationM`. Deterministic, so
 * every client agrees without the server sending them.
 */
export function houseSpawns(
  rooms: Room[],
  furniture: Rect[],
  opts: { perRoom?: number; minSeparationM?: number } = {},
): Spawn[] {
  const perRoom = opts.perRoom ?? 4;
  const minSep = opts.minSeparationM ?? SPAWN_SEPARATION_M;
  const out: Spawn[] = [];
  for (const room of rooms) {
    for (const point of spawnPoints(room, furniture, perRoom, { minSeparationM: minSep })) {
      const clash = out.some(
        (s) => s.floorY === room.floor_y && Math.hypot(s.point[0] - point[0], s.point[1] - point[1]) < minSep,
      );
      if (!clash) out.push({ roomId: room.id, point, floorY: room.floor_y });
    }
  }
  return out;
}
