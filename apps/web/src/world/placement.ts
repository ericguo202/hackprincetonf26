// Lane C: manual furnishing geometry. Pure; no React, no three.js, no Rapier.
// Tested in placement.test.ts.
//
// Given where the crosshair meets the floor, the held asset and the player's
// rotation, planGhost decides the pose the piece would take and whether it
// may be placed there:
//
//   - wall snap: a `wall` or `corner` asset (or a wall-mounted one) near a
//     wall of its room backs onto that wall, front facing into the room.
//     Hold Alt to place freely.
//   - surface: an `on_surface` asset over another piece sits on its top
//     (a TV on a TV stand, a lamp on a nightstand), facing the way it faces.
//   - height: the room's floor_y, plus mount_y_m for wall-mounted assets.
//   - valid: centre in a room on this floor, every footprint corner inside
//     the house's rooms on this floor, nothing solid in front of a door
//     (DOOR_CLEAR_M either side of every door opening: a couch across the
//     front door would trap players), and no solid overlap with another
//     piece (footprints overlap AND height ranges overlap). Rugs and other
//     flat pieces never block and are never blocked.
//
// The game does not check geometry (it has no room polygons), so this is the
// only check a hand-placed piece gets. It is deliberately the same footprint
// model FurnitureLayer's colliders and the indoor spawns use (Rect from
// size_m), so a valid ghost never lands inside another piece's collider.

import type { Asset, Point2, Room } from "../contracts.gen";
import {
  distToSegment,
  pointInPolygon,
  pointInRect,
  rectCorners,
  rectsOverlap,
  type Rect,
} from "./geometry.ts";

/** Within this of a room's floor_y, a room is on the same floor. */
export const SAME_FLOOR_M = 0.3;
/** A wall asset snaps when its back would be within this of a wall. */
export const SNAP_M = 0.45;
/** Gap left between a snapped piece's back and the wall polygon. */
export const WALL_GAP_M = 0.02;
/** Footprints shrink by this on each side before the room and overlap tests (flush is fine). */
export const TOLERANCE_M = 0.03;
/** Thinner than this (a rug, a play mat) never blocks and is never blocked. */
export const FLAT_H_M = 0.05;
/** Keep-clear depth on each side of a door opening. */
export const DOOR_CLEAR_M = 0.6;
/** Highest support an on_surface asset will climb onto, above the floor. */
const MAX_SURFACE_M = 1.6;

export type PieceLike = { instance_id: string; asset_id: string; x: number; y: number; z: number; rot_deg: number };

/** A placed piece as a solid: footprint plus its height range. */
export type Solid = { id: string; name: string; rect: Rect; y0: number; y1: number };

export function solidsOf(objects: PieceLike[], catalog: Record<string, Asset>, excludeId?: string | null): Solid[] {
  const out: Solid[] = [];
  for (const o of objects) {
    const a = catalog[o.asset_id];
    if (!a || o.instance_id === excludeId) continue;
    out.push({
      id: o.instance_id,
      name: a.name,
      rect: { x: o.x, z: o.z, w: a.size_m.w, d: a.size_m.d, rotDeg: o.rot_deg },
      y0: o.y,
      y1: o.y + a.size_m.h,
    });
  }
  return out;
}

export function roomsOnFloor(rooms: Room[], floorY: number): Room[] {
  return rooms.filter((r) => Math.abs(r.floor_y - floorY) < SAME_FLOOR_M);
}

/** The floor the player stands on: the highest room floor at or below the feet. */
export function floorBelow(feetY: number, rooms: Room[]): number {
  let best: number | null = null;
  for (const r of rooms) if (r.floor_y <= feetY + SAME_FLOOR_M && (best === null || r.floor_y > best)) best = r.floor_y;
  return best ?? (rooms.length ? Math.min(...rooms.map((r) => r.floor_y)) : 0);
}

export type Ghost = {
  x: number;
  y: number;
  z: number;
  rot_deg: number;
  room_id: string | null;
  valid: boolean;
  why: string | null;
  snapped: "wall" | "surface" | null;
};

export type GhostInput = {
  /** Where the crosshair meets this floor, house-local [x, z]. */
  point: Point2;
  /** floor_y of the floor being furnished (floorBelow the player). */
  floorY: number;
  asset: Asset;
  rotDeg: number;
  rooms: Room[];
  /** Every other placed piece (solidsOf, without the one being moved). */
  solids: Solid[];
  /** false while Alt is held: no wall snap. */
  snap: boolean;
};

function normDeg(d: number): number {
  const r = ((d % 360) + 360) % 360;
  return r >= 359.95 ? 0 : r;
}

/** rot_deg that turns the asset's front (+Z at 0) to face along (nx, nz). */
export function facingDeg(nx: number, nz: number): number {
  return normDeg((Math.atan2(nx, nz) * 180) / Math.PI);
}

/**
 * The room edge nearest to `p`, with its unit normal pointing into the room.
 * null for a degenerate polygon.
 */
export function nearestWall(p: Point2, room: Room): { foot: Point2; normal: Point2; dist: number } | null {
  const poly = room.polygon;
  let best: { foot: Point2; normal: Point2; dist: number } | null = null;
  for (let i = 0; i < poly.length; i++) {
    const a = poly[i];
    const b = poly[(i + 1) % poly.length];
    const vx = b[0] - a[0];
    const vz = b[1] - a[1];
    const len = Math.hypot(vx, vz);
    if (len < 1e-6) continue;
    const dist = distToSegment(p, a, b);
    if (best && dist >= best.dist) continue;
    const t = Math.max(0, Math.min(1, ((p[0] - a[0]) * vx + (p[1] - a[1]) * vz) / (len * len)));
    const foot: Point2 = [a[0] + t * vx, a[1] + t * vz];
    // Of the two perpendiculars, the one that steps into the room.
    let normal: Point2 = [-vz / len, vx / len];
    const mid: Point2 = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
    if (!pointInPolygon([mid[0] + normal[0] * 0.05, mid[1] + normal[1] * 0.05], poly)) normal = [-normal[0], -normal[1]];
    best = { foot, normal, dist };
  }
  return best;
}

function snapsToWall(asset: Asset): boolean {
  return asset.placement === "wall" || asset.placement === "corner" || (asset.mount_y_m ?? 0) > 0;
}

function roomAtPoint(p: Point2, rooms: Room[]): Room | null {
  return rooms.find((r) => pointInPolygon(p, r.polygon)) ?? null;
}

/** The keep-clear zone of a door opening: its width along the wall, DOOR_CLEAR_M deep on both sides. */
export function doorClearance(a: Point2, b: Point2): Rect {
  const vx = b[0] - a[0];
  const vz = b[1] - a[1];
  // rectCorners sends local +X to (cos, -sin): align it with the opening.
  const rotDeg = (Math.atan2(-vz, vx) * 180) / Math.PI;
  return { x: (a[0] + b[0]) / 2, z: (a[1] + b[1]) / 2, w: Math.hypot(vx, vz), d: 2 * DOOR_CLEAR_M, rotDeg };
}

function shrink(r: Rect, by: number): Rect {
  return { ...r, w: Math.max(0.01, r.w - 2 * by), d: Math.max(0.01, r.d - 2 * by) };
}

export function planGhost(input: GhostInput): Ghost {
  const { asset, solids, snap } = input;
  const { w, d, h } = asset.size_m;
  const floor = roomsOnFloor(input.rooms, input.floorY);
  let [x, z] = input.point;
  let rot = normDeg(input.rotDeg);
  let snapped: Ghost["snapped"] = null;

  let room = roomAtPoint([x, z], floor);
  if (snap && room && snapsToWall(asset)) {
    const wall = nearestWall([x, z], room);
    if (wall && wall.dist < d / 2 + SNAP_M) {
      const back = d / 2 + WALL_GAP_M;
      x = wall.foot[0] + wall.normal[0] * back;
      z = wall.foot[1] + wall.normal[1] * back;
      rot = facingDeg(wall.normal[0], wall.normal[1]);
      snapped = "wall";
      room = roomAtPoint([x, z], floor) ?? room;
    }
  }

  const floorY = room?.floor_y ?? input.floorY;
  let y = floorY + (asset.mount_y_m ?? 0);
  if (asset.placement === "on_surface") {
    let support: Solid | null = null;
    for (const s of solids) {
      if (s.y1 - floorY < FLAT_H_M || s.y1 - floorY > MAX_SURFACE_M) continue;
      if (pointInRect([x, z], s.rect) && (!support || s.y1 > support.y1)) support = s;
    }
    if (support) {
      y = support.y1;
      snapped = "surface";
      // Face the way the support faces (a TV on its stand); Alt keeps the player's rotation.
      if (snap) rot = normDeg(support.rect.rotDeg);
    }
  }

  const ghost = (valid: boolean, why: string | null): Ghost => ({
    x,
    y,
    z,
    rot_deg: rot,
    room_id: room?.id ?? null,
    valid,
    why,
    snapped,
  });

  if (!room) return ghost(false, "Outside the rooms of this house");
  const rect: Rect = { x, z, w, d, rotDeg: rot };
  const inner = shrink(rect, TOLERANCE_M);
  if (!rectCorners(inner).every((c) => floor.some((r) => pointInPolygon(c, r.polygon)))) {
    return ghost(false, "Goes through a wall");
  }
  if (h >= FLAT_H_M) {
    for (const r of floor) {
      for (const o of r.openings) {
        if (o.type === "door" && rectsOverlap(inner, doorClearance(o.a, o.b))) return ghost(false, "Blocks a doorway");
      }
    }
    const y1 = y + h;
    for (const s of solids) {
      if (s.y1 - s.y0 < FLAT_H_M) continue;
      if (y >= s.y1 - 0.01 || s.y0 >= y1 - 0.01) continue; // stacked, or hanging above
      if (rectsOverlap(inner, shrink(s.rect, 0.01))) return ghost(false, `Hits the ${s.name.toLowerCase()}`);
    }
  }
  return ghost(true, null);
}

// ---------------------------------------------------------------------------
// Picking: which placed piece is under the crosshair
// ---------------------------------------------------------------------------

export type V3 = [number, number, number];

/**
 * Distance along a ray to a placed piece's box (footprint x height, rotated
 * about Y), or null if the ray misses it. Flat pieces are picked as if
 * FLAT_H_M tall so a rug can still be grabbed.
 */
export function rayHitsSolid(origin: V3, dir: V3, s: Solid): number | null {
  const rad = (-s.rect.rotDeg * Math.PI) / 180;
  const c = Math.cos(rad);
  const sn = Math.sin(rad);
  // Into the piece's local frame (inverse of rotationY): same as pointInRect.
  const toLocal = (vx: number, vz: number): [number, number] => [vx * c + vz * sn, -vx * sn + vz * c];
  const [ox, oz] = toLocal(origin[0] - s.rect.x, origin[2] - s.rect.z);
  const [dx, dz] = toLocal(dir[0], dir[2]);
  const top = Math.max(s.y1, s.y0 + FLAT_H_M);
  const lo: V3 = [-s.rect.w / 2, s.y0, -s.rect.d / 2];
  const hi: V3 = [s.rect.w / 2, top, s.rect.d / 2];
  const o: V3 = [ox, origin[1], oz];
  const dv: V3 = [dx, dir[1], dz];
  let tMin = 0;
  let tMax = Infinity;
  for (let i = 0; i < 3; i++) {
    if (Math.abs(dv[i]) < 1e-9) {
      if (o[i] < lo[i] || o[i] > hi[i]) return null;
      continue;
    }
    let t0 = (lo[i] - o[i]) / dv[i];
    let t1 = (hi[i] - o[i]) / dv[i];
    if (t0 > t1) [t0, t1] = [t1, t0];
    tMin = Math.max(tMin, t0);
    tMax = Math.min(tMax, t1);
    if (tMin > tMax) return null;
  }
  return tMin;
}

/** Nearest piece the ray hits before `maxT` (a wall, say), or null. */
export function pickSolid(origin: V3, dir: V3, solids: Solid[], maxT: number): { id: string; t: number } | null {
  let best: { id: string; t: number } | null = null;
  for (const s of solids) {
    const t = rayHitsSolid(origin, dir, s);
    if (t !== null && t < maxT && (!best || t < best.t)) best = { id: s.id, t };
  }
  return best;
}

/** Where a ray meets the horizontal plane y = planeY, or null if it points away from it. */
export function rayPlaneT(origin: V3, dir: V3, planeY: number): number | null {
  if (Math.abs(dir[1]) < 1e-6) return null;
  const t = (planeY - origin[1]) / dir[1];
  return t > 0 ? t : null;
}

// ---------------------------------------------------------------------------
// Display: sizes in feet and inches (AGENTS.md section 5: display strings use feet)
// ---------------------------------------------------------------------------

export function feetInches(m: number): string {
  const inches = Math.round(m * 39.3701);
  const ft = Math.floor(inches / 12);
  const inch = inches % 12;
  return ft > 0 ? `${ft}′${inch}″` : `${inch}″`;
}

export function sizeLabel(size: { w: number; d: number; h: number }): string {
  return `${feetInches(size.w)} × ${feetInches(size.d)} × ${feetInches(size.h)}`;
}
