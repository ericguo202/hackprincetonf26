// Lane C: the front doors E can use, published by World's Zones for the
// DoorPrompt overlay (a DOM sibling of the canvas, so it can't reach Zones'
// refs). `press` runs the same zone switch as the walk-in trigger
// (pack.ts pressFrontDoor -> crossDoor) and reports whether it switched.

import { create } from "zustand";

import type { Door } from "./pack";

export type FrontDoorState = {
  doors: Door[];
  /** listing_id -> street address from the listings index. */
  addresses: Record<string, string>;
  /** E at a front door; null until Zones mounts. */
  press: (() => boolean) | null;
  set: (doors: Door[], addresses: Record<string, string>, press: (() => boolean) | null) => void;
};

export const useFrontDoors = create<FrontDoorState>()((set) => ({
  doors: [],
  addresses: {},
  press: null,
  set: (doors, addresses, press) => set({ doors, addresses, press }),
}));
