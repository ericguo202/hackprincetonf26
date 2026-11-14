import { describe, expect, it } from "vitest";

import { ARM, type V3 } from "./armMath";
import { BLASTER, headFrom, type Hitbox } from "./blasterMath";
import { CENTRE_ABOVE_FLOOR_M, CROUCH, MOVEMENT } from "./movement";
import { NOODLE, WEAPON_KEYS, WEAPON_RULES, canAttack, noodlePayload, noodleSwing } from "./weapons";

const head = headFrom([0, CENTRE_ABOVE_FLOOR_M.standing, 0], ARM.headOffsetY);
const ahead: V3 = [0, 0, -1]; // looking down -Z, level
const R = MOVEMENT.capsuleRadius;

/** A standing player whose body centre is `dist` metres away at `deg` degrees right of the aim. */
function playerAt(id: string, dist: number, deg = 0, crouched = false): Hitbox {
  const a = (deg * Math.PI) / 180;
  const h = crouched ? CROUCH.topM : 2 * (MOVEMENT.capsuleHalfHeight + R);
  return { id, centre: [Math.sin(a) * dist, h / 2, -Math.cos(a) * dist], radius: R, halfHeight: h / 2 - R };
}

describe("noodle defaults (named constants, Lane D may overrule)", () => {
  it("2.0 m reach, 60 degree arc, 0.8 s cooldown, 2 hits to eliminate (blaster: 3)", () => {
    expect(NOODLE).toMatchObject({ reachM: 2.0, arcDeg: 60, cooldownS: 0.8, hitsToEliminate: 2 });
    expect(WEAPON_RULES.noodle.hitsToEliminate).toBe(2);
    expect(WEAPON_RULES.blaster.hitsToEliminate).toBe(3);
    expect(WEAPON_RULES.blaster.minIntervalS).toBeCloseTo(1 / BLASTER.maxShotsPerSecond, 9);
  });

  it("keys 1 and 2 pick the weapon", () => {
    expect(WEAPON_KEYS.Digit1).toBe("blaster");
    expect(WEAPON_KEYS.Digit2).toBe("noodle");
  });
});

describe("reach", () => {
  it("hits a body whose surface is within 2.0 m of the head (3D), misses just beyond", () => {
    // The 1.6 m head is above a standing target's capsule axis (top at 1.3 - R),
    // so the reach is measured to the nearest point of that axis, minus the radius.
    const dy = head[1] - (2 * (MOVEMENT.capsuleHalfHeight + R) - R);
    const horiz = (surface: number) => Math.sqrt((surface + R) ** 2 - dy ** 2);
    expect(noodleSwing(head, ahead, [playerAt("p", horiz(NOODLE.reachM - 0.01))])?.id).toBe("p");
    expect(noodleSwing(head, ahead, [playerAt("p", horiz(NOODLE.reachM + 0.01))])).toBeNull();
  });

  it("reaches a crouched player in front without looking down", () => {
    expect(noodleSwing(head, ahead, [playerAt("p", 1.2, 0, true)])?.id).toBe("p");
  });

  it("picks the nearest of two in reach", () => {
    expect(noodleSwing(head, ahead, [playerAt("far", 1.8), playerAt("near", 0.9)])?.id).toBe("near");
  });
});

describe("arc", () => {
  it("is 60 degrees wide: hits inside 30 degrees either side, misses well outside", () => {
    expect(noodleSwing(head, ahead, [playerAt("p", 1.8, 25)])?.id).toBe("p");
    expect(noodleSwing(head, ahead, [playerAt("p", 1.8, -25)])?.id).toBe("p");
    expect(noodleSwing(head, ahead, [playerAt("p", 1.8, 50)])).toBeNull();
    expect(noodleSwing(head, ahead, [playerAt("p", 1.0, 180)])).toBeNull(); // behind
  });

  it("lets the arc edge graze a body: a target just past 30 degrees still counts if its side is inside", () => {
    // At 1.8 m a 0.25 m radius spans about 8 degrees: centre at 36 degrees, side inside 30.
    expect(noodleSwing(head, ahead, [playerAt("p", 1.8, 36)])?.id).toBe("p");
  });

  it("uses the horizontal aim: looking up or down does not narrow the swing", () => {
    expect(noodleSwing(head, [0, -0.6, -0.8], [playerAt("p", 1.5)])?.id).toBe("p");
    expect(noodleSwing(head, [0, 0.6, -0.8], [playerAt("p", 1.5)])?.id).toBe("p");
  });
});

describe("cooldown", () => {
  it("allows a swing every 0.8 s and no faster", () => {
    expect(canAttack("noodle", null, 0)).toBe(true);
    expect(canAttack("noodle", 1, 1.79)).toBe(false);
    expect(canAttack("noodle", 1, 1.8)).toBe(true);
  });

  it("holding the button for 10 s gives 13 swings (0 s, 0.8 s, ..., 9.6 s)", () => {
    let last: number | null = null;
    let swings = 0;
    for (let f = 0; f <= 600; f++) {
      const t = f / 60;
      if (canAttack("noodle", last, t)) {
        last = t;
        swings++;
      }
    }
    expect(swings).toBe(13);
  });

  it("the blaster keeps its own faster rate", () => {
    expect(canAttack("blaster", 1, 1.25)).toBe(true);
    expect(canAttack("noodle", 1, 1.25)).toBe(false);
  });
});

describe("payload", () => {
  it("is the `fire` message with weapon noodle, origin the head, target only on a hit", () => {
    const hit = noodleSwing(head, ahead, [playerAt("p2", 1.0)]);
    expect(noodlePayload(head, ahead, hit)).toEqual({ origin: head, dir: ahead, weapon: "noodle", target_id: "p2" });
    expect(noodlePayload(head, ahead, null)).toEqual({ origin: head, dir: ahead, weapon: "noodle" });
  });

  it("origin stays inside the server's 1.5 m check", () => {
    const body: V3 = [0, CENTRE_ABOVE_FLOOR_M.standing, 0];
    expect(Math.hypot(...head.map((v, i) => v - body[i]))).toBeLessThan(1.5);
  });
});
