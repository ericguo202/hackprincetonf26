// Lane C: the live sun, aimed where pack v2's baked shadows say the sun is,
// so live shadows (players, furniture) fall the same way as the baked ones.
//
// Lane A, HANDOFF 2026-11-14 22:20, note 3: azimuth 205 deg clockwise from
// north, elevation 32.8 deg; direction TO the sun in the block frame
// (-0.355, 0.541, 0.762). Block frame: +X east, +Y up, +Z south (north is -Z).
// Pure; tested in sun.test.ts.

import type { Vec3 } from "./pack";

/** Unit vector from the ground toward the sun, block frame (Lane A's baked sun). */
export const SUN_DIR_BLOCK: Vec3 = [-0.355, 0.541, 0.762];
export const SUN_AZIMUTH_DEG = 205;
export const SUN_ELEVATION_DEG = 32.8;

/** The light sits this far from its target along SUN_DIR, metres. */
export const SUN_DISTANCE_M = 60;
/** Half the side of the square the shadow map covers around the player, metres. */
export const SUN_SHADOW_HALF_M = 30;
export const SUN_SHADOW_MAP = 2048;

/** Direction to the sun from azimuth (clockwise from north) and elevation, block frame. */
export function sunDirFrom(azimuthDeg: number, elevationDeg: number): Vec3 {
  const az = (azimuthDeg * Math.PI) / 180;
  const el = (elevationDeg * Math.PI) / 180;
  return [Math.sin(az) * Math.cos(el), Math.sin(el), -Math.cos(az) * Math.cos(el)];
}

/** Light position for a target: target + dir * distance. */
export function sunPosition(target: Vec3, dir: Vec3 = SUN_DIR_BLOCK, distance = SUN_DISTANCE_M): Vec3 {
  return [target[0] + dir[0] * distance, target[1] + dir[1] * distance, target[2] + dir[2] * distance];
}

/**
 * The sun direction in a house-local frame (heading_deg counterclockwise from
 * above, as pack.ts houseToBlock): rotate the block vector by -heading. Same
 * sun, so indoor and outdoor live shadows agree.
 */
export function sunDirInHouse(headingDeg: number, dir: Vec3 = SUN_DIR_BLOCK): Vec3 {
  // houseToBlock rotates local [x, z] by r = heading (x' = x c + z s, z' = -x s + z c); invert it.
  const r = (headingDeg * Math.PI) / 180;
  const c = Math.cos(r);
  const s = Math.sin(r);
  return [dir[0] * c - dir[2] * s, dir[1], dir[0] * s + dir[2] * c];
}
