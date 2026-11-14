// Lane C: the tiny bit of state the blaster shares with its DOM overlay
// (Crosshair.tsx sits outside the canvas). Local to player/; not store.ts,
// which is Lane D's and carries network state.

import { create } from "zustand";

export type HitMarker = { kind: "player" | "surface"; seq: number };

type BlasterHud = {
  marker: HitMarker | null;
  showMarker: (kind: HitMarker["kind"]) => void;
};

let seq = 0;

export const useBlasterHud = create<BlasterHud>()((set) => ({
  marker: null,
  // A new seq on every hit restarts the marker animation even on rapid hits.
  showMarker: (kind) => set({ marker: { kind, seq: ++seq } }),
}));
