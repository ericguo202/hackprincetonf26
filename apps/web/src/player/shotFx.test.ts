import { describe, expect, it } from "vitest";

import {
  BURST_S,
  bitFade,
  createBurstState,
  facing,
  hemisphereDir,
  HIT_BITS,
  POP_BIT_SIZE,
  POP_BITS,
  POP_MIN_UP,
  POP_S,
  POP_SPEED_MAX,
  POP_SPEED_MIN,
  popBitCount,
  RING_FROM_M,
  RING_S,
  RING_TO_M,
  ringAt,
  spawnBurst,
  spawnPop,
  stepBurst,
} from "./shotFx";
import type { V3 } from "./armMath";

const dot = (a: V3, b: V3) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const live = (s: ReturnType<typeof createBurstState>) => s.bits.filter((b) => b.life > 0).length;

describe("foam burst pool", () => {
  it("hit splat size is as specified", () => {
    expect(HIT_BITS).toBeGreaterThanOrEqual(10);
    expect(HIT_BITS).toBeLessThanOrEqual(14);
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

describe("elimination foam pop", () => {
  it("has 40 to 50 bits for mine, 60% for others'", () => {
    expect(popBitCount(true)).toBe(POP_BITS);
    expect(POP_BITS).toBeGreaterThanOrEqual(40);
    expect(POP_BITS).toBeLessThanOrEqual(50);
    expect(popBitCount(false)).toBe(Math.round(POP_BITS * 0.6));
    expect(popBitCount(false)).toBeLessThan(popBitCount(true));
  });

  it("stays within its pool, reusing the oldest slots", () => {
    const s = createBurstState(96);
    spawnPop(s, [0, 1, 0], popBitCount(true));
    spawnPop(s, [5, 1, 0], popBitCount(true));
    expect(live(s)).toBe(90);
    spawnPop(s, [9, 1, 0], popBitCount(true));
    expect(s.bits).toHaveLength(96);
    expect(live(s)).toBe(96);
    spawnPop(s, [0, 0, 0], 500); // more than the pool: fills it once
    expect(s.bits).toHaveLength(96);
  });

  it("launches up and outward at 3 to 6 m/s, 1.5x the splat size, gone after 0.8 s", () => {
    const s = createBurstState(POP_BITS);
    let r = 0.31;
    spawnPop(s, [0, 1, 0], POP_BITS, () => (r = (r * 9301 + 0.49297) % 1));
    for (const b of s.bits) {
      const sp = Math.hypot(b.vx, b.vy, b.vz);
      expect(sp).toBeGreaterThanOrEqual(POP_SPEED_MIN - 1e-9);
      expect(sp).toBeLessThanOrEqual(POP_SPEED_MAX + 1e-9);
      expect(b.vy / sp).toBeGreaterThanOrEqual(POP_MIN_UP - 1e-9);
      expect(b.size).toBe(POP_BIT_SIZE);
      expect(b.maxLife).toBe(POP_S);
    }
    expect(POP_BIT_SIZE).toBe(1.5);
    expect(POP_S).toBeCloseTo(0.8, 9);
    stepBurst(s, POP_S / 2);
    expect(bitFade(s.bits[0])).toBeCloseTo(0.5, 6);
    stepBurst(s, POP_S / 2 + 1e-6);
    expect(live(s)).toBe(0);
  });

  it("the ring grows from 0.3 m to 2.0 m over 0.35 s while fading, then is gone", () => {
    expect(ringAt(-0.01)).toBeNull();
    const start = ringAt(0)!;
    expect(start.radius).toBeCloseTo(RING_FROM_M, 9);
    let prev = start;
    for (let t = 0.02; t < RING_S; t += 0.02) {
      const r = ringAt(t)!;
      expect(r.radius).toBeGreaterThan(prev.radius);
      expect(r.opacity).toBeLessThan(prev.opacity);
      expect(r.radius).toBeLessThanOrEqual(RING_TO_M);
      prev = r;
    }
    expect(ringAt(RING_S - 1e-6)!.radius).toBeCloseTo(RING_TO_M, 4);
    expect(ringAt(RING_S - 1e-6)!.opacity).toBeCloseTo(0, 4);
    expect(ringAt(RING_S)).toBeNull();
    expect(ringAt(Infinity)).toBeNull();
    expect(ringAt(Number.NaN)).toBeNull();
  });
});
