// Lane D (in Lane C's world/, agreed in HANDOFF): where the local player respawns.
// The server only flips `alive` back to true after 3 s and opens its teleport
// window; the client picks the point, because only the client knows the free
// floor after the latest furnishing pass (indoorSpawns.ts). Pure; tested in
// respawn.test.ts.

import type { NetPlayer, Vec3 } from "../store";
import type { Spawn } from "./furniturePlan";

/** Drop the body from a little above the floor so it never starts inside it (same as World.tsx). */
export const SPAWN_LIFT_M = 1;

/** Alive→dead→alive for me, seen in order. The first sync (already alive) is not a respawn. */
export type LifeState = { sawDeath: boolean };

/** True exactly when `alive` comes back after we saw this player eliminated. */
export function stepLife(state: LifeState, alive: boolean | undefined): { state: LifeState; respawned: boolean } {
  if (alive === false) return { state: { sawDeath: true }, respawned: false };
  if (alive === true && state.sawDeath) return { state: { sawDeath: false }, respawned: true };
  return { state, respawned: false };
}

/**
 * The indoor spawn farthest from the nearest other living player in the
 * house, so nobody respawns in front of the player who just tagged them.
 * Returns a body-centre position (floor + lift), or null when the house has
 * no spawns yet.
 */
export function pickIndoorRespawn(spawns: Spawn[], others: NetPlayer[]): Vec3 | null {
  if (spawns.length === 0) return null;
  const threats = others.filter((p) => p.alive);
  let best = spawns[0];
  let bestScore = -Infinity;
  for (const s of spawns) {
    const nearest = threats.length
      ? Math.min(...threats.map((p) => Math.hypot(p.pos[0] - s.point[0], p.pos[2] - s.point[1])))
      : 0;
    if (nearest > bestScore) {
      bestScore = nearest;
      best = s;
    }
  }
  return [best.point[0], best.floorY + SPAWN_LIFT_M, best.point[1]];
}
