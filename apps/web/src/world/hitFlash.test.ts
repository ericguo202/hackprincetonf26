import { beforeEach, describe, expect, it } from "vitest";

import { clearHit, flashLevel, HIT_FLASH_S, hitRecently, KILL_CREDIT_S, markHit, resetHits } from "./hitFlash";

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
