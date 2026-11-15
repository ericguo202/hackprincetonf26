// Lane D: foam-fight rules (AGENTS.md section 11, contracts/messages.md `fire`). Pure helpers + constants.
import { WEAPONS, type Weapon } from "../../../../contracts/game-state.ts";

export type Vec3 = [number, number, number];

/** Full health (schema default for Player.hp). */
export const FULL_HP = 3;
export const RESPAWN_MS = 3000;
/** `origin` (the client's head) must be within this of the shooter's last accepted position. */
export const MAX_ORIGIN_OFFSET_M = 1.5;
/** Blaster only: max perpendicular distance from the target's body centre to the shot ray. */
export const AIM_TOLERANCE_M = 1.0;

export const DAMAGE: Record<Weapon, number> = { blaster: 1, noodle: 2 };
/** Min ms between accepted fires: 80% of the client cooldown (blaster 0.25 s, noodle 0.8 s). */
export const MIN_FIRE_INTERVAL_MS: Record<Weapon, number> = { blaster: 200, noodle: 640 };
/** Max origin -> target body centre distance. Noodle: 2.0 reach + 0.35 body radius + 0.55 lag slack. */
export const MAX_RANGE_M: Record<Weapon, number> = { blaster: 40, noodle: 2.9 };

export function parseWeapon(w: unknown): Weapon | null {
  if (w === undefined) return "blaster";
  return (WEAPONS as readonly unknown[]).includes(w) ? (w as Weapon) : null;
}

export function parseVec3(v: unknown): Vec3 | null {
  if (!Array.isArray(v) || v.length !== 3 || !v.every((n) => typeof n === "number" && Number.isFinite(n))) return null;
  return [v[0], v[1], v[2]];
}

/** Unit vector, or null for a zero (or overflowing) vector. */
export function normalize(v: Vec3): Vec3 | null {
  const len = Math.hypot(v[0], v[1], v[2]);
  return len > 0 && Number.isFinite(len) ? [v[0] / len, v[1] / len, v[2] / len] : null;
}

export function distance(a: Vec3, b: Vec3): number {
  return Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]);
}

/** Distance from point p to the ray origin + t * dir (t >= 0); dir must be a unit vector. */
export function rayPointDistance(origin: Vec3, dir: Vec3, p: Vec3): number {
  const d: Vec3 = [p[0] - origin[0], p[1] - origin[1], p[2] - origin[2]];
  const t = Math.max(0, d[0] * dir[0] + d[1] * dir[1] + d[2] * dir[2]);
  return distance(p, [origin[0] + t * dir[0], origin[1] + t * dir[1], origin[2] + t * dir[2]]);
}
