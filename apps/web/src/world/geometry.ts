// Lane C: pure house-local geometry. No three.js, no React, no Rapier, no sockets.
//
// Frame (contracts/room.schema.json): house-local, Y up, metres, 2D points are
// [x, z]. Placement objects carry x/y/z directly in that same frame, so nothing
// here converts between a "plan" frame and a world frame — there isn't one.
// Pass x and z straight into a three.js position and the room is the right way
// round.
//
// rot_deg is taken as degrees about world +Y, matching three.js `rotation-y`
// in sign, so `rotationY = rot_deg * Math.PI / 180` with no negation. The
// schema does not state the sign; if Lane B's solver disagrees, the only line
// that changes is rectCorners below.

import type { Asset, PlacementPlan, Point2, Room } from "../contracts.gen";

export type PlacedObject = PlacementPlan["objects"][number];
export type Opening = Room["openings"][number];

/** An axis-aware footprint in the house-local XZ plane. */
export type Rect = { x: number; z: number; w: number; d: number; rotDeg: number };

export function rectFromPlacement(obj: PlacedObject, asset: Asset): Rect {
  return {
    x: obj.x,
    z: obj.z,
    w: asset.size_m.w,
    d: asset.size_m.d,
    rotDeg: obj.rot_deg,
  };
}

/** Radians for a three.js `rotation-y` prop. */
export function rotationY(rotDeg: number): number {
  return (rotDeg * Math.PI) / 180;
}

// ---------------------------------------------------------------------------
// Polygon primitives
// ---------------------------------------------------------------------------

export function signedArea(poly: Point2[]): number {
  let a = 0;
  for (let i = 0; i < poly.length; i++) {
    const [px, pz] = poly[i];
    const [qx, qz] = poly[(i + 1) % poly.length];
    a += px * qz - qx * pz;
  }
  return a / 2;
}

/** Cross-check against Room.area_m2; a mismatch means a bad trace upstream. */
export function polygonArea(poly: Point2[]): number {
  return Math.abs(signedArea(poly));
}

export function pointInPolygon(p: Point2, poly: Point2[]): boolean {
  let inside = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const a = poly[i];
    const b = poly[j];
    if (a[1] > p[1] !== b[1] > p[1]) {
      const t = (p[1] - a[1]) / (b[1] - a[1]);
      if (p[0] < a[0] + t * (b[0] - a[0])) inside = !inside;
    }
  }
  return inside;
}

export function distToSegment(p: Point2, a: Point2, b: Point2): number {
  const vx = b[0] - a[0];
  const vz = b[1] - a[1];
  const len2 = vx * vx + vz * vz;
  if (len2 < 1e-12) return Math.hypot(p[0] - a[0], p[1] - a[1]);
  let t = ((p[0] - a[0]) * vx + (p[1] - a[1]) * vz) / len2;
  t = Math.max(0, Math.min(1, t));
  return Math.hypot(p[0] - (a[0] + t * vx), p[1] - (a[1] + t * vz));
}

export function minDistToPolygon(p: Point2, poly: Point2[]): number {
  let best = Infinity;
  for (let i = 0; i < poly.length; i++) {
    best = Math.min(best, distToSegment(p, poly[i], poly[(i + 1) % poly.length]));
  }
  return best;
}

// ---------------------------------------------------------------------------
// Rectangles
// ---------------------------------------------------------------------------

/** Corners of a rotated footprint, in house-local [x, z]. */
export function rectCorners(r: Rect): Point2[] {
  const rad = rotationY(r.rotDeg);
  const c = Math.cos(rad);
  const s = Math.sin(rad);
  const hw = r.w / 2;
  const hd = r.d / 2;
  const local: Point2[] = [
    [-hw, -hd],
    [hw, -hd],
    [hw, hd],
    [-hw, hd],
  ];
  // Rotation about +Y: (x, z) -> (x cos + z sin, -x sin + z cos)
  return local.map(([lx, lz]) => [
    r.x + lx * c + lz * s,
    r.z - lx * s + lz * c,
  ]);
}

function unitAxes(corners: Point2[]): Point2[] {
  return [
    [corners[1][0] - corners[0][0], corners[1][1] - corners[0][1]] as Point2,
    [corners[3][0] - corners[0][0], corners[3][1] - corners[0][1]] as Point2,
  ].map(([x, z]) => {
    const len = Math.hypot(x, z) || 1;
    return [x / len, z / len] as Point2;
  });
}

function project(corners: Point2[], axis: Point2): [number, number] {
  const dots = corners.map((c) => c[0] * axis[0] + c[1] * axis[1]);
  return [Math.min(...dots), Math.max(...dots)];
}

/**
 * Separating-axis test. `margin` grows the first rect on every side, so
 * passing an Asset's clearance_m turns "do these touch" into "is there room
 * to walk between them" — the `clearance` conflict type in the plan schema.
 */
export function rectsOverlap(a: Rect, b: Rect, margin = 0): boolean {
  const grown: Rect = { ...a, w: a.w + 2 * margin, d: a.d + 2 * margin };
  const ca = rectCorners(grown);
  const cb = rectCorners(b);
  for (const axis of [...unitAxes(ca), ...unitAxes(cb)]) {
    const [aMin, aMax] = project(ca, axis);
    const [bMin, bMax] = project(cb, axis);
    if (aMax < bMin || bMax < aMin) return false;
  }
  return true;
}

export function pointInRect(p: Point2, r: Rect, margin = 0): boolean {
  const rad = -rotationY(r.rotDeg);
  const dx = p[0] - r.x;
  const dz = p[1] - r.z;
  const lx = dx * Math.cos(rad) + dz * Math.sin(rad);
  const lz = -dx * Math.sin(rad) + dz * Math.cos(rad);
  return Math.abs(lx) <= r.w / 2 + margin && Math.abs(lz) <= r.d / 2 + margin;
}

/** Distance to an opening, which is a wall segment, not a point. */
export function distToOpening(p: Point2, o: Opening): number {
  return distToSegment(p, o.a, o.b);
}

// ---------------------------------------------------------------------------
// Free floor, spawns, push-out
// ---------------------------------------------------------------------------

export type FloorOptions = {
  /** Grid resolution in metres. 0.25 is plenty for spawn selection. */
  cellSize?: number;
  /** Player radius: keep this far from walls and furniture. */
  clearanceM?: number;
  /** Keep clear of doorways so nobody spawns in a door. Windows are ignored. */
  doorClearanceM?: number;
};

/**
 * Every grid cell centre a player could stand in. Walls, furniture and
 * doorways are excluded. The basis for spawn points and push-out.
 */
export function freeFloorCells(
  room: Room,
  furniture: Rect[],
  opts: FloorOptions = {},
): Point2[] {
  const cell = opts.cellSize ?? 0.25;
  const clearance = opts.clearanceM ?? 0.35;
  const doorClearance = opts.doorClearanceM ?? 0.9;
  const doors = room.openings.filter((o) => o.type === "door");

  const xs = room.polygon.map((p) => p[0]);
  const zs = room.polygon.map((p) => p[1]);
  const [minX, maxX] = [Math.min(...xs), Math.max(...xs)];
  const [minZ, maxZ] = [Math.min(...zs), Math.max(...zs)];

  const free: Point2[] = [];
  for (let x = minX + cell / 2; x < maxX; x += cell) {
    for (let z = minZ + cell / 2; z < maxZ; z += cell) {
      const p: Point2 = [x, z];
      if (!pointInPolygon(p, room.polygon)) continue;
      if (minDistToPolygon(p, room.polygon) < clearance) continue;
      if (furniture.some((f) => pointInRect(p, f, clearance))) continue;
      if (doors.some((d) => distToOpening(p, d) < doorClearance)) continue;
      free.push(p);
    }
  }
  return free;
}

export function freeFloorAreaM2(
  room: Room,
  furniture: Rect[],
  opts: FloorOptions = {},
): number {
  const cell = opts.cellSize ?? 0.25;
  return freeFloorCells(room, furniture, opts).length * cell * cell;
}

/**
 * Up to `count` spawn points spread across the free floor, chosen greedily
 * for maximum separation. Deterministic: every client computes the same
 * answer from the same plan, so the game server does not have to send them.
 *
 * Returns fewer than `count` — possibly none — when the room cannot hold
 * them. Fall back to a doorway rather than stacking players.
 */
export function spawnPoints(
  room: Room,
  furniture: Rect[],
  count: number,
  opts: FloorOptions & { minSeparationM?: number } = {},
): Point2[] {
  const minSep = opts.minSeparationM ?? 1.5;
  const free = freeFloorCells(room, furniture, opts);
  if (free.length === 0 || count <= 0) return [];

  const chosen: Point2[] = [
    free.reduce((best, p) =>
      minDistToPolygon(p, room.polygon) > minDistToPolygon(best, room.polygon)
        ? p
        : best,
    ),
  ];

  while (chosen.length < count) {
    let bestP: Point2 | null = null;
    let bestD = -1;
    for (const p of free) {
      const d = Math.min(
        ...chosen.map((c) => Math.hypot(p[0] - c[0], p[1] - c[1])),
      );
      if (d > bestD) {
        bestD = d;
        bestP = p;
      }
    }
    if (!bestP || bestD < minSep) break;
    chosen.push(bestP);
  }
  return chosen;
}

/**
 * After a re-furnish lands on someone, the nearest legal standing position.
 * Returns the input unchanged when they are already clear, or null when the
 * room has no free floor at all — then teleport them to a doorway.
 */
export function pushOut(
  player: Point2,
  room: Room,
  furniture: Rect[],
  opts: FloorOptions = {},
): Point2 | null {
  const clearance = opts.clearanceM ?? 0.35;
  const stuck =
    !pointInPolygon(player, room.polygon) ||
    furniture.some((f) => pointInRect(player, f, clearance));
  if (!stuck) return player;

  const free = freeFloorCells(room, furniture, opts);
  if (free.length === 0) return null;
  return free.reduce((best, p) =>
    Math.hypot(p[0] - player[0], p[1] - player[1]) <
    Math.hypot(best[0] - player[0], best[1] - player[1])
      ? p
      : best,
  );
}

/** Midpoint of an opening, for aiming a camera or placing a fallback spawn. */
export function openingMidpoint(o: Opening): Point2 {
  return [(o.a[0] + o.b[0]) / 2, (o.a[1] + o.b[1]) / 2];
}

/** Which room of a house the player is standing in, or null if none. */
export function roomAt(point: Point2, rooms: Room[], floor?: number): Room | null {
  for (const r of rooms) {
    if (floor !== undefined && r.floor !== floor) continue;
    if (pointInPolygon(point, r.polygon)) return r;
  }
  return null;
}
