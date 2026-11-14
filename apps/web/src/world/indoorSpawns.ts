// Lane C: indoor spawn points per house, recomputed by FurnitureLayer after
// every plan (AGENTS.md section 11). Local to world/; respawn code reads it.

import { create } from "zustand";

import type { Spawn } from "./furniturePlan";

type IndoorSpawns = {
  byHouse: Record<string, Spawn[]>;
  set: (listingId: string, spawns: Spawn[]) => void;
};

export const useIndoorSpawns = create<IndoorSpawns>()((set) => ({
  byHouse: {},
  set: (listingId, spawns) => set((s) => ({ byHouse: { ...s.byHouse, [listingId]: spawns } })),
}));
