// Lane C: which weapon the local player holds (keys 1 and 2, weapons.ts).
// Local to player/: the blaster reads it to attack, Player passes it to
// <Character weapon> so the model holds it. net/game.ts sends it with each
// `move` (contracts v2.7.0) so remote players draw it from Player.weapon.

import { create } from "zustand";

import type { Weapon } from "../../../../contracts/game-state";

type WeaponState = { weapon: Weapon; setWeapon: (w: Weapon) => void };

export const useWeaponState = create<WeaponState>()((set) => ({
  weapon: "blaster",
  setWeapon: (weapon) => set({ weapon }),
}));
