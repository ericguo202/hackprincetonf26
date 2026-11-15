// Lane C: when the local player last attacked, for the weapon bar's cooldown
// sweep (WeaponBar.tsx). Blaster.tsx writes it on every shot or swing, next
// to its own lastShotAt; nothing else writes it. Blaster's cooldown logic
// does not read it. Not network state.
//
// Time is performance.now() in seconds: the DOM bar has no R3F clock. Both
// clocks run in real time, so the sweep and Blaster's canAttack agree to
// within a frame.

import { create } from "zustand";

import type { Weapon } from "../../../../contracts/game-state";

type AttackClock = {
  /** Increments on every attack, so a repeat at the same weapon restarts the sweep. */
  seq: number;
  weapon: Weapon | null;
  /** performance.now() / 1000 at the last attack, or null before the first. */
  atS: number | null;
};

export const useAttackClock = create<AttackClock>()(() => ({ seq: 0, weapon: null, atS: null }));

export const nowS = (): number => performance.now() / 1000;

/** Blaster calls this on every attack it makes. */
export function markAttack(weapon: Weapon, atS: number = nowS()): void {
  useAttackClock.setState((s) => ({ seq: s.seq + 1, weapon, atS }));
}
