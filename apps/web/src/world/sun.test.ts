import { describe, expect, it } from "vitest";
import { OrthographicCamera } from "three";

import { blockToHouse, houseToBlock, type Vec3 } from "./pack";
import {
  lightBasis,
  SUN_AZIMUTH_DEG,
  SUN_DIR_BLOCK,
  SUN_DISTANCE_M,
  SUN_ELEVATION_DEG,
  SUN_TEXEL_M,
  snapToTexel,
  sunDirFrom,
  sunDirInHouse,
  sunPosition,
} from "./sun";

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

describe("snapToTexel (shadow box on the texel grid)", () => {
  const basis = lightBasis(SUN_DIR_BLOCK);
  const T = SUN_TEXEL_M;
  const dot = (a: Vec3, b: Vec3) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
  const add = (p: Vec3, v: Vec3, k: number): Vec3 => [p[0] + v[0] * k, p[1] + v[1] * k, p[2] + v[2] * k];
  const dist = (a: Vec3, b: Vec3) => Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]);
  // A point already on the grid in light x/y (so small moves don't straddle a rounding boundary).
  const base = snapToTexel([12.3, 0.4, -7.9], basis, T, [0, 0, 0]);

  it("the texel is the box width over the map size (about 2.9 cm)", () => {
    expect(T).toBeCloseTo(60 / 2048, 12);
  });

  it("the basis is orthonormal and matches three.js's shadow camera (lookAt with up +Y)", () => {
    for (const v of [basis.x, basis.y, basis.z]) expect(Math.hypot(...v)).toBeCloseTo(1, 12);
    expect(dot(basis.x, basis.y)).toBeCloseTo(0, 12);
    expect(dot(basis.x, basis.z)).toBeCloseTo(0, 12);
    expect(dot(basis.y, basis.z)).toBeCloseTo(0, 12);
    const target: Vec3 = [3, 1, -2];
    const cam = new OrthographicCamera();
    cam.position.set(...add(target, SUN_DIR_BLOCK, 60));
    cam.lookAt(...target);
    cam.updateMatrixWorld(true);
    const e = cam.matrixWorld.elements;
    const cx: Vec3 = [e[0], e[1], e[2]];
    const cy: Vec3 = [e[4], e[5], e[6]];
    const cz: Vec3 = [e[8], e[9], e[10]];
    for (const [a, b] of [
      [cx, basis.x],
      [cy, basis.y],
      [cz, basis.z],
    ] as const)
      for (let i = 0; i < 3; i++) expect(a[i]).toBeCloseTo(b[i], 9);
  });

  it("moving less than a texel along the light-space axes leaves the snap unchanged", () => {
    for (const axis of [basis.x, basis.y]) {
      for (const k of [0.1, 0.3, 0.49, -0.2, -0.49]) {
        expect(dist(snapToTexel(add(base, axis, k * T), basis, T), base)).toBeLessThan(1e-9);
      }
    }
    // Both at once, and in the house frame too.
    const hb = lightBasis(sunDirInHouse(90));
    const hbase = snapToTexel([1, 2, 3], hb, T);
    const moved: Vec3 = add(add(hbase, hb.x, 0.4 * T), hb.y, -0.3 * T);
    expect(dist(snapToTexel(moved, hb, T), hbase)).toBeLessThan(1e-9);
  });

  it("moving 1.5 texels moves the snap by exactly one texel along that axis", () => {
    for (const axis of [basis.x, basis.y]) {
      for (const sign of [1, -1]) {
        // 1.5 rounds to 2 for +, and to -1 for - (Math.round): offset the start by a hair to land at 1.
        const start = add(base, axis, sign * -0.01 * T);
        const s0 = snapToTexel(start, basis, T);
        const s1 = snapToTexel(add(start, axis, sign * 1.5 * T), basis, T);
        expect(dist(s1, s0)).toBeCloseTo(T, 9);
        const d: Vec3 = [s1[0] - s0[0], s1[1] - s0[1], s1[2] - s0[2]];
        expect(dot(d, axis)).toBeCloseTo(sign * T, 9);
      }
    }
  });

  it("moving along the light direction doesn't change the snapped grid cell", () => {
    for (const k of [0.01, 0.5, 3, -7.25]) {
      const p = add(base, basis.z, k);
      const s = snapToTexel(p, basis, T);
      // Same light-space x and y as the base; z follows the point exactly.
      expect(dot(s, basis.x)).toBeCloseTo(dot(base, basis.x), 9);
      expect(dot(s, basis.y)).toBeCloseTo(dot(base, basis.y), 9);
      expect(dot(s, basis.z)).toBeCloseTo(dot(p, basis.z), 9);
    }
  });

  it("writes into `out` (no allocation per frame)", () => {
    const out: Vec3 = [0, 0, 0];
    expect(snapToTexel([1, 2, 3], basis, T, out)).toBe(out);
  });
});
