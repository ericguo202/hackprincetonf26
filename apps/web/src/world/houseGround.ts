// Lane C: should the placeholder yard slab (HouseScene's HouseGround) be drawn?
// Houses whose collision.glb has a `ground` node (pack v6+: the scanned lot,
// lane-a HANDOFF 18:24 / 06:10) bring their own yard; the slab at y = -0.02
// would cover it and its fence would cut the lot off. Older houses (v3) and
// houses whose collision.glb has not loaded or failed keep the slab.

import type { Object3D } from "three";

export const SCANNED_GROUND_NODE = "ground";

export function showPlaceholderGround(collisionScene: Object3D | null): boolean {
  return !collisionScene?.getObjectByName(SCANNED_GROUND_NODE);
}
