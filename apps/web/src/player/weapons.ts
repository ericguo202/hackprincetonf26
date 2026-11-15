// Lane C: the two weapons (contracts v2.4.0 fire.weapon). Pure: no three.js,
// no React; tested in weapons.test.ts. Every number is one named constant,
// DEFAULTS that Lane D's server validation can overrule (HANDOFF).
//
// blaster: hitscan dart (blasterMath.ts), 3 hits to eliminate, 4 shots/s.
// noodle:  pool noodle, a melee swing. A short arc query from the head along
//          the aim direction; hits players only (it passes through furniture
//          and does not stop darts); 2 hits to eliminate; 0.8 s cooldown.
//          It uses the `shoot` anim state: no new state, no contract change.

import type { Weapon } from "../../../../contracts/game-state";
import type { V3 } from "./armMath.ts";
import { BLASTER, type FirePayload, type Hitbox } from "./blasterMath.ts";

export const NOODLE = {
  /** Reach from the head to the target's body surface, metres. */
  reachM: 2.0,
  /** Full width of the swing arc, degrees (30 each side of the aim, horizontally). */
  arcDeg: 60,
  /** Minimum time between swings, seconds. */
  cooldownS: 0.8,
  hitsToEliminate: 2,
} as const;

/** Per weapon, for the client and for Lane D's server rules. */
export const WEAPON_RULES: Record<Weapon, { hitsToEliminate: number; minIntervalS: number }> = {
  blaster: { hitsToEliminate: 3, minIntervalS: 1 / BLASTER.maxShotsPerSecond },
  noodle: { hitsToEliminate: NOODLE.hitsToEliminate, minIntervalS: NOODLE.cooldownS },
};

/** Keys 1 and 2 pick the weapon. */
export const WEAPON_KEYS: Record<string, Weapon> = { Digit1: "blaster", Digit2: "noodle" };

/** Allowed to swing/fire `weapon` at `now` after the last attack at `last` (seconds). */
export function canAttack(weapon: Weapon, last: number | null, now: number): boolean {
  return last === null || now - last >= WEAPON_RULES[weapon].minIntervalS - 1e-9;
}

const sub = (a: V3, b: V3): V3 => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const dot = (a: V3, b: V3) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];

/** Closest point on a vertical capsule's axis segment to `p`. */
function closestOnAxis(p: V3, h: Hitbox): V3 {
  const y = Math.max(h.centre[1] - h.halfHeight, Math.min(h.centre[1] + h.halfHeight, p[1]));
  return [h.centre[0], y, h.centre[2]];
}

/**
 * Where a swing from `head` meets hitbox `h`: the point on the capsule's
 * surface closest to the head (for the foam splat). Inside the capsule: the
 * axis point itself.
 */
export function swingImpactPoint(head: V3, h: Hitbox): V3 {
  const c = closestOnAxis(head, h);
  const v = sub(head, c);
  const l = Math.sqrt(dot(v, v));
  if (l <= h.radius || l < 1e-9) return c;
  return [c[0] + (v[0] / l) * h.radius, c[1] + (v[1] / l) * h.radius, c[2] + (v[2] / l) * h.radius];
}

export type SwingHit = { id: string; dist: number };

/**
 * The noodle's arc query. A target counts when its body surface is within
 * reach of the head (3D: distance to the capsule's axis minus its radius)
 * and the direction to it is within half the arc of the aim, horizontally
 * (so it hits a crouched player in front of you without looking down).
 * Nearest target wins. No walls or furniture are tested: hits register on
 * players only.
 */
export function noodleSwing(head: V3, aimDir: V3, boxes: Hitbox[], rules = NOODLE): SwingHit | null {
  const flat = Math.hypot(aimDir[0], aimDir[2]);
  const ax = flat > 1e-9 ? aimDir[0] / flat : 0;
  const az = flat > 1e-9 ? aimDir[2] / flat : -1;
  const half = ((rules.arcDeg / 2) * Math.PI) / 180;
  let best: SwingHit | null = null;
  for (const h of boxes) {
    const c = closestOnAxis(head, h);
    const v = sub(c, head);
    const dist = Math.max(0, Math.sqrt(dot(v, v)) - h.radius);
    if (dist > rules.reachM) continue;
    const hx = v[0];
    const hz = v[2];
    const hl = Math.hypot(hx, hz);
    // Standing inside someone (hl ~ 0) counts as in front.
    if (hl > 1e-6) {
      const cos = (hx * ax + hz * az) / hl;
      // Allow for the target's width: the arc edge may graze its side.
      const widen = Math.asin(Math.min(1, h.radius / hl));
      if (Math.acos(Math.max(-1, Math.min(1, cos))) > half + widen) continue;
    }
    if (!best || dist < best.dist) best = { id: h.id, dist };
  }
  return best;
}

/** The `fire` message for a swing: origin the head, dir the aim, target only on a hit. */
export function noodlePayload(head: V3, aimDir: V3, hit: SwingHit | null): FirePayload {
  const p: FirePayload = { origin: head, dir: aimDir, weapon: "noodle" };
  if (hit) p.target_id = hit.id;
  return p;
}
