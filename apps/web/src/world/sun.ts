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

/**
 * One shadow-map texel in metres: the shadow box's width over the map size
 * (60 m / 2048 = about 2.9 cm).
 */
export const SUN_TEXEL_M = (2 * SUN_SHADOW_HALF_M) / SUN_SHADOW_MAP;

/**
 * The shadow camera's axes for a sun direction: z along `dir` (light toward
 * the scene looks down -z), x = normalize(up x z), y = z x x, with up = +Y,
 * exactly as three.js aims the shadow camera (lookAt from the light to its
 * target with camera.up = +Y). Build it once per direction.
 */
export type LightBasis = { x: Vec3; y: Vec3; z: Vec3 };

export function lightBasis(dir: Vec3): LightBasis {
  const l = Math.hypot(dir[0], dir[1], dir[2]);
  const z: Vec3 = [dir[0] / l, dir[1] / l, dir[2] / l];
  // up x z, with up = (0, 1, 0)
  let x: Vec3 = [z[2], 0, -z[0]];
  const xl = Math.hypot(x[0], x[1], x[2]);
  x = xl > 1e-9 ? [x[0] / xl, x[1] / xl, x[2] / xl] : [1, 0, 0]; // sun straight overhead: any horizontal x
  const y: Vec3 = [z[1] * x[2] - z[2] * x[1], z[2] * x[0] - z[0] * x[2], z[0] * x[1] - z[1] * x[0]];
  return { x, y, z };
}

/**
 * `point` with its light-space x and y rounded to multiples of `texel`, so
 * the shadow camera that follows it moves in whole texels and shadow edges
 * don't shimmer. The light-space z (along the sun) is kept as is. Written
 * into `out` (no allocation per frame).
 */
export function snapToTexel(point: Vec3, basis: LightBasis, texel: number, out: Vec3 = [0, 0, 0]): Vec3 {
  const { x, y, z } = basis;
  const px = point[0] * x[0] + point[1] * x[1] + point[2] * x[2];
  const py = point[0] * y[0] + point[1] * y[1] + point[2] * y[2];
  const pz = point[0] * z[0] + point[1] * z[1] + point[2] * z[2];
  const sx = Math.round(px / texel) * texel;
  const sy = Math.round(py / texel) * texel;
  out[0] = sx * x[0] + sy * y[0] + pz * z[0];
  out[1] = sx * x[1] + sy * y[1] + pz * z[1];
  out[2] = sx * x[2] + sy * y[2] + pz * z[2];
  return out;
}
