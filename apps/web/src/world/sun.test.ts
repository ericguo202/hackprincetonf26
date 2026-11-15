import { describe, expect, it } from "vitest";

import { blockToHouse, houseToBlock, type Vec3 } from "./pack";
import { SUN_AZIMUTH_DEG, SUN_DIR_BLOCK, SUN_DISTANCE_M, SUN_ELEVATION_DEG, sunDirFrom, sunDirInHouse, sunPosition } from "./sun";

describe("baked sun (Lane A, HANDOFF 22:20)", () => {
  it("Lane A's vector is a unit vector matching azimuth 205 deg, elevation 32.8 deg", () => {
    expect(Math.hypot(...SUN_DIR_BLOCK)).toBeCloseTo(1, 2);
    const d = sunDirFrom(SUN_AZIMUTH_DEG, SUN_ELEVATION_DEG);
    d.forEach((v, i) => expect(v).toBeCloseTo(SUN_DIR_BLOCK[i], 2));
  });

  it("the sun is south-south-west and up (+Z south, -X west)", () => {
    expect(SUN_DIR_BLOCK[0]).toBeLessThan(0);
    expect(SUN_DIR_BLOCK[1]).toBeGreaterThan(0);
    expect(SUN_DIR_BLOCK[2]).toBeGreaterThan(0);
  });

  it("the light sits at target + dir * 60", () => {
    const t: Vec3 = [-15.9, 1.3, 93.2];
    const p = sunPosition(t);
    expect(SUN_DISTANCE_M).toBe(60);
    p.forEach((v, i) => expect(v).toBeCloseTo(t[i] + SUN_DIR_BLOCK[i] * 60, 9));
  });

  it("indoors the same sun is expressed in the house frame (inverse of houseToBlock)", () => {
    for (const heading of [4.3, 0.2, 90, -37]) {
      const world = { position: [0, 0, 0] as Vec3, heading_deg: heading, door: [0, 0, 0] as Vec3 };
      const local = sunDirInHouse(heading);
      houseToBlock(world, local).forEach((v, i) => expect(v).toBeCloseTo(SUN_DIR_BLOCK[i], 9));
      blockToHouse(world, SUN_DIR_BLOCK).forEach((v, i) => expect(v).toBeCloseTo(local[i], 9));
    }
  });
});
