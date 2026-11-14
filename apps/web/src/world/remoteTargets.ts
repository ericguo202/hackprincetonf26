// Lane C: the hitboxes of remote players as they are drawn this frame.
// RemotePlayers writes it every frame from the interpolated samples; the
// blaster's getHitboxes seam reads it. One module slot, like playerControl.

import type { Hitbox } from "../player/blasterMath";

let current: Hitbox[] = [];

export function setRemoteHitboxes(boxes: Hitbox[]): void {
  current = boxes;
}

/** Alive remote players in my zone, as capsules matched to the model. Pass as <Player getHitboxes>. */
export function remoteHitboxes(): Hitbox[] {
  return current;
}
