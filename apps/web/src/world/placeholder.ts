// Lane C: pure geometry for placeholder worlds, used when a pack has no .glb
// files yet (every fixture today). Render and collision placeholders are built
// from the same numbers here, but stay separate components, so the "collide
// against collision, never render" rule holds even for stand-ins.

import { ShapeUtils, Vector2 } from "three";

import type { Point2, Room } from "../contracts.gen";

export type WallSegment = { a: Point2; b: Point2 };

/** Door gaps narrower than this are not worth cutting. */
const MIN_SEGMENT_M = 0.05;
/** An opening counts as on an edge when both ends are this close to it. */
const ON_EDGE_M = 0.05;

function projectT(p: Point2, a: Point2, b: Point2): { t: number; dist: number } {
  const dx = b[0] - a[0];
  const dz = b[1] - a[1];
  const len2 = dx * dx + dz * dz || 1;
  const t = ((p[0] - a[0]) * dx + (p[1] - a[1]) * dz) / len2;
  const cx = a[0] + t * dx;
  const cz = a[1] + t * dz;
  return { t, dist: Math.hypot(p[0] - cx, p[1] - cz) };
}

/**
 * The walls of a room as segments along its polygon edges, with gaps cut for
 * every door opening on that edge, plus a `gapM`-wide gap at each extra door
 * point (the listing's front door) that sits within 0.3 m of an edge.
 * Windows do not cut walls.
 */
export function wallSegments(room: Room, extraDoors: Point2[] = [], gapM = 1.0): WallSegment[] {
  const poly = room.polygon;
  const out: WallSegment[] = [];
  for (let i = 0; i < poly.length; i++) {
    const a = poly[i];
    const b = poly[(i + 1) % poly.length];
    const len = Math.hypot(b[0] - a[0], b[1] - a[1]);
    if (len < MIN_SEGMENT_M) continue;

    const gaps: [number, number][] = [];
    for (const o of room.openings) {
      if (o.type !== "door") continue;
      const pa = projectT(o.a, a, b);
      const pb = projectT(o.b, a, b);
      if (pa.dist > ON_EDGE_M || pb.dist > ON_EDGE_M) continue;
      gaps.push([Math.min(pa.t, pb.t), Math.max(pa.t, pb.t)]);
    }
    for (const d of extraDoors) {
      const p = projectT(d, a, b);
      if (p.dist > 0.3 || p.t < 0 || p.t > 1) continue;
      const half = gapM / 2 / len;
      gaps.push([p.t - half, p.t + half]);
    }

    // Subtract the merged gaps from [0, 1].
    gaps.sort((x, y) => x[0] - y[0]);
    let t = 0;
    const lerp = (u: number): Point2 => [a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u];
    for (const [g0, g1] of gaps) {
      const s = Math.max(0, Math.min(1, g0));
      if ((s - t) * len > MIN_SEGMENT_M) out.push({ a: lerp(t), b: lerp(s) });
      t = Math.max(t, Math.min(1, g1));
    }
    if ((1 - t) * len > MIN_SEGMENT_M) out.push({ a: lerp(t), b: lerp(1) });
  }
  return out;
}

/** Centre, length and yaw (radians, three.js rotation-y) of a wall segment, for a box. */
export function segmentBox(s: WallSegment): { cx: number; cz: number; len: number; yaw: number } {
  const dx = s.b[0] - s.a[0];
  const dz = s.b[1] - s.a[1];
  return {
    cx: (s.a[0] + s.b[0]) / 2,
    cz: (s.a[1] + s.b[1]) / 2,
    len: Math.hypot(dx, dz),
    // A box's length runs along local +X; rotate +X onto (dx, dz).
    yaw: Math.atan2(-dz, dx),
  };
}

/** Footprint box of a house in its own frame: room bounds, or 8 x 6 m behind the door. */
export function houseBoxLocal(
  doorLocal: [number, number, number],
  rooms: Room[],
  height = 3.2,
): { center: [number, number, number]; size: [number, number, number] } {
  let minX: number, maxX: number, minZ: number, maxZ: number;
  if (rooms.length) {
    minX = Math.min(...rooms.flatMap((r) => r.polygon.map((p) => p[0])));
    maxX = Math.max(...rooms.flatMap((r) => r.polygon.map((p) => p[0])));
    minZ = Math.min(...rooms.flatMap((r) => r.polygon.map((p) => p[1])));
    maxZ = Math.max(...rooms.flatMap((r) => r.polygon.map((p) => p[1])));
  } else {
    // No rooms: front face (+Z) through the door, house extends to -Z.
    minX = doorLocal[0] - 4;
    maxX = doorLocal[0] + 4;
    minZ = doorLocal[2] - 6;
    maxZ = doorLocal[2];
  }
  return {
    center: [(minX + maxX) / 2, height / 2, (minZ + maxZ) / 2],
    size: [maxX - minX, height, maxZ - minZ],
  };
}

/**
 * A room's ceiling as a flat trimesh at floor_y + height_m, triangulated from
 * its polygon (rooms are often not rectangles). Lane A's collision.glb has
 * walls, floor and fixtures but no ceiling, so without this a jump or the
 * camera arm passes through the roof.
 */
export function ceilingTrimesh(room: Room): { vertices: Float32Array; indices: Uint32Array } {
  const y = room.floor_y + room.height_m;
  const contour = room.polygon.map(([x, z]) => new Vector2(x, z));
  const tris = ShapeUtils.triangulateShape(contour, []);
  const vertices = new Float32Array(room.polygon.length * 3);
  room.polygon.forEach(([x, z], i) => vertices.set([x, y, z], i * 3));
  return { vertices, indices: new Uint32Array(tris.flat()) };
}
