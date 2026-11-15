// Lane C: interior door state (open/closed) in a small local Zustand store.
//
// Doors are keyed by listing id and owned by the HouseDoors that registers
// them: set on mount, cleared on unmount, for that listing only. Nothing here
// subscribes to the zone. A zone write racing the HouseDoors effect (the
// start-room setMe, a respawn, a server patch) used to be able to wipe the
// list; readers now pick the doors for their own zone with doorsFor().

import { create } from "zustand";

import type { OpenedDoor } from "./doorSplit";

/** Why a house has the doors it has, for the dev probe (window.__laneC.doors()). */
export type DoorSource = {
  /** "glb": split from collision.glb's walls node. */
  collision: "glb";
  /** Boxes found in the walls node before splitting (0 means the node was not found). */
  wallBoxes: number;
};

export type DoorState = {
  /** Open flags by door id. Door ids start with the room id, so they are unique across houses. */
  openDoors: Record<string, boolean>;
  /** Openable doors per listing id, house-local frame. */
  byListing: Record<string, readonly OpenedDoor[]>;
  sources: Record<string, DoorSource>;
  isOpen: (id: string) => boolean;
  setOpen: (id: string, open: boolean) => void;
  toggle: (id: string) => void;
  setDoors: (listingId: string, doors: readonly OpenedDoor[], source: DoorSource) => void;
  /** Forget that listing's doors and their open flags (leaving the house closes them). */
  clearDoors: (listingId: string) => void;
};

const NO_DOORS: readonly OpenedDoor[] = [];

/** The doors of the house `zone` names; none on the block or for an unknown house. */
export function doorsFor(s: Pick<DoorState, "byListing">, zone: string): readonly OpenedDoor[] {
  return zone === "block" ? NO_DOORS : (s.byListing[zone] ?? NO_DOORS);
}

export const useDoorState = create<DoorState>()((set, get) => ({
  openDoors: {},
  byListing: {},
  sources: {},
  isOpen: (id) => Boolean(get().openDoors[id]),
  setOpen: (id, open) => set((s) => ({ openDoors: { ...s.openDoors, [id]: open } })),
  toggle: (id) => set((s) => ({ openDoors: { ...s.openDoors, [id]: !s.openDoors[id] } })),
  setDoors: (listingId, doors, source) =>
    set((s) => ({
      byListing: { ...s.byListing, [listingId]: doors },
      sources: { ...s.sources, [listingId]: source },
    })),
  clearDoors: (listingId) =>
    set((s) => {
      const gone = new Set((s.byListing[listingId] ?? []).map((d) => d.id));
      const byListing = { ...s.byListing };
      const sources = { ...s.sources };
      delete byListing[listingId];
      delete sources[listingId];
      const openDoors = Object.fromEntries(Object.entries(s.openDoors).filter(([id]) => !gone.has(id)));
      return { byListing, sources, openDoors };
    }),
}));
