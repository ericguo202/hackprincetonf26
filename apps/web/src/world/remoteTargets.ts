// Lane C: the hitboxes of remote players as they are drawn this frame.
// RemotePlayers writes it every frame from the interpolated samples; the
// blaster's getHitboxes seam reads it. One module slot, like playerControl.

import type { Hitbox } from "../player/blasterMath";
import type { Vec3 } from "./remoteInterp";

let current: Hitbox[] = [];
let bodies: Vec3[] = [];

export function setRemoteHitboxes(boxes: Hitbox[]): void {
  current = boxes;
}

/** Alive remote players in my zone, as capsules matched to the model. Pass as <Player getHitboxes>. */
export function remoteHitboxes(): Hitbox[] {
  return current;
}

/** Body centres of every remote player drawn in my zone this frame (alive or not). */
export function setRemoteBodies(next: Vec3[]): void {
  bodies = next;
}

/** For matching other players' shots (store.fx) to someone in my zone (remoteFx.fxInMyZone). */
export function remoteBodies(): Vec3[] {
  return bodies;
}
