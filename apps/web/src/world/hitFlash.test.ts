import { beforeEach, describe, expect, it } from "vitest";

import {
  clearHit,
  eliminationPops,
  flashLevel,
  HIT_FLASH_S,
  hitRecently,
  KILL_CREDIT_S,
  markHit,
  markPop,
  POP_PEAK,
  POP_SCALE_S,
  popScale,
  resetHits,
} from "./hitFlash";

describe("hit flash", () => {
  beforeEach(resetHits);

  it("is full at the hit, fades, and expires after HIT_FLASH_S", () => {
    expect(HIT_FLASH_S).toBeCloseTo(0.12, 9);
    expect(flashLevel("a", 10)).toBe(0);
    markHit("a", 10);
    expect(flashLevel("a", 10)).toBe(1);
    expect(flashLevel("a", 10 + HIT_FLASH_S / 2)).toBeCloseTo(0.5, 9);
    expect(flashLevel("a", 10 + HIT_FLASH_S + 1e-9)).toBe(0);
    expect(flashLevel("a", 11)).toBe(0);
    expect(flashLevel("a", 9)).toBe(0); // clock before the hit
  });

  it("is per player id", () => {
    markHit("a", 10);
    expect(flashLevel("b", 10)).toBe(0);
    markHit("b", 10.05);
    expect(flashLevel("a", 10.05)).toBeGreaterThan(0);
    expect(flashLevel("b", 10.05)).toBe(1);
  });

  it("credits an elimination only within KILL_CREDIT_S of my hit, once", () => {
    markHit("a", 10);
    expect(hitRecently("a", 10 + KILL_CREDIT_S - 0.01)).toBe(true);
    expect(hitRecently("a", 10 + KILL_CREDIT_S + 0.01)).toBe(false);
    expect(hitRecently("b", 10)).toBe(false);
    clearHit("a");
    expect(hitRecently("a", 10.1)).toBe(false);
  });
});

describe("elimination pop", () => {
  beforeEach(resetHits);

  it("scales up to 1.15 and back to exactly 1.0 over 0.15 s", () => {
    expect(popScale("a", 5)).toBe(1);
    markPop("a", 5);
    expect(popScale("a", 5)).toBe(1);
    expect(popScale("a", 5 + POP_SCALE_S / 2)).toBeCloseTo(POP_PEAK, 9);
    expect(popScale("a", 5 + POP_SCALE_S / 4)).toBeGreaterThan(1);
    expect(popScale("a", 5 + POP_SCALE_S / 4)).toBeLessThan(POP_PEAK);
    expect(popScale("a", 5 + POP_SCALE_S + 1e-9)).toBe(1);
    expect(popScale("a", 99)).toBe(1);
    expect(popScale("b", 5 + POP_SCALE_S / 2)).toBe(1); // per id
    let max = 1;
    for (let t = 0; t <= POP_SCALE_S; t += 0.001) max = Math.max(max, popScale("a", 5 + t));
    expect(max).toBeLessThanOrEqual(POP_PEAK + 1e-12);
  });

  const P = (id: string, alive: boolean, zone = "hou_1") => ({ id, zone, alive });

  it("pops mine (hit within KILL_CREDIT_S) and others' in my zone, nobody else", () => {
    markHit("mine", 10);
    markHit("stale", 10 - KILL_CREDIT_S - 0.5);
    const prev = {
      me: P("me", true),
      mine: P("mine", true),
      other: P("other", true),
      stale: P("stale", true),
      away: P("away", true, "block"),
      wasDead: P("wasDead", false),
      still: P("still", true),
    };
    const next = {
      me: P("me", false),
      mine: P("mine", false),
      other: P("other", false),
      stale: P("stale", false),
      away: P("away", false, "block"),
      wasDead: P("wasDead", false),
      still: P("still", true),
      joined: P("joined", false),
    };
    const pops = eliminationPops(next, prev, "me", "hou_1", 10.2);
    expect(pops).toEqual([
      { id: "mine", mine: true },
      { id: "other", mine: false },
      { id: "stale", mine: false },
    ]);
    // The same patch again (no flip) pops nobody.
    expect(eliminationPops(next, next, "me", "hou_1", 10.3)).toEqual([]);
  });
});
