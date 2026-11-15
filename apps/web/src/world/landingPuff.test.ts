import { describe, expect, it } from "vitest";

import { bitFade } from "../player/shotFx";
import {
  createPuffState,
  footprintRadius,
  LANDING_BITS_MAX,
  LANDING_BITS_MIN,
  LANDING_S,
  landingBitCount,
  ringAt,
  spawnLanding,
  stepPuffs,
} from "./landingPuff";

const liveBits = (s: ReturnType<typeof createPuffState>) => s.bits.bits.filter((b) => b.life > 0).length;

describe("landing puff", () => {
  it("scales with the footprint: radius from size_m, 6 to 10 bits", () => {
    expect(footprintRadius(0.4, 0.4)).toBeCloseTo(Math.hypot(0.4, 0.4) / 2, 9);
    expect(footprintRadius(0.01, 0.01)).toBe(0.15);
    expect(landingBitCount(footprintRadius(0.3, 0.3))).toBe(LANDING_BITS_MIN + 1);
    expect(landingBitCount(footprintRadius(2.2, 0.95))).toBe(LANDING_BITS_MAX);
    for (const r of [0.1, 0.5, 1, 3]) {
      expect(landingBitCount(r)).toBeGreaterThanOrEqual(6);
      expect(landingBitCount(r)).toBeLessThanOrEqual(10);
    }
  });

  it("the ring grows from the footprint edge and fades out by 0.35 s", () => {
    expect(LANDING_S).toBeCloseTo(0.35, 9);
    expect(ringAt(0, 1)!.radius).toBeCloseTo(1, 9);
    expect(ringAt(LANDING_S / 2, 1)!.radius).toBeGreaterThan(1);
    expect(ringAt(LANDING_S / 2, 1)!.opacity).toBeLessThan(ringAt(0, 1)!.opacity);
    expect(ringAt(LANDING_S, 1)).toBeNull();
    expect(ringAt(Infinity, 1)).toBeNull();
    expect(ringAt(-1, 1)).toBeNull();
  });

  it("reuses its pool: rings and bits never grow, the oldest are taken first", () => {
    const s = createPuffState(3, 20);
    const rings = s.rings.slice();
    const bits = s.bits.bits.slice();
    for (let i = 0; i < 7; i++) spawnLanding(s, [i, 0, 0], 1);
    expect(s.rings).toHaveLength(3);
    expect(s.rings.every((r, i) => r === rings[i])).toBe(true);
    expect(s.bits.bits).toHaveLength(20);
    expect(s.bits.bits.every((b, i) => b === bits[i])).toBe(true);
    // 7 spawns into 3 rings: the last three spawns (x 4, 5, 6) hold them.
    expect(s.rings.map((r) => r.x).sort()).toEqual([4, 5, 6]);
    expect(liveBits(s)).toBe(20);
  });

  it("everything is gone after LANDING_S; dt 0 changes nothing", () => {
    const s = createPuffState();
    spawnLanding(s, [0, 0, 0], 0.8);
    expect(liveBits(s)).toBe(landingBitCount(0.8));
    stepPuffs(s, 0);
    expect(s.rings[0].age).toBe(0);
    stepPuffs(s, LANDING_S / 2);
    expect(bitFade(s.bits.bits[0])).toBeCloseTo(0.5, 6);
    stepPuffs(s, LANDING_S / 2 + 1e-6);
    expect(liveBits(s)).toBe(0);
    expect(ringAt(s.rings[0].age, s.rings[0].r0)).toBeNull();
  });

  it("bits start at the footprint edge and fly outward", () => {
    const s = createPuffState();
    spawnLanding(s, [2, 0, 3], 1, () => 0.5);
    for (const b of s.bits.bits.filter((x) => x.life > 0)) {
      const ox = b.x - 2;
      const oz = b.z - 3;
      expect(Math.hypot(ox, oz)).toBeCloseTo(0.9, 6);
      expect(ox * b.vx + oz * b.vz).toBeGreaterThan(0); // outward
      expect(b.vy).toBeGreaterThan(0);
    }
  });
});
