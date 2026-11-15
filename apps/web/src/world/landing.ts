// Lane C: where the body goes when the player is placed on a floor point (a
// spawn, a door's exitTo / enterTo, a dev start room). Floor points carry
// real heights: on pack v2, Listing.world.position.y is the block ground at
// each front door (7014 0.348 m, 6915 0.103 m; Lane A, HANDOFF 22:20), and
// houseToBlock adds it, so a door's exitTo is already at lawn height. This
// turns a floor point into the ecctrl body centre: the standing foot offset
// plus a small drop, so the capsule never starts inside the ground.

import { FOOT_BELOW_CENTRE_M } from "../player/characterAsset";
import type { Vec3 } from "./pack";

/** Start this far above standing height and let the body settle, metres. */
export const SPAWN_DROP_M = 0.05;

/** Body centre for a player standing on floor point `p` (any frame), or Y coordinate from floor Y. */
export function bodyCentreAt(p: number): number;
export function bodyCentreAt(p: Vec3): Vec3;
export function bodyCentreAt(p: Vec3 | number): Vec3 | number {
  if (typeof p === "number") return p + FOOT_BELOW_CENTRE_M + SPAWN_DROP_M;
  return [p[0], p[1] + FOOT_BELOW_CENTRE_M + SPAWN_DROP_M, p[2]];
}
