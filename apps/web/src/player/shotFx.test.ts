import { describe, expect, it } from "vitest";

import {
  BURST_S,
  bitFade,
  createBurstState,
  facing,
  hemisphereDir,
  HIT_BITS,
  KILL_BITS,
  spawnBurst,
  stepBurst,
} from "./shotFx";
import type { V3 } from "./armMath";

const dot = (a: V3, b: V3) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const live = (s: ReturnType<typeof createBurstState>) => s.bits.filter((b) => b.life > 0).length;

describe("foam burst pool", () => {
  it("hit and elimination sizes are as specified", () => {
    expect(HIT_BITS).toBeGreaterThanOrEqual(10);
    expect(HIT_BITS).toBeLessThanOrEqual(14);
    expect(KILL_BITS).toBe(30);
  });

  it("reuses its slots: never grows, the oldest bits are overwritten", () => {
    const s = createBurstState(20);
    const bits = s.bits.slice();
    spawnBurst(s, [0, 1, 0], [0, 1, 5], 12);
    expect(live(s)).toBe(12);
    expect(s.next).toBe(12);
    spawnBurst(s, [3, 1, 0], [3, 1, 5], 12);
    expect(s.bits).toHaveLength(20);
    expect(s.bits.every((b, i) => b === bits[i])).toBe(true); // same objects
    expect(live(s)).toBe(20);
    expect(s.next).toBe(4);
    // Slots 0..3 now belong to the second burst.
    expect(s.bits[0].x).toBe(3);
    expect(s.bits[4].x).toBe(0);
    // A burst bigger than the pool fills it once.
    spawnBurst(s, [0, 0, 0], [0, 0, 1], 50);
    expect(s.bits).toHaveLength(20);
  });

  it("fades over BURST_S, falls under gravity, then every bit is gone", () => {
    const s = createBurstState(12);
    spawnBurst(s, [0, 1, 0], [0, 1, 5], 12, 1, () => 0.5);
    const b = s.bits[0];
    expect(bitFade(b)).toBe(1);
    const vy0 = b.vy;
    stepBurst(s, BURST_S / 2);
    expect(bitFade(b)).toBeCloseTo(0.5, 6);
    expect(b.vy).toBeLessThan(vy0);
    stepBurst(s, BURST_S / 2 + 1e-6);
    expect(live(s)).toBe(0);
    expect(s.bits.every((x) => bitFade(x) === 0)).toBe(true);
    // dt 0 and NaN change nothing.
    spawnBurst(s, [0, 1, 0], [0, 1, 5], 1);
    const before = { ...s.bits[0] };
    stepBurst(s, 0);
    stepBurst(s, Number.NaN);
    expect(s.bits[0]).toEqual(before);
  });

  it("launches every bit into the hemisphere facing the shooter", () => {
    const at: V3 = [2, 1.2, -3];
    for (const shooter of [
      [2, 1.6, 4],
      [-8, 1, -3],
      [2, 9, -3],
      [5, -2, -7],
    ] as V3[]) {
      const n = facing(at, shooter);
      let r = 0.137;
      const rand = () => (r = (r * 9301 + 0.49297) % 1); // deterministic, spread over [0, 1)
      const s = createBurstState(64);
      spawnBurst(s, at, shooter, 64, 1, rand);
      for (const b of s.bits) {
        const v: V3 = [b.vx, b.vy, b.vz];
        expect(dot(v, n)).toBeGreaterThanOrEqual(0);
      }
      // On average they fly toward the shooter, not just sideways.
      const mean = s.bits.reduce((a, b) => a + dot([b.vx, b.vy, b.vz], n) / Math.hypot(b.vx, b.vy, b.vz), 0) / 64;
      expect(mean).toBeGreaterThan(0.3);
    }
  });

  it("hemisphereDir gives unit vectors, at the pole when u1 is 1", () => {
    const out: V3 = [0, 0, 0];
    const n = facing([0, 0, 0], [1, 2, 3]);
    for (const [u1, u2] of [
      [0, 0],
      [0.3, 0.7],
      [0.99, 0.2],
    ]) {
      hemisphereDir(n, u1, u2, out);
      expect(Math.hypot(...out)).toBeCloseTo(1, 9);
      expect(dot(out, n)).toBeCloseTo(u1, 9);
    }
    expect(facing([1, 1, 1], [1, 1, 1])).toEqual([0, 1, 0]);
  });
});
