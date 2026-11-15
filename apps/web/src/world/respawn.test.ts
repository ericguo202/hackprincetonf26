import { describe, expect, it } from "vitest";

import type { NetPlayer } from "../store";
import { pickIndoorRespawn, SPAWN_LIFT_M, stepLife } from "./respawn";

const player = (id: string, x: number, z: number, alive = true): NetPlayer => ({
  id,
  name: id,
  zone: "hou_1",
  pos: [x, 1, z],
  yaw: 0,
  anim: "idle",
  hp: 3,
  score: 0,
  alive,
});

describe("stepLife", () => {
  it("does not treat the first alive sync as a respawn", () => {
    expect(stepLife({ sawDeath: false }, true).respawned).toBe(false);
  });

  it("fires once on dead then alive", () => {
    let s = stepLife({ sawDeath: false }, false).state;
    expect(s.sawDeath).toBe(true);
    s = stepLife(s, false).state;
    const back = stepLife(s, true);
    expect(back.respawned).toBe(true);
    expect(stepLife(back.state, true).respawned).toBe(false);
  });

  it("ignores a missing player entry", () => {
    expect(stepLife({ sawDeath: true }, undefined)).toEqual({ state: { sawDeath: true }, respawned: false });
  });
});

describe("pickIndoorRespawn", () => {
  const spawns = [
    { roomId: "r1", point: [0, 0] as [number, number], floorY: 0 },
    { roomId: "r2", point: [10, 0] as [number, number], floorY: 0.2 },
  ];

  it("returns null with no spawns", () => {
    expect(pickIndoorRespawn([], [])).toBeNull();
  });

  it("picks the spawn farthest from the nearest living player, lifted off the floor", () => {
    expect(pickIndoorRespawn(spawns, [player("a", 1, 0)])).toEqual([10, 0.2 + SPAWN_LIFT_M, 0]);
    expect(pickIndoorRespawn(spawns, [player("a", 9, 0)])).toEqual([0, SPAWN_LIFT_M, 0]);
  });

  it("ignores eliminated players", () => {
    expect(pickIndoorRespawn(spawns, [player("a", 9, 0, false), player("b", 1, 0)])).toEqual([10, 0.2 + SPAWN_LIFT_M, 0]);
  });
});
