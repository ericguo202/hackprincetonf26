import { describe, expect, it } from "vitest";

import { viewDir } from "../player/armMath";
import { type FocusState, focusArrival, stepFocus, yawToward } from "./focusTeleport";
import type { Door } from "./pack";

const door = (block: [number, number, number], exitTo: [number, number, number]): Door => ({
  listing_id: "hou_1",
  world: { position: [0, 0, 0], heading_deg: 0, door: [0, 0, 0] },
  block,
  local: [0, 0, 0],
  exitTo,
  enterTo: [0, 0, 0],
});

describe("yawToward", () => {
  it("matches the camera's view direction for every quadrant", () => {
    for (const [dx, dz] of [
      [0, -1],
      [1, 0],
      [0, 1],
      [-1, 0],
      [3, -4],
    ]) {
      const [vx, , vz] = viewDir(yawToward(dx, dz), 0);
      const len = Math.hypot(dx, dz);
      expect(vx).toBeCloseTo(dx / len, 6);
      expect(vz).toBeCloseTo(dz / len, 6);
    }
  });
});

describe("focusArrival", () => {
  // Door at x = 10 facing +X (exit point 1.2 m further along +X).
  const d = door([10, 0.3, 5], [11.2, 0.3, 5]);

  it("lands on the nearest sidewalk spawn on the street side, facing the door", () => {
    const spawns: [number, number, number][] = [
      [30, 0.2, 5], // street side, 20 m
      [22, 0.25, 9], // street side, nearest
      [0, 0.2, 5], // behind the house: never
      [50, 0.2, 5], // street side but too far
    ];
    const a = focusArrival(d, 1, spawns);
    expect(a.pos).toEqual([22, 1.25, 9]);
    const [vx, , vz] = viewDir(a.yaw, 0);
    const len = Math.hypot(10 - 22, 5 - 9);
    expect(vx).toBeCloseTo((10 - 22) / len, 6);
    expect(vz).toBeCloseTo((5 - 9) / len, 6);
  });

  it("falls back to the exit point with no spawn in front within range", () => {
    expect(focusArrival(d, 1, [[0, 0.2, 5], [80, 0.2, 5]]).pos).toEqual([11.2, 1.3, 5]);
  });

  it("lands on the door's exit point, lifted, looking back at the door", () => {
    const a = focusArrival(door([10, 0.3, 5], [11.2, 0.3, 5]), 1);
    expect(a.pos).toEqual([11.2, 1.3, 5]);
    const [vx, , vz] = viewDir(a.yaw, 0);
    expect(vx).toBeCloseTo(-1, 6); // the door is at -X from the exit point
    expect(vz).toBeCloseTo(0, 6);
  });
});

describe("stepFocus", () => {
  const fresh: FocusState = { seen: false, listing: undefined };

  it("never teleports on the first value seen (initial sync or reload)", () => {
    expect(stepFocus(fresh, "hou_1").goTo).toBeNull();
    expect(stepFocus(fresh, undefined).goTo).toBeNull();
  });

  it("teleports when my focus changes to a new house, once", () => {
    let s = stepFocus(fresh, undefined).state;
    const first = stepFocus(s, "hou_1");
    expect(first.goTo).toBe("hou_1");
    s = first.state;
    expect(stepFocus(s, "hou_1").goTo).toBeNull();
    expect(stepFocus(s, "hou_2").goTo).toBe("hou_2");
  });

  it("does not teleport when my focus is cleared", () => {
    const s = stepFocus(stepFocus(fresh, "hou_1").state, "hou_2").state;
    expect(stepFocus(s, undefined).goTo).toBeNull();
  });
});
