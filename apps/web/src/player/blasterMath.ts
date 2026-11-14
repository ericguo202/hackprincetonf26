// Lane C: foam blaster, pure parts (AGENTS.md section 11). No three.js, no
// Rapier, so every rule is unit tested (blasterMath.test.ts). Blaster.tsx
// does the raycasts and effects.
//
// The pipeline for one shot, per the note in armMath.ts:
//   1. AIM: the camera ray through the crosshair. It starts at the camera but
//      only counts hits past the head (aimRay().minT), so nothing between the
//      camera and the player can eat the shot. Its first hit is the aim point.
//   2. SHOT: from the head to the aim point (shotRay). This decides what is
//      actually hit, so a wall the shoulder camera can see past still blocks.
//   3. FIRE: `fire {origin: head, dir, target_id?}` (contracts/messages.md).
//      Head, never the camera: the server rejects an origin more than 1.5 m
//      from the shooter and the camera arm reaches about 3.5 m.
//   4. TRACER: from the muzzle to the shot's hit point.

import { viewDir, rightDir, type V3 } from "./armMath.ts";
import type { Weapon } from "../../../../contracts/game-state";

export const BLASTER = {
  /** Server: under 5 shots/s. 4/s leaves room for network jitter bunching two shots. */
  maxShotsPerSecond: 4,
  /** Server: hits under 40 m count. Shots and tracers stop there. */
  rangeM: 40,
  /** Muzzle relative to the head: right, forward, up (metres). Right hand, chest height. */
  muzzle: { right: 0.22, forward: 0.45, up: -0.3 },
  /** Dart tracer speed, m/s. Hitscan decides the hit; this is only how it looks. */
  tracerSpeed: 70,
  /** Hit marker on the crosshair, seconds. */
  hitMarkerS: 0.18,
} as const;

export const MIN_SHOT_INTERVAL_S = 1 / BLASTER.maxShotsPerSecond;

const sub = (a: V3, b: V3): V3 => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const add = (a: V3, b: V3): V3 => [a[0] + b[0], a[1] + b[1], a[2] + b[2]];
const scale = (a: V3, s: number): V3 => [a[0] * s, a[1] * s, a[2] * s];
const dot = (a: V3, b: V3) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const len = (a: V3) => Math.hypot(a[0], a[1], a[2]);

export function pointAt(origin: V3, dir: V3, t: number): V3 {
  return add(origin, scale(dir, t));
}

// ---------------------------------------------------------------------------
// Fire rate
// ---------------------------------------------------------------------------

/** True when a shot at `now` (seconds) is allowed after the last one at `lastShotAt`. */
export function canFire(lastShotAt: number | null, now: number, minInterval = MIN_SHOT_INTERVAL_S): boolean {
  return lastShotAt === null || now - lastShotAt >= minInterval - 1e-9;
}

// ---------------------------------------------------------------------------
// Aim and shot rays
// ---------------------------------------------------------------------------

export type Ray = { origin: V3; dir: V3 };

/**
 * The camera ray through the crosshair. `minT` is how far along it the head
 * is (its projection onto the ray, never negative): hits closer than that are
 * between the camera and the player and are ignored. In first person the
 * camera is at the head and minT is 0.
 */
export function aimRay(camera: V3, cameraDir: V3, head: V3): Ray & { minT: number } {
  const d = len(cameraDir);
  const dir: V3 = d > 0 ? scale(cameraDir, 1 / d) : [0, 0, -1];
  return { origin: camera, dir, minT: Math.max(0, dot(sub(head, camera), dir)) };
}

/** Where the crosshair points: the first hit past minT, or the ray at full range past the head. */
export function aimPoint(aim: Ray & { minT: number }, hitT: number | null, range = BLASTER.rangeM): V3 {
  return pointAt(aim.origin, aim.dir, hitT ?? aim.minT + range);
}

/**
 * The shot: from the head toward the aim point. Returns the head as origin
 * (that is what goes on the wire) and the distance to the aim point. If the
 * aim point is on top of the head (degenerate), fall back to the aim ray's
 * direction.
 */
export function shotRay(head: V3, aim: V3, fallbackDir: V3): Ray & { aimDist: number } {
  const v = sub(aim, head);
  const d = len(v);
  if (d < 1e-6) return { origin: head, dir: fallbackDir, aimDist: 0 };
  return { origin: head, dir: scale(v, 1 / d), aimDist: d };
}

/** The head: body centre plus the arm's head offset. Same point the camera arm uses. */
export function headFrom(bodyCentre: V3, headOffsetY: number): V3 {
  return [bodyCentre[0], bodyCentre[1] + headOffsetY, bodyCentre[2]];
}

/** Muzzle position for the tracer and flash, from the head and the look direction. */
export function muzzleFrom(head: V3, yaw: number, pitch: number): V3 {
  const f = viewDir(yaw, pitch);
  const r = rightDir(yaw);
  const m = BLASTER.muzzle;
  return [
    head[0] + r[0] * m.right + f[0] * m.forward,
    head[1] + f[1] * m.forward + m.up,
    head[2] + r[2] * m.right + f[2] * m.forward,
  ];
}

// ---------------------------------------------------------------------------
// Player hitboxes (the seam for Lane D)
// ---------------------------------------------------------------------------

/**
 * A remote player's hitbox: a vertical capsule. `base` is the capsule's
 * centre in the current zone's frame, the same point NetPlayer.pos carries
 * (the body centre the local Player writes to me.pos).
 */
export type Hitbox = { id: string; centre: V3; radius: number; halfHeight: number };

/**
 * Distance along a ray (unit dir) to a vertical capsule, or null. The
 * capsule's segment runs halfHeight above and below the centre.
 */
export function rayCapsule(ray: Ray, h: Hitbox): number | null {
  const a: V3 = [h.centre[0], h.centre[1] - h.halfHeight, h.centre[2]];
  const b: V3 = [h.centre[0], h.centre[1] + h.halfHeight, h.centre[2]];
  const r = h.radius;
  let best: number | null = null;
  const take = (t: number) => {
    if (t >= 0 && (best === null || t < best)) best = t;
  };

  // Infinite cylinder around the Y axis through the centre, clipped to the segment.
  const ox = ray.origin[0] - a[0];
  const oz = ray.origin[2] - a[2];
  const dx = ray.dir[0];
  const dz = ray.dir[2];
  const qa = dx * dx + dz * dz;
  const qb = 2 * (ox * dx + oz * dz);
  const qc = ox * ox + oz * oz - r * r;
  if (qa > 1e-12) {
    const disc = qb * qb - 4 * qa * qc;
    if (disc >= 0) {
      const s = Math.sqrt(disc);
      for (const t of [(-qb - s) / (2 * qa), (-qb + s) / (2 * qa)]) {
        const y = ray.origin[1] + ray.dir[1] * t;
        if (y >= a[1] && y <= b[1]) take(t);
      }
    }
  }
  // End caps (spheres).
  for (const c of [a, b]) {
    const oc = sub(ray.origin, c);
    const bb = dot(oc, ray.dir);
    const cc = dot(oc, oc) - r * r;
    const disc = bb * bb - cc;
    if (disc >= 0) {
      const s = Math.sqrt(disc);
      take(-bb - s);
      take(-bb + s);
    }
  }
  return best;
}

export type HitboxHit = { id: string; t: number };

/** Nearest hitbox along the ray within [minT, maxT], or null. */
export function nearestHitbox(ray: Ray, boxes: Hitbox[], minT: number, maxT: number): HitboxHit | null {
  let best: HitboxHit | null = null;
  for (const h of boxes) {
    const t = rayCapsule(ray, h);
    if (t === null || t < minT || t > maxT) continue;
    if (!best || t < best.t) best = { id: h.id, t };
  }
  return best;
}

// ---------------------------------------------------------------------------
// Resolving a shot
// ---------------------------------------------------------------------------

export type ShotHit =
  | { kind: "player"; t: number; targetId: string }
  | { kind: "surface"; t: number }
  | { kind: "miss"; t: number };

/** Closer of a world hit and a hitbox hit; a miss at full range when neither. */
export function resolveHit(worldT: number | null, box: HitboxHit | null, range = BLASTER.rangeM): ShotHit {
  if (box && (worldT === null || box.t <= worldT)) return { kind: "player", t: box.t, targetId: box.id };
  if (worldT !== null && worldT <= range) return { kind: "surface", t: worldT };
  return { kind: "miss", t: range };
}

/** The `fire` message (contracts/messages.md). target_id only on a player hit. */
/** contracts v2.4.0: weapon is always sent; the foam blaster is "blaster". */
export type FirePayload = { origin: V3; dir: V3; target_id?: string; weapon: Weapon };

export function firePayload(shot: Ray, hit: ShotHit): FirePayload {
  const p: FirePayload = { origin: shot.origin, dir: shot.dir, weapon: "blaster" };
  if (hit.kind === "player") p.target_id = hit.targetId;
  return p;
}
