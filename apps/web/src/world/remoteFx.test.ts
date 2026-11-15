import { describe, expect, it } from "vitest";

import type { FxEvent } from "../store";
import { FX_MATCH_M, fxInMyZone, seenIds, swooshYaw, takeUnseen, unitDir } from "./remoteFx";
import type { Vec3 } from "./remoteInterp";

const fx = (id: number, origin: Vec3 = [0, 1.6, 0], kind = "blaster"): FxEvent => ({
  id,
  kind,
  origin,
  dir: [0, 0, -1],
  at: id,
});

describe("fxInMyZone", () => {
  it("keeps a shot from a remote player drawn in my zone (head 0.65 m above the body centre)", () => {
    expect(fxInMyZone(fx(1, [2, 1.6, 3]), [[2, 0.95, 3]])).toBe(true);
  });

  it("drops a shot from another zone: its origin is in another frame and matches nobody here", () => {
    // Shooter is on the block at (40, 1.6, -95); my zone's only remote stands in the living room.
    expect(fxInMyZone(fx(1, [40, 1.6, -95]), [[1, 0.95, 2]])).toBe(false);
  });

  it("drops every shot when nobody else is in my zone", () => {
    expect(fxInMyZone(fx(1), [])).toBe(false);
  });

  it("matches at 1.4 m and not at 1.6 m", () => {
    expect(FX_MATCH_M).toBe(1.5);
    expect(fxInMyZone(fx(1, [1.4, 0, 0]), [[0, 0, 0]])).toBe(true);
    expect(fxInMyZone(fx(1, [1.6, 0, 0]), [[0, 0, 0]])).toBe(false);
  });

  it("measures in 3D, not just on the floor", () => {
    expect(fxInMyZone(fx(1, [0, 1.6, 0]), [[0, 0, 0]])).toBe(false);
  });

  it("matches any of several remotes", () => {
    expect(fxInMyZone(fx(1, [10, 1.6, 0]), [[0, 0.95, 0], [10, 0.95, 0.5]])).toBe(true);
  });
});

describe("once per id", () => {
  it("marks everything present on mount as seen, so a reload replays nothing", () => {
    const list = [fx(1), fx(2), fx(3)];
    const seen = seenIds(list);
    expect(takeUnseen(list, seen)).toEqual([]);
  });

  it("plays each new id exactly once, in order", () => {
    const seen = seenIds([fx(1)]);
    const a = [fx(1), fx(2), fx(3)];
    expect(takeUnseen(a, seen).map((e) => e.id)).toEqual([2, 3]);
    expect(takeUnseen(a, seen)).toEqual([]);
    // The same events in a new array (a store re-set) are still not replayed.
    expect(takeUnseen([...a], seen)).toEqual([]);
    expect(takeUnseen([...a, fx(4)], seen).map((e) => e.id)).toEqual([4]);
  });

  it("follows the list as it rolls over its 8-entry cap, and keeps the seen set bounded", () => {
    const seen = seenIds([]);
    let list: FxEvent[] = [];
    const played: number[] = [];
    for (let id = 1; id <= 30; id++) {
      list = [...list, fx(id)].slice(-8);
      played.push(...takeUnseen(list, seen).map((e) => e.id));
      expect(seen.size).toBeLessThanOrEqual(8);
    }
    expect(played).toEqual(Array.from({ length: 30 }, (_, i) => i + 1));
  });

  it("plays a burst of several events that land between two frames", () => {
    const seen = seenIds([fx(1)]);
    expect(takeUnseen([fx(1), fx(2), fx(3), fx(4)], seen).map((e) => e.id)).toEqual([2, 3, 4]);
  });
});

describe("unitDir and swooshYaw", () => {
  it("normalizes, and rejects a zero or NaN dir", () => {
    expect(unitDir([0, 0, -2])).toEqual([0, 0, -1]);
    expect(unitDir([0, 0, 0])).toBeNull();
    expect(unitDir([NaN, 0, 1])).toBeNull();
  });

  it("turns the arc's local +X onto the swing's horizontal direction", () => {
    for (const d of [[1, 0, 0], [0, 0, -1], [-1, 0.3, 1], [0.2, -0.5, 0.7]] as Vec3[]) {
      const yaw = swooshYaw(d)!;
      // three.js rotation.y = yaw maps +X to (cos yaw, 0, -sin yaw).
      const h = Math.hypot(d[0], d[2]);
      expect(Math.cos(yaw)).toBeCloseTo(d[0] / h, 6);
      expect(-Math.sin(yaw)).toBeCloseTo(d[2] / h, 6);
    }
  });

  it("has no yaw for a straight up or down swing", () => {
    expect(swooshYaw([0, 1, 0])).toBeNull();
  });
});
