// Lane C: which weapon the local player holds (keys 1 and 2, weapons.ts).
// Local to player/: the blaster reads it to attack, Player passes it to
// <Character weapon> so the model holds it. Not network state: the wire
// carries the weapon only in `fire` (contracts v2.4.0), so remote players
// always show the blaster until Player state gains a weapon field.

import { create } from "zustand";

import type { Weapon } from "../../../../contracts/game-state";

type WeaponState = { weapon: Weapon; setWeapon: (w: Weapon) => void };

export const useWeaponState = create<WeaponState>()((set) => ({
  weapon: "blaster",
  setWeapon: (weapon) => set({ weapon }),
}));
