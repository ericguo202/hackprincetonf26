// Lane C: Door state management (open/closed) in a small local Zustand store.
// Reset when the zone changes.

import { create } from "zustand";

import { useStore } from "../store";
import type { OpenedDoor } from "./doorSplit";

export type DoorState = {
  openDoors: Record<string, boolean>;
  activeDoors: OpenedDoor[];
  isOpen: (id: string) => boolean;
  setOpen: (id: string, open: boolean) => void;
  toggle: (id: string) => void;
  setActiveDoors: (doors: OpenedDoor[]) => void;
  reset: () => void;
};

export const useDoorState = create<DoorState>()((set, get) => ({
  openDoors: {},
  activeDoors: [],
  isOpen: (id: string) => Boolean(get().openDoors[id]),
  setOpen: (id: string, open: boolean) =>
    set((s) => ({ openDoors: { ...s.openDoors, [id]: open } })),
  toggle: (id: string) =>
    set((s) => ({ openDoors: { ...s.openDoors, [id]: !s.openDoors[id] } })),
  setActiveDoors: (activeDoors: OpenedDoor[]) => set({ activeDoors }),
  reset: () => set({ openDoors: {}, activeDoors: [] }),
}));

// Automatically reset door state whenever the player's zone changes
let currentZone = useStore.getState().me.zone;
useStore.subscribe((state) => {
  if (state.me.zone !== currentZone) {
    currentZone = state.me.zone;
    useDoorState.getState().reset();
  }
});
