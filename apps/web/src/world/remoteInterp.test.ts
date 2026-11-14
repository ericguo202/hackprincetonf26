import { afterEach, describe, expect, it, vi } from "vitest";

import type { NetPlayer } from "../store";
import { aimRay, firePayload, nearestHitbox, resolveHit, shotRay, aimPoint, type Ray } from "../player/blasterMath";
import { CHARACTER, FOOT_BELOW_CENTRE_M } from "../player/characterAsset";
import { CENTRE_ABOVE_FLOOR_M, CROUCH, MOVEMENT } from "../player/movement";
import {
  INTERP_DELAY_S,
  feetFromBody,
  hitboxFromBody,
  lerpAngle,
  pushSnapshot,
  remotesInZone,
  sampleAt,
  sampleDelayed,
  type Snapshot,
  type Vec3,
} from "./remoteInterp";
import { DEV_REMOTE_IDS, devFire, fakeAlive, fakeAnim, fakePose, resetFakes } from "./fakeRemotes";

const snap = (t: number, x: number, extra: Partial<Snapshot> = {}): Snapshot => ({
  t,
  pos: [x, 0.95, 0],
  yaw: 0,
  anim: "run",
  zone: "hou_1",
  alive: true,
  ...extra,
});

const player = (id: string, zone: string, extra: Partial<NetPlayer> = {}): NetPlayer => ({
  id,
  name: id,
  zone,
  pos: [0, 0.95, 0],
  yaw: 0,
  anim: "idle",
  hp: 3,
  score: 0,
  alive: true,
  ...extra,
});

describe("interpolation, 100 ms behind", () => {
  // Patches 50 ms apart (20 Hz), moving +x at 2 m/s.
  let buf: Snapshot[] = [];
  for (let i = 0; i <= 10; i++) buf = pushSnapshot(buf, snap(i * 0.05, i * 0.1));

  it("renders INTERP_DELAY_S behind now", () => {
    expect(INTERP_DELAY_S).toBe(0.1);
    // now = 0.425 -> render time 0.325, between patches at 0.30 (x 0.6) and 0.35 (x 0.7).
    const s = sampleDelayed(buf, 0.425)!;
    expect(s.pos[0]).toBeCloseTo(0.65, 9);
    expect(s.speed).toBeCloseTo(2, 9);
  });

  it("lands exactly on a patch at its time", () => {
    expect(sampleAt(buf, 0.2)!.pos[0]).toBeCloseTo(0.4, 9);
  });

  it("holds the newest patch instead of extrapolating, and the oldest before it", () => {
    expect(sampleAt(buf, 9)!.pos[0]).toBeCloseTo(1.0, 9);
    expect(sampleAt(buf, 9)!.speed).toBe(0);
    expect(sampleAt(buf, -1)!.pos[0]).toBe(0);
    expect(sampleAt([], 1)).toBeNull();
  });

  it("takes discrete state (anim, alive) from the older patch, never blends it", () => {
    let b: Snapshot[] = [];
    b = pushSnapshot(b, snap(0, 0, { anim: "run" }));
    b = pushSnapshot(b, snap(0.05, 0.1, { anim: "shoot", alive: false }));
    expect(sampleAt(b, 0.049)!.anim).toBe("run");
    expect(sampleAt(b, 0.049)!.alive).toBe(true);
    expect(sampleAt(b, 0.05)!.anim).toBe("shoot");
  });

  it("turns the short way through +-pi", () => {
    expect(lerpAngle(3.0, -3.0, 0.5)).toBeCloseTo(Math.PI, 2);
    let b: Snapshot[] = [];
    b = pushSnapshot(b, snap(0, 0, { yaw: 3.0 }));
    b = pushSnapshot(b, snap(0.1, 0, { yaw: -3.0 }));
    expect(Math.abs(sampleAt(b, 0.05)!.yaw)).toBeGreaterThan(3.0);
  });

  it("restarts the buffer on a zone change, so it never lerps across frames", () => {
    let b: Snapshot[] = [snap(0, 0), snap(0.05, 0.1)];
    b = pushSnapshot(b, snap(0.1, 40, { zone: "block" }));
    expect(b).toHaveLength(1);
    expect(sampleAt(b, 0.07)!.pos[0]).toBe(40);
  });

  it("drops out-of-order patches and keeps the buffer short", () => {
    expect(pushSnapshot([snap(1, 0)], snap(0.5, 9))).toHaveLength(1);
    let b: Snapshot[] = [];
    for (let i = 0; i < 100; i++) b = pushSnapshot(b, snap(i, i));
    expect(b.length).toBeLessThanOrEqual(24);
    expect(b[b.length - 1].t).toBe(99);
  });
});

describe("zone filtering", () => {
  const players = {
    me: player("me", "hou_1"),
    a: player("a", "hou_1"),
    b: player("b", "block"),
    c: player("c", "hou_2"),
    d: player("d", "hou_1"),
  };

  it("keeps only other players in my zone, in a stable order", () => {
    expect(remotesInZone(players, "hou_1", "me").map((p) => p.id)).toEqual(["a", "d"]);
    expect(remotesInZone(players, "block", "me").map((p) => p.id)).toEqual(["b"]);
  });

  it("with no id yet (solo), excludes nobody by id", () => {
    expect(remotesInZone(players, "hou_1", null).map((p) => p.id)).toEqual(["a", "d", "me"]);
  });
});

describe("hitboxes match the model, not the floating capsule", () => {
  const body: Vec3 = [2, 0.95, -1]; // standing on a floor at y 0
  const box = hitboxFromBody("p2", body);
  const top = box.centre[1] + box.halfHeight + box.radius;
  const bottom = box.centre[1] - box.halfHeight - box.radius;

  it("spans feet to feet + CHARACTER.heightM", () => {
    expect(feetFromBody(body)[1]).toBeCloseTo(0.95 - FOOT_BELOW_CENTRE_M, 9);
    expect(bottom).toBeCloseTo(0, 9);
    expect(top).toBeCloseTo(CHARACTER.heightM, 9);
    expect(box.radius).toBe(MOVEMENT.capsuleRadius);
  });

  it("a dart at the knees hits (the floated capsule starts floatHeight up and would miss)", () => {
    const knees: Ray = { origin: [2, 0.2, 5], dir: [0, 0, -1] };
    expect(nearestHitbox(knees, [box], 0, 40)?.id).toBe("p2");
    expect(0.2).toBeLessThan(MOVEMENT.floatHeight); // below the capsule's bottom
  });

  it("a dart just over the head misses (the capsule reaches 1.6 m and would hit)", () => {
    const overHead: Ray = { origin: [2, 1.45, 5], dir: [0, 0, -1] };
    expect(nearestHitbox(overHead, [box], 0, 40)).toBeNull();
  });
});

describe("shooting a remote player through the blaster seam", () => {
  const head: Vec3 = [0, 0.95 + 0.65, 0];
  const camera: Vec3 = [0.4, 1.6, 3.0];

  it("hits the nearer of two players in line and sends its id", () => {
    const near = hitboxFromBody("near", [0, 0.95, -4]);
    const far = hitboxFromBody("far", [0, 0.95, -8]);
    const aim = aimRay(camera, [-0.4 / 7, -0.6 / 7, -1], head);
    const aimHit = nearestHitbox(aim, [far, near], aim.minT, 40);
    expect(aimHit?.id).toBe("near");
    const shot = shotRay(head, aimPoint(aim, aimHit!.t), aim.dir);
    const hit = resolveHit(null, nearestHitbox(shot, [far, near], 0, 40));
    expect(hit.kind).toBe("player");
    const payload = firePayload(shot, hit);
    expect(payload.target_id).toBe("near");
    expect(payload.origin).toEqual(head);
  });

  it("a wall in front of the player wins", () => {
    const p = hitboxFromBody("p", [0, 0.95, -6]);
    const ray: Ray = { origin: head, dir: [0, 0, -1] };
    expect(resolveHit(3, nearestHitbox(ray, [p], 0, 40)).kind).toBe("surface");
  });
});

describe("dev fake players (VITE_DEV_REMOTES)", () => {
  afterEach(() => {
    resetFakes();
    vi.restoreAllMocks();
  });

  it("stay within the arena and face the way they run", () => {
    for (const id of DEV_REMOTE_IDS) {
      for (let t = 0; t < 12; t += 0.1) {
        const p = fakePose(id, t, [1, -1], 1.5);
        expect(Math.hypot(p.pos2[0] - 1, p.pos2[1] + 1)).toBeLessThanOrEqual(1.5 + 1e-9);
      }
    }
    // Runner: facing = direction of travel.
    const a = fakePose(DEV_REMOTE_IDS[0], 1.0, [0, 0], 1.5);
    const b = fakePose(DEV_REMOTE_IDS[0], 1.01, [0, 0], 1.5);
    const travel = Math.atan2(b.pos2[0] - a.pos2[0], b.pos2[1] - a.pos2[1]);
    expect(Math.abs(lerpAngle(a.yaw, travel, 1) - a.yaw)).toBeLessThan(0.05);
  });

  it("the walker idles, shoots and walks", () => {
    const states = new Set<string>();
    for (let t = 0; t < 6; t += 0.05) states.add(fakeAnim(DEV_REMOTE_IDS[1], t, fakePose(DEV_REMOTE_IDS[1], t, [0, 0], 1.5)));
    expect([...states].sort()).toEqual(["idle", "run", "shoot"]);
  });

  it("three hits eliminate, then respawn after 3 s; misses and other ids do nothing", () => {
    vi.spyOn(console, "info").mockImplementation(() => {});
    const id = DEV_REMOTE_IDS[0];
    const pose = fakePose(id, 0, [0, 0], 1);
    devFire({ origin: [0, 1.6, 0], dir: [0, 0, -1], weapon: "blaster" }, 10);
    devFire({ origin: [0, 1.6, 0], dir: [0, 0, -1], target_id: "someone-else", weapon: "blaster" }, 10);
    expect(fakeAnim(id, 10, pose)).toBe("run");
    devFire({ origin: [0, 1.6, 0], dir: [0, 0, -1], target_id: id, weapon: "blaster" }, 10);
    expect(fakeAnim(id, 10.1, pose)).toBe("hit");
    devFire({ origin: [0, 1.6, 0], dir: [0, 0, -1], target_id: id, weapon: "blaster" }, 11);
    devFire({ origin: [0, 1.6, 0], dir: [0, 0, -1], target_id: id, weapon: "blaster" }, 12);
    expect(fakeAnim(id, 12.5, pose)).toBe("eliminated");
    expect(fakeAlive(id, 12.5)).toBe(false);
    expect(fakeAlive(id, 15.1)).toBe(true);
    expect(fakeAnim(id, 15.1, pose)).toBe("run");
  });
});

describe("crouched remote players", () => {
  const standingBody: Vec3 = [0, CENTRE_ABOVE_FLOOR_M.standing, 0];
  const crouchedBody: Vec3 = [0, CENTRE_ABOVE_FLOOR_M.crouched, 0];
  const span = (b: ReturnType<typeof hitboxFromBody>) => [b.centre[1] - b.halfHeight - b.radius, b.centre[1] + b.halfHeight + b.radius];

  it("stand on the floor in both postures, crouched 0.97 m tall", () => {
    const [lo, hi] = span(hitboxFromBody("p", crouchedBody, "crouch"));
    expect(lo).toBeCloseTo(0, 9);
    expect(hi).toBeCloseTo(CROUCH.topM, 9);
    const [lo2, hi2] = span(hitboxFromBody("p", standingBody, "idle"));
    expect(lo2).toBeCloseTo(0, 9);
    expect(hi2).toBeCloseTo(CHARACTER.heightM, 9);
  });

  it("a dart over a crouched head misses; the same dart hits them standing", () => {
    const ray: Ray = { origin: [0, 1.1, 5], dir: [0, 0, -1] };
    expect(nearestHitbox(ray, [hitboxFromBody("p", crouchedBody, "crouch")], 0, 40)).toBeNull();
    expect(nearestHitbox(ray, [hitboxFromBody("p", standingBody, "idle")], 0, 40)?.id).toBe("p");
  });
});
