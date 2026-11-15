// Lane C: focus teleport target (HANDOFF lane-b 16:45, lane-d 21:58).
// Pure function returning the position and yaw facing the front door from 1.5 m outside.

import type { Room } from "../contracts.gen";
import { bodyCentreAt } from "./landing";
import { type Door, doorOutwardBlock, type Vec3 } from "./pack";

export const APPROACH_DIST_M = 1.5;

export type Approach = { pos: Vec3; yaw: number };

/**
 * Yaw in radians (three.js / armMath convention: 0 looks down -Z, positive counterclockwise from above)
 * that looks in direction (dx, dz).
 */
export function yawToward(dx: number, dz: number): number {
  return Math.atan2(-dx, -dz);
}

/**
 * Target point in the block frame for teleporting outside a house's front door.
 * Returns body-centre position (1.5 m outside the door, standing height) and yaw facing the door.
 */
export function doorApproach(door: Door, rooms: Room[]): Approach {
  const [ox, oz] = doorOutwardBlock(door, rooms);
  const px = door.block[0] + ox * APPROACH_DIST_M;
  const pz = door.block[2] + oz * APPROACH_DIST_M;
  const py = bodyCentreAt(door.block[1]);
  const yaw = yawToward(door.block[0] - px, door.block[2] - pz);
  return {
    pos: [px, py, pz],
    yaw,
  };
}
