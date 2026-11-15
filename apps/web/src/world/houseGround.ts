// Lane C: houses whose collision.glb has a `ground` node (pack v6+: the scanned
// lot, lane-a HANDOFF 18:24 / 06:10) bring their own yard.
// - The placeholder yard slab (HouseScene's HouseGround) at y = -0.02 would
//   cover it and its fence would cut the lot off, so it is not drawn.
// - The front door opens onto that yard, so walking through it must not leave
//   the house: those houses are left with E only (pack.ts stepZone).
// Older houses (v3) and houses whose collision.glb has not loaded or failed
// keep the slab and the walk-out trigger.

import type { Object3D } from "three";
import { create } from "zustand";

export const SCANNED_GROUND_NODE = "ground";

export function showPlaceholderGround(collisionScene: Object3D | null): boolean {
  return !collisionScene?.getObjectByName(SCANNED_GROUND_NODE);
}

type ScannedLots = {
  /** listing_id -> its collision.glb has a `ground` node. Set by HouseScene once it loads. */
  houses: Record<string, boolean>;
  set: (listingId: string, scanned: boolean) => void;
};

export const useScannedLots = create<ScannedLots>()((set) => ({
  houses: {},
  set: (listingId, scanned) =>
    set((s) => (s.houses[listingId] === scanned ? s : { houses: { ...s.houses, [listingId]: scanned } })),
}));

/** stepZone's leaveByKeyOnly: true once that house's collision.glb is known to have a ground node. */
export function leavesByKeyOnly(listingId: string): boolean {
  return useScannedLots.getState().houses[listingId] === true;
}
