// Lane D (in Lane C's world/, agreed in HANDOFF): the agent's focus_listing
// teleport (HANDOFF lane-b 16:45). When MY entry in state.focus changes, the
// player is put just outside that house's front door, on the block, facing it.
// Pure; tested in focusTeleport.test.ts. World.tsx (Zones) does the move.
//
// The landing spot is the door's existing exitTo (pack.ts makeDoor): the same
// point as walking out of that door, DOOR_STEP_M outside it, so it is clear
// of the door trigger. The server opens its teleport window on a focus change
// (contracts v2.4.1), so the jump is accepted.

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

/** The door's exit point, `lift` m up so the body drops onto the ground, facing the door. */
export function focusArrival(door: Door, lift: number): Arrival {
  const [x, y, z] = door.exitTo;
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
