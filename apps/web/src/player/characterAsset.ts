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
   * is kept, tinted foam orange (no realistic guns, AGENTS.md section 2).
   * RocketLauncher despite its name is the kit's chunky, cartoon, triple-nozzle
   * tube (the Hazmat character holds it in the pack preview): the one prop
   * that reads as a toy blaster. The pistols and rifles are realistic.
   */
  keepWeapon: "RocketLauncher",
  weapons: [
    "AK", "GrenadeLauncher", "Knife_1", "Knife_2", "Pistol", "Revolver", "Revolver_Small",
    "RocketLauncher", "ShortCannon", "Shotgun", "Shovel", "SMG", "Sniper", "Sniper_2",
  ],
  foamColor: "#ff8a1f",
  /**
   * The pool noodle (weapon "noodle"). The kit has no noodle prop, so it is a
   * plain foam cylinder, parented to the same hand bone with the blaster's
   * placement, so it moves exactly like the blaster. World metres.
   */
  noodle: { name: "PoolNoodle", lengthM: 1.2, radiusM: 0.035, color: "#2fb8ff", gripBackM: 0.15 },
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
