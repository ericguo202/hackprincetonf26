// Lane C: the one place that decides whether weapons are live. Two modes,
// store.mode = "explore" | "foam" (Lane D's server writes it; "explore" by
// default). Weapons (blaster, pool noodle) work only in foam: in explore
// nothing fires, nothing is held, the crosshair is hidden and keys 1/2 do
// nothing. Everything here is read live (hooks re-render, frame loops call
// foamModeNow()), so a mode switch lands within a frame with no remount.
//
// Dev only: with VITE_SOLO there is no server, so the mode never changes;
// world/DevModeToggle.tsx flips a local override with F. The override is read
// only when DEV_MODE_TOGGLE_ON, which is false in a production build, so the
// override and its toggle never ship.

import { create } from "zustand";

import type { GameMode, Weapon } from "../../../../contracts/game-state";
import { useStore } from "../store";
import { DEV_MODE_TOGGLE_ON } from "../world/devFlags";
import { WEAPON_KEYS } from "./weapons";

/** The mode in force: a dev override, when one is set, beats the server's. */
export function effectiveMode(server: GameMode, override: GameMode | null): GameMode {
  return override ?? server;
}

export function weaponsEnabled(mode: GameMode): boolean {
  return mode === "foam";
}

/** What the character holds: the selected weapon in foam, nothing in explore. */
export function heldWeaponProp(foam: boolean, selected: Weapon): Weapon | null {
  return foam ? selected : null;
}

/** Keys 1/2: the weapon to switch to, or null (not a weapon key, or explore). */
export function weaponForKey(code: string, foam: boolean): Weapon | null {
  return foam ? (WEAPON_KEYS[code] ?? null) : null;
}

/** Dev-only local override (DevModeToggle). Unused in production builds. */
export const useModeOverride = create<{ override: GameMode | null; set: (m: GameMode | null) => void }>()((set) => ({
  override: null,
  set: (override) => set({ override }),
}));

/** Weapons live right now? For frame loops and event handlers (no re-render needed). */
export function foamModeNow(): boolean {
  const override = DEV_MODE_TOGGLE_ON ? useModeOverride.getState().override : null;
  return weaponsEnabled(effectiveMode(useStore.getState().mode, override));
}

/** Weapons live? For components: re-renders on a mode change (server or dev override). */
export function useFoamMode(): boolean {
  const server = useStore((s) => s.mode);
  const override = useModeOverride((s) => s.override);
  return weaponsEnabled(effectiveMode(server, DEV_MODE_TOGGLE_ON ? override : null));
}
