import { describe, expect, it } from "vitest";

import { ARM, armOffset, viewDir, type V3 } from "./armMath";
import {
  BLASTER,
  MIN_SHOT_INTERVAL_S,
  aimPoint,
  aimRay,
  canFire,
  firePayload,
  headFrom,
  muzzleFrom,
  nearestHitbox,
  rayCapsule,
  resolveHit,
  shotRay,
  type Hitbox,
} from "./blasterMath";
import { MOVEMENT } from "./movement";

const close = (a: V3, b: V3, digits = 9) => a.forEach((v, i) => expect(v).toBeCloseTo(b[i], digits));
const dist = (a: V3, b: V3) => Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]);
const add = (a: V3, b: V3): V3 => [a[0] + b[0], a[1] + b[1], a[2] + b[2]];

// Standing at the origin, looking down -Z, arm fully out over the right shoulder.
const body: V3 = [0, 0.655, 0];
const head = headFrom(body, ARM.headOffsetY);
const camera = add(head, armOffset(0, 0, ARM.back, ARM.side));
const look = viewDir(0, 0);

describe("aim ray", () => {
  it("starts at the camera and points through the crosshair", () => {
    const aim = aimRay(camera, look, head);
    close(aim.origin, camera);
    close(aim.dir, [0, 0, -1]);
  });

  it("normalizes the camera direction", () => {
    close(aimRay(camera, [0, 0, -5], head).dir, [0, 0, -1]);
  });

  it("ignores everything closer than the head: minT is the head's distance along the ray", () => {
    const aim = aimRay(camera, look, head);
    expect(aim.minT).toBeCloseTo(ARM.back, 9);
  });

  it("in first person (camera at the head) ignores nothing", () => {
    expect(aimRay(head, look, head).minT).toBe(0);
  });

  it("never goes negative when the head is behind the camera", () => {
    expect(aimRay([0, 1, -2], look, [0, 1, 0]).minT).toBe(0);
  });

  it("aims at the first hit, or at full range past the head", () => {
    const aim = aimRay(camera, look, head);
    close(aimPoint(aim, 10), [camera[0], camera[1], camera[2] - 10]);
    close(aimPoint(aim, null), [camera[0], camera[1], camera[2] - (aim.minT + BLASTER.rangeM)]);
  });
});

describe("shot origin", () => {
  it("is the head (body centre + head offset), never the camera", () => {
    close(head, [0, 0.655 + ARM.headOffsetY, 0]);
    const aim = aimRay(camera, look, head);
    const shot = shotRay(head, aimPoint(aim, 12), aim.dir);
    close(shot.origin, head);
    expect(dist(shot.origin, camera)).toBeGreaterThan(1.5); // camera would fail the server check
  });

  it("is always within the server's 1.5 m of the position we report (me.pos = body centre)", () => {
    expect(dist(headFrom(body, ARM.headOffsetY), body)).toBeLessThan(1.5);
  });

  it("points from the head to the aim point, converging on the crosshair", () => {
    const aim = aimRay(camera, look, head);
    const target = aimPoint(aim, 12);
    const shot = shotRay(head, target, aim.dir);
    expect(Math.hypot(...shot.dir)).toBeCloseTo(1, 9);
    close(add(head, shot.dir.map((v) => v * shot.aimDist) as V3), target);
    // Over the shoulder the camera sits right of the head, so the shot angles slightly right.
    expect(shot.dir[0]).toBeGreaterThan(0);
  });

  it("falls back to the aim direction if the aim point is on the head", () => {
    expect(shotRay(head, head, [0, 0, -1])).toEqual({ origin: head, dir: [0, 0, -1], aimDist: 0 });
  });

  it("goes on the wire as origin + unit dir, target_id only on a player hit", () => {
    const shot = { origin: head, dir: [0, 0, -1] as V3 };
    expect(firePayload(shot, { kind: "surface", t: 3 })).toEqual({ origin: head, dir: [0, 0, -1] });
    expect(firePayload(shot, { kind: "miss", t: 40 })).not.toHaveProperty("target_id");
    expect(firePayload(shot, { kind: "player", t: 3, targetId: "p2" }).target_id).toBe("p2");
  });

  it("puts the muzzle in front of, right of and below the head", () => {
    const m = muzzleFrom(head, 0, 0);
    expect(m[0]).toBeGreaterThan(head[0]);
    expect(m[1]).toBeLessThan(head[1]);
    expect(m[2]).toBeLessThan(head[2]);
  });
});

describe("fire rate", () => {
  it("stays under the server's 5 shots per second", () => {
    expect(BLASTER.maxShotsPerSecond).toBeLessThan(5);
    expect(MIN_SHOT_INTERVAL_S).toBeGreaterThan(1 / 5);
  });

  it("allows the first shot and spaces the rest", () => {
    expect(canFire(null, 0)).toBe(true);
    expect(canFire(1, 1 + MIN_SHOT_INTERVAL_S - 0.01)).toBe(false);
    expect(canFire(1, 1 + MIN_SHOT_INTERVAL_S)).toBe(true);
  });

  it("holding the trigger for 10 s at 144 fps fires at most 4 per second", () => {
    let last: number | null = null;
    let shots = 0;
    for (let f = 0; f <= 1440; f++) {
      const t = f / 144;
      if (canFire(last, t)) {
        last = t;
        shots++;
      }
    }
    expect(shots).toBeLessThanOrEqual(41);
    expect(shots).toBeGreaterThanOrEqual(38);
  });
});

describe("hitboxes (Lane D seam)", () => {
  const p2: Hitbox = {
    id: "p2",
    centre: [0, 0.655, -10],
    radius: MOVEMENT.capsuleRadius,
    halfHeight: MOVEMENT.capsuleHalfHeight,
  };

  it("hits the capsule's front surface", () => {
    expect(rayCapsule({ origin: [0, 0.655, 0], dir: [0, 0, -1] }, p2)).toBeCloseTo(10 - 0.3, 9);
  });

  it("hits the rounded top", () => {
    const top = p2.centre[1] + p2.halfHeight + 0.2;
    const t = rayCapsule({ origin: [0, top, 0], dir: [0, 0, -1] }, p2)!;
    expect(t).toBeGreaterThan(9.7);
    expect(t).toBeLessThan(10);
  });

  it("misses beside, above and behind", () => {
    expect(rayCapsule({ origin: [0.31, 0.655, 0], dir: [0, 0, -1] }, p2)).toBeNull();
    expect(rayCapsule({ origin: [0, 2, 0], dir: [0, 0, -1] }, p2)).toBeNull();
    expect(rayCapsule({ origin: [0, 0.655, 0], dir: [0, 0, 1] }, p2)).toBeNull();
  });

  it("finds the nearest box in range, and none past a wall", () => {
    const p3 = { ...p2, id: "p3", centre: [0, 0.655, -5] as V3 };
    const ray = { origin: [0, 0.655, 0] as V3, dir: [0, 0, -1] as V3 };
    expect(nearestHitbox(ray, [p2, p3], 0, 40)?.id).toBe("p3");
    expect(nearestHitbox(ray, [p2, p3], 0, 3)).toBeNull(); // wall at 3 m
  });

  it("a player in front of a wall is a player hit; behind it, a surface hit", () => {
    expect(resolveHit(8, { id: "p2", t: 5 })).toEqual({ kind: "player", t: 5, targetId: "p2" });
    expect(resolveHit(3, { id: "p2", t: 5 })).toEqual({ kind: "surface", t: 3 });
    expect(resolveHit(null, null)).toEqual({ kind: "miss", t: BLASTER.rangeM });
    expect(resolveHit(55, null).kind).toBe("miss");
  });
});
