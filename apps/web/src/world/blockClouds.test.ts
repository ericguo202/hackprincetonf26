import { describe, expect, it } from "vitest";

import { BLOCK_CLOUDS, CLOUD_OPACITY, CLOUD_SPEED, cloudPuffTexture } from "./blockClouds";
import { BLOCK_SKY } from "./sun";

describe("block clouds", () => {
  it("5 or 6 clouds, each at most ~20 segments, soft and slow", () => {
    expect(BLOCK_CLOUDS.length).toBeGreaterThanOrEqual(5);
    expect(BLOCK_CLOUDS.length).toBeLessThanOrEqual(6);
    for (const c of BLOCK_CLOUDS) expect(c.segments).toBeLessThanOrEqual(20);
    expect(CLOUD_OPACITY).toBeCloseTo(0.8, 5);
    expect(CLOUD_SPEED).toBeLessThanOrEqual(0.1);
  });

  it("high up, past the houses, inside the sky box", () => {
    const BLOCK_HALF = 165; // block collision.glb is about +-163 m
    for (const c of BLOCK_CLOUDS) {
      const [x, y, z] = c.position;
      const r = Math.hypot(x, z);
      expect(y).toBeGreaterThanOrEqual(60);
      expect(r).toBeGreaterThan(BLOCK_HALF + 20);
      expect(Math.max(Math.abs(x) + c.bounds[0], Math.abs(z) + c.bounds[0])).toBeLessThan(BLOCK_SKY.distance / 2);
      expect(y + c.bounds[1]).toBeLessThan(BLOCK_SKY.distance / 2);
    }
  });

  it("spread around the horizon: every 90 degree quadrant has a cloud", () => {
    const quadrants = new Set(BLOCK_CLOUDS.map((c) => Math.floor(((Math.atan2(c.position[0], c.position[2]) + 2 * Math.PI) % (2 * Math.PI)) / (Math.PI / 2))));
    expect(quadrants.size).toBe(4);
  });

  it("visible from the sidewalk spawn: above the horizon and inside camera far", () => {
    const spawn = [-2.88, 0.29 + 1.6, 89.19]; // manifest spawns[0], eye height
    for (const c of BLOCK_CLOUDS) {
      const d = [c.position[0] - spawn[0], c.position[1] - spawn[1], c.position[2] - spawn[2]];
      const dist = Math.hypot(...d);
      const elevationDeg = (Math.asin(d[1] / dist) * 180) / Math.PI;
      expect(elevationDeg).toBeGreaterThan(10);
      expect(dist).toBeLessThan(1000 - 50);
    }
  });

  it("no CDN texture: the puff is drawn locally (null without a 2D canvas, as in jsdom)", () => {
    const t = cloudPuffTexture();
    expect(t === null || t.startsWith("data:image/png")).toBe(true);
  });
});
