// Lane C: dev-only focus override for testing FocusBeacon on VITE_SOLO.
// Gated by DEV_FOCUS_TOGGLE_ON (dev build with VITE_SOLO=1).

import { create } from "zustand";

export const DEV_FOCUS_HOUSE = "hou_236595179";

export const useDevFocusOverride = create<{
  override: string | null;
  toggle: () => void;
  set: (house: string | null) => void;
}>()((set) => ({
  override: null,
  toggle: () => set((s) => ({ override: s.override ? null : DEV_FOCUS_HOUSE })),
  set: (override) => set({ override }),
}));
