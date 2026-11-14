// Lane C: the player character asset and its license (AGENTS.md rule 10:
// every asset has a recorded license). Credits are also in
// apps/web/public/characters/CREDITS.md.

import { MOVEMENT } from "./movement";

export const CHARACTER = {
  /** Served by Vite from apps/web/public/characters/. */
  url: "/characters/Character_Soldier.gltf",
  pack: "Toon Shooter Game Kit",
  author: "Quaternius",
  source: "https://quaternius.com/packs/toonshootergamekit.html",
  license: "CC0 1.0",
  licenseUrl: "https://creativecommons.org/publicdomain/zero/1.0/",
  /**
   * The model's right hand carries all 14 of the kit's weapons; only this one
   * is kept, tinted foam colours (no realistic guns, AGENTS.md section 2).
   */
  keepWeapon: "Pistol",
  weapons: [
    "AK", "GrenadeLauncher", "Knife_1", "Knife_2", "Pistol", "Revolver", "Revolver_Small",
    "RocketLauncher", "ShortCannon", "Shotgun", "Shovel", "SMG", "Sniper", "Sniper_2",
  ],
  foamColor: "#ff8a1f",
  /** Model height after scaling: the capsule's 2 * (halfHeight + radius). */
  heightM: 2 * (MOVEMENT.capsuleHalfHeight + MOVEMENT.capsuleRadius),
} as const;

/**
 * Feet below the body centre when standing: ecctrl floats the capsule
 * floatHeight above the ground, so the centre sits halfHeight + radius +
 * floatHeight up. Lane D: render a remote player's <Character> at
 * NetPlayer.pos - [0, FOOT_BELOW_CENTRE_M, 0].
 */
export const FOOT_BELOW_CENTRE_M = MOVEMENT.capsuleHalfHeight + MOVEMENT.capsuleRadius + MOVEMENT.floatHeight;
