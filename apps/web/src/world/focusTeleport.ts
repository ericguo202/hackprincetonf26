// Lane D (in Lane C's world/, agreed in HANDOFF): the agent's focus_listing
// teleport (HANDOFF lane-b 16:45). When MY entry in state.focus changes, the
// player is put on the sidewalk in front of that house, on the block, facing it.
// Pure; tested in focusTeleport.test.ts. World.tsx (Zones) does the move.
//
// The landing spot is the nearest sidewalk spawn (manifest.spawns) on the
// street side of the door, so the player sees the house from the street. On
// the block the house is a stand-in lot, and landing at the door's exitTo
// (1.2 m out) put the camera in the fenced lot staring at the door marker.
// With no such spawn within SIDEWALK_MAX_M, exitTo (pack.ts makeDoor) is the
// fallback: the same point as walking out of that door. The server opens its
// teleport window on a focus change (contracts v2.4.1), so the jump is accepted.

import type { Door, Vec3 } from "./pack";

/** Where and how the player lands: body-centre position (lifted) and camera yaw. */
export type Arrival = { pos: Vec3; yaw: number };

/**
 * Camera yaw (radians) that looks along +XZ direction (dx, dz). Matches
 * armMath.viewDir: yaw 0 looks down -Z, yaw grows counterclockwise from above.
 */
export function yawToward(dx: number, dz: number): number {
  return Math.atan2(-dx, -dz);
}

/** Farthest a sidewalk spawn may be from the door to count as "in front of this house". */
export const SIDEWALK_MAX_M = 30;

/**
 * The nearest of `spawns` (block frame) on the street side of the door, within
 * SIDEWALK_MAX_M; else the door's exitTo. `lift` m up so the body drops onto
 * the ground, facing the door.
 */
export function focusArrival(door: Door, lift: number, spawns: Vec3[] = []): Arrival {
  // Outward from the house, in the block frame: door point -> its exit point.
  const ox = door.exitTo[0] - door.block[0];
  const oz = door.exitTo[2] - door.block[2];
  let best: Vec3 = door.exitTo;
  let bestDist = Infinity;
  for (const s of spawns) {
    const dx = s[0] - door.block[0];
    const dz = s[2] - door.block[2];
    const dist = Math.hypot(dx, dz);
    if (dx * ox + dz * oz <= 0 || dist > SIDEWALK_MAX_M || dist >= bestDist) continue;
    best = s;
    bestDist = dist;
  }
  const [x, y, z] = best;
  return {
    pos: [x, y + lift, z],
    yaw: yawToward(door.block[0] - x, door.block[2] - z),
  };
}

/**
 * Focus watcher state. `seen` is false until the first focus value for me has
 * been read: that first value (initial sync, or the state after a reload) is a
 * starting point, never a teleport.
 */
export type FocusState = { seen: boolean; listing: string | undefined };

/** The listing to teleport to when my focus entry CHANGES to a new house, else null. */
export function stepFocus(
  state: FocusState,
  focusForMe: string | undefined,
): { state: FocusState; goTo: string | null } {
  const next: FocusState = { seen: true, listing: focusForMe };
  if (!state.seen) return { state: next, goTo: null };
  if (focusForMe === undefined || focusForMe === state.listing) return { state: next, goTo: null };
  return { state: next, goTo: focusForMe };
}
